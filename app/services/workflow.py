"""Core orchestration: raw feed -> guardrails -> classify (LLM, closed enum) -> extract (LLM, strict schema)
-> deterministic normalisation + attribute validation + compliance rules -> DRAFT | QUARANTINED
-> human review -> live catalog.  This module is the interface contract used by the UI and the API."""
from __future__ import annotations

import json
import time
import uuid
from typing import Literal, Optional

from pydantic import ValidationError

from app.config import get_config, path, public_snapshot
from app.data import db
from app.data.seed_synthetic import seed
from app.models.schemas import (BatchSummary, PipelineResult, RawListing, ReviewDecision, RuleViolation,
                                Variation, utcnow)
from app.observability.logging import log_event, new_request_id, track
from app.services import audit, normalize as N, rules
from app.services.classifier import classify
from app.services.extractor import extract
from app.services.guardrails import check_input, mask_sensitive
from app.services.llm import LLMClient, LLMError

_client: Optional[LLMClient] = None
_fallback_reason: Optional[str] = None


# ------------------------------------------------------------------ setup
def init_app() -> None:
    db.init_schema()
    if db.query_one("SELECT COUNT(*) n FROM canonical_categories")["n"] == 0:
        seed()


def reset_demo() -> None:
    global _client
    _client = None
    seed(reset=True)


def get_client() -> LLMClient:
    """Configured provider; falls back to the deterministic mock if the real provider can't start."""
    global _client, _fallback_reason
    if _client is None:
        try:
            _client = LLMClient()
            _fallback_reason = None
        except LLMError as e:
            _fallback_reason = str(e)
            log_event("llm_fallback_to_mock", reason=str(e))
            _client = LLMClient(provider="mock")
    return _client


def llm_status() -> dict:
    c = get_client()
    return {"provider": c.provider, "model": c.model, "fallback_reason": _fallback_reason}


# ------------------------------------------------------------------ taxonomy
def list_categories() -> list[dict]:
    rows = db.query("SELECT * FROM canonical_categories ORDER BY category_id")
    parents = {r["parent_id"] for r in rows if r["parent_id"]}
    for r in rows:
        r["required_attributes"] = json.loads(r.pop("required_attributes_json") or "[]")
        r["prohibited_keywords"] = json.loads(r["prohibited_keywords"] or "[]")
        r["is_leaf"] = r["category_id"] not in parents
    return rows


def _leaves() -> list[dict]:
    return [c for c in list_categories() if c["is_leaf"]]


# ------------------------------------------------------------------ pipeline
def _variations(sku: str, sizes: list[str], colors: list[str]) -> list[Variation]:
    sizes = sizes or [None]
    colors = colors or [None]
    if sizes == [None] and colors == [None]:
        return []
    out = []
    for c in colors:
        for s in sizes:
            parts = [sku] + ([c.replace(" ", "")[:4].upper()] if c else []) + ([s] if s else [])
            out.append(Variation(size=s, color=c, sku="-".join(parts)))
    return out


def process_listing(feed_id: int, actor: str = "system", run_id: Optional[str] = None,
                    client: Optional[LLMClient] = None) -> PipelineResult:
    feed = db.query_one("SELECT * FROM incoming_supplier_feeds WHERE feed_id=?", (feed_id,))
    if not feed:
        raise ValueError(f"feed_id {feed_id} not found")
    prior = db.query_one("SELECT listing_id,status FROM processed_listings WHERE feed_id=?", (feed_id,))
    if prior and prior["status"] == "PUBLISHED":
        raise ValueError(f"feed_id {feed_id} is already published (listing {prior['listing_id']})")
    raw = RawListing.model_validate(feed)
    client = client or get_client()
    rid = new_request_id("lst")
    t0 = time.perf_counter()

    with track("process_listing", rid, feed_id=feed_id, provider=client.provider) as t:
        violations: list[RuleViolation] = check_input(raw)
        price = N.parse_price(raw.raw_price)
        category, cls, ext, attrs, variations = None, None, None, {}, []
        llm_raw: dict = {}
        cls_attempts = ext_attempts = 0
        clean_title = N.smart_title(mask_sensitive(N.strip_title_noise(raw.raw_title)))[:150]
        pre_blocked = any(v.severity == "BLOCK" for v in violations)

        if not pre_blocked:  # untrusted input passed guardrails -> call the LLM
            c_out = classify(client, raw, _leaves())
            cls_attempts = c_out.attempts
            t.add_tokens(c_out.tokens_in, c_out.tokens_out)
            llm_raw["classification"] = {"outputs": c_out.raw_outputs, "errors": c_out.errors}
            if c_out.value is None:
                violations.append(RuleViolation(code="CLASSIFICATION_INVALID", field="category_id",
                                                detail=f"{c_out.attempts} attempts: {c_out.errors[-1] if c_out.errors else ''}"[:300]))
            else:
                cls = c_out.value
                category = next(c for c in _leaves() if c["category_id"] == cls.category_id)
                violations += rules.check_confidence(cls.confidence)
                e_out = extract(client, raw, category)
                ext_attempts = e_out.attempts
                t.add_tokens(e_out.tokens_in, e_out.tokens_out)
                llm_raw["extraction"] = {"outputs": e_out.raw_outputs, "errors": e_out.errors}
                if e_out.value is None:
                    violations.append(RuleViolation(code="EXTRACTION_INVALID", field="extracted",
                                                    detail=f"{e_out.attempts} attempts: {e_out.errors[-1] if e_out.errors else ''}"[:300]))
                else:
                    ext = e_out.value
                    attrs = rules.assemble_attributes(ext, category)
                    violations += rules.validate_attributes(attrs, category)
                    max_len = get_config()["pipeline"]["max_title_len"]
                    full = N.build_title(ext.brand, ext.gender, ext.product_type, ext.key_features,
                                         ext.pack_count, max_len=10_000)
                    clean_title = full[:max_len].rstrip()
                    violations += rules.check_title(clean_title, raw.raw_title, len(full) > max_len)
                    variations = _variations(raw.supplier_sku, attrs.get("size", []), attrs.get("colors", []))

        violations += rules.check_prohibited(
            {"raw_title": raw.raw_title, "raw_bullets": raw.raw_bullets, "clean_title": clean_title}, category)
        violations += rules.check_price(price)
        status = rules.route(violations)

        now = utcnow()
        with db.tx() as c:
            if prior:
                c.execute("DELETE FROM processed_listings WHERE listing_id=?", (prior["listing_id"],))
            cur = c.execute(
                "INSERT INTO processed_listings(feed_id,run_id,request_id,clean_title,brand,product_type,gender,material,"
                "pack_count,category_id,category_confidence,category_rationale,price,attributes_json,llm_raw_json,"
                "extraction_attempts,classification_attempts,status,created_at,updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (feed_id, run_id, rid, clean_title, ext.brand if ext else None, ext.product_type if ext else None,
                 ext.gender if ext else None, attrs.get("material") if attrs else None,
                 ext.pack_count if ext else None, cls.category_id if cls else None,
                 cls.confidence if cls else None, cls.rationale if cls else None, price,
                 json.dumps(attrs), mask_sensitive(json.dumps(llm_raw)), ext_attempts, cls_attempts, status, now, now))
            listing_id = cur.lastrowid
            c.executemany("INSERT INTO listing_variations(listing_id,size,color,sku) VALUES (?,?,?,?)",
                          [(listing_id, v.size, v.color, v.sku) for v in variations])
            c.executemany("INSERT INTO rule_violations(listing_id,code,severity,field,detail) VALUES (?,?,?,?,?)",
                          [(listing_id, v.code, v.severity, v.field, mask_sensitive(v.detail)) for v in violations])
            c.execute("UPDATE incoming_supplier_feeds SET status='PROCESSED' WHERE feed_id=?", (feed_id,))
            audit.record(c, actor=actor, action="PIPELINE_ROUTE", entity_type="listing", entity_id=listing_id,
                         decision=status, model=f"{client.provider}:{client.model}", request_id=rid,
                         details={"feed_id": feed_id, "run_id": run_id,
                                  "reason_codes": sorted({v.code for v in violations})})
        t.extra.update(route=status, listing_id=listing_id)

    return PipelineResult(
        listing_id=listing_id, feed_id=feed_id, request_id=rid, status=status, clean_title=clean_title,
        category_id=cls.category_id if cls else None, category_confidence=cls.confidence if cls else None,
        price=price, extracted=ext, attributes=attrs, variations=variations, violations=violations,
        extraction_attempts=ext_attempts, classification_attempts=cls_attempts,
        tokens_in=t.tokens_in, tokens_out=t.tokens_out, duration_ms=int((time.perf_counter() - t0) * 1000))


def run_batch(limit: Optional[int] = None, feed_ids: Optional[list[int]] = None, actor: str = "system") -> BatchSummary:
    client = get_client()
    cfg_limit = get_config()["pipeline"]["batch_limit"]
    if feed_ids:
        ids = [int(i) for i in feed_ids][:cfg_limit]
    else:
        n = min(int(limit or cfg_limit), cfg_limit)
        ids = [r["feed_id"] for r in db.query(
            "SELECT feed_id FROM incoming_supplier_feeds WHERE status='PENDING' ORDER BY feed_id LIMIT ?", (n,))]
    run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    snap = public_snapshot()
    snap["llm"].update(effective_provider=client.provider, effective_model=client.model, fallback_reason=_fallback_reason)
    snap_dir = path("run_snapshots")
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / f"{run_id}.json").write_text(json.dumps(snap, indent=2), encoding="utf-8")
    with db.tx() as c:
        c.execute("INSERT INTO pipeline_runs(run_id,started_at,actor,provider,model,config_snapshot_json)"
                  " VALUES (?,?,?,?,?,?)", (run_id, utcnow(), actor, client.provider, client.model, json.dumps(snap)))
    t0 = time.perf_counter()
    results, errors = [], 0
    with track("run_batch", run_id, n=len(ids)) as t:
        for fid in ids:
            try:
                r = process_listing(fid, actor=actor, run_id=run_id, client=client)
                results.append(r)
                t.add_tokens(r.tokens_in, r.tokens_out)
            except Exception as e:  # one bad row must not kill the batch
                errors += 1
                log_event("listing_error", run_id=run_id, feed_id=fid, error=f"{type(e).__name__}: {e}")
    dur = int((time.perf_counter() - t0) * 1000)
    drafted = sum(r.status == "DRAFT" for r in results)
    quarantined = sum(r.status == "QUARANTINED" for r in results)
    with db.tx() as c:
        c.execute("UPDATE pipeline_runs SET finished_at=?,total=?,drafted=?,quarantined=?,errors=?,duration_ms=?"
                  " WHERE run_id=?", (utcnow(), len(ids), drafted, quarantined, errors, dur, run_id))
        audit.record(c, actor=actor, action="BATCH_RUN", entity_type="run", entity_id=run_id, decision="COMPLETED",
                     model=f"{client.provider}:{client.model}", request_id=run_id,
                     details={"total": len(ids), "drafted": drafted, "quarantined": quarantined, "errors": errors})
    return BatchSummary(run_id=run_id, total=len(ids), drafted=drafted, quarantined=quarantined, errors=errors,
                        duration_ms=dur, results=results)


def submit_listing(supplier_id: str, supplier_sku: str, raw_title: str, raw_bullets: str = "",
                   raw_size_tag: str = "", raw_color_tag: str = "", raw_price: str = "",
                   actor: str = "ui") -> PipelineResult:
    """Ad-hoc single listing (UI 'test a listing' / API). Validates input, stores it as a feed row, processes it."""
    try:
        raw = RawListing(supplier_id=supplier_id, supplier_sku=supplier_sku, raw_title=raw_title,
                         raw_bullets=raw_bullets, raw_size_tag=raw_size_tag, raw_color_tag=raw_color_tag,
                         raw_price=raw_price)
    except ValidationError as e:
        raise ValueError("Invalid listing input: " + "; ".join(
            f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())) from e
    sku = raw.supplier_sku
    if db.query_one("SELECT 1 FROM incoming_supplier_feeds WHERE supplier_id=? AND supplier_sku=?", (raw.supplier_id, sku)):
        sku = f"{sku}-{uuid.uuid4().hex[:4]}"
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO incoming_supplier_feeds(supplier_id,supplier_sku,raw_title,raw_bullets,raw_size_tag,"
            "raw_color_tag,raw_price,received_at,status,is_synthetic) VALUES (?,?,?,?,?,?,?,?,'PENDING',1)",
            (raw.supplier_id, sku, raw.raw_title, raw.raw_bullets, raw.raw_size_tag, raw.raw_color_tag,
             raw.raw_price, utcnow()))
        fid = cur.lastrowid
    return process_listing(fid, actor=actor)


# ------------------------------------------------------------------ inspection
_LISTING_SQL = """
SELECT l.listing_id, l.feed_id, l.run_id, l.request_id, f.raw_title, l.clean_title, l.category_id,
       c.name AS category_name, l.category_confidence, l.price, l.status,
       (SELECT GROUP_CONCAT(DISTINCT v.code) FROM rule_violations v
         WHERE v.listing_id=l.listing_id AND v.severity='BLOCK') AS reason_codes,
       l.extraction_attempts, l.classification_attempts, f.supplier_id, f.supplier_sku, l.created_at, l.updated_at
FROM processed_listings l
JOIN incoming_supplier_feeds f ON f.feed_id=l.feed_id
LEFT JOIN canonical_categories c ON c.category_id=l.category_id
"""


def list_feeds(status: Optional[str] = None) -> list[dict]:
    if status:
        return db.query("SELECT * FROM incoming_supplier_feeds WHERE status=? ORDER BY feed_id", (status,))
    return db.query("SELECT * FROM incoming_supplier_feeds ORDER BY feed_id")


def list_listings(status: Optional[str] = None, reason: Optional[str] = None,
                  category_id: Optional[str] = None, limit: int = 500) -> list[dict]:
    where, params = [], []
    if status:
        where.append("l.status=?"); params.append(status)
    if category_id:
        where.append("l.category_id=?"); params.append(category_id)
    if reason:
        where.append("EXISTS (SELECT 1 FROM rule_violations v WHERE v.listing_id=l.listing_id AND v.code LIKE ?)")
        params.append(f"{reason}%")
    sql = _LISTING_SQL + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY l.listing_id LIMIT ?"
    rows = db.query(sql, params + [int(limit)])
    for r in rows:
        r["reason_codes"] = r["reason_codes"] or ""
    return rows


def get_listing_detail(listing_id: int) -> Optional[dict]:
    row = db.query_one("SELECT * FROM processed_listings WHERE listing_id=?", (listing_id,))
    if not row:
        return None
    row["attributes"] = json.loads(row.pop("attributes_json") or "{}")
    row["llm_raw"] = json.loads(row.pop("llm_raw_json") or "{}")
    summary = db.query_one(_LISTING_SQL + " WHERE l.listing_id=?", (listing_id,)) or {}
    row["category_name"] = summary.get("category_name")
    row["reason_codes"] = summary.get("reason_codes") or ""
    cat = next((c for c in list_categories() if c["category_id"] == row["category_id"]), None)
    return {
        "listing": row,
        "feed": db.query_one("SELECT * FROM incoming_supplier_feeds WHERE feed_id=?", (row["feed_id"],)),
        "category": cat,
        "variations": db.query("SELECT size,color,sku FROM listing_variations WHERE listing_id=? ORDER BY variation_id", (listing_id,)),
        "violations": db.query("SELECT code,severity,field,detail FROM rule_violations WHERE listing_id=? ORDER BY violation_id", (listing_id,)),
        "audit": audit.list_audit(limit=50, entity_id=str(listing_id)),
        "live": db.query_one("SELECT * FROM live_catalog WHERE listing_id=?", (listing_id,)),
    }


def list_live_catalog() -> list[dict]:
    rows = db.query("SELECT * FROM live_catalog ORDER BY catalog_id DESC")
    for r in rows:
        r["attributes"] = json.loads(r.pop("attributes_json") or "{}")
    return rows


# ------------------------------------------------------------------ human review
def review_decision(listing_id: int, decision: Literal["APPROVE", "REJECT"], reviewer: str, note: str = "") -> dict:
    try:
        d = ReviewDecision(listing_id=listing_id, decision=decision, reviewer=reviewer, note=note)
    except ValidationError as e:
        raise ValueError("Invalid review: " + "; ".join(f"{x['loc'][0]}: {x['msg']}" for x in e.errors())) from e
    rid = new_request_id("rev")
    with track("review_decision", rid, listing_id=d.listing_id, decision=d.decision):
        row = db.query_one("SELECT * FROM processed_listings WHERE listing_id=?", (d.listing_id,))
        if not row:
            raise ValueError(f"listing {d.listing_id} not found")
        blocks = db.query("SELECT code FROM rule_violations WHERE listing_id=? AND severity='BLOCK'", (d.listing_id,))
        if d.decision == "APPROVE":
            # deterministic publish gate: humans cannot override compliance blocks
            if row["status"] != "DRAFT":
                return {"ok": False, "status": row["status"],
                        "message": f"Only DRAFT listings can be approved (current: {row['status']})."}
            if blocks or not row["category_id"] or not row["price"] or row["price"] <= 0:
                return {"ok": False, "status": row["status"], "message": "Publish gate failed: blocking violations present."}
            new_status = "PUBLISHED"
        else:
            if row["status"] not in ("DRAFT", "QUARANTINED"):
                return {"ok": False, "status": row["status"],
                        "message": f"Cannot reject a listing in status {row['status']}."}
            new_status = "REJECTED"
        now = utcnow()
        with db.tx() as c:
            c.execute("UPDATE processed_listings SET status=?, updated_at=? WHERE listing_id=?",
                      (new_status, now, d.listing_id))
            if new_status == "PUBLISHED":
                c.execute("INSERT INTO live_catalog(listing_id,category_id,title,price,attributes_json,published_at,published_by)"
                          " VALUES (?,?,?,?,?,?,?)", (d.listing_id, row["category_id"], row["clean_title"], row["price"],
                                                      row["attributes_json"], now, d.reviewer))
            audit.record(c, actor=d.reviewer, action="HUMAN_REVIEW", entity_type="listing", entity_id=d.listing_id,
                         decision=d.decision, model=None, request_id=rid,
                         details={"note": d.note, "from_status": row["status"], "to_status": new_status,
                                  "pipeline_request_id": row["request_id"]})
    msg = "Published to live catalog." if new_status == "PUBLISHED" else "Listing rejected."
    return {"ok": True, "status": new_status, "message": msg, "request_id": rid}
