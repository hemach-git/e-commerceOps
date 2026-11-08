"""Analytics — KPIs, quarantine reasons, status by category, run history, drill-down, export."""
import pandas as pd
import plotly.express as px
import streamlit as st

from app.ui import common, listing_detail

workflow, analytics, audit, diagnostics = common.services()

common.show_flash()
common.header("Analytics", "All metrics computed from pipeline records in the local database.")

STATUS_PALETTE = {"DRAFT": "#34A853", "QUARANTINED": "#EA4335", "PUBLISHED": "#1A73E8", "REJECTED": "#9AA0A6"}

kpis = common.safe_call(analytics.get_kpis, default={}, label="Loading KPIs") or {}
k = st.columns(5)
k[0].metric("Publish-ready rate", common.pct(kpis.get("publish_ready_rate")), help="Share of processed listings routed to Draft")
k[1].metric("Quarantine rate", common.pct(kpis.get("quarantine_rate")))
k[2].metric("Approval rate", common.pct(kpis.get("approval_rate")), help="Approved / (approved + rejected)")
k[3].metric("Attribute completeness", common.pct(kpis.get("attr_completeness_rate")))
k[4].metric("Avg confidence", common.pct(kpis.get("avg_confidence")))
k2 = st.columns(5)
k2[0].metric("Processed", common.num(kpis.get("processed")))
k2[1].metric("Drafted", common.num(kpis.get("drafted")))
k2[2].metric("Quarantined", common.num(kpis.get("quarantined")))
k2[3].metric("Published", common.num(kpis.get("published")))
k2[4].metric("LLM retry rate", common.pct(kpis.get("llm_retry_rate")))
st.caption("⚠ **Synthetic data** — figures reflect seeded demo feeds, not production traffic.")

insights = common.safe_call(analytics.business_insights, default=[], label="Loading insights") or []
if insights:
    with st.container(border=True):
        st.markdown("##### 💡 Insights")
        for s in insights:
            st.markdown(f"- {s}")

st.divider()
c1, c2 = st.columns(2, gap="large")
with c1:
    st.markdown("##### Quarantine reasons")
    qr = common.df(common.safe_call(analytics.quarantine_reasons, default=[], label="Loading quarantine reasons"))
    if qr.empty:
        common.empty("No quarantined listings yet.")
    else:
        qr = qr.sort_values("count", ascending=True)
        fig = px.bar(qr, x="count", y="reason", orientation="h", color_discrete_sequence=["#EA4335"])
        fig.update_layout(height=340, margin=dict(l=0, r=10, t=10, b=0), yaxis_title=None, xaxis_title="listings")
        st.plotly_chart(fig, width="stretch")
with c2:
    st.markdown("##### Status by category")
    sc = common.df(common.safe_call(analytics.status_by_category, default=[], label="Loading status by category"))
    if sc.empty:
        common.empty("No processed listings yet.")
    else:
        sc["category"] = sc["category_name"].fillna(sc["category_id"]).fillna("Unclassified")
        fig = px.bar(sc, x="category", y="count", color="status", color_discrete_map=STATUS_PALETTE)
        fig.update_layout(barmode="stack", height=340, margin=dict(l=0, r=10, t=10, b=0),
                          xaxis_title=None, yaxis_title="listings", legend_title=None)
        st.plotly_chart(fig, width="stretch")

st.markdown("##### Run history")
rh = common.df(common.safe_call(analytics.run_history, default=[], label="Loading run history"))
if rh.empty:
    common.empty("No batch runs yet.")
else:
    rh = rh.sort_values("started_at")
    long = rh.melt(id_vars=["run_id", "started_at"], value_vars=[c for c in ["drafted", "quarantined", "errors"] if c in rh],
                   var_name="outcome", value_name="listings")
    fig = px.bar(long, x="started_at", y="listings", color="outcome", hover_data=["run_id"],
                 color_discrete_map={"drafted": "#34A853", "quarantined": "#EA4335", "errors": "#FBBC04"})
    fig.update_layout(barmode="stack", height=300, margin=dict(l=0, r=10, t=10, b=0), xaxis_title=None, legend_title=None)
    st.plotly_chart(fig, width="stretch")
    with st.expander("Run history table"):
        st.dataframe(rh, hide_index=True, width="stretch")

st.divider()
st.markdown("##### 🔎 Listings drill-down")
cats = common.safe_call(workflow.list_categories, default=[], label="Loading categories") or []
f1, f2, f3 = st.columns([1, 2, 2])
status = f1.selectbox("Status", ["All", "DRAFT", "QUARANTINED", "PUBLISHED", "REJECTED"], key="an_status")
cat_opts = {"All": None} | {f"{c.get('name')} ({c.get('category_id')})": c.get("category_id") for c in cats if c.get("is_leaf", True)}
cat_label = f2.selectbox("Category", list(cat_opts.keys()), key="an_cat")
reason = f3.text_input("Reason code prefix", placeholder="e.g. PROHIBITED_KEYWORD", key="an_reason")

rows = common.safe_call(workflow.list_listings, status=None if status == "All" else status,
                        reason=reason.strip() or None, category_id=cat_opts[cat_label],
                        default=[], label="Loading listings") or []
if not rows:
    common.empty("No listings match these filters.")
else:
    frame = common.df(rows, ["listing_id", "status", "clean_title", "category_name", "category_confidence",
                             "price", "reason_codes", "created_at"])
    frame["status"] = frame["status"].map(common.status_label)
    event = st.dataframe(frame, hide_index=True, width="stretch", height=300, on_select="rerun",
                         selection_mode="single-row", key="an_table",
                         column_config={"category_confidence": st.column_config.ProgressColumn(
                             "confidence", min_value=0.0, max_value=1.0, format="percent"),
                             "price": st.column_config.NumberColumn("price", format="$%.2f")})
    st.caption(f"{len(rows)} listing(s). Select a row to drill down.")
    sel = event.selection.rows if event and event.selection else []
    if sel:
        lid = int(rows[sel[0]]["listing_id"])
        detail = common.safe_call(workflow.get_listing_detail, lid, default=None, label="Loading listing detail")
        if detail:
            with st.container(border=True):
                listing_detail.render(detail, key_prefix="an")
        else:
            common.empty(f"Listing #{lid} not found.")

csv_status = None if status == "All" else status
csv = common.safe_call(analytics.export_listings_csv, csv_status, default=None, label="CSV export")
if csv is not None:
    st.download_button("⬇ Export listings CSV" + (f" ({csv_status})" if csv_status else ""), data=csv,
                       file_name=f"listings_{(csv_status or 'all').lower()}.csv", mime="text/csv")
