"""Unit tests: mannequin views in the three canonical finishes (M1 / M2 / M3).

    python -I -m unittest discover -s tools/tests -v

build.py is imported as a module with STORE / MEDIA pointed at a temporary folder of tiny synthetic renders (write=False: no image
export); engine inputs (SKU config, finishes, garment families) are the real, read-only engine_inputs. Also covers the targeted
catalogue.json edit that 02_engine_3d/colab_heavy/apply_results.py makes when it wires a finish folder in.
"""
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

CANON = Path(__file__).resolve().parents[2]
STORE = CANON / "catalogue"
APPLY = CANON.parent / "02_engine_3d/colab_heavy/apply_results.py"
SKU, CW = "BS-POLO-001", "burgundy-white"
SITE = {"currency": {"default": "NGN", "enabled": ["NGN"]}, "checkout": {}}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def png(path, rgba):
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGBA" if rgba else "RGB", (8, 10), (120, 20, 30, 255) if rgba else (243, 238, 231))
    if rgba:
        im.putpixel((0, 0), (0, 0, 0, 0))                     # real transparency, like a film_transparent render
    im.save(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_set(root, rel, renders, skin=None):
    d = root / rel
    log = []
    for r in renders:
        log.append(dict(r, sha256=png(d / r["file"], r["mode"] == "mannequin"), res=[8, 10], colorway=r.get("colorway", CW)))
    prov = {"image_files": {f"assets/renders/{SKU}/run/{skin}/_skin_{skin}.png": "0" * 64}} if skin else {}
    (d / "render_log.json").write_text(json.dumps({"sku": SKU, "complete": True, "provenance": prov, "renders": log}), encoding="utf-8")


def mq(fid, views=((0, "front"), (45, "three_quarter"), (180, "back")), with_finish=True):
    return [dict(file=f"{SKU}_{CW}_mannequin_{v}.png", mode="mannequin", angle=a, **({"finish": fid} if with_finish else {})) for a, v in views]


class MannequinFinishBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bs_mq_"))
        os.environ.pop("BS_PREVIEW_PROVISIONAL", None)
        self.b = load("bs_build_mq", STORE / "build.py")
        self.b.STORE, self.b.MEDIA = self.tmp, self.tmp / "media"
        write_set(self.tmp, "sources/floating", [dict(file=f"{SKU}_{CW}_floating_{v}.png", mode="floating", angle=a, background_hex="#F3EEE7")
                                                 for a, v in ((0, "front"), (35, "three_quarter"), (180, "back"))])
        for f in ("M1", "M2", "M3"):
            write_set(self.tmp, f"sources/{SKU}-MANNEQUIN/{f}", mq(f))
        self.families = self.b.family_index()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def entry(self, dirs):
        return {"sku": SKU, "status": "live", "render_dir": "sources/floating", "extra_render_dirs": dirs,
                "colourways": {CW: {"label": "Red / White", "stock": None}}, "approval": {}, "sale": {"status": "draft"}}

    def build(self, dirs):
        return self.b.build_product(self.entry(dirs), self.families, False, SITE)

    def test_three_finishes_build_in_order_with_distinct_files(self):
        p = self.build([f"sources/{SKU}-MANNEQUIN/{f}" for f in ("M3", "M1", "M2")])   # wiring order must not matter
        rs = p["colourways"][0]["renders"]
        self.assertEqual([(r["presentation"], r["finish"], r["view"]) for r in rs],
                         [("floating", None, v) for v in ("front", "three_quarter", "back")]
                         + [("mannequin", f, v) for f in ("M1", "M2", "M3") for v in ("front", "three_quarter", "back")])
        self.assertEqual(len({r["tile"] for r in rs}), len(rs), "each finish needs its own media file")
        names = {r["finish"]: r["mannequin_finish"] for r in rs if r["finish"]}
        finishes = json.loads((STORE / "engine_inputs/configs/mannequin_finishes.json").read_text())["finishes"]
        self.assertEqual(names, {k: v["name"] for k, v in finishes.items()})
        self.assertTrue(all(r["background"] is None for r in rs if r["presentation"] == "mannequin"), "RGBA renders sit on the CSS studio")
        self.assertTrue(all(r["finish_hex"] == finishes[r["finish"]]["hex"] for r in rs if r["finish"]))
        self.assertEqual(p["mannequin_default_finish"], "M2")

    def test_default_finish_falls_back_to_one_the_product_has(self):
        p = self.build([f"sources/{SKU}-MANNEQUIN/M1", f"sources/{SKU}-MANNEQUIN/M3"])
        self.assertEqual(p["mannequin_default_finish"], "M1")
        self.assertIsNone(self.build([])["mannequin_default_finish"])

    def test_log_without_a_finish_uses_the_sku_finish(self):
        write_set(self.tmp, "sources/legacy", mq(None, views=((0, "front"),), with_finish=False))
        rs = self.build(["sources/legacy"])["colourways"][0]["renders"]
        self.assertEqual([r["finish"] for r in rs if r["presentation"] == "mannequin"], ["M2"])

    def test_two_renders_for_one_slot_fail_the_build(self):
        with self.assertRaises(self.b.BuildError):
            self.build([f"sources/{SKU}-MANNEQUIN/M2", f"sources/{SKU}-MANNEQUIN/M2"])

    def test_logs_without_finish_take_it_from_the_skin_or_the_folder(self):
        # render_sku.py before 2026-11-15 wrote no "finish": the run's provenance names the skin it loaded (_skin_M1.png)
        for f in ("M1", "M2", "M3"):
            write_set(self.tmp, f"sources/old/{f}", mq(f, with_finish=False), skin=f)
        write_set(self.tmp, "sources/folder_only/M3", mq("M3", views=((0, "front"),), with_finish=False))
        rs = self.build([f"sources/old/{f}" for f in ("M1", "M2", "M3")])["colourways"][0]["renders"]
        self.assertEqual(sorted({r["finish"] for r in rs if r["finish"]}), ["M1", "M2", "M3"])
        rs = self.build(["sources/folder_only/M3"])["colourways"][0]["renders"]
        self.assertEqual([r["finish"] for r in rs if r["finish"]], ["M3"])

    def test_skin_and_folder_that_disagree_fail_the_build(self):
        write_set(self.tmp, "sources/misfiled/M3", mq("M3", with_finish=False), skin="M1")
        with self.assertRaises(self.b.BuildError):
            self.build(["sources/misfiled/M3"])

    def test_unknown_finish_fails_the_build(self):
        write_set(self.tmp, "sources/bad", mq("M9"))
        with self.assertRaises(self.b.BuildError):
            self.build(["sources/bad"])


class CatalogueWiringTests(unittest.TestCase):
    """apply_results.set_extra_render_dirs: one product's extra_render_dirs changes, nothing else in the hand-laid file does."""

    @classmethod
    def setUpClass(cls):
        cls.a = load("bs_apply_mq", APPLY)
        cls.text = (STORE / "data/catalogue.json").read_text(encoding="utf-8")
        cls.cat = json.loads(cls.text)

    def check(self, sku, dirs):
        out = self.a.set_extra_render_dirs(self.text, sku, dirs)
        new = json.loads(out)
        want = copy.deepcopy(self.cat)
        next(p for p in want["products"] if p["sku"] == sku)["extra_render_dirs"] = dirs
        self.assertEqual(new, want)
        changed = [l for l in out.splitlines() if l not in self.text.splitlines()]
        self.assertEqual(len(changed), 1, changed)            # a single line edited or added; the layout is kept

    def test_replace_existing_list(self):
        self.check("BS-POLO-001", ["sources/BS-POLO-001-MANNEQUIN/M1", "sources/BS-POLO-001-MANNEQUIN/M2", "sources/BS-POLO-001-MANNEQUIN/M3"])

    def test_add_list_to_a_product_without_one(self):
        self.check("BS-CAMP-002", ["sources/BS-CAMP-002-MANNEQUIN/M2"])


class ApplyMannequinTests(unittest.TestCase):
    """apply_results.apply_mannequin on a synthetic COLAB_RESULTS/catalogue_mannequin_renders folder, into a temporary storefront copy."""

    def setUp(self):
        self.a = load("bs_apply_mq2", APPLY)
        self.tmp = Path(tempfile.mkdtemp(prefix="bs_apply_"))
        self.store = self.tmp / "catalogue"; (self.store / "data").mkdir(parents=True)
        shutil.copy2(STORE / "data/catalogue.json", self.store / "data/catalogue.json")
        (self.store / "sources/BS-POLO-001-MANNEQUIN").mkdir(parents=True)                   # the legacy root set stays on disk
        self.a.STORE, self.a.CATALOGUE = self.store, self.store / "data/catalogue.json"
        self.M = self.tmp / "catalogue_mannequin_renders"; steps = []
        cws = {"BS-POLO-001": ["black-white", "burgundy-white"], "BS-CAMP-002": ["black-plaid"]}
        for sku, ids in cws.items():
            for f in ("M1", "M2", "M3"):
                d = self.M / sku / f; log = []
                for cw in ids:
                    for a, v in ((0, "front"), (45, "three_quarter"), (180, "back")):
                        n = f"{sku}_{cw}_mannequin_{v}.png"
                        log.append(dict(file=n, colorway=cw, mode="mannequin", finish=f, angle=a, sha256=png(d / n, True)))
                (d / "render_log.json").write_text(json.dumps(dict(sku=sku, complete=True, renders=log)))
                ok = not (sku == "BS-CAMP-002" and f == "M3")                                 # one finish fails the run's own check
                steps.append(dict(step=f"{sku}_{f}", sku=sku, finish=f, exit=0, ok=ok, problems=[] if ok else ["synthetic failure"]))
        steps.append(dict(step="BS-RINGER-001", skipped=True, reason="master quality gate did not pass: stays floating-only"))
        (self.M / "JOB_STATUS.json").write_text(json.dumps(dict(job="mannequin", status="FAIL", steps=steps)))
        files = sorted(p for p in self.M.rglob("*") if p.is_file())
        (self.M / "RESULTS_MANIFEST.sha256").write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(self.M).as_posix()}\n" for p in files))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_passing_folders_are_copied_and_wired_failing_ones_refused(self):
        rep = self.a.apply_mannequin(self.M, "t", False)
        cat = {p["sku"]: p for p in json.loads((self.store / "data/catalogue.json").read_text())["products"]}
        self.assertEqual(cat["BS-POLO-001"]["extra_render_dirs"], [f"sources/BS-POLO-001-MANNEQUIN/{f}" for f in ("M1", "M2", "M3")])   # legacy root unwired
        self.assertEqual(cat["BS-CAMP-002"]["extra_render_dirs"], [f"sources/BS-CAMP-002-MANNEQUIN/{f}" for f in ("M1", "M2")])
        self.assertIn("M3", rep["products"]["BS-CAMP-002"]["refused"])
        self.assertFalse((self.store / "sources/BS-CAMP-002-MANNEQUIN/M3").exists())
        self.assertEqual(len(list((self.store / "sources/BS-POLO-001-MANNEQUIN/M2").glob("*.png"))), 6)
        self.assertTrue((self.store / "sources/BS-POLO-001-MANNEQUIN").is_dir())
        self.assertEqual([s["step"] for s in rep["skipped"]], ["BS-RINGER-001"])
        self.assertTrue((self.store / "data/catalogue.pre_colab_t.json").exists(), "the owner file is backed up before it is edited")

    def test_tampered_image_is_refused(self):
        p = self.M / "BS-CAMP-002/M1/BS-CAMP-002_black-plaid_mannequin_front.png"
        p.write_bytes(p.read_bytes() + b"x")
        with self.assertRaises(SystemExit):                    # RESULTS_MANIFEST check stops before anything is copied
            self.a.apply_mannequin(self.M, "t", False)
        self.assertFalse((self.store / "sources/BS-CAMP-002-MANNEQUIN").exists())


if __name__ == "__main__":
    unittest.main()
