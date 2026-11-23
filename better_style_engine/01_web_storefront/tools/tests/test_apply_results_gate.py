"""Batch 1 regression tests for colab_heavy/apply_results.py (--skus allowlist & SHA-256 verification gate)."""
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

APPLY_PATH = Path(__file__).resolve().parents[3] / "02_engine_3d" / "colab_heavy" / "apply_results.py"
spec = importlib.util.spec_from_file_location("apply_results_under_test", str(APPLY_PATH))
AR = importlib.util.module_from_spec(spec)
spec.loader.exec_module(AR)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class TestApplyResultsSafetyGate(unittest.TestCase):
    def test_default_live_dirs_excludes_camp_shirts(self):
        self.assertNotIn("BS-CAMP-001", AR.LIVE_DIRS)
        self.assertNotIn("BS-CAMP-002", AR.LIVE_DIRS)
        self.assertIn("BS-POLO-001", AR.LIVE_DIRS)

    def test_verify_rejects_sha256_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            img = root / "sample.png"
            img.write_bytes(b"real-bytes")
            (root / "RESULTS_MANIFEST.sha256").write_text(f"{'0' * 64}  sample.png\n", encoding="utf-8")
            with self.assertRaises(SystemExit) as ctx:
                AR.verify(root)
            self.assertIn("missing or corrupt", str(ctx.exception))

    def test_verify_passes_matching_sha256(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            img = root / "sample.png"
            img.write_bytes(b"good-bytes")
            (root / "RESULTS_MANIFEST.sha256").write_text(f"{_sha(img)}  sample.png\n", encoding="utf-8")
            AR.verify(root)  # should not raise

    def test_apply_mannequin_enforces_skus_allowlist(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            job = {
                "job": "mannequin",
                "status": "ok",
                "steps": [
                    {"sku": "BS-CAMP-001", "finish": "M1", "exit": 0, "ok": True},
                    {"sku": "BS-CAMP-002", "finish": "M2", "exit": 0, "ok": True},
                ],
            }
            js_path = root / "JOB_STATUS.json"
            js_path.write_text(json.dumps(job), encoding="utf-8")
            (root / "RESULTS_MANIFEST.sha256").write_text(f"{_sha(js_path)}  JOB_STATUS.json\n", encoding="utf-8")
            rep = AR.apply_mannequin(root, "20261119_test", dry=True, allowed_skus={"BS-POLO-001"})
            self.assertFalse(rep["any_applied"])
            self.assertIn("blocked by --skus allowlist", rep["products"]["BS-CAMP-001"]["refused"]["*"])
            self.assertIn("blocked by --skus allowlist", rep["products"]["BS-CAMP-002"]["refused"]["*"])


if __name__ == "__main__":
    unittest.main()
