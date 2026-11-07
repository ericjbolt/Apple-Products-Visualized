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

def menu_html(*links):
    """An apple.com chapter-navigation menu with the given (href, label) links."""
    items = "".join(f'<li class="chapternav-item"><a href="{h}"><span class="chapternav-label">{l}</span></a></li>'
                    for h, l in links)
    return f'<nav class="chapternav"><ul>{items}</ul></nav>'


# The product menus as apple.com listed them in November 2026, including the links
# that broke the label-based sync: family labels ("AirPods" for AirPods 5), the
# Displays hub, a macOS release name, and store-only models such as iPhone 16.
MENUS = {
    "Mac": [("/macbook-neo/", "MacBook Neo"), ("/macbook-air/", "MacBook Air"),
            ("/macbook-pro/", "MacBook ProNew"), ("/imac/", "iMacNew"), ("/mac-mini/", "Mac miniNew"),
            ("/mac-studio/", "Mac StudioNew"), ("/mac/compare/", "Compare"),
            ("/mac/best-mac/", "Help Me Choose"), ("/displays/", "Displays"),
            ("/us/shop/goto/mac/accessories", "Accessories"), ("/us/shop/goto/buy_mac", "Shop Mac"),
            ("/os/macos/", "Golden Gate")],
    "Displays": [("/studio-display/", "Studio Display"), ("/studio-display-xdr/", "Studio Display XDR")],
    "iPad": [("/ipad-pro/", "iPad Pro"), ("/ipad-air/", "iPad Air"), ("/ipad-11/", "iPad"),
             ("/ipad-mini/", "iPad miniNew"), ("/ipad/compare/", "Compare"),
             ("/apple-pencil/", "Apple Pencil"), ("/ipad-keyboards/", "Keyboards"),
             ("/os/ipados/", "iPadOS 27"), ("/us/shop/goto/buy_ipad", "Shop iPad")],
    "iPhone": [("/iphone-duo/", "iPhone Duo New"), ("/iphone-18-pro/", "iPhone 18 Pro New"),
               ("/iphone-air/", "iPhone Air"), ("/iphone-17/", "iPhone 17"), ("/iphone-17e/", "iPhone 17e"),
               ("/us/shop/goto/buy_iphone/iphone_16", "iPhone 16"), ("/iphone/compare/", "Compare"),
               ("/us/shop/goto/buy_iphone", "Shop iPhone"), ("/os/ios/", "iOS 27")],
    "Apple Watch": [("/apple-watch-series-12/", "Apple WatchSeries 12New"),
                    ("/apple-watch-ultra-4/", "Apple WatchUltra 4New"), ("/apple-watch-se-3/", "Apple WatchSE 3"),
                    ("/apple-watch-nike/", "Apple WatchNikeNew Colors"), ("/apple-watch-hermes/", "Apple WatchHermès"),
                    ("/us/shop/goto/watch/bands", "Bands"), ("/apple-fitness-plus/", "Apple Fitness+")],
    "AirPods": [("/airpods-5/", "AirPods New"), ("/airpods-pro/", "AirPods Pro"),
                ("/airpods-max/", "AirPods Max"), ("/airpods/compare/", "Compare"),
                ("/apple-music/", "Apple Music")],
    "TV & Home": [("/apple-homeview/", "Apple HomeView New"), ("/apple-tv-4k/", "Apple TV 4K New"),
                  ("/homepod-mini/", "HomePod mini 2 New"), ("/homepod-2nd-generation/", "HomePod"),
                  ("/home/accessories/", "Accessories New"), ("/home-app/", "Home app")],
}


def listed_from_menus(menus=MENUS):
    """What scrape() would return for these menus, without touching the network."""
    listed = {}
    for category, links in menus.items():
        if category == "Displays":
            continue
        for page, label in fetch_current.parse_menu(menu_html(*links)):
            if page in fetch_current.HUB_PAGES:
                for sub, sub_label in fetch_current.parse_menu(menu_html(*menus["Displays"])):
                    listed.setdefault(sub, (sub_label, "Displays"))
            else:
                listed.setdefault(page, (label, category))
    for page, category in fetch_current.STANDALONE_PAGES.items():
        listed.setdefault(page, (page, category))
    return listed


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
        if not p["current"]:
            assert p.get("retired", "") >= p["date"], ("past products need a retirement date", p)


def test_menu_parser_keeps_only_product_pages():
    pages = [p for p, _ in fetch_current.parse_menu(menu_html(*MENUS["Mac"]))]
    assert pages == ["/macbook-neo/", "/macbook-air/", "/macbook-pro/", "/imac/", "/mac-mini/",
                     "/mac-studio/", "/displays/"], "no compare, shop, accessories, or macOS links"
    iphone = dict(fetch_current.parse_menu(menu_html(*MENUS["iPhone"])))
    assert iphone["/us/shop/goto/buy_iphone/iphone_16"] == "iPhone 16", "store-only models count"
    assert "/us/shop/goto/buy_iphone" not in iphone, "the plain Shop iPhone link does not"
    assert dict(fetch_current.parse_menu(menu_html(*MENUS["AirPods"])))["/airpods-5/"] == "AirPods"


def test_current_lineup_matches_apple_com_exactly():
    """The November 2026 menus must produce no changes: nothing retired, nothing added."""
    doc = copy.deepcopy(load())
    assert fetch_current.apply(doc, listed_from_menus()) == []


def test_every_current_product_has_a_page():
    for p in load()["products"]:
        if p["current"]:
            assert p.get("page"), p["name"]
        else:
            assert "page" not in p, ("retired products must not claim a page", p["name"])


def test_removed_page_retires_its_products_and_new_page_is_added():
    doc = copy.deepcopy(load())
    listed = listed_from_menus()
    del listed["/iphone-18-pro/"]
    listed["/iphone-19e/"] = ("iPhone 19e", "iPhone")
    changes = fetch_current.apply(doc, listed)
    by_name = {p["name"]: p for p in doc["products"]}
    for name in ("iPhone 18 Pro", "iPhone 18 Pro Max"):
        assert not by_name[name]["current"] and by_name[name]["retired"], name
        assert "page" not in by_name[name]
    assert by_name["iPhone 19e"]["needs_review"] and by_name["iPhone 19e"]["page"] == "/iphone-19e/"
    assert sorted(changes) == sorted(["No longer current: iPhone 18 Pro", "No longer current: iPhone 18 Pro Max",
                                      "New product added: iPhone 19e (/iphone-19e/)"])
    assert by_name["iPhone 16 Plus"]["current"] is False, "a retired model stays retired"


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


def test_bubbles_stay_inside_the_word(monkeypatch):
    small_words(monkeypatch)
    mask = render.np.array(render.word_mask("thank you"))
    bubbles = render.pack_bubbles(mask)
    assert len(bubbles) > 252, "enough bubbles to give every past product its own cluster"
    for b in bubbles:
        assert mask[int(b.y), int(b.x)] > 0, "every bubble center is inside a letter"
    radii = sorted(b.r for b in bubbles)
    assert radii[-1] > 3 * radii[0], "bubbles come in many sizes"


def test_each_past_product_appears_once_in_retirement_order(monkeypatch):
    small_words(monkeypatch)
    _, past = render.ordered_products(load()["products"])
    groups = render.group_bubbles(render.pack_bubbles(render.np.array(render.word_mask("thank you"))), past)
    assert [p for p, _ in groups] == past, "one cluster per product, in retirement order"
    retired = [p["retired"] for p, _ in groups]
    assert retired == sorted(retired)


def test_hello_is_in_introduction_order(monkeypatch):
    small_words(monkeypatch)
    current, _ = render.ordered_products(load()["products"])
    groups = render.group_bubbles(render.pack_bubbles(render.np.array(render.word_mask("hello"))), current)
    assert [p for p, _ in groups] == current
    dates = [p["date"] for p, _ in groups]
    assert dates == sorted(dates)


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
    image = render.render_word("hello", current, "test")
    assert image.width > 0
    render._photo_cache.clear()
