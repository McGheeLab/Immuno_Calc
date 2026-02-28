#!/usr/bin/env python3
"""
fix_broken_string.py — Fix unterminated string literals in biocompare_scraper.py.

Run from project root:
    python fix_broken_string.py

Problem:
    The selector fix patcher had \\n inside triple-quoted strings, which
    Python interpreted as actual newlines. So lines like:
        card_text = card.get_text(separator="\\n")
    became two lines:
        card_text = card.get_text(separator="
        ")
"""

import os
import sys


def find_project_root():
    if os.path.isfile(os.path.join("core", "biocompare_scraper.py")):
        return os.getcwd()
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if os.path.isfile(os.path.join(script_dir, "core", "biocompare_scraper.py")):
        return script_dir
    parent = os.path.dirname(script_dir)
    if os.path.isfile(os.path.join(parent, "core", "biocompare_scraper.py")):
        return parent
    return ""


def main():
    root = find_project_root()
    if not root:
        print("❌ Could not find project root.")
        sys.exit(1)

    filepath = os.path.join(root, "core", "biocompare_scraper.py")
    print(f"📄 Fixing: {filepath}")

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    original = content
    fixes = 0

    # The broken patterns are literal newlines where \n should be.
    # We find lines ending with =" and merge with the next line's ")
    #
    # Broken:  separator="\n")   where \n is a REAL newline
    # Fixed:   separator="\n")   where \n is the TWO CHARACTERS \ and n

    lines = content.split("\n")
    fixed_lines = []
    skip_next = False

    for i, line in enumerate(lines):
        if skip_next:
            skip_next = False
            continue

        stripped = line.rstrip()

        # Check if this line ends with an unterminated string: =" or ("
        # and the next line starts with ")
        if (stripped.endswith('="') or stripped.endswith("='")
            or stripped.endswith('("') or stripped.endswith("('")) and i + 1 < len(lines):
            next_stripped = lines[i + 1].strip()
            # Next line should be just ") or ") if ln.strip()] or similar
            if next_stripped.startswith('")') or next_stripped.startswith("')"):
                quote = stripped[-1]  # " or '
                # Reconstruct: this line + \n + rest of next line
                fixed_line = stripped + "\\n" + next_stripped
                fixed_lines.append(fixed_line)
                skip_next = True
                fixes += 1
                print(f"  ✅ Line {i + 1}: merged broken string literal")
                continue

        fixed_lines.append(line)

    if fixes == 0:
        print("  ⚠️  No broken string literals found. May already be fixed.")
    else:
        content = "\n".join(fixed_lines)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"\n  ✅ Fixed {fixes} broken string literal(s)")

    # Verify syntax
    print("\n  Verifying syntax...")
    import py_compile
    try:
        py_compile.compile(filepath, doraise=True)
        print("  ✅ Syntax OK — file is valid Python")
    except py_compile.PyCompileError as e:
        print(f"  ❌ Still has syntax error:\n      {e}")
        print("\n  You may need to manually check the file around the reported line.")


if __name__ == "__main__":
    main()
