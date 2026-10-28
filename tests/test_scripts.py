"""Offline tests for the parsers, sync logic, and renderer. Run with: python -m pytest"""

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import fetch_all_time  # noqa: E402
import fetch_images  # noqa: E402
import fetch_current  # noqa: E402
import render  # noqa: E402
from common import TYPE_TO_GROUP, fingerprint, product_type  # noqa: E402

STRIP_HTML = """
<nav class="chapternav"><ul class="chapternav-items">
  <li class="chapternav-item"><a><span class="chapternav-label">iPhone 18 Pro</span></a></li>
  <li class="chapternav-item"><a><span class="chapternav-label">iPhone Duo</span>
      <span class="chapternav-new">New</span></a></li>
  <li class="chapternav-item"><a><span class="chapternav-label">iPhone 19e</span></a></li>
  <li class="chapternav-item"><a><span class="chapternav-label">Compare</span></a></li>
  <li class="chapternav-item"><a><span class="chapternav-label">Accessories</span></a></li>
  <li class="chapternav-item"><a><span class="chapternav-label">iOS 27</span></a></li>
</ul></nav>
"""

TIMELINE_HTML = """
<table>
  <tr><th>Release date</th><th>Model</th><th>Family</th></tr>
  <tr><td>January 15, 2027</td><td>iPhone 18e</td><td>iPhone</td></tr>
  <tr><td>January 15, 2027</td><td>Magic Keyboard (USB-C)</td><td>Accessory</td></tr>
  <tr><td>September 9, 2026</td><td>iPhone Duo</td><td>iPhone</td></tr>
</table>
"""


def load():
    with open(ROOT / "data" / "products.json", encoding="utf-8") as handle:
        return json.load(handle)


def test_dataset_is_valid():
    doc = load()
    names = [p["name"] for p in doc["products"]]
    assert len(names) == len(set(names)), "duplicate product names"
    for p in doc["products"]:
        assert product_type(p) in TYPE_TO_GROUP, p
        assert len(p["date"]) == 7 and p["date"][4] == "-", p


def test_strip_parser_skips_non_products():
    assert fetch_current.parse_strip(STRIP_HTML) == ["iPhone 18 Pro", "iPhone Duo", "iPhone 19e"]


def test_current_sync_marks_missing_and_adds_new():
    doc = copy.deepcopy(load())
    found = {"iPhone 18 Pro": "iPhone", "iPhone Duo": "iPhone", "iPhone 19e": "iPhone",
             "MacBook Air": "Mac"}
    changes = fetch_current.apply(doc, found)
    current = {p["name"] for p in doc["products"] if p["current"]}
    assert "iPhone Duo" in current
    assert "MacBook Air (13-inch, M5)" in current and "MacBook Air (15-inch, M5)" in current
    assert "iPhone 17e" not in current
    assert "iPhone 19e" in current
    assert any("New product added: iPhone 19e" in c for c in changes)


def test_timeline_parser_adds_only_newer_hardware():
    doc = copy.deepcopy(load())
    rows = fetch_all_time.parse_tables(TIMELINE_HTML)
    changes = fetch_all_time.apply(doc, rows)
    assert changes == ["New product added: iPhone 18e (iPhone, 2027-01)"]


def test_fingerprint_matches_generation_names():
    assert fingerprint("AirPods 5") == fingerprint("AirPods (5th generation)")


def small_words(monkeypatch):
    """Render at a small size so the tests run quickly."""
    monkeypatch.setattr(render, "WORD_SIZE", 300)


def test_words_fill_exactly(monkeypatch):
    small_words(monkeypatch)
    mask = render.np.array(render.word_mask("thank you"))
    for count in (1, 32, 219, 500):
        assert len(render.exact_grid(mask, count)) == count, "every tile must be inside the word"


def test_timeline_shows_every_product_in_order(monkeypatch):
    small_words(monkeypatch)
    past = [p for p in sorted(load()["products"], key=lambda p: p["date"]) if not p["current"]]
    tiles = render.plan_tiles(render.np.array(render.word_mask("thank you")), past, "timeline")
    shown = []
    for _, product in tiles:
        if not shown or shown[-1] is not product:
            shown.append(product)
    assert shown == past, "each past product appears once, as one run, oldest first"


def test_cycle_repeats_the_lineup(monkeypatch):
    small_words(monkeypatch)
    current = [p for p in load()["products"] if p["current"]]
    tiles = render.plan_tiles(render.np.array(render.word_mask("hello")), current, "cycle")
    assert len(tiles) >= len(current)
    assert {id(p) for _, p in tiles} == {id(p) for p in current}


def test_only_free_licenses_are_accepted():
    assert fetch_images.license_ok({"license": "CC BY-SA 4.0"})
    assert fetch_images.license_ok({"license": "CC BY 2.0"})
    assert fetch_images.license_ok({"license": "CC0"})
    assert fetch_images.license_ok({"license": "Public domain"})
    assert not fetch_images.license_ok({"license": "Fair use"})
    assert not fetch_images.license_ok({"license": "CC BY-NC 4.0"})
    assert not fetch_images.license_ok({"license": ""})


def test_title_candidates_drop_alternates_and_details():
    product = {"name": "iPhone 16 and iPhone 16 Plus", "line": "iPhone"}
    assert fetch_images.title_candidates(product) == ["iPhone 16", "iPhone"]
    product = {"name": "MacBook Air (M5)", "line": "MacBook Air"}
    assert fetch_images.title_candidates(product) == ["MacBook Air (M5)", "MacBook Air"]


def test_photo_tiles_render(tmp_path, monkeypatch):
    from PIL import Image
    small_words(monkeypatch)
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (64, 40), (90, 90, 90)).save(photo)
    monkeypatch.setattr(render, "photo_path", lambda product: photo)
    render._photo_cache.clear()
    current = [p for p in load()["products"] if p["current"]]
    image = render.render_word("hello", current, "test", mode="cycle")
    assert image.width > 0
    render._photo_cache.clear()
