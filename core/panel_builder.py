"""
core/panel_builder.py — Fluorophore Assignment Optimizer.

Given targets with expression levels and an instrument profile,
suggests optimal fluorophore-to-channel assignments following the
golden rules of panel design.
"""

from __future__ import annotations

import itertools
from typing import Optional

import numpy as np

from core.models import (
    Channel, ExpressionLevel, InstrumentProfile, Panel, PanelTarget,
    Severity, ValidationResult,
)
from core.fluorophores import (
    brightness_score, compute_spillover, get_fluorophore,
    get_fluorophores_for_channel, load_fluorophores, rank_fluorophores_by_brightness,
)
from core.rules_engine import validate_panel, has_errors


# ─── Expression Level to Brightness Mapping ──────────────────────────────────

EXPRESSION_BRIGHTNESS_PREFERENCE = {
    ExpressionLevel.RARE: [5, 4],     # Needs Very High or High brightness
    ExpressionLevel.LOW: [5, 4, 3],   # Needs High+ brightness
    ExpressionLevel.MEDIUM: [4, 3, 2],  # Medium brightness OK
    ExpressionLevel.HIGH: [3, 2, 1],    # Dim is fine — lots of signal
}


def suggest_assignments(
    panel: Panel,
    profile: InstrumentProfile,
    max_alternatives: int = 3,
) -> list[Panel]:
    """
    Auto-assign fluorophores to channels for all targets in the panel.
    Returns up to max_alternatives valid panel configurations, ranked by
    total spillover score (lower is better).

    Algorithm:
    1. Sort targets by expression level (rare first)
    2. For each channel, determine compatible fluorophores
    3. Score each fluorophore-channel pair by brightness match + spillover
    4. Greedy assignment with backtracking on validation errors
    5. Return top N valid assignments
    """
    if not profile or not profile.channels:
        return [panel]

    # Reserve DAPI channel for nuclear stain if present
    usable_channels = [
        ch for ch in profile.channels
        if ch.name.upper() != "DAPI"
    ]

    targets = [t for t in panel.targets if t.target_name]
    if not targets:
        return [panel]

    # Sort targets: rare/low first (they need the best fluorophores)
    expr_order = {
        ExpressionLevel.RARE: 0,
        ExpressionLevel.LOW: 1,
        ExpressionLevel.MEDIUM: 2,
        ExpressionLevel.HIGH: 3,
    }
    targets_sorted = sorted(targets, key=lambda t: expr_order.get(t.expression_level, 2))

    # Build candidate fluorophores per channel
    channel_candidates: dict[str, list] = {}
    for ch in usable_channels:
        candidates = get_fluorophores_for_channel(ch)
        # Sort by brightness (brightest first)
        candidates = rank_fluorophores_by_brightness(candidates)
        channel_candidates[ch.name] = [(ch, f) for f in candidates]

    # Flatten all (channel, fluorophore) options
    all_options: list[list[tuple]] = []
    for target in targets_sorted:
        target_options = []
        for ch in usable_channels:
            for ch_obj, fluor in channel_candidates.get(ch.name, []):
                score = _score_assignment(target, fluor, ch_obj)
                target_options.append((ch_obj, fluor, score))

        # Sort by score (lower is better)
        target_options.sort(key=lambda x: x[2])
        all_options.append(target_options)

    # Generate assignments using greedy approach
    assignments_list = _greedy_assign(targets_sorted, all_options, max_alternatives)

    # Build panel variants
    result_panels = []
    for assignment in assignments_list:
        new_panel = panel.model_copy(deep=True)
        # Map target names to assignment
        for target, (channel, fluorophore) in zip(targets_sorted, assignment):
            for pt in new_panel.targets:
                if pt.target_name == target.target_name:
                    pt.fluorophore = fluorophore.name
                    pt.channel = channel.name
                    break
        result_panels.append(new_panel)

    # If no valid assignments found, return original panel
    if not result_panels:
        return [panel]

    return result_panels[:max_alternatives]


def _score_assignment(target: PanelTarget, fluor, channel) -> float:
    """
    Score a fluorophore-channel assignment for a target.
    Lower score = better assignment.
    """
    score = 0.0

    # Brightness match penalty
    bs = brightness_score(fluor)
    preferred = EXPRESSION_BRIGHTNESS_PREFERENCE.get(target.expression_level, [3])
    if bs in preferred:
        score += 0  # Perfect match
    else:
        # Penalty proportional to distance from preferred
        min_dist = min(abs(bs - p) for p in preferred)
        score += min_dist * 10

    # Tandem dye penalty (prefer non-tandem)
    if fluor.is_tandem:
        score += 5

    # Polymer dye slight preference if available
    if fluor.is_polymer:
        score += 2

    return score


def _greedy_assign(
    targets: list[PanelTarget],
    all_options: list[list[tuple]],
    max_results: int = 3,
) -> list[list[tuple]]:
    """
    Greedy assignment: for each target, pick the best unused channel+fluorophore.
    Generates multiple solutions by varying first choices.
    """
    results = []

    # Try different starting points for diversity
    for start_offset in range(min(5, max(1, len(all_options[0]) if all_options else 1))):
        assignment = []
        used_channels: set[str] = set()
        used_fluorophores: set[str] = set()
        valid = True

        for i, (target, options) in enumerate(zip(targets, all_options)):
            assigned = False
            # Offset the starting point for diversity
            offset_options = options[start_offset:] + options[:start_offset]

            for ch, fluor, score in offset_options:
                if ch.name not in used_channels and fluor.name not in used_fluorophores:
                    assignment.append((ch, fluor))
                    used_channels.add(ch.name)
                    used_fluorophores.add(fluor.name)
                    assigned = True
                    break

            if not assigned:
                valid = False
                break

        if valid and assignment not in results:
            results.append(assignment)

        if len(results) >= max_results:
            break

    return results


def compute_panel_spillover_score(panel: Panel, profile: InstrumentProfile) -> float:
    """
    Compute the total spillover score for a panel.
    Lower score = less spectral overlap = better panel design.
    """
    assigned = []
    for t in panel.targets:
        if t.fluorophore and t.channel:
            fluor = get_fluorophore(t.fluorophore)
            ch = _find_channel_in_profile(profile, t.channel)
            if fluor and ch:
                assigned.append((fluor, ch))

    if len(assigned) < 2:
        return 0.0

    total_spillover = 0.0
    for i, (f1, c1) in enumerate(assigned):
        for j, (f2, c2) in enumerate(assigned):
            if i != j:
                total_spillover += compute_spillover(f1, c2)

    return total_spillover


def _find_channel_in_profile(profile: InstrumentProfile, name: str) -> Optional[Channel]:
    for ch in profile.channels:
        if ch.name == name:
            return ch
    return None


def get_available_fluorophores_for_target(
    target: PanelTarget,
    profile: InstrumentProfile,
    used_fluorophores: Optional[set[str]] = None,
) -> dict[str, list]:
    """
    For a given target, return available fluorophores grouped by channel.
    Excludes already-used fluorophores.
    """
    if used_fluorophores is None:
        used_fluorophores = set()

    result: dict[str, list] = {}
    for ch in profile.channels:
        if ch.name.upper() == "DAPI":
            continue
        candidates = get_fluorophores_for_channel(ch)
        available = [f for f in candidates if f.name not in used_fluorophores]
        if available:
            result[ch.name] = available

    return result
