"""Shared UI helpers: styling, pills, badges, safe service calls, formatting."""
from __future__ import annotations

import html
from typing import Any, Callable

import pandas as pd
import streamlit as st

STATUS_COLORS = {
    "DRAFT": ("#E6F4EA", "#137333"),
    "PUBLISHED": ("#E8F0FE", "#1A56DB"),
    "QUARANTINED": ("#FCE8E6", "#C5221F"),
    "REJECTED": ("#F1F3F4", "#5F6368"),
    "PENDING": ("#FEF7E0", "#B06000"),
    "PROCESSED": ("#E8F0FE", "#1A56DB"),
    "BLOCK": ("#FCE8E6", "#C5221F"),
    "WARN": ("#FEF7E0", "#B06000"),
    "OK": ("#E6F4EA", "#137333"),
    "ERROR": ("#FCE8E6", "#C5221F"),
}

STATUS_EMOJI = {
    "DRAFT": "🟢 DRAFT",
    "PUBLISHED": "🔵 PUBLISHED",
    "QUARANTINED": "🔴 QUARANTINED",
    "REJECTED": "⚪ REJECTED",
    "PENDING": "🟡 PENDING",
    "PROCESSED": "🔵 PROCESSED",
    "ok": "🟢 ok",
    "error": "🔴 error",
}

PIPELINE_STAGES = ["Ingest", "Clean", "Classify", "Validate", "Comply",
                   "Draft / Quarantine", "Human Review", "Live"]

_CSS = """
<style>
.block-container {padding-top: 2rem;}
.pill {display:inline-block;padding:2px 10px;border-radius:999px;font-size:0.78rem;
       font-weight:600;letter-spacing:.02em;margin-right:4px;}
.badge {display:inline-block;padding:3px 10px;border-radius:6px;font-size:0.78rem;
        font-weight:600;margin-right:6px;border:1px solid transparent;}
.badge-synth {background:#FEF7E0;color:#8A4B00;border-color:#F9D88A;}
.badge-llm {background:#E8F0FE;color:#1F4E79;border-color:#B6CCF5;}
.stage-strip {display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:0.5rem 0 1rem 0;}
.stage {background:#F3F6FA;border:1px solid #D5DEEA;border-radius:8px;padding:6px 12px;
        font-size:0.85rem;font-weight:600;color:#1F4E79;}
.stage-arrow {color:#8A98AB;font-weight:700;}
div[data-testid="stMetric"] {background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;
        padding:10px 14px;}
.kv {font-size:0.9rem;margin:2px 0;}
.kv b {color:#475569;}
</style>
"""


def apply_style() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def as_dict(obj: Any) -> Any:
    """Pydantic model -> dict; dict/list returned unchanged."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj


def pill(status: str | None) -> str:
    s = (status or "").upper() or "—"
    bg, fg = STATUS_COLORS.get(s, ("#F1F3F4", "#3C4043"))
    return f'<span class="pill" style="background:{bg};color:{fg}">{html.escape(s)}</span>'


def show_pill(status: str | None) -> None:
    st.markdown(pill(status), unsafe_allow_html=True)


def status_label(status: str | None) -> str:
    """Emoji label usable inside dataframes."""
    if status is None:
        return "—"
    return STATUS_EMOJI.get(status, STATUS_EMOJI.get(str(status).upper(), str(status)))


def synthetic_badge(text: str = "SYNTHETIC DEMO DATA") -> str:
    return f'<span class="badge badge-synth">⚠ {html.escape(text)}</span>'


def llm_badge(provider: str, model: str) -> str:
    return f'<span class="badge badge-llm">LLM: {html.escape(str(provider))} · {html.escape(str(model))}</span>'


def stage_strip(stages: list[str] = PIPELINE_STAGES) -> None:
    parts = []
    for i, s in enumerate(stages):
        if i:
            parts.append('<span class="stage-arrow">→</span>')
        parts.append(f'<span class="stage">{html.escape(s)}</span>')
    st.markdown(f'<div class="stage-strip">{"".join(parts)}</div>', unsafe_allow_html=True)


def pct(v: Any) -> str:
    try:
        return f"{float(v) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def num(v: Any, fmt: str = "{:,.0f}") -> str:
    try:
        return fmt.format(float(v))
    except (TypeError, ValueError):
        return "—"


def safe_call(fn: Callable, *args, default: Any = None, label: str | None = None, **kwargs) -> Any:
    """Call a service function; show st.error with the exception message on failure."""
    try:
        return fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 - surface any backend error to the user
        name = label or getattr(fn, "__name__", "service call")
        st.error(f"{name} failed: {type(e).__name__}: {e}")
        return default


def services():
    """Import service modules lazily; show a friendly error if the backend isn't ready."""
    try:
        from app.services import analytics, audit, diagnostics, workflow  # noqa: F401
        return workflow, analytics, audit, diagnostics
    except Exception as e:  # noqa: BLE001
        st.error(f"Backend services are not available yet: {type(e).__name__}: {e}")
        st.stop()


@st.cache_data(ttl=30, show_spinner=False)
def _config_snapshot_cached() -> dict:
    from app.services import diagnostics
    return diagnostics.get_config_snapshot() or {}


def config_snapshot() -> dict:
    try:
        return _config_snapshot_cached()
    except Exception:  # noqa: BLE001
        try:
            from app.config import public_snapshot
            return public_snapshot()
        except Exception:  # noqa: BLE001
            return {}


def provider_model() -> tuple[str, str]:
    cfg = config_snapshot()
    llm = cfg.get("llm", {}) if isinstance(cfg, dict) else {}
    return str(llm.get("provider", cfg.get("provider", "?"))), str(llm.get("model", cfg.get("model", "?")))


def header(title: str, subtitle: str | None = None) -> None:
    """Page header with synthetic + LLM badges."""
    provider, model = provider_model()
    st.title(title)
    st.markdown(synthetic_badge() + llm_badge(provider, model), unsafe_allow_html=True)
    if subtitle:
        st.caption(subtitle)


def df(rows: list[dict] | None, columns: list[str] | None = None) -> pd.DataFrame:
    frame = pd.DataFrame(rows or [])
    if columns and not frame.empty:
        frame = frame[[c for c in columns if c in frame.columns]]
    return frame


def stringify_complex(frame: pd.DataFrame) -> pd.DataFrame:
    """Make dict/list cells display-friendly (Arrow can't always render mixed objects)."""
    out = frame.copy()
    for c in out.columns:
        if out[c].map(lambda x: isinstance(x, (dict, list))).any():
            out[c] = out[c].map(lambda x: _fmt_cell(x))
    return out


def _fmt_cell(x: Any) -> str:
    if isinstance(x, dict):
        return "; ".join(f"{k}={_fmt_cell(v)}" for k, v in x.items())
    if isinstance(x, list):
        return ", ".join(_fmt_cell(v) for v in x)
    return "" if x is None else str(x)


def empty(msg: str) -> None:
    st.info(msg, icon="ℹ️")


def invalidate() -> None:
    """Clear cached reads after a mutation."""
    st.cache_data.clear()


def flash(kind: str, msg: str) -> None:
    """Queue a message that survives st.rerun()."""
    st.session_state.setdefault("_flash", []).append((kind, msg))


def show_flash() -> None:
    for kind, msg in st.session_state.pop("_flash", []):
        getattr(st, kind, st.info)(msg)
