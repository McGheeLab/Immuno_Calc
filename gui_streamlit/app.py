"""
gui_streamlit/app.py — Main Streamlit entry point.

Sidebar navigation, session state initialization, and page routing.
Run with: streamlit run gui_streamlit/app.py
"""

import streamlit as st

# shared.py handles sys.path setup and provides cached resources
from gui_streamlit.shared import (
    load_config,
    load_instrument_profiles,
    get_db_manager,
    PROJECT_ROOT,
)


# ─── Page Configuration ──────────────────────────────────────────────────────

st.set_page_config(
    page_title="IF Panel Designer",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ─── Session State Initialization ────────────────────────────────────────────

def init_session_state():
    config = load_config()
    defaults = {
        "active_profile": config.get("defaults", {}).get("active_profile", "Default Lab Microscope"),
        "panel_current": None,
        "panel_step": 1,
        "validation_results": [],
        "alt_plans": [],
        "cost_estimate": None,
        "inv_search_query": "",
        "inv_selected_item_id": None,
        "price_search_query": "",
        "price_results": [],
        "proto_current": None,
        "nav_page": "Dashboard",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


init_session_state()


# ─── Sidebar Navigation ─────────────────────────────────────────────────────

def render_sidebar():
    config = load_config()

    st.sidebar.markdown("# 🔬 IF Panel Designer")
    st.sidebar.caption(f"Lab: {config.get('lab', {}).get('name', 'My Lab')}")
    st.sidebar.divider()

    pages = {
        "Dashboard": "📊",
        "Inventory": "📦",
        "Panel Builder": "🧪",
        "Price Search": "💰",
        "Protocol": "📄",
        "Settings": "⚙️",
    }

    for page_name, icon in pages.items():
        if st.sidebar.button(
            f"{icon} {page_name}",
            use_container_width=True,
            type="primary" if st.session_state.nav_page == page_name else "secondary",
        ):
            st.session_state.nav_page = page_name
            st.rerun()

    st.sidebar.divider()

    # Active instrument profile indicator
    st.sidebar.caption(f"🔧 Profile: {st.session_state.active_profile}")

    # Quick stats
    db = get_db_manager()
    with db.inventory_session() as session:
        from core.inventory import get_inventory_summary
        summary = get_inventory_summary(session)
        st.sidebar.metric("Antibodies in Stock", summary["total_antibodies"])
        if summary["expired_count"] > 0:
            st.sidebar.error(f"⚠️ {summary['expired_count']} expired")
        if summary["low_stock_count"] > 0:
            st.sidebar.warning(f"📉 {summary['low_stock_count']} low stock")


render_sidebar()


# ─── Page Router ─────────────────────────────────────────────────────────────

def render_page():
    page = st.session_state.nav_page

    if page == "Dashboard":
        from gui_streamlit.pages.page_dashboard import render
        render()
    elif page == "Inventory":
        from gui_streamlit.pages.page_inventory import render
        render()
    elif page == "Panel Builder":
        from gui_streamlit.pages.page_panel_builder import render
        render()
    elif page == "Price Search":
        from gui_streamlit.pages.page_price_search import render
        render()
    elif page == "Protocol":
        from gui_streamlit.pages.page_protocol import render
        render()
    elif page == "Settings":
        from gui_streamlit.pages.page_settings import render
        render()


render_page()
