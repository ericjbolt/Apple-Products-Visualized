"""Add newly released products from Wikipedia's "Timeline of Apple Inc. products".

The historical list in data/products.json is curated by hand. This script only
looks for products released after the newest month already in the file, so it
never rewrites history. New rows are flagged with "needs_review" so they can be
checked in the weekly pull request.

Usage:
    python scripts/fetch_all_time.py            # update data/products.json
    python scripts/fetch_all_time.py --dry-run  # show changes only
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from io import StringIO

import pandas as pd
import requests

from common import fingerprint, load_products, save_products

API = ("https://en.wikipedia.org/w/api.php?action=parse&format=json&prop=text"
       "&formatversion=2&page=Timeline_of_Apple_Inc._products")
HEADERS = {"User-Agent": "apple-products-visualized/1.0 (+https://github.com/ericjbolt/Apple-Products-Visualized)"}

ACCESSORIES = re.compile(
    r"keyboard|mouse|trackpad|pencil|cable|adapter|charger|remote|case|cover|band|"
    r"magsafe|battery|beats|power ?beats|software|\bos\b|service|dock|stand|mount",
    re.IGNORECASE,
)

CATEGORY_KEYWORDS = [
    ("iPhone", "iPhone"), ("iPad", "iPad"), ("Watch", "Apple Watch"), ("AirPods", "AirPods"),
    ("Display", "Displays"), ("Vision", "Apple Vision Pro"), ("iPod", "iPod"),
    ("HomePod", "TV & Home"), ("Apple TV", "TV & Home"), ("Home", "TV & Home"),
    ("AirPort", "TV & Home"), ("Mac", "Mac"), ("AirTag", "Other"),
]


def categorize(*texts: str) -> str:
    joined = " ".join(t for t in texts if t)
    for keyword, category in CATEGORY_KEYWORDS:
        if keyword.lower() in joined.lower():
            return category
    return "Other"


def find_column(columns, *needles):
    for col in columns:
        label = " ".join(map(str, col)) if isinstance(col, tuple) else str(col)
        if any(n in label.lower() for n in needles):
            return col
    return None


def parse_tables(html: str) -> list[dict]:
    """Pull (name, family, month) rows out of every timeline table that has them."""
    rows = []
    for table in pd.read_html(StringIO(html)):
        date_col = find_column(table.columns, "release", "date", "announced")
        name_col = find_column(table.columns, "model", "product", "name")
        family_col = find_column(table.columns, "family", "line", "type")
        if date_col is None or name_col is None:
            continue
        for _, row in table.iterrows():
            when = pd.to_datetime(str(row[date_col]), errors="coerce")
            name = str(row[name_col]).strip()
            if pd.isna(when) or not name or name.lower() == "nan":
                continue
            name = re.sub(r"\[\w+\]", "", name).strip()  # drop footnote markers
            family = str(row[family_col]) if family_col is not None else ""
            rows.append({"name": name, "family": family, "month": when.strftime("%Y-%m")})
    return rows


def apply(doc: dict, rows: list[dict]) -> list[str]:
    products = doc["products"]
    newest_month = max(p["date"] for p in products)
    known = {fingerprint(p["name"]) for p in products}
    known |= {fingerprint(p["apple_name"]) for p in products if p.get("apple_name")}
    changes = []
    for row in rows:
        if row["month"] <= newest_month or ACCESSORIES.search(row["name"]):
            continue
        key = fingerprint(row["name"])
        if not key or key in known:
            continue
        known.add(key)
        category = categorize(row["family"], row["name"])
        products.append({
            "name": row["name"], "line": row["family"] or row["name"], "category": category,
            "date": row["month"], "current": False, "needs_review": True,
        })
        changes.append(f"New product added: {row['name']} ({category}, {row['month']})")
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    response = requests.get(API, headers=HEADERS, timeout=30)
    response.raise_for_status()
    rows = parse_tables(response.json()["parse"]["text"])
    if not rows:
        print("No timeline rows found. The Wikipedia page layout may have changed.",
              file=sys.stderr)
        return 1

    doc = load_products()
    changes = apply(doc, rows)
    report = "\n".join(f"- {c}" for c in changes) or "- No new releases."
    print(f"Read {len(rows)} timeline rows.\n{report}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"### Wikipedia timeline\n{report}\n")

    if changes and not args.dry_run:
        save_products(doc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
