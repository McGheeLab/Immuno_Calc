#!/usr/bin/env python3
"""
scripts/run_monthly_scrape.py — Monthly Biocompare Catalog Scraper

Run this once a month to update the local antibody catalog.
The IF Panel Designer app reads from this local catalog for instant search results.

USAGE:
    # Full scrape — all antigens A-Z (may take several hours)
    python scripts/run_monthly_scrape.py

    # Scrape only one letter (useful for testing or partial updates)
    python scripts/run_monthly_scrape.py --letter R

    # Quick test — scrape 3 antigens to verify everything works
    python scripts/run_monthly_scrape.py --test

    # Full scrape + deep detail pages (SLOW — fetches every individual product page)
    python scripts/run_monthly_scrape.py --deep

    # Export catalog to CSV after scraping
    python scripts/run_monthly_scrape.py --letter R --export-csv

    # Just export existing catalog to CSV (no scraping)
    python scripts/run_monthly_scrape.py --export-only

    # Show catalog statistics
    python scripts/run_monthly_scrape.py --stats

NOTES:
    - Results are saved to db/biocompare_catalog.db (SQLite)
    - Rate limited to 2.5-5 seconds between requests
    - Can be interrupted with Ctrl+C and resumed (upsert = no duplicates)
    - Schedule with cron/Task Scheduler for automatic monthly updates
    - CSS selectors may need updating if Biocompare changes their layout

CRON EXAMPLE (1st of each month at 2 AM):
    0 2 1 * * cd /path/to/if_panel_designer && python scripts/run_monthly_scrape.py >> logs/scrape.log 2>&1
"""

import argparse
import logging
import os
import sys

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from core.biocompare_scraper import BiocompareScraper, BiocompareCatalogDB


def main():
    parser = argparse.ArgumentParser(
        description="Monthly Biocompare antibody catalog scraper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--letter", type=str, default=None,
                        help="Scrape only antigens starting with this letter (A-Z)")
    parser.add_argument("--test", action="store_true",
                        help="Quick test: scrape 3 antigens from letter R")
    parser.add_argument("--deep", action="store_true",
                        help="Also fetch individual product detail pages (much slower)")
    parser.add_argument("--max-pages", type=int, default=5,
                        help="Max listing pages to follow per antigen (default: 5)")
    parser.add_argument("--max-antigens", type=int, default=3,
                        help="Max antigens for --test mode (default: 3)")
    parser.add_argument("--export-csv", action="store_true",
                        help="Export catalog to CSV after scraping")
    parser.add_argument("--export-only", action="store_true",
                        help="Only export existing catalog to CSV (no scraping)")
    parser.add_argument("--stats", action="store_true",
                        help="Show catalog statistics and exit")
    parser.add_argument("--db-path", type=str, default=None,
                        help="Path to catalog database (default: db/biocompare_catalog.db)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose logging (DEBUG level)")

    args = parser.parse_args()

    # Setup logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Database path
    db_dir = os.path.join(project_root, "db")
    os.makedirs(db_dir, exist_ok=True)
    db_path = args.db_path or os.path.join(db_dir, "biocompare_catalog.db")

    # ── Stats only ──
    if args.stats:
        db = BiocompareCatalogDB(db_path)
        stats = db.get_stats()
        print(f"\n{'='*50}")
        print(f"  Biocompare Catalog Statistics")
        print(f"{'='*50}")
        print(f"  Antigens:  {stats['antigen_count']:,}")
        print(f"  Products:  {stats['product_count']:,}")
        print(f"  Vendors:   {stats['vendor_count']:,}")
        if stats['last_scrape']:
            ls = stats['last_scrape']
            print(f"  Last run:  {ls.get('started_at', 'N/A')}")
            print(f"  Status:    {ls.get('status', 'N/A')}")
        else:
            print(f"  Last run:  No scrape runs recorded")
        print(f"  DB file:   {db_path}")
        print(f"  DB size:   {os.path.getsize(db_path) / (1024*1024):.1f} MB" if os.path.exists(db_path) else "  DB file does not exist")
        print(f"{'='*50}\n")
        return

    # ── Export only ──
    if args.export_only:
        db = BiocompareCatalogDB(db_path)
        csv_path = os.path.join(project_root, "data", "biocompare_catalog.csv")
        db.export_to_csv(csv_path)
        print(f"Exported catalog to: {csv_path}")
        return

    # ── Scrape ──
    scraper = BiocompareScraper(db_path)

    print(f"\n{'='*60}")
    print(f"  Biocompare Monthly Catalog Scraper")
    print(f"  Database: {db_path}")
    print(f"  Deep scrape: {'Yes' if args.deep else 'No'}")
    print(f"  Max pages/antigen: {args.max_pages}")
    print(f"{'='*60}\n")

    if args.test:
        print("Running TEST scrape (3 antigens from letter R)...")
        scraper.run_test_scrape(max_antigens=args.max_antigens)

    elif args.letter:
        letter = args.letter.upper()
        if letter not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ#":
            print(f"Invalid letter: {letter}. Use A-Z or #")
            sys.exit(1)
        print(f"Scraping letter {letter}...")
        scraper.run_letter_scrape(letter, deep_scrape=args.deep, max_pages=args.max_pages)

    else:
        print("Running FULL scrape (all letters A-Z)...")
        print("This may take several hours. Press Ctrl+C to stop (progress is saved).\n")
        scraper.run_full_scrape(deep_scrape=args.deep, max_pages_per_antigen=args.max_pages)

    # ── Export CSV if requested ──
    if args.export_csv:
        csv_path = os.path.join(project_root, "data", "biocompare_catalog.csv")
        db = BiocompareCatalogDB(db_path)
        db.export_to_csv(csv_path)
        print(f"\nExported catalog to: {csv_path}")

    # Show final stats
    db = BiocompareCatalogDB(db_path)
    stats = db.get_stats()
    print(f"\nCatalog now contains: {stats['antigen_count']:,} antigens, "
          f"{stats['product_count']:,} products from {stats['vendor_count']:,} vendors")


if __name__ == "__main__":
    main()
