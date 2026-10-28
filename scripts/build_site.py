"""Build the zoomable viewer website from the full-size images in output/.

Each image is cut into a pyramid of small tiles (the Deep Zoom format), so a
browser only loads the part of the picture you are looking at. This keeps the
viewer fast and lets phones, which cannot open a 47-megapixel image in one piece,
zoom all the way in.

The site is written to site/ and published by the "Publish zoomable viewer"
workflow. It is not committed to the repository.

Usage:
    python scripts/build_site.py
    python -m http.server --directory site   # then open http://localhost:8000
"""

from __future__ import annotations

import hashlib
import math
import shutil
from pathlib import Path

from PIL import Image

from common import OUTPUT_DIR, ROOT

Image.MAX_IMAGE_PIXELS = None

SITE_DIR = ROOT / "site"
TILE = 254
OVERLAP = 1
WORDS = [("hello", "hello"), ("thank_you", "thank you")]


def write_dzi(source: Path, dest: Path, name: str) -> tuple[int, int]:
    """Write name.dzi and name_files/<level>/<col>_<row>.jpg under dest."""
    image = Image.open(source).convert("RGB")
    width, height = image.size
    max_level = math.ceil(math.log2(max(width, height)))
    files = dest / f"{name}_files"
    for level in range(max_level, -1, -1):
        scale = 2 ** (max_level - level)
        lw, lh = max(1, math.ceil(width / scale)), max(1, math.ceil(height / scale))
        layer = image if (lw, lh) == image.size else image.resize((lw, lh), Image.LANCZOS)
        folder = files / str(level)
        folder.mkdir(parents=True, exist_ok=True)
        for col in range(math.ceil(lw / TILE)):
            for row in range(math.ceil(lh / TILE)):
                x0 = col * TILE - (OVERLAP if col else 0)
                y0 = row * TILE - (OVERLAP if row else 0)
                x1 = min(lw, (col + 1) * TILE + OVERLAP)
                y1 = min(lh, (row + 1) * TILE + OVERLAP)
                layer.crop((x0, y0, x1, y1)).save(folder / f"{col}_{row}.jpg", quality=82)
        image = layer  # each level is made from the previous one, which is faster
    (dest / f"{name}.dzi").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<Image xmlns="http://schemas.microsoft.com/deepzoom/2008" Format="jpg" '
        f'Overlap="{OVERLAP}" TileSize="{TILE}"><Size Width="{width}" Height="{height}"/></Image>\n',
        encoding="utf-8",
    )
    return width, height


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Apple Products Visualized</title>
<meta name="description" content="Apple's product history as zoomable cursive mosaics.">
<style>
  :root { --bg: #0d0d12; --panel: #17171f; --text: #ececf0; --muted: #8c8c98; --accent: #4f8ef7; }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: var(--bg); color: var(--text);
    font: 15px/1.4 -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif; }
  body { display: flex; flex-direction: column; }
  header { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between;
    padding: 12px 16px; border-bottom: 1px solid #23232d; }
  h1 { font-size: 17px; margin: 0; font-weight: 600; }
  .tabs { display: flex; gap: 6px; background: var(--panel); padding: 4px; border-radius: 10px; }
  .tabs button { border: 0; background: transparent; color: var(--muted); font: inherit; font-weight: 600;
    padding: 6px 14px; border-radius: 7px; cursor: pointer; }
  .tabs button[aria-pressed="true"] { background: var(--accent); color: #fff; }
  #viewer { flex: 1; min-height: 0; }
  footer { padding: 8px 16px; color: var(--muted); font-size: 12px; border-top: 1px solid #23232d; }
  footer a { color: var(--text); }
  .hint { color: var(--muted); font-size: 13px; }
  .kofi { color: #fff; background: #ff5e5b; text-decoration: none; font-weight: 600; font-size: 13px;
    padding: 6px 12px; border-radius: 8px; white-space: nowrap; }
  .kofi:hover { filter: brightness(1.08); }
</style>
</head>
<body>
<header>
  <h1>Apple Products Visualized</h1>
  <div class="tabs" role="group" aria-label="Choose a word">
    __TABS__
  </div>
  <span class="hint">Scroll or pinch to zoom. Drag to move.</span>
  <a class="kofi" href="https://ko-fi.com/ericbolt" target="_blank" rel="noopener">Support on Ko-fi</a>
</header>
<div id="viewer" role="img" aria-label="Zoomable mosaic of Apple products"></div>
<footer>
  Unofficial fan project, not affiliated with Apple Inc. Product photos from Wikimedia Commons contributors,
  see <a href="https://github.com/ericjbolt/Apple-Products-Visualized/blob/main/CREDITS.md">credits</a>.
  <a href="https://github.com/ericjbolt/Apple-Products-Visualized">Source on GitHub</a>.
  <a href="https://ko-fi.com/ericbolt">Support on Ko-fi</a>.
</footer>
<script src="https://cdnjs.cloudflare.com/ajax/libs/openseadragon/4.1.0/openseadragon.min.js"></script>
<script>
  const viewer = OpenSeadragon({
    id: "viewer",
    prefixUrl: "https://cdnjs.cloudflare.com/ajax/libs/openseadragon/4.1.0/images/",
    tileSources: "tiles/__FIRST__.dzi",
    showNavigator: true,
    navigatorPosition: "BOTTOM_RIGHT",
    maxZoomPixelRatio: 2,
    visibilityRatio: 0.5,
    gestureSettingsMouse: { clickToZoom: false },
  });
  document.querySelectorAll(".tabs button").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".tabs button").forEach((b) => b.setAttribute("aria-pressed", "false"));
      button.setAttribute("aria-pressed", "true");
      viewer.open("tiles/" + button.dataset.word + ".dzi");
    });
  });
</script>
</body>
</html>
"""


def main() -> None:
    if SITE_DIR.exists():
        shutil.rmtree(SITE_DIR)
    tiles = SITE_DIR / "tiles"
    tiles.mkdir(parents=True)
    built = []
    for stem, label in WORDS:
        source = OUTPUT_DIR / f"{stem}.jpg"
        if source.exists():
            # A fingerprint of the image goes into the tile names, so browsers never mix
            # cached tiles from an older version with tiles from the new one.
            version = hashlib.sha1(source.read_bytes()).hexdigest()[:10]
            name = f"{stem}-{version}"
            w, h = write_dzi(source, tiles, name)
            built.append((name, label))
            print(f"Tiled {stem}: {w} x {h}")
    tabs = "\n    ".join(
        f'<button type="button" data-word="{stem}" aria-pressed="{str(i == 0).lower()}">{label}</button>'
        for i, (stem, label) in enumerate(built)
    )
    page = PAGE.replace("__TABS__", tabs).replace("__FIRST__", built[0][0])
    (SITE_DIR / "index.html").write_text(page, encoding="utf-8")
    (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Site written to {SITE_DIR}")


if __name__ == "__main__":
    main()
