#!/usr/bin/env python3
"""
scripts/run_scrape.py — Run the Biocompare scraper from VS Code.

Just edit the variables below and hit Run (▶️) or F5.
No command-line arguments needed.
"""

import logging
import os
import sys

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from core.biocompare_scraper import run_scrape


# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  EDIT THESE VARIABLES, THEN RUN THE FILE                                ║
# ╚═══════════════════════════════════════════════════════════════════════════╝

# ── Mode (pick one, comment out the rest) ──
MODE = "test"               # Quick test — 3 antigens from letter R
# MODE = "targets"          # Scrape specific antigens by name
# MODE = "letter"           # Scrape one letter of the alphabet
# MODE = "full"             # Full A-Z scrape (takes hours)
# MODE = "url"              # Scrape a single listing URL
# MODE = "stats"            # Just print catalog stats (no Chrome)
# MODE = "export_csv"       # Export catalog to CSV (no Chrome)

# ── Targets (used when MODE = "targets") ──
# Opens biocompare.com/Antibodies/, types each name into the search bar, parses results
TARGETS = ["CD3", "Ki67", "GFAP"]

# ── Letter (used when MODE = "letter") ──
LETTER = "R"

# ── URL (used when MODE = "url") ──
URL = ""
ANTIGEN_NAME = ""           # Optional — auto-detected from URL if blank

# ── Scraper options ──
HEADLESS = False            # True = hide Chrome window
INTERACTIVE = False         # True = pause at each page for manual inspection
DEEP = False                # True = also fetch product detail pages (slower)
MAX_PAGES = 5               # Max listing pages per antigen
MAX_ANTIGENS = 3            # Max antigens for test mode

# ── Logging ──
VERBOSE = False             # True = debug-level logging

# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  DON'T EDIT BELOW HERE                                                  ║
# ╚═══════════════════════════════════════════════════════════════════════════╝

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG if VERBOSE else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    print(f"\n{'=' * 60}")
    print(f"  Biocompare Scraper — mode: {MODE}")
    if MODE not in ("stats", "export_csv"):
        print(f"  Chrome:      {'Headless' if HEADLESS else 'Visible window'}")
        print(f"  Interactive: {'Yes' if INTERACTIVE else 'No'}")
        print(f"  Deep scrape: {'Yes' if DEEP else 'No'}")
    if MODE == "targets":
        print(f"  Targets:     {', '.join(TARGETS)}")
    if MODE == "letter":
        print(f"  Letter:      {LETTER}")
    print(f"{'=' * 60}\n")

    result = run_scrape(
        mode=MODE,
        letter=LETTER,
        targets=TARGETS,
        url=URL,
        antigen_name=ANTIGEN_NAME,
        headless=HEADLESS,
        interactive=INTERACTIVE,
        deep=DEEP,
        max_pages=MAX_PAGES,
        max_antigens=MAX_ANTIGENS,
    )

    # ── Print results ──
    print(f"\n{'─' * 40}")
    if result["status"] == "ok":
        if MODE == "stats":
            print(f"  Antigens:  {result.get('antigen_count', 0):,}")
            print(f"  Products:  {result.get('product_count', 0):,}")
            print(f"  Vendors:   {result.get('vendor_count', 0):,}")
            if result.get("last_scrape"):
                ls = result["last_scrape"]
                print(f"  Last run:  {ls.get('started_at', 'N/A')}")
                print(f"  Status:    {ls.get('status', 'N/A')}")
        elif MODE == "export_csv":
            print(f"  Exported to: {result.get('path')}")
        elif MODE == "targets":
            print(f"  Total: {result.get('antigens', 0)} antigens, {result.get('products', 0)} products")
            for t, info in result.get("per_target", {}).items():
                icon = "✅" if info["status"] == "ok" else "⚠️"
                print(f"  {icon} {t}: {info['products']} products ({info['status']})")
        else:
            print(f"  ✅ {result.get('antigens', 0)} antigens, {result.get('products', 0)} products")
    else:
        print(f"  ❌ Error: {result.get('message', 'unknown')}")
    print(f"{'─' * 40}\n")
