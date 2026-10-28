"""Shared helpers for loading and saving the product dataset."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "products.json"
OUTPUT_DIR = ROOT / "output"

# Display order and colors for each product category.
CATEGORIES: dict[str, str] = {
    "Apple I, II, III & Lisa": "#c9a227",
    "Mac": "#4f8ef7",
    "Displays": "#7fa7c4",
    "iPod": "#d94f8c",
    "iPhone": "#4cc38a",
    "iPad": "#8f6ff0",
    "Apple Watch": "#e5484d",
    "AirPods": "#e6e6ea",
    "TV & Home": "#e0763c",
    "Apple Vision Pro": "#38bdc9",
    "Other": "#9a8f86",
}


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
