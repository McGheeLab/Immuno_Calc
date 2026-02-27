"""
gui_streamlit/pages/page_scraper.py — Biocompare Scraper Controls.

Full GUI for running the Biocompare scraper with all options:
target list, antibody type, vendor filters, deep/headless modes.
"""

import streamlit as st


def render():
    from core.biocompare_scraper import (
        run_scrape, load_vendors_config, build_search_url, ANTIBODY_TYPE_SOIDS,
    )
    from core.scraper import get_catalog_stats

    st.title("🌐 Biocompare Scraper")

    # ─── Catalog status ─────────────────────────────────────────────
    stats = get_catalog_stats()
    if stats and stats.get("product_count", 0) > 0:
        c1, c2, c3 = st.columns(3)
        c1.metric("Products in catalog", f"{stats['product_count']:,}")
        c2.metric("Antigens", f"{stats['antigen_count']:,}")
        last = stats.get("last_scrape")
        c3.metric("Last scrape", last.get("started_at", "N/A")[:10] if last else "Never")
    else:
        st.info("Local catalog is empty. Run a scrape to populate it.")

    st.divider()

    # ─── Scrape by Target List ──────────────────────────────────────
    st.subheader("🎯 Scrape by Target List")
    st.caption(
        "Enter target antigens to search on Biocompare. Chrome will open, "
        "navigate to the filtered search page, type each target, and save results."
    )

    # Pre-fill from session state (e.g., redirected from Price Search)
    default_targets = st.session_state.get("scrape_targets", "")

    targets_str = st.text_area(
        "Targets (one per line or comma-separated)",
        value=default_targets,
        height=120,
        placeholder="CD3\nKi67\nGFAP\nVimentin",
        key="scraper_targets_input",
    )

    # ─── Antibody Type ──────────────────────────────────────────────
    st.subheader("🔬 Antibody Type")
    st.caption("Select the antibody type BEFORE scraping. This filters Biocompare results.")

    vendors_config = load_vendors_config()
    type_config = vendors_config.get("antibody_types", {})

    type_options = {
        "primary": "Primary Antibodies",
        "secondary": "Secondary Antibodies",
        "pairs": "Antibody Pairs",
    }

    default_type = st.session_state.get("scrape_antibody_type", "primary")
    default_idx = list(type_options.keys()).index(default_type) if default_type in type_options else 0

    antibody_type = st.radio(
        "Type",
        options=list(type_options.keys()),
        format_func=lambda x: type_options[x],
        index=default_idx,
        horizontal=True,
        key="scraper_ab_type",
    )
    st.session_state.scrape_antibody_type = antibody_type

    # ─── Vendor Filter ──────────────────────────────────────────────
    st.subheader("🏢 Vendor Filter")
    st.caption("Only show results from these vendors. Uncheck all to search all vendors.")

    all_vendors = vendors_config.get("vendors", [])
    enabled_vendors = [v for v in all_vendors if v.get("enabled", False)]
    disabled_vendors = [v for v in all_vendors if not v.get("enabled", False)]

    # Show enabled vendors as pre-checked
    selected_vids = []
    if enabled_vendors:
        st.caption("**Approved vendors:**")
        vcols = st.columns(min(4, len(enabled_vendors)))
        for i, v in enumerate(enabled_vendors):
            with vcols[i % len(vcols)]:
                checked = st.checkbox(v["name"], value=True, key=f"vendor_{v['vid']}")
                if checked:
                    selected_vids.append(v["vid"])

    if disabled_vendors:
        with st.expander("Show more vendors"):
            vcols = st.columns(min(4, len(disabled_vendors)))
            for i, v in enumerate(disabled_vendors):
                with vcols[i % len(vcols)]:
                    checked = st.checkbox(v["name"], value=False, key=f"vendor_{v['vid']}")
                    if checked:
                        selected_vids.append(v["vid"])

    # Preview URL
    preview_url = build_search_url(antibody_type, selected_vids if selected_vids else None)
    st.caption(f"🔗 Search URL: `{preview_url}`")

    # ─── Scraper Options ────────────────────────────────────────────
    st.subheader("⚙️ Options")
    oc1, oc2, oc3, oc4 = st.columns(4)
    with oc1:
        headless = st.checkbox("Headless", value=False, key="scraper_headless",
                               help="Hide Chrome window")
    with oc2:
        deep = st.checkbox("Deep scrape", value=False, key="scraper_deep",
                           help="Also fetch each product's detail page for full specs")
    with oc3:
        interactive = st.checkbox("Interactive", value=False, key="scraper_interactive",
                                  help="Pause at each page for manual inspection")
    with oc4:
        max_pages = st.number_input("Max pages/target", value=3, min_value=1, max_value=20,
                                    key="scraper_maxpages")

    # Check if user wants to update existing entries
    update_existing = st.checkbox(
        "Update existing entries",
        value=True,
        key="scraper_update",
        help="If a product already exists in the catalog, update it with fresh data. "
             "If unchecked, existing entries are skipped.",
    )
    # Note: upsert in the DB always updates on conflict, so this is the default behavior.
    # If we wanted skip-only, we'd need to change DB logic. For now this is informational.

    # ─── Run Button ─────────────────────────────────────────────────
    st.divider()

    if st.button("🚀 Start Scraping", type="primary", use_container_width=True, key="scraper_run"):
        # Parse targets
        target_list = []
        for line in targets_str.replace(",", "\n").split("\n"):
            t = line.strip()
            if t:
                target_list.append(t)

        if not target_list:
            st.error("Enter at least one target.")
            return

        # Check selenium
        try:
            import selenium  # noqa: F401
        except ImportError:
            st.error("Selenium not installed. Run: `pip install selenium webdriver-manager`")
            return

        progress = st.empty()
        progress.info(
            f"Opening Chrome...\n"
            f"Targets: {', '.join(target_list[:10])}\n"
            f"Type: {type_options[antibody_type]}\n"
            f"Vendors: {len(selected_vids)} selected"
        )

        result = run_scrape(
            mode="targets",
            targets=target_list,
            antibody_type=antibody_type,
            vendor_vids=selected_vids if selected_vids else None,
            headless=headless,
            interactive=interactive,
            deep=deep,
            max_pages=int(max_pages),
        )

        progress.empty()

        if result["status"] == "ok":
            total = result.get("products", 0)
            st.success(f"✅ Done! Found {total} products across {result.get('antigens', 0)} targets.")

            per_target = result.get("per_target", {})
            if per_target:
                for target, info in per_target.items():
                    icon = "✅" if info["status"] == "ok" and info["products"] > 0 else "⚠️"
                    st.caption(f"  {icon} **{target}**: {info['products']} products ({info['status']})")

            st.info("Browse results on the **Catalog** page.")
        else:
            st.error(f"Scrape error: {result.get('message', 'unknown')}")

    # ─── Other Scrape Modes ─────────────────────────────────────────
    st.divider()
    with st.expander("📖 Browse Scrape (by letter)"):
        st.caption("Scrape all antigens starting with a specific letter from Biocompare's browse pages.")
        bc1, bc2, bc3 = st.columns(3)
        with bc1:
            letter = st.selectbox("Letter", list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"), key="scraper_letter")
        with bc2:
            letter_deep = st.checkbox("Deep", value=False, key="letter_deep")
        with bc3:
            letter_max = st.number_input("Max pages", value=5, min_value=1, key="letter_max")

        if st.button("Scrape Letter", key="scraper_letter_run"):
            result = run_scrape(
                mode="letter", letter=letter, headless=headless,
                deep=letter_deep, max_pages=int(letter_max),
            )
            if result["status"] == "ok":
                st.success(f"✅ {result.get('antigens', 0)} antigens, {result.get('products', 0)} products")
            else:
                st.error(f"Error: {result.get('message')}")

    with st.expander("🧪 Test Scrape"):
        st.caption("Quick test with 3 antigens to verify Selenium and parsers work.")
        if st.button("Run Test", key="scraper_test"):
            result = run_scrape(mode="test", headless=headless, max_antigens=3)
            if result["status"] == "ok":
                st.success(f"✅ Test passed: {result.get('products', 0)} products")
            else:
                st.error(f"Error: {result.get('message')}")

    with st.expander("📊 Catalog Stats"):
        if st.button("Refresh Stats", key="scraper_stats"):
            result = run_scrape(mode="stats")
            if result["status"] == "ok":
                st.json(result)

    with st.expander("📤 Export Catalog to CSV"):
        if st.button("Export", key="scraper_export"):
            result = run_scrape(mode="export_csv")
            if result["status"] == "ok":
                st.success(f"Exported to: {result.get('path')}")
