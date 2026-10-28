"""Render the cursive product mosaics.

Two images are produced, each as a large zoomable JPEG and a smaller PNG preview:

    output/hello.jpg / hello.png          "hello", filled with today's lineup
    output/thank_you.jpg / thank_you.png  "thank you", filled with every past product

Usage:
    python scripts/render.py
"""

from __future__ import annotations

import glob
import math
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from common import (GROUPS, OUTPUT_DIR, RAINBOW, ROOT, load_products, photo_path,
                    product_group, product_type, type_color)

Image.MAX_IMAGE_PIXELS = None  # the full-size images are intentionally large

BACKGROUND = (13, 13, 18)
TEXT = (236, 236, 240)
MUTED = (140, 140, 152)

SCRIPT_FONT = ROOT / "assets" / "fonts" / "Pacifico-Regular.ttf"
WORD_SIZE = 2400          # script font size in pixels for the full-size image
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


# --------------------------------------------------------------------------- bubbles

@dataclass
class Bubble:
    x: float  # center, in word-mask pixels
    y: float
    r: float


PACK_SCALE = 4             # pack on a mask this many times smaller, for speed
MIN_RADIUS = 0.006         # smallest bubble, as a share of WORD_SIZE
BUBBLE_GAP = 0.004         # space between bubbles, as a share of WORD_SIZE


def pack_bubbles(mask: np.ndarray) -> list[Bubble]:
    """Fill the word with circles of many sizes, largest first.

    Each step places the biggest circle that still fits, using a distance map of the
    space left inside the letters. Thick strokes get large bubbles, thin strokes and
    corners get small ones, and packing stops when only tiny gaps remain.
    """
    from scipy.ndimage import distance_transform_edt

    small = Image.fromarray(mask).resize((mask.shape[1] // PACK_SCALE, mask.shape[0] // PACK_SCALE),
                                         Image.BILINEAR)
    inside = np.array(small) > 127
    sx, sy = mask.shape[1] / small.width, mask.shape[0] / small.height  # exact scale back up
    dist = distance_transform_edt(inside)
    gap = WORD_SIZE * BUBBLE_GAP / PACK_SCALE
    min_r = WORD_SIZE * MIN_RADIUS / PACK_SCALE
    h, w = dist.shape
    bubbles = []
    while True:
        index = int(np.argmax(dist))
        room = float(dist.flat[index])
        if room < min_r + gap:
            break
        cy, cx = divmod(index, w)
        r = room - gap
        bubbles.append(Bubble((cx + 0.5) * sx, (cy + 0.5) * sy, r * min(sx, sy)))
        # Every point near the new bubble is now only as far from an obstacle as it is
        # from this bubble's edge, plus the gap.
        reach = int(r + gap + room) + 2
        y0, y1, x0, x1 = max(0, cy - reach), min(h, cy + reach), max(0, cx - reach), min(w, cx + reach)
        yy, xx = np.ogrid[y0:y1, x0:x1]
        edge = np.hypot(yy - cy, xx - cx) - (r + gap)
        np.minimum(dist[y0:y1, x0:x1], np.maximum(edge, 0), out=dist[y0:y1, x0:x1])
    return bubbles


def group_bubbles(bubbles: list[Bubble], products: list[dict]) -> list[tuple[dict, list[Bubble]]]:
    """Give each product a cluster of neighboring bubbles, in order from left to right.

    Bubbles are sorted by position along the word and split into one group per product,
    with each group covering about the same area. A product appears exactly once: its
    largest bubble shows the photo or name, and the rest of its cluster shares its color.
    """
    if not products or not bubbles:
        return []
    ordered = sorted(bubbles, key=lambda b: (b.x, b.y))
    n = min(len(products), len(ordered))
    areas = [b.r ** 2 for b in ordered]
    remaining = sum(areas)
    groups: list[list[Bubble]] = []
    i = 0
    for g in range(n):
        groups_left = n - g
        target = remaining / groups_left
        group, taken = [], 0.0
        # Take at least one bubble, aim for an equal share of the remaining area, and
        # always leave at least one bubble for each product still to come.
        while i < len(ordered) and (not group or taken < target) and len(ordered) - i > groups_left - 1:
            group.append(ordered[i])
            taken += areas[i]
            i += 1
            if groups_left == 1:
                continue
        if groups_left == 1:
            group.extend(ordered[i:])
            i = len(ordered)
        remaining -= sum(b.r ** 2 for b in group)
        groups.append(group)
    return [(p, sorted(g, key=lambda b: -b.r)) for p, g in zip(products, groups)]


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


def paste_round_photo(canvas: Image.Image, photo: Image.Image, cx: float, cy: float, r: float) -> None:
    """Fit the photo inside a white circle without cropping it."""
    d = max(2, int(2 * r))
    disc = Image.new("RGB", (d, d), (255, 255, 255))
    fitted = photo.copy()
    side = int(d * 0.62)  # the largest square that sits comfortably inside the circle
    fitted.thumbnail((side, side), Image.LANCZOS)
    disc.paste(fitted, ((d - fitted.width) // 2, (d - fitted.height) // 2))
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, d - 1, d - 1), fill=255)
    canvas.paste(disc, (int(cx - d / 2), int(cy - d / 2)), mask)


def draw_group(canvas: Image.Image, product: dict, bubbles: list[Bubble], origin) -> bool:
    """Draw a product's cluster of bubbles. Returns True when a photo was used."""
    ox, oy = origin
    draw = ImageDraw.Draw(canvas)
    color = type_color(product_type(product))
    for b in bubbles:
        draw.ellipse((ox + b.x - b.r, oy + b.y - b.r, ox + b.x + b.r, oy + b.y + b.r), fill=color)
    # Smaller bubbles in the cluster show the product's configurations, such as sizes,
    # storage, chips, and finishes, largest bubble first.
    text_color = text_color_for(color)
    for b, variant in zip(bubbles[1:], product.get("variants", [])):
        side = b.r * 1.41
        if side < 24:
            continue  # too small to read even when zoomed in
        vf, vsize, vlines = fit_label(variant, side)
        vh = vsize * 1.18
        vtop = oy + b.y - vh * len(vlines) / 2 + vh / 2
        for i, line in enumerate(vlines):
            draw.text((ox + b.x, vtop + i * vh), line, font=vf, fill=text_color, anchor="mm")
    main = bubbles[0]
    cx, cy = ox + main.x, oy + main.y
    photo = load_photo(product)
    if photo is not None:
        paste_round_photo(canvas, photo, cx, cy, main.r * 0.9)
        return True
    side = main.r * 1.41  # square inscribed in the circle
    fnt, size, lines = fit_label(product["name"], side)
    line_h = size * 1.18
    top = cy - line_h * len(lines) / 2 + line_h / 2
    for i, line in enumerate(lines):
        draw.text((cx, top + i * line_h), line, font=fnt, fill=text_color_for(color), anchor="mm")
    return False


# --------------------------------------------------------------------------- composition

def render_word(word: str, products: list[dict], caption: str) -> Image.Image:
    mask_img = word_mask(word)
    groups = group_bubbles(pack_bubbles(np.array(mask_img)), products)

    # Legend: one column per color family, in rainbow order, listing each product type.
    counts: dict[str, int] = {}
    for p in products:
        counts[product_type(p)] = counts.get(product_type(p), 0) + 1
    columns = []
    for group, (color_name, types) in GROUPS.items():
        present = [t for t in types if t in counts]
        extra = sorted(t for t in counts if product_group({"name": t, "line": t}) == group
                       and t not in types)
        if present or extra:
            columns.append((group, color_name, present + extra))
    width = mask_img.width + 2 * MARGIN
    k = width / 6000  # scale text with the image so the preview stays readable
    word_top = MARGIN
    caption_y = word_top + mask_img.height + int(200 * k)
    legend_top = caption_y + int(190 * k)
    row_h = int(84 * k)
    header_h = int(150 * k)
    longest = max((len(types) for _, _, types in columns), default=0)
    height = legend_top + header_h + longest * row_h + int(320 * k)

    canvas = Image.new("RGB", (width, height), BACKGROUND)
    photos = sum(draw_group(canvas, product, bubbles, (MARGIN, word_top)) for product, bubbles in groups)

    draw = ImageDraw.Draw(canvas)
    draw.text((width / 2, caption_y), caption, font=font(int(110 * k), "Medium"), fill=TEXT, anchor="mm")

    legend_font = font(int(50 * k))
    header_font = font(int(50 * k), "Bold")
    swatch = int(52 * k)
    col_w = (width - 2 * MARGIN) / max(1, len(columns))
    for c, (group, color_name, types) in enumerate(columns):
        x = MARGIN + c * col_w + int(20 * k)
        header_lines = wrap(group, header_font, col_w - int(60 * k))[:2]
        for i, line in enumerate(header_lines):
            draw.text((x, legend_top + i * int(60 * k)), line, font=header_font,
                      fill=hex_rgb(RAINBOW[color_name]), anchor="la")
        for r, t in enumerate(types):
            y = legend_top + header_h + r * row_h
            draw.rounded_rectangle((x, y, x + swatch, y + swatch), radius=int(12 * k), fill=type_color(t))
            draw.text((x + swatch + int(22 * k), y + swatch / 2), f"{t}  {counts[t]}", font=legend_font,
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


def ordered_products(products: list[dict]) -> tuple[list[dict], list[dict]]:
    """Current products by introduction date, past products by retirement date.

    Products from the same month keep the order they have in data/products.json. A past
    product with no retirement date yet is placed by its introduction date.
    """
    current = sorted((p for p in products if p.get("current")), key=lambda p: p["date"])
    past = sorted((p for p in products if not p.get("current")),
                  key=lambda p: (p.get("retired", p["date"]), p["date"]))
    return current, past


def main() -> None:
    current, past = ordered_products(load_products()["products"])
    OUTPUT_DIR.mkdir(exist_ok=True)

    save(render_word("hello", current,
                     f"{len(current)} products on apple.com today, in order of introduction",
                     ), "hello")
    retired_years = sorted(p.get("retired", p["date"])[:4] for p in past)
    span = f"{retired_years[0]}–{retired_years[-1]}" if retired_years else ""
    save(render_word("thank you", past,
                     f"{len(past)} past products, in order of retirement, {span}"),
         "thank_you")
    print(f"Rendered {len(current)} current and {len(past)} past products.")


if __name__ == "__main__":
    main()
