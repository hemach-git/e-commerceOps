"""Reusable listing detail panel: Raw vs Clean, violations, attributes, variations, audit."""
from __future__ import annotations

import json
from typing import Any

import pandas as pd
import streamlit as st

from app.ui import common


def _rationale(listing: dict) -> str | None:
    raw = listing.get("llm_raw") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None
    cls = raw.get("classification") if isinstance(raw, dict) else None
    if isinstance(cls, str):
        try:
            cls = json.loads(cls)
        except ValueError:
            return cls[:300]
    if isinstance(cls, dict):
        return cls.get("rationale")
    return None


def _kv(label: str, value: Any) -> None:
    v = "—" if value in (None, "", [], {}) else value
    st.markdown(f'<div class="kv"><b>{label}:</b> {common.html.escape(str(v))}</div>',
                unsafe_allow_html=True)


def render(detail: dict, key_prefix: str = "ld") -> None:
    listing = detail.get("listing") or {}
    feed = detail.get("feed") or {}
    category = detail.get("category") or {}
    variations = detail.get("variations") or []
    violations = detail.get("violations") or []
    audit_rows = detail.get("audit") or []

    top = st.columns([3, 1, 1, 1])
    top[0].markdown(f"#### Listing #{listing.get('listing_id')} · feed #{listing.get('feed_id')}")
    with top[1]:
        common.show_pill(listing.get("status"))
    top[2].caption(f"request_id: `{listing.get('request_id', '—')}`")
    top[3].caption(f"updated: {listing.get('updated_at', '—')}")

    raw_col, clean_col = st.columns(2, gap="large")
    with raw_col:
        with st.container(border=True):
            st.markdown("##### 🧾 Raw seller input")
            _kv("Supplier", f"{feed.get('supplier_id', '—')} / {feed.get('supplier_sku', '—')}")
            st.markdown("**Raw title**")
            st.code(feed.get("raw_title") or listing.get("raw_title") or "—", language=None, wrap_lines=True)
            st.markdown("**Raw bullets**")
            st.code(feed.get("raw_bullets") or "—", language=None, wrap_lines=True)
            _kv("Size tag", feed.get("raw_size_tag"))
            _kv("Color tag", feed.get("raw_color_tag"))
            _kv("Price (raw)", feed.get("raw_price"))
    with clean_col:
        with st.container(border=True):
            st.markdown("##### ✨ Cleaned & classified")
            st.markdown("**Clean title**")
            st.code(listing.get("clean_title") or "— (not produced)", language=None, wrap_lines=True)
            cat_name = listing.get("category_name") or category.get("name")
            _kv("Category", f"{cat_name or '—'} ({listing.get('category_id') or '—'})")
            conf = listing.get("category_confidence")
            if conf is not None:
                try:
                    st.progress(min(max(float(conf), 0.0), 1.0), text=f"Confidence {common.pct(conf)}")
                except (TypeError, ValueError):
                    _kv("Confidence", conf)
            _kv("Rationale", _rationale(listing))
            _kv("Price", common.num(listing.get("price"), "${:,.2f}") if listing.get("price") is not None else None)
            c1, c2, c3 = st.columns(3)
            c1.metric("Extraction attempts", listing.get("extraction_attempts", "—"))
            c2.metric("Pack count", listing.get("pack_count") or "—")
            c3.metric("Variations", len(variations))
            _kv("Brand", listing.get("brand"))
            _kv("Product type", listing.get("product_type"))
            _kv("Gender", listing.get("gender"))
            _kv("Material", listing.get("material"))

    st.markdown("##### 🚦 Compliance & validation")
    if violations:
        vdf = common.df(violations, ["code", "severity", "field", "detail"])
        vdf = vdf.rename(columns={"detail": "matched evidence / detail"})
        if "severity" in vdf.columns:
            vdf["severity"] = vdf["severity"].map(lambda s: "⛔ BLOCK" if s == "BLOCK" else ("⚠️ WARN" if s == "WARN" else s))
        st.dataframe(vdf, hide_index=True, width="stretch")
    else:
        st.success("No rule violations — passed validation and compliance checks.")

    a_col, v_col = st.columns(2, gap="large")
    with a_col:
        st.markdown("##### 🏷️ Attributes")
        attrs = listing.get("attributes") or {}
        required = {a.get("name"): a for a in (category.get("required_attributes") or []) if isinstance(a, dict)}
        names = list(dict.fromkeys(list(required.keys()) + list(attrs.keys())))
        if names:
            rows = []
            for n in names:
                spec = required.get(n, {})
                val = attrs.get(n)
                rows.append({
                    "attribute": n,
                    "value": common._fmt_cell(val) if val not in (None, "", []) else "—",
                    "required": "yes" if spec.get("required") else ("no" if spec else "extra"),
                    "allowed": ", ".join(spec.get("allowed") or []) if spec.get("allowed") else "",
                    "present": "✅" if val not in (None, "", []) else ("❌" if spec.get("required") else "—"),
                })
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        else:
            common.empty("No attributes extracted.")
    with v_col:
        st.markdown("##### 🎨 Variations")
        if variations:
            st.dataframe(common.df(variations, ["sku", "size", "color"]), hide_index=True, width="stretch")
        else:
            common.empty("No variations.")

    with st.expander("🔍 Raw LLM output (classification / extraction)"):
        raw = listing.get("llm_raw")
        if raw:
            st.json(raw, expanded=False)
        else:
            st.caption("No LLM output stored.")

    with st.expander(f"📜 Audit trail for this listing ({len(audit_rows)})", expanded=False):
        if audit_rows:
            st.dataframe(common.stringify_complex(common.df(audit_rows)), hide_index=True, width="stretch")
        else:
            st.caption("No audit entries.")
