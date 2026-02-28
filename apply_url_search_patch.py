#!/usr/bin/env python3
"""
apply_url_search_patch.py — Auto-apply the direct-URL search patches.

Run from your project root:
    python apply_url_search_patch.py

What it does:
    1. Opens core/biocompare_scraper.py and applies 4 patches
    2. Opens scripts/run_scrape.py and applies 1 patch
    3. Creates .bak backups of both files before modifying

KEY CHANGE: Instead of navigating to a filtered Biocompare page and
typing into the ASP.NET search bar (fragile DOM selectors), we now
build the complete search URL with the search term as a query parameter:

    /Search-Antibodies/?search=ki67&said=0&soids=269752&vids=100436
"""

import os
import shutil
import sys


def find_project_root() -> str:
    """Find project root by looking for core/biocompare_scraper.py."""
    # Try current directory first
    if os.path.isfile(os.path.join("core", "biocompare_scraper.py")):
        return os.getcwd()
    # Try script's parent directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if os.path.isfile(os.path.join(script_dir, "core", "biocompare_scraper.py")):
        return script_dir
    # Try one level up from script
    parent = os.path.dirname(script_dir)
    if os.path.isfile(os.path.join(parent, "core", "biocompare_scraper.py")):
        return parent
    return ""


def replace_once(content: str, old: str, new: str, label: str) -> str:
    """Replace exactly one occurrence of `old` with `new` in `content`."""
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


def patch_biocompare_scraper(filepath: str) -> bool:
    """Apply patches 1-4 to core/biocompare_scraper.py."""
    print(f"\n📄 Patching: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Back up
    backup = filepath + ".bak"
    shutil.copy2(filepath, backup)
    print(f"  💾 Backup: {backup}")

    patches_applied = 0

    # ──────────────────────────────────────────────────────────────────────
    # PATCH 1: Add "Direct search" line to URL STRUCTURE docstring
    # ──────────────────────────────────────────────────────────────────────
    OLD_1 = (
        "    Product listing: /pfu/110447/soids/{ID}/Antibodies/{ANTIGEN}\n"
        "    Product detail:  individual product page (linked from listing)\n"
        '"""'
    )
    NEW_1 = (
        "    Product listing: /pfu/110447/soids/{ID}/Antibodies/{ANTIGEN}\n"
        "    Product detail:  individual product page (linked from listing)\n"
        "    Direct search:   /Search-Antibodies/?search={QUERY}&said=0&soids={TYPE_ID}&vids={VENDOR_IDS}\n"
        "                     (used by run_targets_scrape — no DOM interaction needed)\n"
        '"""'
    )
    content = replace_once(content, OLD_1, NEW_1, "PATCH 1: docstring URL STRUCTURE")
    patches_applied += 1

    # ──────────────────────────────────────────────────────────────────────
    # PATCH 2: Replace build_search_url() function
    # ──────────────────────────────────────────────────────────────────────
    OLD_2 = '''def build_search_url(
    antibody_type: str = "",
    vendor_vids: list[str] = None,
) -> str:
    """
    Build a Biocompare filtered search URL.

    Examples:
        build_search_url("primary")
        → https://www.biocompare.com/Search-Antibodies/?said=0&soids=269752
        build_search_url("secondary", ["100436", "100041"])
        → https://www.biocompare.com/Search-Antibodies/?said=0&soids=218589&vids=100436,100041
    """
    params = ["said=0"]
    if antibody_type and antibody_type in ANTIBODY_TYPE_SOIDS:
        params.append(f"soids={ANTIBODY_TYPE_SOIDS[antibody_type]}")
    if vendor_vids:
        params.append(f"vids={','.join(vendor_vids)}")
    return f"{SEARCH_ANTIBODIES_URL}?{'&'.join(params)}"'''

    NEW_2 = '''def build_search_url(
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
    return f"{SEARCH_ANTIBODIES_URL}?{'&'.join(params)}"'''

    content = replace_once(content, OLD_2, NEW_2, "PATCH 2: build_search_url()")
    patches_applied += 1

    # ──────────────────────────────────────────────────────────────────────
    # PATCH 3: Replace ChromeBrowser.search_biocompare() method
    #
    # We match from the method signature through to the end (return ""),
    # right before _save_debug_state.
    # ──────────────────────────────────────────────────────────────────────
    OLD_3 = '''    def search_biocompare(
        self,
        query: str,
        search_url: str = "",
        wait_seconds: float = PAGE_LOAD_DELAY,
    ) -> str:
        """
        Search Biocompare for antibodies. Navigates to the search page,
        types the query into the search bar, clicks Search, returns results.

        Actual page elements (as of 2025):
          Search input:  <input id="ctl06_ctl12_ctl00_txtSearch" class="span2 txtSearch textbox">
          Search button: <input id="ctl06_ctl12_ctl00_btnProductSearch" class="button search btnProductSearch" type="button">
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        target_page = search_url or ANTIBODY_SEARCH_URL

        try:
            # ── Navigate ──────────────────────────────────────────────
            logger.info(f"  Navigating to {target_page}")
            self.driver.get(target_page)
            time.sleep(wait_seconds + 2)  # Extra time for ASP.NET page to render

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

            # ── Find search input ─────────────────────────────────────
            # The ID contains ASP.NET control path — use class or ends-with
            search_box = None
            input_selectors = [
                "input.txtSearch",                          # class match
                "input[id$='_txtSearch']",                  # ID ends with _txtSearch
                "#ctl06_ctl12_ctl00_txtSearch",             # exact ID from page
                "input[name$='txtSearch']",                 # name ends with txtSearch
                "#txtSearch",                               # simple ID (other pages)
                "input.textbox[type='text']",               # generic textbox
            ]

            for selector in input_selectors:
                try:
                    search_box = WebDriverWait(self.driver, 3).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                    )
                    if search_box:
                        logger.info(f"  ✅ Found search input: {selector}")
                        break
                except Exception:
                    continue

            if not search_box:
                # Debug: save page source and screenshot
                self._save_debug_state("search_input_not_found")
                logger.error("  ❌ Could not find search input. Debug files saved.")
                return ""

            # Scroll into view and click to focus
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center'}); arguments[0].focus();",
                search_box,
            )
            time.sleep(0.5)

            # Clear and type
            search_box.clear()
            time.sleep(0.3)
            search_box.send_keys(query)
            time.sleep(0.5)
            logger.info(f"  Typed: {query}")

            # ── Find and click Search button ──────────────────────────
            # type="button" means it's JS-driven, not a form submit
            search_btn = None
            btn_selectors = [
                "input.btnProductSearch",                   # class match
                "input[id$='_btnProductSearch']",           # ID ends with
                "#ctl06_ctl12_ctl00_btnProductSearch",      # exact ID from page
                "input[name$='btnProductSearch']",          # name ends with
                "input.button.search",                      # compound class
                "input[value='Search']",                    # by value text
            ]

            for selector in btn_selectors:
                try:
                    search_btn = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if search_btn:
                        logger.info(f"  ✅ Found search button: {selector}")
                        break
                except Exception:
                    continue

            if search_btn:
                # Scroll button into view
                self.driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});", search_btn
                )
                time.sleep(0.3)

                # Try normal click first
                try:
                    search_btn.click()
                    logger.info("  Clicked search button (normal click)")
                except Exception as click_err:
                    # Fallback: JavaScript click (bypasses overlays)
                    logger.info(f"  Normal click failed ({click_err}), trying JS click")
                    self.driver.execute_script("arguments[0].click();", search_btn)
                    logger.info("  Clicked search button (JS click)")
            else:
                # No button found — try Enter key, then try JS to trigger ASP.NET postback
                logger.warning("  ⚠️ Search button not found, trying alternatives")
                try:
                    # Try triggering the ASP.NET __doPostBack directly
                    self.driver.execute_script(
                        "if (typeof __doPostBack === 'function') {"
                        "  __doPostBack('ctl06$ctl12$ctl00$btnProductSearch','');"
                        "}"
                    )
                    logger.info("  Triggered __doPostBack")
                except Exception:
                    search_box.send_keys(Keys.RETURN)
                    logger.info("  Pressed Enter as final fallback")

            # ── Wait for results ──────────────────────────────────────
            logger.info("  Waiting for results to load...")
            time.sleep(wait_seconds + 3)

            # Wait for either product results or "no results" message
            try:
                WebDriverWait(self.driver, 10).until(
                    lambda d: (
                        d.find_elements(By.CSS_SELECTOR, ".product-listing, .search-result, table.results, .no-results, .product-item")
                        or "results" in d.page_source.lower()
                    )
                )
            except Exception:
                logger.info("  No specific results container detected, using page as-is")

            # Scroll to load lazy content
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)

            page_source = self.driver.page_source
            logger.info(f"  Search complete for: {query} ({len(page_source)} chars)")

            # Save debug HTML
            self._save_debug(f"search_results_{query}", page_source)

            return page_source

        except Exception as e:
            logger.error(f"  Search failed for '{query}': {e}")
            self._save_debug_state(f"search_error_{query}")
            return ""'''

    NEW_3 = '''    def search_biocompare(
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

        This avoids all DOM interaction with the ASP.NET search bar, which
        is fragile and breaks when Biocompare changes their page structure.

        URL pattern:
            /Search-Antibodies/?search=ki67&said=0&soids=269752&vids=100436

        Args:
            query: Search term (e.g., "ki67", "CD3", "GFAP")
            search_url: Pre-built full URL (if provided, used as-is).
                        If empty, builds URL from antibody_type + vendor_vids.
            antibody_type: "primary", "secondary", or "pairs" (used if search_url is empty)
            vendor_vids: Vendor filter IDs (used if search_url is empty)
            wait_seconds: Time to wait for page to render

        Returns:
            Page source HTML string, or "" on failure.
        """
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        # Build the full URL with search term included
        if search_url:
            # search_url already has the search term baked in
            target_url = search_url
        else:
            # Build from scratch with search term
            target_url = build_search_url(
                antibody_type=antibody_type,
                vendor_vids=vendor_vids,
                search_term=query,
            )

        try:
            # ── Navigate directly to search results ───────────────────
            logger.info(f"  Navigating to: {target_url}")
            self.driver.get(target_url)
            time.sleep(wait_seconds + 2)  # Let ASP.NET page render

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

            # ── Wait for results to load ──────────────────────────────
            logger.info("  Waiting for results to load...")
            try:
                WebDriverWait(self.driver, 10).until(
                    lambda d: (
                        d.find_elements(By.CSS_SELECTOR,
                            ".product-listing, .search-result, table.results, "
                            ".no-results, .product-item")
                        or "results" in d.page_source.lower()
                    )
                )
            except Exception:
                logger.info("  No specific results container detected, using page as-is")

            # Scroll to load lazy content
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1.5)

            page_source = self.driver.page_source
            logger.info(f"  Search complete for: {query} ({len(page_source)} chars)")

            # Save debug HTML
            self._save_debug(f"search_results_{query}", page_source)

            return page_source

        except Exception as e:
            logger.error(f"  Search failed for '{query}': {e}")
            self._save_debug_state(f"search_error_{query}")
            return ""'''

    content = replace_once(content, OLD_3, NEW_3, "PATCH 3: search_biocompare()")
    patches_applied += 1

    # ──────────────────────────────────────────────────────────────────────
    # PATCH 4: Replace run_targets_scrape() — just the top portion
    # that builds the URL and calls search_biocompare differently.
    #
    # We replace from the docstring down through the search call.
    # The rest of the method (parse, paginate, deep, DB, etc.) is identical.
    # ──────────────────────────────────────────────────────────────────────
    OLD_4 = '''        """
        Scrape specific target antigens by searching Biocompare's antibody page.

        For each target:
          1. Navigates to biocompare.com/Search-Antibodies/ with type + vendor filters
          2. Types the target name into the search bar (#txtSearch)
          3. Submits and parses the results page
          4. Optionally follows pagination and detail links

        Args:
            targets: List of antigen names (e.g., ["CD3", "Ki67", "GFAP"])
            antibody_type: "primary", "secondary", or "pairs"
            vendor_vids: List of Biocompare vendor IDs to filter by
            deep: Also fetch individual product detail pages
            max_pages: Max result pages per target

        Returns:
            dict with per-target results:
            {"CD3": {"products": 12, "status": "ok"}, ...}
        """
        label = ", ".join(t.upper() for t in targets[:5])
        if len(targets) > 5:
            label += f" (+{len(targets)-5} more)"
        run_id = self.db.start_scrape_run(f"TARGETS:{label}")
        logger.info(f"Searching Biocompare for {len(targets)} targets: {label}")
        logger.info(f"  Type: {antibody_type}, Vendors: {vendor_vids or 'all'}")
        self.browser.start()

        # Build the filtered search URL
        filtered_url = build_search_url(antibody_type, vendor_vids)
        logger.info(f"  Search URL: {filtered_url}")

        results_summary = {}

        try:
            for target_name in targets:
                target_name = target_name.strip()
                if not target_name:
                    continue

                logger.info(f"\\n  🔍 Searching: {target_name}")

                # Search using the Biocompare search bar on the filtered page
                html = self.browser.search_biocompare(target_name, search_url=filtered_url)'''

    NEW_4 = '''        """
        Scrape specific target antigens by searching Biocompare directly via URL.

        For each target:
          1. Builds a direct search URL with the target name as a query param
             e.g. /Search-Antibodies/?search=ki67&said=0&soids=269752&vids=100436
          2. Navigates directly to the results page (no DOM search bar interaction)
          3. Parses product listings from the results
          4. Optionally follows pagination and detail links

        Args:
            targets: List of antigen names (e.g., ["CD3", "Ki67", "GFAP"])
            antibody_type: "primary", "secondary", or "pairs"
            vendor_vids: List of Biocompare vendor IDs to filter by
            deep: Also fetch individual product detail pages
            max_pages: Max result pages per target

        Returns:
            dict with per-target results:
            {"CD3": {"products": 12, "status": "ok"}, ...}
        """
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

                logger.info(f"\\n  🔍 Searching: {target_name}")

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
                )'''

    content = replace_once(content, OLD_4, NEW_4, "PATCH 4: run_targets_scrape()")
    patches_applied += 1

    # ── Write ─────────────────────────────────────────────────────────────
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"  📝 Wrote {patches_applied} patches to {filepath}")
    return True


def patch_run_scrape(filepath: str) -> bool:
    """Apply patch 5 to scripts/run_scrape.py."""
    print(f"\n📄 Patching: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    backup = filepath + ".bak"
    shutil.copy2(filepath, backup)
    print(f"  💾 Backup: {backup}")

    OLD_5 = (
        "# ── Targets (used when MODE = \"targets\") ──\n"
        "# Opens biocompare.com/Antibodies/, types each name into the search bar, parses results\n"
        "TARGETS = [\"CD3\", \"Ki67\", \"GFAP\"]"
    )
    NEW_5 = (
        "# ── Targets (used when MODE = \"targets\") ──\n"
        "# Builds a direct search URL per target (e.g. /Search-Antibodies/?search=CD3&said=0&soids=269752)\n"
        "TARGETS = [\"CD3\", \"Ki67\", \"GFAP\"]"
    )

    content = replace_once(content, OLD_5, NEW_5, "PATCH 5: TARGETS comment")

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"  📝 Wrote 1 patch to {filepath}")
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  Biocompare Direct-URL Search Patch")
    print("  Replaces fragile DOM search bar interaction with")
    print("  direct URL navigation: ?search=<term>&said=0&soids=...")
    print("=" * 60)

    root = find_project_root()
    if not root:
        print("\n❌ Could not find project root.")
        print("   Run this script from the if_panel_designer/ directory,")
        print("   or place it inside the project folder.")
        sys.exit(1)

    print(f"\n📁 Project root: {root}")

    scraper_path = os.path.join(root, "core", "biocompare_scraper.py")
    run_scrape_path = os.path.join(root, "scripts", "run_scrape.py")

    # Verify files exist
    for path in [scraper_path, run_scrape_path]:
        if not os.path.isfile(path):
            print(f"\n❌ File not found: {path}")
            sys.exit(1)

    # Apply patches
    ok1 = patch_biocompare_scraper(scraper_path)
    ok2 = patch_run_scrape(run_scrape_path)

    if ok1 and ok2:
        print(f"\n{'=' * 60}")
        print("  ✅ All patches applied successfully!")
        print()
        print("  Files modified:")
        print(f"    • core/biocompare_scraper.py  (4 patches)")
        print(f"    • scripts/run_scrape.py        (1 patch)")
        print()
        print("  Backups created:")
        print(f"    • core/biocompare_scraper.py.bak")
        print(f"    • scripts/run_scrape.py.bak")
        print()
        print("  To undo:  Rename the .bak files back.")
        print(f"{'=' * 60}")
    else:
        print("\n⚠️  Some patches may not have applied. Check output above.")


if __name__ == "__main__":
    main()
