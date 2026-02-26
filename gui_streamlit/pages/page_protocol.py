"""
gui_streamlit/pages/page_protocol.py — Protocol Generator.

Converts a validated panel into a complete experimental protocol.
Section-by-section editor with PDF/DOCX/XLSX export.
"""

import streamlit as st
import os
import tempfile


def render():
    from gui_streamlit.shared import get_db_manager, load_config
    from core.protocol_generator import generate_protocol
    from core.exporters import export_docx, export_pdf, export_xlsx
    from core.cost_calculator import calculate_panel_cost
    from core.models import ExportFormat

    db = get_db_manager()
    config = load_config()

    st.title("📄 Protocol Generator")

    panel = st.session_state.panel_current

    if not panel:
        st.warning("No panel loaded. Please build a panel first in the Panel Builder.")
        if st.button("→ Go to Panel Builder"):
            st.session_state.nav_page = "Panel Builder"
            st.rerun()
        return

    st.info(f"**Panel:** {panel.name} | **Type:** {panel.experiment_type.value} | "
            f"**Targets:** {', '.join(t.target_name for t in panel.targets)}")

    # ─── Generate Protocol ───────────────────────────────────────────
    if st.button("🔄 Generate Protocol", type="primary") or st.session_state.proto_current:
        if not st.session_state.proto_current or st.button("Regenerate"):
            lab = config.get("lab", {})
            protocol = generate_protocol(
                panel=panel,
                lab_name=lab.get("name", ""),
                researcher=panel.researcher_name or lab.get("default_researcher", ""),
                institution=lab.get("institution", ""),
            )
            st.session_state.proto_current = protocol

        protocol = st.session_state.proto_current

        if not protocol:
            return

        # ─── Protocol Title & Version ────────────────────────────
        col1, col2 = st.columns([3, 1])
        with col1:
            protocol.title = st.text_input("Protocol Title", value=protocol.title)
        with col2:
            protocol.version = st.text_input("Version", value=protocol.version)

        st.divider()

        # ─── Section Editor (Tabs) ──────────────────────────────
        tabs = st.tabs([s.title for s in protocol.sections])
        for tab, section in zip(tabs, protocol.sections):
            with tab:
                section.content = st.text_area(
                    f"Edit: {section.title}",
                    value=section.content,
                    height=300,
                    key=f"section_{section.order}",
                )

        st.divider()

        # ─── Controls Checklist ──────────────────────────────────
        st.subheader("Controls")
        for i, ctrl in enumerate(protocol.controls):
            cols = st.columns([1, 3, 3, 1])
            with cols[0]:
                ctrl.is_required = st.checkbox("Req", value=ctrl.is_required, key=f"ctrl_req_{i}")
            with cols[1]:
                st.write(f"**{ctrl.control_type}**")
            with cols[2]:
                st.caption(ctrl.purpose)
            with cols[3]:
                if ctrl.is_required:
                    st.success("✅")
                else:
                    st.info("Optional")

        st.divider()

        # ─── Cost Summary ────────────────────────────────────────
        st.subheader("Cost Summary")
        cost = calculate_panel_cost(panel)
        st.session_state.cost_estimate = cost

        cc1, cc2, cc3 = st.columns(3)
        cc1.metric("In-House Items", len(cost.in_house_items), delta="$0")
        cc2.metric("To Purchase", len(cost.to_purchase))
        cc3.metric("Estimated Total", f"${cost.grand_total:.2f}")

        if cost.to_purchase:
            with st.expander("📋 Purchase List"):
                for item in cost.to_purchase:
                    st.write(f"• {item.item_name}: {item.notes}")

        st.divider()

        # ─── Export Buttons ──────────────────────────────────────
        st.subheader("Export")
        exp_c1, exp_c2, exp_c3 = st.columns(3)

        with exp_c1:
            if st.button("📝 Export DOCX", use_container_width=True):
                with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
                    export_docx(protocol, f.name)
                    with open(f.name, "rb") as file:
                        st.download_button(
                            "📥 Download DOCX",
                            file.read(),
                            f"{protocol.title.replace(' ', '_')}.docx",
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        )
                    os.unlink(f.name)

        with exp_c2:
            if st.button("📕 Export PDF", use_container_width=True):
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                    export_pdf(protocol, f.name)
                    with open(f.name, "rb") as file:
                        st.download_button(
                            "📥 Download PDF",
                            file.read(),
                            f"{protocol.title.replace(' ', '_')}.pdf",
                            "application/pdf",
                        )
                    os.unlink(f.name)

        with exp_c3:
            if st.button("📊 Export Excel", use_container_width=True):
                with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
                    export_xlsx(protocol, f.name)
                    with open(f.name, "rb") as file:
                        st.download_button(
                            "📥 Download Excel",
                            file.read(),
                            f"{protocol.title.replace(' ', '_')}_reagents.xlsx",
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        )
                    os.unlink(f.name)

        # ─── Save Protocol ───────────────────────────────────────
        st.divider()
        if st.button("💾 Save Protocol to History"):
            from core.database import SavedProtocolORM
            from datetime import datetime
            with db.inventory_session() as session:
                orm = SavedProtocolORM(
                    id=protocol.id,
                    panel_id=protocol.panel_id,
                    title=protocol.title,
                    protocol_json=protocol.model_dump_json(),
                    created_at=datetime.now(),
                    version=protocol.version,
                )
                session.merge(orm)
            st.success("Protocol saved!")
