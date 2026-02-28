#!/usr/bin/env python3
"""
apply_selector_fix.py — Fix CSS selectors to match actual Biocompare DOM.

Run from your project root:
    python apply_selector_fix.py

Problem:
    Every CSS selector in the parser is wrong. Biocompare uses camelCase
    class names (productRow, productList) but the code looks for
    hyphenated ones (product-row, product-listing). The page loads fine
    with 45 products but the parser finds 0 cards.

    Actual DOM structure (from diagnose_search_page.py):
        div.productList                         ← container
          div.productRow.module                  ← each product
            div.product-review-container
              div.product
                <a href="/9776-Antibodies/...">  ← product name + link
                <span/div with vendor name>      ← e.g. "Abcam"
                Applications: IHC-p, ICC, IF
                Reactivity: Hu, Ms
                Conjugate/Tag: Unconjugated
                Quantity: 100µg
                $475.00
              div.productRatingModule
              div.productActions

Fix:
    1. search_biocompare() wait condition: add .productList, .productRow
    2. parse_product_listing() card selectors: add div.productRow first
    3. _parse_product_card() name: add Biocompare-specific <a> selector
    4. _parse_product_card() vendor: add text-based fallback
"""

import os
import shutil
import sys


def find_project_root() -> str:
    if os.path.isfile(os.path.join("core", "biocompare_scraper.py")):
        return os.getcwd()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if os.path.isfile(os.path.join(script_dir, "core", "biocompare_scraper.py")):
        return script_dir
    parent = os.path.dirname(script_dir)
    if os.path.isfile(os.path.join(parent, "core", "biocompare_scraper.py")):
        return parent
    return ""


def replace_once(content: str, old: str, new: str, label: str) -> str:
    count = content.count(old)
    if count == 0:
        print(f"  ⚠️  SKIP {label}: old text not found (already patched?)")
        return content
    if count > 1:
        print(f"  ❌ ABORT {label}: found {count} occurrences (expected 1). Manual fix needed.")
        sys.exit(1)
    result = content.replace(old, new, 1)
    print(f"  ✅ {label}")
    return result


def patch(filepath: str) -> bool:
    print(f"\n📄 Patching: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    backup = filepath + ".pre_selector_fix.bak"
    shutil.copy2(filepath, backup)
    print(f"  💾 Backup: {backup}")

    # ──────────────────────────────────────────────────────────────────────
    # FIX 1: search_biocompare() wait condition selectors
    #
    # The WebDriverWait checks for elements that don't exist on Biocompare.
    # Add the real selectors so it actually detects when results load.
    # ──────────────────────────────────────────────────────────────────────
    OLD_1 = '''            try:
                WebDriverWait(self.driver, 10).until(
                    lambda d: (
                        d.find_elements(By.CSS_SELECTOR,
                            ".product-listing, .search-result, table.results, "
                            ".no-results, .product-item")
                        or "results" in d.page_source.lower()
                    )
                )
            except Exception:
                logger.info("  No specific results container detected, using page as-is")'''

    NEW_1 = '''            try:
                WebDriverWait(self.driver, 10).until(
                    lambda d: d.find_elements(By.CSS_SELECTOR,
                        ".productList, .productRow, "
                        ".product-listing, .search-result, table.results, "
                        ".no-results, .product-item")
                )
            except Exception:
                logger.info("  No specific results container detected, using page as-is")'''

    content = replace_once(content, OLD_1, NEW_1,
                           "FIX 1: search_biocompare() wait selectors")

    # ──────────────────────────────────────────────────────────────────────
    # FIX 2: parse_product_listing() card selectors
    #
    # The actual Biocompare class is "productRow" (camelCase), not
    # "product-row" (hyphenated). Put the real selectors FIRST so they
    # match before falling through to the wrong ones.
    # ──────────────────────────────────────────────────────────────────────
    OLD_2 = '''    card_selectors = [
        ".product-card",
        ".search-result-item",
        ".product-listing-item",
        ".pfu-product",
        "[data-product-id]",
        ".product-result",
        ".compare-product-container",
        ".product-row",
    ]'''

    NEW_2 = '''    card_selectors = [
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
    ]'''

    content = replace_once(content, OLD_2, NEW_2,
                           "FIX 2: parse_product_listing() card selectors")

    # ──────────────────────────────────────────────────────────────────────
    # FIX 3: _parse_product_card() product name extraction
    #
    # Biocompare product names are in <a> tags whose href contains
    # "/Antibodies/" or "/9776-Antibodies/". Add this as the first
    # selector tried, before generic h3/h4 which may not exist.
    # ──────────────────────────────────────────────────────────────────────
    OLD_3 = '''        # Product name
        name = ""
        for sel in ["h3", "h4", "h5", ".product-name", ".product-title", "strong > a", "a > strong"]:
            el = card.select_one(sel)
            if el:
                name = el.get_text(strip=True)
                break
        if not name:
            first_link = card.select_one("a")
            name = first_link.get_text(strip=True) if first_link else ""
        if not name or len(name) < 3:
            return None'''

    NEW_3 = '''        # Product name — Biocompare puts it in <a> linking to the product page
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
            return None'''

    content = replace_once(content, OLD_3, NEW_3,
                           "FIX 3: _parse_product_card() name extraction")

    # ──────────────────────────────────────────────────────────────────────
    # FIX 4: _parse_product_card() vendor extraction
    #
    # No Biocompare element matches .supplier / .vendor / .company-name.
    # The vendor name (e.g. "Abcam") appears as a text node between the
    # product name link and the "Applications:" label. Add a text-based
    # fallback that extracts it from the card text.
    # ──────────────────────────────────────────────────────────────────────
    OLD_4 = '''        # Vendor
        vendor = ""
        for sel in [".supplier", ".vendor", ".company-name", ".supplier-name"]:
            el = card.select_one(sel)
            if el:
                vendor = el.get_text(strip=True)
                break'''

    NEW_4 = '''        # Vendor — try CSS selectors first, then text-based extraction
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
                    break'''

    content = replace_once(content, OLD_4, NEW_4,
                           "FIX 4: _parse_product_card() vendor extraction")

    # ──────────────────────────────────────────────────────────────────────
    # FIX 5: _parse_product_card() detail_url extraction
    #
    # The current code looks for "/pfu/" in hrefs, but search results use
    # "/9776-Antibodies/" pattern. Add this pattern.
    # ──────────────────────────────────────────────────────────────────────
    OLD_5 = '''        # URLs
        detail_url = ""
        for a in card.select("a[href]"):
            href = a.get("href", "")
            if "/pfu/" in href or "Antibod" in href:
                detail_url = urljoin(BASE_URL, href)
                break'''

    NEW_5 = '''        # URLs
        detail_url = ""
        for a in card.select("a[href]"):
            href = a.get("href", "")
            if "/pfu/" in href or "/Antibodies/" in href or "/antibodies/" in href:
                detail_url = urljoin(BASE_URL, href)
                break'''

    content = replace_once(content, OLD_5, NEW_5,
                           "FIX 5: _parse_product_card() detail_url pattern")

    # ── Write ─────────────────────────────────────────────────────────────
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return True


def main():
    print("=" * 60)
    print("  Biocompare Selector Fix")
    print("  Matches CSS selectors to actual Biocompare DOM")
    print("=" * 60)

    root = find_project_root()
    if not root:
        print("\n❌ Could not find project root.")
        sys.exit(1)

    print(f"\n📁 Project root: {root}")

    filepath = os.path.join(root, "core", "biocompare_scraper.py")
    if not os.path.isfile(filepath):
        print(f"\n❌ File not found: {filepath}")
        sys.exit(1)

    ok = patch(filepath)

    if ok:
        print(f"\n{'=' * 60}")
        print("  ✅ Selector fix applied!")
        print()
        print("  What changed in core/biocompare_scraper.py:")
        print("    1. Wait condition: added .productList, .productRow")
        print("    2. Card selectors: added div.productRow (camelCase)")
        print("    3. Name extraction: Biocompare product <a> href first")
        print("    4. Vendor extraction: text-based fallback added")
        print("    5. Detail URL: added /Antibodies/ href pattern")
        print()
        print("  To undo: rename .pre_selector_fix.bak back")
        print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
