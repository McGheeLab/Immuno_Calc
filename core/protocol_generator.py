"""
core/protocol_generator.py — Protocol Assembly Engine.

Assembles a complete Protocol object from a validated Panel using
Jinja2 templates for each section. Templates allow protocol text
modification without touching Python code.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Optional

from core.models import (
    ControlEntry, ExperimentType, Panel, Protocol, ProtocolSection,
    SampleType, InstrumentProfile,
)


# ─── Protocol Template Text (Embedded) ───────────────────────────────────────
# In production, these would be Jinja2 .j2 files in data/protocol_templates/
# For now, embedded for simplicity and portability.

def generate_protocol(
    panel: Panel,
    profile: Optional[InstrumentProfile] = None,
    lab_name: str = "",
    researcher: str = "",
    institution: str = "",
) -> Protocol:
    """
    Generate a complete experimental protocol from a validated panel.
    """
    if profile is None:
        profile = panel.instrument_profile

    sections: list[ProtocolSection] = []
    order = 0

    # 1. Header / Overview
    order += 1
    sections.append(ProtocolSection(
        title="Experiment Overview",
        content=_generate_overview(panel, researcher),
        order=order,
    ))

    # 2. Reagents & Antibody Table
    order += 1
    sections.append(ProtocolSection(
        title="Reagents & Antibody Table",
        content=_generate_reagent_table(panel),
        order=order,
    ))

    # 3. Additional Reagents
    order += 1
    sections.append(ProtocolSection(
        title="Additional Reagents",
        content=_generate_additional_reagents(panel),
        order=order,
    ))

    # 4. Equipment Required
    order += 1
    sections.append(ProtocolSection(
        title="Equipment Required",
        content=_generate_equipment(panel, profile),
        order=order,
    ))

    # 5. Sample Preparation
    order += 1
    sections.append(ProtocolSection(
        title="Sample Preparation",
        content=_generate_sample_prep(panel),
        order=order,
    ))

    # 6. Blocking Step
    order += 1
    sections.append(ProtocolSection(
        title="Blocking",
        content=_generate_blocking(panel),
        order=order,
    ))

    # 7. Primary Antibody Incubation
    order += 1
    sections.append(ProtocolSection(
        title="Primary Antibody Incubation",
        content=_generate_primary_incubation(panel),
        order=order,
    ))

    # 8. Wash Steps
    order += 1
    sections.append(ProtocolSection(
        title="Wash Steps",
        content=_generate_wash_steps(panel),
        order=order,
    ))

    # 9. Secondary Antibody Incubation
    order += 1
    has_secondaries = any(t.secondary for t in panel.targets)
    if has_secondaries:
        sections.append(ProtocolSection(
            title="Secondary Antibody Incubation",
            content=_generate_secondary_incubation(panel),
            order=order,
        ))

    # 10. Nuclear Stain / Counterstain
    order += 1
    sections.append(ProtocolSection(
        title="Nuclear Counterstain",
        content=_generate_counterstain(panel),
        order=order,
    ))

    # 11. Mounting & Coverslipping
    order += 1
    if panel.experiment_type != ExperimentType.FLOW_CYTOMETRY:
        sections.append(ProtocolSection(
            title="Mounting & Coverslipping",
            content=_generate_mounting(panel),
            order=order,
        ))

    # 12. Imaging Parameters
    order += 1
    sections.append(ProtocolSection(
        title="Imaging Parameters",
        content=_generate_imaging_params(panel, profile),
        order=order,
    ))

    # 13. Controls
    controls = _generate_controls_list(panel)
    order += 1
    sections.append(ProtocolSection(
        title="Controls",
        content=_format_controls(controls),
        order=order,
    ))

    # 14. Troubleshooting
    order += 1
    sections.append(ProtocolSection(
        title="Troubleshooting Guide",
        content=_generate_troubleshooting(panel),
        order=order,
    ))

    return Protocol(
        panel_id=panel.id,
        title=f"Protocol: {panel.name}",
        author=researcher or panel.researcher_name,
        lab_name=lab_name,
        institution=institution,
        date=date.today(),
        sections=sections,
        controls=controls,
        panel=panel,
    )


# ─── Section Generators ──────────────────────────────────────────────────────

def _generate_overview(panel: Panel, researcher: str) -> str:
    targets_str = ", ".join(t.target_name for t in panel.targets if t.target_name)
    return (
        f"Experiment: {panel.name}\n"
        f"Type: {panel.experiment_type.value}\n"
        f"Sample: {panel.sample_type.value} ({panel.sample_species.value})\n"
        f"Targets: {targets_str}\n"
        f"Number of Samples: {panel.num_samples}\n"
        f"Researcher: {researcher or panel.researcher_name}\n"
        f"Date: {date.today().isoformat()}\n\n"
        f"This protocol describes the immunofluorescence staining procedure for "
        f"the detection of {targets_str} in {panel.sample_species.value} "
        f"{panel.sample_type.value} using {panel.experiment_type.value}."
    )


def _generate_reagent_table(panel: Panel) -> str:
    lines = [
        "| Target | Antibody | Host | Clone | Conjugate | Dilution | Catalog # | Vendor | Lot # | Channel |",
        "|--------|----------|------|-------|-----------|----------|-----------|--------|-------|---------|",
    ]

    for t in panel.targets:
        if not t.primary:
            continue
        p = t.primary
        lines.append(
            f"| {t.target_name} | Anti-{t.target_name} | {p.host_species.value} | "
            f"{p.clone_name or 'N/A'} | {p.conjugate} | {p.dilution or 'TBD'} | "
            f"{p.catalog_no or 'TBD'} | {p.vendor or 'TBD'} | {p.lot_no or 'TBD'} | "
            f"{t.channel or 'TBD'} |"
        )

    # Secondary antibodies
    secondaries = [t for t in panel.targets if t.secondary]
    if secondaries:
        lines.append("\n**Secondary Antibodies:**\n")
        lines.append("| Secondary | Host | Fluorophore | Dilution | Catalog # | Vendor |")
        lines.append("|-----------|------|-------------|----------|-----------|--------|")
        for t in secondaries:
            s = t.secondary
            lines.append(
                f"| Anti-{s.target_host.value} {s.isotype_specificity} | "
                f"{s.host_species.value} | {s.fluorophore} | {s.dilution} | "
                f"{s.catalog_no or 'TBD'} | {s.vendor or 'TBD'} |"
            )

    return "\n".join(lines)


def _generate_additional_reagents(panel: Panel) -> str:
    reagents = [
        "- PBS (Phosphate Buffered Saline), pH 7.4",
        "- PBS-T (PBS + 0.1% Tween-20) — for wash steps",
    ]

    if panel.experiment_type in (ExperimentType.ICC, ExperimentType.IHC_IF, ExperimentType.MULTIPLEX_IF):
        reagents.extend([
            "- 4% Paraformaldehyde (PFA) in PBS — fixative",
            "- 0.1-0.3% Triton X-100 in PBS — permeabilization",
            "- Normal serum (matched to secondary host species) — blocking",
            "- BSA (Bovine Serum Albumin) — optional blocking supplement",
        ])

    if panel.experiment_type == ExperimentType.IHC_IF:
        reagents.extend([
            "- Antigen retrieval buffer (Citrate pH 6.0 or EDTA pH 9.0)",
            "- Xylene — deparaffinization",
            "- Graded ethanol series (100%, 95%, 70%) — rehydration",
        ])

    reagents.extend([
        "- DAPI (1 µg/mL in PBS) or Hoechst 33342 (1:2000) — nuclear counterstain",
        "- Mounting medium (anti-fade, e.g., ProLong Gold or Fluoromount-G)",
        "- Glass coverslips (#1.5 thickness for optimal imaging)",
    ])

    return "\n".join(reagents)


def _generate_equipment(panel: Panel, profile: Optional[InstrumentProfile]) -> str:
    equipment = [
        "- Fluorescence microscope or confocal system",
    ]

    if profile:
        lasers = sorted(set(ch.laser_nm for ch in profile.channels))
        equipment.append(f"  - Laser lines: {', '.join(str(int(l)) + 'nm' for l in lasers)}")
        equipment.append(f"  - Channels: {', '.join(ch.name for ch in profile.channels)}")

    equipment.extend([
        "- Humidified chamber (dark) for antibody incubations",
        "- Orbital shaker or rocker for wash steps",
        "- Micropipettes and tips",
        "- Timer",
    ])

    if panel.experiment_type == ExperimentType.IHC_IF:
        equipment.extend([
            "- Pressure cooker or microwave for antigen retrieval",
            "- Coplin jars for deparaffinization/rehydration",
            "- PAP pen for hydrophobic barrier",
        ])

    if panel.experiment_type == ExperimentType.FLOW_CYTOMETRY:
        equipment.extend([
            "- Flow cytometer with appropriate laser configuration",
            "- FACS tubes or 96-well plates",
            "- Centrifuge with plate/tube adapters",
        ])

    return "\n".join(equipment)


def _generate_sample_prep(panel: Panel) -> str:
    if panel.sample_type == SampleType.FFPE_TISSUE:
        return (
            "1. Deparaffinize sections in xylene: 2 × 5 min\n"
            "2. Rehydrate through graded ethanol series:\n"
            "   - 100% ethanol: 2 × 3 min\n"
            "   - 95% ethanol: 1 × 3 min\n"
            "   - 70% ethanol: 1 × 3 min\n"
            "3. Rinse in distilled water: 2 × 2 min\n"
            "4. Antigen retrieval (see antibody-specific recommendations):\n"
            "   - Citrate buffer pH 6.0: 95-100°C for 20 min, OR\n"
            "   - EDTA/Tris buffer pH 9.0: 95-100°C for 20 min\n"
            "5. Cool slides in retrieval buffer for 20 min at room temperature\n"
            "6. Rinse in PBS: 2 × 5 min"
        )
    elif panel.sample_type == SampleType.CULTURED_CELLS:
        return (
            "1. Grow cells on sterile glass coverslips or chamber slides\n"
            "2. Aspirate culture medium\n"
            "3. Wash gently with warm PBS: 2 × briefly\n"
            "4. Fix with 4% PFA in PBS for 15 min at room temperature\n"
            "5. Wash with PBS: 3 × 5 min\n"
            "6. Permeabilize with 0.1% Triton X-100 in PBS for 10 min\n"
            "   (Skip if targeting only surface markers)\n"
            "7. Wash with PBS: 3 × 5 min"
        )
    elif panel.sample_type == SampleType.CRYOSECTION:
        return (
            "1. Cut frozen sections at 5-10 µm thickness\n"
            "2. Mount on charged slides\n"
            "3. Air dry for 30-60 min at room temperature\n"
            "4. Fix with cold acetone for 10 min at -20°C, OR\n"
            "   Fix with 4% PFA for 10 min at room temperature\n"
            "5. Wash with PBS: 3 × 5 min\n"
            "6. Permeabilize if needed (0.1% Triton X-100, 10 min)"
        )
    elif panel.sample_type == SampleType.SUSPENSION_CELLS:
        return (
            "1. Harvest cells and wash with PBS\n"
            "2. Count cells and adjust to 1 × 10⁶ cells/mL\n"
            "3. For surface staining: proceed directly to blocking\n"
            "4. For intracellular staining:\n"
            "   - Fix with 4% PFA for 15 min at room temperature\n"
            "   - Wash with PBS: 2 × centrifuge at 300g for 5 min\n"
            "   - Permeabilize with 0.1% saponin in PBS or commercial perm buffer"
        )
    else:
        return (
            "Prepare samples according to standard protocol for "
            f"{panel.sample_type.value}.\n"
            "Ensure samples are properly fixed and permeabilized before proceeding."
        )


def _generate_blocking(panel: Panel) -> str:
    # Determine blocking serum from secondary antibody host species
    secondary_hosts = set()
    for t in panel.targets:
        if t.secondary:
            secondary_hosts.add(t.secondary.host_species.value)

    if secondary_hosts:
        serum = f"normal {'/'.join(secondary_hosts).lower()} serum"
    else:
        serum = "normal serum (matched to secondary host species)"

    return (
        f"1. Prepare blocking buffer: 5-10% {serum} + 1% BSA in PBS\n"
        f"2. Apply blocking buffer to cover all sections/cells\n"
        f"3. Incubate for 1 hour at room temperature in humidified chamber\n"
        f"4. Do NOT wash after blocking — proceed directly to primary antibody\n\n"
        f"NOTE: If using mouse primary on mouse tissue, add Mouse-on-Mouse (M.O.M.) "
        f"blocking reagent per manufacturer instructions."
    )


def _generate_primary_incubation(panel: Panel) -> str:
    lines = ["Prepare primary antibody cocktail in blocking buffer:\n"]

    for t in panel.targets:
        if not t.primary:
            continue
        dilution = t.primary.dilution or "manufacturer recommended dilution"
        lines.append(
            f"- Anti-{t.target_name} ({t.primary.host_species.value}, "
            f"{t.primary.clone_name or 'polyclonal'}): {dilution}"
        )

    lines.extend([
        "",
        "Incubation:",
        "1. Remove blocking buffer (do not wash)",
        f"2. Apply primary antibody solution ({100 if panel.num_samples == 1 else 'calculated volume'} µL per section/well)",
        "3. Incubate overnight (12-16 hours) at 4°C in humidified chamber",
        "   OR 1-2 hours at room temperature (optimize per antibody)",
        "4. Ensure sections do not dry out during incubation",
    ])

    return "\n".join(lines)


def _generate_wash_steps(panel: Panel) -> str:
    return (
        "After primary antibody incubation:\n"
        "1. Carefully aspirate antibody solution\n"
        "2. Wash with PBS-T (PBS + 0.1% Tween-20):\n"
        "   - Wash 1: 5 min with gentle agitation\n"
        "   - Wash 2: 5 min with gentle agitation\n"
        "   - Wash 3: 5 min with gentle agitation\n"
        "3. Ensure complete coverage during each wash\n\n"
        "Repeat the same wash protocol after secondary antibody incubation."
    )


def _generate_secondary_incubation(panel: Panel) -> str:
    lines = [
        "⚠️ FROM THIS POINT FORWARD, PROTECT SAMPLES FROM LIGHT ⚠️\n",
        "Prepare secondary antibody cocktail in blocking buffer:\n"
    ]

    seen = set()
    for t in panel.targets:
        if not t.secondary:
            continue
        key = (t.secondary.target_host.value, t.secondary.fluorophore)
        if key in seen:
            continue
        seen.add(key)
        lines.append(
            f"- {t.secondary.display_name}: {t.secondary.dilution}"
        )

    lines.extend([
        "",
        "Incubation:",
        "1. Apply secondary antibody solution to sections/cells",
        "2. Incubate for 1 hour at room temperature in dark humidified chamber",
        "3. Wash with PBS-T: 3 × 5 min (in the dark)",
    ])

    return "\n".join(lines)


def _generate_counterstain(panel: Panel) -> str:
    return (
        "1. Prepare DAPI working solution: 1 µg/mL in PBS\n"
        "   OR Hoechst 33342: 1:2000 in PBS\n"
        "2. Apply to sections/cells for 5-10 min at room temperature\n"
        "3. Wash with PBS: 2 × 5 min\n\n"
        "NOTE: Many mounting media contain DAPI — if using such media, "
        "skip this separate DAPI incubation step."
    )


def _generate_mounting(panel: Panel) -> str:
    return (
        "1. Remove excess PBS (tilt slide and blot edge with tissue)\n"
        "2. Apply 1-2 drops of anti-fade mounting medium\n"
        "   Recommended: ProLong Gold, ProLong Diamond, or Fluoromount-G\n"
        "3. Carefully lower coverslip (#1.5 thickness) avoiding air bubbles\n"
        "4. Allow to cure:\n"
        "   - ProLong Gold/Diamond: 24 hours at room temperature in dark\n"
        "   - Fluoromount-G: 1 hour at room temperature\n"
        "5. Seal edges with clear nail polish (optional, for long-term storage)\n"
        "6. Store slides at 4°C in dark until imaging"
    )


def _generate_imaging_params(panel: Panel, profile: Optional[InstrumentProfile]) -> str:
    lines = ["Imaging Setup:\n"]

    if profile:
        lines.append(f"Instrument Profile: {profile.name}")
        lines.append(f"Mode: {profile.mode.value}\n")

        lines.append("Channel Configuration:")
        for ch in profile.channels:
            assigned_targets = [
                t.target_name for t in panel.targets
                if t.channel == ch.name
            ]
            target_str = ", ".join(assigned_targets) if assigned_targets else "Nuclear counterstain" if "DAPI" in ch.name.upper() else "Unassigned"
            lines.append(
                f"  - {ch.name}: Laser {int(ch.laser_nm)}nm, "
                f"Filter {ch.filter_notation}, Target: {target_str}"
            )

    lines.extend([
        "",
        "Acquisition Settings:",
        "- Use 20× objective for survey images",
        "- Use 40× or 63× oil immersion for high-resolution imaging",
        "- Set exposure times to avoid saturation in any channel",
        "- Acquire z-stacks if tissue section is >5 µm thick",
        "- Include a brightfield/DIC channel for tissue context",
        "",
        "Image each channel sequentially (not simultaneously) to minimize bleed-through.",
    ])

    return "\n".join(lines)


def _generate_controls_list(panel: Panel) -> list[ControlEntry]:
    """Auto-generate appropriate controls based on the panel design."""
    controls: list[ControlEntry] = []

    # Always: Unstained/autofluorescence control
    controls.append(ControlEntry(
        control_type="Unstained Control",
        purpose="Measures sample autofluorescence background in all channels",
        preparation="Process identically but omit all antibodies and stains",
        is_required=True,
    ))

    # No-primary controls for each secondary
    seen_secondaries = set()
    for t in panel.targets:
        if t.secondary:
            key = t.secondary.target_host.value
            if key not in seen_secondaries:
                seen_secondaries.add(key)
                controls.append(ControlEntry(
                    control_type=f"No-Primary Control (anti-{key} secondary)",
                    purpose=f"Detects non-specific binding of anti-{key} secondary antibody",
                    preparation=f"Omit all {key} primary antibodies; apply secondary normally",
                    is_required=True,
                ))

    # Isotype controls
    for t in panel.targets:
        if t.primary:
            controls.append(ControlEntry(
                control_type=f"Isotype Control ({t.primary.isotype}, {t.target_name})",
                purpose=f"Controls for non-specific Fc binding of {t.primary.isotype}",
                preparation=f"Replace anti-{t.target_name} with matched isotype at same concentration",
                is_required=False,
            ))

    # Flow cytometry specific
    if panel.experiment_type == ExperimentType.FLOW_CYTOMETRY:
        # FMO controls
        for t in panel.targets:
            if t.fluorophore:
                controls.append(ControlEntry(
                    control_type=f"FMO Control (minus {t.fluorophore}/{t.target_name})",
                    purpose=f"Sets gating boundary for {t.target_name} accounting for spillover",
                    preparation=f"Include all antibodies EXCEPT anti-{t.target_name}",
                    is_required=True,
                ))

        # Single-color compensation controls
        controls.append(ControlEntry(
            control_type="Single-Color Compensation Controls",
            purpose="Calculate compensation/spillover matrix",
            preparation="Stain compensation beads with each individual fluorophore-conjugated antibody",
            is_required=True,
        ))

        # Viability
        controls.append(ControlEntry(
            control_type="Viability Dye Control",
            purpose="Validates live/dead discrimination",
            preparation="Mix live and heat-killed (56°C, 10 min) cells, stain with viability dye",
            is_required=True,
        ))

    # IHC specific
    if panel.experiment_type in (ExperimentType.IHC_IF, ExperimentType.MULTIPLEX_IF):
        controls.append(ControlEntry(
            control_type="Positive Tissue Control",
            purpose="Confirms antibody works on tissue known to express targets",
            preparation="Include a section of tissue known to express all target proteins",
            is_required=True,
        ))
        controls.append(ControlEntry(
            control_type="Negative Tissue Control",
            purpose="Confirms antibody specificity — no signal on negative tissue",
            preparation="Include a section of tissue known to NOT express target proteins",
            is_required=True,
        ))

    return controls


def _format_controls(controls: list[ControlEntry]) -> str:
    lines = []
    for i, ctrl in enumerate(controls, 1):
        req = "REQUIRED" if ctrl.is_required else "Recommended"
        lines.append(
            f"{i}. [{req}] {ctrl.control_type}\n"
            f"   Purpose: {ctrl.purpose}\n"
            f"   Preparation: {ctrl.preparation}\n"
        )
    return "\n".join(lines)


def _generate_troubleshooting(panel: Panel) -> str:
    return (
        "Common Issues and Solutions:\n\n"
        "NO SIGNAL:\n"
        "- Verify antibody is validated for this application and species\n"
        "- Check antibody dilution — may need optimization\n"
        "- Verify antigen retrieval method (for FFPE)\n"
        "- Check secondary antibody species compatibility\n"
        "- Run positive control to confirm antibody activity\n\n"
        "HIGH BACKGROUND:\n"
        "- Increase blocking time or serum concentration\n"
        "- Dilute primary antibody further\n"
        "- Use pre-adsorbed secondary antibodies\n"
        "- Add Sudan Black B treatment for autofluorescence reduction\n\n"
        "BLEED-THROUGH BETWEEN CHANNELS:\n"
        "- Acquire channels sequentially, not simultaneously\n"
        "- Reduce primary antibody concentration\n"
        "- Apply spectral unmixing/compensation corrections\n"
        "- Consider redesigning panel with lower-spillover fluorophore pairs\n\n"
        "WEAK/UNEVEN STAINING:\n"
        "- Ensure sections don't dry during incubation\n"
        "- Use humidified chamber\n"
        "- Apply antibody solution evenly with sufficient volume\n"
        "- Check that fixation was adequate and uniform"
    )
