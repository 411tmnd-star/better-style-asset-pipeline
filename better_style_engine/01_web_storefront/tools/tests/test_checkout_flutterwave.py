"""Unit tests: Mode B (Flutterwave Inline v3) configuration, the BS-SLV-001 wiring and the #/fitting / checkout front-end hooks.

    python -I -m unittest discover -s tools/tests -v
No image build and no browser: the validator runs on in-memory copies of catalogue.json, build.py is imported as a module,
and the shipped JS/HTML are read as text. The Playwright side lives in catalogue/tests/checkout.mjs (section G) and run.mjs.
"""
import copy
import importlib.util
import json
import os
import re
import sys
import unittest
from pathlib import Path

CANON = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CANON / "tools"))
import validate_catalogue as vc  # noqa: E402
import make_test_fixture as mtf  # noqa: E402

STORE = CANON / "catalogue"
REAL = json.loads((STORE / "data/catalogue.json").read_text(encoding="utf-8"))
STORE_JS = (STORE / "js/store.js").read_text(encoding="utf-8")
INDEX = (STORE / "index.html").read_text(encoding="utf-8")
PLACEHOLDER = "FLWPUBK_TEST-REPLACE_WITH_LIVE_KEY"


def load_build():
    os.environ.pop("BS_PREVIEW_PROVISIONAL", None)
    spec = importlib.util.spec_from_file_location("bs_build_flw_test", STORE / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FlutterwaveConfigTests(unittest.TestCase):
    def errs(self, cat):
        return vc.validate(cat, STORE)[0]

    def warns(self, cat):
        return vc.validate(cat, STORE)[1]

    def mut(self):
        return copy.deepcopy(REAL)

    def test_real_config_block(self):
        f = REAL["site"]["checkout"]["flutterwave"]
        self.assertEqual(f["public_key"], PLACEHOLDER)
        self.assertEqual(f["currencies"], ["NGN", "USD"])
        self.assertEqual(f["payment_options"], "card, banktransfer, ussd")
        self.assertEqual(self.errs(REAL), [])
        self.assertTrue(any("placeholder key" in w for w in self.warns(REAL)))

    def test_secret_key_is_an_error(self):
        c = self.mut(); c["site"]["checkout"]["flutterwave"]["public_key"] = "FLWSECK_TEST-abc123-X"
        self.assertTrue(any("SECRET" in e or "PUBLIC key" in e for e in self.errs(c)))

    def test_malformed_key_is_an_error(self):
        c = self.mut(); c["site"]["checkout"]["flutterwave"]["public_key"] = "pk_live_123"
        self.assertTrue(any("public_key" in e for e in self.errs(c)))

    def test_live_key_passes_without_placeholder_warning(self):
        c = self.mut(); c["site"]["checkout"]["flutterwave"]["public_key"] = "FLWPUBK-0123456789abcdef0123456789abcdef-X"
        self.assertEqual(self.errs(c), [])
        self.assertFalse(any("placeholder key" in w for w in self.warns(c)))

    def test_unknown_currency_is_an_error(self):
        c = self.mut(); c["site"]["checkout"]["flutterwave"]["currencies"] = ["NGN", "XYZ"]
        self.assertTrue(any("flutterwave.currencies" in e for e in self.errs(c)))

    def test_empty_payment_options_is_an_error(self):
        c = self.mut(); c["site"]["checkout"]["flutterwave"]["payment_options"] = " "
        self.assertTrue(any("payment_options" in e for e in self.errs(c)))

    def test_block_is_optional(self):
        c = self.mut(); del c["site"]["checkout"]["flutterwave"]
        self.assertEqual(self.errs(c), [])


class SleevelessWiringTests(unittest.TestCase):
    def setUp(self):
        self.p = next(x for x in REAL["products"] if x["sku"] == "BS-SLV-001")

    def test_entry_follows_the_tank_schema(self):
        tank = next(x for x in REAL["products"] if x["sku"] == "BS-TANK-001")
        self.assertEqual(set(self.p) - {"status_note"}, set(tank) - {"status_note"})
        self.assertEqual(self.p["status"], "live")
        self.assertEqual(self.p["sizes"], ["L", "XL", "XXL"])
        self.assertEqual(self.p["prices"], {"NGN": None})
        self.assertEqual(self.p["sale"]["status"], "draft")
        self.assertTrue(all(a["status"] == "draft" for a in self.p["approval"].values()))
        self.assertEqual(self.p["presentation"], "floating")          # the landing archive only lists floating fronts

    def test_front_only_and_source_on_disk(self):
        cw = self.p["colourways"]["grey"]
        self.assertEqual(list(cw["views"]), ["front"])                 # no back view is claimed
        src = STORE / cw["views"]["front"]
        self.assertTrue(src.is_file())
        rec = json.loads((src.parent / "BS-SLV-001_grey_front_v1.provenance.json").read_text(encoding="utf-8"))
        import hashlib
        self.assertEqual(hashlib.sha256(src.read_bytes()).hexdigest(), rec["sha256"])
        self.assertIn("back view", rec["not_claimed"])

    def test_build_publishes_inquiry_sizes_and_no_price(self):
        b = load_build()
        out = b.commercial(self.p, REAL["site"])
        self.assertIsNone(out["prices"]); self.assertIsNone(out["price"]); self.assertIsNone(out["sizes"])
        self.assertEqual(out["sale_status"], "not_for_sale")
        self.assertEqual(out["inquiry_sizes"], ["L", "XL", "XXL"])

    def test_bst_set_live_and_camp_shirts_held_on_landing(self):
        self.assertEqual(next(x for x in REAL["products"] if x["sku"] == "BS-BSTSET-001")["status"], "live")
        landing = (CANON / "landing/assets/js/landing.js").read_text(encoding="utf-8")
        self.assertIn("var HELD_BACK = /^BS-CAMP-/;", landing)

    def test_fixture_prices_cover_every_live_sku(self):
        live = {p["sku"] for p in REAL["products"] if p["status"] == "live"}
        self.assertLessEqual(live, set(mtf.USD)); self.assertLessEqual(live, set(mtf.NGN))


class FrontEndHookTests(unittest.TestCase):
    def test_v3_script_tag_deferred(self):
        self.assertRegex(INDEX, r'<script src="https://checkout\.flutterwave\.com/v3\.js" defer></script>')

    def test_store_never_calls_flutterwave_unguarded(self):
        calls = [m.start() for m in re.finditer(r"window\.FlutterwaveCheckout\(", STORE_JS)]
        self.assertEqual(len(calls), 1)
        guard = STORE_JS.rfind('typeof window.FlutterwaveCheckout !== "function"', 0, calls[0])
        self.assertGreater(guard, 0, "FlutterwaveCheckout must be feature-checked before it is called")

    def test_tx_ref_and_pending_flag(self):
        self.assertIn("`BS-TX-${Date.now()}`", STORE_JS)
        self.assertIn('"PENDING_SERVER_VERIFY"', STORE_JS)
        self.assertIn("function triggerWhatsAppDispatch(orderSummary, txRef)", STORE_JS)

    def test_placeholder_key_disables_mode_b(self):
        self.assertRegex(STORE_JS, r"/REPLACE/i\.test\(key\)\) return null")

    def test_fitting_route_and_studio_backdrops(self):
        # Page 2: #/fitting with ?sku=&color=&size= (the query is parsed, the route compares the path before "?")
        self.assertIn('const fit = path === "#/fitting";', STORE_JS)
        self.assertIn("renderFitting(parseHashQuery(h))", STORE_JS)
        self.assertIn('id="view-fitting"', INDEX)
        css = (STORE / "css/store.css").read_text(encoding="utf-8")
        for f, name in (("M1", "Warm Brutalist Stone"), ("M2", "Deep Obsidian Gallery"), ("M3", "Muted Travertine Putty")):
            self.assertIn(f'.studio-stage[data-finish="{f}"]', css)
            self.assertIn(f'{f}: "{name}"', STORE_JS)
        self.assertIn("transparentStage: true", STORE_JS)

    def test_landing_links_into_page2(self):
        html = (CANON / "landing/index.html").read_text(encoding="utf-8")
        self.assertGreaterEqual(html.count('href="fitting/"'), 3)
        fit = (CANON / "landing/fitting/index.html").read_text(encoding="utf-8")
        self.assertIn('location.replace("/catalogue/#/fitting" + (s ? "?" + s : ""))', fit)
        self.assertIn('["sku", "color", "size"]', fit)


if __name__ == "__main__":
    unittest.main()
