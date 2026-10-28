"""Sync the 'current' flags with the products listed on apple.com.

Each apple.com category page has a product strip near the top (the "chapter
navigation"). This script reads those strips, marks matching products as current,
marks missing products as no longer current, and adds any new product it has not
seen before (flagged for review).

Only Apple hardware categories are read, so the Apple Store accessories section
and third-party products are never included.

Usage:
    python scripts/fetch_current.py            # update data/products.json
    python scripts/fetch_current.py --dry-run  # show changes only
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date

import requests
from bs4 import BeautifulSoup

from common import fingerprint, load_products, normalize, save_products

CATEGORY_PAGES = {
    "Mac": "https://www.apple.com/mac/",
    "iPad": "https://www.apple.com/ipad/",
    "iPhone": "https://www.apple.com/iphone/",
    "Apple Watch": "https://www.apple.com/watch/",
    "AirPods": "https://www.apple.com/airpods/",
    "TV & Home": "https://www.apple.com/tv-home/",
}

# Single-product pages: the product is current if its page loads.
SINGLE_PAGES = {
    "Apple Vision Pro": ("Apple Vision Pro", "https://www.apple.com/apple-vision-pro/"),
    "AirTag": ("Other", "https://www.apple.com/airtag/"),
}

# Strip items that are not hardware products.
EXCLUDE = re.compile(
    r"compare|accessor|shop|store|why |switch|help|support|trade|"
    r"\bmacos\b|\bios\b|\bipados\b|\bwatchos\b|\btvos\b|\bvisionos\b|"
    r"apple tv app|apple tv\+|apple music|apple one|apple arcade|fitness|"
    r"home app|^home$|siri|airplay|apps|family|business|education|band|"
    r"watch studio|herm[eè]s|nike|beats|"
    r"pencil|keyboard|mouse|trackpad|remote|case|cover|cable|adapter|charger",
    re.IGNORECASE,
)

MINIMUM_EXPECTED = 15  # fewer labels than this means the page layout changed
HEADERS = {"User-Agent": "apple-products-visualized/1.0 (+https://github.com/ericjbolt/Apple-Products-Visualized)"}


def parse_strip(html: str) -> list[str]:
    """Return the product names in an apple.com chapter-navigation strip."""
    soup = BeautifulSoup(html, "html.parser")
    labels = []
    for node in soup.select('[class*="chapternav-label"]'):
        text = " ".join(node.get_text(" ", strip=True).split())
        text = re.sub(r"\s*(New|Nuevo)$", "", text)  # drop "New" badges
        if text and not EXCLUDE.search(text) and text not in labels:
            labels.append(text)
    return labels


def guess_category(label: str, page_category: str) -> str:
    if "display" in label.lower():
        return "Displays"
    return page_category


def scrape() -> dict[str, str]:
    """Map each product label found on apple.com to its category."""
    found: dict[str, str] = {}
    session = requests.Session()
    session.headers.update(HEADERS)
    for category, url in CATEGORY_PAGES.items():
        response = session.get(url, timeout=30)
        response.raise_for_status()
        for label in parse_strip(response.text):
            found.setdefault(label, guess_category(label, category))
    for label, (category, url) in SINGLE_PAGES.items():
        if session.get(url, timeout=30).status_code == 200:
            found.setdefault(label, category)
    return found


def apply(doc: dict, found: dict[str, str]) -> list[str]:
    """Update current flags in place and return a human-readable change log."""
    products = doc["products"]
    labels = {normalize(label): label for label in found}
    changes: list[str] = []
    claimed: set[str] = set()

    # 1. Exact matches on apple_name.
    for p in products:
        if p.get("apple_name") and normalize(p["apple_name"]) in labels:
            claimed.add(normalize(p["apple_name"]))

    # 2. Labels that name a product line (for example "MacBook Air").
    line_hits: set[str] = set()
    for key in labels:
        if key in claimed:
            continue
        in_line = [p for p in products if normalize(p["line"]) == key]
        if in_line:
            claimed.add(key)
            line_hits.add(key)
            if not any(p.get("current") for p in in_line):
                newest = max(in_line, key=lambda p: p["date"])
                newest["current"] = True
                changes.append(f"Now current: {newest['name']}")

    # 3. Recompute every flag.
    for p in products:
        before = bool(p.get("current"))
        if p.get("apple_name"):
            after = normalize(p["apple_name"]) in labels
        else:
            after = before and normalize(p["line"]) in line_hits
        if before != after:
            p["current"] = after
            if after:
                p.pop("retired", None)
            else:
                p["retired"] = date.today().strftime("%Y-%m")  # moves it into "thank you"
            changes.append(("Now current: " if after else "No longer current: ") + p["name"])

    # 4. Anything left is either a product another script already added under a
    #    slightly different name, or a brand-new product.
    month = date.today().strftime("%Y-%m")
    by_fingerprint = {fingerprint(p["name"]): p for p in products}
    for key, label in labels.items():
        if key in claimed:
            continue
        existing = by_fingerprint.get(fingerprint(label))
        if existing:
            existing["apple_name"] = label
            if not existing.get("current"):
                existing["current"] = True
                changes.append(f"Now current: {existing['name']}")
        else:
            products.append({
                "name": label, "line": label, "category": found[label], "date": month,
                "current": True, "apple_name": label, "needs_review": True,
            })
            changes.append(f"New product added: {label} ({found[label]})")
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    found = scrape()
    if len(found) < MINIMUM_EXPECTED:
        print(f"Only {len(found)} products found on apple.com. The page layout may have "
              "changed, so no changes were written.", file=sys.stderr)
        return 1

    doc = load_products()
    changes = apply(doc, found)
    report = "\n".join(f"- {c}" for c in changes) or "- No lineup changes."
    print(f"Found {len(found)} products on apple.com.\n{report}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"### apple.com lineup\n{report}\n")

    if changes and not args.dry_run:
        save_products(doc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
