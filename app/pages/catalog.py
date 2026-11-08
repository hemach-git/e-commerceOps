"""Live Catalog — human-approved, published listings."""
import streamlit as st

from app.ui import common

workflow, analytics, audit, diagnostics = common.services()

common.show_flash()
common.header("Live Catalog", "Listings approved by a human reviewer and published to the marketplace.")

rows = common.safe_call(workflow.list_live_catalog, default=[], label="Loading live catalog") or []
cats = common.safe_call(workflow.list_categories, default=[], label="Loading categories") or []
cat_names = {c.get("category_id"): c.get("name") for c in cats}

if not rows:
    common.empty("No published listings yet. Approve a Draft in the **Review Queue** to publish it.")
    st.stop()

frame = common.df(rows)
frame["category"] = frame["category_id"].map(lambda c: f"{cat_names.get(c, c)} ({c})")

f1, f2 = st.columns([2, 2])
options = sorted(frame["category"].dropna().unique().tolist())
chosen = f1.multiselect("Filter by category", options, key="cat_filter")
query = f2.text_input("Search title", key="cat_search")
if chosen:
    frame = frame[frame["category"].isin(chosen)]
if query:
    frame = frame[frame["title"].fillna("").str.contains(query, case=False, regex=False)]

m = st.columns(3)
m[0].metric("Published listings", len(frame))
m[1].metric("Categories", frame["category_id"].nunique())
m[2].metric("Avg price", common.num(frame["price"].mean() if "price" in frame and len(frame) else None, "${:,.2f}"))

cols = ["catalog_id", "listing_id", "title", "category", "price", "attributes", "published_at", "published_by"]
show = common.stringify_complex(frame[[c for c in cols if c in frame.columns]])
st.dataframe(show, hide_index=True, width="stretch",
             column_config={"price": st.column_config.NumberColumn("price", format="$%.2f")})
st.caption("⚠ Synthetic demo data.")
