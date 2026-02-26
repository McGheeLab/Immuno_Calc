"""
core/price_cache.py — SQLite TTL cache for scraped antibody prices.

Read-through cache pattern:
1. Check cache for query
2. If fresh (< TTL hours old), return cached results
3. If stale or missing, caller should invoke scraper
4. Store fresh results after scraping
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from core.database import PriceCacheORM, orm_to_price_result, price_result_to_orm
from core.models import PriceResult


def get_cached(
    session: Session,
    query: str,
    ttl_hours: int = 24,
) -> Optional[list[PriceResult]]:
    """
    Retrieve cached price results for a query.
    Returns None if cache is empty or stale.
    """
    cutoff = datetime.now() - timedelta(hours=ttl_hours)

    rows = session.query(PriceCacheORM).filter(
        PriceCacheORM.query == query,
        PriceCacheORM.scraped_at >= cutoff,
    ).all()

    if not rows:
        return None

    return [orm_to_price_result(r) for r in rows]


def store_results(
    session: Session,
    query: str,
    results: list[PriceResult],
) -> int:
    """
    Store scraped price results in cache.
    Removes any existing entries for this query first.
    Returns count of stored items.
    """
    # Clear old entries for this query
    session.query(PriceCacheORM).filter(
        PriceCacheORM.query == query
    ).delete()

    for result in results:
        orm_obj = price_result_to_orm(query, result)
        session.add(orm_obj)

    session.flush()
    return len(results)


def purge_stale_cache(session: Session, ttl_hours: int = 24) -> int:
    """
    Delete all cache entries older than TTL.
    Returns count of deleted rows.
    """
    cutoff = datetime.now() - timedelta(hours=ttl_hours)
    count = session.query(PriceCacheORM).filter(
        PriceCacheORM.scraped_at < cutoff
    ).delete()
    session.flush()
    return count


def clear_cache(session: Session) -> int:
    """Delete all cache entries. Returns count deleted."""
    count = session.query(PriceCacheORM).delete()
    session.flush()
    return count


def get_cache_stats(session: Session) -> dict:
    """Get cache statistics."""
    total = session.query(PriceCacheORM).count()
    if total == 0:
        return {"total_entries": 0, "oldest_entry": None, "newest_entry": None}

    oldest = session.query(PriceCacheORM).order_by(
        PriceCacheORM.scraped_at.asc()
    ).first()
    newest = session.query(PriceCacheORM).order_by(
        PriceCacheORM.scraped_at.desc()
    ).first()

    return {
        "total_entries": total,
        "oldest_entry": oldest.scraped_at if oldest else None,
        "newest_entry": newest.scraped_at if newest else None,
    }
