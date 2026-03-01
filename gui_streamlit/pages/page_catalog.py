"""
gui_streamlit/pages/page_catalog.py — Local Biocompare Catalog Browser.

Browse, search, sort, and filter all antibodies in the local database.
Includes integrated wishlist functionality via row selection.
"""

import streamlit as st
import pandas as pd


def render():
    from core.biocompare_scraper import get_catalog_db, run_scrape
    from core.scraper import get_catalog_stats

    st.title("📚 Local Antibody Catalog")

    stats = get_catalog_stats()
    catalog = get_catalog_db()

    if not catalog or not stats or stats.get("product_count", 0) == 0:
        st.warning(
            "No local catalog data. Go to the **Scraper** page to fetch antibodies from Biocompare."
        )
        if st.button("→ Go to Scraper"):
            st.session_state.nav_page = "Scraper"
            st.rerun()
        return

    # ─── Stats bar ──────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Products", f"{stats['product_count']:,}")
    c2.metric("Antigens", f"{stats['antigen_count']:,}")
    c3.metric("Vendors", f"{stats['vendor_count']:,}")
    last = stats.get("last_scrape")
    c4.metric("Last Updated", last.get("started_at", "N/A")[:10] if last else "Never")

    # ─── Search & Filters ───────────────────────────────────────────
    st.subheader("Search & Filter")

    sc1, sc2 = st.columns([4, 1])
    with sc1:
        query = st.text_input("Search", placeholder="Product name, target, vendor...", key="cat_query")
    with sc2:
        st.write("")
        search_btn = st.button("🔍 Search", type="primary", use_container_width=True, key="cat_search")

    fc1, fc2, fc3, fc4, fc5 = st.columns(5)
    with fc1:
        f_target = st.text_input("Target", key="cat_target", placeholder="e.g., CD3")
    with fc2:
        f_host = st.selectbox("Host", ["Any", "Rabbit", "Mouse", "Goat", "Rat", "Chicken", "Donkey"], key="cat_host")
    with fc3:
        f_app = st.selectbox("Application", ["Any", "IF", "IHC", "ICC", "FC", "WB", "ELISA", "IP"], key="cat_app")
    with fc4:
        f_conjugate = st.text_input("Conjugate", key="cat_conj", placeholder="e.g., AF647")
    with fc5:
        f_reactivity = st.selectbox("Reactivity", ["Any", "Human", "Mouse", "Rat", "Rabbit"], key="cat_react")

    max_results = st.slider("Max results", 10, 500, 100, step=10, key="cat_max")

    # ─── Query ──────────────────────────────────────────────────────
    if search_btn or query or f_target:
        results = catalog.search_products(
            query=query,
            target=f_target,
            host=f_host if f_host != "Any" else "",
            application=f_app if f_app != "Any" else "",
            conjugate=f_conjugate,
            reactivity=f_reactivity if f_reactivity != "Any" else "",
            max_results=max_results,
        )

        if results:
            st.success(f"Found {len(results)} products")

            # Store raw results for wishlist access
            st.session_state["cat_results_raw"] = results

            # Build dataframe
            df = pd.DataFrame(results)

            # Select and rename display columns
            display_cols = {
                "antigen_name": "Target",
                "product_name": "Product",
                "vendor": "Vendor",
                "catalog_no": "Catalog #",
                "price": "Price",
                "package_size": "Size",
                "price_per_ug": "$/µg",
                "host_species": "Host",
                "conjugate": "Conjugate",
                "applications": "Applications",
                "reactivity": "Reactivity",
                "clonality": "Clonality",
                "isotype": "Isotype",
            }
            available = [c for c in display_cols if c in df.columns]
            df_display = df[available].rename(columns=display_cols)

            # Format price
            if "Price" in df_display.columns:
                df_display["Price"] = df_display["Price"].apply(
                    lambda x: f"${x:.2f}" if x and x > 0 else "—"
                )

            # Format $/µg
            if "$/µg" in df_display.columns:
                df_display["$/µg"] = df_display["$/µg"].apply(
                    lambda x: f"${x:.2f}" if x and x > 0 else "—"
                )

            # Sort controls
            sort_options = ["Target", "Vendor", "Price", "$/µg", "Product"]
            sort_avail = [s for s in sort_options if s in df_display.columns]
            sort_col = st.selectbox("Sort by", sort_avail, key="cat_sort")
            if sort_col:
                ascending = sort_col not in ("Price",)
                df_display = df_display.sort_values(sort_col, ascending=ascending, na_position="last")

            # ─── Interactive table with row selection ─────────────
            st.caption("Select rows to add to wishlist:")
            event = st.dataframe(
                df_display,
                use_container_width=True,
                hide_index=True,
                height=500,
                on_select="rerun",
                selection_mode="multi-row",
            )

            # ─── Add Selected to Wishlist ─────────────────────────
            selected_rows = []
            if event and event.selection and event.selection.rows:
                selected_rows = event.selection.rows

            if selected_rows:
                st.info(f"**{len(selected_rows)}** product(s) selected")
                if st.button("🛒 Add Selected to Wishlist", type="primary"):
                    added = 0
                    for idx in selected_rows:
                        if idx < len(results):
                            _add_to_wishlist(results[idx])
                            added += 1
                    st.success(f"Added {added} product(s) to wishlist!")
                    st.rerun()

        else:
            st.info("No results found. Try different search terms or broaden filters.")

    # ─── Antigen Index ──────────────────────────────────────────────
    with st.expander("📖 Browse by antigen"):
        antigens = catalog.get_antigens()
        if antigens:
            antigen_df = pd.DataFrame(antigens)
            if "name" in antigen_df.columns:
                st.dataframe(
                    antigen_df[["name", "product_count", "last_scraped"]].rename(columns={
                        "name": "Antigen", "product_count": "Products", "last_scraped": "Last Scraped",
                    }),
                    use_container_width=True,
                    hide_index=True,
                    height=400,
                )
        else:
            st.info("No antigens in catalog.")


def _add_to_wishlist(row: dict):
    """Add a catalog row to the wishlist."""
    from gui_streamlit.shared import get_db_manager
    from core.wishlist import add_to_wishlist

    db = get_db_manager()
    with db.inventory_session() as session:
        add_to_wishlist(
            session,
            product_name=row.get("product_name", ""),
            target=row.get("antigen_name", ""),
            vendor=row.get("vendor", ""),
            catalog_no=row.get("catalog_no", ""),
            host_species=row.get("host_species", ""),
            isotype=row.get("isotype", ""),
            clonality=row.get("clonality", ""),
            conjugate=row.get("conjugate", ""),
            applications=row.get("applications", ""),
            reactivity=row.get("reactivity", ""),
            price=float(row.get("price", 0) or 0),
            package_size=row.get("package_size", ""),
            url=row.get("detail_url", "") or row.get("supplier_url", ""),
        )
