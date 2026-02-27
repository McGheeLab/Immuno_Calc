"""
gui_streamlit/pages/page_price_search.py — Antibody Price Search.

Searches the local Biocompare catalog (populated by monthly batch scrape).
Falls back to manual entry if catalog is empty.
"""

import streamlit as st
import pandas as pd
from datetime import datetime


def render():
    from gui_streamlit.shared import get_db_manager, load_config
    from core.scraper import search as scraper_search, create_manual_price_result, get_catalog_stats
    from core.models import PriceResult

    config = load_config()

    st.title("💰 Antibody Price Search")

    # ─── Catalog Status Banner ────────────────────────────────────────
    stats = get_catalog_stats()
    if stats and stats.get("product_count", 0) > 0:
        last = stats.get("last_scrape")
        last_date = last.get("started_at", "Unknown")[:10] if last else "Unknown"
        st.caption(
            f"📚 Local catalog: **{stats['product_count']:,}** products, "
            f"**{stats['antigen_count']:,}** antigens, "
            f"**{stats['vendor_count']:,}** vendors  "
            f"(last updated: {last_date})"
        )
    else:
        st.warning(
            "⚠️ No local Biocompare catalog found. Run the monthly scraper to populate it:\n\n"
            "```\npython scripts/run_monthly_scrape.py --test\n```\n\n"
            "In the meantime, you can use manual entry below."
        )

    # ─── Search Bar ──────────────────────────────────────────────────
    col1, col2 = st.columns([4, 1])
    with col1:
        query = st.text_input(
            "Search antibodies",
            value=st.session_state.price_search_query,
            placeholder="e.g., CD3, RAB5, Ki67, GFAP...",
        )
        st.session_state.price_search_query = query
    with col2:
        st.write("")
        search_btn = st.button("🔍 Search", type="primary", use_container_width=True)

    # ─── Filters ─────────────────────────────────────────────────────
    with st.expander("🔽 Filters"):
        fc1, fc2, fc3, fc4 = st.columns(4)
        with fc1:
            host_filter = st.selectbox("Host Species", ["Any", "Rabbit", "Mouse", "Goat", "Rat", "Human"])
        with fc2:
            app_filter = st.selectbox("Application", ["Any", "IF", "IHC", "ICC", "FC", "WB", "ELISA", "IP"])
        with fc3:
            conjugate_filter = st.text_input("Conjugate", "", placeholder="e.g., AF647, FITC, Unconjugated")
        with fc4:
            reactivity_filter = st.selectbox("Reactivity", ["Any", "Human", "Mouse", "Rat"])

        sort_by = st.radio("Sort by", ["Price (Low → High)", "Price (High → Low)", "Product Name"], horizontal=True)

    # ─── Execute Search ──────────────────────────────────────────────
    if search_btn and query:
        with st.spinner("Searching local catalog..."):
            results = scraper_search(
                query=query,
                host=host_filter if host_filter != "Any" else "",
                application=app_filter if app_filter != "Any" else "",
                conjugate=conjugate_filter,
                reactivity=reactivity_filter if reactivity_filter != "Any" else "",
                max_results=100,
            )

            if results:
                st.session_state.price_results = results
                st.success(f"Found {len(results)} results in local catalog.")
            else:
                st.warning(
                    "No results found. The local catalog may not contain this target. "
                    "Try a different search term or use manual entry below."
                )
                st.session_state.price_results = []

    # ─── Results Table ───────────────────────────────────────────────
    results = st.session_state.get("price_results", [])

    if results:
        st.subheader(f"Results ({len(results)})")

        # Apply post-search filters
        filtered = results
        if host_filter != "Any":
            filtered = [r for r in filtered if host_filter.lower() in r.host_species.lower()]
        if app_filter != "Any":
            filtered = [r for r in filtered
                        if any(app_filter.lower() in a.lower() for a in r.validated_applications)
                        or app_filter.upper() in (r.conjugate or "").upper()]
        if conjugate_filter:
            filtered = [r for r in filtered if conjugate_filter.lower() in (r.conjugate or "").lower()]

        # Sort
        if sort_by == "Price (Low → High)":
            filtered.sort(key=lambda r: r.price if r.price > 0 else 99999)
        elif sort_by == "Price (High → Low)":
            filtered.sort(key=lambda r: r.price if r.price > 0 else 0, reverse=True)
        elif sort_by == "Product Name":
            filtered.sort(key=lambda r: r.product_name.lower())

        if filtered:
            df = pd.DataFrame([
                {
                    "Product": r.product_name[:70] if r.product_name else f"Anti-{r.target}",
                    "Target": r.target,
                    "Vendor": r.vendor,
                    "Catalog #": r.catalog_no,
                    "Price": f"${r.price:.2f}" if r.price > 0 else "Inquire",
                    "Size": r.package_size,
                    "Host": r.host_species,
                    "Conjugate": r.conjugate or "",
                    "Apps": ", ".join(r.validated_applications[:4]) if r.validated_applications else "",
                    "Reactivity": ", ".join(r.reactivity[:3]) if r.reactivity else "",
                }
                for r in filtered
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)

            # Action buttons per result
            for i, r in enumerate(filtered[:20]):  # Limit buttons to first 20
                cols = st.columns([3, 1, 1])
                with cols[0]:
                    st.caption(f"{r.vendor} — {r.catalog_no or 'N/A'} — {r.product_name[:50]}")
                with cols[1]:
                    if st.button("📦 Add to Inventory", key=f"inv_{i}"):
                        st.session_state.nav_page = "Inventory"
                        st.session_state["prefill_antibody"] = {
                            "name": r.product_name,
                            "target": r.target,
                            "vendor": r.vendor,
                            "catalog_no": r.catalog_no,
                            "host_species": r.host_species,
                            "conjugate": r.conjugate,
                            "isotype": r.isotype,
                            "clonality": r.clonality,
                        }
                        st.rerun()
                with cols[2]:
                    if r.url:
                        st.link_button("🔗 View", r.url)
        else:
            st.info("No results match your filters. Try broadening the filter criteria.")

    # ─── Manual Entry Fallback ───────────────────────────────────────
    st.divider()
    with st.expander("✏️ Manual Price Entry"):
        st.caption("Enter antibody pricing details manually.")
        with st.form("manual_price"):
            mc1, mc2 = st.columns(2)
            with mc1:
                m_target = st.text_input("Target", value=query.split()[0] if query else "")
                m_vendor = st.text_input("Vendor")
                m_catalog = st.text_input("Catalog #")
                m_host = st.text_input("Host Species")
            with mc2:
                m_price = st.number_input("Price ($)", min_value=0.0, step=10.0)
                m_size = st.text_input("Package Size", placeholder="e.g., 100 µg")
                m_url = st.text_input("Product URL")
                m_conjugate = st.text_input("Conjugate", placeholder="e.g., Unconjugated, AF647")

            if st.form_submit_button("Add Manual Entry"):
                manual = create_manual_price_result(
                    target=m_target, vendor=m_vendor, catalog_no=m_catalog,
                    price=m_price, package_size=m_size, url=m_url,
                    host_species=m_host, conjugate=m_conjugate,
                )
                current_results = st.session_state.get("price_results", [])
                current_results.append(manual)
                st.session_state.price_results = current_results
                st.success("Manual entry added.")
                st.rerun()
