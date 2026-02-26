"""
gui_streamlit/pages/page_price_search.py — Antibody Price Search.

Standalone pricing search and comparison screen.
Can be used independently or linked from the Panel Builder.
"""

import streamlit as st
import pandas as pd
from datetime import datetime


def render():
    from gui_streamlit.shared import get_db_manager, load_config
    from core.scraper import search as scraper_search, create_manual_price_result
    from core.price_cache import get_cached, store_results
    from core.models import PriceResult

    db = get_db_manager()
    config = load_config()

    st.title("💰 Antibody Price Search")

    # ─── Search Bar ──────────────────────────────────────────────────
    col1, col2 = st.columns([4, 1])
    with col1:
        query = st.text_input(
            "Search antibodies",
            value=st.session_state.price_search_query,
            placeholder="e.g., anti-CD3 human IF rabbit",
        )
        st.session_state.price_search_query = query
    with col2:
        st.write("")
        search_btn = st.button("🔍 Search", type="primary", use_container_width=True)

    # ─── Filters ─────────────────────────────────────────────────────
    with st.expander("🔽 Filters"):
        fc1, fc2, fc3 = st.columns(3)
        with fc1:
            host_filter = st.selectbox("Host Species", ["Any", "Rabbit", "Mouse", "Goat", "Rat"])
        with fc2:
            app_filter = st.selectbox("Application", ["Any", "IF", "IHC", "ICC", "FC", "WB"])
        with fc3:
            conjugate_filter = st.text_input("Conjugate", "", placeholder="e.g., AF647, FITC")

        sort_by = st.radio("Sort by", ["Price (Low → High)", "Price/size", "Review Score"], horizontal=True)

    # ─── Execute Search ──────────────────────────────────────────────
    if search_btn and query:
        with st.spinner("Searching... (checking cache first)"):
            # Check cache
            with db.cache_session() as session:
                cached = get_cached(session, query)

            if cached:
                st.session_state.price_results = cached
                st.info(f"📦 Showing {len(cached)} cached results. Click 'Refresh' for live data.")
            else:
                # Try live scraping
                try:
                    vendors = config.get("scraping", {}).get("vendor_priority", [
                        "Biocompare", "Abcam", "Cell Signaling Technology", "BioLegend"
                    ])
                    results = scraper_search(query, vendors=vendors, max_results=10)

                    if results:
                        # Cache results
                        with db.cache_session() as session:
                            store_results(session, query, results)
                        st.session_state.price_results = results
                        st.success(f"Found {len(results)} results.")
                    else:
                        st.warning("No results found. Try different search terms or use manual entry below.")
                        st.session_state.price_results = []

                except Exception as e:
                    st.error(f"Search failed: {e}. Use manual entry below.")
                    st.session_state.price_results = []

    # ─── Results Table ───────────────────────────────────────────────
    results = st.session_state.get("price_results", [])

    if results:
        st.subheader(f"Results ({len(results)})")

        # Apply filters
        filtered = results
        if host_filter != "Any":
            filtered = [r for r in filtered if host_filter.lower() in r.host_species.lower()]
        if app_filter != "Any":
            filtered = [r for r in filtered if any(app_filter.lower() in a.lower() for a in r.validated_applications)]
        if conjugate_filter:
            filtered = [r for r in filtered if conjugate_filter.lower() in r.conjugate.lower()]

        # Sort
        if sort_by == "Price (Low → High)":
            filtered.sort(key=lambda r: r.price if r.price > 0 else 99999)
        elif sort_by == "Review Score":
            filtered.sort(key=lambda r: r.review_score, reverse=True)

        if filtered:
            df = pd.DataFrame([
                {
                    "Product": r.product_name[:60] if r.product_name else f"Anti-{r.target}",
                    "Vendor": r.vendor,
                    "Catalog #": r.catalog_no,
                    "Price": f"${r.price:.2f}" if r.price > 0 else "Contact",
                    "Size": r.package_size,
                    "Host": r.host_species,
                    "Conjugate": r.conjugate,
                    "Apps": ", ".join(r.validated_applications[:3]) if r.validated_applications else "",
                    "Reviews": f"⭐ {r.review_score:.1f} ({r.num_reviews})" if r.review_score else "",
                    "Cache": "📦 Cached" if r.is_cached else "🔴 Live",
                }
                for r in filtered
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)

            # Action buttons per result
            for i, r in enumerate(filtered):
                cols = st.columns([3, 1, 1])
                with cols[0]:
                    st.caption(f"{r.vendor} — {r.catalog_no}")
                with cols[1]:
                    if st.button("📦 Add to Inventory", key=f"inv_{i}"):
                        st.session_state.nav_page = "Inventory"
                        st.session_state["show_add_form"] = True
                        st.rerun()
                with cols[2]:
                    if r.url:
                        st.link_button("🔗 View", r.url)
        else:
            st.info("No results match your filters.")

    # ─── Manual Entry Fallback ───────────────────────────────────────
    st.divider()
    with st.expander("✏️ Manual Price Entry"):
        st.caption("If scraping is unavailable, enter price details manually.")
        with st.form("manual_price"):
            mc1, mc2 = st.columns(2)
            with mc1:
                m_target = st.text_input("Target", value=query.split()[0] if query else "")
                m_vendor = st.text_input("Vendor")
                m_catalog = st.text_input("Catalog #")
            with mc2:
                m_price = st.number_input("Price ($)", min_value=0.0, step=10.0)
                m_size = st.text_input("Package Size", placeholder="e.g., 100 µg")
                m_url = st.text_input("Product URL")

            if st.form_submit_button("Add Manual Entry"):
                manual = create_manual_price_result(
                    target=m_target, vendor=m_vendor, catalog_no=m_catalog,
                    price=m_price, package_size=m_size, url=m_url,
                )
                current_results = st.session_state.get("price_results", [])
                current_results.append(manual)
                st.session_state.price_results = current_results
                st.success("Manual entry added.")
                st.rerun()
