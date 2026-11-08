import sqlite3

import pytest

from app.data import db
from app.services import analytics, audit, diagnostics, workflow
from app.services.llm import LLMClient, LLMResponse


def by_sku(results, sku):
    fid = db.query_one("SELECT feed_id FROM incoming_supplier_feeds WHERE supplier_sku=?", (sku,))["feed_id"]
    return next(r for r in results if r.feed_id == fid)


@pytest.fixture
def batch():
    return workflow.run_batch(actor="test")


def test_batch_routes_everything(batch):
    assert batch.total == 40 and batch.errors == 0
    assert batch.drafted + batch.quarantined == 40
    assert batch.drafted > 10 and batch.quarantined > 10


def test_gym_shorts_example(batch):
    r = by_sku(batch.results, "ATX-1001")
    assert r.status == "DRAFT" and r.category_id == "APP-SHORTS-M"
    assert r.extracted.pack_count == 2
    assert r.attributes["colors"] == ["Black", "Navy"] and r.attributes["size"] == ["L"]
    assert "(Pack of 2)" in r.clean_title and "Running Workout" not in r.clean_title
    assert {v.sku for v in r.variations} == {"ATX-1001-BLAC-L", "ATX-1001-NAVY-L"}


def test_planted_violations(batch):
    expect = {"PK-S-78": "PROHIBITED_KEYWORD", "Z9-SH-02": "MISSING_REQUIRED_ATTR", "VT-SP-02": "PROHIBITED_KEYWORD",
              "OR-TS-09": "INVALID_PRICE", "SH-XX-01": "PROMPT_INJECTION_DETECTED", "SH-XX-02": "PII_IN_LISTING",
              "MS-XX-01": "LOW_CONFIDENCE", "MS-XX-03": "PROHIBITED_KEYWORD"}
    for sku, code in expect.items():
        r = by_sku(batch.results, sku)
        assert r.status == "QUARANTINED", sku
        assert code in {v.code for v in r.violations}, (sku, [v.code for v in r.violations])


def test_injection_skips_llm(batch):
    r = by_sku(batch.results, "SH-XX-01")
    assert r.classification_attempts == 0 and r.tokens_in == 0


def test_retry_path_exercised(batch):
    assert any(r.classification_attempts > 1 or r.extraction_attempts > 1 for r in batch.results)
    assert all(r.status in ("DRAFT", "QUARANTINED") for r in batch.results)


class AlwaysBadLLM(LLMClient):
    def __init__(self):
        self.provider, self.model, self.cfg = "fake", "always-bad", {"max_tokens": 10}

    def structured(self, **kw):
        return LLMResponse(text='{"category_id": "NOT-A-CATEGORY", "confidence": 0.99, "rationale": "trust me"}',
                           provider="fake", model="always-bad")


def test_out_of_taxonomy_llm_answer_is_quarantined():
    r = workflow.process_listing(1, client=AlwaysBadLLM())
    assert r.status == "QUARANTINED"
    assert [v.code for v in r.violations if v.severity == "BLOCK"] == ["CLASSIFICATION_INVALID"]
    assert r.classification_attempts == 3  # 1 + max_retries


class CompliantClaimLLM(LLMClient):
    """LLM 'says' everything is fine and invents extra keys - schema must reject."""
    def __init__(self):
        self.provider, self.model, self.cfg = "fake", "liar", {"max_tokens": 10}

    def structured(self, task, **kw):
        if task == "classify":
            return LLMResponse(text='{"category_id":"HLTH-SUPP","confidence":0.99,"rationale":"supplement"}',
                               provider="fake", model="liar")
        return LLMResponse(text='{"product_type":"Capsules","compliant":true,"attributes":{"count":90,"form":"Capsule"}}',
                           provider="fake", model="liar")


def test_llm_cannot_assert_compliance():
    fid = db.query_one("SELECT feed_id FROM incoming_supplier_feeds WHERE supplier_sku='VT-SP-02'")["feed_id"]
    r = workflow.process_listing(fid, client=CompliantClaimLLM())
    codes = {v.code for v in r.violations}
    assert r.status == "QUARANTINED" and "EXTRACTION_INVALID" in codes and "PROHIBITED_KEYWORD" in codes


def test_human_review_publish_and_audit(batch):
    r = by_sku(batch.results, "ATX-1001")
    res = workflow.review_decision(r.listing_id, "APPROVE", "alice.reviewer", "looks good")
    assert res["ok"] and res["status"] == "PUBLISHED"
    live = workflow.list_live_catalog()
    assert live[0]["listing_id"] == r.listing_id and live[0]["published_by"] == "alice.reviewer"
    entries = audit.list_audit(entity_id=str(r.listing_id))
    assert entries[0]["action"] == "HUMAN_REVIEW" and entries[0]["actor"] == "alice.reviewer"
    assert entries[0]["request_id"]
    with pytest.raises(ValueError):  # cannot re-process a published listing
        workflow.process_listing(r.feed_id)


def test_quarantined_cannot_be_approved(batch):
    r = by_sku(batch.results, "VT-SP-02")
    res = workflow.review_decision(r.listing_id, "APPROVE", "alice.reviewer")
    assert not res["ok"] and res["status"] == "QUARANTINED"
    assert workflow.list_live_catalog() == []
    assert workflow.review_decision(r.listing_id, "REJECT", "alice.reviewer")["status"] == "REJECTED"


def test_invalid_reviewer_rejected(batch):
    with pytest.raises(ValueError):
        workflow.review_decision(batch.results[0].listing_id, "APPROVE", "<script>")


def test_foreign_keys_enforced():
    with pytest.raises(sqlite3.IntegrityError):
        with db.tx() as c:
            c.execute("INSERT INTO rule_violations(listing_id,code,severity) VALUES (99999,'X','BLOCK')")
    with pytest.raises(sqlite3.IntegrityError):
        with db.tx() as c:
            c.execute("INSERT INTO processed_listings(feed_id,request_id,category_id,status,created_at,updated_at)"
                      " VALUES (1,'r','NO-SUCH-CAT','DRAFT','t','t')")


def test_submit_listing_validation():
    with pytest.raises(ValueError):
        workflow.submit_listing("", "sku", "title")
    r = workflow.submit_listing("SUP-T", "T-1", "Women's High-Waisted Yoga Leggings", raw_size_tag="M",
                                raw_color_tag="Black", raw_price="$20")
    assert r.status == "DRAFT"


def test_kpis_from_records(batch):
    k = analytics.get_kpis()
    assert k["processed"] == 40 and k["drafted"] == batch.drafted
    assert abs(k["quarantine_rate"] - batch.quarantined / 40) < 1e-9
    assert analytics.export_listings_csv().count("\n") == 41
    assert analytics.business_insights()


def test_request_log_written(batch):
    m = diagnostics.ops_metrics()
    assert m["requests"] == 40 and m["error_rate"] == 0
