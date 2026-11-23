"""One-time, recorded edit: re-point the landing page's six broken Shop links to the integrated catalogue.

Exact string replacement with asserted counts (the landing HTML is minified; no parser or reformatting is used, so
nothing else in the files can change). Safe to re-run: when already applied it reports 0 remaining and changes nothing.

    python -I tools/repoint_landing.py
"""
import sys
from pathlib import Path

LANDING = Path(__file__).resolve().parent.parent / "landing"
OLD = 'href="https://better-style-shop.vercel.app/shop/"'
EDITS = [  # file, replacement href, expected occurrences
    ("index.html", 'href="catalogue/"', 3),
    ("item/index.html", 'href="../catalogue/"', 2),
    ("apply/index.html", 'href="../catalogue/"', 1),
]
README_OLD = "- Storefront (separate repo, `better-style-shop`): https://better-style-shop.vercel.app/shop/"
README_NEW = "- Store: /catalogue/ (the SRC Catalogue, same site)"


def main():
    total = 0
    for rel, new, n in EDITS:
        p = LANDING / rel
        raw = p.read_bytes()
        text = raw.decode("utf-8")
        have = text.count(OLD)
        if have == 0 and text.count(new) >= n:
            print(f"{rel}: already re-pointed")
            continue
        if have != n:
            sys.exit(f"{rel}: expected {n} occurrence(s) of {OLD}, found {have}. Stopping without changes.")
        p.write_bytes(text.replace(OLD, new).encode("utf-8"))
        total += n
        print(f"{rel}: replaced {n}")
    rp = LANDING / "README.md"
    t = rp.read_bytes().decode("utf-8")
    if README_OLD in t:
        rp.write_bytes(t.replace(README_OLD, README_NEW).encode("utf-8"))
        print("README.md: storefront line updated")
    print(f"total href replacements: {total}")


if __name__ == "__main__":
    main()
