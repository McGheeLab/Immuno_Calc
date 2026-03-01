"""
core/biocompare_scraper.py — Monthly Batch Biocompare Catalog Scraper.

Uses Selenium to drive a VISIBLE Chrome browser window. You can watch it work,
manually solve CAPTCHAs, and inspect pages in real-time from VS Code's terminal.

SETUP (one time):
    pip install selenium webdriver-manager beautifulsoup4 lxml

    webdriver-manager auto-downloads the correct ChromeDriver for your Chrome version.
    No manual driver installation needed.

USAGE:
    python scripts/run_monthly_scrape.py --test           # Quick test, 3 antigens
    python scripts/run_monthly_scrape.py --letter R        # Scrape letter R
    python scripts/run_monthly_scrape.py                   # Full A-Z scrape
    python scripts/run_monthly_scrape.py --interactive     # Pause between pages

URL STRUCTURE (Biocompare):
    Browse index:    /1997-BrowseCategory/browse/gb1/9776/all
    Letter page:     /1997-BrowseCategory/browse/gb1/9776/{LETTER}/0/0
    Product listing: /pfu/110447/soids/{ID}/Antibodies/{ANTIGEN}
    Product detail:  individual product page (linked from listing)
    Direct search:   /Search-Antibodies/?search={QUERY}&said=0&soids={TYPE_ID}&vids={VENDOR_IDS}
                     (used by run_targets_scrape — no DOM interaction needed)
"""

from __future__ import annotations

import json
import logging
import os
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
ANTIBODY_SEARCH_URL = f"{BASE_URL}/Antibodies/"
SEARCH_ANTIBODIES_URL = f"{BASE_URL}/Search-Antibodies/"
BROWSE_INDEX_URL = f"{BASE_URL}/1997-BrowseCategory/browse/gb1/9776/all"
BROWSE_LETTER_URL = f"{BASE_URL}/1997-BrowseCategory/browse/gb1/9776/{{letter}}/0/0"

# Biocompare antibody type filter IDs (soids parameter)
ANTIBODY_TYPE_SOIDS = {
    "primary": "269752",
    "secondary": "218589",
    "pairs": "2318295",
}

# How long to wait between page loads (seconds) — be polite to Biocompare
PAGE_LOAD_DELAY = 3.0
# How long Selenium waits for elements to appear
IMPLICIT_WAIT = 10
# Maximum time to wait for page to fully load
PAGE_TIMEOUT = 30

BROWSE_LETTERS = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ["#"]


def build_search_url(
    antibody_type: str = "",
    vendor_vids: list[str] = None,
    search_term: str = "",
) -> str:
    """
    Build a Biocompare filtered search URL with optional search term.

    When search_term is provided, the URL goes directly to search results —
    no need to interact with the search bar DOM. This is the preferred
    approach for targeted scraping.

    Examples:
        build_search_url("primary")
        → https://www.biocompare.com/Search-Antibodies/?said=0&soids=269752

        build_search_url("primary", search_term="ki67")
        → https://www.biocompare.com/Search-Antibodies/?search=ki67&said=0&soids=269752

        build_search_url("primary", ["100436"], search_term="ki67")
        → https://www.biocompare.com/Search-Antibodies/?search=ki67&said=0&soids=269752&vids=100436

        build_search_url("secondary", ["100436", "100041"])
        → https://www.biocompare.com/Search-Antibodies/?said=0&soids=218589&vids=100436,100041
    """
    from urllib.parse import quote

    params = []
    if search_term:
        params.append(f"search={quote(search_term)}")
    params.append("said=0")
    if antibody_type and antibody_type in ANTIBODY_TYPE_SOIDS:
        params.append(f"soids={ANTIBODY_TYPE_SOIDS[antibody_type]}")
    if vendor_vids:
        params.append(f"vids={','.join(vendor_vids)}")
    return f"{SEARCH_ANTIBODIES_URL}?{'&'.join(params)}"


def load_vendors_config() -> dict:
    """Load vendors.yaml. Returns {'antibody_types': {...}, 'vendors': [...]}."""
    import yaml
    vendors_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "vendors.yaml"
    )
    if os.path.exists(vendors_path):
        with open(vendors_path) as f:
            return yaml.safe_load(f) or {}
    return {"antibody_types": {}, "vendors": []}


# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE (same as before — local SQLite catalog)
# ═══════════════════════════════════════════════════════════════════════════════

CATALOG_DB_SCHEMA_TABLES = """
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
    concentration        TEXT DEFAULT '',
    clone               TEXT DEFAULT '',
    gene_name           TEXT DEFAULT '',
    target              TEXT DEFAULT '',
    research_area       TEXT DEFAULT '',
    ncbi_full_gene_name TEXT DEFAULT '',
    citations_count     INTEGER DEFAULT 0,
    figures_count       INTEGER DEFAULT 0,
    review_count        INTEGER DEFAULT 0,
    detail_url          TEXT DEFAULT '',
    supplier_url        TEXT DEFAULT '',
    scraped_at          TEXT NOT NULL,
    scrape_level        INTEGER DEFAULT 2,
    ug_amount           REAL DEFAULT 0,
    price_per_ug        REAL DEFAULT 0,
    UNIQUE(antigen_name, product_name, vendor)
);

CREATE TABLE IF NOT EXISTS scrape_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    level       TEXT NOT NULL,
    url         TEXT,
    message     TEXT
);
"""

CATALOG_DB_SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_products_antigen ON products(antigen_name);
CREATE INDEX IF NOT EXISTS idx_products_vendor ON products(vendor);
CREATE INDEX IF NOT EXISTS idx_products_applications ON products(applications);
CREATE INDEX IF NOT EXISTS idx_products_reactivity ON products(reactivity);
CREATE INDEX IF NOT EXISTS idx_products_conjugate ON products(conjugate);
CREATE INDEX IF NOT EXISTS idx_products_host ON products(host_species);
"""

# Combined for backward compatibility
CATALOG_DB_SCHEMA = CATALOG_DB_SCHEMA_TABLES + CATALOG_DB_SCHEMA_INDEXES


class BiocompareCatalogDB:
    """Interface to the local Biocompare catalog SQLite database."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._ensure_schema()

    def _ensure_schema(self):
        with sqlite3.connect(self.db_path) as conn:
            # First, create tables (IF NOT EXISTS won't fail on existing tables)
            # Split out table creation from index creation
            conn.executescript(CATALOG_DB_SCHEMA_TABLES)
            # Migrate existing databases: add new columns if missing
            self._migrate_add_columns(conn)
            # Now create indexes (safe because columns exist)
            conn.executescript(CATALOG_DB_SCHEMA_INDEXES)

    def _migrate_add_columns(self, conn):
        """Add any missing columns to the products table.
        Handles both new deep-scraping columns and any other columns
        that might be missing from older database versions."""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
        # All columns that should exist (mapped to their SQL type defaults)
        expected_columns = {
            "catalog_no": "TEXT DEFAULT ''",
            "price": "REAL DEFAULT 0",
            "price_currency": "TEXT DEFAULT 'USD'",
            "package_size": "TEXT DEFAULT ''",
            "applications": "TEXT DEFAULT ''",
            "conjugate": "TEXT DEFAULT ''",
            "reactivity": "TEXT DEFAULT ''",
            "host_species": "TEXT DEFAULT ''",
            "isotype": "TEXT DEFAULT ''",
            "clonality": "TEXT DEFAULT ''",
            "antibody_type": "TEXT DEFAULT ''",
            "immunogen": "TEXT DEFAULT ''",
            "gene_aliases": "TEXT DEFAULT ''",
            "format_info": "TEXT DEFAULT ''",
            "storage": "TEXT DEFAULT ''",
            "concentration": "TEXT DEFAULT ''",
            "clone": "TEXT DEFAULT ''",
            "gene_name": "TEXT DEFAULT ''",
            "target": "TEXT DEFAULT ''",
            "research_area": "TEXT DEFAULT ''",
            "ncbi_full_gene_name": "TEXT DEFAULT ''",
            "citations_count": "INTEGER DEFAULT 0",
            "figures_count": "INTEGER DEFAULT 0",
            "review_count": "INTEGER DEFAULT 0",
            "detail_url": "TEXT DEFAULT ''",
            "supplier_url": "TEXT DEFAULT ''",
            "scrape_level": "INTEGER DEFAULT 2",
            "ug_amount": "REAL DEFAULT 0",
            "price_per_ug": "REAL DEFAULT 0",
        }
        for col_name, col_type in expected_columns.items():
            if col_name not in existing:
                conn.execute(f"ALTER TABLE products ADD COLUMN {col_name} {col_type}")
                logger.info(f"  DB migration: added column '{col_name}' to products table")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── Write operations ──

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
            "concentration", "clone", "gene_name", "target",
            "research_area", "ncbi_full_gene_name",
            "citations_count", "figures_count", "review_count",
            "detail_url", "supplier_url", "scraped_at", "scrape_level",
            "ug_amount", "price_per_ug",
        ]
        data.setdefault("scraped_at", datetime.now().isoformat())
        data.setdefault("scrape_level", 2)
        data.setdefault("price_currency", "USD")
        data.setdefault("antigen_name", "")
        data["antigen_name"] = data["antigen_name"].upper()

        values = [data.get(f, "") for f in fields]
        placeholders = ", ".join(["?"] * len(fields))
        columns = ", ".join(fields)

        # Build a CONDITIONAL update clause:
        #   - Text fields: only update if the new value is non-empty
        #   - Numeric fields: only update if the new value is non-zero
        #   - Metadata fields (scraped_at, scrape_level): always update
        always_update = {"scraped_at", "scrape_level"}
        numeric_fields = {"price", "citations_count", "figures_count",
                          "review_count", "ug_amount", "price_per_ug"}
        skip_fields = {"antigen_name", "product_name", "vendor"}

        update_parts = []
        for f in fields:
            if f in skip_fields:
                continue
            if f in always_update:
                update_parts.append(f"{f}=excluded.{f}")
            elif f in numeric_fields:
                # Keep existing non-zero value if new value is zero
                update_parts.append(
                    f"{f}=CASE WHEN excluded.{f} != 0 THEN excluded.{f} "
                    f"ELSE products.{f} END"
                )
            else:
                # Keep existing non-empty value if new value is empty
                update_parts.append(
                    f"{f}=CASE WHEN excluded.{f} != '' THEN excluded.{f} "
                    f"ELSE products.{f} END"
                )

        update_clause = ", ".join(update_parts)

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

    # ── Read operations (used by the app for instant search) ──

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
        sort_by: str = "price",
    ) -> list[dict]:
        conditions = []
        params = []

        if query:
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

        # Sort order
        order_map = {
            "price": "price DESC",
            "price_per_ug": "CASE WHEN price_per_ug > 0 THEN 0 ELSE 1 END, price_per_ug ASC",
            "vendor": "vendor ASC",
            "product": "product_name ASC",
        }
        order = order_map.get(sort_by, "price DESC")

        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM products WHERE {where} ORDER BY {order} LIMIT ?",
                params + [max_results],
            ).fetchall()
            return [dict(row) for row in rows]

    def get_antigens(self, letter: str = "") -> list[dict]:
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


# ═══════════════════════════════════════════════════════════════════════════════
# SELENIUM BROWSER DRIVER
# ═══════════════════════════════════════════════════════════════════════════════

class ChromeBrowser:
    """
    Manages a visible Chrome browser window via Selenium.

    The browser stays OPEN and VISIBLE so you can:
      - Watch pages load in real time
      - Manually solve CAPTCHAs if Biocompare presents one
      - Open Chrome DevTools to inspect HTML structure
      - Right-click > Inspect to find CSS selectors
    """

    def __init__(self, headless: bool = False, page_timeout: int = PAGE_TIMEOUT):
        self.driver = None
        self.headless = headless
        self.page_timeout = page_timeout

    def start(self):
        """Launch Chrome. Uses webdriver-manager to auto-install ChromeDriver."""
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service

        options = Options()

        # Don't wait for all resources (images, ads, trackers) to load.
        # "eager" = ready after DOMContentLoaded, which is enough for scraping.
        options.page_load_strategy = "eager"

        if self.headless:
            options.add_argument("--headless=new")

        # Normal browser settings to avoid detection
        options.add_argument("--window-size=1400,900")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        # Try webdriver-manager first (auto-downloads correct ChromeDriver)
        try:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=options)
        except ImportError:
            # Fall back to assuming chromedriver is on PATH
            logger.info("webdriver-manager not installed, using system chromedriver")
            self.driver = webdriver.Chrome(options=options)

        self.driver.implicitly_wait(IMPLICIT_WAIT)
        self.driver.set_page_load_timeout(self.page_timeout)

        logger.info(f"  Page load timeout: {self.page_timeout}s")

        # Remove webdriver flag to reduce bot detection
        self.driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        logger.info(f"Chrome started ({'headless' if self.headless else 'visible window'})")

    def get_page(self, url: str, wait_seconds: float = PAGE_LOAD_DELAY) -> str:
        """
        Navigate to URL and return the fully-rendered page source.
        ALWAYS captures page_source even if the page load times out —
        Biocompare loads tons of ad trackers that cause timeouts even
        when the actual content is fully loaded.
        """
        from selenium.common.exceptions import TimeoutException

        try:
            try:
                self.driver.get(url)
            except TimeoutException:
                logger.warning(f"  Page load timed out ({self.page_timeout}s) — "
                               f"continuing with partial page")

            time.sleep(wait_seconds)  # Let JS render

            # Always return whatever page_source we have
            page_source = self.driver.page_source
            if page_source and len(page_source) > 500:
                return page_source
            else:
                logger.warning(f"  Page source too small ({len(page_source or '')} chars)")
                return page_source or ""

        except Exception as e:
            logger.error(f"Failed to load {url}: {e}")
            # Last-resort: try to get whatever is in the browser
            try:
                return self.driver.page_source or ""
            except Exception:
                return ""

    def get_page_interactive(self, url: str) -> str:
        """
        Navigate to URL and WAIT for user to press Enter in terminal.
        Use this when you need to manually interact with the page first
        (solve CAPTCHA, click cookie consent, etc.)
        """
        from selenium.common.exceptions import TimeoutException
        try:
            try:
                self.driver.get(url)
            except TimeoutException:
                logger.warning(f"  Page load timed out ({self.page_timeout}s) — "
                               f"page may still be usable")
            time.sleep(2)
            input(f"\n    ⏸️  Page loaded: {url}\n"
                  f"    → Inspect the page in Chrome, solve any CAPTCHAs, etc.\n"
                  f"    → Press ENTER in this terminal when ready to continue...\n")
            return self.driver.page_source
        except Exception as e:
            logger.error(f"Failed to load {url}: {e}")
            try:
                return self.driver.page_source or ""
            except Exception:
                return ""

    def scroll_to_bottom(self):
        """Scroll page to bottom to trigger lazy-loaded content."""
        self.driver.execute_script(
            "window.scrollTo(0, document.body.scrollHeight);"
        )
        time.sleep(1.5)

    def search_biocompare(
        self,
        query: str,
        search_url: str = "",
        antibody_type: str = "primary",
        vendor_vids: list[str] = None,
        wait_seconds: float = PAGE_LOAD_DELAY,
    ) -> str:
        """
        Search Biocompare for antibodies by navigating directly to a URL
        with the search term baked in as a query parameter.

        URL pattern:
            /Search-Antibodies/?search=ki67&said=0&soids=269752&vids=100436

        Returns:
            Page source HTML string, or "" on failure.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.common.exceptions import TimeoutException

        # Build the full URL with search term included
        if search_url:
            target_url = search_url
        else:
            target_url = build_search_url(
                antibody_type=antibody_type,
                vendor_vids=vendor_vids,
                search_term=query,
            )

        # ── Navigate directly to search results ───────────────────
        logger.info(f"  Navigating to: {target_url}")
        try:
            self.driver.get(target_url)
        except TimeoutException:
            logger.warning(f"  Page load timed out ({self.page_timeout}s) — "
                           f"continuing with partial page")
        except Exception as e:
            logger.error(f"  Navigation failed for '{query}': {e}")
            # Try to return whatever we have
            try:
                return self.driver.page_source or ""
            except Exception:
                return ""

        time.sleep(wait_seconds + 2)  # Let remaining JS render

        # ── Dismiss cookie consent / overlays if present ──────────
        for dismiss_sel in [
            "button[id*='cookie']", "button[id*='consent']",
            "button[class*='cookie']", "a[class*='close']",
            ".cookie-accept", "#onetrust-accept-btn-handler",
        ]:
            try:
                btn = self.driver.find_element(By.CSS_SELECTOR, dismiss_sel)
                if btn.is_displayed():
                    btn.click()
                    logger.info(f"  Dismissed overlay: {dismiss_sel}")
                    time.sleep(1)
                    break
            except Exception:
                continue

        # ── Wait for results to appear ────────────────────────────
        logger.info("  Waiting for results to load...")
        try:
            WebDriverWait(self.driver, 10).until(
                lambda d: d.find_elements(By.CSS_SELECTOR,
                    ".productList, .productRow, "
                    ".product-listing, .search-result, table.results, "
                    ".no-results, .product-item")
            )
        except Exception:
            logger.info("  No specific results container detected, using page as-is")

        # Scroll to load lazy content
        try:
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)
        except Exception:
            pass

        # ── Always capture and return page source ─────────────────
        try:
            page_source = self.driver.page_source
        except Exception as e:
            logger.error(f"  Could not get page source: {e}")
            return ""

        logger.info(f"  Search complete for: {query} ({len(page_source):,} chars)")

        # Save debug HTML (safe — no crash if it fails)
        try:
            self._save_debug_state(f"search_results_{query}")
        except Exception:
            pass

        return page_source

    def _save_debug_state(self, label: str):
        """Save screenshot + page source for debugging."""
        debug_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "debug")
        os.makedirs(debug_dir, exist_ok=True)
        try:
            self.driver.save_screenshot(os.path.join(debug_dir, f"{label}.png"))
            with open(os.path.join(debug_dir, f"{label}.html"), "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            logger.info(f"  Debug files saved: debug/{label}.png + .html")
        except Exception as e:
            logger.error(f"  Could not save debug state: {e}")

    def save_screenshot(self, path: str):
        """Save a screenshot for debugging."""
        self.driver.save_screenshot(path)
        logger.info(f"Screenshot saved: {path}")

    def save_page_source(self, path: str):
        """Save current page HTML for debugging selectors offline."""
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.driver.page_source)
        logger.info(f"Page source saved: {path}")

    def quit(self):
        if self.driver:
            self.driver.quit()
            logger.info("Chrome closed")


# ═══════════════════════════════════════════════════════════════════════════════
# HTML PARSING (works on page_source from Selenium)
# ═══════════════════════════════════════════════════════════════════════════════

def parse_antigen_index(html: str) -> list[dict]:
    """
    Parse the browse/letter page to extract antigen names + listing URLs.
    These pages list target antigens as links to their product listing pages.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    antigens = []

    for link in soup.find_all("a", href=True):
        href = link["href"]
        # Match product listing links (pattern: /pfu/.../Antibodies/...)
        if "/pfu/" in href and "/Antibodies/" in href:
            name = link.get_text(strip=True)
            if name and len(name) > 1:
                antigens.append({
                    "name": name.upper().strip(),
                    "display_name": name.strip(),
                    "url": urljoin(BASE_URL, href),
                })
        # Also match browse category links with /soids/
        elif "/soids/" in href and "Antibod" in href:
            name = link.get_text(strip=True)
            if name and len(name) > 1:
                antigens.append({
                    "name": name.upper().strip(),
                    "display_name": name.strip(),
                    "url": urljoin(BASE_URL, href),
                })

    # Deduplicate
    seen = set()
    unique = []
    for a in antigens:
        if a["name"] not in seen:
            seen.add(a["name"])
            unique.append(a)

    logger.info(f"Found {len(unique)} antigens on browse page")
    return unique


def parse_product_listing(html: str, antigen_name: str) -> tuple[list[dict], Optional[str]]:
    """
    Parse a product listing page. Returns (products, next_page_url).

    The listing shows product cards with name, vendor, price, applications,
    reactivity, conjugate, quantity, citations. Since Biocompare renders
    this with JS, we get the FULLY RENDERED page source from Selenium.

    Three strategies (tries each in order until one finds products):
      1. CSS selector-based card detection
      2. Link-based: find <a> tags pointing to product pages, walk up to parent block
      3. Text-block parsing: look for "Applications:" markers in page text
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    products = []
    now = datetime.now().isoformat()

    # ── Strategy 1: Find product card containers by CSS class ──
    card_selectors = [
        "div.productRow",                   # Biocompare search results (camelCase!)
        ".productList > div.module",        # Alternative: children of productList
        ".product-card",
        ".search-result-item",
        ".product-listing-item",
        ".pfu-product",
        "[data-product-id]",
        ".product-result",
        ".compare-product-container",
        ".product-row",
        ".product-item",
        "div.product-listing",
    ]

    product_cards = []
    for selector in card_selectors:
        product_cards = soup.select(selector)
        if product_cards:
            logger.info(f"  Strategy 1: Found {len(product_cards)} cards with selector: {selector}")
            break

    if product_cards:
        for card in product_cards:
            product = _parse_product_card(card, antigen_name, now)
            if product and product.get("product_name"):
                products.append(product)

    if products:
        logger.info(f"  Strategy 1 succeeded: {len(products)} products parsed")
    else:
        logger.info(f"  Strategy 1: No products found via CSS selectors, trying Strategy 2 (link-based)...")

    # ── Strategy 2: Link-based parsing — find product links, walk up to parent block ──
    # This is the approach proven to work by diagnose_search_page.py:
    # find <a> tags with /pfu/ or /Antibodies/ in href and extract from context
    if not products:
        products = _parse_listing_by_links(soup, antigen_name, now)
        if products:
            logger.info(f"  Strategy 2 (link-based) succeeded: {len(products)} products")
        else:
            logger.info(f"  Strategy 2: No products found via links, trying Strategy 3 (text-based)...")

    # ── Strategy 3: Text-block parsing (fallback) ──
    if not products:
        products = _parse_listing_by_text(soup, antigen_name, now)
        if products:
            logger.info(f"  Strategy 3 (text-based) succeeded: {len(products)} products")
        else:
            logger.warning(f"  All 3 strategies failed for {antigen_name}. "
                          f"Page HTML is {len(html):,} chars. "
                          f"Check debug/ folder for saved HTML.")

    # ── Find next page link ──
    next_url = _find_next_page(soup)

    logger.info(
        f"  Parsed {len(products)} products for {antigen_name}"
        + (f" (next: {next_url})" if next_url else " (last page)")
    )
    return products, next_url


def _parse_product_card(card, antigen_name: str, timestamp: str) -> Optional[dict]:
    """Parse structured product card element."""
    try:
        # Product name — Biocompare puts it in <a> linking to the product page
        name = ""
        # Try Biocompare-specific product link first
        for a in card.select("a[href]"):
            href = a.get("href", "")
            if "/Antibodies/" in href or "/antibodies/" in href:
                candidate = a.get_text(strip=True)
                if candidate and len(candidate) > 3 and "antibod" in candidate.lower():
                    name = candidate
                    break
        # Fallback to generic heading/class selectors
        if not name:
            for sel in ["h3", "h4", "h5", ".product-name", ".product-title", "strong > a", "a > strong"]:
                el = card.select_one(sel)
                if el:
                    name = el.get_text(strip=True)
                    break
        if not name:
            first_link = card.select_one("a")
            name = first_link.get_text(strip=True) if first_link else ""
        if not name or len(name) < 3:
            return None

        # Vendor — try CSS selectors first, then text-based extraction
        vendor = ""
        for sel in [".supplier", ".vendor", ".company-name", ".supplier-name"]:
            el = card.select_one(sel)
            if el:
                vendor = el.get_text(strip=True)
                break
        if not vendor:
            # Biocompare: vendor name appears between product name and "Applications:"
            # in the card text. Extract it by finding text after the name, before labels.
            card_text = card.get_text(separator="\n")
            lines = [ln.strip() for ln in card_text.split("\n") if ln.strip()]
            for idx, line in enumerate(lines):
                if line == name and idx + 1 < len(lines):
                    # The line right after the product name is typically the vendor
                    candidate = lines[idx + 1]
                    # Make sure it's not a field label
                    if (candidate and not candidate.startswith(("Applications", "Reactivity",
                        "Conjugate", "Quantity", "Reviews", "Compare", "Supplier", "$",
                        "Sponsored", "Write"))
                        and len(candidate) < 60):
                        vendor = candidate
                    break

        # Price
        price = 0.0
        price_el = card.select_one(".price, .product-price, [class*='price']")
        if price_el:
            price = _parse_price_str(price_el.get_text(strip=True))
        else:
            m = re.search(r'\$[\d,]+\.?\d*', card.get_text())
            if m:
                price = _parse_price_str(m.group())

        card_text = card.get_text(separator="\n")

        # Labeled fields
        apps = _extract_field(card_text, "Applications")
        reactivity = _extract_field(card_text, "Reactivity")
        conjugate = _extract_field(card_text, "Conjugate/Tag") or _extract_field(card_text, "Conjugate")
        quantity = _extract_field(card_text, "Quantity")

        # URLs
        detail_url = ""
        # Biocompare search results use <a class="url fn"> for the main product link
        url_fn = card.select_one("a.url.fn")
        if url_fn and url_fn.get("href"):
            detail_url = urljoin(BASE_URL, url_fn["href"])
        if not detail_url:
            for a in card.select("a[href]"):
                href = a.get("href", "")
                if ("/9776-Antibodies/" in href or "/pfu/" in href
                        or "/Antibodies/" in href.split("?")[0]):
                    detail_url = urljoin(BASE_URL, href)
                    break

        supplier_url = ""
        for a in card.select("a"):
            if "Supplier Page" in a.get_text():
                supplier_url = a.get("href", "")
                break

        # Citations / Figures
        citations = 0
        m = re.search(r'Citations.*?\((\d+)\)', card_text)
        if m:
            citations = int(m.group(1))
        figures = 0
        m = re.search(r'Figures.*?\((\d+)\)', card_text)
        if m:
            figures = int(m.group(1))

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
        logger.debug(f"Card parse error: {e}")
        return None


def _parse_listing_by_links(soup, antigen_name: str, timestamp: str) -> list[dict]:
    """
    Strategy 2: Parse products by finding product links and walking up to parent blocks.

    This is the approach proven to work by diagnose_search_page.py:
    find <a> tags with product-like URLs (/pfu/, /Antibodies/, etc.),
    then extract product data from the nearest meaningful parent element.
    """
    products = []
    seen_urls = set()

    # Find all links that point to product pages
    product_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        # Match Biocompare product links
        if (("/pfu/" in href or "/Antibodies/" in href.split("?")[0])
                and href not in seen_urls
                and text
                and len(text) > 5):
            # Skip navigation/category links (too short or generic)
            if text.lower() in ("antibodies", "search antibodies", "browse", "home", "next", "previous"):
                continue
            product_links.append((a, href, text))
            seen_urls.add(href)

    logger.info(f"  Strategy 2: Found {len(product_links)} product-like links")

    for a_tag, href, link_text in product_links:
        try:
            # Walk up the DOM to find the product's container block.
            # Try parent, grandparent, etc. up to 5 levels — pick the one
            # that contains typical product fields like "Applications" or a price.
            block = None
            candidate = a_tag.parent
            for _ in range(6):
                if candidate is None or candidate.name in ("body", "html", "[document]"):
                    break
                candidate_text = candidate.get_text(separator="\n")
                # A good product block usually has "Applications" or a price
                has_fields = any(marker in candidate_text for marker in
                    ["Applications", "Reactivity", "Conjugate", "Quantity", "$"])
                # But it shouldn't be the whole page
                if has_fields and len(candidate_text) < 3000:
                    block = candidate
                    break
                candidate = candidate.parent

            if block is None:
                # Fallback: use the link's immediate parent
                block = a_tag.parent

            block_text = block.get_text(separator="\n")

            # ── Product name ──
            product_name = link_text

            # ── Vendor ──
            vendor = ""
            # Look for vendor in text lines near the product name
            lines = [ln.strip() for ln in block_text.split("\n") if ln.strip()]
            for idx, line in enumerate(lines):
                if line == product_name and idx + 1 < len(lines):
                    candidate_vendor = lines[idx + 1]
                    skip_prefixes = ("Applications", "Reactivity", "Conjugate",
                                     "Quantity", "Reviews", "Compare", "$",
                                     "Sponsored", "Write", "Citations", "Figures")
                    if (candidate_vendor
                            and not candidate_vendor.startswith(skip_prefixes)
                            and len(candidate_vendor) < 80):
                        vendor = candidate_vendor
                    break

            # ── Labeled fields ──
            apps = _extract_field(block_text, "Applications")
            reactivity = _extract_field(block_text, "Reactivity")
            conjugate = (_extract_field(block_text, "Conjugate/Tag")
                         or _extract_field(block_text, "Conjugate"))
            quantity = _extract_field(block_text, "Quantity")

            # ── Price ──
            price = 0.0
            m = re.search(r'\$[\d,]+\.?\d*', block_text)
            if m:
                price = _parse_price_str(m.group())

            # ── Detail URL ──
            detail_url = urljoin(BASE_URL, href)

            # ── Supplier URL ──
            supplier_url = ""
            for link in block.find_all("a", href=True):
                if "Supplier" in link.get_text():
                    supplier_url = link["href"]
                    break

            # ── Citations / Figures ──
            citations = 0
            m = re.search(r'Citations.*?\((\d+)\)', block_text)
            if m:
                citations = int(m.group(1))
            figures = 0
            m = re.search(r'Figures.*?\((\d+)\)', block_text)
            if m:
                figures = int(m.group(1))

            products.append({
                "antigen_name": antigen_name.upper(),
                "product_name": product_name,
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
            })
        except Exception as e:
            logger.debug(f"  Link-based parse error for '{link_text[:40]}': {e}")
            continue

    return products


def _parse_listing_by_text(soup, antigen_name: str, timestamp: str) -> list[dict]:
    """
    Fallback: parse products from page text when structured cards aren't found.
    Looks for "Applications:" as a marker for each product block.
    """
    products = []
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.split("\n") if line.strip()]

    i = 0
    while i < len(lines):
        if lines[i].startswith("Applications:") or lines[i].startswith("Applications"):
            # Find product name (look backwards for something that looks like one)
            product_name = ""
            for j in range(max(0, i - 5), i):
                candidate = lines[j]
                if (candidate and len(candidate) > 5
                    and not candidate.startswith(("Compare", "View All", "See More", "Supplier"))
                    and ("Antibody" in candidate or "antibody" in candidate or "Ab" in candidate)):
                    product_name = candidate
                    break
            if not product_name and i > 0:
                product_name = lines[i - 1]

            block = "\n".join(lines[max(0, i - 3):min(len(lines), i + 15)])

            apps = _extract_field(block, "Applications")
            reactivity = _extract_field(block, "Reactivity")
            conjugate = _extract_field(block, "Conjugate/Tag") or _extract_field(block, "Conjugate")
            quantity = _extract_field(block, "Quantity")

            price = 0.0
            m = re.search(r'\$[\d,]+\.?\d*', block)
            if m:
                price = _parse_price_str(m.group())

            # Vendor: look for company name patterns
            vendor = ""
            vendor_suffixes = [
                "Ltd", "Inc.", "LLC", "GmbH", "Biologicals", "Biotech",
                "Biotechnology", "Scientific", "Sciences", "Systems",
                "Biosciences", "Bio-Techne", "Solutions", "BioLegend",
            ]
            for j in range(max(0, i - 5), min(len(lines), i + 15)):
                if any(s in lines[j] for s in vendor_suffixes) and len(lines[j]) < 80:
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


def parse_product_detail(html: str, antigen_name: str, detail_url: str = "") -> Optional[dict]:
    """
    Parse a product DETAIL page (full specs) using the structured
    productDetailUL table.

    The detail page has a well-structured <ul class="productDetailUL"> with:
      - data attributes: data-vendor-name, data-catalog-no, data-quantity, data-name
      - <li class="productDetailSpecRow"> rows, each containing:
        - <span class="label_header">Field Name</span>
        - <span>Field Value</span>

    This extracts ALL spec fields from the table, plus the supplier URL
    and any data attributes on the UL element.

    Fields captured:
        Item, Company, Price, Catalog Number, Quantity, Applications,
        Conjugate/Tag, Format, Concentration, Reactivity, Isotype, Clone,
        Host, Gene Name, Antibody Type, Target, Clonality, Research Area,
        NCBI Gene Aliases, NCBI Full Gene Name, Storage, Immunogen
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    now = datetime.now().isoformat()

    # ── Strategy 1: Parse the structured productDetailUL table ──
    specs = {}
    spec_list = soup.select_one("ul.productDetailUL")

    if spec_list:
        logger.debug("  Detail: Found productDetailUL — using CSS selector parsing")

        # Extract data attributes from the <ul> tag itself
        data_attrs = {
            "data-vendor-name": spec_list.get("data-vendor-name", ""),
            "data-catalog-no": spec_list.get("data-catalog-no", ""),
            "data-quantity": spec_list.get("data-quantity", ""),
            "data-name": spec_list.get("data-name", ""),
        }

        # Parse each spec row
        for row in spec_list.select("li.productDetailSpecRow"):
            label_el = row.select_one("span.label_header")
            if not label_el:
                continue

            label = label_el.get_text(strip=True)
            # Clean up label (e.g., "Price" has an embedded help link)
            label = re.sub(r'\s+', ' ', label).strip()

            # The value is in the next <span> sibling (not the label_header)
            value = ""
            for sibling in label_el.find_next_siblings("span"):
                value = sibling.get_text(strip=True)
                break

            # If value span not found, get remaining text in the <li>
            if not value:
                # Remove the label text and get what's left
                row_text = row.get_text(strip=True)
                if row_text.startswith(label):
                    value = row_text[len(label):].strip()

            if label and value:
                specs[label] = value

        # Also extract the supplier URL from the Price row or request info section
        supplier_url = ""
        # Check the price row for supplier links
        price_row = spec_list.select_one("span.price")
        if price_row:
            for a in price_row.select("a[href]"):
                href = a.get("href", "")
                # Check that it's an external link (not to biocompare.com itself)
                if href.startswith("http") and "biocompare.com" not in href.split("?")[0]:
                    supplier_url = href
                    break

        # Also check the requestInformationModule for supplier URL
        if not supplier_url:
            req_info = soup.select_one(".requestInformationModule")
            if req_info:
                for a in req_info.select("a[href]"):
                    href = a.get("href", "")
                    if href.startswith("http") and "biocompare.com" not in href.split("?")[0]:
                        supplier_url = href
                        break

        logger.info(
            f"  Detail: Parsed {len(specs)} spec fields from productDetailUL"
            f" — {list(specs.keys())[:5]}..."
        )

    # ── Strategy 2: Fallback to text-based regex parsing ──
    if not specs:
        logger.info("  Detail: No productDetailUL found, falling back to text-based parsing")
        text = soup.get_text(separator="\n")

        def get_spec(label: str) -> str:
            for pattern in [
                rf'{label}\s*[:：]\s*(.+?)(?:\n|$)',
                rf'{label}([^\n]+)',
            ]:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    val = match.group(1).strip()
                    for nxt in ["Company", "Price", "Catalog", "Quantity", "Applications",
                                "Conjugate", "Format", "Concentration", "Reactivity",
                                "Target", "Isotype", "Host", "Clone", "Gene Name",
                                "Antibody Type", "Immunogen", "NCBI", "Clonality",
                                "Research Area", "Storage", "Supplier"]:
                        if nxt in val and nxt != label:
                            val = val[:val.index(nxt)].strip()
                            break
                    return val
            return ""

        specs = {
            "Item": get_spec("Item") or get_spec("Product"),
            "Company": get_spec("Company"),
            "Catalog Number": get_spec("Catalog Number") or get_spec("Catalog"),
            "Quantity": get_spec("Quantity"),
            "Applications": get_spec("Applications"),
            "Conjugate/Tag": get_spec("Conjugate/Tag") or get_spec("Conjugate"),
            "Format": get_spec("Format"),
            "Concentration": get_spec("Concentration"),
            "Reactivity": get_spec("Reactivity"),
            "Host": get_spec("Host"),
            "Isotype": get_spec("Isotype"),
            "Clone": get_spec("Clone"),
            "Gene Name": get_spec("Gene Name"),
            "Antibody Type": get_spec("Antibody Type"),
            "Target": get_spec("Target"),
            "Clonality": get_spec("Clonality"),
            "Research Area": get_spec("Research Area"),
            "NCBI Gene Aliases": get_spec("NCBI Gene Aliases"),
            "NCBI Full Gene Name": get_spec("NCBI Full Gene Name"),
            "Immunogen": get_spec("Immunogen"),
            "Storage": get_spec("Storage"),
        }
        supplier_url = ""
        data_attrs = {}

    # ── Build the product dict ──
    product_name = specs.get("Item", "") or specs.get("Product", "")
    if not product_name:
        # Try the data attribute or page title
        product_name = data_attrs.get("data-name", "") if data_attrs else ""
    if not product_name:
        title = soup.select_one("h1, .product-title, title")
        product_name = title.get_text(strip=True) if title else ""
    if not product_name:
        return None

    vendor = specs.get("Company", "")
    if not vendor and data_attrs:
        vendor = data_attrs.get("data-vendor-name", "")

    catalog_no = specs.get("Catalog Number", "")
    if not catalog_no and data_attrs:
        catalog_no = data_attrs.get("data-catalog-no", "")

    price = _parse_price_str(specs.get("Price", ""))

    return {
        "antigen_name": antigen_name.upper(),
        "product_name": product_name,
        "vendor": vendor,
        "catalog_no": catalog_no,
        "price": price,
        "package_size": specs.get("Quantity", ""),
        "applications": specs.get("Applications", ""),
        "conjugate": specs.get("Conjugate/Tag", "") or specs.get("Conjugate", ""),
        "reactivity": specs.get("Reactivity", ""),
        "host_species": specs.get("Host", ""),
        "isotype": specs.get("Isotype", ""),
        "clonality": specs.get("Clonality", ""),
        "antibody_type": specs.get("Antibody Type", ""),
        "immunogen": specs.get("Immunogen", ""),
        "gene_aliases": specs.get("NCBI Gene Aliases", ""),
        "format_info": specs.get("Format", ""),
        "storage": specs.get("Storage", ""),
        "concentration": specs.get("Concentration", ""),
        "clone": specs.get("Clone", ""),
        "gene_name": specs.get("Gene Name", ""),
        "target": specs.get("Target", ""),
        "research_area": specs.get("Research Area", ""),
        "ncbi_full_gene_name": specs.get("NCBI Full Gene Name", ""),
        "detail_url": detail_url,
        "supplier_url": supplier_url,
        "scraped_at": now,
        "scrape_level": 3,
    }


def parse_supplier_page(html: str, supplier_url: str = "") -> dict:
    """
    Parse a supplier's product page (e.g., Abcam, BioLegend, Thermo Fisher)
    to extract pricing and additional product data.

    Uses a multi-strategy approach:
      1. JSON-LD structured data (schema.org Product) — most reliable,
         used by Abcam and many other scientific suppliers for SEO.
         Contains price, currency, SKU, and product properties.
      2. Open Graph / meta tags — some suppliers put price in meta tags.
      3. HTML patterns — fallback: look for price in visible page content.

    Returns a dict of extracted fields (only non-empty ones included).
    Callers should merge this into the existing product record.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    result = {}

    # ── Strategy 1: JSON-LD structured data ──────────────────────
    # Many suppliers embed schema.org Product data for SEO.
    # This is the goldmine: price, currency, SKU, and all properties
    # in a clean machine-readable format.
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string)

            # Handle both single objects and arrays
            items = data if isinstance(data, list) else [data]

            for item in items:
                if not isinstance(item, dict):
                    continue

                item_type = item.get("@type", "")
                if item_type != "Product":
                    continue

                # ── Price from offers ──
                offers = item.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if isinstance(offers, dict):
                    price_str = str(offers.get("price", ""))
                    if price_str:
                        price = _parse_price_str(price_str)
                        if price > 0:
                            result["price"] = price
                    currency = offers.get("priceCurrency", "")
                    if currency:
                        result["price_currency"] = currency

                # ── SKU ──
                sku = item.get("sku", "")
                if sku:
                    result["catalog_no"] = sku

                # ── Aggregate rating ──
                rating = item.get("aggregateRating", {})
                if isinstance(rating, dict):
                    review_count = rating.get("reviewCount") or rating.get("ratingCount")
                    if review_count:
                        try:
                            result["review_count"] = int(review_count)
                        except (ValueError, TypeError):
                            pass

                # ── Additional properties (PropertyValue array) ──
                # Abcam puts host species, clonality, isotype, applications,
                # storage buffer, etc. here as structured PropertyValue objects.
                props = item.get("additionalProperty", [])
                if isinstance(props, list):
                    prop_map = {}
                    for prop in props:
                        if not isinstance(prop, dict):
                            continue
                        name = prop.get("name", "").strip()
                        value = prop.get("value", "")
                        # Handle arrays (e.g., applications, reactive species)
                        if isinstance(value, list):
                            value = ", ".join(str(v) for v in value)
                        elif not isinstance(value, str):
                            value = str(value)
                        if name and value:
                            prop_map[name.lower()] = value

                    # Map PropertyValue names to our DB fields
                    field_mapping = {
                        "host species": "host_species",
                        "isotype": "isotype",
                        "clonality": "clonality",
                        "applications": "applications",
                        "reactive species": "reactivity",
                        "form": "format_info",
                        "storage buffer": "storage",
                        "purification technique": "format_info",
                        "citation count": "citations_count",
                    }

                    for prop_name, db_field in field_mapping.items():
                        if prop_name in prop_map:
                            val = prop_map[prop_name]
                            if db_field == "citations_count":
                                try:
                                    result[db_field] = int(val)
                                except (ValueError, TypeError):
                                    pass
                            elif db_field not in result or not result[db_field]:
                                result[db_field] = val

                if result.get("price"):
                    # We found what we need from JSON-LD
                    logger.info(
                        f"  Supplier: JSON-LD extracted — "
                        f"${result.get('price', 0):.0f} {result.get('price_currency', 'USD')}"
                        f" + {len([k for k in result if k not in ('price', 'price_currency')])} properties"
                    )
                    break  # Don't need other strategies

        except (json.JSONDecodeError, TypeError, KeyError) as e:
            logger.debug(f"  Supplier: JSON-LD parse error: {e}")
            continue

    # ── Strategy 2: Meta tags ────────────────────────────────────
    if not result.get("price"):
        # Some suppliers put price in og:price or product:price meta tags
        for meta in soup.find_all("meta"):
            prop = meta.get("property", "") or meta.get("name", "")
            content = meta.get("content", "")
            if "price" in prop.lower() and content:
                price = _parse_price_str(content)
                if price > 0:
                    result["price"] = price
            elif "currency" in prop.lower() and content:
                result["price_currency"] = content
            elif prop == "product-id" and content:
                result.setdefault("catalog_no", content)

    # ── Strategy 3: HTML price patterns ──────────────────────────
    if not result.get("price"):
        # Look for common price containers
        price_selectors = [
            ".product-pricing",
            ".price", ".product-price",
            "[class*='price']",
            "[data-price]",
        ]
        for sel in price_selectors:
            for el in soup.select(sel):
                text = el.get_text(strip=True)
                m = re.search(r'\$\s*([\d,]+\.?\d*)', text)
                if m:
                    price = _parse_price_str(m.group(1))
                    if price > 0:
                        result["price"] = price
                        break
            if result.get("price"):
                break

        # Also try page-wide price search as last resort
        if not result.get("price"):
            text = soup.get_text()
            # Look for "Price: $xxx" or "Starts from $xxx" patterns
            m = re.search(r'(?:Price|Starts?\s+from)[:\s]*\$\s*([\d,]+\.?\d*)', text)
            if m:
                price = _parse_price_str(m.group(1))
                if price > 0:
                    result["price"] = price

    if result.get("price"):
        logger.info(f"  Supplier: Price found — ${result['price']:.2f} "
                     f"{result.get('price_currency', 'USD')}")
    else:
        logger.info(f"  Supplier: No price found on page ({supplier_url[:60]}...)")

    # ── Extract package size ─────────────────────────────────────
    # Many supplier pages show the size that corresponds to the price.
    # Abcam: "20ul selling size" badge in product header
    # Others may have it in meta tags or structured data
    if not result.get("package_size"):
        # Strategy A: Look for "Xxul selling size" or similar badges
        for el in soup.select("li, span, div"):
            text = el.get_text(strip=True)
            if re.search(r'\d+\s*[uµ][lL]\s*selling\s*size', text, re.IGNORECASE):
                # Extract the size part: "20ul selling size" → "20ul"
                m = re.search(r'(\d+\s*[uµ][lL])', text, re.IGNORECASE)
                if m:
                    result["package_size"] = m.group(1).strip()
                    break

        # Strategy B: Look for size in the product-pricing div
        if not result.get("package_size"):
            for el in soup.select(".product-pricing, [class*='pricing'], [class*='size']"):
                text = el.get_text(strip=True)
                sizes = parse_package_size(text)
                if sizes:
                    result["package_size"] = f"{sizes[0]['amount']}{sizes[0]['unit']}"
                    break

        # Strategy C: Check page text for common size patterns near price mentions
        if not result.get("package_size"):
            text = soup.get_text()
            # Look for size in "trial size... (20 µL)" style mentions
            m = re.search(
                r'(?:trial|selling|starting)\s+size[^)]*?(\d+\s*[uµ][lLgG])',
                text, re.IGNORECASE
            )
            if m:
                result["package_size"] = m.group(1).strip()

    if result.get("package_size"):
        logger.info(f"  Supplier: Package size — {result['package_size']}")

    result["supplier_url"] = supplier_url
    return result


# ── Helpers ──

def _extract_field(text: str, label: str) -> str:
    for pattern in [rf'{label}\s*[:：]\s*(.+)', rf'{label}\s*\n\s*(.+)']:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip().split("\n")[0].strip()
    return ""


def _parse_price_str(s: str) -> float:
    if not s:
        return 0.0
    cleaned = re.sub(r'[^\d.]', '', s)
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


# ── Size parsing and price normalization ──

# Standard assumption: if a supplier only lists volume (e.g., 20µL) without
# a concentration, assume 1 mg/mL (= 1 µg/µL). This is the most common
# antibody concentration and gives a reasonable baseline for price comparison.
DEFAULT_CONCENTRATION_UG_PER_UL = 1.0  # µg/µL = 1 mg/mL

# Unit conversion factors
_WEIGHT_TO_UG = {
    "µg": 1.0, "ug": 1.0, "mcg": 1.0,
    "mg": 1000.0,
    "g": 1_000_000.0,
}
_VOLUME_TO_UL = {
    "µl": 1.0, "ul": 1.0, "µL": 1.0, "uL": 1.0,
    "ml": 1000.0, "mL": 1000.0,
    "l": 1_000_000.0, "L": 1_000_000.0,
}

# Regex for matching size strings: "100µg", "1 mg", "20ul", "0.5mL", etc.
_SIZE_PATTERN = re.compile(
    r'(\d+(?:[.,]\d+)?)\s*'
    r'(µg|ug|mcg|mg|g|µl|ul|µL|uL|ml|mL|l|L|tests?|rxns?|assays?|slides?|strips?|units?)',
    re.IGNORECASE,
)


def parse_package_size(size_str: str) -> list[dict]:
    """
    Parse a package size string into structured size entries.

    Returns list of dicts, each with amount, unit, and either
    'ug' (weight in µg) or 'ul' (volume in µL).
    """
    if not size_str:
        return []

    results = []
    for match in _SIZE_PATTERN.finditer(size_str):
        amount_str = match.group(1).replace(",", ".")
        unit = match.group(2)

        try:
            amount = float(amount_str)
        except ValueError:
            continue

        entry = {"amount": amount, "unit": unit}

        if unit in _WEIGHT_TO_UG:
            entry["ug"] = amount * _WEIGHT_TO_UG[unit]
        elif unit in _VOLUME_TO_UL:
            entry["ul"] = amount * _VOLUME_TO_UL[unit]

        results.append(entry)

    return results


def _parse_concentration(conc_str: str) -> Optional[float]:
    """
    Parse a concentration string into µg/µL.

    Examples:
        "1 mg/ml"      → 1.0 µg/µL
        "0.5 mg/mL"    → 0.5 µg/µL
        "200 µg/mL"    → 0.2 µg/µL
        ""             → None (caller should use default)
    """
    if not conc_str:
        return None

    m = re.search(
        r'(\d+(?:\.\d+)?)\s*(mg|µg|ug|mcg|g)\s*/\s*(ml|mL|µl|ul|µL|uL|l|L)',
        conc_str, re.IGNORECASE
    )
    if not m:
        return None

    amount = float(m.group(1))
    w_unit = m.group(2)
    v_unit = m.group(3)

    w_factor = _WEIGHT_TO_UG.get(w_unit, 0)
    v_factor = _VOLUME_TO_UL.get(v_unit, 0)
    if not w_factor or not v_factor:
        return None

    return (amount * w_factor) / v_factor if v_factor > 0 else None


def normalize_price_per_ug(
    price: float,
    package_size: str,
    concentration: str = "",
) -> dict:
    """
    Calculate normalized price per µg of antibody.

    This is THE universal comparison metric. Everything is converted to
    weight (µg) so you can directly compare across vendors:
      - Abcam selling 20µL @ $140  → $7.00/µg (assuming 1 mg/mL)
      - BioLegend selling 100µg @ $350  → $3.50/µg
      - R&D Systems selling 1mg @ $700  → $0.70/µg

    Conversion rules:
      1. Weight (µg, mg, g) → use directly
      2. Volume (µL, mL) + known concentration → volume × conc = µg
      3. Volume + no concentration → assume 1 mg/mL (standard for most antibodies)

    When multiple sizes listed, uses the SMALLEST (matches "starts from" pricing).
    """
    if not price or price <= 0 or not package_size:
        return {}

    sizes = parse_package_size(package_size)
    if not sizes:
        return {}

    conc_ug_per_ul = _parse_concentration(concentration)

    ug_options = []
    for s in sizes:
        assumed = False
        if "ug" in s:
            ug_options.append((s["ug"], s, assumed))
        elif "ul" in s:
            if conc_ug_per_ul:
                ug = s["ul"] * conc_ug_per_ul
            else:
                ug = s["ul"] * DEFAULT_CONCENTRATION_UG_PER_UL
                assumed = True
            ug_options.append((ug, s, assumed))

    if not ug_options:
        return {}

    ug_amount, matched, assumed = min(ug_options, key=lambda x: x[0])
    if ug_amount <= 0:
        return {}

    ppu = price / ug_amount
    amt_str = f"{matched['amount']:g}"
    return {
        "price_per_ug": round(ppu, 4),
        "ug_amount": round(ug_amount, 2),
        "display": f"${ppu:.2f}/µg",
        "matched_size": f"{amt_str}{matched['unit']}",
        "assumed_conc": assumed,
    }


def _find_next_page(soup) -> Optional[str]:
    """Find the 'next page' link in pagination."""
    # Direct next links
    for sel in ["a.next", "a[rel='next']", ".pagination a.next", ".pager a.next"]:
        link = soup.select_one(sel)
        if link and link.get("href"):
            return urljoin(BASE_URL, link["href"])

    # Arrow/text next links
    for link in soup.select(".pagination a, .pager a, nav a"):
        text = link.get_text(strip=True)
        if text in (">", "Next", "»", "next", "›"):
            href = link.get("href", "")
            if href:
                return urljoin(BASE_URL, href)

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# SCRAPER ORCHESTRATOR (uses ChromeBrowser + parsers + DB)
# ═══════════════════════════════════════════════════════════════════════════════

class BiocompareScraper:
    """
    Monthly batch scraper using a visible Chrome browser.

    Usage (from scripts/run_monthly_scrape.py):
        scraper = BiocompareScraper("db/biocompare_catalog.db")
        scraper.run_test_scrape()
        scraper.run_letter_scrape("R")
        scraper.run_full_scrape()
    """

    def __init__(self, db_path: str | Path, headless: bool = False,
                 interactive: bool = False, page_timeout: int = PAGE_TIMEOUT):
        """
        Args:
            db_path: Path to SQLite catalog database.
            headless: Run Chrome without a visible window (not recommended).
            interactive: Pause after each page load and wait for Enter.
            page_timeout: Seconds before Selenium gives up waiting for page load.
                          Biocompare loads many ad trackers — increase if you see timeouts.
        """
        self.db = BiocompareCatalogDB(db_path)
        self.browser = ChromeBrowser(headless=headless, page_timeout=page_timeout)
        self.interactive = interactive
        self.total_products = 0
        self.total_antigens = 0
        self._debug_dir = os.path.join(os.path.dirname(db_path), "scrape_debug")

    def _fetch(self, url: str) -> str:
        """Fetch a page — interactive or automatic."""
        if self.interactive:
            return self.browser.get_page_interactive(url)
        else:
            return self.browser.get_page(url)

    def _save_debug(self, name: str, html: str):
        """Save page HTML for offline debugging if needed."""
        os.makedirs(self._debug_dir, exist_ok=True)
        path = os.path.join(self._debug_dir, f"{name}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)

    # ── Entry points ──

    def run_full_scrape(self, deep: bool = False, fetch_prices: bool = False,
                        max_pages: int = 5):
        """Scrape all letters A-Z + #."""
        if fetch_prices:
            deep = True
        run_id = self.db.start_scrape_run("ALL")
        logger.info("=" * 60)
        logger.info("FULL SCRAPE — Opening Chrome...")
        logger.info("=" * 60)
        self.browser.start()

        try:
            for letter in BROWSE_LETTERS:
                self._scrape_letter(letter, deep, max_pages,
                                    fetch_prices=fetch_prices)
            self.db.finish_scrape_run(run_id, self.total_antigens, self.total_products)
            logger.info(f"\n✅ Complete: {self.total_antigens} antigens, {self.total_products} products")
        except KeyboardInterrupt:
            logger.warning("\n⏹️  Interrupted (progress saved)")
            self.db.finish_scrape_run(run_id, self.total_antigens, self.total_products, "interrupted")
        except Exception as e:
            logger.error(f"Scrape error: {e}")
            self.db.finish_scrape_run(run_id, self.total_antigens, self.total_products, f"error: {e}")
            raise
        finally:
            self.browser.quit()

    def run_letter_scrape(self, letter: str, deep: bool = False,
                          fetch_prices: bool = False, max_pages: int = 5):
        """Scrape one letter."""
        if fetch_prices:
            deep = True
        run_id = self.db.start_scrape_run(letter.upper())
        logger.info(f"Scraping letter {letter.upper()} — Opening Chrome...")
        self.browser.start()

        try:
            self._scrape_letter(letter.upper(), deep, max_pages,
                                fetch_prices=fetch_prices)
            self.db.finish_scrape_run(run_id, self.total_antigens, self.total_products)
            logger.info(f"\n✅ Letter {letter}: {self.total_antigens} antigens, {self.total_products} products")
        except KeyboardInterrupt:
            logger.warning("\n⏹️  Interrupted (progress saved)")
            self.db.finish_scrape_run(run_id, self.total_antigens, self.total_products, "interrupted")
        except Exception as e:
            self.db.finish_scrape_run(run_id, self.total_antigens, self.total_products, f"error: {e}")
            raise
        finally:
            self.browser.quit()

    def run_test_scrape(self, max_antigens: int = 3):
        """Quick test: scrape a few antigens from letter R."""
        run_id = self.db.start_scrape_run("TEST")
        logger.info(f"TEST SCRAPE ({max_antigens} antigens) — Opening Chrome...")
        self.browser.start()

        try:
            url = BROWSE_LETTER_URL.format(letter="R")
            html = self._fetch(url)
            if not html:
                logger.error("❌ Could not load browse page. Check your internet connection.")
                self.db.finish_scrape_run(run_id, 0, 0, "error: no page loaded")
                return

            # Save for debugging
            self._save_debug("test_browse_R", html)
            logger.info(f"📄 Saved page HTML to {self._debug_dir}/test_browse_R.html")

            antigens = parse_antigen_index(html)
            if not antigens:
                logger.warning(
                    "⚠️  No antigens found on the page. The HTML structure may have changed.\n"
                    f"    Check the saved HTML: {self._debug_dir}/test_browse_R.html\n"
                    "    Look for <a> tags with href containing '/pfu/' and '/Antibodies/'\n"
                    "    Then update parse_antigen_index() in biocompare_scraper.py"
                )
                # Save a screenshot too
                self.browser.save_screenshot(os.path.join(self._debug_dir, "test_browse_R.png"))
                self.db.finish_scrape_run(run_id, 0, 0, "error: no antigens parsed")
                return

            logger.info(f"Found {len(antigens)} antigens. Scraping first {max_antigens}...")

            for antigen in antigens[:max_antigens]:
                self._scrape_antigen(antigen, deep=True, max_pages=1)

            self.db.finish_scrape_run(run_id, self.total_antigens, self.total_products)
            logger.info(f"\n✅ Test complete: {self.total_antigens} antigens, {self.total_products} products")

        except KeyboardInterrupt:
            logger.warning("\n⏹️  Interrupted")
            self.db.finish_scrape_run(run_id, self.total_antigens, self.total_products, "interrupted")
        except Exception as e:
            logger.error(f"Test scrape error: {e}")
            self.db.finish_scrape_run(run_id, self.total_antigens, self.total_products, f"error: {e}")
            raise
        finally:
            self.browser.quit()

    def run_single_antigen(self, antigen_name: str, listing_url: str, deep: bool = True):
        """Scrape a single antigen by name + URL. Useful for debugging one target."""
        run_id = self.db.start_scrape_run(f"SINGLE:{antigen_name}")
        logger.info(f"Scraping single antigen: {antigen_name}")
        self.browser.start()

        try:
            antigen = {"name": antigen_name.upper(), "display_name": antigen_name, "url": listing_url}
            self._scrape_antigen(antigen, deep=deep, max_pages=5)
            self.db.finish_scrape_run(run_id, 1, self.total_products)
            logger.info(f"✅ {antigen_name}: {self.total_products} products")
        finally:
            self.browser.quit()

    def run_targets_scrape(
        self,
        targets: list[str],
        antibody_type: str = "primary",
        vendor_vids: list[str] = None,
        deep: bool = False,
        fetch_prices: bool = False,
        max_pages: int = 3,
    ) -> dict:
        """
        Scrape specific target antigens by searching Biocompare directly via URL.

        For each target:
          1. Builds a direct search URL with the target name as a query param
             e.g. /Search-Antibodies/?search=ki67&said=0&soids=269752&vids=100436
          2. Navigates directly to the results page (no DOM search bar interaction)
          3. Parses product listings from the results
          4. Optionally follows pagination and detail links
          5. Optionally follows supplier URLs to get pricing

        Args:
            targets: List of antigen names (e.g., ["CD3", "Ki67", "GFAP"])
            antibody_type: "primary", "secondary", or "pairs"
            vendor_vids: List of Biocompare vendor IDs to filter by
            deep: Also fetch individual product detail pages
            fetch_prices: Also follow supplier URLs to get pricing
                          (implies deep=True since supplier URLs come from detail pages)
            max_pages: Max result pages per target

        Returns:
            dict with per-target results:
            {"CD3": {"products": 12, "status": "ok"}, ...}
        """
        # fetch_prices implies deep (need detail pages to get supplier URLs)
        if fetch_prices:
            deep = True
        label = ", ".join(t.upper() for t in targets[:5])
        if len(targets) > 5:
            label += f" (+{len(targets)-5} more)"
        run_id = self.db.start_scrape_run(f"TARGETS:{label}")
        logger.info(f"Searching Biocompare for {len(targets)} targets: {label}")
        logger.info(f"  Type: {antibody_type}, Vendors: {vendor_vids or 'all'}")
        self.browser.start()

        results_summary = {}

        try:
            for target_name in targets:
                target_name = target_name.strip()
                if not target_name:
                    continue

                logger.info(f"\n  🔍 Searching: {target_name}")

                # Build a per-target URL with the search term baked in
                target_url = build_search_url(
                    antibody_type=antibody_type,
                    vendor_vids=vendor_vids,
                    search_term=target_name,
                )
                logger.info(f"  URL: {target_url}")

                # Navigate directly to results (no search bar interaction)
                html = self.browser.search_biocompare(
                    query=target_name,
                    search_url=target_url,
                    wait_seconds=PAGE_LOAD_DELAY,
                )

                if not html:
                    results_summary[target_name] = {"products": 0, "status": "search_failed"}
                    continue

                # Save for debugging
                self._save_debug(f"search_{target_name}", html)

                # Parse search results (same format as product listings)
                products, next_url = parse_product_listing(html, target_name)

                # Save to DB
                for p in products:
                    self.db.upsert_product(p)
                    self.total_products += 1

                # Follow pagination if there are more pages
                page_num = 1
                while next_url and page_num < max_pages:
                    page_num += 1
                    logger.info(f"    Page {page_num}...")
                    html = self._fetch(next_url)
                    if not html:
                        break
                    more_products, next_url = parse_product_listing(html, target_name)
                    for p in more_products:
                        self.db.upsert_product(p)
                        self.total_products += 1
                    products.extend(more_products)

                # Deep scrape: fetch detail pages for full specs
                if deep:
                    for p in products:
                        detail_url = p.get("detail_url", "")
                        if detail_url and detail_url.startswith("http"):
                            try:
                                self._scrape_detail(
                                    detail_url, target_name,
                                    fetch_prices=fetch_prices,
                                )
                            except Exception as e:
                                logger.warning(
                                    f"       Deep: ⚠️ Error on detail page, "
                                    f"skipping: {e}"
                                )

                # Supplier scrape fallback: catch any products that _scrape_detail missed.
                # This handles cases where the listing had a supplier_url but
                # the detail page didn't (different DOM structure, JS-loaded, etc.)
                if fetch_prices:
                    db_products = self.db.search_products(
                        query=target_name, max_results=500
                    )
                    no_price = [
                        p for p in db_products
                        if (not p.get("price") or p["price"] == 0)
                        and p.get("supplier_url", "").startswith("http")
                    ]
                    if no_price:
                        logger.info(
                            f"    💰 Fallback: {len(no_price)} products still "
                            f"need pricing — retrying supplier pages..."
                        )
                        for p in no_price:
                            try:
                                self._scrape_supplier(p, target_name)
                            except Exception as e:
                                logger.warning(
                                    f"       Supplier: ⚠️ Error on supplier "
                                    f"page, skipping: {e}"
                                )
                    else:
                        logger.info(
                            f"    ✅ All products have prices or no supplier URLs"
                        )

                self.total_antigens += 1
                self.db.upsert_antigen(
                    target_name.upper(), target_name, "", len(products)
                )

                results_summary[target_name] = {
                    "products": len(products),
                    "status": "ok" if products else "no_products",
                }

                logger.info(f"    → {len(products)} products found")

            self.db.finish_scrape_run(run_id, self.total_antigens, self.total_products)
            logger.info(f"\n✅ Targets scrape complete: {self.total_products} products")

        except KeyboardInterrupt:
            logger.warning("\n⏹️  Interrupted (progress saved)")
            self.db.finish_scrape_run(run_id, self.total_antigens, self.total_products, "interrupted")
        except Exception as e:
            logger.error(f"Targets scrape error: {e}")
            self.db.finish_scrape_run(run_id, self.total_antigens, self.total_products, f"error: {e}")
            raise
        finally:
            self.browser.quit()

        return results_summary

    # ── Internal methods ──

    def _scrape_letter(self, letter: str, deep: bool, max_pages: int,
                       fetch_prices: bool = False):
        url = BROWSE_LETTER_URL.format(letter=letter)
        logger.info(f"\n{'─' * 50}")
        logger.info(f"📖 Letter {letter}: {url}")

        html = self._fetch(url)
        if not html:
            logger.warning(f"  ⚠️ Could not load letter {letter}")
            return

        antigens = parse_antigen_index(html)
        logger.info(f"  Found {len(antigens)} antigens for letter {letter}")

        for antigen in antigens:
            self._scrape_antigen(antigen, deep, max_pages,
                                 fetch_prices=fetch_prices)

    def _scrape_antigen(self, antigen: dict, deep: bool, max_pages: int,
                        fetch_prices: bool = False):
        name = antigen["name"]
        url = antigen["url"]
        logger.info(f"  🎯 {name}")

        self.db.upsert_antigen(name, antigen.get("display_name", name), url)
        self.total_antigens += 1

        page_num = 1
        current_url = url
        antigen_product_count = 0

        while current_url and page_num <= max_pages:
            html = self._fetch(current_url)
            if not html:
                break

            # Save first page of each antigen for debugging
            if page_num == 1:
                self._save_debug(f"listing_{name}_p{page_num}", html)

            products, next_url = parse_product_listing(html, name)

            for product in products:
                self.db.upsert_product(product)
                antigen_product_count += 1
                self.total_products += 1

            # Deep scrape: fetch individual detail pages for full specs
            if deep:
                for product in products:
                    detail_url = product.get("detail_url", "")
                    if detail_url and detail_url.startswith("http"):
                        try:
                            self._scrape_detail(
                                detail_url, name,
                                fetch_prices=fetch_prices,
                            )
                        except Exception as e:
                            logger.warning(
                                f"       Deep: ⚠️ Error on detail page, "
                                f"skipping: {e}"
                            )

            current_url = next_url
            page_num += 1

        self.db.upsert_antigen(name, antigen.get("display_name", name), url, antigen_product_count)
        logger.info(f"     → {antigen_product_count} products")

    def _scrape_detail(self, url: str, antigen_name: str,
                       fetch_prices: bool = False):
        """
        Fetch + parse a single product detail page for full specs.

        Deep scraping opens each product's individual page to collect
        the complete product specification table (productDetailUL),
        which includes fields not available on the search results page:
        Concentration, Clone, Gene Name, Target, Research Area,
        NCBI Full Gene Name, Format, Storage, Immunogen, etc.

        When fetch_prices=True: if the detail page has no price but has
        a supplier URL ("View Supplier Product Page"), immediately
        follows it to get pricing from the vendor's site.
        """
        logger.info(f"       Deep: {url[:80]}...")

        html = self._fetch(url)
        if not html:
            logger.warning(f"       Deep: Failed to load detail page")
            return

        # Save debug HTML for offline inspection
        url_slug = url.rstrip("/").split("/")[-1].split("?")[0][:50]
        self._save_debug(f"detail_{antigen_name}_{url_slug}", html)

        detail = parse_product_detail(html, antigen_name, detail_url=url)
        if not detail:
            logger.warning(f"       Deep: Could not parse detail page")
            return

        # Calculate normalized price if we have both price and size
        price = detail.get("price", 0)
        pkg_size = detail.get("package_size", "")
        conc = detail.get("concentration", "")
        if price and price > 0 and pkg_size:
            norm = normalize_price_per_ug(price, pkg_size, concentration=conc)
            if norm:
                detail["ug_amount"] = norm["ug_amount"]
                detail["price_per_ug"] = norm["price_per_ug"]

        self.db.upsert_product(detail)

        product_name = detail.get('product_name', '')[:50]
        vendor = detail.get('vendor', '')
        logger.info(
            f"       Deep: ✓ {product_name} ({vendor})"
        )

        # ── Immediately follow supplier URL if no price ──
        supplier_url = detail.get("supplier_url", "")
        if (fetch_prices
                and supplier_url
                and supplier_url.startswith("http")
                and (not price or price == 0)):
            logger.info(
                f"       → Following supplier link for pricing..."
            )
            self._scrape_supplier(detail, antigen_name)
        elif fetch_prices and (not price or price == 0):
            logger.info(
                f"       → No supplier URL found on detail page — "
                f"cannot fetch price for {vendor}"
            )

        # Be polite — extra delay between page requests
        time.sleep(PAGE_LOAD_DELAY * 0.5)

    def _scrape_supplier(self, product: dict, antigen_name: str):
        """
        Fetch the supplier's own product page to get pricing info.

        Called during deep scrape when a product has no price but does
        have a supplier_url (the "View Supplier Product Page" link).
        Common for Abcam, BioLegend, Thermo Fisher, etc.

        The supplier page is parsed for:
          - Price (via JSON-LD schema.org data, meta tags, or HTML patterns)
          - Additional properties that may enrich the catalog entry

        Updates the product in-place in the database (scrape_level → 4).
        """
        supplier_url = product.get("supplier_url", "")
        if not supplier_url or not supplier_url.startswith("http"):
            return

        product_name = product.get("product_name", "")[:50]
        vendor = product.get("vendor", "")
        logger.info(f"       Supplier: {vendor} — {supplier_url[:70]}...")

        html = self._fetch(supplier_url)
        if not html:
            logger.warning(f"       Supplier: Failed to load page for {product_name}")
            return

        # Save debug HTML
        url_slug = supplier_url.rstrip("/").split("/")[-1].split("?")[0][:40]
        self._save_debug(f"supplier_{antigen_name}_{url_slug}", html)

        supplier_data = parse_supplier_page(html, supplier_url=supplier_url)
        if not supplier_data or (not supplier_data.get("price") and len(supplier_data) <= 1):
            logger.info(f"       Supplier: No useful data extracted from {vendor}")
            return

        # Merge supplier data into the existing product record.
        # Only fill in fields that are empty/zero in the existing record.
        merged = {}
        for key, val in supplier_data.items():
            if key == "supplier_url":
                continue  # Already have it
            existing = product.get(key, "")
            # Fill empty strings, zeros, or missing fields
            if not existing or existing == 0 or existing == "0":
                merged[key] = val

        if merged:
            # Build an update dict with the product's identity + merged fields
            update = {
                "antigen_name": product.get("antigen_name", antigen_name.upper()),
                "product_name": product.get("product_name", ""),
                "vendor": product.get("vendor", ""),
                "scraped_at": datetime.now().isoformat(),
                "scrape_level": 4,  # Level 4 = enriched with supplier data
            }
            update.update(merged)

            # ── Calculate normalized price ──
            # Use the best data available (supplier overrides biocompare)
            final_price = merged.get("price") or product.get("price", 0)
            final_size = (merged.get("package_size")
                          or product.get("package_size", ""))
            final_conc = (merged.get("concentration")
                          or product.get("concentration", ""))
            if final_price and final_size:
                norm = normalize_price_per_ug(
                    final_price, final_size, concentration=final_conc
                )
                if norm:
                    update["ug_amount"] = norm["ug_amount"]
                    update["price_per_ug"] = norm["price_per_ug"]
                    assumed = " (assumed 1 mg/mL)" if norm.get("assumed_conc") else ""
                    logger.info(
                        f"       Supplier: 📊 {norm['display']}"
                        f" for {norm['matched_size']}{assumed}"
                    )

            self.db.upsert_product(update)

            price_msg = f"${merged['price']:.2f}" if "price" in merged else "no price"
            extra = [k for k in merged if k != "price"]
            logger.info(
                f"       Supplier: ✓ {price_msg}"
                f"{f' + {len(extra)} fields' if extra else ''}"
                f" from {vendor}"
            )
        else:
            logger.info(f"       Supplier: No new data to merge from {vendor}")

        # Be polite
        time.sleep(PAGE_LOAD_DELAY * 0.5)


# ═══════════════════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTIONS (used by the main app for instant local search)
# ═══════════════════════════════════════════════════════════════════════════════

def get_catalog_db(db_dir: str | Path = None) -> Optional[BiocompareCatalogDB]:
    """Get the local catalog DB, or None if it doesn't exist."""
    if db_dir is None:
        db_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db")
    db_path = os.path.join(str(db_dir), "biocompare_catalog.db")
    if os.path.exists(db_path):
        return BiocompareCatalogDB(db_path)
    return None


def search_local_catalog(
    query: str = "", target: str = "", host: str = "", application: str = "",
    conjugate: str = "", reactivity: str = "", vendor: str = "",
    max_results: int = 50, db_dir: str | Path = None,
) -> list[dict]:
    """Search local catalog. Returns empty list if no catalog exists."""
    catalog = get_catalog_db(db_dir)
    if catalog is None:
        return []
    return catalog.search_products(
        query=query, target=target, host=host, application=application,
        conjugate=conjugate, reactivity=reactivity, vendor=vendor,
        max_results=max_results,
    )


def _find_best_antigen_match(target_name: str, antigens: list[dict]) -> Optional[dict]:
    """
    Find the best matching antigen from a browse page for a given target name.
    Tries exact match first, then prefix, then substring.
    """
    name = target_name.upper().strip()

    # Exact match
    for a in antigens:
        if a["name"] == name:
            return a

    # Exact match on display_name (case-insensitive)
    for a in antigens:
        if a.get("display_name", "").upper().strip() == name:
            return a

    # Prefix match (e.g., target "CD3" matches antigen "CD3 / CD3D / CD3E")
    for a in antigens:
        if a["name"].startswith(name) or a["name"].startswith(name + " "):
            return a

    # Substring match (e.g., target "RAB5" matches "RAB5A")
    for a in antigens:
        if name in a["name"]:
            return a

    return None


# ═══════════════════════════════════════════════════════════════════════════════
# run_scrape() — Single callable entry point for CLI and GUI
# ═══════════════════════════════════════════════════════════════════════════════

def run_scrape(
    # ── Mode (pick one) ──
    mode: str = "full",          # "full", "letter", "test", "targets", "url", "stats", "export_csv"
    # ── Mode-specific params ──
    letter: str = "R",           # Which letter (mode="letter")
    targets: list[str] = None,   # Antigen names (mode="targets")
    url: str = "",               # Single listing URL (mode="url")
    antigen_name: str = "",      # Name for --url mode
    # ── Biocompare filters ──
    antibody_type: str = "primary",  # "primary", "secondary", "pairs"
    vendor_vids: list[str] = None,   # Biocompare vendor IDs (from vendors.yaml)
    # ── Scraper options ──
    headless: bool = False,      # Hide Chrome window
    interactive: bool = False,   # Pause at each page for manual inspection
    deep: bool = False,          # Also fetch product detail pages
    fetch_prices: bool = False,  # Also follow supplier URLs for pricing (implies deep)
    max_pages: int = 5,          # Max listing pages per antigen
    max_antigens: int = 3,       # Max antigens for test mode
    page_timeout: int = PAGE_TIMEOUT,  # Selenium page load timeout (seconds)
    # ── Paths ──
    db_path: str = None,         # Override database path
) -> dict:
    """
    Universal entry point for the Biocompare scraper. Callable from CLI, GUI,
    or any Python code. Returns a result dict.

    Modes:
        "full"      — Scrape all letters A-Z (takes hours)
        "letter"    — Scrape one letter
        "test"      — Quick test with a few antigens from letter R
        "targets"   — Scrape specific antigens by name (for panel refresh)
        "url"       — Scrape a single listing URL
        "stats"     — Return catalog statistics (no Chrome needed)
        "export_csv"— Export catalog to CSV (no Chrome needed)

    Returns:
        dict with keys: "status" (ok/error), "antigens", "products",
        "message", and mode-specific data.

    Examples:
        # From GUI — refresh pricing for current panel targets:
        result = run_scrape(mode="targets", targets=["CD3", "Ki67", "GFAP"], headless=True)

        # From CLI — scrape letter R:
        result = run_scrape(mode="letter", letter="R")

        # Quick check:
        result = run_scrape(mode="stats")
    """
    # Resolve default DB path
    if db_path is None:
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db_dir = os.path.join(project_root, "db")
        os.makedirs(db_dir, exist_ok=True)
        db_path = os.path.join(db_dir, "biocompare_catalog.db")

    # ── Stats (no browser needed) ──
    if mode == "stats":
        db = BiocompareCatalogDB(db_path)
        stats = db.get_stats()
        return {"status": "ok", "mode": "stats", **stats}

    # ── Export CSV (no browser needed) ──
    if mode == "export_csv":
        db = BiocompareCatalogDB(db_path)
        csv_dir = os.path.join(os.path.dirname(db_path), "..", "data")
        os.makedirs(csv_dir, exist_ok=True)
        csv_path = os.path.join(csv_dir, "biocompare_catalog.csv")
        db.export_to_csv(csv_path)
        return {"status": "ok", "mode": "export_csv", "path": csv_path}

    # ── Modes that need Chrome ──
    scraper = BiocompareScraper(
        db_path=db_path,
        headless=headless,
        interactive=interactive,
        page_timeout=page_timeout,
    )

    try:
        if mode == "test":
            scraper.run_test_scrape(max_antigens=max_antigens)

        elif mode == "letter":
            scraper.run_letter_scrape(letter.upper(), deep=deep,
                                      fetch_prices=fetch_prices,
                                      max_pages=max_pages)

        elif mode == "targets":
            if not targets:
                return {"status": "error", "message": "No targets provided"}
            summary = scraper.run_targets_scrape(
                targets, antibody_type=antibody_type,
                vendor_vids=vendor_vids, deep=deep,
                fetch_prices=fetch_prices, max_pages=max_pages,
            )
            return {
                "status": "ok",
                "mode": "targets",
                "antigens": scraper.total_antigens,
                "products": scraper.total_products,
                "per_target": summary,
            }

        elif mode == "url":
            name = antigen_name or url.rstrip("/").split("/")[-1].upper()
            scraper.run_single_antigen(name, url, deep=deep)

        elif mode == "full":
            scraper.run_full_scrape(deep=deep, fetch_prices=fetch_prices,
                                    max_pages=max_pages)

        else:
            return {"status": "error", "message": f"Unknown mode: {mode}"}

    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
            "antigens": scraper.total_antigens,
            "products": scraper.total_products,
        }

    return {
        "status": "ok",
        "mode": mode,
        "antigens": scraper.total_antigens,
        "products": scraper.total_products,
    }
