"""
gui_streamlit/pages/page_inventory.py — Inventory Manager.

Full CRUD interface for antibody inventory with search, filtering,
CSV import/export, and usage logging.
"""

import streamlit as st
import pandas as pd
from datetime import date


def render():
    from gui_streamlit.shared import get_db_manager
    from core.inventory import (
        add_item, get_item, update_item, delete_item, get_all_items,
        search_items, get_low_stock, get_expiring, export_csv, import_csv,
        get_usage_log, increment_freeze_thaw,
    )
    from core.models import (
        InventoryItem, HostSpecies, Clonality, StorageTemp,
    )

    db = get_db_manager()
    st.title("📦 Antibody Inventory")

    # ─── Top Bar: Search + Actions ───────────────────────────────────
    col_search, col_actions = st.columns([3, 1])

    with col_search:
        query = st.text_input(
            "🔍 Search inventory",
            value=st.session_state.inv_search_query,
            placeholder="Search by target, vendor, catalog #, clone...",
        )
        st.session_state.inv_search_query = query

    with col_actions:
        st.write("")  # spacer
        action_cols = st.columns(3)
        with action_cols[0]:
            add_new = st.button("➕ Add", use_container_width=True)
        with action_cols[1]:
            do_export = st.button("📤 CSV", use_container_width=True)
        with action_cols[2]:
            do_import = st.button("📥 Import", use_container_width=True)

    # ─── Filter Bar ──────────────────────────────────────────────────
    with st.expander("🔽 Filters", expanded=False):
        fc1, fc2, fc3, fc4 = st.columns(4)
        with fc1:
            filter_host = st.selectbox("Host Species", ["All"] + [h.value for h in HostSpecies])
        with fc2:
            filter_conjugate = st.text_input("Conjugate", "")
        with fc3:
            filter_app = st.selectbox("Application", ["All", "IF", "IHC", "ICC", "FC", "WB", "ELISA"])
        with fc4:
            filter_stock = st.selectbox("Stock Status", ["All", "Low Stock", "Expiring Soon", "Expired"])

    # ─── Get Data ────────────────────────────────────────────────────
    with db.inventory_session() as session:
        if query:
            items = search_items(session, query)
        else:
            items = get_all_items(session)

        # Apply filters
        if filter_host != "All":
            items = [i for i in items if i.host_species.value == filter_host]
        if filter_conjugate:
            items = [i for i in items if filter_conjugate.lower() in i.conjugate.lower()]
        if filter_app != "All":
            items = [i for i in items if any(filter_app.lower() in a.lower() for a in i.validated_applications)]
        if filter_stock == "Low Stock":
            items = [i for i in items if i.is_low_stock]
        elif filter_stock == "Expiring Soon":
            items = [i for i in items if i.is_expiring_soon]
        elif filter_stock == "Expired":
            items = [i for i in items if i.is_expired]

    # ─── Inventory Table ─────────────────────────────────────────────
    st.subheader(f"Inventory ({len(items)} items)")

    if items:
        df = pd.DataFrame([
            {
                "Target": it.target,
                "Name": it.antibody_name,
                "Host": it.host_species.value,
                "Clone": it.clone_name,
                "Conjugate": it.conjugate,
                "Vendor": it.vendor,
                "Catalog #": it.catalog_no,
                "Volume (µL)": f"{it.total_volume_ul:.0f}",
                "Storage": it.storage_temp.value,
                "Location": it.location,
                "Expiry": str(it.expiry_date) if it.expiry_date else "N/A",
                "Status": _get_status(it),
                "id": it.id,
            }
            for it in items
        ])

        # Display table
        event = st.dataframe(
            df.drop(columns=["id"]),
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
        )

        # Handle row selection
        if event and event.selection and event.selection.rows:
            selected_idx = event.selection.rows[0]
            selected_id = df.iloc[selected_idx]["id"]
            st.session_state.inv_selected_item_id = selected_id
    else:
        st.info("No antibodies in inventory. Click '➕ Add' to add your first antibody.")

    # ─── Detail Panel ────────────────────────────────────────────────
    if st.session_state.inv_selected_item_id:
        _render_detail_panel(db, st.session_state.inv_selected_item_id)

    # ─── Add New Antibody Modal ──────────────────────────────────────
    if add_new:
        st.session_state["show_add_form"] = True

    if st.session_state.get("show_add_form", False):
        _render_add_edit_form(db)

    # ─── CSV Export ──────────────────────────────────────────────────
    if do_export:
        with db.inventory_session() as session:
            csv_data = export_csv(session)
        if csv_data:
            st.download_button(
                "📥 Download CSV",
                csv_data,
                "antibody_inventory.csv",
                "text/csv",
            )
        else:
            st.warning("No data to export.")

    # ─── CSV Import ──────────────────────────────────────────────────
    if do_import:
        uploaded = st.file_uploader("Upload CSV", type=["csv"])
        if uploaded:
            csv_text = uploaded.getvalue().decode("utf-8")
            with db.inventory_session() as session:
                count, errors = import_csv(session, csv_text)
            st.success(f"Imported {count} antibodies.")
            if errors:
                for err in errors:
                    st.error(err)
            st.rerun()


def _get_status(item) -> str:
    flags = []
    if item.is_expired:
        flags.append("🔴 Expired")
    elif item.is_expiring_soon:
        flags.append("🟡 Expiring")
    if item.is_low_stock:
        flags.append("📉 Low")
    if item.freeze_thaw_count > 3:
        flags.append("🧊 F/T>" + str(item.freeze_thaw_count))
    return " ".join(flags) if flags else "✅ OK"


def _parse_location(location_str: str) -> dict:
    """Parse a structured location string back into components.

    Expected format: 'Freezer / Shelf / Box / Position'
    Falls back gracefully if format doesn't match.
    """
    parts = {
        "freezer": "",
        "shelf": "",
        "box": "",
        "position": "",
    }
    if not location_str:
        return parts

    segments = [s.strip() for s in location_str.split("/")]
    if len(segments) >= 1:
        parts["freezer"] = segments[0]
    if len(segments) >= 2:
        parts["shelf"] = segments[1]
    if len(segments) >= 3:
        parts["box"] = segments[2]
    if len(segments) >= 4:
        parts["position"] = segments[3]

    return parts


def _build_location(freezer: str, shelf: str, box: str, position: str) -> str:
    """Build a structured location string from components.

    Format: 'Freezer / Shelf / Box / Position'
    Omits trailing empty segments.
    """
    parts = [freezer.strip(), shelf.strip(), box.strip(), position.strip()]
    # Remove trailing empty parts
    while parts and not parts[-1]:
        parts.pop()
    if not parts:
        return ""
    return " / ".join(parts)


def _render_detail_panel(db, item_id: str):
    from core.inventory import get_item, delete_item, increment_freeze_thaw, get_usage_log

    st.divider()
    st.subheader("📋 Antibody Details")

    with db.inventory_session() as session:
        item = get_item(session, item_id)

    if not item:
        st.error("Item not found.")
        return

    col1, col2 = st.columns(2)
    with col1:
        st.write(f"**Target:** {item.target}")
        st.write(f"**Name:** {item.antibody_name}")
        st.write(f"**Host:** {item.host_species.value}")
        st.write(f"**Isotype:** {item.isotype}")
        st.write(f"**Clone:** {item.clone_name or 'N/A'}")
        st.write(f"**Conjugate:** {item.conjugate}")
        st.write(f"**Vendor:** {item.vendor}")
        st.write(f"**Catalog #:** {item.catalog_no}")
        st.write(f"**Lot #:** {item.lot_no}")

    with col2:
        st.write(f"**Volume:** {item.total_volume_ul:.0f} µL")
        st.write(f"**Concentration:** {item.concentration} mg/mL")
        st.write(f"**Storage:** {item.storage_temp.value}")
        # Display structured location
        loc = _parse_location(item.location)
        if item.location:
            st.write(f"**Location:** {item.location}")
            if loc["freezer"]:
                st.caption(
                    f"  Freezer: {loc['freezer']}"
                    + (f" → Shelf: {loc['shelf']}" if loc["shelf"] else "")
                    + (f" → Box: {loc['box']}" if loc["box"] else "")
                    + (f" → Pos: {loc['position']}" if loc["position"] else "")
                )
        else:
            st.write("**Location:** Not set")
        st.write(f"**Expiry:** {item.expiry_date or 'N/A'}")
        st.write(f"**Received:** {item.date_received or 'N/A'}")
        st.write(f"**Freeze-Thaw Count:** {item.freeze_thaw_count}")
        st.write(f"**IF Dilution:** {item.working_dilution_if or 'N/A'}")
        st.write(f"**Applications:** {', '.join(item.validated_applications) or 'N/A'}")
        st.write(f"**Reactivity:** {', '.join(item.reactivity) or 'N/A'}")

    if item.notes:
        st.info(f"**Notes:** {item.notes}")

    # Action buttons
    btn_col1, btn_col2, btn_col3 = st.columns(3)
    with btn_col1:
        if st.button("🧊 Add Freeze-Thaw"):
            with db.inventory_session() as session:
                new_count = increment_freeze_thaw(session, item_id)
            st.success(f"Freeze-thaw count: {new_count}")
            st.rerun()
    with btn_col2:
        if st.button("✏️ Edit"):
            st.session_state["edit_item_id"] = item_id
            st.session_state["show_add_form"] = True
            st.rerun()
    with btn_col3:
        if st.button("🗑️ Delete", type="secondary"):
            with db.inventory_session() as session:
                delete_item(session, item_id)
            st.session_state.inv_selected_item_id = None
            st.success("Deleted.")
            st.rerun()

    # Usage log
    with st.expander("📊 Usage History"):
        with db.inventory_session() as session:
            logs = get_usage_log(session, item_id)
        if logs:
            log_df = pd.DataFrame([
                {"Date": l.date_used, "Experiment": l.experiment_name, "Volume (µL)": l.volume_used_ul}
                for l in logs
            ])
            st.dataframe(log_df, use_container_width=True, hide_index=True)
        else:
            st.caption("No usage recorded yet.")


def _render_add_edit_form(db):
    from core.inventory import add_item, get_item, update_item
    from core.models import InventoryItem, HostSpecies, Clonality, StorageTemp

    edit_id = st.session_state.get("edit_item_id")
    existing = None
    if edit_id:
        with db.inventory_session() as session:
            existing = get_item(session, edit_id)

    st.divider()
    st.subheader("✏️ Edit Antibody" if existing else "➕ Add New Antibody")

    # Pre-parse existing location into structured parts
    existing_loc = _parse_location(existing.location if existing else "")

    with st.form("antibody_form"):
        # Identity
        st.markdown("**Identity**")
        fc1, fc2 = st.columns(2)
        with fc1:
            ab_name = st.text_input("Antibody Name", value=existing.antibody_name if existing else "")
            target = st.text_input("Target Antigen *", value=existing.target if existing else "")
            catalog = st.text_input("Catalog #", value=existing.catalog_no if existing else "")
        with fc2:
            lot = st.text_input("Lot #", value=existing.lot_no if existing else "")
            vendor = st.text_input("Vendor", value=existing.vendor if existing else "")
            clone = st.text_input("Clone Name", value=existing.clone_name if existing else "")

        # Biology
        st.markdown("**Biology**")
        bc1, bc2, bc3 = st.columns(3)
        with bc1:
            host = st.selectbox(
                "Host Species",
                [h.value for h in HostSpecies],
                index=[h.value for h in HostSpecies].index(existing.host_species.value) if existing else 1,
            )
        with bc2:
            isotype = st.text_input("Isotype", value=existing.isotype if existing else "IgG")
            clonality = st.selectbox(
                "Clonality",
                [c.value for c in Clonality],
                index=[c.value for c in Clonality].index(existing.clonality.value) if existing else 0,
            )
        with bc3:
            conjugate = st.text_input("Conjugate", value=existing.conjugate if existing else "Unconjugated")
            apps = st.multiselect(
                "Validated Applications",
                ["WB", "IHC", "ICC", "IF", "FC", "ELISA", "ChIP", "IP"],
                default=existing.validated_applications if existing else [],
            )

        reactivity = st.multiselect(
            "Reactivity (species)",
            ["Human", "Mouse", "Rat", "Rabbit", "Zebrafish", "Bovine", "Porcine"],
            default=existing.reactivity if existing else [],
        )

        # Stock
        st.markdown("**Stock**")
        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            conc = st.number_input("Concentration (mg/mL)", value=existing.concentration if existing else 0.0, min_value=0.0, step=0.1)
            volume = st.number_input("Total Volume (µL)", value=existing.total_volume_ul if existing else 0.0, min_value=0.0)
        with sc2:
            aliquot = st.number_input("Aliquot Size (µL)", value=existing.aliquot_size_ul if existing else 0.0, min_value=0.0)
            storage = st.selectbox(
                "Storage Temp",
                [s.value for s in StorageTemp],
                index=[s.value for s in StorageTemp].index(existing.storage_temp.value) if existing else 1,
            )
        with sc3:
            expiry = st.date_input("Expiry Date", value=existing.expiry_date if existing and existing.expiry_date else None)

        # ─── Storage Location (structured) ──────────────────────
        st.markdown("**Physical Storage Location**")
        loc1, loc2, loc3, loc4 = st.columns(4)
        with loc1:
            loc_freezer = st.text_input(
                "Freezer / Fridge",
                value=existing_loc["freezer"],
                placeholder="e.g., Freezer A, -80 Main",
            )
        with loc2:
            loc_shelf = st.text_input(
                "Shelf / Rack",
                value=existing_loc["shelf"],
                placeholder="e.g., Shelf 3, Rack B",
            )
        with loc3:
            loc_box = st.text_input(
                "Box / Drawer",
                value=existing_loc["box"],
                placeholder="e.g., Box 12, Drawer 2",
            )
        with loc4:
            loc_position = st.text_input(
                "Position / Slot",
                value=existing_loc["position"],
                placeholder="e.g., A5, Slot 7",
            )

        # Dilutions
        st.markdown("**Working Dilutions**")
        dc1, dc2, dc3 = st.columns(3)
        with dc1:
            dil_if = st.text_input("IF Dilution", value=existing.working_dilution_if if existing else "", placeholder="e.g., 1:200")
        with dc2:
            dil_fc = st.text_input("FC Dilution", value=existing.working_dilution_fc if existing else "")
        with dc3:
            dil_ihc = st.text_input("IHC Dilution", value=existing.working_dilution_ihc if existing else "")

        # Purchase
        price = st.number_input("Purchase Price ($)", value=existing.purchase_price if existing else 0.0, min_value=0.0)
        notes = st.text_area("Notes", value=existing.notes if existing else "")

        submitted = st.form_submit_button("💾 Save Antibody", type="primary")

    if submitted:
        if not target:
            st.error("Target antigen is required.")
            return

        # Build combined location string
        location = _build_location(loc_freezer, loc_shelf, loc_box, loc_position)

        item = InventoryItem(
            id=existing.id if existing else "",
            antibody_name=ab_name,
            target=target,
            catalog_no=catalog,
            lot_no=lot,
            vendor=vendor,
            host_species=host,
            isotype=isotype,
            clonality=clonality,
            clone_name=clone,
            conjugate=conjugate,
            validated_applications=apps,
            reactivity=reactivity,
            concentration=conc,
            total_volume_ul=volume,
            aliquot_size_ul=aliquot,
            storage_temp=storage,
            location=location,
            expiry_date=expiry if expiry else None,
            working_dilution_if=dil_if,
            working_dilution_fc=dil_fc,
            working_dilution_ihc=dil_ihc,
            purchase_price=price,
            notes=notes,
        )

        with db.inventory_session() as session:
            if existing:
                update_item(session, item)
                st.success(f"Updated: Anti-{target}")
            else:
                add_item(session, item)
                st.success(f"Added: Anti-{target}")

        st.session_state["show_add_form"] = False
        st.session_state.pop("edit_item_id", None)
        st.rerun()

    if st.button("Cancel"):
        st.session_state["show_add_form"] = False
        st.session_state.pop("edit_item_id", None)
        st.rerun()
