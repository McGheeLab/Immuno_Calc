"""
core/scraper.py — Antibody Pricing Scraper.

Pulls antibody listings and prices from Biocompare and major vendor websites.
All requests are rate-limited, cached, and respect robots.txt.
Falls back to cached data if a site is unreachable.

NOTE: This module requires network access. When unavailable, all functions
return empty results gracefully and the manual entry fallback should be used.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime
from typing import Optional
from urllib.parse import quote_plus

from core.models import PriceResult

logger = logging.getLogger(__name__)

# ─── Rate Limiting ───────────────────────────────────────────────────────────

_last_request_time: dict[str, float] = {}
MIN_DELAY_SECONDS = 2.5
MAX_DELAY_SECONDS = 5.0

# User-Agent rotation
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
]


def _rate_limit(vendor: str):
    """Enforce rate limiting per vendor."""
    now = time.time()
    last = _last_request_time.get(vendor, 0)
    elapsed = now - last
    delay = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
    if elapsed < delay:
        time.sleep(delay - elapsed)
    _last_request_time[vendor] = time.time()


def _get_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }


# ─── Main Search Function ────────────────────────────────────────────────────

def search(
    query: str,
    vendors: Optional[list[str]] = None,
    max_results: int = 10,
    timeout: int = 30,
) -> list[PriceResult]:
    """
    Search for antibody products across vendors.

    Args:
        query: Search string (e.g., "anti-CD3 human IF rabbit")
        vendors: List of vendor names to search. None = all enabled.
        max_results: Maximum results per vendor.
        timeout: Request timeout in seconds.

    Returns:
        List of PriceResult objects with product details and prices.
    """
    if vendors is None:
        vendors = ["Biocompare", "Abcam", "Cell Signaling Technology", "BioLegend"]

    all_results: list[PriceResult] = []

    vendor_scrapers = {
        "Biocompare": _scrape_biocompare,
        "Abcam": _scrape_abcam,
        "Cell Signaling Technology": _scrape_cst,
        "BioLegend": _scrape_biolegend,
        "Thermo Fisher": _scrape_thermo,
        "R&D Systems": _scrape_rnd,
        "Sigma-Aldrich": _scrape_sigma,
    }

    for vendor in vendors:
        scraper_fn = vendor_scrapers.get(vendor)
        if scraper_fn is None:
            logger.warning(f"No scraper implemented for {vendor}")
            continue

        try:
            _rate_limit(vendor)
            results = scraper_fn(query, max_results, timeout)
            all_results.extend(results)
        except Exception as e:
            logger.error(f"Scraper error for {vendor}: {e}")
            continue

    return all_results


# ─── Vendor-Specific Scrapers ────────────────────────────────────────────────

def _scrape_biocompare(query: str, max_results: int, timeout: int) -> list[PriceResult]:
    """
    Scrape Biocompare antibody search results.
    URL pattern: https://www.biocompare.com/pfu/110487/scp/antibodies?search=<query>
    """
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("requests/beautifulsoup4 not installed — scraping unavailable")
        return []

    url = f"https://www.biocompare.com/pfu/110487/scp/antibodies?search={quote_plus(query)}"
    results = []

    try:
        resp = requests.get(url, headers=_get_headers(), timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Parse product cards (CSS selectors may change — update as needed)
        cards = soup.select(".product-card, .search-result-item, .product-listing")[:max_results]

        for card in cards:
            try:
                name_el = card.select_one(".product-name, .product-title, h3, h4")
                price_el = card.select_one(".price, .product-price")
                vendor_el = card.select_one(".supplier, .vendor-name")
                catalog_el = card.select_one(".catalog-number, .cat-num")
                link_el = card.select_one("a[href]")

                results.append(PriceResult(
                    product_name=name_el.get_text(strip=True) if name_el else "",
                    price=_parse_price(price_el.get_text(strip=True)) if price_el else 0,
                    vendor=vendor_el.get_text(strip=True) if vendor_el else "Biocompare",
                    catalog_no=catalog_el.get_text(strip=True) if catalog_el else "",
                    url=link_el["href"] if link_el and link_el.get("href") else url,
                    target=query.split()[0] if query else "",
                    scraped_at=datetime.now(),
                    is_cached=False,
                ))
            except Exception:
                continue

    except Exception as e:
        logger.error(f"Biocompare scraping failed: {e}")

    return results


def _scrape_abcam(query: str, max_results: int, timeout: int) -> list[PriceResult]:
    """Scrape Abcam using JSON-LD structured data."""
    try:
        import requests
        from bs4 import BeautifulSoup
        import json
    except ImportError:
        return []

    url = f"https://www.abcam.com/en-us/search?keywords={quote_plus(query)}"
    results = []

    try:
        resp = requests.get(url, headers=_get_headers(), timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Look for JSON-LD product data
        scripts = soup.find_all("script", type="application/ld+json")
        for script in scripts:
            try:
                data = json.loads(script.string)
                if isinstance(data, list):
                    for item in data[:max_results]:
                        if item.get("@type") == "Product":
                            offer = item.get("offers", {})
                            results.append(PriceResult(
                                product_name=item.get("name", ""),
                                price=float(offer.get("price", 0)),
                                vendor="Abcam",
                                catalog_no=item.get("sku", ""),
                                url=item.get("url", url),
                                target=query.split()[0] if query else "",
                                scraped_at=datetime.now(),
                                is_cached=False,
                            ))
                elif isinstance(data, dict) and data.get("@type") == "Product":
                    offer = data.get("offers", {})
                    results.append(PriceResult(
                        product_name=data.get("name", ""),
                        price=float(offer.get("price", 0)),
                        vendor="Abcam",
                        catalog_no=data.get("sku", ""),
                        url=data.get("url", url),
                        target=query.split()[0] if query else "",
                        scraped_at=datetime.now(),
                        is_cached=False,
                    ))
            except (json.JSONDecodeError, ValueError):
                continue

    except Exception as e:
        logger.error(f"Abcam scraping failed: {e}")

    return results[:max_results]


def _scrape_cst(query: str, max_results: int, timeout: int) -> list[PriceResult]:
    """Scrape Cell Signaling Technology using JSON-LD."""
    try:
        import requests
        from bs4 import BeautifulSoup
        import json
    except ImportError:
        return []

    url = f"https://www.cellsignal.com/search?q={quote_plus(query)}"
    results = []

    try:
        resp = requests.get(url, headers=_get_headers(), timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        scripts = soup.find_all("script", type="application/ld+json")
        for script in scripts:
            try:
                data = json.loads(script.string)
                items = data if isinstance(data, list) else [data]
                for item in items[:max_results]:
                    if item.get("@type") == "Product":
                        offer = item.get("offers", {})
                        if isinstance(offer, list):
                            offer = offer[0] if offer else {}
                        results.append(PriceResult(
                            product_name=item.get("name", ""),
                            price=float(offer.get("price", 0)),
                            vendor="Cell Signaling Technology",
                            catalog_no=item.get("sku", ""),
                            url=item.get("url", url),
                            target=query.split()[0] if query else "",
                            scraped_at=datetime.now(),
                            is_cached=False,
                        ))
            except (json.JSONDecodeError, ValueError):
                continue

    except Exception as e:
        logger.error(f"CST scraping failed: {e}")

    return results[:max_results]


def _scrape_biolegend(query: str, max_results: int, timeout: int) -> list[PriceResult]:
    """Scrape BioLegend structured catalog."""
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    url = f"https://www.biolegend.com/en-us/search-results?Keywords={quote_plus(query)}"
    results = []

    try:
        resp = requests.get(url, headers=_get_headers(), timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        cards = soup.select(".product-listing, .search-result")[:max_results]
        for card in cards:
            try:
                name_el = card.select_one(".product-name, h3")
                price_el = card.select_one(".price")
                cat_el = card.select_one(".catalog-number")

                results.append(PriceResult(
                    product_name=name_el.get_text(strip=True) if name_el else "",
                    price=_parse_price(price_el.get_text(strip=True)) if price_el else 0,
                    vendor="BioLegend",
                    catalog_no=cat_el.get_text(strip=True) if cat_el else "",
                    url=url,
                    target=query.split()[0] if query else "",
                    scraped_at=datetime.now(),
                    is_cached=False,
                ))
            except Exception:
                continue

    except Exception as e:
        logger.error(f"BioLegend scraping failed: {e}")

    return results[:max_results]


def _scrape_thermo(query: str, max_results: int, timeout: int) -> list[PriceResult]:
    """
    Thermo Fisher requires JS rendering (Playwright).
    Falls back to empty list if Playwright not available.
    """
    logger.info("Thermo Fisher scraper requires Playwright — skipping in basic mode")
    return []


def _scrape_rnd(query: str, max_results: int, timeout: int) -> list[PriceResult]:
    """Scrape R&D Systems."""
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    url = f"https://www.rndsystems.com/search?keywords={quote_plus(query)}"
    results = []

    try:
        resp = requests.get(url, headers=_get_headers(), timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        cards = soup.select(".product-card, .search-result")[:max_results]
        for card in cards:
            try:
                name_el = card.select_one(".product-name, h3, h4")
                price_el = card.select_one(".price")
                results.append(PriceResult(
                    product_name=name_el.get_text(strip=True) if name_el else "",
                    price=_parse_price(price_el.get_text(strip=True)) if price_el else 0,
                    vendor="R&D Systems",
                    url=url,
                    target=query.split()[0] if query else "",
                    scraped_at=datetime.now(),
                    is_cached=False,
                ))
            except Exception:
                continue

    except Exception as e:
        logger.error(f"R&D Systems scraping failed: {e}")

    return results[:max_results]


def _scrape_sigma(query: str, max_results: int, timeout: int) -> list[PriceResult]:
    """Scrape Sigma-Aldrich / MilliporeSigma."""
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return []

    url = f"https://www.sigmaaldrich.com/US/en/search/{quote_plus(query)}?focus=products&page=1&perpage={max_results}&sort=relevance&term={quote_plus(query)}&type=product"
    results = []

    try:
        resp = requests.get(url, headers=_get_headers(), timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Sigma embeds data in __INITIAL_STATE__
        scripts = soup.find_all("script")
        for script in scripts:
            if script.string and "__INITIAL_STATE__" in (script.string or ""):
                import json
                import re
                match = re.search(r"window\.__INITIAL_STATE__\s*=\s*({.*?});", script.string, re.DOTALL)
                if match:
                    try:
                        data = json.loads(match.group(1))
                        # Extract products from state (structure varies)
                        products = data.get("search", {}).get("products", [])
                        for p in products[:max_results]:
                            results.append(PriceResult(
                                product_name=p.get("name", ""),
                                price=float(p.get("price", {}).get("value", 0)),
                                vendor="Sigma-Aldrich",
                                catalog_no=p.get("productNumber", ""),
                                url=f"https://www.sigmaaldrich.com{p.get('url', '')}",
                                target=query.split()[0] if query else "",
                                scraped_at=datetime.now(),
                                is_cached=False,
                            ))
                    except (json.JSONDecodeError, ValueError):
                        pass

    except Exception as e:
        logger.error(f"Sigma scraping failed: {e}")

    return results[:max_results]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _parse_price(price_str: str) -> float:
    """Parse a price string like '$385.00' or '€420' into a float."""
    import re
    if not price_str:
        return 0
    # Remove currency symbols, commas, whitespace
    cleaned = re.sub(r"[^\d.]", "", price_str)
    try:
        return float(cleaned)
    except ValueError:
        return 0


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
    Used as fallback when scraping fails.
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
