"""
gui_streamlit/shared.py — Shared utilities for all Streamlit pages.

This module holds cached resources (DB manager, config, profiles) that both
app.py and individual pages need. Extracting them here prevents circular imports
(app.py imports pages, pages would otherwise import app.py).
"""

import os
import sys

import streamlit as st
import yaml

# ─── Path Setup ──────────────────────────────────────────────────────────────
# Ensure the project root is on sys.path so `core/` imports work regardless
# of which directory the user runs `streamlit run` from.

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(_THIS_DIR)

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ─── Cached Resources ───────────────────────────────────────────────────────

@st.cache_resource
def load_config() -> dict:
    """Load config.yaml from project root."""
    config_path = os.path.join(PROJECT_ROOT, "config.yaml")
    if os.path.exists(config_path):
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    return {"lab": {"name": "My Lab"}, "defaults": {}, "scraping": {}, "inventory": {}}


@st.cache_resource
def load_instrument_profiles() -> dict:
    """Load instrument profiles from data/instrument_profiles.yaml."""
    profiles_path = os.path.join(PROJECT_ROOT, "data", "instrument_profiles.yaml")
    if os.path.exists(profiles_path):
        with open(profiles_path) as f:
            data = yaml.safe_load(f) or {}
        return data.get("profiles", {})
    return {}


@st.cache_resource
def get_db_manager():
    """Get or create the DatabaseManager singleton (cached per Streamlit server)."""
    from core.database import DatabaseManager

    db_dir = os.path.join(PROJECT_ROOT, "db")
    db = DatabaseManager(db_dir)
    db.create_all()
    return db
