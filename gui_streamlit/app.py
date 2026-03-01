"""
gui_streamlit/app.py — Main Streamlit entry point.

Horizontal tab navigation. No sidebar page list.
Run with: python run_app.py
"""

import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st

from gui_streamlit.shared import load_config, get_db_manager

# ─── Page Configuration ──────────────────────────────────────────────────────

st.set_page_config(
    page_title="IF Panel Designer",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="collapsed",
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
        "scrape_targets": "",
        "scrape_antibody_type": "primary",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


init_session_state()


# ─── Navigation ──────────────────────────────────────────────────────────────

PAGES = {
    "Dashboard": "📊",
    "Inventory": "📦",
    "Catalog": "📚",
    "Panel Builder": "🧪",
    "Scraper": "🌐",
    "Wishlist": "🛒",
    "Protocol": "📄",
    "Settings": "⚙️",
}

# Redirect legacy Price Search nav to Catalog
if st.session_state.nav_page == "Price Search":
    st.session_state.nav_page = "Catalog"

# Top navigation bar
cols = st.columns(len(PAGES))
for col, (page_name, icon) in zip(cols, PAGES.items()):
    with col:
        is_active = st.session_state.nav_page == page_name
        if st.button(
            f"{icon} {page_name}",
            use_container_width=True,
            type="primary" if is_active else "secondary",
            key=f"nav_{page_name}",
        ):
            st.session_state.nav_page = page_name
            st.rerun()

st.divider()


# ─── Page Router ─────────────────────────────────────────────────────────────

page = st.session_state.nav_page

if page == "Dashboard":
    from gui_streamlit.pages.page_dashboard import render
    render()
elif page == "Inventory":
    from gui_streamlit.pages.page_inventory import render
    render()
elif page == "Catalog":
    from gui_streamlit.pages.page_catalog import render
    render()
elif page == "Panel Builder":
    from gui_streamlit.pages.page_panel_builder import render
    render()
elif page == "Scraper":
    from gui_streamlit.pages.page_scraper import render
    render()
elif page == "Wishlist":
    from gui_streamlit.pages.page_wishlist import render
    render()
elif page == "Protocol":
    from gui_streamlit.pages.page_protocol import render
    render()
elif page == "Settings":
    from gui_streamlit.pages.page_settings import render
    render()
