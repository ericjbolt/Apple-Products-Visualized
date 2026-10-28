"""Shared helpers for loading and saving the product dataset."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "products.json"
OUTPUT_DIR = ROOT / "output"

# Tile colors use the six colors of Apple's 1977 rainbow logo, in stripe order.
# Hex values are the widely published equivalents. Apple has never released official
# Pantone numbers for the logo.
RAINBOW = {
    "green": "#61BB46",
    "yellow": "#FDB827",
    "orange": "#F5821F",
    "red": "#E03A3E",
    "purple": "#963D97",
    "blue": "#009DDC",
}

# Each color covers one family of products. Within a family, every product type gets
# its own shade of that color, from darker to lighter in the order listed here.
GROUPS: dict[str, tuple[str, list[str]]] = {
    "iPhone": ("green", ["iPhone", "iPhone Pro", "iPhone SE and e models", "iPhone Air", "iPhone Duo"]),
    "Early Computers and Specialty Products": ("yellow", [
        "Apple I", "Apple II", "Apple III", "Lisa", "LaserWriter", "Newton", "QuickTake",
        "Pippin", "eMate", "iSight"]),
    "TV and Home": ("orange", ["Apple TV", "HomePod", "HomePod mini", "HomeView", "AirPort"]),
    "Apple Watch, iPod, AirPods, and AirTag": ("red", [
        "Apple Watch", "Apple Watch SE", "Apple Watch Ultra", "iPod", "iPod mini", "iPod nano",
        "iPod shuffle", "iPod touch", "iPod Hi-Fi", "AirPods", "AirPods Pro", "AirPods Max", "AirTag"]),
    "iPad and Apple Vision Pro": ("purple", ["iPad", "iPad mini", "iPad Air", "iPad Pro", "Apple Vision Pro"]),
    "Mac and Displays": ("blue", [
        "Macintosh", "PowerBook", "iBook", "MacBook", "MacBook Air", "MacBook Pro", "MacBook Neo",
        "iMac", "iMac Pro", "eMac", "Mac mini", "Mac Studio", "Power Mac", "Mac Pro", "Xserve",
        "Displays"]),
}

# Product lines that roll up into a broader product type.
LINE_TO_TYPE = {
    "Macintosh Portable": "Macintosh", "Quadra": "Macintosh", "Centris": "Macintosh",
    "Power Macintosh": "Power Mac", "iPod classic": "iPod",
    "Cinema Display": "Displays", "Thunderbolt Display": "Displays", "Pro Display XDR": "Displays",
    "Studio Display": "Displays", "Studio Display XDR": "Displays",
    "iPhone SE": "iPhone SE and e models",
}

TYPE_TO_GROUP = {t: g for g, (_, types) in GROUPS.items() for t in types}


def product_type(product: dict) -> str:
    """The specific product type shown in the legend, such as "iPad Air" or "iPod nano"."""
    line, name = product.get("line", ""), product["name"]
    if line == "iPhone":
        if " Pro" in name:
            return "iPhone Pro"
        if re.search(r"\biPhone \d+e\b", name):
            return "iPhone SE and e models"
        return "iPhone"
    return LINE_TO_TYPE.get(line, line)


def product_group(product: dict) -> str:
    """The color family a product belongs to. Unknown types join the specialty family."""
    return TYPE_TO_GROUP.get(product_type(product), "Early Computers and Specialty Products")


def _mix(hex_color: str, toward: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    base = [int(hex_color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return tuple(round(b + (t - b) * amount) for b, t in zip(base, toward))


def type_color(product_type_name: str) -> tuple[int, int, int]:
    """A shade of the family's rainbow color, unique to this product type."""
    group = TYPE_TO_GROUP.get(product_type_name, "Early Computers and Specialty Products")
    color_name, types = GROUPS[group]
    base = RAINBOW[color_name]
    if product_type_name not in types or len(types) == 1:
        return _mix(base, (0, 0, 0), 0)
    position = types.index(product_type_name) / (len(types) - 1)  # 0 = first, 1 = last
    shift = (position - 0.5) * 0.8  # from 40 percent darker to 40 percent lighter
    return _mix(base, (0, 0, 0), -shift) if shift < 0 else _mix(base, (255, 255, 255), shift)


def load_products() -> dict:
    with DATA_FILE.open(encoding="utf-8") as handle:
        return json.load(handle)


def save_products(doc: dict) -> None:
    doc["products"].sort(key=lambda p: p["date"])  # stable sort keeps same-month order
    doc["updated"] = date.today().isoformat()
    with DATA_FILE.open("w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def normalize(text: str) -> str:
    """Lowercase and strip punctuation so 'iPhone 17e' and 'iphone 17E ' compare equal."""
    keep = [ch.lower() if ch.isalnum() else " " for ch in text]
    return " ".join("".join(keep).split())


def fingerprint(name: str) -> str:
    """Loose key so 'AirPods 5' and 'AirPods (5th generation)' count as the same product."""
    text = normalize(name)
    text = re.sub(r"\b(\d+)(st|nd|rd|th)\b", r"\1", text)
    text = re.sub(r"\b(apple|the|generation|gen|model|inch)\b", " ", text)
    return " ".join(text.split())


PHOTO_DIR = ROOT / "assets" / "photos"


def slug(name: str) -> str:
    return normalize(name).replace(" ", "-")


def photo_path(product: dict) -> Path:
    """Where the downloaded photo for a product lives (it may not exist yet)."""
    return PHOTO_DIR / f"{slug(product['name'])}.jpg"
