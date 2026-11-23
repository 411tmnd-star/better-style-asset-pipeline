"""The old-shop reference data is complete, provenance-tagged, read-only, and kept out of the live catalogue."""
import hashlib
import json
import os
import stat
import unittest
from pathlib import Path

CANON = Path(__file__).resolve().parents[2]
REF = CANON / "reference/old_shop"
EXTRACT = json.loads((REF / "old_shop_extract.json").read_text(encoding="utf-8"))
CAT = json.loads((CANON / "catalogue/data/catalogue.json").read_text(encoding="utf-8"))


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class ReferenceDataTests(unittest.TestCase):
    def test_counts_match_the_inspected_old_shop(self):
        c = EXTRACT["counts"]
        self.assertEqual((c["products"], c["variants"], c["images"]), (11, 41, 16))
        self.assertEqual(len(EXTRACT["records"]), 11)
        self.assertEqual(sum(len(r["variants"]) for r in EXTRACT["records"]), 41)
        self.assertEqual(sum(len(r["images"]) for r in EXTRACT["records"]), 16)

    def test_every_record_has_provenance_and_classification(self):
        for r in EXTRACT["records"]:
            self.assertIsNotNone(r["old_product_id"], r["old_slug"])
            for f in ("description", "price_ngn", "colours", "sizes"):
                self.assertTrue(r[f]["source"] and r[f]["class"], (r["old_slug"], f))
            self.assertEqual(r["price_ngn"]["class"], "PROVISIONAL")
            self.assertTrue(all(v["placeholder_stock"]["class"] == "PLACEHOLDER_NOT_INVENTORY" for v in r["variants"]))
            self.assertEqual(r["verification_status"], "UNVERIFIED_BY_OWNER")
        for k in ("seed", "db"):
            self.assertEqual(len(EXTRACT["sources"][k]["sha256"]), 64)

    def test_no_mapping_is_owner_confirmed_and_weak_ones_are_unresolved(self):
        n = 0
        for r in EXTRACT["records"]:
            pm = r["proposed_match"]
            if not pm:
                continue
            n += 1
            self.assertFalse(pm["owner_confirmed"])
            self.assertEqual(pm["class"], "INFERRED_UNCONFIRMED" if pm["confidence"] == "strong" else "UNRESOLVED")
            self.assertTrue(pm["basis"] and pm["conflicts"])
        self.assertEqual(n, 5)

    def test_manifest_hashes_match_files(self):
        man = json.loads((REF / "MANIFEST.json").read_text(encoding="utf-8"))["files"]
        for rel, m in man.items():
            self.assertEqual(sha(REF / rel), m["sha256"], rel)
        for r in EXTRACT["records"]:
            for im in r["images"]:
                self.assertEqual(sha(REF / im["file"]), im["sha256"])

    def test_reference_files_are_read_only(self):
        for p in REF.rglob("*"):
            if p.is_file():
                self.assertFalse(os.stat(p).st_mode & stat.S_IWRITE, f"{p} is writable")

    def test_nothing_from_the_old_shop_is_in_the_live_catalogue(self):
        old_prices = {r["price_ngn"]["value"] for r in EXTRACT["records"]}
        for p in CAT["products"]:
            if p["status"] != "live":
                continue
            self.assertTrue(all(v is None for v in p["prices"].values()), p["sku"])
            self.assertEqual(p["sale"]["status"], "draft")
            for f, a in p["approval"].items():
                self.assertEqual(a["status"], "draft", (p["sku"], f))
                self.assertFalse(str(a.get("source") or "").startswith("old_shop:"), (p["sku"], f))
            self.assertTrue(not (set(v for v in p["prices"].values() if v) & old_prices))
        self.assertNotIn("old_shop", json.dumps(CAT))

    def test_no_django_code_in_the_project(self):
        bad = [p for p in CANON.rglob("*") if p.is_file() and ".git" not in p.parts and "node_modules" not in p.parts
               and p.name in ("models.py", "views.py", "wsgi.py", "manage.py", "settings.py", "admin.py")]
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
