"""
core/database.py — SQLAlchemy ORM layer for inventory and price cache databases.

Two separate SQLite databases:
  - inventory.db: Antibody stock, usage logs, saved panels/protocols
  - price_cache.db: Scraped price results with TTL
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Generator, Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer, String, Text,
    create_engine, event,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


# ─── Base ────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ─── Inventory ORM Models ───────────────────────────────────────────────────

class InventoryItemORM(Base):
    __tablename__ = "inventory_items"

    id = Column(String, primary_key=True)
    antibody_name = Column(String, default="")
    target = Column(String, index=True, default="")
    catalog_no = Column(String, default="")
    lot_no = Column(String, default="")
    vendor = Column(String, index=True, default="")
    host_species = Column(String, default="Rabbit")
    isotype = Column(String, default="IgG")
    clonality = Column(String, default="Monoclonal")
    clone_name = Column(String, default="")
    conjugate = Column(String, default="Unconjugated")
    validated_applications = Column(Text, default="[]")  # JSON list
    reactivity = Column(Text, default="[]")  # JSON list
    concentration = Column(Float, default=0)
    total_volume_ul = Column(Float, default=0)
    aliquot_size_ul = Column(Float, default=0)
    storage_temp = Column(String, default="-20°C")
    expiry_date = Column(String, nullable=True)  # ISO date string
    date_received = Column(String, nullable=True)
    date_opened = Column(String, nullable=True)
    location = Column(String, default="")
    working_dilution_if = Column(String, default="")
    working_dilution_fc = Column(String, default="")
    working_dilution_ihc = Column(String, default="")
    freeze_thaw_count = Column(Integer, default=0)
    notes = Column(Text, default="")
    datasheet_url = Column(String, default="")
    purchase_price = Column(Float, default=0)
    quantity_per_unit = Column(String, default="")


class UsageLogORM(Base):
    __tablename__ = "usage_logs"

    id = Column(String, primary_key=True)
    inventory_item_id = Column(String, index=True)
    experiment_name = Column(String, default="")
    panel_id = Column(String, default="")
    volume_used_ul = Column(Float, default=0)
    date_used = Column(String, default="")
    notes = Column(Text, default="")


class SavedPanelORM(Base):
    __tablename__ = "saved_panels"

    id = Column(String, primary_key=True)
    name = Column(String, default="")
    panel_json = Column(Text, default="{}")  # Full Panel serialized as JSON
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)


class SavedProtocolORM(Base):
    __tablename__ = "saved_protocols"

    id = Column(String, primary_key=True)
    panel_id = Column(String, default="")
    title = Column(String, default="")
    protocol_json = Column(Text, default="{}")  # Full Protocol serialized as JSON
    created_at = Column(DateTime, default=datetime.now)
    version = Column(String, default="1.0")


# ─── Price Cache ORM ────────────────────────────────────────────────────────

class PriceCacheBase(DeclarativeBase):
    pass


class PriceCacheORM(PriceCacheBase):
    __tablename__ = "price_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    query = Column(String, index=True)
    catalog_no = Column(String, default="")
    vendor = Column(String, default="")
    product_name = Column(String, default="")
    target = Column(String, default="")
    host_species = Column(String, default="")
    isotype = Column(String, default="")
    clonality = Column(String, default="")
    conjugate = Column(String, default="")
    validated_applications = Column(Text, default="[]")
    reactivity = Column(Text, default="[]")
    price = Column(Float, default=0)
    package_size = Column(String, default="")
    url = Column(String, default="")
    review_score = Column(Float, default=0)
    num_reviews = Column(Integer, default=0)
    scraped_at = Column(DateTime, default=datetime.now)


# ─── Database Manager ────────────────────────────────────────────────────────

class DatabaseManager:
    """Manages database connections, sessions, and initialization."""

    def __init__(self, db_dir: str = "db"):
        self.db_dir = db_dir
        os.makedirs(db_dir, exist_ok=True)

        inventory_path = os.path.join(db_dir, "inventory.db")
        cache_path = os.path.join(db_dir, "price_cache.db")

        self.inventory_engine = create_engine(
            f"sqlite:///{inventory_path}", echo=False
        )
        self.cache_engine = create_engine(
            f"sqlite:///{cache_path}", echo=False
        )

        # Enable WAL mode for better concurrent access
        @event.listens_for(self.inventory_engine, "connect")
        def set_sqlite_pragma_inv(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        @event.listens_for(self.cache_engine, "connect")
        def set_sqlite_pragma_cache(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        self.InventorySession = sessionmaker(bind=self.inventory_engine)
        self.CacheSession = sessionmaker(bind=self.cache_engine)

    def create_all(self):
        """Create all tables in both databases."""
        Base.metadata.create_all(self.inventory_engine)
        PriceCacheBase.metadata.create_all(self.cache_engine)

    @contextmanager
    def inventory_session(self) -> Generator[Session, None, None]:
        """Context manager for inventory database sessions."""
        session = self.InventorySession()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def cache_session(self) -> Generator[Session, None, None]:
        """Context manager for price cache database sessions."""
        session = self.CacheSession()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


# ─── Conversion Helpers ──────────────────────────────────────────────────────

def _parse_date(date_str: Optional[str]) -> Optional[date]:
    if not date_str:
        return None
    try:
        return date.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None


def _date_to_str(d: Optional[date]) -> Optional[str]:
    if not d:
        return None
    return d.isoformat()


def inventory_item_to_orm(item) -> InventoryItemORM:
    """Convert a Pydantic InventoryItem to ORM."""
    from core.models import InventoryItem
    return InventoryItemORM(
        id=item.id,
        antibody_name=item.antibody_name,
        target=item.target,
        catalog_no=item.catalog_no,
        lot_no=item.lot_no,
        vendor=item.vendor,
        host_species=item.host_species.value if hasattr(item.host_species, 'value') else str(item.host_species),
        isotype=item.isotype,
        clonality=item.clonality.value if hasattr(item.clonality, 'value') else str(item.clonality),
        clone_name=item.clone_name,
        conjugate=item.conjugate,
        validated_applications=json.dumps(item.validated_applications),
        reactivity=json.dumps(item.reactivity),
        concentration=item.concentration,
        total_volume_ul=item.total_volume_ul,
        aliquot_size_ul=item.aliquot_size_ul,
        storage_temp=item.storage_temp.value if hasattr(item.storage_temp, 'value') else str(item.storage_temp),
        expiry_date=_date_to_str(item.expiry_date),
        date_received=_date_to_str(item.date_received),
        date_opened=_date_to_str(item.date_opened),
        location=item.location,
        working_dilution_if=item.working_dilution_if,
        working_dilution_fc=item.working_dilution_fc,
        working_dilution_ihc=item.working_dilution_ihc,
        freeze_thaw_count=item.freeze_thaw_count,
        notes=item.notes,
        datasheet_url=item.datasheet_url,
        purchase_price=item.purchase_price,
        quantity_per_unit=item.quantity_per_unit,
    )


def orm_to_inventory_item(orm_obj: InventoryItemORM):
    """Convert an ORM row to a Pydantic InventoryItem."""
    from core.models import InventoryItem, HostSpecies, Clonality, StorageTemp

    def safe_enum(enum_cls, value, default):
        try:
            return enum_cls(value)
        except (ValueError, KeyError):
            return default

    return InventoryItem(
        id=orm_obj.id,
        antibody_name=orm_obj.antibody_name or "",
        target=orm_obj.target or "",
        catalog_no=orm_obj.catalog_no or "",
        lot_no=orm_obj.lot_no or "",
        vendor=orm_obj.vendor or "",
        host_species=safe_enum(HostSpecies, orm_obj.host_species, HostSpecies.RABBIT),
        isotype=orm_obj.isotype or "IgG",
        clonality=safe_enum(Clonality, orm_obj.clonality, Clonality.MONOCLONAL),
        clone_name=orm_obj.clone_name or "",
        conjugate=orm_obj.conjugate or "Unconjugated",
        validated_applications=json.loads(orm_obj.validated_applications or "[]"),
        reactivity=json.loads(orm_obj.reactivity or "[]"),
        concentration=orm_obj.concentration or 0,
        total_volume_ul=orm_obj.total_volume_ul or 0,
        aliquot_size_ul=orm_obj.aliquot_size_ul or 0,
        storage_temp=safe_enum(StorageTemp, orm_obj.storage_temp, StorageTemp.MINUS_20),
        expiry_date=_parse_date(orm_obj.expiry_date),
        date_received=_parse_date(orm_obj.date_received),
        date_opened=_parse_date(orm_obj.date_opened),
        location=orm_obj.location or "",
        working_dilution_if=orm_obj.working_dilution_if or "",
        working_dilution_fc=orm_obj.working_dilution_fc or "",
        working_dilution_ihc=orm_obj.working_dilution_ihc or "",
        freeze_thaw_count=orm_obj.freeze_thaw_count or 0,
        notes=orm_obj.notes or "",
        datasheet_url=orm_obj.datasheet_url or "",
        purchase_price=orm_obj.purchase_price or 0,
        quantity_per_unit=orm_obj.quantity_per_unit or "",
    )


def price_result_to_orm(query: str, result) -> PriceCacheORM:
    """Convert a PriceResult to cache ORM."""
    return PriceCacheORM(
        query=query,
        catalog_no=result.catalog_no,
        vendor=result.vendor,
        product_name=result.product_name,
        target=result.target,
        host_species=result.host_species,
        isotype=result.isotype,
        clonality=result.clonality,
        conjugate=result.conjugate,
        validated_applications=json.dumps(result.validated_applications),
        reactivity=json.dumps(result.reactivity),
        price=result.price,
        package_size=result.package_size,
        url=result.url,
        review_score=result.review_score,
        num_reviews=result.num_reviews,
        scraped_at=datetime.now(),
    )


def orm_to_price_result(orm_obj: PriceCacheORM):
    """Convert a cache ORM row to PriceResult."""
    from core.models import PriceResult
    return PriceResult(
        catalog_no=orm_obj.catalog_no or "",
        vendor=orm_obj.vendor or "",
        product_name=orm_obj.product_name or "",
        target=orm_obj.target or "",
        host_species=orm_obj.host_species or "",
        isotype=orm_obj.isotype or "",
        clonality=orm_obj.clonality or "",
        conjugate=orm_obj.conjugate or "",
        validated_applications=json.loads(orm_obj.validated_applications or "[]"),
        reactivity=json.loads(orm_obj.reactivity or "[]"),
        price=orm_obj.price or 0,
        package_size=orm_obj.package_size or "",
        url=orm_obj.url or "",
        review_score=orm_obj.review_score or 0,
        num_reviews=orm_obj.num_reviews or 0,
        scraped_at=orm_obj.scraped_at,
        is_cached=True,
    )
