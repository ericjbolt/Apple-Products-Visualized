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
    "Apple I & II": "#c9a227",
    "Mac": "#4f8ef7",
    "Display": "#7fa7c4",
    "iPod & Music": "#d94f8c",
    "iPhone": "#4cc38a",
    "iPad": "#8f6ff0",
    "Watch": "#e5484d",
    "AirPods": "#e6e6ea",
    "Home & TV": "#e0763c",
    "Vision": "#38bdc9",
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
