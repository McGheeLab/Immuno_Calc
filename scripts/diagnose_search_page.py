#!/usr/bin/env python3
"""
scripts/diagnose_search_page.py — Inspect what Biocompare actually renders.

Run from your project root:
    python scripts/diagnose_search_page.py

What it does:
    1. Opens Chrome (visible) to a Biocompare direct-search URL
    2. Takes screenshots at 3s, 8s, 15s, 25s after load
    3. Saves the full page HTML at each interval
    4. Analyzes the HTML for known + unknown product container patterns
    5. Dumps all CSS classes, IDs, and data-* attributes found
    6. Prints a summary of what it found

Output goes to:  debug/diagnose/
Share the output with Claude to update parse_product_listing().
"""

import os
import re
import sys
import time
from datetime import datetime

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  EDIT THESE                                                              ║
# ╚═══════════════════════════════════════════════════════════════════════════╝

# The exact URL you tested that "goes to the correct page"
TEST_URL = "https://www.biocompare.com/Search-Antibodies/?search=ki67&said=0&soids=269752&vids=100436"

# Alternative: build it from parts
# from core.biocompare_scraper import build_search_url
# TEST_URL = build_search_url("primary", ["100436"], search_term="ki67")

HEADLESS = False  # Keep False so you can watch what happens


# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  DON'T EDIT BELOW HERE                                                  ║
# ╚═══════════════════════════════════════════════════════════════════════════╝

def setup_chrome(headless: bool = False):
    """Launch Chrome with standard settings."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1400,900")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    try:
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
    except ImportError:
        driver = webdriver.Chrome(options=options)

    driver.implicitly_wait(10)
    driver.set_page_load_timeout(30)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


def save_snapshot(driver, out_dir: str, label: str):
    """Save screenshot + HTML at this moment."""
    driver.save_screenshot(os.path.join(out_dir, f"{label}.png"))
    html = driver.page_source
    with open(os.path.join(out_dir, f"{label}.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"    📸 {label}.png + .html  ({len(html):,} chars)")
    return html


def analyze_html(html: str, label: str):
    """Analyze the HTML to find product-related elements."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    body_text = soup.get_text(separator=" ", strip=True)

    print(f"\n{'─' * 60}")
    print(f"  ANALYSIS: {label}")
    print(f"{'─' * 60}")

    # ── 1. Check known selectors from parse_product_listing ──
    print("\n  🔍 Known product card selectors:")
    known_selectors = [
        ".product-card",
        ".search-result-item",
        ".product-listing-item",
        ".pfu-product",
        "[data-product-id]",
        ".product-result",
        ".compare-product-container",
        ".product-row",
        ".product-item",
        ".search-result",
        "table.results",
        ".no-results",
        ".product-listing",
    ]
    for sel in known_selectors:
        elements = soup.select(sel)
        if elements:
            print(f"    ✅ {sel:40s} → {len(elements)} elements")
        # Only show misses at debug level

    # ── 2. Find ALL elements with 'product' in class or id ──
    print("\n  🔍 Elements with 'product' in class/id:")
    product_elements = []
    for el in soup.find_all(True):
        classes = " ".join(el.get("class", []))
        el_id = el.get("id", "")
        if "product" in classes.lower() or "product" in el_id.lower():
            tag_info = f"<{el.name}"
            if el_id:
                tag_info += f' id="{el_id}"'
            if classes:
                tag_info += f' class="{classes}"'
            tag_info += ">"
            text_preview = el.get_text(strip=True)[:80]
            product_elements.append((tag_info, text_preview))

    if product_elements:
        for tag, preview in product_elements[:20]:
            print(f"    {tag}")
            if preview:
                print(f"      → {preview}")
    else:
        print("    ❌ None found")

    # ── 3. Find ALL elements with 'result' in class or id ──
    print("\n  🔍 Elements with 'result' or 'search' in class/id:")
    for el in soup.find_all(True):
        classes = " ".join(el.get("class", []))
        el_id = el.get("id", "")
        combined = (classes + " " + el_id).lower()
        if "result" in combined or "search" in combined:
            tag_info = f"<{el.name}"
            if el_id:
                tag_info += f' id="{el_id}"'
            if classes:
                tag_info += f' class="{classes}"'
            tag_info += ">"
            # Only show first 3 lines to avoid spam
            text = el.get_text(strip=True)[:100]
            print(f"    {tag_info}")
            if text:
                print(f"      → {text}")

    # ── 4. Find ALL elements with data-* attributes ──
    print("\n  🔍 Elements with data-* attributes (potential product markers):")
    data_attrs = set()
    for el in soup.find_all(True):
        for attr in el.attrs:
            if attr.startswith("data-"):
                val = str(el[attr])[:50]
                data_attrs.add(f"{attr}={val}")
    for attr in sorted(data_attrs)[:30]:
        print(f"    {attr}")

    # ── 5. Find all tables (Biocompare often uses tables for results) ──
    print("\n  🔍 Tables found:")
    tables = soup.find_all("table")
    for i, table in enumerate(tables):
        table_id = table.get("id", "")
        table_class = " ".join(table.get("class", []))
        rows = table.find_all("tr")
        cells_in_first_row = len(rows[0].find_all(["td", "th"])) if rows else 0
        text_preview = table.get_text(strip=True)[:120]
        print(f"    Table {i}: id='{table_id}' class='{table_class}' rows={len(rows)} cols≈{cells_in_first_row}")
        if text_preview:
            print(f"      → {text_preview}")

    # ── 6. Look for iframes (results might be in an iframe) ──
    print("\n  🔍 Iframes:")
    iframes = soup.find_all("iframe")
    if iframes:
        for iframe in iframes:
            src = iframe.get("src", "no src")
            iframe_id = iframe.get("id", "")
            print(f"    <iframe id='{iframe_id}' src='{src[:100]}'>")
    else:
        print("    None")

    # ── 7. Check for key text patterns ──
    print("\n  🔍 Key text patterns in page:")
    patterns = {
        "Antibody/antibodies": r'(?i)antibod',
        "Applications:": r'Applications\s*:',
        "Reactivity:": r'Reactivity\s*:',
        "Price / $": r'\$\d+',
        "Catalog #": r'(?i)catalog\s*(number|#|no)',
        "No results": r'(?i)no\s+(results|products|items)\s+found',
        "Showing X results": r'(?i)showing\s+\d+',
        "results for": r'(?i)results?\s+for',
        "Loading...": r'(?i)loading',
        "Please wait": r'(?i)please\s+wait',
    }
    for label, pattern in patterns.items():
        matches = re.findall(pattern, body_text)
        if matches:
            print(f"    ✅ '{label}' found ({len(matches)} matches)")
            # Show first match in context
            m = re.search(pattern, body_text)
            if m:
                start = max(0, m.start() - 30)
                end = min(len(body_text), m.end() + 60)
                context = body_text[start:end].replace("\n", " ")
                print(f"       Context: ...{context}...")

    # ── 8. Count links that look like product links ──
    print("\n  🔍 Links containing product-like URLs:")
    product_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(kw in href.lower() for kw in ["/pfu/", "antibod", "product", "catalog"]):
            text = a.get_text(strip=True)[:60]
            product_links.append((href[:100], text))
    print(f"    Found {len(product_links)} product-like links")
    for href, text in product_links[:10]:
        print(f"    → {href}")
        if text:
            print(f"      {text}")

    # ── 9. Page title and meta ──
    title = soup.find("title")
    print(f"\n  📄 Page title: {title.get_text(strip=True) if title else 'N/A'}")

    # ── 10. Count total visible text volume ──
    text_length = len(body_text)
    word_count = len(body_text.split())
    print(f"  📄 Page text: {text_length:,} chars, ~{word_count:,} words")

    # ── 11. Dump first 200 unique CSS classes for manual inspection ──
    all_classes = set()
    for el in soup.find_all(True, class_=True):
        for cls in el.get("class", []):
            all_classes.add(cls)
    classes_sorted = sorted(all_classes)
    class_file = os.path.join(out_dir, f"{label}_classes.txt")
    with open(class_file, "w") as f:
        f.write("\n".join(classes_sorted))
    print(f"\n  💾 All {len(all_classes)} CSS classes saved to {label}_classes.txt")

    return {
        "text_length": text_length,
        "product_links": len(product_links),
        "tables": len(tables),
        "iframes": len(iframes),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    out_dir = os.path.join(project_root, "debug", "diagnose")
    os.makedirs(out_dir, exist_ok=True)

    print("=" * 60)
    print("  Biocompare Search Page Diagnostics")
    print(f"  URL: {TEST_URL}")
    print(f"  Output: {out_dir}/")
    print("=" * 60)

    driver = setup_chrome(headless=HEADLESS)

    try:
        print(f"\n⏳ Loading URL...")
        driver.get(TEST_URL)

        # Snapshot at multiple intervals to see if content loads later
        intervals = [
            (3,  "t03s_initial"),
            (5,  "t08s_early"),
            (7,  "t15s_mid"),
            (10, "t25s_late"),
        ]

        total_waited = 0
        for wait, label in intervals:
            time.sleep(wait)
            total_waited += wait
            print(f"\n  ⏱️  t={total_waited}s — saving {label}...")
            html = save_snapshot(driver, out_dir, label)

        # Also try scrolling to trigger lazy load
        print("\n  📜 Scrolling to bottom...")
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)
        html = save_snapshot(driver, out_dir, "t28s_after_scroll")

        # Try clicking any "Show More" or "Load More" buttons
        from selenium.webdriver.common.by import By
        for btn_text in ["Show More", "Load More", "View More", "See All"]:
            try:
                btns = driver.find_elements(By.XPATH, f"//button[contains(text(), '{btn_text}')] | //a[contains(text(), '{btn_text}')]")
                for btn in btns:
                    if btn.is_displayed():
                        print(f"  🖱️  Clicking '{btn_text}' button...")
                        btn.click()
                        time.sleep(3)
                        html = save_snapshot(driver, out_dir, f"after_click_{btn_text.lower().replace(' ', '_')}")
            except Exception:
                pass

        # ── Run analysis on the latest snapshot ──
        final_html = driver.page_source
        with open(os.path.join(out_dir, "final.html"), "w", encoding="utf-8") as f:
            f.write(final_html)

        stats = analyze_html(final_html, "final")

        # ── Summary ──
        print(f"\n{'=' * 60}")
        print("  SUMMARY")
        print(f"{'=' * 60}")
        print(f"  Page text:     {stats['text_length']:,} chars")
        print(f"  Product links: {stats['product_links']}")
        print(f"  Tables:        {stats['tables']}")
        print(f"  Iframes:       {stats['iframes']}")
        print()
        print(f"  Output files in: {out_dir}/")
        print(f"  Share final.html and the analysis above with Claude")
        print(f"  to update the parser selectors.")
        print(f"{'=' * 60}")

        # Keep browser open for manual inspection
        print("\n  Browser is still open. Press Enter to close...")
        input()

    except KeyboardInterrupt:
        print("\n  Interrupted.")
    except Exception as e:
        print(f"\n  ❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        driver.quit()
        print("  Chrome closed.")
