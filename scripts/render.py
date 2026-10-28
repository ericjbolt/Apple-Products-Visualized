"""Render the cursive product mosaics.

Two images are produced, each as a large zoomable JPEG and a smaller PNG preview:

    output/current.jpg / current.png      "current", filled with today's lineup
    output/thank_you.jpg / thank_you.png  "thank you", filled with every past product

Usage:
    python scripts/render.py
"""

from __future__ import annotations

import glob
import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from common import CATEGORIES, OUTPUT_DIR, ROOT, load_products, photo_path

Image.MAX_IMAGE_PIXELS = None  # the full-size images are intentionally large

BACKGROUND = (13, 13, 18)
TEXT = (236, 236, 240)
MUTED = (140, 140, 152)

SCRIPT_FONT = ROOT / "assets" / "fonts" / "Pacifico-Regular.ttf"
WORD_SIZE = 2400          # script font size in pixels for the full-size image
TILE_PER_STROKE = 0.028   # tile size as a share of WORD_SIZE (about 4 to 5 tiles per stroke)
MARGIN = 220
PREVIEW_WIDTH = 1800


# --------------------------------------------------------------------------- fonts

def _font_path(weight: str) -> str | None:
    patterns = [
        f"/usr/share/fonts/**/Inter-{weight}.otf",
        f"/usr/share/fonts/**/Inter-{weight}.ttf",
        "/usr/share/fonts/**/DejaVuSans-Bold.ttf" if weight != "Regular" else "/usr/share/fonts/**/DejaVuSans.ttf",
        "/System/Library/Fonts/SFNS.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for pattern in patterns:
        found = glob.glob(pattern, recursive=True)
        if found:
            return found[0]
    return None


def font(size: int, weight: str = "Regular") -> ImageFont.FreeTypeFont:
    path = _font_path(weight)
    return ImageFont.truetype(path, size) if path else ImageFont.load_default(size)


# --------------------------------------------------------------------------- word shape

def word_mask(word: str, size: int | None = None) -> Image.Image:
    """White-on-black mask of the word in the script font, with a little padding."""
    size = size or WORD_SIZE
    script = ImageFont.truetype(str(SCRIPT_FONT), size)
    left, top, right, bottom = script.getbbox(word)
    pad = int(size * 0.04)
    mask = Image.new("L", (right - left + 2 * pad, bottom - top + 2 * pad), 0)
    ImageDraw.Draw(mask).text((pad - left, pad - top), word, font=script, fill=255)
    return mask


# --------------------------------------------------------------------------- grid

@dataclass
class Cell:
    x: float
    y: float
    size: float


def _coverage(mask: np.ndarray, cell: float, ox: float, oy: float):
    """Grid of candidate cells and the share of each cell that lies inside the mask."""
    h, w = mask.shape
    cols = np.arange(-1, int(w / cell) + 1)
    rows = np.arange(-1, int(h / cell) + 1)
    x0 = ox + cols * cell
    y0 = oy + rows * cell
    samples = (np.arange(4) + 0.5) / 4 * cell
    xs = x0[None, :, None] + samples[None, None, :]
    ys = y0[:, None, None] + samples[None, None, :]
    xi = np.clip(xs.astype(int), 0, w - 1)
    yi = np.clip(ys.astype(int), 0, h - 1)
    inside = mask[yi[:, :, :, None], xi[:, :, None, :]] > 127
    valid = ((xs >= 0) & (xs < w))[:, :, None, :] & ((ys >= 0) & (ys < h))[:, :, :, None]
    return x0, y0, (inside & valid).mean(axis=(2, 3))


OFFSETS = [0.0, 0.25, 0.5, 0.75]


def cells_at(mask: np.ndarray, size: float, threshold: float = 0.5) -> int:
    """Most cells of this size that fit, over a few grid offsets."""
    return max(int((_coverage(mask, size, fx * size, fy * size)[2] >= threshold).sum())
               for fx in OFFSETS for fy in OFFSETS)


def exact_grid(mask: np.ndarray, count: int, threshold: float = 0.5) -> list[Cell]:
    """Exactly `count` square tiles that together fill the whole shape.

    Tries many tile sizes and grid offsets, keeps the largest tile size whose grid has at
    least `count` cells mostly inside the shape, then drops the cells least inside the
    shape, so the shape is always completely full with no empty slots.
    """
    if count <= 0:
        return []
    best = None
    lo, hi = 2.0, min(mask.shape) / 2
    for _ in range(36):
        mid = (lo + hi) / 2
        candidate = None
        for fx in OFFSETS:
            for fy in OFFSETS:
                x0, y0, cov = _coverage(mask, mid, fx * mid, fy * mid)
                n = int((cov >= threshold).sum())
                if n >= count and (candidate is None or n - count < candidate[1]):
                    candidate = (mid, n - count, x0, y0, cov)
        if candidate:
            best, lo = candidate, mid
        else:
            hi = mid
    size, _, x0, y0, cov = best
    r_idx, c_idx = np.nonzero(cov >= threshold)
    order = np.argsort(-cov[r_idx, c_idx], kind="stable")[:count]
    cells = [Cell(float(x0[c_idx[i]]), float(y0[r_idx[i]]), size) for i in order]
    # Reading order: column by column from left to right, top to bottom within a column,
    # so the products flow along the word the way it is written.
    cells.sort(key=lambda k: (round(k.x, 3), k.y))
    return cells


def plan_tiles(mask: np.ndarray, products: list[dict], mode: str) -> list[tuple[Cell, dict]]:
    """Pair every tile with a product.

    The tile count is the larger of the product count and the number of tiles needed
    for the letters to stay readable. When there are more tiles than products:
      "timeline" gives each product a run of neighboring tiles, so history reads left to right
      "cycle" repeats the whole lineup in order along the word
    """
    if not products:
        return []
    readable = cells_at(mask, WORD_SIZE * TILE_PER_STROKE)
    total = max(len(products), readable)
    cells = exact_grid(mask, total)
    n = len(products)
    if mode == "cycle":
        picks = [products[i % n] for i in range(total)]
    else:
        picks = [products[min(n - 1, i * n // total)] for i in range(total)]
    return list(zip(cells, picks))


# --------------------------------------------------------------------------- tile drawing

def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def text_color_for(rgb) -> tuple[int, int, int]:
    r, g, b = (c / 255 for c in rgb)
    return (20, 20, 26) if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.55 else (250, 250, 252)


def wrap(text: str, fnt, max_width: float) -> list[str]:
    words, lines, line = text.split(), [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if fnt.getlength(candidate) <= max_width or not line:
            line = candidate
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


_label_cache: dict[tuple, tuple] = {}


def fit_label(text: str, box_size: float):
    """Largest font that wraps the label into at most four lines inside the tile."""
    key = (text, int(box_size))
    if key not in _label_cache:
        size, inner = int(box_size * 0.2), box_size * 0.84
        while size > 6:
            fnt = font(size, "SemiBold")
            lines = wrap(text, fnt, inner)
            if (len(lines) <= 4 and len(lines) * size * 1.18 <= inner
                    and all(fnt.getlength(line) <= inner for line in lines)):
                break
            size -= 1
        fnt = font(size, "SemiBold")
        _label_cache[key] = (fnt, size, wrap(text, fnt, inner)[:4])
    return _label_cache[key]


_photo_cache: dict[str, Image.Image | None] = {}


def load_photo(product: dict) -> Image.Image | None:
    """The product's downloaded photo, or None to fall back to a plain colored tile."""
    path = photo_path(product)
    key = str(path)
    if key not in _photo_cache:
        try:
            _photo_cache[key] = Image.open(path).convert("RGB") if path.exists() else None
        except OSError:
            _photo_cache[key] = None
    return _photo_cache[key]


def paste_photo(canvas: Image.Image, photo: Image.Image, card, radius: int) -> None:
    """Fit the photo inside a white rounded card without cropping it."""
    x0, y0, x1, y1 = (int(round(v)) for v in card)
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    tile = Image.new("RGB", (w, h), (255, 255, 255))
    pad = max(1, int(min(w, h) * 0.06))
    fitted = photo.copy()
    fitted.thumbnail((w - 2 * pad, h - 2 * pad), Image.LANCZOS)
    tile.paste(fitted, ((w - fitted.width) // 2, (h - fitted.height) // 2))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    canvas.paste(tile, (x0, y0), mask)


def draw_tile(canvas: Image.Image, box, product: dict, radius: int) -> bool:
    """Draw one tile. Returns True when a photo was used."""
    draw = ImageDraw.Draw(canvas)
    color = hex_rgb(CATEGORIES.get(product["category"], CATEGORIES["Other"]))
    draw.rounded_rectangle(box, radius=radius, fill=color)
    photo = load_photo(product)
    if photo is not None:
        frame = max(2, int((box[2] - box[0]) * 0.06))
        card = (box[0] + frame, box[1] + frame, box[2] - frame, box[3] - frame)
        paste_photo(canvas, photo, card, max(1, radius - frame))
        return True
    fnt, size, lines = fit_label(product["name"], box[2] - box[0])
    line_h = size * 1.18
    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
    top = cy - line_h * len(lines) / 2 + line_h / 2
    for i, line in enumerate(lines):
        draw.text((cx, top + i * line_h), line, font=fnt, fill=text_color_for(color), anchor="mm")
    return False


# --------------------------------------------------------------------------- composition

def render_word(word: str, products: list[dict], caption: str, mode: str) -> Image.Image:
    mask_img = word_mask(word)
    mask = np.array(mask_img)
    tiles = plan_tiles(mask, products, mode)

    categories = [c for c in CATEGORIES if any(p["category"] == c for p in products)]
    per_row = 6
    legend_rows = math.ceil(len(categories) / per_row)
    width = mask_img.width + 2 * MARGIN
    k = width / 6000  # scale text with the image so the preview stays readable
    word_top = MARGIN
    caption_y = word_top + mask_img.height + int(200 * k)
    legend_top = caption_y + int(190 * k)
    row_h = int(120 * k)
    height = legend_top + legend_rows * row_h + int(300 * k)

    canvas = Image.new("RGB", (width, height), BACKGROUND)
    size = tiles[0][0].size if tiles else 0
    gap = max(1.0, size * 0.1)
    radius = max(1, int(size * 0.16))
    photos = 0
    for cell, product in tiles:
        box = (MARGIN + cell.x + gap / 2, word_top + cell.y + gap / 2,
               MARGIN + cell.x + size - gap / 2, word_top + cell.y + size - gap / 2)
        photos += draw_tile(canvas, box, product, radius)

    draw = ImageDraw.Draw(canvas)
    draw.text((width / 2, caption_y), caption, font=font(int(110 * k), "Medium"), fill=TEXT, anchor="mm")

    legend_font = font(int(64 * k))
    swatch = int(66 * k)
    col_w = (width - 2 * MARGIN) / per_row
    for i, cat in enumerate(categories):
        row, col = divmod(i, per_row)
        in_row = min(per_row, len(categories) - row * per_row)
        x = (width - in_row * col_w) / 2 + col * col_w
        y = legend_top + row * row_h
        draw.rounded_rectangle((x, y, x + swatch, y + swatch), radius=int(14 * k), fill=hex_rgb(CATEGORIES[cat]))
        count = sum(1 for p in products if p["category"] == cat)
        draw.text((x + swatch + int(26 * k), y + swatch / 2), f"{cat}  {count}", font=legend_font,
                  fill=TEXT, anchor="lm")

    footer = "Unofficial fan project, not affiliated with Apple Inc.  ·  Script font: Pacifico (SIL Open Font License)"
    if photos:
        footer = "Product photos: Wikimedia Commons contributors, see CREDITS.md  ·  " + footer
    draw.text((width / 2, height - int(130 * k)), footer, font=font(int(52 * k)), fill=MUTED, anchor="mm")
    return canvas


def save(image: Image.Image, stem: str) -> None:
    image.save(OUTPUT_DIR / f"{stem}.jpg", quality=85, optimize=True, progressive=True)
    preview = image.copy()
    preview.thumbnail((PREVIEW_WIDTH, PREVIEW_WIDTH * 2), Image.LANCZOS)
    preview.save(OUTPUT_DIR / f"{stem}.png", optimize=True)


def year_span(products: list[dict]) -> str:
    years = sorted(p["date"][:4] for p in products)
    return f"{years[0]}–{years[-1]}" if years else ""


def main() -> None:
    products = sorted(load_products()["products"], key=lambda p: p["date"])
    current = [p for p in products if p.get("current")]
    past = [p for p in products if not p.get("current")]
    OUTPUT_DIR.mkdir(exist_ok=True)

    save(render_word("current", current,
                     f"{len(current)} products on apple.com today", mode="cycle"), "current")
    save(render_word("thank you", past,
                     f"{len(past)} past products, {year_span(past)}, oldest first", mode="timeline"),
         "thank_you")
    print(f"Rendered {len(current)} current and {len(past)} past products.")


if __name__ == "__main__":
    main()
