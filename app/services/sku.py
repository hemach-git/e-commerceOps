"""Marketplace SKUs, created only when a human approves a listing.

Format: MP-<category code:3>-<feed_id:6>-<variant:2>, e.g. MP-010-000001-01.
One SKU per sellable variant. A multi-pack is ONE sellable unit, so it gets one SKU."""
from __future__ import annotations

import sqlite3

from app.config import get_config


def marketplace_skus(category_id: str, feed_id: int, pack_count: int | None, variations: list[dict]) -> list[dict]:
    codes = get_config()["sku"]["category_codes"]
    if category_id not in codes:
        raise ValueError(f"no marketplace SKU code for category {category_id}; add it to config.yaml sku.category_codes")
    if pack_count and pack_count > 1:
        units = [{"size": _joined(variations, "size"), "color": _joined(variations, "color")}]
    else:
        units = list({(v.get("size"), v.get("color")): {"size": v.get("size"), "color": v.get("color")}
                      for v in variations}.values()) or [{"size": None, "color": None}]
    return [{"sku": f"MP-{codes[category_id]:03d}-{feed_id:06d}-{i:02d}", "variant_index": i, **u}
            for i, u in enumerate(units, start=1)]


def _joined(variations: list[dict], key: str) -> str | None:
    return "/".join(dict.fromkeys(v[key] for v in variations if v.get(key))) or None


def create_for_listing(c: sqlite3.Connection, listing: dict, *, approver: str, created_at: str) -> list[str]:
    """Insert the SKUs for an approved listing inside the caller's transaction."""
    feed = c.execute("SELECT supplier_id, supplier_sku FROM incoming_supplier_feeds WHERE feed_id=?",
                     (listing["feed_id"],)).fetchone()
    variations = [dict(r) for r in c.execute(
        "SELECT size, color FROM listing_variations WHERE listing_id=? ORDER BY variation_id",
        (listing["listing_id"],))]
    rows = marketplace_skus(listing["category_id"], listing["feed_id"], listing["pack_count"], variations)
    c.executemany(
        "INSERT INTO catalog_skus(sku,listing_id,feed_id,variant_index,size,color,supplier_id,supplier_sku,"
        "category_id,title,approved_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [(r["sku"], listing["listing_id"], listing["feed_id"], r["variant_index"], r["size"], r["color"],
          feed["supplier_id"], feed["supplier_sku"], listing["category_id"], listing["clean_title"],
          approver, created_at) for r in rows])
    return [r["sku"] for r in rows]
