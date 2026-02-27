"""
core/wishlist.py — Wishlist CRUD operations.

Manages antibodies the lab wants to purchase. Items move through:
  wishlist → ordered → received (moved to inventory)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from core.database import WishlistItemORM


def add_to_wishlist(
    session: Session,
    product_name: str = "",
    target: str = "",
    vendor: str = "",
    catalog_no: str = "",
    host_species: str = "",
    isotype: str = "",
    clonality: str = "",
    conjugate: str = "",
    applications: str = "",
    reactivity: str = "",
    price: float = 0,
    package_size: str = "",
    url: str = "",
    antibody_type: str = "primary",
    notes: str = "",
    priority: str = "normal",
) -> WishlistItemORM:
    """Add an antibody to the wishlist."""
    item = WishlistItemORM(
        id=str(uuid.uuid4()),
        product_name=product_name,
        target=target,
        vendor=vendor,
        catalog_no=catalog_no,
        host_species=host_species,
        isotype=isotype,
        clonality=clonality,
        conjugate=conjugate,
        applications=applications,
        reactivity=reactivity,
        price=price,
        package_size=package_size,
        url=url,
        antibody_type=antibody_type,
        notes=notes,
        priority=priority,
        added_at=datetime.now(),
        status="wishlist",
    )
    session.add(item)
    return item


def get_all_wishlist(session: Session, status: str = "") -> list[WishlistItemORM]:
    """Get wishlist items, optionally filtered by status."""
    q = session.query(WishlistItemORM)
    if status:
        q = q.filter(WishlistItemORM.status == status)
    return q.order_by(WishlistItemORM.added_at.desc()).all()


def get_wishlist_item(session: Session, item_id: str) -> Optional[WishlistItemORM]:
    return session.query(WishlistItemORM).filter_by(id=item_id).first()


def update_status(session: Session, item_id: str, new_status: str):
    """Update wishlist item status (wishlist / ordered / received)."""
    item = session.query(WishlistItemORM).filter_by(id=item_id).first()
    if item:
        item.status = new_status


def remove_from_wishlist(session: Session, item_id: str):
    session.query(WishlistItemORM).filter_by(id=item_id).delete()


def get_wishlist_summary(session: Session) -> dict:
    total = session.query(WishlistItemORM).count()
    wishlist = session.query(WishlistItemORM).filter_by(status="wishlist").count()
    ordered = session.query(WishlistItemORM).filter_by(status="ordered").count()
    return {"total": total, "wishlist": wishlist, "ordered": ordered}


def move_to_inventory(session: Session, item_id: str) -> Optional[str]:
    """
    Move a wishlist item (status=ordered) to the inventory.
    Returns the new inventory item ID, or None if not found.
    """
    import json
    from core.database import InventoryItemORM

    item = session.query(WishlistItemORM).filter_by(id=item_id).first()
    if not item:
        return None

    inv_id = str(uuid.uuid4())
    inv_item = InventoryItemORM(
        id=inv_id,
        antibody_name=item.product_name,
        target=item.target,
        catalog_no=item.catalog_no,
        vendor=item.vendor,
        host_species=item.host_species or "Rabbit",
        isotype=item.isotype or "IgG",
        clonality=item.clonality or "Monoclonal",
        conjugate=item.conjugate or "Unconjugated",
        validated_applications=json.dumps(
            [a.strip() for a in item.applications.split(",") if a.strip()]
        ) if item.applications else "[]",
        reactivity=json.dumps(
            [r.strip() for r in item.reactivity.split(",") if r.strip()]
        ) if item.reactivity else "[]",
        purchase_price=item.price,
        quantity_per_unit=item.package_size,
        datasheet_url=item.url,
        date_received=datetime.now().date().isoformat(),
        notes=f"From wishlist. {item.notes}".strip(),
    )
    session.add(inv_item)

    # Mark wishlist item as received
    item.status = "received"

    return inv_id
