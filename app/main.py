"""Streamlit entry point: Marketplace Catalog Ingestion Engine.

Run:  .venv\\Scripts\\python -m streamlit run app/main.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

st.set_page_config(
    page_title="Catalog Ingestion Engine",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded",
)

from app.ui import common  # noqa: E402

common.apply_style()


@st.cache_resource(show_spinner="Initialising database…")
def _init() -> bool:
    from app.services import workflow
    workflow.init_app()
    return True


try:
    _init()
except Exception as e:  # noqa: BLE001
    st.error(f"App initialisation failed: {type(e).__name__}: {e}")
    st.stop()

PAGES_DIR = Path(__file__).parent / "pages"
pages = {
    "Overview": [
        st.Page(PAGES_DIR / "home.py", title="Home", icon="🏠", default=True),
    ],
    "Operations": [
        st.Page(PAGES_DIR / "ingest.py", title="Ingest", icon="📥"),
        st.Page(PAGES_DIR / "review.py", title="Review Queue", icon="🧑‍⚖️"),
        st.Page(PAGES_DIR / "catalog.py", title="Live Catalog", icon="🛍️"),
    ],
    "Insights & Governance": [
        st.Page(PAGES_DIR / "analytics.py", title="Analytics", icon="📊"),
        st.Page(PAGES_DIR / "diagnostics.py", title="Diagnostics", icon="🛠️"),
    ],
}

with st.sidebar:
    st.markdown("### 🛒 Catalog Ingestion Engine")
    st.markdown(common.synthetic_badge(), unsafe_allow_html=True)
    provider, model = common.provider_model()
    st.markdown(common.llm_badge(provider, model), unsafe_allow_html=True)
    st.divider()

nav = st.navigation(pages)
nav.run()
