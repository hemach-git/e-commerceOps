"""Seller-SKU rules, marketplace SKUs, grounding check, schema upgrade and the Ollama provider."""
import json

import httpx
import pytest

from app.config import get_config
from app.data import db
from app.data.seed_synthetic import CATEGORIES
from app.services import rules, workflow
from app.services.llm import LLMClient, LLMError
from app.services.sku import marketplace_skus

LEGGINGS = dict(raw_title="Women's High-Waisted Yoga Leggings", raw_size_tag="S/M", raw_color_tag="Black",
                raw_price="$20")


def codes(result):
    return {v.code for v in result.violations}


@pytest.mark.parametrize("sku,code", [("", "MISSING_SELLER_SKU"), ("   ", "MISSING_SELLER_SKU"),
                                      ("bad sku!", "INVALID_SELLER_SKU"), ("X", "INVALID_SELLER_SKU")])
def test_missing_or_invalid_seller_sku_is_quarantined(sku, code):
    r = workflow.submit_listing("SUP-T", sku, **LEGGINGS)
    assert r.status == "QUARANTINED" and code in codes(r)


def test_two_missing_skus_do_not_crash():
    for _ in range(2):
        assert "MISSING_SELLER_SKU" in codes(workflow.submit_listing("SUP-T", "", **LEGGINGS))


def test_first_listing_keeps_a_seller_sku_and_reuse_is_quarantined():
    first = workflow.submit_listing("SUP-T", "DUP-1", **LEGGINGS)
    second = workflow.submit_listing("SUP-T", "DUP-1", **LEGGINGS)
    other_supplier = workflow.submit_listing("SUP-U", "DUP-1", **LEGGINGS)
    assert first.status == "DRAFT" and other_supplier.status == "DRAFT"
    assert second.status == "QUARANTINED" and "DUPLICATE_SELLER_SKU" in codes(second)
    assert not workflow.review_decision(second.listing_id, "APPROVE", "alice.reviewer")["ok"]  # no override
    again = workflow.process_listing(second.feed_id)  # re-processing keeps the verdict
    assert "DUPLICATE_SELLER_SKU" in codes(again)


def test_seed_listings_raise_no_new_violations():
    batch = workflow.run_batch(actor="test")
    new = {"MISSING_SELLER_SKU", "INVALID_SELLER_SKU", "DUPLICATE_SELLER_SKU", "UNGROUNDED_ATTR"}
    assert not [(r.feed_id, v.detail) for r in batch.results for v in r.violations if v.code in new]


def test_micro_usb_cable_is_not_read_as_usb_c():
    r = workflow.submit_listing("SUP-T", "CB-1", "Micro USB Cable 2 Pack Nylon Braided", raw_price="$8")
    assert r.attributes["connector_type"] == "Micro-USB"


def test_skus_created_only_on_approval():
    r = workflow.submit_listing("SUP-T", "LG-1", **LEGGINGS)
    assert workflow.get_listing_detail(r.listing_id)["marketplace_skus"] == []
    res = workflow.review_decision(r.listing_id, "APPROVE", "alice.reviewer")
    first = f"MP-011-{r.feed_id:06d}-01"
    assert res["marketplace_skus"] == [first, f"MP-011-{r.feed_id:06d}-02"]
    assert workflow.get_by_sku(first.lower())["supplier_sku"] == "LG-1"
    assert workflow.list_live_catalog()[0]["marketplace_skus"] == res["marketplace_skus"]


def test_rejection_creates_no_sku():
    r = workflow.submit_listing("SUP-T", "LG-2", **LEGGINGS)
    assert workflow.review_decision(r.listing_id, "REJECT", "alice.reviewer")["marketplace_skus"] == []
    assert db.query("SELECT * FROM catalog_skus") == []


def test_multipack_is_one_sellable_unit():
    variations = [{"size": "L", "color": "Black"}, {"size": "L", "color": "Navy"}]
    rows = marketplace_skus("APP-SHORTS-M", 1, 2, variations)
    assert [(r["sku"], r["color"], r["size"]) for r in rows] == [("MP-010-000001-01", "Black/Navy", "L")]
    assert len(marketplace_skus("APP-SHORTS-M", 1, 1, variations)) == 2
    assert len(marketplace_skus("HLTH-SUPP", 1, 1, [])) == 1


def test_every_leaf_category_has_a_unique_sku_code():
    leaves = {c[0] for c in CATEGORIES if c[2]}
    table = get_config()["sku"]["category_codes"]
    assert leaves <= set(table) and len(set(table.values())) == len(table)


def test_grounding_accepts_aliases_and_numbers():
    text = "Mens USB C 20W charger, Lrg, Blk, 120 softgels, US 10.5"
    attrs = {"gender": "Men", "connector_type": "USB-C", "wattage": 20, "size": ["L", "10.5"], "colors": ["Black"],
             "form": "Softgel", "count": 120}
    assert rules.check_grounding(attrs, text) == []


def test_grounding_rejects_invented_values():
    found = rules.check_grounding({"wattage": 65, "colors": ["Black", "Pink"], "material": "Leather",
                                   "gender": "Men"}, "Womens 20W charger in black, 650 cycles")
    assert {v.field for v in found} == {"wattage", "colors", "material", "gender"}


def test_old_database_is_upgraded(tmp_path, monkeypatch):
    monkeypatch.setenv("CATALOG_DB_PATH", str(tmp_path / "old.db"))
    with db.tx() as c:
        c.execute("CREATE TABLE incoming_supplier_feeds (feed_id INTEGER PRIMARY KEY, supplier_id TEXT,"
                  " supplier_sku TEXT, status TEXT)")
    db.init_schema()
    assert "declared_sku" in {r["name"] for r in db.query("PRAGMA table_info(incoming_supplier_feeds)")}


def _fake_ollama(monkeypatch, models, reply=None):
    request = httpx.Request("GET", "http://ollama.test")
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(
        200, json={"models": [{"name": m} for m in models]}, request=request))
    monkeypatch.setattr(httpx, "post", lambda url, json=None, **kw: httpx.Response(
        200, json=reply(json), request=request))


def test_ollama_returns_text_and_tokens(monkeypatch):
    seen = {}

    def reply(body):
        seen.update(body)
        return {"message": {"content": json.dumps({"ok": 1})}, "prompt_eval_count": 7, "eval_count": 3}

    _fake_ollama(monkeypatch, ["llama3:8b"], reply)
    out = LLMClient(provider="ollama").structured(task="classify", system="s", user="u",
                                                  schema={"type": "object"}, attempt=1)
    assert (out.text, out.tokens_in, out.tokens_out, out.provider) == ('{"ok": 1}', 7, 3, "ollama")
    assert seen["model"] == "llama3:8b" and seen["format"] == {"type": "object"} and seen["stream"] is False


def test_ollama_missing_model_falls_back_to_mock(monkeypatch):
    _fake_ollama(monkeypatch, ["something-else"])
    with pytest.raises(LLMError, match="not installed"):
        LLMClient(provider="ollama")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    workflow._client = None
    assert workflow.llm_status()["provider"] == "mock" and workflow.llm_status()["fallback_reason"]
