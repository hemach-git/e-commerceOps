"""Home / Overview."""
import streamlit as st

from app.ui import common

workflow, analytics, audit, diagnostics = common.services()

common.show_flash()
common.header("Marketplace Catalog Ingestion Engine",
              "AI-assisted onboarding of third-party seller listings into the canonical catalog.")

st.markdown(
    "Third-party sellers send messy, inconsistent listings — keyword-stuffed titles, size and colour "
    "variations crammed into free text, missing attributes and occasionally non-compliant claims. "
    "This engine **cleans titles and variations**, **classifies** each listing into the canonical "
    "marketplace taxonomy with an LLM, **validates required attributes** per category, and enforces "
    "**deterministic compliance rules** (prohibited keywords, unsafe claims, prompt-injection attempts). "
    "Every listing is routed either to the **Draft Catalog**, where a human reviewer approves it into the "
    "**Live Catalog**, or to **Quarantine** with explicit reason codes. The LLM never publishes: "
    "only a named human reviewer can approve, and quarantined listings can't be approved."
)

st.subheader("Pipeline")
common.stage_strip()

kpis = common.safe_call(analytics.get_kpis, default={}, label="Loading KPIs") or {}

st.subheader("Headline KPIs")
r1 = st.columns(5)
r1[0].metric("Feeds received", common.num(kpis.get("feeds_total")))
r1[1].metric("Processed", common.num(kpis.get("processed")))
r1[2].metric("Pending review (Draft)", common.num(kpis.get("pending_review", kpis.get("drafted"))))
r1[3].metric("Quarantined", common.num(kpis.get("quarantined")))
r1[4].metric("Published (Live)", common.num(kpis.get("published")))
r2 = st.columns(5)
r2[0].metric("Publish-ready rate", common.pct(kpis.get("publish_ready_rate")))
r2[1].metric("Quarantine rate", common.pct(kpis.get("quarantine_rate")))
r2[2].metric("Approval rate", common.pct(kpis.get("approval_rate")))
r2[3].metric("Avg category confidence", common.pct(kpis.get("avg_confidence")))
r2[4].metric("Attribute completeness", common.pct(kpis.get("attr_completeness_rate")))

if kpis.get("is_synthetic", True):
    st.caption("⚠ All figures are computed from **synthetic demo data** seeded locally — not real seller data.")

if not kpis.get("processed"):
    common.empty("No listings processed yet — go to **Ingest** and run a batch to populate the pipeline.")

st.divider()
left, right = st.columns([3, 1])
with left:
    st.markdown("**Demo controls** — reset drops the local database and reseeds synthetic feeds. "
                "Drafts, approvals and audit history will be lost.")
with right:
    confirm = st.checkbox("I understand", key="home_reset_confirm")
    if st.button("🔄 Reset demo data", type="secondary", disabled=not confirm, width="stretch"):
        with st.spinner("Resetting demo database…"):
            ok = common.safe_call(workflow.reset_demo, default=False, label="Reset demo")
        if ok is not False:
            common.invalidate()
            st.session_state.pop("ing_last_batch", None)
            common.flash("success", "Demo data reset and reseeded.")
            st.rerun()
