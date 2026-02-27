"""
core/biocompare_scraper.py — Monthly Batch Biocompare Catalog Scraper.

Crawls Biocompare's antibody browse pages and saves product data to a local
SQLite database for instant offline querying by the panel designer software.

USAGE:
    python scripts/run_monthly_scrape.py              # Full scrape (all antigens)
    python scripts/run_monthly_scrape.py --letter R    # Only letter R
    python scripts/run_monthly_scrape.py --test        # Test with 3 antigens

URL STRUCTURE (from biocompare.com):
    Index:     /1997-BrowseCategory/browse/gb1/9776/all
    Letter:    /1997-BrowseCategory/browse/gb1/9776/{LETTER}/0/0
    Listing:   /pfu/110447/soids/{ANTIGEN_ID}/Antibodies/{ANTIGEN_NAME}
    Detail:    individual product pages linked from listing

SCRAPE LEVELS:
    Level 1 — Antigen index:   Get all target antigen names + listing URLs
    Level 2 — Product listing:  Get summary data per antibody (name, vendor, price,
                                applications, reactivity, conjugate, quantity)
    Level 3 — Product detail:   Get full specs (catalog#, host, isotype, clonality,
                                immunogen) — optional, much slower

NOTE: CSS selectors in this file are based on page content analysis from Feb 2026.
      Biocompare may change their HTML structure. If scraping breaks, update the
      selector constants in the _SELECTORS dict and the parsing functions below.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import sqlite3
import time
from datetime import datetime, date
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────

BASE_URL = "https://www.biocompare.com"
BROWSE_INDEX_URL = f"{BASE_URL}/1997-BrowseCategory/browse/gb1/9776/all"
BROWSE_LETTER_URL = f"{BASE_URL}/1997-BrowseCategory/browse/gb1/9776/{{letter}}/0/0"

# Rate limiting
MIN_DELAY = 2.5  # seconds between requests
MAX_DELAY = 5.0

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# Letters to iterate for browsing
BROWSE_LETTERS = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ["#"]

# ─── Database Schema ─────────────────────────────────────────────────────────

CATALOG_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS scrape_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    letters_scraped TEXT,
    antigens_found  INTEGER DEFAULT 0,
    products_found  INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS antigens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    display_name    TEXT,
    browse_url      TEXT,
    product_count   INTEGER DEFAULT 0,
    last_scraped    TEXT,
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS products (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    antigen_name        TEXT NOT NULL,
    product_name        TEXT NOT NULL,
    vendor              TEXT NOT NULL DEFAULT '',
    catalog_no          TEXT DEFAULT '',
    price               REAL DEFAULT 0,
    price_currency      TEXT DEFAULT 'USD',
    package_size        TEXT DEFAULT '',
    applications        TEXT DEFAULT '',
    conjugate           TEXT DEFAULT '',
    reactivity          TEXT DEFAULT '',
    host_species        TEXT DEFAULT '',
    isotype             TEXT DEFAULT '',
    clonality           TEXT DEFAULT '',
    antibody_type       TEXT DEFAULT '',
    immunogen           TEXT DEFAULT '',
    gene_aliases        TEXT DEFAULT '',
    format_info         TEXT DEFAULT '',
    storage             TEXT DEFAULT '',
    citations_count     INTEGER DEFAULT 0,
    figures_count       INTEGER DEFAULT 0,
    review_count        INTEGER DEFAULT 0,
    detail_url          TEXT DEFAULT '',
    supplier_url        TEXT DEFAULT '',
    scraped_at          TEXT NOT NULL,
    scrape_level        INTEGER DEFAULT 2,
    UNIQUE(antigen_name, product_name, vendor)
);

CREATE INDEX IF NOT EXISTS idx_products_antigen ON products(antigen_name);
CREATE INDEX IF NOT EXISTS idx_products_vendor ON products(vendor);
CREATE INDEX IF NOT EXISTS idx_products_applications ON products(applications);
CREATE INDEX IF NOT EXISTS idx_products_reactivity ON products(reactivity);
CREATE INDEX IF NOT EXISTS idx_products_conjugate ON products(conjugate);
CREATE INDEX IF NOT EXISTS idx_products_host ON products(host_species);

CREATE TABLE IF NOT EXISTS scrape_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    level       TEXT NOT NULL,
    url         TEXT,
    message     TEXT
);
"""


# ─── Database Helpers ────────────────────────────────────────────────────────

class BiocompareCatalogDB:
    """Interface to the local Biocompare catalog SQLite database."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._ensure_schema()

    def _ensure_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(CATALOG_DB_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Write operations (used by scraper) ──

    def start_scrape_run(self, letters: str = "all") -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO scrape_runs (started_at, letters_scraped) VALUES (?, ?)",
                (datetime.now().isoformat(), letters),
            )
            return cursor.lastrowid

    def finish_scrape_run(self, run_id: int, antigens: int, products: int, status: str = "completed"):
        with self._connect() as conn:
            conn.execute(
                "UPDATE scrape_runs SET finished_at=?, antigens_found=?, products_found=?, status=? WHERE id=?",
                (datetime.now().isoformat(), antigens, products, status, run_id),
            )

    def upsert_antigen(self, name: str, display_name: str = "", browse_url: str = "", product_count: int = 0):
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO antigens (name, display_name, browse_url, product_count, last_scraped)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                       display_name=excluded.display_name,
                       browse_url=excluded.browse_url,
                       product_count=excluded.product_count,
                       last_scraped=excluded.last_scraped""",
                (name.upper(), display_name or name, browse_url, product_count, datetime.now().isoformat()),
            )

    def upsert_product(self, data: dict):
        """Insert or update a product record."""
        fields = [
            "antigen_name", "product_name", "vendor", "catalog_no", "price",
            "price_currency", "package_size", "applications", "conjugate",
            "reactivity", "host_species", "isotype", "clonality", "antibody_type",
            "immunogen", "gene_aliases", "format_info", "storage",
            "citations_count", "figures_count", "review_count",
            "detail_url", "supplier_url", "scraped_at", "scrape_level",
        ]
        # Set defaults
        data.setdefault("scraped_at", datetime.now().isoformat())
        data.setdefault("scrape_level", 2)
        data.setdefault("price_currency", "USD")
        data.setdefault("antigen_name", "")
        data["antigen_name"] = data["antigen_name"].upper()

        values = [data.get(f, "") for f in fields]

        placeholders = ", ".join(["?"] * len(fields))
        columns = ", ".join(fields)
        update_clause = ", ".join(f"{f}=excluded.{f}" for f in fields if f not in ("antigen_name", "product_name", "vendor"))

        with self._connect() as conn:
            conn.execute(
                f"""INSERT INTO products ({columns}) VALUES ({placeholders})
                    ON CONFLICT(antigen_name, product_name, vendor) DO UPDATE SET {update_clause}""",
                values,
            )

    def log_event(self, level: str, message: str, url: str = ""):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO scrape_log (timestamp, level, url, message) VALUES (?, ?, ?, ?)",
                (datetime.now().isoformat(), level, url, message),
            )

    # ── Read operations (used by app) ──

    def search_products(
        self,
        query: str = "",
        target: str = "",
        host: str = "",
        application: str = "",
        conjugate: str = "",
        reactivity: str = "",
        vendor: str = "",
        max_results: int = 50,
    ) -> list[dict]:
        """
        Search the local catalog. All filters use LIKE matching.
        Returns list of product dicts.
        """
        conditions = []
        params = []

        if query:
            # Search across product name, antigen, and vendor
            conditions.append("(product_name LIKE ? OR antigen_name LIKE ? OR vendor LIKE ?)")
            q = f"%{query}%"
            params.extend([q, q, q])
        if target:
            conditions.append("antigen_name LIKE ?")
            params.append(f"%{target.upper()}%")
        if host:
            conditions.append("host_species LIKE ?")
            params.append(f"%{host}%")
        if application:
            conditions.append("applications LIKE ?")
            params.append(f"%{application}%")
        if conjugate:
            conditions.append("conjugate LIKE ?")
            params.append(f"%{conjugate}%")
        if reactivity:
            conditions.append("reactivity LIKE ?")
            params.append(f"%{reactivity}%")
        if vendor:
            conditions.append("vendor LIKE ?")
            params.append(f"%{vendor}%")

        where = " AND ".join(conditions) if conditions else "1=1"

        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM products WHERE {where} ORDER BY price DESC LIMIT ?",
                params + [max_results],
            ).fetchall()
            return [dict(row) for row in rows]

    def get_antigens(self, letter: str = "") -> list[dict]:
        """Get all antigens, optionally filtered by starting letter."""
        with self._connect() as conn:
            if letter:
                rows = conn.execute(
                    "SELECT * FROM antigens WHERE name LIKE ? ORDER BY name",
                    (f"{letter.upper()}%",),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM antigens ORDER BY name").fetchall()
            return [dict(row) for row in rows]

    def get_stats(self) -> dict:
        """Get catalog statistics."""
        with self._connect() as conn:
            antigen_count = conn.execute("SELECT COUNT(*) FROM antigens").fetchone()[0]
            product_count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            vendor_count = conn.execute("SELECT COUNT(DISTINCT vendor) FROM products").fetchone()[0]

            last_run = conn.execute(
                "SELECT * FROM scrape_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()

            return {
                "antigen_count": antigen_count,
                "product_count": product_count,
                "vendor_count": vendor_count,
                "last_scrape": dict(last_run) if last_run else None,
            }

    def export_to_csv(self, output_path: str | Path):
        """Export entire product catalog to CSV."""
        import csv
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM products ORDER BY antigen_name, vendor").fetchall()
            if not rows:
                logger.warning("No products to export")
                return

            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(rows[0].keys())
                for row in rows:
                    writer.writerow(tuple(row))

        logger.info(f"Exported {len(rows)} products to {output_path}")


# ─── HTTP Helpers ────────────────────────────────────────────────────────────

_last_request_time = 0.0


def _rate_limit():
    """Enforce minimum delay between requests."""
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    delay = random.uniform(MIN_DELAY, MAX_DELAY)
    if elapsed < delay:
        time.sleep(delay - elapsed)
    _last_request_time = time.time()


def _get_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
    }


def _fetch_page(url: str, timeout: int = 30, retries: int = 3) -> Optional[str]:
    """
    Fetch a URL with rate limiting, retries, and error handling.
    Returns HTML text or None on failure.
    """
    import requests

    for attempt in range(retries):
        _rate_limit()
        try:
            resp = requests.get(url, headers=_get_headers(), timeout=timeout)
            if resp.status_code == 200:
                return resp.text
            elif resp.status_code == 429:
                # Rate limited — back off exponentially
                wait = (2 ** attempt) * 10
                logger.warning(f"Rate limited (429) on {url} — waiting {wait}s")
                time.sleep(wait)
            elif resp.status_code == 404:
                logger.warning(f"404 Not Found: {url}")
                return None
            else:
                logger.warning(f"HTTP {resp.status_code} for {url}")
                time.sleep(5)
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout on attempt {attempt + 1} for {url}")
            time.sleep(5)
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error on {url}: {e}")
            time.sleep(5)

    logger.error(f"Failed to fetch {url} after {retries} attempts")
    return None


# ─── Parsing Functions ───────────────────────────────────────────────────────
# These parse Biocompare HTML pages. If Biocompare changes their layout,
# update these functions. Each is isolated so changes are localized.


def parse_antigen_index(html: str) -> list[dict]:
    """
    Parse the antigen browse page to extract target names and their listing URLs.

    The browse page (e.g., /1997-BrowseCategory/browse/gb1/9776/all) contains
    links to each antigen's product listing page.

    Returns list of dicts: [{"name": "RAB5", "url": "/pfu/.../Antibodies/RAB5", "display_name": "..."}]
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    antigens = []

    # The browse page lists antigens as links. Look for links that point to
    # product listing pages (contain /pfu/ or /soids/ in the href).
    # Also look for links in the main content area that list antigen names.
    # 
    # Strategy: Find all <a> tags whose href contains the antibody listing pattern
    # Pattern from user: /pfu/110447/soids/277971/Antibodies/RAB
    for link in soup.find_all("a", href=True):
        href = link["href"]
        # Match links to product listing pages
        if "/pfu/" in href and "/Antibodies/" in href:
            name = link.get_text(strip=True)
            if name and len(name) > 1:
                antigens.append({
                    "name": name.upper().strip(),
                    "display_name": name.strip(),
                    "url": urljoin(BASE_URL, href),
                })
        # Also match browse sub-category links
        elif "/soids/" in href:
            name = link.get_text(strip=True)
            if name and len(name) > 1:
                antigens.append({
                    "name": name.upper().strip(),
                    "display_name": name.strip(),
                    "url": urljoin(BASE_URL, href),
                })

    # Deduplicate by name
    seen = set()
    unique = []
    for a in antigens:
        if a["name"] not in seen:
            seen.add(a["name"])
            unique.append(a)

    logger.info(f"Found {len(unique)} antigens on index page")
    return unique


def parse_product_listing(html: str, antigen_name: str) -> tuple[list[dict], Optional[str]]:
    """
    Parse a product listing page to extract antibody product summaries.

    The listing page (e.g., /pfu/110447/soids/277971/Antibodies/RAB) shows
    product cards with: name, vendor, price, applications, reactivity,
    conjugate, quantity, citations.

    Returns:
        (products, next_page_url) — next_page_url is None if no more pages
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    products = []
    now = datetime.now().isoformat()

    # ── Strategy for parsing product cards ──
    # Based on the pasted content, each product block has:
    #   - Product name (link text)
    #   - "Applications:" line
    #   - "Reactivity:" line
    #   - "Conjugate/Tag:" line
    #   - "Quantity:" line
    #   - Price (like "$711.00")
    #   - Vendor name (like "Abbexa Ltd")
    #   - "Supplier Page" link
    #   - "Citations:" count
    #   - "Figures:" count
    #
    # We try multiple parsing strategies to be robust.

    # Strategy 1: Look for product containers with structured data
    # Biocompare uses product card divs. Try common class patterns.
    product_cards = soup.select(
        ".product-card, .search-result-item, .product-listing-item, "
        ".pfu-product, .compare-product, [data-product-id], "
        ".product-result, .product-row"
    )

    if product_cards:
        for card in product_cards:
            product = _parse_product_card(card, antigen_name, now)
            if product and product.get("product_name"):
                products.append(product)

    # Strategy 2: If structured cards not found, try text-based parsing
    # The pasted content shows products as text blocks with labeled fields
    if not products:
        products = _parse_listing_text_blocks(soup, antigen_name, now)

    # Strategy 3: Parse from any table structure
    if not products:
        products = _parse_listing_tables(soup, antigen_name, now)

    # ── Find next page URL ──
    next_url = None
    # Look for pagination: "1 2 >" or "Next" links
    pager = soup.select("a.next, a[rel='next'], .pagination a, .pager a")
    for link in pager:
        text = link.get_text(strip=True)
        if text in (">", "Next", "»", "next") or "next" in link.get("class", []):
            href = link.get("href", "")
            if href:
                next_url = urljoin(BASE_URL, href)
                break

    # Also check for numbered pagination
    if not next_url:
        page_links = soup.select("a[href*='page='], a[href*='/page/']")
        # Find current page number and get next
        for link in page_links:
            text = link.get_text(strip=True)
            if text == ">":
                href = link.get("href", "")
                if href:
                    next_url = urljoin(BASE_URL, href)
                    break

    logger.info(f"Parsed {len(products)} products for {antigen_name}" +
                (f" (next page: {next_url})" if next_url else " (last page)"))
    return products, next_url


def _parse_product_card(card, antigen_name: str, timestamp: str) -> Optional[dict]:
    """Parse a single product card element."""
    try:
        # Product name: usually in a heading or strong link
        name_el = card.select_one("h3, h4, h5, .product-name, .product-title, strong > a, a > strong")
        name = name_el.get_text(strip=True) if name_el else ""
        if not name:
            # Try first link text
            first_link = card.select_one("a")
            name = first_link.get_text(strip=True) if first_link else ""

        if not name or len(name) < 3:
            return None

        # Vendor: look for company/supplier name
        vendor = ""
        vendor_candidates = card.select(".supplier, .vendor, .company-name, .supplier-name")
        if vendor_candidates:
            vendor = vendor_candidates[0].get_text(strip=True)

        # Price: look for dollar amount
        price = 0.0
        price_el = card.select_one(".price, .product-price, [class*='price']")
        if price_el:
            price = _parse_price_str(price_el.get_text(strip=True))
        else:
            # Search all text for $xxx.xx pattern
            card_text = card.get_text()
            price_match = re.search(r'\$[\d,]+\.?\d*', card_text)
            if price_match:
                price = _parse_price_str(price_match.group())

        # Applications
        apps = _extract_labeled_field(card, "Applications")

        # Reactivity
        reactivity = _extract_labeled_field(card, "Reactivity")

        # Conjugate/Tag
        conjugate = _extract_labeled_field(card, "Conjugate") or _extract_labeled_field(card, "Tag")

        # Quantity
        quantity = _extract_labeled_field(card, "Quantity")

        # Detail URL
        detail_url = ""
        detail_link = card.select_one("a[href*='/pfu/'], a[href*='Supplier']")
        if detail_link:
            detail_url = urljoin(BASE_URL, detail_link.get("href", ""))

        # Supplier Page URL
        supplier_url = ""
        for link in card.select("a"):
            if "Supplier Page" in link.get_text():
                supplier_url = link.get("href", "")
                break

        # Citations count
        citations = 0
        cit_match = re.search(r'Citations.*?\((\d+)\)', card.get_text())
        if cit_match:
            citations = int(cit_match.group(1))

        # Figures count
        figures = 0
        fig_match = re.search(r'Figures.*?\((\d+)\)', card.get_text())
        if fig_match:
            figures = int(fig_match.group(1))

        return {
            "antigen_name": antigen_name.upper(),
            "product_name": name,
            "vendor": vendor,
            "price": price,
            "package_size": quantity,
            "applications": apps,
            "conjugate": conjugate,
            "reactivity": reactivity,
            "citations_count": citations,
            "figures_count": figures,
            "detail_url": detail_url,
            "supplier_url": supplier_url,
            "scraped_at": timestamp,
            "scrape_level": 2,
        }
    except Exception as e:
        logger.debug(f"Error parsing product card: {e}")
        return None


def _parse_listing_text_blocks(soup, antigen_name: str, timestamp: str) -> list[dict]:
    """
    Fallback parser: extract products from text content when structured
    HTML cards aren't found. Uses regex patterns matching the Biocompare
    listing format visible in the pasted content.
    """
    products = []
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    # Pattern: product names are followed by Applications:, Reactivity:, etc.
    i = 0
    while i < len(lines):
        # Look for "Applications:" as a marker that we're in a product block
        if lines[i].startswith("Applications:") or lines[i].startswith("Applications"):
            # Work backwards to find product name (usually 1-3 lines before)
            product_name = ""
            for j in range(max(0, i - 5), i):
                candidate = lines[j]
                # Skip navigation/filter text
                if candidate and len(candidate) > 5 and not candidate.startswith(("Compare", "View All", "See More", "Supplier")):
                    if "Antibody" in candidate or "antibody" in candidate or "Ab" in candidate:
                        product_name = candidate
                        break
            if not product_name and i > 0:
                product_name = lines[i - 1]

            # Extract fields from surrounding lines
            block_text = "\n".join(lines[max(0, i - 3):min(len(lines), i + 15)])

            apps = _extract_field_from_text(block_text, "Applications")
            reactivity = _extract_field_from_text(block_text, "Reactivity")
            conjugate = _extract_field_from_text(block_text, "Conjugate/Tag") or _extract_field_from_text(block_text, "Conjugate")
            quantity = _extract_field_from_text(block_text, "Quantity")

            # Price
            price = 0.0
            price_match = re.search(r'\$[\d,]+\.?\d*', block_text)
            if price_match:
                price = _parse_price_str(price_match.group())

            # Vendor: look for company names in nearby lines
            vendor = ""
            for j in range(max(0, i - 5), min(len(lines), i + 15)):
                # Known vendor patterns (lines that end with common vendor suffixes)
                if any(suffix in lines[j] for suffix in ["Ltd", "Inc.", "LLC", "GmbH", "Biologicals", "Biotech",
                                                          "Biotechnology", "Scientific", "Sciences", "Systems",
                                                          "Biosciences", "Bio-Techne", "Solutions"]):
                    if len(lines[j]) < 80:  # Vendor names are short
                        vendor = lines[j].strip()
                        break

            if product_name:
                products.append({
                    "antigen_name": antigen_name.upper(),
                    "product_name": product_name,
                    "vendor": vendor,
                    "price": price,
                    "package_size": quantity,
                    "applications": apps,
                    "conjugate": conjugate,
                    "reactivity": reactivity,
                    "scraped_at": timestamp,
                    "scrape_level": 2,
                })

        i += 1

    return products


def _parse_listing_tables(soup, antigen_name: str, timestamp: str) -> list[dict]:
    """Fallback: try to parse products from table elements."""
    products = []
    for table in soup.select("table"):
        for row in table.select("tr"):
            cells = row.select("td, th")
            if len(cells) >= 3:
                products.append({
                    "antigen_name": antigen_name.upper(),
                    "product_name": cells[0].get_text(strip=True),
                    "vendor": cells[1].get_text(strip=True) if len(cells) > 1 else "",
                    "price": _parse_price_str(cells[2].get_text(strip=True)) if len(cells) > 2 else 0,
                    "scraped_at": timestamp,
                    "scrape_level": 2,
                })
    return products


def parse_product_detail(html: str, antigen_name: str) -> Optional[dict]:
    """
    Parse a product detail page for full specs.

    Based on the pasted content, the detail page has a "Product Specs" section
    with labeled fields: Item, Company, Price, Catalog Number, Quantity,
    Applications, Conjugate/Tag, Format, Reactivity, Target, Isotype, Host,
    Antibody Type, Immunogen, NCBI Full Gene Name, NCBI Gene Aliases,
    Clonality, Storage.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    now = datetime.now().isoformat()

    # The detail page has labeled fields. Extract them.
    text = soup.get_text(separator="\n")

    # Helper to extract "LabelValue" patterns from the specs section
    def get_spec(label: str) -> str:
        # Look for patterns like "LabelValue" or "Label: Value" or "Label\nValue"
        for pattern in [
            rf'{label}\s*[:：]\s*(.+?)(?:\n|$)',
            rf'{label}([^\n]+)',
        ]:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                val = match.group(1).strip()
                # Clean up: remove trailing labels
                for next_label in ["Company", "Price", "Catalog", "Quantity", "Applications",
                                   "Conjugate", "Format", "Reactivity", "Target", "Isotype",
                                   "Host", "Antibody Type", "Immunogen", "NCBI", "Clonality",
                                   "Storage", "Supplier"]:
                    if next_label in val and next_label != label:
                        val = val[:val.index(next_label)].strip()
                        break
                return val
        return ""

    product_name = get_spec("Item") or get_spec("Product")
    if not product_name:
        # Try page title
        title = soup.select_one("h1, .product-title, title")
        product_name = title.get_text(strip=True) if title else ""

    if not product_name:
        return None

    # Extract price
    price = 0.0
    price_str = get_spec("Price")
    if price_str:
        price = _parse_price_str(price_str)

    return {
        "antigen_name": antigen_name.upper(),
        "product_name": product_name,
        "vendor": get_spec("Company"),
        "catalog_no": get_spec("Catalog Number"),
        "price": price,
        "package_size": get_spec("Quantity"),
        "applications": get_spec("Applications"),
        "conjugate": get_spec("Conjugate/Tag") or get_spec("Conjugate"),
        "reactivity": get_spec("Reactivity"),
        "host_species": get_spec("Host"),
        "isotype": get_spec("Isotype"),
        "clonality": get_spec("Clonality"),
        "antibody_type": get_spec("Antibody Type"),
        "immunogen": get_spec("Immunogen"),
        "gene_aliases": get_spec("NCBI Gene Aliases"),
        "format_info": get_spec("Format"),
        "storage": get_spec("Storage"),
        "scraped_at": now,
        "scrape_level": 3,
    }


# ─── Extraction Helpers ──────────────────────────────────────────────────────

def _extract_labeled_field(element, label: str) -> str:
    """Extract value from a labeled field inside an HTML element."""
    text = element.get_text(separator="\n")
    for pattern in [
        rf'{label}\s*[:：]\s*(.+)',
        rf'{label}\s*\n\s*(.+)',
    ]:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            val = match.group(1).strip().split("\n")[0].strip()
            return val
    return ""


def _extract_field_from_text(text: str, label: str) -> str:
    """Extract labeled field from raw text block."""
    for pattern in [
        rf'{label}\s*[:：]\s*(.+)',
        rf'{label}\s*\n\s*(.+)',
    ]:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip().split("\n")[0].strip()
    return ""


def _parse_price_str(s: str) -> float:
    """Parse price string like '$711.00' to float."""
    if not s:
        return 0.0
    cleaned = re.sub(r'[^\d.]', '', s)
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


# ─── Main Scraper Orchestration ──────────────────────────────────────────────

class BiocompareScraper:
    """
    Monthly batch scraper for Biocompare antibody catalog.

    Usage:
        scraper = BiocompareScraper("db/biocompare_catalog.db")
        scraper.run_full_scrape()           # Scrape all letters
        scraper.run_letter_scrape("R")      # Scrape only letter R
        scraper.run_test_scrape()           # Quick test with 3 antigens
    """

    def __init__(self, db_path: str | Path):
        self.db = BiocompareCatalogDB(db_path)
        self.total_products = 0
        self.total_antigens = 0
        self._setup_logging()

    def _setup_logging(self):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"
        ))
        root_logger = logging.getLogger()
        if not root_logger.handlers:
            root_logger.addHandler(handler)
            root_logger.setLevel(logging.INFO)

    def run_full_scrape(self, deep_scrape: bool = False, max_pages_per_antigen: int = 5):
        """
        Full monthly scrape: all letters A-Z + #.

        Args:
            deep_scrape: If True, also fetch individual product detail pages (SLOW).
            max_pages_per_antigen: Max pagination pages to follow per antigen listing.
        """
        run_id = self.db.start_scrape_run("ALL")
        logger.info("=" * 60)
        logger.info("Starting FULL Biocompare scrape")
        logger.info("=" * 60)

        try:
            for letter in BROWSE_LETTERS:
                self._scrape_letter(letter, deep_scrape, max_pages_per_antigen)

            self.db.finish_scrape_run(run_id, self.total_antigens, self.total_products)
            logger.info(f"\nScrape complete: {self.total_antigens} antigens, {self.total_products} products")

        except KeyboardInterrupt:
            logger.warning("\nScrape interrupted by user")
            self.db.finish_scrape_run(run_id, self.total_antigens, self.total_products, "interrupted")
        except Exception as e:
            logger.error(f"Scrape failed: {e}")
            self.db.finish_scrape_run(run_id, self.total_antigens, self.total_products, f"error: {e}")
            raise

    def run_letter_scrape(self, letter: str, deep_scrape: bool = False, max_pages: int = 5):
        """Scrape only antigens starting with a specific letter."""
        run_id = self.db.start_scrape_run(letter.upper())
        logger.info(f"Scraping Biocompare letter: {letter.upper()}")

        try:
            self._scrape_letter(letter.upper(), deep_scrape, max_pages)
            self.db.finish_scrape_run(run_id, self.total_antigens, self.total_products)
            logger.info(f"Letter {letter} complete: {self.total_antigens} antigens, {self.total_products} products")
        except Exception as e:
            self.db.finish_scrape_run(run_id, self.total_antigens, self.total_products, f"error: {e}")
            raise

    def run_test_scrape(self, max_antigens: int = 3):
        """Quick test scrape: fetch a few antigens to verify selectors work."""
        run_id = self.db.start_scrape_run("TEST")
        logger.info(f"Running TEST scrape (max {max_antigens} antigens)")

        try:
            # Fetch the R letter page as test
            url = BROWSE_LETTER_URL.format(letter="R")
            html = _fetch_page(url)
            if not html:
                logger.error("Could not fetch browse page — check network/URL")
                self.db.finish_scrape_run(run_id, 0, 0, "error: fetch failed")
                return

            antigens = parse_antigen_index(html)
            logger.info(f"Found {len(antigens)} antigens on letter R page")

            for antigen in antigens[:max_antigens]:
                self._scrape_antigen(antigen, deep_scrape=False, max_pages=1)

            self.db.finish_scrape_run(run_id, self.total_antigens, self.total_products)
            logger.info(f"Test complete: {self.total_antigens} antigens, {self.total_products} products")

        except Exception as e:
            self.db.finish_scrape_run(run_id, self.total_antigens, self.total_products, f"error: {e}")
            raise

    def _scrape_letter(self, letter: str, deep_scrape: bool, max_pages: int):
        """Scrape all antigens for a given starting letter."""
        url = BROWSE_LETTER_URL.format(letter=letter)
        logger.info(f"\n{'─'*40}")
        logger.info(f"Fetching letter {letter}: {url}")

        html = _fetch_page(url)
        if not html:
            logger.warning(f"Could not fetch letter {letter} page")
            self.db.log_event("WARNING", f"Failed to fetch letter {letter}", url)
            return

        antigens = parse_antigen_index(html)
        logger.info(f"Letter {letter}: {len(antigens)} antigens found")

        for antigen in antigens:
            self._scrape_antigen(antigen, deep_scrape, max_pages)

    def _scrape_antigen(self, antigen: dict, deep_scrape: bool, max_pages: int):
        """Scrape all products for a single antigen target."""
        name = antigen["name"]
        url = antigen["url"]
        logger.info(f"  Scraping antigen: {name}")

        # Store antigen
        self.db.upsert_antigen(name, antigen.get("display_name", name), url)
        self.total_antigens += 1

        # Fetch listing pages
        page_num = 1
        current_url = url
        antigen_products = 0

        while current_url and page_num <= max_pages:
            html = _fetch_page(current_url)
            if not html:
                logger.warning(f"  Failed to fetch page {page_num} for {name}")
                break

            products, next_url = parse_product_listing(html, name)

            for product in products:
                self.db.upsert_product(product)
                antigen_products += 1
                self.total_products += 1

            # Optional deep scrape of individual detail pages
            if deep_scrape:
                for product in products:
                    detail_url = product.get("detail_url", "")
                    if detail_url:
                        self._scrape_product_detail(detail_url, name)

            current_url = next_url
            page_num += 1

        # Update antigen product count
        self.db.upsert_antigen(name, antigen.get("display_name", name), url, antigen_products)
        logger.info(f"  {name}: {antigen_products} products scraped")

    def _scrape_product_detail(self, url: str, antigen_name: str):
        """Scrape a single product detail page for full specs."""
        html = _fetch_page(url)
        if not html:
            return

        detail = parse_product_detail(html, antigen_name)
        if detail:
            self.db.upsert_product(detail)


# ─── Convenience Functions (for app integration) ────────────────────────────

def get_catalog_db(db_dir: str | Path = None) -> Optional[BiocompareCatalogDB]:
    """
    Get the Biocompare catalog database. Returns None if no catalog exists.
    Used by the app to check if a local catalog is available.
    """
    if db_dir is None:
        # Default: look relative to this file
        db_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db")

    db_path = os.path.join(str(db_dir), "biocompare_catalog.db")
    if os.path.exists(db_path):
        return BiocompareCatalogDB(db_path)
    return None


def search_local_catalog(
    query: str = "",
    target: str = "",
    host: str = "",
    application: str = "",
    conjugate: str = "",
    reactivity: str = "",
    vendor: str = "",
    max_results: int = 50,
    db_dir: str | Path = None,
) -> list[dict]:
    """
    Search the local Biocompare catalog.
    Returns empty list if no catalog exists.
    """
    catalog = get_catalog_db(db_dir)
    if catalog is None:
        return []
    return catalog.search_products(
        query=query, target=target, host=host, application=application,
        conjugate=conjugate, reactivity=reactivity, vendor=vendor,
        max_results=max_results,
    )
