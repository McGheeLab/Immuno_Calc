"""
core/cost_calculator.py — Volume & Cost Calculation Engine.

Calculates antibody volumes needed, units to purchase, and total costs
for a panel design.
"""

from __future__ import annotations

import math
import re
from typing import Optional

from core.models import (
    CostEstimate, CostLineItem, ExperimentType, Panel, PanelTarget,
    PriceResult, InventoryItem,
)


# ─── Default Parameters ──────────────────────────────────────────────────────

DEFAULT_VOLUME_PER_SAMPLE_UL = {
    ExperimentType.ICC: 100,          # 100 µL per well/coverslip
    ExperimentType.IHC_IF: 100,       # 100 µL per tissue section
    ExperimentType.FLOW_CYTOMETRY: 50, # 50 µL per tube
    ExperimentType.LIVE_IF: 100,
    ExperimentType.MULTIPLEX_IF: 100,
    ExperimentType.PLA: 50,
}

OVERAGE_FACTOR = 1.2  # 20% overage buffer


def parse_dilution(dilution_str: str) -> Optional[float]:
    """
    Parse a dilution string like '1:500' or '1/200' into the numeric dilution factor.
    Returns 500.0 for '1:500'.
    """
    if not dilution_str:
        return None
    dilution_str = dilution_str.strip()

    # Match patterns like 1:500, 1/500
    match = re.match(r"1\s*[:/]\s*(\d+)", dilution_str)
    if match:
        return float(match.group(1))

    # Try direct numeric (e.g., "500" meaning 1:500)
    try:
        val = float(dilution_str)
        if val > 1:
            return val
        elif val > 0:
            return 1.0 / val
    except ValueError:
        pass

    return None


def parse_package_size(size_str: str) -> Optional[float]:
    """
    Parse a package size string like '100 µg', '0.1 mg', '500 tests'
    into volume in µL (assuming typical antibody concentration of 1 mg/mL).
    Returns µL equivalent.
    """
    if not size_str:
        return None
    size_str = size_str.strip().lower()

    # Match patterns like "100 µg", "100ug", "0.1 mg"
    match = re.match(r"([\d.]+)\s*(µg|ug|mg|ml|µl|ul|tests?)", size_str)
    if not match:
        return None

    value = float(match.group(1))
    unit = match.group(2)

    if unit in ("µg", "ug"):
        return value  # Assume 1 mg/mL = 1 µg/µL
    elif unit == "mg":
        return value * 1000  # 1 mg = 1000 µL at 1 mg/mL
    elif unit in ("ml",):
        return value * 1000
    elif unit in ("µl", "ul"):
        return value
    elif unit.startswith("test"):
        return value * 5  # Rough estimate: 5 µL per test

    return None


# ─── Volume Calculations ─────────────────────────────────────────────────────

def calculate_volume_needed(
    num_samples: int,
    dilution_factor: float,
    volume_per_sample_ul: float = 100,
    overage: float = OVERAGE_FACTOR,
) -> float:
    """
    Calculate total antibody volume needed in µL.
    Formula: (µL_per_sample / dilution_factor) × num_samples × overage
    """
    if dilution_factor <= 0:
        return 0
    volume = (volume_per_sample_ul / dilution_factor) * num_samples * overage
    return round(volume, 2)


def calculate_units_to_order(
    volume_needed_ul: float,
    package_size_ul: float,
) -> int:
    """
    Calculate how many units to order. Rounds up to nearest integer.
    """
    if package_size_ul <= 0 or volume_needed_ul <= 0:
        return 0
    return math.ceil(volume_needed_ul / package_size_ul)


# ─── Full Cost Estimate ──────────────────────────────────────────────────────

def calculate_panel_cost(
    panel: Panel,
    price_results: Optional[dict[str, PriceResult]] = None,
    inventory_items: Optional[dict[str, InventoryItem]] = None,
) -> CostEstimate:
    """
    Calculate complete cost estimate for a panel.

    Args:
        panel: The validated panel design
        price_results: Dict of target_name -> PriceResult for items to purchase
        inventory_items: Dict of target_name -> InventoryItem for in-house items
    """
    if price_results is None:
        price_results = {}
    if inventory_items is None:
        inventory_items = {}

    volume_per_sample = DEFAULT_VOLUME_PER_SAMPLE_UL.get(
        panel.experiment_type, 100
    )

    in_house_items: list[CostLineItem] = []
    to_purchase: list[CostLineItem] = []

    for target in panel.targets:
        if not target.primary:
            continue

        # Determine dilution
        dilution = None
        if target.target_name in inventory_items:
            inv = inventory_items[target.target_name]
            dil_str = inv.working_dilution_if or inv.working_dilution_fc or inv.working_dilution_ihc
            dilution = parse_dilution(dil_str)
        if dilution is None:
            dilution = parse_dilution(target.primary.dilution)
        if dilution is None:
            dilution = 200  # Default 1:200

        vol_needed = calculate_volume_needed(
            panel.num_samples, dilution, volume_per_sample
        )

        if target.is_in_house and target.target_name in inventory_items:
            inv = inventory_items[target.target_name]
            in_house_items.append(CostLineItem(
                item_name=f"Anti-{target.target_name} ({inv.vendor})",
                catalog_no=inv.catalog_no,
                vendor=inv.vendor,
                unit_price=0,
                units_needed=1,
                total_price=0,
                is_in_house=True,
                notes=f"In-house. Volume needed: {vol_needed:.1f} µL. Stock: {inv.total_volume_ul:.0f} µL",
            ))
        elif target.target_name in price_results:
            pr = price_results[target.target_name]
            pkg_ul = parse_package_size(pr.package_size) or 100
            units = calculate_units_to_order(vol_needed, pkg_ul)
            total = pr.price * units

            to_purchase.append(CostLineItem(
                item_name=f"Anti-{target.target_name} ({pr.vendor})",
                catalog_no=pr.catalog_no,
                vendor=pr.vendor,
                unit_price=pr.price,
                units_needed=units,
                total_price=total,
                is_in_house=False,
                notes=f"Volume needed: {vol_needed:.1f} µL. Package: {pr.package_size}",
            ))
        else:
            to_purchase.append(CostLineItem(
                item_name=f"Anti-{target.target_name} (price TBD)",
                notes=f"Volume needed: {vol_needed:.1f} µL. Price not yet determined.",
            ))

    # Add secondary antibody costs
    secondary_targets = [t for t in panel.targets if t.secondary]
    for t in secondary_targets:
        sec = t.secondary
        to_purchase.append(CostLineItem(
            item_name=f"Secondary: {sec.display_name}",
            catalog_no=sec.catalog_no,
            vendor=sec.vendor,
            unit_price=0,
            notes="Secondary antibody — price TBD if not in-house",
        ))

    subtotal_antibodies = sum(item.total_price for item in to_purchase)

    return CostEstimate(
        in_house_items=in_house_items,
        to_purchase=to_purchase,
        subtotal_antibodies=subtotal_antibodies,
        subtotal_consumables=0,  # Can be extended with consumable estimates
        imaging_cost=0,
        grand_total=subtotal_antibodies,
    )
