"""Business KPIs computed from DB records (never hardcoded), CSV export, plain-English insights."""
from __future__ import annotations

import csv
import io
from typing import Optional

from app.data import db
from app.services.rules import REASON_CODES


def _rate(a: float, b: float) -> float:
    return round(a / b, 4) if b else 0.0


def get_kpis() -> dict:
    s = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM processed_listings GROUP BY status")}
    processed = sum(s.values())
    drafted, quarantined = s.get("DRAFT", 0), s.get("QUARANTINED", 0)
    published, rejected = s.get("PUBLISHED", 0), s.get("REJECTED", 0)
    feeds = db.query_one("SELECT COUNT(*) n, COALESCE(MIN(is_synthetic),1) syn FROM incoming_supplier_feeds")
    # publish-ready = passed all automated gates (DRAFT now, or later PUBLISHED / rejected-from-draft)
    passed_gates = db.query_one(
        "SELECT COUNT(*) n FROM processed_listings l WHERE NOT EXISTS "
        "(SELECT 1 FROM rule_violations v WHERE v.listing_id=l.listing_id AND v.severity='BLOCK')")["n"]
    blocked = processed - passed_gates
    reviewed = db.query_one("SELECT SUM(decision='APPROVE') a, COUNT(*) n FROM audit_log WHERE action='HUMAN_REVIEW'")
    classified = db.query_one("SELECT AVG(category_confidence) c, COUNT(category_id) n FROM processed_listings")
    with_cat = classified["n"] or 0
    missing = db.query_one(
        "SELECT COUNT(DISTINCT l.listing_id) n FROM processed_listings l JOIN rule_violations v "
        "ON v.listing_id=l.listing_id WHERE v.code IN ('MISSING_REQUIRED_ATTR','INVALID_ATTR_VALUE')")["n"]
    attempts = db.query_one(
        "SELECT SUM(extraction_attempts>1 OR classification_attempts>1) r, "
        "SUM(extraction_attempts>0 OR classification_attempts>0) n FROM processed_listings")
    return {
        "feeds_total": feeds["n"], "processed": processed, "drafted": drafted, "quarantined": quarantined,
        "published": published, "rejected": rejected, "pending_review": drafted,
        "publish_ready_rate": _rate(passed_gates, processed), "quarantine_rate": _rate(blocked, processed),
        "approval_rate": _rate(reviewed["a"] or 0, reviewed["n"] or 0),
        "avg_confidence": round(classified["c"] or 0.0, 3),
        "attr_completeness_rate": _rate(with_cat - missing, with_cat),
        "llm_retry_rate": _rate(attempts["r"] or 0, attempts["n"] or 0),
        "is_synthetic": bool(feeds["syn"]),
    }


def quarantine_reasons() -> list[dict]:
    return db.query("SELECT code AS reason, COUNT(DISTINCT listing_id) AS count FROM rule_violations "
                    "WHERE severity='BLOCK' GROUP BY code ORDER BY count DESC")


def status_by_category() -> list[dict]:
    return db.query("SELECT COALESCE(l.category_id,'UNCLASSIFIED') category_id, COALESCE(c.name,'Unclassified') "
                    "category_name, l.status, COUNT(*) count FROM processed_listings l LEFT JOIN canonical_categories c "
                    "ON c.category_id=l.category_id GROUP BY 1,2,3 ORDER BY 1,3")


def run_history() -> list[dict]:
    return db.query("SELECT run_id, started_at, total, drafted, quarantined, errors, duration_ms, provider, model "
                    "FROM pipeline_runs ORDER BY started_at")


def export_listings_csv(status: Optional[str] = None) -> str:
    from app.services.workflow import list_listings
    rows = list_listings(status=status, limit=100000)
    buf = io.StringIO()
    cols = ["listing_id", "feed_id", "supplier_id", "supplier_sku", "status", "category_id", "category_name",
            "category_confidence", "price", "clean_title", "raw_title", "reason_codes", "extraction_attempts",
            "classification_attempts", "request_id", "run_id", "updated_at"]
    w = csv.DictWriter(buf, fieldnames=cols + ["data_label"], extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({**r, "data_label": "SYNTHETIC"})
    return buf.getvalue()


def business_insights() -> list[str]:
    k = get_kpis()
    if not k["processed"]:
        return ["No listings processed yet - run a batch on the Ingest page."]
    out = [f"{k['publish_ready_rate']:.0%} of {k['processed']} processed listings passed every automated check "
           f"and are ready for (or past) human review; {k['quarantine_rate']:.0%} were quarantined."]
    reasons = quarantine_reasons()
    if reasons:
        top = reasons[0]
        out.append(f"Top quarantine reason: {top['reason']} ({top['count']} listings) - "
                   f"{REASON_CODES.get(top['reason'], '').lower()}.")
    comp = [r for r in reasons if r["reason"] in ("PROHIBITED_KEYWORD", "PROMPT_INJECTION_DETECTED", "PII_IN_LISTING")]
    if comp:
        out.append(f"{sum(r['count'] for r in comp)} listings were stopped for compliance/safety reasons before "
                   "reaching shoppers.")
    out.append(f"Required-attribute completeness is {k['attr_completeness_rate']:.0%}; missing sizes/specs are the "
               "main fixable supplier-data gap." if k["attr_completeness_rate"] < 1 else
               "All classified listings have complete required attributes.")
    if k["published"] or k["rejected"]:
        out.append(f"Reviewers approved {k['approval_rate']:.0%} of decisions; {k['published']} listings are live.")
    return out
