"""Stage 1B: one canonical record per SKU / colourway drives both pages, and the Maximus Motion Studio is the ONLY 3D stage.

    python -I -m unittest discover -s tools/tests -v

Reads the BUILT catalogue/data/products.json and catalogue/data/identity.json (catalogue/build.py) and the page sources.
  * identity.json conforms to config/product_identity.schema.json and mirrors products.json one-to-one.
  * readiness is honest: dressed_ready only where the product's studio block names a certified dressed GLB; Maximus stills
    only on dressed_ready colourways, each with a record tying it to that GLB's sha256.
  * deep links follow the routing contract (/fitting/?sku=&color= <-> #/p/<SKU>/<COLOR>).
  * nothing outside Page 2 mounts 3D: no "model" presentation, no .glb/.sog outside media/maximus_motion, viewer3d.js refuses
    non-studio mounts and is imported once (by the studio), no PDP 3D markup or CSS remains.
"""
import json
import re
import sys
import unittest
from pathlib import Path

CANON = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CANON / "tools"))
import validate_catalogue as vc  # noqa: E402

STORE = CANON / "catalogue"
PRODUCTS = json.loads((STORE / "data/products.json").read_text(encoding="utf-8"))
IDENTITY = json.loads((STORE / "data/identity.json").read_text(encoding="utf-8"))
CATALOGUE = json.loads((STORE / "data/catalogue.json").read_text(encoding="utf-8"))
STORE_JS = (STORE / "js/store.js").read_text(encoding="utf-8")
VIEWER_JS = (STORE / "js/viewer3d.js").read_text(encoding="utf-8")
STORE_CSS = (STORE / "css/store.css").read_text(encoding="utf-8")


def walk(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from walk(v, f"{path}.{k}" if path else k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, o


class CanonicalIdentity(unittest.TestCase):
    def test_identity_conforms_to_the_schema(self):
        try:
            import jsonschema  # noqa: F401
        except ImportError:
            self.skipTest("jsonschema not installed")
        self.assertEqual(vc.validate_identity(IDENTITY), [])
        self.assertEqual(IDENTITY["routing"]["fitting_query_template"], "/fitting/?sku={sku}&color={colourway}")

    def test_identity_mirrors_products_one_to_one(self):
        keys = lambda d: [(p["sku"], c["id"]) for p in d["products"] for c in p["colourways"]]
        self.assertEqual(keys(IDENTITY), keys(PRODUCTS))
        for p, ip in zip(PRODUCTS["products"], IDENTITY["products"]):
            for c, ic in zip(p["colourways"], ip["colourways"]):
                for k in ("merch_views", "mannequin_views", "links"):
                    self.assertEqual(c[k], ic[k], f"{p['sku']}/{c['id']} {k}")
                self.assertEqual(c["studio_assets"]["readiness_state"], ic["studio_assets"]["readiness_state"])

    def test_every_colourway_has_unworn_merch_and_a_readiness(self):
        for p in PRODUCTS["products"]:
            for c in p["colourways"]:
                self.assertGreaterEqual(len(c["merch_views"]), 1, f"{p['sku']}/{c['id']}")
                self.assertIn(c["readiness"], ("dressed_ready", "dressing_pending"))
                self.assertTrue(all(v["aspect"] == 0.72 for v in c["merch_views"] + c["mannequin_views"]))

    def test_readiness_is_never_fabricated(self):
        studio = {(p["sku"], cw): b for p in CATALOGUE["products"] for cw, b in (p.get("studio") or {}).items() if not cw.startswith("_")}
        for p in PRODUCTS["products"]:
            for c in p["colourways"]:
                b = studio.get((p["sku"], c["id"]))
                dressed = bool(b) and b["readiness_state"] in ("certified_live", "c0_only_pending_splat")
                self.assertEqual(c["readiness"] == "dressed_ready", dressed, f"{p['sku']}/{c['id']}")
                if c["readiness"] != "dressed_ready":
                    self.assertEqual(c["mannequin_views"], [], f"{p['sku']}/{c['id']}: Maximus views on a pending colourway")
                    self.assertIsNone(c["studio_assets"]["glb"])
        ready = [(p["sku"], c["id"]) for p in PRODUCTS["products"] for c in p["colourways"] if c["readiness"] == "dressed_ready"]
        self.assertEqual(ready, [("BS-RINGER-001", "white-black")])      # Phase 3 has certified exactly one dressed look so far

    def test_maximus_stills_are_tied_to_the_certified_glb(self):
        p = next(x for x in PRODUCTS["products"] if x["sku"] == "BS-RINGER-001")
        c = next(x for x in p["colourways"] if x["id"] == "white-black")
        glb = c["studio_assets"]["glb"]
        self.assertLessEqual(glb["bytes"], 1258291)
        self.assertTrue(glb["meshopt"])
        self.assertIn("size_XXL", glb["morph_targets"])
        for s in next(x for x in CATALOGUE["products"] if x["sku"] == "BS-RINGER-001")["studio"]["white-black"]["stills"]:
            rec = json.loads((STORE / s["file"]).with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(rec["glb_sha256"], glb["sha256"])
            self.assertEqual((rec["sku"], rec["colourway"], rec["kind"]), ("BS-RINGER-001", "white-black", "maximus_studio_still"))
        self.assertEqual(sorted(v["finish"] for v in c["mannequin_views"]), ["M1", "M2", "M3"])
        for v in c["mannequin_views"]:
            self.assertTrue((STORE / v["tile"]).is_file() and (STORE / v["full"]).is_file(), v)

    def test_ringer_studio_copy_is_cotton_not_wool(self):
        p = next(x for x in PRODUCTS["products"] if x["sku"] == "BS-RINGER-001")
        self.assertEqual(p["fabric_spec"], "280 GSM heavyweight cotton jersey")
        text = json.dumps(next(c for c in p["colourways"] if c["id"] == "white-black")["studio_assets"]["anchors"])
        self.assertNotRegex(text.lower(), r"wool|knit")
        self.assertIn("280 GSM heavyweight cotton jersey", text)
        self.assertNotRegex(STORE_JS, r"(?i)knitted|wool")

    def test_deep_links_follow_the_routing_contract(self):
        for p in PRODUCTS["products"]:
            for c in p["colourways"]:
                self.assertEqual(c["links"], {
                    "page1_hash": f"#/p/{p['sku']}/{c['id']}",
                    "page2_fitting_url": f"/fitting/?sku={p['sku']}&color={c['id']}",
                    "page2_fitting_hash": f"catalogue/#/fitting?sku={p['sku']}&color={c['id']}"})
        self.assertEqual(PRODUCTS["routing"]["page2_fitting_path"], "/fitting/")


class OnlyTheStudioIs3D(unittest.TestCase):
    def test_no_model_presentation_and_no_3d_reference_outside_studio_assets(self):
        for p in PRODUCTS["products"]:
            for c in p["colourways"]:
                self.assertFalse(any(r["presentation"] == "model" or "model" in r for r in c["renders"]), f"{p['sku']}/{c['id']}")
        stray = [(path, v) for path, v in walk(PRODUCTS) if isinstance(v, str) and re.search(r"\.(glb|gltf|sog|ply|splat)$", v, re.I)
                 and ".studio_assets." not in f".{path}."]
        self.assertEqual(stray, [])

    def test_3d_assets_live_only_in_media_maximus_motion(self):
        found = [f.relative_to(STORE).as_posix() for ext in ("glb", "gltf", "sog", "ply", "splat") for f in (STORE / "media").rglob(f"*.{ext}")]
        self.assertTrue(found)
        self.assertEqual([f for f in found if not f.startswith("media/maximus_motion/")], [])
        self.assertEqual([f.name for f in (STORE / "sources").rglob("*.glb")], [])

    def test_viewer_refuses_non_studio_mounts_and_is_imported_once(self):
        self.assertIn('if (!studioMode) throw new Error("viewer3d: only the Maximus Motion Studio may mount a 3D stage', VIEWER_JS)
        self.assertEqual(STORE_JS.count('import("./viewer3d.js")'), 1)
        i = STORE_JS.index('import("./viewer3d.js")')
        self.assertGreater(i, STORE_JS.index("async function mountMotionMode("))
        self.assertLess(i, STORE_JS.index("function selectLook("))
        self.assertEqual(STORE_JS.count("studioMode: true"), 1)

    def test_no_product_page_3d_markup_or_styles_remain(self):
        for bad in ("open3d", "model-stage", "thumb-3d", "macro-3d", "zipper_hero_instanced", 'presentation === "model"'):
            self.assertNotIn(bad, STORE_JS, bad)
        for bad in ('data-presentation="model"', ".model-stage", ".thumb-3d", ".macro-3d"):
            self.assertNotIn(bad, STORE_CSS, bad)
        self.assertFalse((STORE / "tests/model3d.mjs").exists())

    def test_hardware_macro_panel_is_opt_in_and_off_for_every_current_piece(self):
        self.assertIn("${p.hardware_macro === true ? '<details class=\"macro-craft-acc\" id=\"macro-craft-acc\">", STORE_JS)
        self.assertEqual(STORE_JS.count('id="macro-craft-acc"'), 1)
        self.assertTrue(all(p["hardware_macro"] is False for p in PRODUCTS["products"]))
        self.assertFalse(any("hardware_macro" in p for p in CATALOGUE["products"]))

    def test_set_sheet_view_reads_front_and_back(self):
        self.assertIn('sheet: "Front + Back"', STORE_JS)
        self.assertIn('VIEW_LABEL[r.sheet_ground && r.view === "front" ? "sheet" : r.view]', STORE_JS)
        bst = next(p for p in PRODUCTS["products"] if p["sku"] == "BS-BSTSET-001")
        self.assertTrue(all(c["renders"][0]["sheet_ground"] and c["merch_views"][0]["view"] == "sheet" for c in bst["colourways"]))

    def test_page1_never_loads_3d(self):
        for f in ("landing/index.html", "landing/assets/js/landing.js"):
            s = (CANON / f).read_text(encoding="utf-8").lower()
            for bad in ("viewer3d", "three.module", "@sparkjsdev", "importmap", ".glb", ".sog"):
                self.assertNotIn(bad, s, f"{f}: {bad}")


if __name__ == "__main__":
    unittest.main()
