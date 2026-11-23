"""Unit tests: the mannequin imagery gate (owner rule 1) in tools/validate_catalogue.py.

    python -I -m unittest discover -s tools/tests -v
"""
import copy
import json
import sys
import unittest
from pathlib import Path

CANON = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CANON / "tools"))
import validate_catalogue as vc  # noqa: E402

REAL = json.loads((CANON / "catalogue/data/catalogue.json").read_text(encoding="utf-8"))
STORE = CANON / "catalogue"
APPROVED = {"mannequin_imagery_approved": {"by": "Owner", "at": "2026-11-11T12:00:00"}}


def gate_errors(errors, sku=None):
    """Gate messages; with sku, only those about that product (the real catalogue also carries owner-approved 3D models)."""
    return [e for e in errors if "mannequin_imagery_approved" in e and (sku is None or f" {sku}:" in e)]


class MannequinGateTests(unittest.TestCase):
    def first_product(self):
        cat = copy.deepcopy(REAL)
        return cat, cat["products"][0]

    def test_non_empty_mannequin_set_fails_without_owner_approval(self):
        cat, p = self.first_product()
        p["mannequin_set"] = ["M1"]
        e, _ = vc.validate(cat, STORE, owner_decisions={})
        msgs = gate_errors(e, p["sku"])
        self.assertEqual(len(msgs), 1, msgs)
        self.assertIn("mannequin_set", msgs[0])

    def test_glb_gltf_and_mannequin_file_references_fail_without_owner_approval(self):
        cases = [
            ("preview_model", "media/BS-X/model.glb"),
            ("preview_model", "media/BS-X/model.gltf"),
            ("preview_image", "media/BS-X/white_mannequin_front_full.webp"),
        ]
        for field, value in cases:
            with self.subTest(value=value):
                cat, p = self.first_product()
                p[field] = value
                e, _ = vc.validate(cat, STORE, owner_decisions={})
                msgs = gate_errors(e, p["sku"])
                self.assertEqual(len(msgs), 1, msgs)
                self.assertIn(field, msgs[0])

    def test_owner_approval_with_non_empty_by_and_at_clears_the_gate(self):
        cat, p = self.first_product()
        p["mannequin_set"] = ["M1"]
        p["preview_model"] = "media/BS-X/model.glb"
        e, _ = vc.validate(cat, STORE, owner_decisions=APPROVED)
        self.assertEqual(gate_errors(e), [])
        for bad in [{"by": "Owner"}, {"at": "2026-11-11"}, {"by": " ", "at": "2026-11-11"}, "yes", None]:
            with self.subTest(owner=bad):
                e, _ = vc.validate(cat, STORE, owner_decisions={"mannequin_imagery_approved": bad})
                self.assertTrue(gate_errors(e), "approval without both by and at must not clear the gate")

    def test_current_catalogue_passes_the_gate(self):
        # Stage 1B purged the BST set's standalone 3D models: the live catalogue now names no .glb / .gltf and no mannequin render
        # file at all (the Maximus studio assets are referenced through the studio manifest, a .json), so nothing is gated
        gated = {(p["sku"], f) for p in REAL["products"] for f, _ in vc.mannequin_gate_problems(p)}
        self.assertEqual(gated, set())
        owner = vc.load_owner_decisions()
        self.assertTrue(vc.mannequin_imagery_approved(owner))
        e, _ = vc.validate(REAL, STORE)
        self.assertEqual(gate_errors(e), [])
        e, _ = vc.validate(REAL, STORE, owner_decisions={})
        self.assertEqual(gate_errors(e), [])


if __name__ == "__main__":
    unittest.main()
