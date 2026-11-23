"""Unit tests: catalogue validator (schema /2) and the build's sale-approval gate.

    python -I -m unittest discover -s tools/tests -v
No image build is run here: build.py is imported as a module and only commercial() is exercised.
"""
import copy
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

CANON = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CANON / "tools"))
import validate_catalogue as vc  # noqa: E402

REAL = json.loads((CANON / "catalogue/data/catalogue.json").read_text(encoding="utf-8"))
STORE = CANON / "catalogue"


def load_build(provisional=False):
    if provisional:
        os.environ["BS_PREVIEW_PROVISIONAL"] = "1"
    else:
        os.environ.pop("BS_PREVIEW_PROVISIONAL", None)
    spec = importlib.util.spec_from_file_location("bs_build_under_test", STORE / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def approve(p, *fields, by="owner", at="2026-11-11T12:00:00"):
    for f in fields:
        p["approval"][f] = {"status": "approved", "source": None, "by": by, "at": at}


def orderable_product(cat):
    """Mutate the first live product into a fully approved, orderable one (TEST VALUES ONLY)."""
    p = next(x for x in cat["products"] if x["status"] == "live")
    p["prices"] = {"NGN": 1000}
    p["sizes"] = [{"label": "M", "stock": None}]
    approve(p, "prices", "sizes")
    p["sale"] = {"status": "approved_for_sale", "by": "owner", "at": "2026-11-11T12:00:00"}
    return p


class ValidatorTests(unittest.TestCase):
    def errs(self, cat):
        e, _ = vc.validate(cat, STORE)
        return e

    def mut(self):
        return copy.deepcopy(REAL)

    def test_real_catalogue_has_no_errors(self):
        e, w = vc.validate(REAL, STORE)
        self.assertEqual(e, [])
        self.assertTrue(any("not orderable yet" in x for x in w))     # honest: nothing is approved

    def test_real_catalogue_has_nothing_approved_or_priced(self):
        for p in REAL["products"]:
            if p["status"] != "live":
                continue
            self.assertEqual(p["sale"]["status"], "draft", p["sku"])
            self.assertEqual(p["prices"], {"NGN": None}, p["sku"])
            self.assertTrue(all(a["status"] == "draft" for a in p["approval"].values()), p["sku"])
        self.assertEqual(REAL["site"]["checkout"]["whatsapp"], "2348146263165")
        self.assertIsNone(REAL["site"]["checkout"]["email"])

    def test_wrong_schema(self):
        c = self.mut(); c["schema"] = "x"
        self.assertTrue(any("schema" in e for e in self.errs(c)))

    def test_default_currency_must_be_enabled(self):
        c = self.mut(); c["site"]["currency"]["default"] = "USD"
        self.assertTrue(any("default" in e for e in self.errs(c)))

    def test_unknown_currency_rejected(self):
        c = self.mut(); c["site"]["currency"]["enabled"] = ["NGN", "XYZ"]
        self.assertTrue(any("XYZ" in e for e in self.errs(c)))

    def test_price_in_disabled_currency_rejected(self):
        c = self.mut(); c["products"][0]["prices"]["USD"] = 20
        self.assertTrue(any("not an enabled" in e for e in self.errs(c)))

    def test_usd_allowed_when_enabled_with_two_decimals(self):
        c = self.mut(); c["site"]["currency"]["enabled"] = ["NGN", "USD"]
        c["products"][0]["prices"]["USD"] = 24.99
        self.assertEqual(self.errs(c), [])
        c["products"][0]["prices"]["USD"] = 24.999
        self.assertTrue(any("decimals" in e for e in self.errs(c)))

    def test_ngn_must_be_whole_positive(self):
        for bad in (0, -5, 10.5, "1000", True):
            c = self.mut(); c["products"][0]["prices"]["NGN"] = bad
            self.assertTrue(self.errs(c), bad)

    def test_legacy_price_keys_rejected(self):
        c = self.mut(); c["products"][0]["price"] = 5
        self.assertTrue(any("legacy" in e for e in self.errs(c)))

    def test_sizes_rules(self):
        c = self.mut(); c["products"][0]["sizes"] = [{"label": "M"}, {"label": "M"}]
        self.assertTrue(any("unique" in e for e in self.errs(c)))
        c = self.mut(); c["products"][0]["sizes"] = [{"label": " "}]
        self.assertTrue(any("label" in e for e in self.errs(c)))
        c = self.mut(); c["products"][0]["sizes"] = [{"label": "M", "stock": -1}]
        self.assertTrue(any("stock" in e for e in self.errs(c)))

    def test_colourway_stock_rule(self):
        c = self.mut(); next(iter(c["products"][0]["colourways"].values()))["stock"] = -2
        self.assertTrue(any("stock" in e for e in self.errs(c)))

    def test_flat_product_view_must_exist(self):
        c = self.mut(); p = next(x for x in c["products"] if x.get("source_kind"))
        next(iter(p["colourways"].values()))["views"]["front"] = "sources/nope.png"
        self.assertTrue(any("not found" in e for e in self.errs(c)))

    def test_checkout_contact_validation(self):
        for field, bad in (("whatsapp", "+23480"), ("whatsapp", "12"), ("whatsapp", "0801 234 5678"), ("email", "nobody"), ("email", "a@b")):
            c = self.mut(); c["site"]["checkout"][field] = bad
            self.assertTrue(self.errs(c), (field, bad))
        c = self.mut(); c["site"]["checkout"]["whatsapp"] = "2340000000000"; c["site"]["checkout"]["email"] = "orders@example.test"
        self.assertEqual(self.errs(c), [])

    def test_approval_requires_audit_fields(self):
        c = self.mut(); c["products"][0]["approval"]["description"] = {"status": "approved", "source": None, "by": None, "at": None}
        self.assertTrue(any("no 'by'/'at'" in e for e in self.errs(c)))

    def test_approved_prices_all_null_rejected(self):
        c = self.mut(); approve(c["products"][0], "prices")
        self.assertTrue(any("every price is null" in e for e in self.errs(c)))

    def test_bad_sale_status(self):
        c = self.mut(); c["products"][0]["sale"]["status"] = "yes"
        self.assertTrue(any("sale.status" in e for e in self.errs(c)))

    def test_sale_approval_requires_approved_price_and_sizes(self):
        c = self.mut(); p = c["products"][0]
        p["sale"] = {"status": "approved_for_sale", "by": "owner", "at": "t"}
        e = self.errs(c)
        self.assertTrue(any("requires an approved NGN price" in x for x in e))
        self.assertTrue(any("requires approved sizes" in x for x in e))

    def test_sale_approval_requires_owner_record(self):
        c = self.mut(); p = orderable_product(c); p["sale"] = {"status": "approved_for_sale", "by": None, "at": None}
        self.assertTrue(any("sale approved but has no" in e for e in self.errs(c)))

    def test_fully_approved_product_is_valid_and_ready(self):
        c = self.mut(); p = orderable_product(c)
        self.assertEqual(self.errs(c), [])
        self.assertEqual(vc.readiness(c)[p["sku"]], [])

    def test_old_shop_value_cannot_be_approved_without_owner_record(self):
        c = self.mut(); p = c["products"][0]; p["prices"] = {"NGN": 35000}
        p["approval"]["prices"] = {"status": "approved", "source": "old_shop:bst-shirt", "by": None, "at": None}
        self.assertTrue(any("old shop" in e for e in self.errs(c)))

    def test_duplicate_sku_and_hero(self):
        c = self.mut(); c["products"].append(copy.deepcopy(c["products"][0]))
        self.assertTrue(any("duplicate" in e for e in self.errs(c)))
        c = self.mut(); c["site"]["hero"]["sku"] = "BS-CAMP-001"
        self.assertTrue(any("hero" in e for e in self.errs(c)))

    def _set(self, c):
        return next(p for p in c["products"] if p["sku"] == "BS-BSTSET-001")

    # ---- Stage 1B: the Maximus Motion Studio is the ONLY interactive 3D / splat stage on the site
    def test_bst_set_has_no_standalone_model_and_no_glb_on_disk(self):
        p = self._set(self.mut())
        self.assertEqual(sorted(p["colourways"]), ["blue", "red"])
        self.assertTrue(all("model" not in cw for cw in p["colourways"].values()))
        self.assertEqual(list((STORE / "sources/BS-BSTSET-001").glob("*.glb")), [])
        self.assertEqual(list((STORE / "sources/BS-BSTSET-001").glob("*.report.json")), [])
        self.assertEqual(list((STORE / "media/BS-BSTSET-001").glob("*_model.glb")), [])

    def test_model_key_is_rejected_on_every_kind_of_product(self):
        for sku, cw in (("BS-BSTSET-001", "blue"), ("BS-POLO-001", "black-white"), ("BS-RINGER-001", "white-black")):
            with self.subTest(sku=sku):
                c = self.mut(); p = next(x for x in c["products"] if x["sku"] == sku)
                p["colourways"][cw]["model"] = f"sources/{sku}/{cw}.glb"
                self.assertTrue(any("standalone 3D 'model' keys are retired" in e for e in self.errs(c)), self.errs(c))

    def _ringer(self, c):
        return next(p for p in c["products"] if p["sku"] == "BS-RINGER-001")

    def test_studio_block_is_validated(self):
        c = self.mut(); self._ringer(c)["studio"]["purple"] = {"readiness_state": "pending_dress"}
        self.assertTrue(any("studio.purple: colourway 'purple' is not in this product" in e for e in self.errs(c)))
        c = self.mut(); self._ringer(c)["studio"]["white-black"]["readiness_state"] = "ready-ish"
        self.assertTrue(any("readiness_state must be one of" in e for e in self.errs(c)))
        c = self.mut(); self._ringer(c)["studio"]["white-black"]["manifest"] = "media/maximus_motion/missing.json"
        self.assertTrue(any("manifest not found" in e for e in self.errs(c)))
        c = self.mut(); self._ringer(c)["studio"]["black-white"] = {"readiness_state": "certified_live", "manifest": "media/maximus_motion/m9_dressed_manifest.json"}
        self.assertEqual(self.errs(c), [])           # validator accepts; build.py then proves the GLB belongs to the colourway via the stills / manifest
        c = self.mut(); self._ringer(c)["studio"]["white-black"]["stills"] = [{"finish": "M1", "view": "front", "file": "sources/BS-RINGER-001-MAXIMUS/missing.png"}]
        self.assertTrue(any("must be an existing file under sources/BS-RINGER-001-MAXIMUS/" in e for e in self.errs(c)))

    def test_hardware_macro_must_be_boolean(self):
        c = self.mut(); self._ringer(c)["hardware_macro"] = "yes"
        self.assertTrue(any("hardware_macro must be true or false" in e for e in self.errs(c)))
        c = self.mut(); self._ringer(c)["hardware_macro"] = True
        self.assertEqual(self.errs(c), [])

    def test_cotton_ringer_callouts_may_not_say_wool(self):
        for word in ("Heavyweight wool jersey", "Knitted Ringer Tee"):
            with self.subTest(word=word):
                c = self.mut(); self._ringer(c)["studio"]["white-black"]["anchors"][1]["spec"] = word
                self.assertTrue(any("says wool/knit" in e for e in self.errs(c)))
        self.assertIn("cotton", self._ringer(self.mut())["fabric_spec"].lower())

    def test_lookbook_plates_and_styled_looks_link_only_live_pieces(self):
        c = self.mut(); c["site"]["lookbook_plates"][0]["linked_color"] = "purple"
        self.assertTrue(any("colourway 'purple' is not in BS-TANK-001" in e for e in self.errs(c)))
        c = self.mut(); c["site"]["lookbook_plates"][1]["image"] = "assets/editorial/missing.webp"
        self.assertTrue(any("not found under landing/" in e for e in self.errs(c)))
        c = self.mut(); c["site"]["lookbook_plates"][2]["desktop_crop"] = "centre"
        self.assertTrue(any("desktop_crop must be an object-position" in e for e in self.errs(c)))
        c = self.mut(); c["site"]["lookbook_plates"][0]["plate_type"] = "maximus_editorial"      # BS-TANK-001 is not dressed
        self.assertTrue(any("maximus_editorial plate needs a dressed colourway" in e for e in self.errs(c)))
        c = self.mut(); c["site"]["styled_looks"][0]["items"][0]["linked_sku"] = "BS-NOPE-001"
        self.assertTrue(any("linked SKU 'BS-NOPE-001' is not a live product" in e for e in self.errs(c)))
        plates = REAL["site"]["lookbook_plates"]
        self.assertEqual(len(plates), 5)
        self.assertTrue(all(pl["plate_type"] == "editorial_photo" for pl in plates))     # V1 creative lock: real photography only


class GatingTests(unittest.TestCase):
    def setUp(self):
        self.b = load_build()
        self.site = REAL["site"]

    def entry(self, **kw):
        c = copy.deepcopy(REAL); p = orderable_product(c)
        p.update(kw); return c, p

    def test_all_draft_publishes_nothing(self):
        for p in REAL["products"]:
            if p["status"] != "live":
                continue
            out = self.b.commercial(p, self.site)
            self.assertIsNone(out["price"]); self.assertIsNone(out["currency"]); self.assertIsNone(out["sizes"]); self.assertIsNone(out["prices"])
            self.assertEqual(out["sale_status"], "not_for_sale")

    def test_approved_and_sale_approved_publishes_price(self):
        c, p = self.entry()
        out = self.b.commercial(p, c["site"])
        self.assertEqual(out["price"], 1000); self.assertEqual(out["currency"], "NGN")
        self.assertEqual(out["prices"], {"NGN": 1000}); self.assertEqual(out["sizes"], [{"label": "M", "stock": None}])
        self.assertEqual(out["sale_status"], "approved_for_sale")

    def test_price_approved_but_sale_not_approved_is_not_published(self):
        for st in ("draft", "awaiting_approval", "unavailable"):
            c, p = self.entry(); p["sale"]["status"] = st
            out = self.b.commercial(p, c["site"])
            self.assertIsNone(out["price"], st); self.assertIsNone(out["sizes"], st)
        c, p = self.entry(); p["sale"]["status"] = "unavailable"
        self.assertEqual(self.b.commercial(p, c["site"])["sale_status"], "unavailable")

    def test_sale_approved_but_price_draft_is_not_published(self):
        c, p = self.entry(); p["approval"]["prices"] = {"status": "draft", "source": None, "by": None, "at": None}
        out = self.b.commercial(p, c["site"])
        self.assertIsNone(out["price"]); self.assertIsNone(out["currency"])
        self.assertIsNotNone(out["sizes"])      # sizes are separately approved

    def test_descriptive_field_needs_its_own_approval_only(self):
        c, p = self.entry(); p["description"] = "Test description"
        self.assertIsNone(self.b.commercial(p, c["site"])["description"])
        approve(p, "description")
        self.assertEqual(self.b.commercial(p, c["site"])["description"], "Test description")
        p["sale"]["status"] = "draft"           # description does not depend on sale approval
        self.assertEqual(self.b.commercial(p, c["site"])["description"], "Test description")

    def test_price_in_disabled_currency_never_published(self):
        c, p = self.entry(); p["prices"] = {"NGN": 1000, "USD": 25}
        out = self.b.commercial(p, c["site"])
        self.assertEqual(out["prices"], {"NGN": 1000})

    def test_no_conversion_between_currencies(self):
        c, p = self.entry(); p["prices"] = {"NGN": None, "USD": 25}; c["site"]["currency"] = {"default": "NGN", "enabled": ["NGN", "USD"]}
        out = self.b.commercial(p, c["site"])
        self.assertIsNone(out["price"]); self.assertIsNone(out["currency"])      # USD price is NOT turned into an NGN price
        self.assertEqual(out["prices"], {"USD": 25})

    def test_dual_currency_publishes_each_price(self):
        c, p = self.entry(); p["prices"] = {"NGN": 1000, "USD": 2.5}; c["site"]["currency"] = {"default": "NGN", "enabled": ["NGN", "USD"]}
        out = self.b.commercial(p, c["site"])
        self.assertEqual(out["prices"], {"NGN": 1000, "USD": 2.5}); self.assertEqual(out["price"], 1000)

    def test_provisional_preview_flag(self):
        b = load_build(provisional=True)
        try:
            c, p = self.entry(); p["approval"]["prices"] = {"status": "draft", "source": None, "by": None, "at": None}; p["sale"]["status"] = "draft"
            out = b.commercial(p, c["site"])
            self.assertEqual(out["price"], 1000)            # draft values visible in the local preview...
            self.assertTrue(b.PREVIEW_PROVISIONAL)
        finally:
            load_build(provisional=False)
        self.assertIsNone(self.b.commercial(p, c["site"])["price"])      # ...and never in a normal build

    def test_build_and_validator_agree_on_orderability(self):
        """Whatever the validator calls ready is exactly what the build publishes as purchasable."""
        states = []
        c, p = self.entry(); states.append((c, p))
        c, p = self.entry(); p["sale"]["status"] = "awaiting_approval"; states.append((c, p))
        c, p = self.entry(); p["approval"]["sizes"]["status"] = "draft"; states.append((c, p))
        c, p = self.entry(); p["prices"] = {"NGN": None}; states.append((c, p))
        for c, p in states:
            ready = vc.readiness(c)[p["sku"]] == []
            out = self.b.commercial(p, c["site"])
            published_orderable = out["price"] is not None and bool(out["sizes"]) and out["sale_status"] == "approved_for_sale"
            self.assertEqual(ready, published_orderable, p["sale"])


if __name__ == "__main__":
    unittest.main()
