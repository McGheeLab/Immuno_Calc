#!/usr/bin/env python3
"""
test_parser_on_debug_html.py — Test parse_product_listing on saved debug HTML files.

This script loads saved HTML (from the scraper's debug/ folder or from
diagnose_search_page.py's output) and runs the parser to see what it finds.

Run from your project root:
    python test_parser_on_debug_html.py

It will:
  1. Look for saved HTML in debug/, db/scrape_debug/, debug/diagnose/
  2. Run parse_product_listing on each file
  3. Show exactly what each strategy found
"""

import glob
import logging
import os
import sys

# Add project root to path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

# Enable verbose logging so we can see strategy output
logging.basicConfig(level=logging.DEBUG, format="%(message)s")

from core.biocompare_scraper import parse_product_listing


def find_debug_html_files():
    """Find all saved debug HTML files."""
    search_dirs = [
        os.path.join(project_root, "debug"),
        os.path.join(project_root, "debug", "diagnose"),
        os.path.join(project_root, "db", "scrape_debug"),
    ]
    html_files = []
    for d in search_dirs:
        if os.path.isdir(d):
            for f in sorted(glob.glob(os.path.join(d, "*.html"))):
                html_files.append(f)
    return html_files


def test_file(filepath):
    """Run parser on a single HTML file."""
    with open(filepath, "r", encoding="utf-8") as f:
        html = f.read()

    basename = os.path.basename(filepath)
    print(f"\n{'=' * 70}")
    print(f"  FILE: {basename}  ({len(html):,} chars)")
    print(f"{'=' * 70}")

    # Use "TEST" as the antigen name
    products, next_url = parse_product_listing(html, "TEST")

    print(f"\n  RESULTS: {len(products)} products found")
    if next_url:
        print(f"  Next page: {next_url}")

    for i, p in enumerate(products[:5]):
        print(f"\n  Product {i+1}:")
        print(f"    Name:      {p.get('product_name', '')[:70]}")
        print(f"    Vendor:    {p.get('vendor', '')[:50]}")
        print(f"    Price:     ${p.get('price', 0):.2f}")
        print(f"    Apps:      {p.get('applications', '')[:50]}")
        print(f"    Reactivity:{p.get('reactivity', '')[:50]}")
        print(f"    URL:       {p.get('detail_url', '')[:80]}")

    if len(products) > 5:
        print(f"\n  ... and {len(products) - 5} more products")

    return len(products)


def main():
    print("=" * 70)
    print("  Parser Diagnostic Tool")
    print("  Tests parse_product_listing against saved debug HTML")
    print("=" * 70)

    html_files = find_debug_html_files()

    if not html_files:
        print("\n  No debug HTML files found!")
        print("  Looked in: debug/, debug/diagnose/, db/scrape_debug/")
        print("\n  To generate test HTML, run:")
        print("    python scripts/diagnose_search_page.py")
        print("  or do a GUI search (the scraper saves debug HTML automatically)")
        return

    print(f"\n  Found {len(html_files)} HTML files:")
    for f in html_files:
        print(f"    {os.path.relpath(f, project_root)}")

    total_products = 0
    for filepath in html_files:
        count = test_file(filepath)
        total_products += count

    print(f"\n{'=' * 70}")
    print(f"  TOTAL: {total_products} products found across {len(html_files)} files")
    if total_products == 0:
        print("  ⚠️  Still finding 0 products!")
        print("  Share one of the debug HTML files with Claude for further analysis.")
    else:
        print("  ✅ Parser is working!")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
