"""Diagnostics / Governance — ops metrics, requests, evals, config, audit, taxonomy."""
import pandas as pd
import streamlit as st

from app.ui import common

workflow, analytics, audit, diagnostics = common.services()

common.show_flash()
common.header("Diagnostics & Governance", "Operational health, evaluation results, configuration and the audit trail.")

SECRET_HINTS = ("key", "secret", "token", "password")


def _redact(obj):
    """Defensive: never render anything that looks like a secret value."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if any(h in kl for h in SECRET_HINTS) and not isinstance(v, (bool, int, float, dict, list)) and v:
                if not (kl.endswith("_configured") or kl in ("max_tokens",)):
                    out[k] = "••••• (redacted)"
                    continue
            out[k] = _redact(v)
        return out
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


t_ops, t_eval, t_audit, t_tax, t_cfg = st.tabs(
    ["⚙️ Ops & requests", "🧪 Evaluations", "📜 Audit trail", "🗂️ Taxonomy", "🔧 Config"])

with t_ops:
    ops = common.safe_call(diagnostics.ops_metrics, default={}, label="Loading ops metrics") or {}
    m = st.columns(6)
    m[0].metric("Requests", common.num(ops.get("requests")))
    m[1].metric("Error rate", common.pct(ops.get("error_rate")))
    m[2].metric("Avg latency", common.num(ops.get("avg_latency_ms"), "{:,.0f} ms"))
    m[3].metric("p95 latency", common.num(ops.get("p95_latency_ms"), "{:,.0f} ms"))
    m[4].metric("Tokens in", common.num(ops.get("tokens_in")))
    m[5].metric("Tokens out", common.num(ops.get("tokens_out")))
    st.markdown("##### Recent requests")
    reqs = common.safe_call(diagnostics.recent_requests, 100, default=[], label="Loading recent requests") or []
    if reqs:
        rdf = common.df(reqs, ["ts", "request_id", "operation", "status", "duration_ms", "tokens_in", "tokens_out", "error"])
        rdf["status"] = rdf["status"].map(common.status_label)
        only_err = st.toggle("Errors only", key="dg_err_only")
        if only_err:
            rdf = rdf[rdf["status"].str.contains("error", na=False)]
        st.dataframe(rdf, hide_index=True, width="stretch", height=380)
    else:
        common.empty("No requests logged yet — run a batch on **Ingest**.")

with t_eval:
    ev = common.safe_call(diagnostics.latest_eval_summary, default=None, label="Loading eval summary")
    if not ev:
        common.empty("No evaluation results found. Run the eval suite (see `evals/`) to populate this view.")
    else:
        m = st.columns(4)
        m[0].metric("Pass rate", common.pct(ev.get("pass_rate")))
        m[1].metric("Passed", f"{ev.get('passed', 0)} / {ev.get('total', 0)}")
        m[2].metric("Failures", len(ev.get("failures") or []))
        m[3].metric("Run at", str(ev.get("ts", "—"))[:19])
        metrics = ev.get("metrics") or {}
        if metrics:
            st.markdown("##### Metrics")
            mdf = pd.DataFrame([{"metric": k, "value": common._fmt_cell(v)} for k, v in metrics.items()])
            st.dataframe(mdf, hide_index=True, width="stretch")
        fails = ev.get("failures") or []
        st.markdown("##### Failures")
        if fails:
            fdf = pd.DataFrame(fails) if isinstance(fails[0], dict) else pd.DataFrame({"failure": fails})
            st.dataframe(common.stringify_complex(fdf), hide_index=True, width="stretch")
        else:
            st.success("All eval cases passed.")

with t_audit:
    rows = common.safe_call(audit.list_audit, 500, default=[], label="Loading audit trail") or []
    if not rows:
        common.empty("Audit trail is empty.")
    else:
        adf = common.df(rows, ["ts", "actor", "action", "entity_type", "entity_id", "decision", "model", "request_id", "details"])
        f1, f2 = st.columns(2)
        actions = sorted(adf["action"].dropna().unique().tolist()) if "action" in adf else []
        pick = f1.multiselect("Action", actions, key="dg_actions")
        actor = f2.text_input("Actor contains", key="dg_actor")
        if pick:
            adf = adf[adf["action"].isin(pick)]
        if actor:
            adf = adf[adf["actor"].fillna("").str.contains(actor, case=False, regex=False)]
        st.dataframe(common.stringify_complex(adf), hide_index=True, width="stretch", height=420)
        st.caption(f"{len(adf)} entr(ies). Every pipeline decision and human review is recorded.")

with t_tax:
    cats = common.safe_call(workflow.list_categories, default=[], label="Loading taxonomy") or []
    if not cats:
        common.empty("No categories loaded.")
    else:
        rows = []
        for c in cats:
            req = c.get("required_attributes") or []
            rows.append({
                "category_id": c.get("category_id"),
                "name": c.get("name"),
                "parent_id": c.get("parent_id"),
                "leaf": "✅" if c.get("is_leaf") else "",
                "required_attributes": ", ".join(a.get("name", "") for a in req if a.get("required", True)),
                "optional_attributes": ", ".join(a.get("name", "") for a in req if not a.get("required", True)),
                "prohibited_keywords": ", ".join(c.get("prohibited_keywords") or []),
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        leaf = [c for c in cats if c.get("required_attributes")]
        if leaf:
            pick = st.selectbox("Inspect attribute spec", [f"{c['name']} ({c['category_id']})" for c in leaf], key="dg_cat")
            c = leaf[[f"{x['name']} ({x['category_id']})" for x in leaf].index(pick)]
            st.dataframe(common.stringify_complex(common.df(c.get("required_attributes"),
                         ["name", "type", "required", "allowed", "min", "max"])), hide_index=True, width="stretch")

with t_cfg:
    cfg = _redact(common.safe_call(diagnostics.get_config_snapshot, default={}, label="Loading config") or {})
    llm = cfg.get("llm", {}) if isinstance(cfg, dict) else {}
    m = st.columns(3)
    m[0].metric("Provider", llm.get("provider", "—"))
    m[1].metric("Model", llm.get("model", "—"))
    m[2].metric("API key configured", "Yes" if llm.get("api_key_configured") else "No")
    st.caption("Secrets are read from environment variables only and are never displayed.")
    st.json(cfg, expanded=1)
    st.divider()
    confirm = st.checkbox("I understand reset deletes all drafts, approvals and audit history", key="dg_reset_confirm")
    if st.button("🔄 Reset demo data", disabled=not confirm):
        with st.spinner("Resetting demo database…"):
            ok = common.safe_call(workflow.reset_demo, default=False, label="Reset demo")
        if ok is not False:
            common.invalidate()
            st.session_state.pop("ing_last_batch", None)
            common.flash("success", "Demo data reset and reseeded.")
            st.rerun()
