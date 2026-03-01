"""
gui_streamlit/pages/page_wishlist.py — Purchase Wishlist.

Tracks antibodies we want to buy. Flow:
  Wishlist → Mark as Ordered → Confirm Received → Moves to Inventory
"""

import streamlit as st
import pandas as pd


def _orm_to_dict(item) -> dict:
    """Convert a WishlistItemORM to a plain dict while session is open."""
    return {
        "id": item.id,
        "target": item.target,
        "product_name": item.product_name,
        "vendor": item.vendor,
        "catalog_no": item.catalog_no,
        "price": item.price,
        "package_size": item.package_size,
        "host_species": item.host_species,
        "conjugate": item.conjugate,
        "antibody_type": item.antibody_type,
        "priority": item.priority,
        "added_at": item.added_at,
        "notes": item.notes,
        "url": item.url,
        "status": item.status,
        "isotype": item.isotype,
        "clonality": item.clonality,
        "applications": item.applications,
        "reactivity": item.reactivity,
    }


def render():
    from gui_streamlit.shared import get_db_manager
    from core.wishlist import (
        get_all_wishlist, get_wishlist_summary, update_status,
        remove_from_wishlist, move_to_inventory, add_to_wishlist,
    )

    db = get_db_manager()

    st.title("🛒 Purchase Wishlist")

    # ─── Summary ────────────────────────────────────────────────────
    with db.inventory_session() as session:
        summary = get_wishlist_summary(session)

    c1, c2, c3 = st.columns(3)
    c1.metric("On Wishlist", summary["wishlist"])
    c2.metric("Ordered", summary["ordered"])
    c3.metric("Total Items", summary["total"])

    # ─── Tab View ───────────────────────────────────────────────────
    tab_wish, tab_ordered, tab_add = st.tabs([
        "📋 Wishlist", "📦 Ordered", "➕ Add Manually"
    ])

    # ─── Wishlist Tab ───────────────────────────────────────────────
    with tab_wish:
        with db.inventory_session() as session:
            raw_items = get_all_wishlist(session, status="wishlist")
            items = [_orm_to_dict(it) for it in raw_items]

        if not items:
            st.info(
                "Wishlist is empty. Add antibodies from the "
                "**Catalog** page or from the **Panel Builder**."
            )
        else:
            # Table
            df = pd.DataFrame([
                {
                    "Target": item["target"],
                    "Product": (item["product_name"] or "")[:60],
                    "Vendor": item["vendor"],
                    "Catalog #": item["catalog_no"],
                    "Price": f"${item['price']:.2f}" if item["price"] else "—",
                    "Size": item["package_size"],
                    "Host": item["host_species"],
                    "Conjugate": item["conjugate"],
                    "Type": item["antibody_type"],
                    "Priority": item["priority"],
                    "Added": item["added_at"].strftime("%Y-%m-%d") if item["added_at"] else "",
                }
                for item in items
            ])
            st.dataframe(df, use_container_width=True, hide_index=True, height=350)

            # Per-item actions
            st.subheader("Actions")
            for i, item in enumerate(items):
                cols = st.columns([3, 1, 1, 1])
                with cols[0]:
                    label = f"**{item['target']}** — {item['vendor']} — {item['catalog_no'] or 'N/A'}"
                    if item["price"]:
                        label += f" — ${item['price']:.2f}"
                    st.caption(label)
                with cols[1]:
                    if st.button("📦 Mark Ordered", key=f"order_{item['id']}"):
                        with db.inventory_session() as session:
                            update_status(session, item["id"], "ordered")
                        st.success(f"Marked as ordered!")
                        st.rerun()
                with cols[2]:
                    pri_opts = ["low", "normal", "high", "urgent"]
                    current_pri = item["priority"] if item["priority"] in pri_opts else "normal"
                    new_pri = st.selectbox(
                        "Priority", pri_opts,
                        index=pri_opts.index(current_pri),
                        key=f"pri_{item['id']}",
                        label_visibility="collapsed",
                    )
                    if new_pri != item["priority"]:
                        with db.inventory_session() as session:
                            from core.database import WishlistItemORM
                            wi = session.query(WishlistItemORM).filter_by(id=item["id"]).first()
                            if wi:
                                wi.priority = new_pri
                        st.rerun()
                with cols[3]:
                    if st.button("🗑️", key=f"del_{item['id']}"):
                        with db.inventory_session() as session:
                            remove_from_wishlist(session, item["id"])
                        st.rerun()

    # ─── Ordered Tab ────────────────────────────────────────────────
    with tab_ordered:
        with db.inventory_session() as session:
            raw_ordered = get_all_wishlist(session, status="ordered")
            ordered_items = [_orm_to_dict(it) for it in raw_ordered]

        if not ordered_items:
            st.info("No items currently on order.")
        else:
            st.caption(
                "When an order arrives, click **Received → Inventory** to move it "
                "to your lab inventory."
            )

            df = pd.DataFrame([
                {
                    "Target": item["target"],
                    "Product": (item["product_name"] or "")[:60],
                    "Vendor": item["vendor"],
                    "Catalog #": item["catalog_no"],
                    "Price": f"${item['price']:.2f}" if item["price"] else "—",
                    "Size": item["package_size"],
                    "Added": item["added_at"].strftime("%Y-%m-%d") if item["added_at"] else "",
                }
                for item in ordered_items
            ])
            st.dataframe(df, use_container_width=True, hide_index=True, height=300)

            for i, item in enumerate(ordered_items):
                cols = st.columns([3, 1, 1])
                with cols[0]:
                    st.caption(
                        f"**{item['target']}** — {item['vendor']} — "
                        f"{item['catalog_no'] or 'N/A'}"
                    )
                with cols[1]:
                    if st.button("✅ Received → Inventory", key=f"recv_{item['id']}", type="primary"):
                        with db.inventory_session() as session:
                            inv_id = move_to_inventory(session, item["id"])
                        if inv_id:
                            st.success(
                                f"Moved to inventory! Go to the **Inventory** page "
                                f"to fill in storage details (location, dilutions, etc.)."
                            )
                            st.rerun()
                        else:
                            st.error("Could not move to inventory.")
                with cols[2]:
                    if st.button("← Back to Wishlist", key=f"unorder_{item['id']}"):
                        with db.inventory_session() as session:
                            update_status(session, item["id"], "wishlist")
                        st.rerun()

    # ─── Manual Add Tab ─────────────────────────────────────────────
    with tab_add:
        st.caption("Manually add an antibody to the wishlist.")
        with st.form("manual_wishlist"):
            mc1, mc2 = st.columns(2)
            with mc1:
                m_target = st.text_input("Target *", placeholder="e.g., CD3")
                m_vendor = st.text_input("Vendor", placeholder="e.g., Abcam")
                m_catalog = st.text_input("Catalog #")
                m_product = st.text_input("Product Name")
                m_type = st.selectbox("Antibody Type", ["primary", "secondary", "pairs"])
            with mc2:
                m_price = st.number_input("Price ($)", min_value=0.0, step=10.0)
                m_size = st.text_input("Package Size", placeholder="e.g., 100 µg")
                m_host = st.text_input("Host Species", placeholder="e.g., Rabbit")
                m_conjugate = st.text_input("Conjugate", placeholder="e.g., Unconjugated, AF647")
                m_priority = st.selectbox("Priority", ["low", "normal", "high", "urgent"], index=1)

            m_notes = st.text_area("Notes", height=60)
            m_url = st.text_input("Product URL")

            if st.form_submit_button("🛒 Add to Wishlist", type="primary"):
                if not m_target:
                    st.error("Target is required.")
                else:
                    with db.inventory_session() as session:
                        add_to_wishlist(
                            session,
                            product_name=m_product,
                            target=m_target,
                            vendor=m_vendor,
                            catalog_no=m_catalog,
                            host_species=m_host,
                            conjugate=m_conjugate,
                            price=m_price,
                            package_size=m_size,
                            url=m_url,
                            antibody_type=m_type,
                            notes=m_notes,
                            priority=m_priority,
                        )
                    st.success("Added to wishlist!")
                    st.rerun()