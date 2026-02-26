"""
gui_streamlit/pages/page_dashboard.py — Home Dashboard.

At-a-glance overview of lab state, recent panels, alerts, and quick-start buttons.
"""

import streamlit as st
from datetime import date


def render():
    from gui_streamlit.shared import get_db_manager, load_config
    from core.inventory import get_inventory_summary, get_alerts, get_recent_panels

    config = load_config()
    db = get_db_manager()

    st.title("📊 Dashboard")
    st.caption(f"{config.get('lab', {}).get('name', 'My Lab')} — {date.today().strftime('%B %d, %Y')}")

    # ─── Quick Start Buttons ─────────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("🧪 New Panel", use_container_width=True, type="primary"):
            st.session_state.nav_page = "Panel Builder"
            st.session_state.panel_step = 1
            st.session_state.panel_current = None
            st.rerun()
    with col2:
        if st.button("📦 Add Antibody", use_container_width=True):
            st.session_state.nav_page = "Inventory"
            st.rerun()
    with col3:
        if st.button("💰 Search Prices", use_container_width=True):
            st.session_state.nav_page = "Price Search"
            st.rerun()

    st.divider()

    # ─── Inventory Summary ───────────────────────────────────────────
    with db.inventory_session() as session:
        summary = get_inventory_summary(session)
        alerts = get_alerts(session)

    st.subheader("Inventory Summary")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Antibodies", summary["total_antibodies"])
    m2.metric("Unique Targets", len(summary["targets"]))
    m3.metric("Vendors", len(summary["vendors"]))
    m4.metric("Total Alerts", alerts["total_alerts"])

    # ─── Alerts ──────────────────────────────────────────────────────
    if alerts["total_alerts"] > 0:
        st.subheader("Alerts")

        if alerts["expired"]:
            st.error(f"🚨 **{len(alerts['expired'])} Expired Antibodies**")
            for item in alerts["expired"]:
                st.caption(f"  • {item.antibody_name or item.target} — expired {item.expiry_date}")

        if alerts["expiring_soon"]:
            st.warning(f"⏰ **{len(alerts['expiring_soon'])} Expiring Soon** (within 90 days)")
            for item in alerts["expiring_soon"]:
                st.caption(f"  • {item.antibody_name or item.target} — expires {item.expiry_date}")

        if alerts["low_stock"]:
            st.warning(f"📉 **{len(alerts['low_stock'])} Low Stock**")
            for item in alerts["low_stock"]:
                st.caption(f"  • {item.antibody_name or item.target} — {item.total_volume_ul:.0f} µL remaining")
    else:
        st.success("✅ No alerts — inventory is in good shape!")

    # ─── Recent Panels ───────────────────────────────────────────────
    st.divider()
    st.subheader("Recent Panels")

    with db.inventory_session() as session:
        recent = get_recent_panels(session, n=5)

    if recent:
        for panel in recent:
            with st.expander(f"🧪 {panel.name} — {panel.experiment_type.value}"):
                st.write(f"**Type:** {panel.experiment_type.value}")
                st.write(f"**Species:** {panel.sample_species.value}")
                st.write(f"**Targets:** {', '.join(t.target_name for t in panel.targets)}")
                st.write(f"**Created:** {panel.created_at.strftime('%Y-%m-%d')}")
                if st.button("Open in Panel Builder", key=f"open_{panel.id}"):
                    st.session_state.panel_current = panel
                    st.session_state.panel_step = 2
                    st.session_state.nav_page = "Panel Builder"
                    st.rerun()
    else:
        st.info("No saved panels yet. Click 'New Panel' to get started!")

    # ─── Active Profile ──────────────────────────────────────────────
    st.divider()
    st.subheader("Instrument Profile")
    st.info(f"🔧 Active profile: **{st.session_state.active_profile}**")
    st.caption("Change in Settings → Instrument Profiles")
