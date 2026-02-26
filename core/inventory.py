"""
core/inventory.py — Inventory CRUD operations.

All functions accept a SQLAlchemy session and return Pydantic models.
Completely GUI-agnostic.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.database import (
    InventoryItemORM, UsageLogORM, SavedPanelORM,
    inventory_item_to_orm, orm_to_inventory_item,
)
from core.models import InventoryItem, UsageLogEntry, Panel


# ─── CRUD Operations ────────────────────────────────────────────────────────

def add_item(session: Session, item: InventoryItem) -> InventoryItem:
    """Add a new antibody to inventory."""
    if not item.id:
        item.id = str(uuid.uuid4())
    orm_obj = inventory_item_to_orm(item)
    session.add(orm_obj)
    session.flush()
    return item


def get_item(session: Session, item_id: str) -> Optional[InventoryItem]:
    """Get a single inventory item by ID."""
    orm_obj = session.query(InventoryItemORM).filter_by(id=item_id).first()
    if orm_obj is None:
        return None
    return orm_to_inventory_item(orm_obj)


def update_item(session: Session, item: InventoryItem) -> bool:
    """Update an existing inventory item."""
    orm_obj = session.query(InventoryItemORM).filter_by(id=item.id).first()
    if orm_obj is None:
        return False

    updated = inventory_item_to_orm(item)
    for col in InventoryItemORM.__table__.columns:
        if col.name != "id":
            setattr(orm_obj, col.name, getattr(updated, col.name))
    session.flush()
    return True


def delete_item(session: Session, item_id: str) -> bool:
    """Delete an inventory item."""
    orm_obj = session.query(InventoryItemORM).filter_by(id=item_id).first()
    if orm_obj is None:
        return False
    session.delete(orm_obj)
    session.flush()
    return True


def get_all_items(session: Session) -> list[InventoryItem]:
    """Get all inventory items."""
    rows = session.query(InventoryItemORM).order_by(InventoryItemORM.target).all()
    return [orm_to_inventory_item(r) for r in rows]


# ─── Filtered Queries ───────────────────────────────────────────────────────

def get_matching_antibodies(
    session: Session,
    target: str = "",
    species: str = "",
    application: str = "",
    host_species: str = "",
    conjugate: str = "",
    vendor: str = "",
) -> list[InventoryItem]:
    """
    Find inventory items matching panel builder criteria.
    All filters are optional — empty string means no filter.
    """
    query = session.query(InventoryItemORM)

    if target:
        query = query.filter(
            InventoryItemORM.target.ilike(f"%{target}%")
        )
    if host_species:
        query = query.filter(InventoryItemORM.host_species == host_species)
    if conjugate:
        query = query.filter(
            InventoryItemORM.conjugate.ilike(f"%{conjugate}%")
        )
    if vendor:
        query = query.filter(
            InventoryItemORM.vendor.ilike(f"%{vendor}%")
        )

    items = [orm_to_inventory_item(r) for r in query.all()]

    # Post-filter on JSON fields (reactivity, validated_applications)
    if species:
        species_lower = species.lower()
        items = [
            it for it in items
            if any(species_lower in s.lower() for s in it.reactivity)
        ]
    if application:
        app_lower = application.lower()
        items = [
            it for it in items
            if any(app_lower in a.lower() for a in it.validated_applications)
        ]

    # Sort: items with working dilutions first, then by most recent
    items.sort(key=lambda x: (
        bool(x.working_dilution_if or x.working_dilution_fc or x.working_dilution_ihc),
        x.date_received or date.min,
    ), reverse=True)

    return items


def search_items(session: Session, query_str: str) -> list[InventoryItem]:
    """Free-text search across target, vendor, antibody_name, catalog_no."""
    q = session.query(InventoryItemORM).filter(
        or_(
            InventoryItemORM.target.ilike(f"%{query_str}%"),
            InventoryItemORM.antibody_name.ilike(f"%{query_str}%"),
            InventoryItemORM.vendor.ilike(f"%{query_str}%"),
            InventoryItemORM.catalog_no.ilike(f"%{query_str}%"),
            InventoryItemORM.clone_name.ilike(f"%{query_str}%"),
        )
    )
    return [orm_to_inventory_item(r) for r in q.all()]


# ─── Alert Queries ──────────────────────────────────────────────────────────

def get_low_stock(session: Session, threshold_ul: float = 10.0) -> list[InventoryItem]:
    """Get items below the low-stock volume threshold."""
    items = get_all_items(session)
    return [it for it in items if it.is_low_stock or it.total_volume_ul < threshold_ul]


def get_expiring(session: Session, days: int = 90) -> list[InventoryItem]:
    """Get items expiring within the specified number of days."""
    items = get_all_items(session)
    return [it for it in items if it.is_expiring_soon or it.is_expired]


def get_alerts(session: Session) -> dict:
    """Get a summary of all alerts."""
    items = get_all_items(session)
    expired = [it for it in items if it.is_expired]
    expiring = [it for it in items if it.is_expiring_soon and not it.is_expired]
    low_stock = [it for it in items if it.is_low_stock]

    return {
        "expired": expired,
        "expiring_soon": expiring,
        "low_stock": low_stock,
        "total_alerts": len(expired) + len(expiring) + len(low_stock),
    }


def get_inventory_summary(session: Session) -> dict:
    """Summary statistics for the dashboard."""
    items = get_all_items(session)
    alerts = get_alerts(session)

    return {
        "total_antibodies": len(items),
        "low_stock_count": len(alerts["low_stock"]),
        "expiring_soon_count": len(alerts["expiring_soon"]),
        "expired_count": len(alerts["expired"]),
        "vendors": list(set(it.vendor for it in items if it.vendor)),
        "targets": list(set(it.target for it in items if it.target)),
    }


# ─── Usage Logging ──────────────────────────────────────────────────────────

def log_usage(session: Session, entry: UsageLogEntry) -> UsageLogEntry:
    """Record antibody usage and deduct volume from inventory."""
    orm_obj = UsageLogORM(
        id=entry.id,
        inventory_item_id=entry.inventory_item_id,
        experiment_name=entry.experiment_name,
        panel_id=entry.panel_id,
        volume_used_ul=entry.volume_used_ul,
        date_used=entry.date_used.isoformat(),
        notes=entry.notes,
    )
    session.add(orm_obj)

    # Deduct volume from inventory
    inv_orm = session.query(InventoryItemORM).filter_by(
        id=entry.inventory_item_id
    ).first()
    if inv_orm:
        inv_orm.total_volume_ul = max(0, (inv_orm.total_volume_ul or 0) - entry.volume_used_ul)

    session.flush()
    return entry


def get_usage_log(session: Session, item_id: str) -> list[UsageLogEntry]:
    """Get usage history for an inventory item."""
    rows = session.query(UsageLogORM).filter_by(
        inventory_item_id=item_id
    ).order_by(UsageLogORM.date_used.desc()).all()

    return [
        UsageLogEntry(
            id=r.id,
            inventory_item_id=r.inventory_item_id,
            experiment_name=r.experiment_name or "",
            panel_id=r.panel_id or "",
            volume_used_ul=r.volume_used_ul or 0,
            date_used=date.fromisoformat(r.date_used) if r.date_used else date.today(),
            notes=r.notes or "",
        )
        for r in rows
    ]


def increment_freeze_thaw(session: Session, item_id: str) -> Optional[int]:
    """Increment freeze-thaw counter. Returns new count or None if not found."""
    orm_obj = session.query(InventoryItemORM).filter_by(id=item_id).first()
    if orm_obj is None:
        return None
    orm_obj.freeze_thaw_count = (orm_obj.freeze_thaw_count or 0) + 1
    session.flush()
    return orm_obj.freeze_thaw_count


# ─── CSV Import/Export ───────────────────────────────────────────────────────

def export_csv(session: Session) -> str:
    """Export full inventory as CSV string."""
    items = get_all_items(session)
    if not items:
        return ""

    output = io.StringIO()
    fieldnames = [
        "id", "antibody_name", "target", "catalog_no", "lot_no", "vendor",
        "host_species", "isotype", "clonality", "clone_name", "conjugate",
        "validated_applications", "reactivity", "concentration", "total_volume_ul",
        "aliquot_size_ul", "storage_temp", "expiry_date", "date_received",
        "location", "working_dilution_if", "working_dilution_fc",
        "working_dilution_ihc", "freeze_thaw_count", "purchase_price",
        "quantity_per_unit", "notes",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for item in items:
        row = {
            "id": item.id,
            "antibody_name": item.antibody_name,
            "target": item.target,
            "catalog_no": item.catalog_no,
            "lot_no": item.lot_no,
            "vendor": item.vendor,
            "host_species": item.host_species.value,
            "isotype": item.isotype,
            "clonality": item.clonality.value,
            "clone_name": item.clone_name,
            "conjugate": item.conjugate,
            "validated_applications": ";".join(item.validated_applications),
            "reactivity": ";".join(item.reactivity),
            "concentration": item.concentration,
            "total_volume_ul": item.total_volume_ul,
            "aliquot_size_ul": item.aliquot_size_ul,
            "storage_temp": item.storage_temp.value,
            "expiry_date": item.expiry_date.isoformat() if item.expiry_date else "",
            "date_received": item.date_received.isoformat() if item.date_received else "",
            "location": item.location,
            "working_dilution_if": item.working_dilution_if,
            "working_dilution_fc": item.working_dilution_fc,
            "working_dilution_ihc": item.working_dilution_ihc,
            "freeze_thaw_count": item.freeze_thaw_count,
            "purchase_price": item.purchase_price,
            "quantity_per_unit": item.quantity_per_unit,
            "notes": item.notes,
        }
        writer.writerow(row)

    return output.getvalue()


def import_csv(session: Session, csv_text: str) -> tuple[int, list[str]]:
    """
    Import antibodies from CSV text. Returns (success_count, error_messages).
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    success = 0
    errors = []

    for i, row in enumerate(reader, start=2):
        try:
            item = InventoryItem(
                id=row.get("id") or str(uuid.uuid4()),
                antibody_name=row.get("antibody_name", ""),
                target=row.get("target", ""),
                catalog_no=row.get("catalog_no", ""),
                lot_no=row.get("lot_no", ""),
                vendor=row.get("vendor", ""),
                host_species=row.get("host_species", "Rabbit"),
                isotype=row.get("isotype", "IgG"),
                clonality=row.get("clonality", "Monoclonal"),
                clone_name=row.get("clone_name", ""),
                conjugate=row.get("conjugate", "Unconjugated"),
                validated_applications=row.get("validated_applications", "").split(";") if row.get("validated_applications") else [],
                reactivity=row.get("reactivity", "").split(";") if row.get("reactivity") else [],
                concentration=float(row.get("concentration", 0) or 0),
                total_volume_ul=float(row.get("total_volume_ul", 0) or 0),
                aliquot_size_ul=float(row.get("aliquot_size_ul", 0) or 0),
                storage_temp=row.get("storage_temp", "-20°C"),
                expiry_date=date.fromisoformat(row["expiry_date"]) if row.get("expiry_date") else None,
                date_received=date.fromisoformat(row["date_received"]) if row.get("date_received") else None,
                location=row.get("location", ""),
                working_dilution_if=row.get("working_dilution_if", ""),
                working_dilution_fc=row.get("working_dilution_fc", ""),
                working_dilution_ihc=row.get("working_dilution_ihc", ""),
                freeze_thaw_count=int(row.get("freeze_thaw_count", 0) or 0),
                purchase_price=float(row.get("purchase_price", 0) or 0),
                quantity_per_unit=row.get("quantity_per_unit", ""),
                notes=row.get("notes", ""),
            )
            add_item(session, item)
            success += 1
        except Exception as e:
            errors.append(f"Row {i}: {str(e)}")

    return success, errors


# ─── Saved Panels ───────────────────────────────────────────────────────────

def save_panel(session: Session, panel: Panel) -> str:
    """Save a panel to the database. Returns panel ID."""
    existing = session.query(SavedPanelORM).filter_by(id=panel.id).first()
    panel_json = panel.model_dump_json()

    if existing:
        existing.name = panel.name
        existing.panel_json = panel_json
        existing.updated_at = datetime.now()
    else:
        orm_obj = SavedPanelORM(
            id=panel.id,
            name=panel.name,
            panel_json=panel_json,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        session.add(orm_obj)

    session.flush()
    return panel.id


def get_recent_panels(session: Session, n: int = 5) -> list[Panel]:
    """Get the most recently updated panels."""
    rows = session.query(SavedPanelORM).order_by(
        SavedPanelORM.updated_at.desc()
    ).limit(n).all()

    panels = []
    for r in rows:
        try:
            panel = Panel.model_validate_json(r.panel_json)
            panels.append(panel)
        except Exception:
            continue
    return panels
