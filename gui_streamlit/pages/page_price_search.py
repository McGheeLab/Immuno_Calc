"""
gui_streamlit/pages/page_price_search.py — Antibody Price Search.

Searches the local Biocompare catalog. Wishlist button on results.
Redirects to Scraper page if no results found.
"""

import streamlit as st
import pandas as pd


def render():
    from gui_streamlit.shared import get_db_manager
    from core.scraper import search as scraper_search, get_catalog_stats
    from core.wishlist import add_to_wishlist

    db = get_db_manager()

    st.title("💰 Antibody Price Search")

    # ─── Catalog Status ─────────────────────────────────────────────
    stats = get_catalog_stats()
    if stats and stats.get("product_count", 0) > 0:
        last = stats.get("last_scrape")
        last_date = last.get("started_at", "Unknown")[:10] if last else "Unknown"
        st.caption(
            f"📚 Local catalog: **{stats['product_count']:,}** products, "
            f"**{stats['antigen_count']:,}** antigens  |  "
            f"Last updated: {last_date}"
        )
    else:
        st.warning("Local catalog is empty. Use the **Scraper** page to fetch antibodies first.")

    # ─── Search Bar ─────────────────────────────────────────────────
    sc1, sc2 = st.columns([4, 1])
    with sc1:
        query = st.text_input(
            "Search antibodies",
            value=st.session_state.price_search_query,
            placeholder="e.g., CD3, Ki67, GFAP, Vimentin...",
        )
        st.session_state.price_search_query = query
    with sc2:
        st.write("")
        search_btn = st.button("🔍 Search", type="primary", use_container_width=True)

    # ─── Filters ────────────────────────────────────────────────────
    with st.expander("🔽 Filters"):
        fc1, fc2, fc3, fc4 = st.columns(4)
        with fc1:
            host_filter = st.selectbox("Host Species",
                ["Any", "Rabbit", "Mouse", "Goat", "Rat", "Human", "Chicken", "Donkey"])
        with fc2:
            app_filter = st.selectbox("Application",
                ["Any", "IF", "IHC", "ICC", "FC", "WB", "ELISA", "IP"])
        with fc3:
            conjugate_filter = st.text_input("Conjugate", "", placeholder="e.g., AF647, FITC")
        with fc4:
            reactivity_filter = st.selectbox("Reactivity",
                ["Any", "Human", "Mouse", "Rat", "Rabbit"])

        sort_by = st.radio("Sort by",
            ["Price (Low → High)", "Price (High → Low)", "Product Name"],
            horizontal=True)

    # ─── Execute Search ─────────────────────────────────────────────
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
                st.success(f"Found {len(results)} results.")
            else:
                st.session_state.price_results = []
                # No results — offer to scrape
                st.warning(f"No results found for **{query}** in the local catalog.")
                st.caption("This target may not have been scraped yet.")
                if st.button("🌐 Scrape Biocompare for this target →", type="primary"):
                    st.session_state.scrape_targets = query
                    st.session_state.nav_page = "Scraper"
                    st.rerun()

    # ─── Results Table ──────────────────────────────────────────────
    results = st.session_state.get("price_results", [])

    if results:
        st.subheader(f"Results ({len(results)})")

        # Post-filter
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
            # Scrollable results table
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
            st.dataframe(df, use_container_width=True, hide_index=True, height=400)

            # Wishlist buttons per result
            for i, r in enumerate(filtered[:30]):
                cols = st.columns([4, 1, 1])
                with cols[0]:
                    st.caption(
                        f"{r.vendor} — {r.catalog_no or 'N/A'} — "
                        f"{r.product_name[:50]}"
                    )
                with cols[1]:
                    if st.button("🛒 Add to Wishlist", key=f"wish_{i}"):
                        with db.inventory_session() as session:
                            add_to_wishlist(
                                session,
                                product_name=r.product_name,
                                target=r.target,
                                vendor=r.vendor,
                                catalog_no=r.catalog_no,
                                host_species=r.host_species,
                                isotype=r.isotype,
                                clonality=r.clonality,
                                conjugate=r.conjugate,
                                applications=", ".join(r.validated_applications) if r.validated_applications else "",
                                reactivity=", ".join(r.reactivity) if r.reactivity else "",
                                price=r.price,
                                package_size=r.package_size,
                                url=r.url,
                            )
                        st.success(f"Added to wishlist!")
                with cols[2]:
                    if r.url:
                        st.link_button("🔗 View", r.url)
        else:
            st.info("No results match your filters.")
