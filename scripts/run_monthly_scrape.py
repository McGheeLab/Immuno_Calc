#!/usr/bin/env python3
"""
scripts/run_monthly_scrape.py — CLI for Biocompare catalog scraper.

Thin wrapper around core.biocompare_scraper.run_scrape(). The same function
is called by the Streamlit GUI for on-demand target refreshes.

SETUP:
    pip install selenium webdriver-manager beautifulsoup4 lxml

USAGE:
    python scripts/run_monthly_scrape.py --test
    python scripts/run_monthly_scrape.py --test --interactive
    python scripts/run_monthly_scrape.py --letter R
    python scripts/run_monthly_scrape.py --targets CD3 Ki67 GFAP
    python scripts/run_monthly_scrape.py --url "https://www.biocompare.com/pfu/..."
    python scripts/run_monthly_scrape.py                  # full A-Z
    python scripts/run_monthly_scrape.py --stats
    python scripts/run_monthly_scrape.py --export-csv
    python scripts/run_monthly_scrape.py --letter R --headless --deep
"""

import argparse
import logging
import os
import sys

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)


def main():
    parser = argparse.ArgumentParser(
        description="Biocompare catalog scraper (Selenium + visible Chrome)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # ── Mode selection ──
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--test", action="store_true",
                            help="Quick test: scrape a few antigens from letter R")
    mode_group.add_argument("--letter", type=str,
                            help="Scrape one letter (A-Z)")
    mode_group.add_argument("--targets", nargs="+", type=str,
                            help="Scrape specific antigens by name (e.g., --targets CD3 Ki67)")
    mode_group.add_argument("--url", type=str,
                            help="Scrape a single listing URL")
    mode_group.add_argument("--stats", action="store_true",
                            help="Show catalog statistics")
    mode_group.add_argument("--export-csv", action="store_true",
                            help="Export catalog to CSV")

    # ── Scraper options ──
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="Pause at each page for manual inspection / CAPTCHAs")
    parser.add_argument("--headless", action="store_true",
                        help="Run Chrome without visible window")
    parser.add_argument("--deep", action="store_true",
                        help="Also fetch product detail pages (slower, more data)")
    parser.add_argument("--max-pages", type=int, default=5,
                        help="Max listing pages per antigen (default: 5)")
    parser.add_argument("--max-antigens", type=int, default=3,
                        help="Max antigens for --test mode (default: 3)")
    parser.add_argument("--antigen-name", type=str, default="",
                        help="Antigen name when using --url")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Debug-level logging")

    args = parser.parse_args()

    # ── Logging ──
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # ── Resolve mode ──
    if args.test:
        mode = "test"
    elif args.letter:
        mode = "letter"
    elif args.targets:
        mode = "targets"
    elif args.url:
        mode = "url"
    elif args.stats:
        mode = "stats"
    elif args.export_csv:
        mode = "export_csv"
    else:
        mode = "full"

    # ── Check selenium for browser modes ──
    if mode not in ("stats", "export_csv"):
        try:
            import selenium  # noqa: F401
        except ImportError:
            print("\n❌ Selenium not installed. Run:\n\n    pip install selenium webdriver-manager\n")
            sys.exit(1)

    # ── Print banner ──
    print(f"\n{'=' * 60}")
    print(f"  Biocompare Scraper — mode: {mode}")
    if mode not in ("stats", "export_csv"):
        print(f"  Chrome:      {'Headless' if args.headless else 'Visible window'}")
        print(f"  Interactive: {'Yes' if args.interactive else 'No'}")
        print(f"  Deep scrape: {'Yes' if args.deep else 'No'}")
    print(f"{'=' * 60}\n")

    # ── Call the universal entry point ──
    from core.biocompare_scraper import run_scrape

    result = run_scrape(
        mode=mode,
        letter=args.letter or "R",
        targets=args.targets or [],
        url=args.url or "",
        antigen_name=args.antigen_name,
        headless=args.headless,
        interactive=args.interactive,
        deep=args.deep,
        max_pages=args.max_pages,
        max_antigens=args.max_antigens,
    )

    # ── Print results ──
    print(f"\n{'─' * 40}")
    if result["status"] == "ok":
        if mode == "stats":
            print(f"  Antigens:  {result.get('antigen_count', 0):,}")
            print(f"  Products:  {result.get('product_count', 0):,}")
            print(f"  Vendors:   {result.get('vendor_count', 0):,}")
            if result.get("last_scrape"):
                ls = result["last_scrape"]
                print(f"  Last run:  {ls.get('started_at', 'N/A')}")
                print(f"  Status:    {ls.get('status', 'N/A')}")
        elif mode == "export_csv":
            print(f"  Exported to: {result.get('path')}")
        elif mode == "targets":
            print(f"  Total: {result.get('antigens', 0)} antigens, {result.get('products', 0)} products")
            for t, info in result.get("per_target", {}).items():
                status_icon = "✅" if info["status"] == "ok" else "⚠️"
                print(f"  {status_icon} {t}: {info['products']} products ({info['status']})")
        else:
            print(f"  ✅ {result.get('antigens', 0)} antigens, {result.get('products', 0)} products")
    else:
        print(f"  ❌ Error: {result.get('message', 'unknown')}")
    print(f"{'─' * 40}\n")


if __name__ == "__main__":
    main()
