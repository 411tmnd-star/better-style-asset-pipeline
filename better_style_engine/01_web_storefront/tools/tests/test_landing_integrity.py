"""Landing lookbook integrity: references resolve, image sizes are true, the journey contract holds, deep links are real.

Static checks on landing/index.html and landing/film/index.html (no browser): every local src/srcset/poster/href
resolves to a file in the source tree laid out as the assembled site (landing at /, catalogue at /catalogue/); every
srcset width descriptor equals the file's real width; every <img width height> equals its src file's size; exactly three
anchors read "Shop" and point at catalogue/ (catalogue/tests/site.mjs counts them); every catalogue deep link names a
product and colourway present in catalogue/data/products.json and none is held back (camp shirts); and assemble_site
copies the optional landing folders while keeping the editorial provenance file private.
"""
import json
import re
import shutil
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image

CANON = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CANON / "tools"))
import assemble_site  # noqa: E402

PRODUCTS = json.loads((CANON / "catalogue/data/products.json").read_text(encoding="utf-8"))
HELD_BACK = re.compile(r"^BS-CAMP-")


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.refs, self.imgs, self.anchors, self._a = [], [], [], None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for key in ("src", "href", "poster"):
            if a.get(key):
                self.refs.append((tag, key, a[key]))
        for key in ("srcset", "imagesrcset"):
            if a.get(key):
                for part in a[key].split(","):
                    bits = part.strip().split()
                    self.refs.append((tag, key, bits[0]))
                    if len(bits) > 1 and bits[1].endswith("w"):
                        self.refs.append((tag, "w-descriptor", f"{bits[0]}|{bits[1][:-1]}"))
        if tag == "img":
            self.imgs.append(a)
        if tag == "a":
            self._a = {"href": a.get("href"), "text": ""}

    def handle_data(self, data):
        if self._a is not None:
            self._a["text"] += data

    def handle_endtag(self, tag):
        if tag == "a" and self._a is not None:
            self.anchors.append(self._a)
            self._a = None


def parse(rel):
    p = Page()
    p.feed((CANON / "landing" / rel).read_text(encoding="utf-8"))
    return p


def resolve(url, page_dir):
    """Map a reference on an assembled-site page to its file in the source tree, or None for external/hash links."""
    if re.match(r"^(https?:|mailto:|tel:|data:|#)", url):
        return None
    path = unquote(urlsplit(url).path)
    site_path = (Path("/") / page_dir / path).resolve().as_posix() if path else "/" + page_dir
    site_path = re.sub(r"^[A-Za-z]:", "", site_path)
    if site_path.startswith("/catalogue"):
        f = CANON / "catalogue" / site_path[len("/catalogue"):].lstrip("/")
    else:
        f = CANON / "landing" / site_path.lstrip("/")
    return f / "index.html" if (url.endswith("/") or f.is_dir()) else f


class TestLandingIntegrity(unittest.TestCase):
    PAGES = {"index.html": "", "film/index.html": "film"}

    def test_every_local_reference_resolves(self):
        for rel, page_dir in self.PAGES.items():
            p = parse(rel)
            missing = []
            for tag, key, url in p.refs:
                if key == "w-descriptor":
                    continue
                f = resolve(url, page_dir)
                if f is not None and not f.is_file():
                    missing.append(f"{tag}[{key}]={url}")
            self.assertEqual(missing, [], f"{rel}: unresolved references")

    def test_srcset_widths_and_img_dimensions_are_true(self):
        p = parse("index.html")
        for tag, key, val in p.refs:
            if key != "w-descriptor":
                continue
            url, w = val.split("|")
            with Image.open(resolve(url, "")) as im:
                self.assertEqual(im.width, int(w), f"{url} is {im.width}px wide, srcset says {w}w")
        for a in p.imgs:
            src = a.get("src")
            self.assertTrue(src, f"<img> without src: {a}")
            self.assertIn("alt", a, f"<img> without alt: {src}")
            if a.get("width") and a.get("height"):
                with Image.open(resolve(src, "")) as im:
                    self.assertEqual((im.width, im.height), (int(a["width"]), int(a["height"])), f"{src}: width/height attributes")

    def test_journey_contract_three_shop_links(self):
        shop = [a for a in parse("index.html").anchors if a["text"].strip().lower() == "shop"]
        self.assertEqual(len(shop), 3, [a["href"] for a in shop])
        self.assertTrue(all(a["href"] == "catalogue/" for a in shop))

    def test_page1_deep_links_are_real_and_nothing_held_back_is_linked(self):
        # Page 1 links pieces as #/p/<SKU>/<COLOUR> (its own quick view; the deck, collection and looks are rendered from
        # products.json, whose links tools/validate_catalogue.py checks). Every static one must name a live colourway.
        live = {(p["sku"], c["id"]) for p in PRODUCTS["products"] for c in p["colourways"]}
        links = [a["href"] for a in parse("index.html").anchors if a["href"] and "#/p/" in a["href"]]
        self.assertGreaterEqual(len(links), 1)
        for href in links:
            m = re.match(r"^(?:catalogue/)?#/p/([^/]+)/([^/?]+)$", href)
            self.assertIsNotNone(m, href)
            self.assertIn((unquote(m.group(1)), unquote(m.group(2))), live, href)
            self.assertIsNone(HELD_BACK.match(m.group(1)), f"held-back product linked: {href}")

    def test_page1_has_no_scroll_jacking_and_no_3d(self):
        html = (CANON / "landing/index.html").read_text(encoding="utf-8")
        js = (CANON / "landing/assets/js/landing.js").read_text(encoding="utf-8")
        for bad in ("gsap", "ScrollTrigger", "lenis", "viewer3d", "three.module", "three/addons", "@sparkjsdev", ".glb", ".sog", "webgl"):
            self.assertNotIn(bad, html.lower(), bad)
            self.assertNotIn(bad, js.lower(), bad)
        css = (CANON / "landing/assets/css/landing.css").read_text(encoding="utf-8")
        self.assertIn("scroll-snap-type: x mandatory", css)        # the deck advances in place on a native horizontal strip
        self.assertRegex(css, r"\.deck \{[^}]*height: 82svh")

    def test_shared_tokens_and_fonts(self):
        css = (CANON / "catalogue/css/maison.css").read_text(encoding="utf-8")
        for url in re.findall(r'url\("([^"]+)"\)', css):
            self.assertTrue((CANON / "catalogue/css" / url).is_file(), url)
        self.assertIn('href="css/maison.css"', (CANON / "catalogue/index.html").read_text(encoding="utf-8"))
        self.assertIn('href="catalogue/css/maison.css"', (CANON / "landing/index.html").read_text(encoding="utf-8"))

    def test_assembly_copies_optional_landing_folders_and_keeps_provenance_private(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for f in ("index.html", "item/index.html", "apply/index.html", "film/index.html"):
                (root / "landing" / f).parent.mkdir(parents=True, exist_ok=True)
                (root / "landing" / f).write_text("<html></html>", encoding="utf-8")
            for f in ("media/x.webp", "assets/editorial/a-480.webp", "vendor/gsap.min.js"):
                (root / "landing" / f).parent.mkdir(parents=True, exist_ok=True)
                (root / "landing" / f).write_bytes(b"x")
            (root / "landing/assets/editorial/editorial.json").write_text('{"assets": {}}', encoding="utf-8")
            (root / "catalogue/dist/data").mkdir(parents=True)
            (root / "catalogue/dist/index.html").write_text("<html></html>", encoding="utf-8")
            (root / "catalogue/dist/data/products.json").write_text(json.dumps({"products": []}), encoding="utf-8")
            (root / "config").mkdir()
            shutil.copy2(CANON / "config/site_vercel.json", root / "config/site_vercel.json")
            assemble_site.assemble(canon=root, out=root / "deploy/site")
            site = root / "deploy/site"
            for f in ("film/index.html", "assets/editorial/a-480.webp", "vendor/gsap.min.js", "media/x.webp", "catalogue/index.html"):
                self.assertTrue((site / f).is_file(), f)
            self.assertFalse((site / "assets/editorial/editorial.json").exists())


if __name__ == "__main__":
    unittest.main()
