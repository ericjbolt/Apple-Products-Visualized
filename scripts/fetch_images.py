"""Download a freely licensed photo for each product from Wikipedia / Wikimedia Commons.

For each product the script:
  1. finds the matching Wikipedia article (or uses the "image_file" override in
     data/products.json, which names a specific Commons file),
  2. takes the article's lead image,
  3. checks the image license on Commons and accepts only Creative Commons
     Attribution, Attribution-ShareAlike, CC0, or public domain,
  4. saves a small thumbnail to assets/photos/ and records the author, license,
     and source link in data/photo_credits.json,
  5. rewrites CREDITS.md from those records.

Products that already have a photo are skipped, so reruns are quick.

Usage:
    python scripts/fetch_images.py               # download missing photos
    python scripts/fetch_images.py --refresh     # re-check every product
    python scripts/fetch_images.py --only "iPhone Duo"
"""

from __future__ import annotations

import argparse
import html
import io
import json
import re
import sys
import time

import requests
from PIL import Image

from common import PHOTO_DIR, ROOT, load_products, slug

WIKI_API = "https://en.wikipedia.org/w/api.php"
CREDITS_FILE = ROOT / "data" / "photo_credits.json"
CREDITS_MD = ROOT / "CREDITS.md"
THUMB_WIDTH = 320
HEADERS = {"User-Agent": "apple-products-visualized/1.0 (https://github.com/ericjbolt/Apple-Products-Visualized; bolteric@me.com)"}

FREE_LICENSE = re.compile(r"^(cc[ -]by(-sa)?[ -][\d.]+|cc0|public domain|pd\b)", re.IGNORECASE)
SKIP_FILE = re.compile(r"logo|icon|wordmark|\.svg$|screenshot|diagram|map\b", re.IGNORECASE)


def strip_html(value: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value or "")).split())


def title_candidates(product: dict) -> list[str]:
    """Article titles worth trying, most specific first."""
    name = re.split(r" / | and (?![^()]*\))", product["name"])[0].strip()
    plain = re.sub(r"\s*\(.*?\)", "", name).strip()
    titles = [name, plain, product["line"]]
    seen, result = set(), []
    for title in titles:
        if title and title.lower() not in seen:
            seen.add(title.lower())
            result.append(title)
    return result


class Wiki:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def get(self, **params):
        params.update(format="json", formatversion=2)
        time.sleep(0.2)  # be polite to Wikimedia
        response = self.session.get(WIKI_API, params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def lead_image(self, title: str) -> str | None:
        """File name of an article's free lead image, following redirects."""
        data = self.get(action="query", titles=title, redirects=1, prop="pageimages",
                        piprop="name", pilicense="free")
        for page in data.get("query", {}).get("pages", []):
            if not page.get("missing") and page.get("pageimage"):
                return page["pageimage"]
        return None

    def search_title(self, product: dict) -> str | None:
        query = f'{re.split(r" / | and (?![^()]*\))", product["name"])[0]} Apple'
        data = self.get(action="query", list="search", srsearch=query, srlimit=1, srnamespace=0)
        hits = data.get("query", {}).get("search", [])
        return hits[0]["title"] if hits else None

    def file_info(self, filename: str) -> dict | None:
        data = self.get(action="query", titles=f"File:{filename}", prop="imageinfo",
                        iiprop="url|extmetadata", iiurlwidth=THUMB_WIDTH)
        pages = data.get("query", {}).get("pages", [])
        if not pages or not pages[0].get("imageinfo"):
            return None
        info = pages[0]["imageinfo"][0]
        meta = info.get("extmetadata", {})
        return {
            "file": filename,
            "thumb_url": info.get("thumburl") or info.get("url"),
            "source": info.get("descriptionurl"),
            "artist": strip_html(meta.get("Artist", {}).get("value", "")) or "Unknown author",
            "license": strip_html(meta.get("LicenseShortName", {}).get("value", "")),
            "license_url": meta.get("LicenseUrl", {}).get("value", ""),
        }

    def download(self, url: str, path) -> None:
        time.sleep(0.2)
        response = self.session.get(url, timeout=60)
        response.raise_for_status()
        # Flatten any transparency onto white and store every photo as a JPEG.
        image = Image.open(io.BytesIO(response.content))
        image = image.convert("RGBA")
        flat = Image.new("RGB", image.size, (255, 255, 255))
        flat.paste(image, mask=image.getchannel("A"))
        flat.thumbnail((THUMB_WIDTH, THUMB_WIDTH))
        flat.save(path, "JPEG", quality=88)


def license_ok(info: dict) -> bool:
    return bool(FREE_LICENSE.search(info.get("license", "")))


def find_photo(wiki: Wiki, product: dict) -> dict | None:
    if product.get("image_file"):
        candidates = [product["image_file"].removeprefix("File:")]
    else:
        candidates = []
        for title in title_candidates(product):
            image = wiki.lead_image(title)
            if image:
                candidates.append(image)
                break
        if not candidates:
            title = wiki.search_title(product)
            image = wiki.lead_image(title) if title else None
            if image:
                candidates.append(image)
    for filename in candidates:
        if SKIP_FILE.search(filename) and not product.get("image_file"):
            continue
        info = wiki.file_info(filename)
        if info and license_ok(info):
            return info
    return None


def write_credits_md(credits: dict, products: list[dict]) -> None:
    names = {slug(p["name"]): p["name"] for p in products}
    lines = [
        "# Photo credits",
        "",
        "Product photos come from Wikimedia Commons and are used under the licenses listed",
        "below. Each photo has been resized. Click a source link for the original file and",
        "its full license terms. Because some photos use Share-Alike licenses, the images in",
        "the `output` folder are released under CC BY-SA 4.0.",
        "",
        "| Product | Photo | Author | License |",
        "| --- | --- | --- | --- |",
    ]
    for key in sorted(credits, key=lambda k: names.get(k, k).lower()):
        c = credits[key]
        artist = c["artist"].replace("|", "/")
        license_text = f"[{c['license']}]({c['license_url']})" if c.get("license_url") else c["license"]
        lines.append(f"| {names.get(key, key)} | [{c['file']}]({c['source']}) | {artist} | {license_text} |")
    CREDITS_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="re-check products that already have photos")
    parser.add_argument("--only", help="process a single product by name")
    args = parser.parse_args()

    products = load_products()["products"]
    credits = json.loads(CREDITS_FILE.read_text(encoding="utf-8")) if CREDITS_FILE.exists() else {}
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    wiki = Wiki()

    added, missing = [], []
    for product in products:
        if args.only and product["name"] != args.only:
            continue
        key = slug(product["name"])
        path = PHOTO_DIR / f"{key}.jpg"
        if key in credits and path.exists() and not args.refresh:
            continue
        try:
            info = find_photo(wiki, product)
            if info:
                wiki.download(info.pop("thumb_url"), path)
                credits[key] = info
                added.append(product["name"])
            else:
                missing.append(product["name"])
        except requests.RequestException as error:
            print(f"Skipped {product['name']}: {error}", file=sys.stderr)
            missing.append(product["name"])

    CREDITS_FILE.write_text(json.dumps(credits, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_credits_md(credits, products)
    print(f"Photos added: {len(added)}. Without a free photo: {len(missing)}.")
    for name in missing:
        print(f"  no photo: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
