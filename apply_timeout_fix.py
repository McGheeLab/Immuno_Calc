#!/usr/bin/env python3
"""
apply_timeout_fix.py — Fix the page load timeout in search_biocompare().

Run from your project root:
    python apply_timeout_fix.py

Problem:
    driver.get(url) waits for ALL resources (images, ads, trackers) to finish
    loading. The direct search URL is heavier than the old empty search page,
    so it exceeds PAGE_TIMEOUT (30s). When it times out, the except block
    returns "" and run_targets_scrape sees no HTML.

Fix:
    1. Set page_load_strategy = "eager" in ChromeBrowser.start() so Selenium
       only waits for DOMContentLoaded, not every resource.
    2. Catch TimeoutException in search_biocompare() as a safety net — if the
       page still times out, grab whatever has rendered instead of returning "".
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

    backup = filepath + ".pre_timeout_fix.bak"
    shutil.copy2(filepath, backup)
    print(f"  💾 Backup: {backup}")

    # ──────────────────────────────────────────────────────────────────────
    # FIX 1: Add page_load_strategy = "eager" in ChromeBrowser.start()
    #
    # This tells Selenium to consider the page "loaded" once DOMContentLoaded
    # fires, rather than waiting for every image/ad/tracker to finish.
    # The product data is in the DOM long before all resources finish.
    # ──────────────────────────────────────────────────────────────────────
    OLD_1 = '''        options = Options()

        if self.headless:
            options.add_argument("--headless=new")'''

    NEW_1 = '''        options = Options()

        # Don't wait for all resources (images, ads, trackers) to load.
        # "eager" = ready after DOMContentLoaded, which is enough for scraping.
        options.page_load_strategy = "eager"

        if self.headless:
            options.add_argument("--headless=new")'''

    content = replace_once(content, OLD_1, NEW_1, "FIX 1: page_load_strategy = eager")

    # ──────────────────────────────────────────────────────────────────────
    # FIX 2: Catch TimeoutException in search_biocompare() so a slow page
    # load doesn't kill the whole scrape — we grab whatever has rendered.
    # ──────────────────────────────────────────────────────────────────────
    OLD_2 = '''        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        # Build the full URL with search term included'''

    NEW_2 = '''        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import TimeoutException

        # Build the full URL with search term included'''

    content = replace_once(content, OLD_2, NEW_2, "FIX 2a: import TimeoutException")

    OLD_3 = '''            # ── Navigate directly to search results ───────────────────
            logger.info(f"  Navigating to: {target_url}")
            self.driver.get(target_url)
            time.sleep(wait_seconds + 2)  # Let ASP.NET page render'''

    NEW_3 = '''            # ── Navigate directly to search results ───────────────────
            logger.info(f"  Navigating to: {target_url}")
            try:
                self.driver.get(target_url)
            except TimeoutException:
                logger.warning("  Page load timed out — continuing with partial page")
            time.sleep(wait_seconds + 2)  # Let remaining JS render'''

    content = replace_once(content, OLD_3, NEW_3, "FIX 2b: catch TimeoutException on driver.get()")

    # ── Write ─────────────────────────────────────────────────────────────
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return True


def main():
    print("=" * 60)
    print("  Timeout Fix for Biocompare Direct-URL Search")
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
        print("  ✅ Timeout fix applied!")
        print()
        print("  What changed:")
        print("    1. Chrome now uses page_load_strategy='eager'")
        print("       → Only waits for DOM, not images/ads/trackers")
        print("    2. search_biocompare() catches TimeoutException")
        print("       → Uses partial page instead of returning empty")
        print()
        print("  To undo: rename .pre_timeout_fix.bak back")
        print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
