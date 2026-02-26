"""
gui_streamlit/pages/page_settings.py — Settings & Configuration.

Instrument profile CRUD, lab identity, scraping preferences,
cache management, and fluorophore database viewer.
"""

import streamlit as st
import yaml
import os


def render():
    from gui_streamlit.shared import get_db_manager, load_config, load_instrument_profiles
    from core.fluorophores import load_fluorophores, reload_fluorophores
    from core.price_cache import clear_cache, get_cache_stats, purge_stale_cache

    db = get_db_manager()
    config = load_config()
    profiles = load_instrument_profiles()

    st.title("⚙️ Settings")

    tab1, tab2, tab3, tab4, tab5 = st.tabs([
        "🏷️ Lab Identity",
        "🔬 Instrument Profiles",
        "🌐 Scraping",
        "💾 Cache",
        "🌈 Fluorophores",
    ])

    # ─── Tab 1: Lab Identity ─────────────────────────────────────────
    with tab1:
        st.subheader("Lab Identity")
        st.caption("These values appear in protocol headers and exports.")

        with st.form("lab_settings"):
            lab = config.get("lab", {})
            lab_name = st.text_input("Lab Name", value=lab.get("name", ""))
            pi_name = st.text_input("PI Name", value=lab.get("pi_name", ""))
            institution = st.text_input("Institution", value=lab.get("institution", ""))
            researcher = st.text_input("Default Researcher", value=lab.get("default_researcher", ""))

            if st.form_submit_button("💾 Save Lab Settings"):
                config["lab"] = {
                    "name": lab_name,
                    "pi_name": pi_name,
                    "institution": institution,
                    "default_researcher": researcher,
                }
                _save_config(config)
                st.success("Lab settings saved!")

    # ─── Tab 2: Instrument Profiles ──────────────────────────────────
    with tab2:
        st.subheader("Instrument Profiles")

        # Active profile selector
        profile_names = list(profiles.keys())
        if profile_names:
            active_idx = profile_names.index(st.session_state.active_profile) if st.session_state.active_profile in profile_names else 0
            selected = st.selectbox("Active Profile", profile_names, index=active_idx)
            if selected != st.session_state.active_profile:
                st.session_state.active_profile = selected
                st.success(f"Active profile set to: {selected}")

            # Show profile details
            prof = profiles[selected]
            st.write(f"**Mode:** {prof.get('mode', 'conventional')}")
            st.write("**Channels:**")

            import pandas as pd
            channels_df = pd.DataFrame([
                {
                    "Name": ch["name"],
                    "Laser (nm)": ch["laser_nm"],
                    "Filter": f"{ch['filter_center']}/{ch['filter_bandwidth']}",
                    "Common Fluorophores": ", ".join(ch.get("common_fluorophores", [])),
                }
                for ch in prof.get("channels", [])
            ])
            st.dataframe(channels_df, use_container_width=True, hide_index=True)

        # Add new profile
        with st.expander("➕ Add New Instrument Profile"):
            with st.form("new_profile"):
                new_name = st.text_input("Profile Name")
                new_mode = st.selectbox("Mode", ["conventional", "spectral"])

                st.caption("Add channels (one per line): name, laser_nm, filter_center, filter_bandwidth")
                channels_text = st.text_area(
                    "Channels (CSV format)",
                    placeholder="DAPI,405,450,50\nFITC,488,530,30\nCy5,640,670,30",
                    height=150,
                )

                if st.form_submit_button("Add Profile"):
                    if new_name and channels_text:
                        channels = []
                        for line in channels_text.strip().split("\n"):
                            parts = [p.strip() for p in line.split(",")]
                            if len(parts) >= 4:
                                channels.append({
                                    "name": parts[0],
                                    "laser_nm": float(parts[1]),
                                    "filter_center": float(parts[2]),
                                    "filter_bandwidth": float(parts[3]),
                                    "common_fluorophores": [],
                                })
                        if channels:
                            profiles[new_name] = {
                                "mode": new_mode,
                                "channels": channels,
                            }
                            _save_profiles(profiles)
                            st.success(f"Profile '{new_name}' added!")
                            st.rerun()
                    else:
                        st.error("Name and channels required.")

    # ─── Tab 3: Scraping Preferences ─────────────────────────────────
    with tab3:
        st.subheader("Web Scraping Preferences")

        scraping = config.get("scraping", {})

        with st.form("scraping_settings"):
            enabled = st.checkbox("Enable web scraping", value=scraping.get("enabled", True))
            cache_ttl = st.number_input("Cache TTL (hours)", value=scraping.get("cache_ttl_hours", 24), min_value=1)
            max_results = st.number_input("Max results per vendor", value=scraping.get("max_results_per_vendor", 10), min_value=1)
            timeout = st.number_input("Timeout (seconds)", value=scraping.get("timeout_seconds", 30), min_value=5)

            st.caption("Vendor Priority (checked in order):")
            default_vendors = [
                "Biocompare", "Abcam", "Cell Signaling Technology",
                "BioLegend", "Thermo Fisher", "BD Biosciences",
                "R&D Systems", "Sigma-Aldrich",
            ]
            vendor_priority = st.multiselect(
                "Enabled Vendors",
                default_vendors,
                default=scraping.get("vendor_priority", default_vendors),
            )

            if st.form_submit_button("💾 Save Scraping Settings"):
                config["scraping"] = {
                    "enabled": enabled,
                    "cache_ttl_hours": cache_ttl,
                    "max_results_per_vendor": max_results,
                    "timeout_seconds": timeout,
                    "vendor_priority": vendor_priority,
                }
                _save_config(config)
                st.success("Scraping settings saved!")

    # ─── Tab 4: Cache Management ─────────────────────────────────────
    with tab4:
        st.subheader("Price Cache Management")

        with db.cache_session() as session:
            stats = get_cache_stats(session)

        st.metric("Cached Entries", stats["total_entries"])
        if stats["oldest_entry"]:
            st.caption(f"Oldest: {stats['oldest_entry']}")
        if stats["newest_entry"]:
            st.caption(f"Newest: {stats['newest_entry']}")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("🧹 Purge Stale Entries"):
                ttl = config.get("scraping", {}).get("cache_ttl_hours", 24)
                with db.cache_session() as session:
                    count = purge_stale_cache(session, ttl)
                st.success(f"Purged {count} stale entries.")
                st.rerun()
        with col2:
            if st.button("🗑️ Clear All Cache"):
                with db.cache_session() as session:
                    count = clear_cache(session)
                st.success(f"Cleared {count} entries.")
                st.rerun()

    # ─── Tab 5: Fluorophore Database ─────────────────────────────────
    with tab5:
        st.subheader("Fluorophore Database")

        if st.button("🔄 Reload from YAML"):
            reload_fluorophores()
            st.success("Fluorophore database reloaded.")

        fluor_lib = load_fluorophores()
        st.caption(f"{len(fluor_lib)} fluorophores loaded")

        import pandas as pd
        fluor_df = pd.DataFrame([
            {
                "Name": f.name,
                "Ex (nm)": f.ex_peak,
                "Em (nm)": f.em_peak,
                "Brightness": f.brightness,
                "Lasers": ", ".join(str(int(l)) for l in f.laser_lines),
                "Tandem": "✅" if f.is_tandem else "",
                "Polymer": "✅" if f.is_polymer else "",
                "Stokes Shift": f"{f.stokes_shift:.0f} nm",
            }
            for f in sorted(fluor_lib.values(), key=lambda x: x.ex_peak)
        ])
        st.dataframe(fluor_df, use_container_width=True, hide_index=True)


def _save_config(config: dict):
    """Save config back to config.yaml."""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    config_path = os.path.join(project_root, "config.yaml")
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)


def _save_profiles(profiles: dict):
    """Save instrument profiles back to YAML."""
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    profiles_path = os.path.join(project_root, "data", "instrument_profiles.yaml")
    with open(profiles_path, "w") as f:
        yaml.dump({"profiles": profiles}, f, default_flow_style=False)
