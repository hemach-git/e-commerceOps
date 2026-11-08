"""Review Queue — human-in-the-loop approval of Draft listings; inspection of Quarantine."""
import streamlit as st

from app.ui import common, listing_detail

workflow, analytics, audit, diagnostics = common.services()

common.show_flash()
common.header("Review Queue",
              "Human-in-the-loop gate: only a named reviewer can publish a Draft. Quarantined listings cannot be approved.")

LIST_COLS = ["listing_id", "status", "clean_title", "category_name", "category_confidence", "price",
             "reason_codes", "extraction_attempts", "updated_at"]
COL_CFG = {
    "category_confidence": st.column_config.ProgressColumn("confidence", min_value=0.0, max_value=1.0, format="percent"),
    "price": st.column_config.NumberColumn("price", format="$%.2f"),
}


def _queue(status: str, key: str) -> int | None:
    rows = common.safe_call(workflow.list_listings, status=status, default=[], label=f"Loading {status} listings") or []
    if not rows:
        common.empty("Queue is empty." + (" Run a batch on **Ingest** to create drafts." if status == "DRAFT" else ""))
        return None
    frame = common.df(rows, LIST_COLS)
    frame["status"] = frame["status"].map(common.status_label)
    event = st.dataframe(frame, hide_index=True, width="stretch", height=280, column_config=COL_CFG,
                         on_select="rerun", selection_mode="single-row", key=f"rq_table_{key}")
    sel = event.selection.rows if event and event.selection else []
    ids = [int(r["listing_id"]) for r in rows]
    if sel:
        return ids[sel[0]]
    default = st.session_state.get(f"rq_sel_{key}")
    choice = st.selectbox("…or pick a listing", ids, index=ids.index(default) if default in ids else 0,
                          format_func=lambda i: f"#{i} — {next((r.get('clean_title') or r.get('raw_title') or '') for r in rows if r['listing_id'] == i)[:90]}",
                          key=f"rq_pick_{key}")
    return choice


def _decision_panel(detail: dict, key: str) -> None:
    listing = detail.get("listing") or {}
    lid = listing.get("listing_id")
    status = listing.get("status")
    st.markdown("##### ✅ Reviewer decision")
    with st.container(border=True):
        reviewer = st.session_state.get("rq_reviewer", "") or ""
        st.caption(f"Reviewer: **{reviewer or '— set your name above the queue —'}**")
        note = st.text_input("Note (optional)", key=f"rq_note_{key}_{lid}")
        can_approve = status == "DRAFT"
        b1, b2, b3 = st.columns([1, 1, 3])
        approve = b1.button("✅ Approve & publish", type="primary", disabled=not can_approve,
                            key=f"rq_appr_{key}_{lid}", width="stretch")
        reject = b2.button("❌ Reject", disabled=status not in ("DRAFT", "QUARANTINED"),
                           key=f"rq_rej_{key}_{lid}", width="stretch")
        if not can_approve:
            b3.caption("🔒 Approve is disabled: compliance gate — only DRAFT listings can be published."
                       if status == "QUARANTINED" else f"Listing is already {status}.")
        if approve or reject:
            if not reviewer.strip():
                st.error("Enter a reviewer name before deciding.")
                return
            decision = "APPROVE" if approve else "REJECT"
            with st.spinner(f"Recording {decision}…"):
                try:
                    res = workflow.review_decision(lid, decision, reviewer.strip(), note)
                except ValueError as e:
                    st.error(f"Decision rejected: {e}")
                    return
                except Exception as e:  # noqa: BLE001
                    st.error(f"Decision failed: {type(e).__name__}: {e}")
                    return
            res = common.as_dict(res) or {}
            common.invalidate()
            st.session_state["rq_last_decision"] = {"listing_id": lid, "decision": decision, **res}
            if res.get("ok"):
                common.flash("success", f"Listing #{lid}: {decision} recorded → status **{res.get('status')}**. {res.get('message', '')}")
            else:
                common.flash("error", f"Listing #{lid}: {decision} not applied. {res.get('message', '')}")
            st.rerun()


last = st.session_state.get("rq_last_decision")
if last:
    with st.expander(f"🧾 Last decision: #{last.get('listing_id')} {last.get('decision')} → {last.get('status')}", expanded=True):
        st.write(last.get("message", ""))
        entries = [a for a in (common.safe_call(audit.list_audit, 50, default=[], label="Loading audit") or [])
                   if str(a.get("entity_id")) == str(last.get("listing_id"))]
        if entries:
            st.markdown("**Audit entry**")
            st.dataframe(common.stringify_complex(common.df(entries[:3])), hide_index=True, width="stretch")
        else:
            st.caption("No matching audit entry found.")

st.text_input("Reviewer name * (recorded in the audit trail)", key="rq_reviewer", placeholder="e.g. j.smith")

tab_d, tab_q = st.tabs(["🟢 Draft (awaiting approval)", "🔴 Quarantine"])
for tab, status, key in ((tab_d, "DRAFT", "d"), (tab_q, "QUARANTINED", "q")):
    with tab:
        lid = _queue(status, key)
        if lid is None:
            continue
        st.session_state[f"rq_sel_{key}"] = lid
        detail = common.safe_call(workflow.get_listing_detail, lid, default=None, label="Loading listing detail")
        if not detail:
            common.empty(f"Listing #{lid} not found (it may have just been moved).")
            continue
        st.divider()
        listing_detail.render(detail, key_prefix=f"rq_{key}")
        _decision_panel(detail, key)
