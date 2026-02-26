"""
core/rules_engine.py — Panel Validation Rules Engine.

Implements all 15 validation rules from the IF Panel Design specification.
Completely stateless and side-effect free — accepts a Panel, returns ValidationResults.
"""

from __future__ import annotations

from collections import Counter
from typing import Optional

import numpy as np

from core.models import (
    Channel, ExperimentType, InstrumentProfile, Panel, PanelTarget,
    SampleSpecies, Severity, ValidationResult,
)
from core.fluorophores import (
    brightness_score, compute_spillover, get_fluorophore, load_fluorophores,
)


# ─── Main Entry Point ────────────────────────────────────────────────────────

def validate_panel(
    panel: Panel,
    profile: Optional[InstrumentProfile] = None,
) -> list[ValidationResult]:
    """
    Run all validation rules against a panel.
    Returns results sorted: ERRORs first, then WARNINGs, then INFO.
    """
    if profile is None:
        profile = panel.instrument_profile

    if profile is None:
        return [ValidationResult(
            rule_id="SYS",
            severity=Severity.ERROR,
            message="No instrument profile defined. Cannot validate panel.",
            suggestion="Select an instrument profile in Settings.",
        )]

    results: list[ValidationResult] = []

    # Run each rule
    rule_checks = [
        _check_r01, _check_r02, _check_r03, _check_r04, _check_r05,
        _check_r06, _check_r07, _check_r08, _check_r09, _check_r10,
        _check_r11, _check_r12, _check_r13, _check_r14, _check_r15,
    ]

    for check_fn in rule_checks:
        try:
            result = check_fn(panel, profile)
            if result:
                if isinstance(result, list):
                    results.extend(result)
                else:
                    results.append(result)
        except Exception as e:
            results.append(ValidationResult(
                rule_id="SYS",
                severity=Severity.WARNING,
                message=f"Rule check failed: {e}",
            ))

    # Sort by severity
    severity_order = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}
    results.sort(key=lambda r: severity_order.get(r.severity, 3))

    return results


# ─── Individual Rule Implementations ─────────────────────────────────────────

def _check_r01(panel: Panel, profile: InstrumentProfile):
    """
    R01: Two primaries from same host species using indirect (species-specific) secondaries.
    Cannot use two mouse primaries with anti-mouse secondary simultaneously.
    """
    # Group indirect-labeled targets by host species
    indirect_targets: dict[str, list[str]] = {}
    for t in panel.targets:
        if t.primary and not t.primary.is_conjugated and t.secondary:
            host = t.primary.host_species.value
            indirect_targets.setdefault(host, []).append(t.target_name)

    results = []
    for host, targets in indirect_targets.items():
        if len(targets) > 1:
            results.append(ValidationResult(
                rule_id="R01",
                severity=Severity.ERROR,
                message=(
                    f"Multiple indirect primaries from same host species ({host}): "
                    f"{', '.join(targets)}. Species-specific secondaries will cross-react."
                ),
                affected_targets=targets,
                suggestion=(
                    "Switch one to a directly conjugated primary, use isotype-specific "
                    "secondaries, use Fab fragment secondaries, or stain sequentially."
                ),
            ))
    return results if results else None


def _check_r02(panel: Panel, profile: InstrumentProfile):
    """
    R02: Fluorophore not excitable by any available laser line (>20% excitation efficiency).
    """
    available_lasers = set(profile.available_laser_wavelengths)
    results = []

    for t in panel.targets:
        if not t.fluorophore:
            continue
        fluor = get_fluorophore(t.fluorophore)
        if not fluor:
            continue

        # Check if any available laser can excite this fluorophore
        excitable = any(laser in fluor.laser_lines for laser in available_lasers)
        if not excitable:
            # Check with tolerance
            excitable = any(
                any(abs(laser - ll) <= 20 for ll in fluor.laser_lines)
                for laser in available_lasers
            )

        if not excitable:
            results.append(ValidationResult(
                rule_id="R02",
                severity=Severity.ERROR,
                message=(
                    f"{t.fluorophore} assigned to {t.target_name} cannot be excited by "
                    f"any available laser ({', '.join(str(int(l)) for l in sorted(available_lasers))}nm)."
                ),
                affected_targets=[t.target_name],
                suggestion="Select a fluorophore compatible with available lasers.",
            ))
    return results if results else None


def _check_r03(panel: Panel, profile: InstrumentProfile):
    """
    R03: Fluorophore emission peak falls outside all available detector channels.
    """
    results = []
    for t in panel.targets:
        if not t.fluorophore:
            continue
        fluor = get_fluorophore(t.fluorophore)
        if not fluor:
            continue

        in_any_channel = False
        for ch in profile.channels:
            if ch.filter_low <= fluor.em_peak <= ch.filter_high:
                in_any_channel = True
                break

        if not in_any_channel:
            results.append(ValidationResult(
                rule_id="R03",
                severity=Severity.ERROR,
                message=(
                    f"{t.fluorophore} (em={fluor.em_peak}nm) assigned to {t.target_name} "
                    f"falls outside all available detector channels."
                ),
                affected_targets=[t.target_name],
                suggestion="Select a fluorophore whose emission matches an available channel.",
            ))
    return results if results else None


def _check_r04(panel: Panel, profile: InstrumentProfile):
    """
    R04: Same fluorophore assigned to two different targets.
    """
    fluor_targets: dict[str, list[str]] = {}
    for t in panel.targets:
        if t.fluorophore:
            fluor_targets.setdefault(t.fluorophore, []).append(t.target_name)

    results = []
    for fluor, targets in fluor_targets.items():
        if len(targets) > 1:
            results.append(ValidationResult(
                rule_id="R04",
                severity=Severity.ERROR,
                message=(
                    f"Fluorophore {fluor} assigned to multiple targets: "
                    f"{', '.join(targets)}. Cannot distinguish signals."
                ),
                affected_targets=targets,
                suggestion="Assign a different fluorophore to one of the targets.",
            ))
    return results if results else None


def _check_r05(panel: Panel, profile: InstrumentProfile):
    """
    R05: Tandem dye lot not validated for fixation protocol used.
    """
    if panel.experiment_type not in (ExperimentType.IHC_IF, ExperimentType.ICC):
        return None

    results = []
    for t in panel.targets:
        if not t.fluorophore:
            continue
        fluor = get_fluorophore(t.fluorophore)
        if fluor and fluor.is_tandem:
            results.append(ValidationResult(
                rule_id="R05",
                severity=Severity.WARNING,
                message=(
                    f"Tandem dye {t.fluorophore} used with {panel.experiment_type.value}. "
                    f"Some fixatives disrupt tandem structure — validate lot compatibility."
                ),
                affected_targets=[t.target_name],
                suggestion="Test tandem dye with your fixation protocol or use a non-tandem alternative.",
            ))
    return results if results else None


def _check_r06(panel: Panel, profile: InstrumentProfile):
    """
    R06: Polymer dyes from different manufacturers in same panel.
    """
    polymer_families: dict[str, list[str]] = {}
    for t in panel.targets:
        if not t.fluorophore:
            continue
        fluor = get_fluorophore(t.fluorophore)
        if fluor and fluor.is_polymer and fluor.manufacturer_family:
            polymer_families.setdefault(fluor.manufacturer_family, []).append(t.target_name)

    if len(polymer_families) > 1:
        all_targets = [t for targets in polymer_families.values() for t in targets]
        families = list(polymer_families.keys())
        return ValidationResult(
            rule_id="R06",
            severity=Severity.WARNING,
            message=(
                f"Polymer dyes from different manufacturers in same panel: "
                f"{', '.join(families)}. Polymer-polymer interactions can cause "
                f"unpredictable FRET and false signals."
            ),
            affected_targets=all_targets,
            suggestion=(
                "Use dyes from the same polymer family, or add Brilliant Stain Buffer / "
                "True-Stain Monocyte Blocker."
            ),
        )
    return None


def _check_r07(panel: Panel, profile: InstrumentProfile):
    """
    R07: Spillover between two channels exceeds 15%.
    """
    # Get all assigned fluorophores and their channels
    assignments = []
    for t in panel.targets:
        if t.fluorophore and t.channel:
            fluor = get_fluorophore(t.fluorophore)
            ch = _find_channel(profile, t.channel)
            if fluor and ch:
                assignments.append((t, fluor, ch))

    if len(assignments) < 2:
        return None

    results = []
    for i, (t1, f1, c1) in enumerate(assignments):
        for j, (t2, f2, c2) in enumerate(assignments):
            if i >= j:
                continue
            # Check spillover of f1 into c2
            spill_12 = compute_spillover(f1, c2)
            spill_21 = compute_spillover(f2, c1)

            max_spill = max(spill_12, spill_21) * 100  # Convert to percentage

            if max_spill > 15:
                results.append(ValidationResult(
                    rule_id="R07",
                    severity=Severity.WARNING,
                    message=(
                        f"High spectral spillover ({max_spill:.1f}%) between "
                        f"{t1.fluorophore} ({t1.target_name}) and "
                        f"{t2.fluorophore} ({t2.target_name})."
                    ),
                    affected_targets=[t1.target_name, t2.target_name],
                    suggestion="Swap fluorophore assignments or choose a lower-spillover pair.",
                ))
    return results if results else None


def _check_r08(panel: Panel, profile: InstrumentProfile):
    """
    R08: Dim fluorophore assigned to low-abundance target.
    (Brightness rule: brightest fluorophores should go to rarest targets.)
    """
    from core.models import ExpressionLevel

    results = []
    for t in panel.targets:
        if not t.fluorophore:
            continue
        fluor = get_fluorophore(t.fluorophore)
        if not fluor:
            continue

        bs = brightness_score(fluor)

        # Rare/low targets should have brightness >= 4
        if t.expression_level in (ExpressionLevel.RARE, ExpressionLevel.LOW) and bs < 4:
            results.append(ValidationResult(
                rule_id="R08",
                severity=Severity.WARNING,
                message=(
                    f"Dim fluorophore {t.fluorophore} (brightness: {fluor.brightness}) "
                    f"assigned to {t.expression_level.value}-abundance target {t.target_name}."
                ),
                affected_targets=[t.target_name],
                suggestion=(
                    f"Assign a brighter fluorophore (PE, BV421, AF647) to {t.target_name} "
                    f"for better signal-to-noise."
                ),
            ))

        # High-abundance targets with brightest fluorophores is wasteful
        if t.expression_level == ExpressionLevel.HIGH and bs >= 5:
            results.append(ValidationResult(
                rule_id="R08",
                severity=Severity.INFO,
                message=(
                    f"Very bright fluorophore {t.fluorophore} assigned to high-abundance "
                    f"target {t.target_name}. Consider reserving bright fluorophores for "
                    f"rarer targets."
                ),
                affected_targets=[t.target_name],
                suggestion="Swap with a dimmer fluorophore and assign this bright one to a rare target.",
            ))

    return results if results else None


def _check_r09(panel: Panel, profile: InstrumentProfile):
    """
    R09: Primary antibody not validated for experiment application type.
    """
    app_map = {
        ExperimentType.ICC: ["ICC", "IF"],
        ExperimentType.IHC_IF: ["IHC", "IHC-IF", "IF"],
        ExperimentType.FLOW_CYTOMETRY: ["FC", "FACS", "Flow"],
        ExperimentType.LIVE_IF: ["IF", "Live"],
        ExperimentType.MULTIPLEX_IF: ["IF", "mIF", "IHC"],
        ExperimentType.PLA: ["PLA", "IF", "ICC"],
    }
    required_apps = app_map.get(panel.experiment_type, [])

    results = []
    for t in panel.targets:
        if not t.primary or not t.primary.validated_applications:
            continue

        has_valid_app = any(
            any(req.lower() in app.lower() for req in required_apps)
            for app in t.primary.validated_applications
        )
        if not has_valid_app:
            results.append(ValidationResult(
                rule_id="R09",
                severity=Severity.WARNING,
                message=(
                    f"Primary antibody for {t.target_name} ({t.primary.catalog_no}) "
                    f"is not validated for {panel.experiment_type.value}. "
                    f"Validated for: {', '.join(t.primary.validated_applications)}."
                ),
                affected_targets=[t.target_name],
                suggestion="Use a validated antibody for this application or validate empirically.",
            ))
    return results if results else None


def _check_r10(panel: Panel, profile: InstrumentProfile):
    """
    R10: Secondary antibody host same as sample species.
    """
    sample_species_map = {
        SampleSpecies.HUMAN: "Human",
        SampleSpecies.MOUSE: "Mouse",
        SampleSpecies.RAT: "Rat",
        SampleSpecies.RABBIT: "Rabbit",
        SampleSpecies.ZEBRAFISH: "Zebrafish",
    }
    sample_sp = sample_species_map.get(panel.sample_species, "")

    results = []
    for t in panel.targets:
        if not t.secondary:
            continue
        sec_host = t.secondary.host_species.value
        if sec_host.lower() == sample_sp.lower():
            results.append(ValidationResult(
                rule_id="R10",
                severity=Severity.ERROR,
                message=(
                    f"Secondary antibody for {t.target_name} is raised in {sec_host}, "
                    f"same as sample species ({sample_sp}). Will cause massive background."
                ),
                affected_targets=[t.target_name],
                suggestion="Choose a secondary raised in a different species (e.g., donkey, goat).",
            ))
    return results if results else None


def _check_r11(panel: Panel, profile: InstrumentProfile):
    """
    R11: Live-cell experiment uses fixation-only antibody.
    """
    if panel.experiment_type != ExperimentType.LIVE_IF:
        return None

    results = []
    for t in panel.targets:
        if not t.primary:
            continue
        apps = [a.lower() for a in t.primary.validated_applications]
        if apps and not any(kw in " ".join(apps) for kw in ["live", "fc", "facs", "flow"]):
            results.append(ValidationResult(
                rule_id="R11",
                severity=Severity.WARNING,
                message=(
                    f"Primary for {t.target_name} may not be validated for live-cell use. "
                    f"Validated apps: {', '.join(t.primary.validated_applications)}."
                ),
                affected_targets=[t.target_name],
                suggestion="Check for a live-cell validated alternative.",
            ))
    return results if results else None


def _check_r12(panel: Panel, profile: InstrumentProfile):
    """
    R12: DAPI used in live-cell experiment.
    """
    if panel.experiment_type != ExperimentType.LIVE_IF:
        return None

    for t in panel.targets:
        if t.fluorophore and t.fluorophore.upper() == "DAPI":
            return ValidationResult(
                rule_id="R12",
                severity=Severity.WARNING,
                message="DAPI is used in a live-cell experiment. DAPI requires membrane permeabilization.",
                affected_targets=[t.target_name],
                suggestion="Use Hoechst 33342 instead — it is cell-permeable and less toxic.",
            )
    return None


def _check_r13(panel: Panel, profile: InstrumentProfile):
    """
    R13: More primaries specified than available channels.
    """
    num_targets = len([t for t in panel.targets if t.target_name])
    num_channels = len(profile.channels)

    # Subtract 1 channel for nuclear stain (DAPI) in most experiments
    usable_channels = num_channels - 1 if panel.experiment_type != ExperimentType.FLOW_CYTOMETRY else num_channels

    if num_targets > usable_channels:
        return ValidationResult(
            rule_id="R13",
            severity=Severity.ERROR,
            message=(
                f"Panel has {num_targets} targets but only {usable_channels} usable channels "
                f"(instrument has {num_channels} total, 1 reserved for nuclear stain)."
            ),
            affected_targets=[t.target_name for t in panel.targets],
            suggestion="Reduce panel size, add instrument channels, or split into sequential panels.",
        )
    return None


def _check_r14(panel: Panel, profile: InstrumentProfile):
    """
    R14: No viability dye included in flow cytometry panel.
    """
    if panel.experiment_type != ExperimentType.FLOW_CYTOMETRY:
        return None

    viability_dyes = {"7-AAD", "DRAQ7", "PI", "Propidium Iodide", "Zombie", "LIVE/DEAD"}
    has_viability = any(
        t.fluorophore and any(vd.lower() in t.fluorophore.lower() for vd in viability_dyes)
        for t in panel.targets
    )

    if not has_viability:
        return ValidationResult(
            rule_id="R14",
            severity=Severity.WARNING,
            message="No viability dye detected in flow cytometry panel.",
            suggestion="Add a viability dye (7-AAD, DRAQ7, or Zombie dye) to exclude dead cells.",
        )
    return None


def _check_r15(panel: Panel, profile: InstrumentProfile):
    """
    R15: Isotype control not defined for each primary antibody.
    """
    targets_with_primary = [t for t in panel.targets if t.primary]
    if not targets_with_primary:
        return None

    has_isotype_controls = any(
        c.control_type.lower().startswith("isotype") for c in panel.controls
    )

    if not has_isotype_controls and len(targets_with_primary) > 0:
        return ValidationResult(
            rule_id="R15",
            severity=Severity.INFO,
            message="No isotype controls defined. Isotype controls validate gating specificity.",
            affected_targets=[t.target_name for t in targets_with_primary],
            suggestion="Add matched isotype controls for each primary antibody.",
        )
    return None


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _find_channel(profile: InstrumentProfile, channel_name: str) -> Optional[Channel]:
    """Find a channel by name in the instrument profile."""
    for ch in profile.channels:
        if ch.name == channel_name:
            return ch
    return None


def count_by_severity(results: list[ValidationResult]) -> dict[str, int]:
    """Count validation results by severity level."""
    counts = {"ERROR": 0, "WARNING": 0, "INFO": 0}
    for r in results:
        counts[r.severity.value] = counts.get(r.severity.value, 0) + 1
    return counts


def has_errors(results: list[ValidationResult]) -> bool:
    """Check if any validation result is an ERROR."""
    return any(r.severity == Severity.ERROR for r in results)
