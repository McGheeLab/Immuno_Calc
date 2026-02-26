"""
core/alternative_plans.py — Alternative Panel Plan Generator.

Generates multiple alternative panel designs by varying fluorophore assignments,
direct vs. indirect labeling, and vendor alternatives. Compares plans side by side.
"""

from __future__ import annotations

from typing import Optional

from core.models import (
    AlternativePlan, CostEstimate, InstrumentProfile, Panel, PriceResult, Severity,
)
from core.panel_builder import compute_panel_spillover_score, suggest_assignments
from core.rules_engine import count_by_severity, validate_panel
from core.cost_calculator import calculate_panel_cost


def generate_alternatives(
    panel: Panel,
    profile: Optional[InstrumentProfile] = None,
    price_results: Optional[dict[str, PriceResult]] = None,
    max_plans: int = 3,
) -> list[AlternativePlan]:
    """
    Generate alternative panel designs for comparison.

    Strategy:
    1. Plan A: Current panel as-is
    2. Plans B, C: Different fluorophore/channel assignments from auto-suggest
    3. Each plan scored on cost, validation, and spectral overlap.
    """
    if profile is None:
        profile = panel.instrument_profile
    if profile is None:
        return [_create_plan(panel, "Plan A", profile, price_results)]

    plans: list[AlternativePlan] = []

    # Plan A: Current panel (or auto-assigned if no assignments yet)
    plans.append(_create_plan(panel, "Plan A", profile, price_results))

    # Generate alternative fluorophore assignments
    alt_panels = suggest_assignments(panel, profile, max_alternatives=max_plans)

    labels = ["Plan B", "Plan C", "Plan D", "Plan E"]
    for i, alt_panel in enumerate(alt_panels):
        if i >= len(labels):
            break
        # Skip if identical to Plan A
        if _panels_have_same_assignments(alt_panel, panel):
            continue
        plans.append(_create_plan(alt_panel, labels[i], profile, price_results))

    # Sort by validation score (higher = better), then by overlap (lower = better)
    plans.sort(key=lambda p: (-p.validation_score, p.overlap_score))

    # Re-label after sorting
    for i, plan in enumerate(plans):
        plan.plan_label = f"Plan {chr(65 + i)}"  # A, B, C...

    return plans[:max_plans]


def _create_plan(
    panel: Panel,
    label: str,
    profile: Optional[InstrumentProfile],
    price_results: Optional[dict[str, PriceResult]] = None,
) -> AlternativePlan:
    """Create an AlternativePlan from a panel with scoring."""
    # Validate
    validation_results = validate_panel(panel, profile) if profile else []
    severity_counts = count_by_severity(validation_results)

    # Calculate validation score (100 = perfect, deduct for issues)
    validation_score = 100.0
    validation_score -= severity_counts.get("ERROR", 0) * 25
    validation_score -= severity_counts.get("WARNING", 0) * 10
    validation_score -= severity_counts.get("INFO", 0) * 2
    validation_score = max(0, validation_score)

    # Calculate spillover score
    overlap_score = 0.0
    if profile:
        overlap_score = compute_panel_spillover_score(panel, profile)

    # Calculate cost
    cost = calculate_panel_cost(panel, price_results or {})

    # Determine procurement needs
    needs_procurement = [
        t.target_name for t in panel.targets
        if not t.is_in_house and t.primary
    ]

    return AlternativePlan(
        plan_label=label,
        panel=panel,
        total_cost=cost.grand_total,
        validation_score=validation_score,
        overlap_score=round(overlap_score * 100, 2),  # as percentage
        needs_procurement=needs_procurement,
        num_errors=severity_counts.get("ERROR", 0),
        num_warnings=severity_counts.get("WARNING", 0),
    )


def _panels_have_same_assignments(panel_a: Panel, panel_b: Panel) -> bool:
    """Check if two panels have identical fluorophore-channel assignments."""
    assign_a = {
        (t.target_name, t.fluorophore, t.channel) for t in panel_a.targets
    }
    assign_b = {
        (t.target_name, t.fluorophore, t.channel) for t in panel_b.targets
    }
    return assign_a == assign_b


def compare_plans(plans: list[AlternativePlan]) -> dict:
    """
    Generate a comparison summary of multiple plans.
    Returns a dict suitable for display in a table.
    """
    if not plans:
        return {}

    comparison = {
        "labels": [p.plan_label for p in plans],
        "total_costs": [f"${p.total_cost:.2f}" for p in plans],
        "validation_scores": [f"{p.validation_score:.0f}/100" for p in plans],
        "overlap_scores": [f"{p.overlap_score:.1f}%" for p in plans],
        "errors": [p.num_errors for p in plans],
        "warnings": [p.num_warnings for p in plans],
        "procurement_count": [len(p.needs_procurement) for p in plans],
    }

    # Highlight best in each category
    comparison["best_cost"] = min(range(len(plans)), key=lambda i: plans[i].total_cost)
    comparison["best_validation"] = max(range(len(plans)), key=lambda i: plans[i].validation_score)
    comparison["best_overlap"] = min(range(len(plans)), key=lambda i: plans[i].overlap_score)

    return comparison
