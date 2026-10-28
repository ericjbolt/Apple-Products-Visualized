"""Render the apple-shaped product mosaics.

Usage:
    python scripts/render.py                # both PNGs
    python scripts/render.py --animate      # also the year-by-year GIF
"""

from __future__ import annotations

import argparse
import glob
import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from common import CATEGORIES, OUTPUT_DIR, load_products, photo_path

BACKGROUND = (13, 13, 18)
TEXT = (236, 236, 240)
MUTED = (140, 140, 152)
STEM = (120, 92, 66)
LEAF = (70, 140, 90)

WIDTH = 1400
SHAPE_SIZE = 1000  # width and height of the square box the apple is drawn in
SUPERSAMPLE = 4
STEM_BASE = 0.255  # where the stem meets the apple, as a share of SHAPE_SIZE
LEAF_TOP = 0.08   # highest point of the stem and leaf


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


# --------------------------------------------------------------------------- shape

def _ellipse(cx, cy, rx, ry, rotation_deg=0.0, points=180):
    t = np.linspace(0, 2 * math.pi, points, endpoint=False)
    x, y = rx * np.cos(t), ry * np.sin(t)
    r = math.radians(rotation_deg)
    xr = x * math.cos(r) - y * math.sin(r)
    yr = x * math.sin(r) + y * math.cos(r)
    return list(zip(cx + xr, cy + yr))


def _scale(poly, size):
    return [(x * size, y * size) for x, y in poly]


# The body is a union of overlapping ellipses: two upper lobes, a wide lower body,
# and two small bottom lobes that create the dimple. Coordinates are in a unit square.
BODY = [
    (0.355, 0.50, 0.305, 0.300),
    (0.645, 0.50, 0.305, 0.300),
    (0.500, 0.64, 0.410, 0.320),
    (0.375, 0.85, 0.185, 0.130),
    (0.625, 0.85, 0.185, 0.130),
]


def body_mask(size: int) -> Image.Image:
    """Smooth, anti-aliased mask of the apple body (no stem or leaf)."""
    big = size * SUPERSAMPLE
    img = Image.new("L", (big, big), 0)
    draw = ImageDraw.Draw(img)
    for cx, cy, rx, ry in BODY:
        draw.polygon(_scale(_ellipse(cx, cy, rx, ry, points=360), big), fill=255)
    return img.resize((size, size), Image.LANCZOS)


def draw_stem_and_leaf(canvas: Image.Image, origin: tuple[int, int], size: int) -> None:
    big = size * SUPERSAMPLE
    layer = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    # Slightly curved stem rising from the top dimple.
    stem = [(0.488, 0.255), (0.512, 0.255), (0.545, 0.095), (0.522, 0.088)]
    draw.polygon(_scale(stem, big), fill=STEM)
    # Leaf attached to the stem, tilted up and to the right.
    leaf = _ellipse(0.625, 0.135, 0.105, 0.042, rotation_deg=-28, points=200)
    draw.polygon(_scale(leaf, big), fill=LEAF)
    vein = [(0.535, 0.17), (0.71, 0.09)]
    draw.line(_scale(vein, big), fill=(52, 105, 68), width=max(2, big // 400))
    layer = layer.resize((size, size), Image.LANCZOS)
    canvas.alpha_composite(layer, origin)


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
    y0 = h - oy - (rows + 1) * cell  # rows counted upward from the bottom edge
    samples = (np.arange(4) + 0.5) / 4 * cell
    xs = (x0[None, :, None] + samples[None, None, :])  # (1, cols, 4)
    ys = (y0[:, None, None] + samples[None, None, :])  # (rows, 1, 4)
    xi = np.clip(xs.astype(int), 0, w - 1)
    yi = np.clip(ys.astype(int), 0, h - 1)
    inside = mask[yi[:, :, :, None], xi[:, :, None, :]] > 127  # (rows, cols, 4, 4)
    valid = ((xs >= 0) & (xs < w))[:, :, None, :] & ((ys >= 0) & (ys < h))[:, :, :, None]
    coverage = (inside & valid).mean(axis=(2, 3))
    return x0, y0, coverage


def exact_grid(mask: np.ndarray, count: int, threshold: float = 0.5) -> list[Cell]:
    """Exactly `count` square tiles that together fill the whole apple.

    The search tries many tile sizes and grid offsets and keeps the largest tile size
    whose grid has at least `count` cells that are mostly inside the shape. If that grid
    has a few extra cells, the ones least inside the shape are dropped, so the apple is
    always completely full with no empty slots.
    """
    if count <= 0:
        return []
    offsets = [0.0, 0.25, 0.5, 0.75]
    best = None  # (cell size, surplus, x0, y0, coverage)
    lo, hi = 2.0, mask.shape[0] / 2
    for _ in range(36):
        mid = (lo + hi) / 2
        candidate = None
        for fx in offsets:
            for fy in offsets:
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
    # Fill order: bottom row first, left to right, like water filling a glass.
    cells.sort(key=lambda k: (-round(k.y, 3), k.x))
    return cells


# --------------------------------------------------------------------------- drawing helpers

def hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def text_color_for(rgb) -> tuple[int, int, int]:
    r, g, b = (c / 255 for c in rgb)
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return (20, 20, 26) if luminance > 0.55 else (250, 250, 252)


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


def month_label(value: str) -> str:
    return datetime.strptime(value, "%Y-%m").strftime("%b %Y")


def tile_label(product: dict) -> str:
    return product["name"]


def fit_label(text: str, max_width: float, start_size: int, max_lines: int = 4,
              min_size: int = 9, max_height: float | None = None):
    """Shrink the font until the label wraps into few enough lines that all fit."""
    size = start_size
    while size > min_size:
        fnt = font(size, "SemiBold")
        lines = wrap(text, fnt, max_width)
        fits_height = max_height is None or len(lines) * size * 1.18 <= max_height
        if (len(lines) <= max_lines and fits_height
                and all(fnt.getlength(line) <= max_width for line in lines)):
            return fnt, size, lines
        size -= 1
    fnt = font(size, "SemiBold")
    return fnt, size, wrap(text, fnt, max_width)[:max_lines]


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


# --------------------------------------------------------------------------- main render

def render(products: list[dict], title: str, subtitle: str,
           show_labels: bool, row_years: bool, cells: list[Cell] | None = None,
           highlight_newest: bool = True,
           legend: list[str] | None = None) -> tuple[Image.Image, list[Cell]]:
    mask_img = body_mask(SHAPE_SIZE)
    mask = np.array(mask_img)
    if cells is None:
        cells = exact_grid(mask, len(products))

    shape_x = (WIDTH - SHAPE_SIZE) // 2
    # The stem's base tucks just inside the top row of tiles, wherever that row lands.
    cell_size = cells[0].size if cells else 0
    top_tile = min((c.y for c in cells), default=STEM_BASE * SHAPE_SIZE)
    stem_shift = int(top_tile + 0.4 * cell_size - STEM_BASE * SHAPE_SIZE)
    shape_y = max(150, 175 - int(LEAF_TOP * SHAPE_SIZE) - stem_shift)
    categories = legend or [c for c in CATEGORIES if any(p["category"] == c for p in products)]
    legend_rows = math.ceil(len(categories) / 4) if categories else 0
    height = shape_y + SHAPE_SIZE + 90 + legend_rows * 46 + 120

    canvas = Image.new("RGBA", (WIDTH, height), BACKGROUND + (255,))
    draw = ImageDraw.Draw(canvas)

    # Header
    draw.text((WIDTH / 2, 70), title, font=font(54, "Bold"), fill=TEXT, anchor="mm")
    draw.text((WIDTH / 2, 128), subtitle, font=font(26), fill=MUTED, anchor="mm")

    # No outline: the tiles themselves form the apple. Only the stem and leaf are drawn.
    draw_stem_and_leaf(canvas, (shape_x, shape_y + stem_shift), SHAPE_SIZE)
    draw = ImageDraw.Draw(canvas)

    gap = max(1.0, cell_size * 0.12)
    radius = max(1, int(cell_size * 0.18))
    label_font_size = max(10, int(cell_size * 0.16))
    label_font = font(label_font_size, "SemiBold")

    last_labeled_year = None
    last_row_y = None
    photos_used = 0
    year_font = font(max(14, min(22, int(cell_size * 0.5))), "Medium")
    shape_left = shape_x + min((c.x for c in cells), default=0)

    for product, cell in zip(products, cells):
        color = hex_rgb(CATEGORIES.get(product["category"], CATEGORIES["Other"]))
        box = (shape_x + cell.x + gap / 2, shape_y + cell.y + gap / 2,
               shape_x + cell.x + cell.size - gap / 2, shape_y + cell.y + cell.size - gap / 2)
        draw.rounded_rectangle(box, radius=radius, fill=color)

        photo = load_photo(product)
        label_box = box
        if photo is not None:
            photos_used += 1
            # The category color becomes a frame around a white card holding the photo.
            frame = max(2, int(cell.size * 0.07))
            card_bottom = box[3] - frame
            if show_labels:
                card_bottom = box[1] + (box[3] - box[1]) * 0.64
                label_box = (box[0], card_bottom, box[2], box[3])
            card = (box[0] + frame, box[1] + frame, box[2] - frame, card_bottom)
            paste_photo(canvas, photo, card, max(1, radius - frame))
            draw = ImageDraw.Draw(canvas)

        if show_labels:
            inner = cell.size - gap - cell.size * 0.14
            max_lines = 2 if photo is not None else 4
            label_font, label_font_size, lines = fit_label(
                tile_label(product), inner, label_font_size, max_lines=max_lines,
                min_size=8 if photo is not None else 9,
                max_height=(label_box[3] - label_box[1]) * 0.86)
            line_h = label_font_size * 1.18
            middle = (label_box[1] + label_box[3]) / 2
            top = middle - line_h * len(lines) / 2 + line_h / 2
            for i, line in enumerate(lines):
                draw.text(((box[0] + box[2]) / 2, top + i * line_h), line,
                          font=label_font, fill=text_color_for(color), anchor="mm")
            label_font_size = max(10, int(cell_size * 0.16))  # reset for the next tile

        # Year markers down the left edge, one per row when at least five years have passed.
        if row_years and (last_row_y is None or abs(cell.y - last_row_y) > 0.5):
            last_row_y = cell.y
            year = int(product["date"][:4])
            if last_labeled_year is None or year - last_labeled_year >= 5:
                last_labeled_year = year
                draw.text((shape_left - 18, shape_y + cell.y + cell.size / 2), str(year),
                          font=year_font, fill=MUTED, anchor="rm")

    if highlight_newest and products:
        newest = products[-1]
        cell = cells[len(products) - 1]
        box = (shape_x + cell.x + gap / 2 - 3, shape_y + cell.y + gap / 2 - 3,
               shape_x + cell.x + cell.size - gap / 2 + 3, shape_y + cell.y + cell.size - gap / 2 + 3)
        draw.rounded_rectangle(box, radius=radius + 2, outline=(255, 255, 255), width=3)
        draw.text((WIDTH / 2, shape_y + SHAPE_SIZE + 40),
                  f"Newest: {newest['name']} ({month_label(newest['date'])})",
                  font=font(26, "Medium"), fill=TEXT, anchor="mm")

    # Legend
    legend_top = shape_y + SHAPE_SIZE + 100
    col_w = 300
    legend_font = font(22)
    total_w = min(4, len(categories)) * col_w
    for i, cat in enumerate(categories):
        row, col = divmod(i, 4)
        x = (WIDTH - total_w) / 2 + col * col_w
        y = legend_top + row * 46
        draw.rounded_rectangle((x, y, x + 26, y + 26), radius=6, fill=hex_rgb(CATEGORIES[cat]))
        count = sum(1 for p in products if p["category"] == cat)
        draw.text((x + 38, y + 13), f"{cat}  {count}", font=legend_font,
                  fill=TEXT if count else MUTED, anchor="lm")

    footer = "Unofficial fan project, not affiliated with Apple Inc."
    if photos_used:
        footer = ("Product photos: Wikimedia Commons contributors, see CREDITS.md  ·  " + footer)
    draw.text((WIDTH / 2, height - 50), footer, font=font(20), fill=MUTED, anchor="mm")
    return canvas.convert("RGB"), cells


def build_all_time(products):
    first, last = products[0]["date"][:4], products[-1]["date"][:4]
    return dict(
        title="Every Apple Product",
        subtitle=f"{len(products)} products, {first}–{last}, oldest at the bottom",
        show_labels=False, row_years=True,
    )


def build_current(products):
    return dict(
        title="Apple's Current Lineup",
        subtitle=f"{len(products)} products sold new on apple.com, oldest at the bottom",
        show_labels=True, row_years=False,
    )


def animate(products: list[dict], path) -> None:
    """One frame per year. Every frame is a full apple, with tiles shrinking as products are added."""
    legend = [c for c in CATEGORIES if any(p["category"] == c for p in products)]
    first, last = int(products[0]["date"][:4]), int(products[-1]["date"][:4])
    frames = []
    for year in range(first, last + 1):
        subset = [p for p in products if int(p["date"][:4]) <= year]
        frame, _ = render(subset, title=f"Every Apple Product: {year}",
                          subtitle=f"{len(subset)} products so far",
                          show_labels=False, row_years=True,
                          highlight_newest=False, legend=legend)
        frames.append(frame.resize((frame.width // 2, frame.height // 2), Image.LANCZOS))
    durations = [180] * (len(frames) - 1) + [2500]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=durations,
                   loop=0, optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--animate", action="store_true", help="also write the year-by-year GIF")
    args = parser.parse_args()

    products = load_products()["products"]
    products.sort(key=lambda p: p["date"])  # stable: keeps same-month order from the file
    current = [p for p in products if p.get("current")]

    OUTPUT_DIR.mkdir(exist_ok=True)
    image, _ = render(products, **build_all_time(products))
    image.save(OUTPUT_DIR / "all_time.png", optimize=True)
    image, _ = render(current, **build_current(current))
    image.save(OUTPUT_DIR / "current.png", optimize=True)
    print(f"Rendered {len(products)} all-time and {len(current)} current products.")

    if args.animate:
        animate(products, OUTPUT_DIR / "all_time.gif")
        print("Rendered the animation.")


if __name__ == "__main__":
    main()
