"""
core/scraper.py — Antibody Search Interface.

Primary data source: local Biocompare catalog (populated by monthly batch scrape).
Fallback: live web scraping (rate-limited) if no local catalog available.
Manual entry always available as last resort.

The local catalog at db/biocompare_catalog.db is populated by running:
    python scripts/run_monthly_scrape.py
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from core.models import PriceResult

logger = logging.getLogger(__name__)


# ─── Main Search Function ────────────────────────────────────────────────────

def search(
    query: str,
    target: str = "",
    host: str = "",
    application: str = "",
    conjugate: str = "",
    reactivity: str = "",
    vendor: str = "",
    max_results: int = 50,
    db_dir: str = None,
) -> list[PriceResult]:
    """
    Search for antibody products. Checks local Biocompare catalog first,
    then falls back to live scraping if catalog is empty or unavailable.

    Args:
        query: General search string (e.g., "anti-CD3 human IF rabbit")
        target: Target antigen name filter
        host: Host species filter
        application: Application filter (IF, IHC, FC, WB, etc.)
        conjugate: Conjugate/fluorophore filter
        reactivity: Sample species reactivity filter
        vendor: Vendor name filter
        max_results: Maximum results to return
        db_dir: Path to database directory (default: auto-detect)

    Returns:
        List of PriceResult objects.
    """
    results = []

    # ── Try local catalog first (instant, no network needed) ──
    try:
        results = _search_local_catalog(
            query=query, target=target, host=host, application=application,
            conjugate=conjugate, reactivity=reactivity, vendor=vendor,
            max_results=max_results, db_dir=db_dir,
        )
        if results:
            logger.info(f"Local catalog returned {len(results)} results for '{query}'")
            return results
        else:
            logger.info(f"No local catalog results for '{query}' — catalog may be empty")
    except Exception as e:
        logger.warning(f"Local catalog search failed: {e}")

    # ── Fallback: live scraping ──
    logger.info("Falling back to live scraping (local catalog not available or empty)")
    results = _live_search(query, max_results)
    return results


def _search_local_catalog(
    query: str = "",
    target: str = "",
    host: str = "",
    application: str = "",
    conjugate: str = "",
    reactivity: str = "",
    vendor: str = "",
    max_results: int = 50,
    db_dir: str = None,
) -> list[PriceResult]:
    """Search the local Biocompare catalog database."""
    from core.biocompare_scraper import search_local_catalog

    rows = search_local_catalog(
        query=query, target=target, host=host, application=application,
        conjugate=conjugate, reactivity=reactivity, vendor=vendor,
        max_results=max_results, db_dir=db_dir,
    )

    results = []
    for row in rows:
        # Parse applications string to list
        apps_str = row.get("applications", "")
        apps_list = [a.strip() for a in apps_str.replace(",", " ").split() if a.strip()] if apps_str else []

        # Parse reactivity string to list
        react_str = row.get("reactivity", "")
        react_list = [r.strip() for r in react_str.replace(",", " ").split() if r.strip()] if react_str else []

        results.append(PriceResult(
            product_name=row.get("product_name", ""),
            target=row.get("antigen_name", ""),
            vendor=row.get("vendor", ""),
            catalog_no=row.get("catalog_no", ""),
            price=float(row.get("price", 0)),
            package_size=row.get("package_size", ""),
            host_species=row.get("host_species", ""),
            isotype=row.get("isotype", ""),
            clonality=row.get("clonality", ""),
            conjugate=row.get("conjugate", ""),
            validated_applications=apps_list,
            reactivity=react_list,
            url=row.get("detail_url", "") or row.get("supplier_url", ""),
            scraped_at=datetime.fromisoformat(row["scraped_at"]) if row.get("scraped_at") else None,
            is_cached=True,  # It's from local catalog
        ))

    return results


def _live_search(query: str, max_results: int) -> list[PriceResult]:
    """
    Fallback live scraping. Only used when local catalog is empty/unavailable.
    Rate-limited and slower than local catalog.
    """
    import random
    import time
    from urllib.parse import quote_plus

    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("requests/beautifulsoup4 not installed — live scraping unavailable")
        return []

    # Simple Biocompare search as fallback
    url = f"https://www.biocompare.com/pfu/110487/scp/antibodies?search={quote_plus(query)}"
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
    ]

    try:
        time.sleep(random.uniform(2.5, 5.0))
        headers = {
            "User-Agent": random.choice(user_agents),
            "Accept": "text/html,application/xhtml+xml",
        }
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()

        # Try to extract any product info from the page
        # (This is best-effort — the monthly scrape is the primary approach)
        logger.info(f"Live search returned {resp.status_code} for {url}")
        return []  # Parsing would need site-specific selectors

    except Exception as e:
        logger.error(f"Live search failed: {e}")
        return []


def get_catalog_stats(db_dir: str = None) -> Optional[dict]:
    """Get statistics about the local Biocompare catalog."""
    try:
        from core.biocompare_scraper import get_catalog_db
        catalog = get_catalog_db(db_dir)
        if catalog:
            return catalog.get_stats()
    except Exception:
        pass
    return None


# ─── Manual Entry ────────────────────────────────────────────────────────────

def create_manual_price_result(
    target: str,
    vendor: str = "",
    catalog_no: str = "",
    price: float = 0,
    package_size: str = "",
    url: str = "",
    **kwargs,
) -> PriceResult:
    """
    Create a PriceResult from manual user input.
    Always available as last resort when both catalog and live scraping fail.
    """
    return PriceResult(
        target=target,
        vendor=vendor,
        catalog_no=catalog_no,
        price=price,
        package_size=package_size,
        url=url,
        scraped_at=datetime.now(),
        is_cached=False,
        **kwargs,
    )
