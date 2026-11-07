"""Sync the 'current' flags with the products sold on apple.com.

Each apple.com category page has a product menu near the top (the "chapter
navigation"). This script collects the product page addresses in those menus, such
as /airpods-pro/ or /macbook-air/, and compares them with the "page" field of each
current product in data/products.json:

  * A current product stays current while its page is still in a menu.
  * When a page disappears, every product on that page is retired with this month's
    date, and its page is cleared so it cannot be revived by mistake later.
  * A product page that no product claims is added as a new product, flagged with
    "needs_review" so it is checked in the weekly pull request.

Pages are matched by address, not by menu label, because Apple's labels change
often ("AirPods" for AirPods 5, "New" badges, family names) while page addresses
stay stable. Only Apple hardware pages are read. Menus link hub pages, such as
/displays/, are followed one level to reach the products inside them.

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

from common import load_products, save_products

SITE = "https://www.apple.com"

CATEGORY_PAGES = {
    "Mac": "/mac/",
    "iPad": "/ipad/",
    "iPhone": "/iphone/",
    "Apple Watch": "/watch/",
    "AirPods": "/airpods/",
    "TV & Home": "/tv-home/",
}

# Hub pages whose own menus list products, such as the Displays page.
HUB_PAGES = {"/displays/": "Displays"}

# Products with no category menu: current while their page loads.
STANDALONE_PAGES = {"/apple-vision-pro/": "Apple Vision Pro", "/airtag/": "Other"}

# Menu links that are not hardware products: comparison and shopping pages,
# operating systems, services, accessories, and special editions of a product
# already listed (Apple Watch Nike and Hermès).
NOT_PRODUCTS = re.compile(
    r"compare|accessor|best-mac|/os/|apple-music|fitness|apple-pencil|keyboards|"
    r"home-app|apple-watch-nike|apple-watch-hermes|bands|switch|trade-in",
    re.IGNORECASE,
)

MINIMUM_EXPECTED = 20  # fewer pages than this means apple.com's layout changed
MAXIMUM_RETIRED_SHARE = 0.25  # retiring more than this in one run is treated as an error
HEADERS = {"User-Agent": "apple-products-visualized/1.0 (+https://github.com/ericjbolt/Apple-Products-Visualized)"}


def normalize_page(href: str) -> str | None:
    """Turn a menu link into a product page address, or None if it is not a product.

    Product pages are single-segment addresses such as /iphone-17/. Older models that
    Apple still sells but no longer gives a page, such as iPhone 16, appear as store
    links with a model at the end (/us/shop/goto/buy_iphone/iphone_16) and count too.
    Plain store links such as "Shop iPhone" (/us/shop/goto/buy_iphone) do not.
    """
    href = (href or "").split("?")[0].split("#")[0]
    if href.startswith(SITE):
        href = href[len(SITE):]
    if not href.startswith("/") or NOT_PRODUCTS.search(href):
        return None
    parts = [p for p in href.split("/") if p]
    if len(parts) == 1:
        return f"/{parts[0]}/"
    if parts[:3] == ["us", "shop", "goto"] and len(parts) == 5 and parts[3].startswith("buy_"):
        return "/" + "/".join(parts)
    return None


def parse_menu(html: str) -> list[tuple[str, str]]:
    """Return (page, label) for each product in an apple.com chapter-navigation menu."""
    soup = BeautifulSoup(html, "html.parser")
    found = []
    for link in soup.select('[class*="chapternav-item"] a'):
        page = normalize_page(link.get("href"))
        if page is None:
            continue
        label_node = link.select_one('[class*="chapternav-label"]') or link
        label = " ".join(label_node.get_text(" ", strip=True).split())
        label = re.sub(r"\s*\b(New|New Colors)$", "", label)
        if page not in (p for p, _ in found):
            found.append((page, label))
    return found


def scrape() -> dict[str, tuple[str, str]]:
    """Map each product page sold on apple.com to (label, category)."""
    session = requests.Session()
    session.headers.update(HEADERS)
    listed: dict[str, tuple[str, str]] = {}

    def read(path: str, category: str) -> None:
        response = session.get(SITE + path, timeout=30)
        response.raise_for_status()
        for page, label in parse_menu(response.text):
            if page in HUB_PAGES:
                read(page, HUB_PAGES[page])
            else:
                listed.setdefault(page, (label, category))

    for category, path in CATEGORY_PAGES.items():
        read(path, category)
    for page, category in STANDALONE_PAGES.items():
        if session.get(SITE + page, timeout=30).status_code == 200:
            listed.setdefault(page, (page.strip("/").replace("-", " "), category))
    return listed


def apply(doc: dict, listed: dict[str, tuple[str, str]]) -> list[str]:
    """Update current flags in place and return a human-readable change log."""
    products = doc["products"]
    month = date.today().strftime("%Y-%m")
    changes: list[str] = []

    claimed = {p.get("page") for p in products if p.get("current") and p.get("page")}
    for p in products:
        if p.get("current") and p.get("page") and p["page"] not in listed:
            p["current"] = False
            p["retired"] = month
            changes.append(f"No longer current: {p['name']}")
            p.pop("page")

    for page, (label, category) in listed.items():
        if page not in claimed:
            products.append({
                "name": label, "line": label, "category": category, "date": month,
                "current": True, "page": page, "needs_review": True,
            })
            changes.append(f"New product added: {label} ({page})")
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    listed = scrape()
    if len(listed) < MINIMUM_EXPECTED:
        print(f"Only {len(listed)} product pages found on apple.com. The site layout may have "
              "changed, so no changes were written.", file=sys.stderr)
        return 1

    doc = load_products()
    current_before = sum(1 for p in doc["products"] if p.get("current"))
    changes = apply(doc, listed)
    retired = sum(1 for c in changes if c.startswith("No longer current"))
    report = "\n".join(f"- {c}" for c in changes) or "- No lineup changes."
    print(f"Found {len(listed)} product pages on apple.com.\n{report}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"### apple.com lineup\n{report}\n")

    if retired > MAXIMUM_RETIRED_SHARE * current_before:
        print(f"{retired} of {current_before} products would be retired at once, which "
              "suggests a site change rather than real discontinuations. No changes were "
              "written.", file=sys.stderr)
        return 1
    if changes and not args.dry_run:
        save_products(doc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
