"""
gui_streamlit/pages/page_panel_builder.py — Panel Design Wizard.

6-step workflow: Setup → Targets → Antibody Assignment → Fluorophore/Channel →
Validation → Alternative Plans.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import numpy as np


def render():
    from gui_streamlit.shared import get_db_manager, load_instrument_profiles
    from core.models import (
        Panel, PanelTarget, ExperimentType, SampleType, SampleSpecies,
        ExpressionLevel, SubcellularLocation, InstrumentProfile, Channel, Laser,
    )
    from core.fluorophores import load_fluorophores, get_fluorophores_for_channel, get_fluorophore
    from core.rules_engine import validate_panel, has_errors, count_by_severity
    from core.panel_builder import suggest_assignments
    from core.alternative_plans import generate_alternatives, compare_plans
    from core.inventory import get_matching_antibodies, save_panel

    db = get_db_manager()
    profiles_data = load_instrument_profiles()

    st.title("🧪 Panel Builder")

    # Step indicator
    step = st.session_state.panel_step
    steps = ["Setup", "Targets", "Antibodies", "Channels", "Validation", "Alternatives"]
    step_cols = st.columns(len(steps))
    for i, (col, name) in enumerate(zip(step_cols, steps)):
        s = i + 1
        if s < step:
            col.success(f"✅ {s}. {name}")
        elif s == step:
            col.info(f"▶️ {s}. {name}")
        else:
            col.caption(f"⬜ {s}. {name}")

    st.divider()

    # ─── Step 1: Experiment Setup ────────────────────────────────────
    if step == 1:
        st.subheader("Step 1: Experiment Setup")

        panel = st.session_state.panel_current or Panel()

        with st.form("setup_form"):
            name = st.text_input("Experiment Name", value=panel.name)
            c1, c2 = st.columns(2)
            with c1:
                exp_type = st.selectbox("Experiment Type", [e.value for e in ExperimentType],
                    index=[e.value for e in ExperimentType].index(panel.experiment_type.value))
                sample_species = st.selectbox("Sample Species", [s.value for s in SampleSpecies],
                    index=[s.value for s in SampleSpecies].index(panel.sample_species.value))
                num_samples = st.number_input("Number of Samples", value=max(1, panel.num_samples), min_value=1)
            with c2:
                sample_type = st.selectbox("Sample Type", [s.value for s in SampleType],
                    index=[s.value for s in SampleType].index(panel.sample_type.value))
                profile_names = list(profiles_data.keys())
                active_idx = profile_names.index(st.session_state.active_profile) if st.session_state.active_profile in profile_names else 0
                profile_name = st.selectbox("Instrument Profile", profile_names, index=active_idx)
                researcher = st.text_input("Researcher Name", value=panel.researcher_name)

            submitted = st.form_submit_button("Next →", type="primary")

        if submitted:
            # Build instrument profile from YAML
            prof_data = profiles_data.get(profile_name, {})
            channels = [
                Channel(
                    name=ch["name"],
                    laser_nm=ch["laser_nm"],
                    filter_center=ch["filter_center"],
                    filter_bandwidth=ch["filter_bandwidth"],
                    common_fluorophores=ch.get("common_fluorophores", []),
                )
                for ch in prof_data.get("channels", [])
            ]
            instrument = InstrumentProfile(
                name=profile_name,
                channels=channels,
                mode=prof_data.get("mode", "conventional"),
            )

            panel = Panel(
                id=panel.id,
                name=name,
                experiment_type=ExperimentType(exp_type),
                sample_type=SampleType(sample_type),
                sample_species=SampleSpecies(sample_species),
                instrument_profile=instrument,
                num_samples=num_samples,
                researcher_name=researcher,
                targets=panel.targets,
            )
            st.session_state.panel_current = panel
            st.session_state.panel_step = 2
            st.rerun()

    # ─── Step 2: Define Targets ──────────────────────────────────────
    elif step == 2:
        st.subheader("Step 2: Define Targets")
        panel = st.session_state.panel_current

        if not panel:
            st.error("Please complete Step 1 first.")
            return

        st.caption(f"Instrument: {panel.instrument_profile.name if panel.instrument_profile else 'None'} — "
                   f"Available channels: {len(panel.instrument_profile.channels) if panel.instrument_profile else 0}")

        # Initialize targets list in state
        if "panel_targets_data" not in st.session_state:
            st.session_state.panel_targets_data = [
                {"target": t.target_name, "expression": t.expression_level.value,
                 "location": t.subcellular_location.value}
                for t in panel.targets
            ] if panel.targets else []

        # Add target button
        if st.button("➕ Add Target"):
            st.session_state.panel_targets_data.append(
                {"target": "", "expression": "Medium", "location": "Cytoplasmic"}
            )
            st.rerun()

        # Edit targets
        targets_data = st.session_state.panel_targets_data
        to_remove = None

        for i, td in enumerate(targets_data):
            cols = st.columns([3, 2, 2, 1])
            with cols[0]:
                td["target"] = st.text_input(f"Target {i+1}", value=td["target"],
                    key=f"tgt_{i}", placeholder="e.g., CD3, Ki67, GFAP")
            with cols[1]:
                td["expression"] = st.selectbox(f"Expression {i+1}",
                    [e.value for e in ExpressionLevel], key=f"expr_{i}",
                    index=[e.value for e in ExpressionLevel].index(td["expression"]))
            with cols[2]:
                td["location"] = st.selectbox(f"Location {i+1}",
                    [s.value for s in SubcellularLocation], key=f"loc_{i}",
                    index=[s.value for s in SubcellularLocation].index(td["location"]))
            with cols[3]:
                st.write("")
                if st.button("🗑️", key=f"del_{i}"):
                    to_remove = i

        if to_remove is not None:
            targets_data.pop(to_remove)
            st.rerun()

        # Check in-house availability
        if targets_data:
            st.divider()
            st.caption("**In-house availability:**")
            with db.inventory_session() as session:
                for td in targets_data:
                    if td["target"]:
                        matches = get_matching_antibodies(
                            session, target=td["target"],
                            species=panel.sample_species.value,
                        )
                        if matches:
                            st.success(f"✅ {td['target']} — {len(matches)} in-house option(s)")
                        else:
                            st.warning(f"❌ {td['target']} — not in inventory, will need purchasing")

        # Navigation
        nav_c1, nav_c2 = st.columns(2)
        with nav_c1:
            if st.button("← Back"):
                st.session_state.panel_step = 1
                st.rerun()
        with nav_c2:
            if st.button("Next →", type="primary"):
                valid_targets = [td for td in targets_data if td["target"].strip()]
                if not valid_targets:
                    st.error("Add at least one target.")
                else:
                    panel.targets = [
                        PanelTarget(
                            target_name=td["target"].strip(),
                            expression_level=ExpressionLevel(td["expression"]),
                            subcellular_location=SubcellularLocation(td["location"]),
                        )
                        for td in valid_targets
                    ]
                    st.session_state.panel_current = panel
                    st.session_state.panel_step = 3
                    st.rerun()

    # ─── Step 3: Antibody Assignment ─────────────────────────────────
    elif step == 3:
        st.subheader("Step 3: Antibody Assignment")
        panel = st.session_state.panel_current

        if not panel:
            st.error("Please complete previous steps.")
            return

        st.caption("For each target, select a primary antibody from inventory or mark for purchase.")

        # ── Refresh prices for panel targets ──
        target_names = [t.target_name for t in panel.targets if t.target_name]
        if target_names:
            with st.expander("🔄 Refresh Biocompare pricing for these targets"):
                st.caption(f"Targets: **{', '.join(target_names)}**")
                rc1, rc2 = st.columns([3, 1])
                with rc1:
                    refresh_headless = st.checkbox("Headless Chrome", value=False, key="pb_headless")
                with rc2:
                    refresh_deep = st.checkbox("Deep scrape", value=False, key="pb_deep")

                if st.button("🚀 Fetch fresh prices", key="pb_refresh_btn"):
                    try:
                        import selenium  # noqa: F401
                        from core.biocompare_scraper import run_scrape

                        progress = st.empty()
                        progress.info(f"Opening Chrome to fetch: {', '.join(target_names)}...")

                        result = run_scrape(
                            mode="targets",
                            targets=target_names,
                            headless=refresh_headless,
                            deep=refresh_deep,
                            max_pages=3,
                        )

                        progress.empty()

                        if result["status"] == "ok":
                            total = result.get("products", 0)
                            st.success(f"✅ Found {total} products. Check Price Search page for details.")
                            for t, info in result.get("per_target", {}).items():
                                icon = "✅" if info["status"] == "ok" and info["products"] > 0 else "⚠️"
                                st.caption(f"  {icon} **{t}**: {info['products']} products")
                        else:
                            st.error(f"Error: {result.get('message', 'unknown')}")

                    except ImportError:
                        st.error("Selenium not installed. Run: `pip install selenium webdriver-manager`")

        for i, target in enumerate(panel.targets):
            with st.expander(f"🎯 {target.target_name} ({target.expression_level.value})", expanded=True):
                # Check inventory
                with db.inventory_session() as session:
                    matches = get_matching_antibodies(
                        session, target=target.target_name,
                        species=panel.sample_species.value,
                    )

                if matches:
                    st.success(f"{len(matches)} in-house option(s)")
                    options = ["Select..."] + [
                        f"{m.vendor} — {m.catalog_no} ({m.host_species.value}, {m.conjugate})"
                        for m in matches
                    ]
                    sel = st.selectbox(f"In-house antibody for {target.target_name}",
                        options, key=f"ab_sel_{i}")

                    if sel != "Select...":
                        idx = options.index(sel) - 1
                        inv_item = matches[idx]
                        target.primary = inv_item.to_primary_antibody()
                        target.is_in_house = True
                        target.inventory_item_id = inv_item.id
                        st.caption(f"Host: {inv_item.host_species.value} | Clone: {inv_item.clone_name} | Conjugate: {inv_item.conjugate}")
                else:
                    st.warning("Not in inventory — will need to purchase")

                # Manual entry option
                with st.popover("✏️ Manual Entry"):
                    from core.models import PrimaryAntibody, HostSpecies, Clonality
                    m_host = st.selectbox("Host", [h.value for h in HostSpecies], key=f"man_host_{i}")
                    m_conj = st.text_input("Conjugate", "Unconjugated", key=f"man_conj_{i}")
                    m_cat = st.text_input("Catalog #", key=f"man_cat_{i}")
                    m_vendor = st.text_input("Vendor", key=f"man_vendor_{i}")
                    m_dil = st.text_input("Dilution", "1:200", key=f"man_dil_{i}")
                    if st.button("Apply", key=f"man_apply_{i}"):
                        target.primary = PrimaryAntibody(
                            target=target.target_name,
                            host_species=HostSpecies(m_host),
                            conjugate=m_conj,
                            catalog_no=m_cat,
                            vendor=m_vendor,
                            dilution=m_dil,
                        )
                        target.is_in_house = False
                        st.rerun()

                if target.primary:
                    st.info(f"Selected: {target.primary.display_name}")

        nav_c1, nav_c2 = st.columns(2)
        with nav_c1:
            if st.button("← Back"):
                st.session_state.panel_step = 2
                st.rerun()
        with nav_c2:
            if st.button("Next →", type="primary"):
                st.session_state.panel_current = panel
                st.session_state.panel_step = 4
                st.rerun()

    # ─── Step 4: Fluorophore & Channel Assignment ────────────────────
    elif step == 4:
        st.subheader("Step 4: Fluorophore & Channel Assignment")
        panel = st.session_state.panel_current

        if not panel or not panel.instrument_profile:
            st.error("Please complete previous steps.")
            return

        profile = panel.instrument_profile
        fluorophore_lib = load_fluorophores()

        # Auto-assign button
        if st.button("🤖 Auto-Assign Fluorophores", type="primary"):
            suggestions = suggest_assignments(panel, profile, max_alternatives=1)
            if suggestions:
                panel = suggestions[0]
                st.session_state.panel_current = panel
                st.success("Auto-assignment complete!")
                st.rerun()

        st.divider()

        # Manual assignment
        usable_channels = [ch for ch in profile.channels if ch.name.upper() != "DAPI"]

        for i, target in enumerate(panel.targets):
            cols = st.columns([2, 2, 2, 1])
            with cols[0]:
                st.write(f"**{target.target_name}** ({target.expression_level.value})")
            with cols[1]:
                channel_options = ["Unassigned"] + [ch.name for ch in usable_channels]
                current_ch = channel_options.index(target.channel) if target.channel in channel_options else 0
                ch_sel = st.selectbox(f"Channel", channel_options, index=current_ch, key=f"ch_{i}")
                target.channel = ch_sel if ch_sel != "Unassigned" else None
            with cols[2]:
                # Show fluorophores compatible with selected channel
                if target.channel:
                    ch_obj = next((c for c in profile.channels if c.name == target.channel), None)
                    if ch_obj:
                        compatible = get_fluorophores_for_channel(ch_obj)
                        fluor_options = ["Unassigned"] + [f.name for f in compatible]
                    else:
                        fluor_options = ["Unassigned"]
                else:
                    fluor_options = ["Unassigned"] + list(fluorophore_lib.keys())

                current_fl = fluor_options.index(target.fluorophore) if target.fluorophore in fluor_options else 0
                fl_sel = st.selectbox(f"Fluorophore", fluor_options, index=current_fl, key=f"fl_{i}")
                target.fluorophore = fl_sel if fl_sel != "Unassigned" else None
            with cols[3]:
                # Brightness indicator
                if target.fluorophore:
                    fluor = get_fluorophore(target.fluorophore)
                    if fluor:
                        from core.fluorophores import brightness_score
                        bs = brightness_score(fluor)
                        if target.expression_level.value in ("Rare", "Low") and bs >= 4:
                            st.success("✅")
                        elif target.expression_level.value in ("Rare", "Low") and bs < 4:
                            st.error("⚠️")
                        else:
                            st.info("OK")

        # Spillover heatmap
        assigned_targets = [t for t in panel.targets if t.fluorophore and t.channel]
        if len(assigned_targets) >= 2:
            st.divider()
            st.subheader("Spillover Preview")
            _render_spillover_heatmap(assigned_targets, profile)

        st.session_state.panel_current = panel

        nav_c1, nav_c2 = st.columns(2)
        with nav_c1:
            if st.button("← Back"):
                st.session_state.panel_step = 3
                st.rerun()
        with nav_c2:
            if st.button("Next → Validate", type="primary"):
                st.session_state.panel_step = 5
                st.rerun()

    # ─── Step 5: Validation Results ──────────────────────────────────
    elif step == 5:
        st.subheader("Step 5: Validation Results")
        panel = st.session_state.panel_current

        if not panel:
            st.error("Please complete previous steps.")
            return

        results = validate_panel(panel)
        st.session_state.validation_results = results
        counts = count_by_severity(results)

        # Summary metrics
        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("Errors", counts["ERROR"], delta=None)
        mc2.metric("Warnings", counts["WARNING"])
        mc3.metric("Info", counts["INFO"])

        if not results:
            st.success("🎉 Panel passes all validation checks!")
        else:
            for r in results:
                if r.severity.value == "ERROR":
                    with st.expander(f"🔴 [{r.rule_id}] {r.message}", expanded=True):
                        if r.affected_targets:
                            st.write(f"**Affected:** {', '.join(r.affected_targets)}")
                        st.write(f"**Suggestion:** {r.suggestion}")
                elif r.severity.value == "WARNING":
                    with st.expander(f"🟡 [{r.rule_id}] {r.message}"):
                        if r.affected_targets:
                            st.write(f"**Affected:** {', '.join(r.affected_targets)}")
                        st.write(f"**Suggestion:** {r.suggestion}")
                else:
                    with st.expander(f"🔵 [{r.rule_id}] {r.message}"):
                        st.write(f"**Suggestion:** {r.suggestion}")

        # Save panel
        if st.button("💾 Save Panel"):
            with db.inventory_session() as session:
                save_panel(session, panel)
            st.success("Panel saved!")

        nav_c1, nav_c2 = st.columns(2)
        with nav_c1:
            if st.button("← Back"):
                st.session_state.panel_step = 4
                st.rerun()
        with nav_c2:
            can_proceed = not has_errors(results)
            if st.button("Next → Alternatives" if can_proceed else "Fix Errors First",
                         type="primary" if can_proceed else "secondary",
                         disabled=not can_proceed):
                st.session_state.panel_step = 6
                st.rerun()

    # ─── Step 6: Alternative Plans ───────────────────────────────────
    elif step == 6:
        st.subheader("Step 6: Alternative Plans")
        panel = st.session_state.panel_current

        if not panel:
            st.error("Please complete previous steps.")
            return

        if st.button("🔄 Generate Alternatives"):
            with st.spinner("Generating alternative plans..."):
                plans = generate_alternatives(panel, max_plans=3)
                st.session_state.alt_plans = plans

        plans = st.session_state.alt_plans
        if plans:
            comparison = compare_plans(plans)

            # Comparison table
            comp_df = pd.DataFrame({
                "Plan": comparison["labels"],
                "Total Cost": comparison["total_costs"],
                "Validation": comparison["validation_scores"],
                "Spillover": comparison["overlap_scores"],
                "Errors": comparison["errors"],
                "Warnings": comparison["warnings"],
                "To Purchase": comparison["procurement_count"],
            })
            st.dataframe(comp_df, use_container_width=True, hide_index=True)

            # Plan details
            for plan in plans:
                with st.expander(f"📋 {plan.plan_label} — Score: {plan.validation_score:.0f}/100"):
                    for t in plan.panel.targets:
                        st.write(f"• {t.target_name} → {t.fluorophore or 'Unassigned'} ({t.channel or 'N/A'})")

                    if st.button(f"✅ Select {plan.plan_label}", key=f"sel_{plan.plan_id}"):
                        st.session_state.panel_current = plan.panel
                        st.success(f"Selected {plan.plan_label}!")
                        st.rerun()

        # Final actions
        st.divider()
        c1, c2, c3 = st.columns(3)
        with c1:
            if st.button("← Back"):
                st.session_state.panel_step = 5
                st.rerun()
        with c2:
            if st.button("📄 Generate Protocol", type="primary"):
                st.session_state.nav_page = "Protocol"
                st.rerun()
        with c3:
            if st.button("💾 Save Panel"):
                with db.inventory_session() as session:
                    save_panel(session, panel)
                st.success("Panel saved!")


def _render_spillover_heatmap(targets, profile):
    """Render a Plotly spillover heatmap for assigned fluorophores."""
    from core.fluorophores import get_fluorophore, compute_spillover

    labels = [f"{t.target_name}\n({t.fluorophore})" for t in targets]
    n = len(targets)
    matrix = np.zeros((n, n))

    for i, t1 in enumerate(targets):
        f1 = get_fluorophore(t1.fluorophore)
        if not f1:
            continue
        for j, t2 in enumerate(targets):
            ch = next((c for c in profile.channels if c.name == t2.channel), None)
            if ch and f1:
                matrix[i][j] = compute_spillover(f1, ch) * 100

    fig = go.Figure(data=go.Heatmap(
        z=matrix,
        x=labels,
        y=labels,
        colorscale="RdYlGn_r",
        text=np.round(matrix, 1),
        texttemplate="%{text}%",
        textfont={"size": 10},
        colorbar=dict(title="Spillover %"),
        zmin=0,
        zmax=50,
    ))
    fig.update_layout(
        title="Spillover Matrix (% of signal in other channels)",
        height=350,
        margin=dict(l=20, r=20, t=40, b=20),
    )
    st.plotly_chart(fig, use_container_width=True)
