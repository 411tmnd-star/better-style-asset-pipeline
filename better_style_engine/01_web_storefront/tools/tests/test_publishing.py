"""Publishing: candidate -> approve -> publish -> rollback, with every refusal path.

Uses a temporary project copy: real validator, real assembly and real scan; the image build, git and Vercel are fakes, so
nothing here can touch a live site (the Vercel fake only records the commands it would have run).
"""
import copy
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

CANON = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CANON / "tools"))
import publishing as pb  # noqa: E402

REAL_CAT = json.loads((CANON / "catalogue/data/catalogue.json").read_text(encoding="utf-8"))


def load_build():
    os.environ.pop("BS_PREVIEW_PROVISIONAL", None)
    spec = importlib.util.spec_from_file_location("bs_build_pub_test", CANON / "catalogue/build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BUILD = load_build()


class FakeSteps:
    """Replaces build/git/vercel. build() writes a catalogue/dist from the draft using the REAL commercial() gate."""

    def __init__(self, project, leak=False):
        self.p, self.leak, self.calls, self.vercel_calls = project, leak, [], []
        self.vercel_out = {"deploy": (0, "Production: https://better-style-shop-newdeploy-411-004b.vercel.app [3s]"), "promote": (0, "ok"), "rollback": (0, "ok")}

    def build(self, mode, provisional=False):
        self.calls.append(("build", mode, provisional))
        cat = json.loads(self.p.cat_json.read_text(encoding="utf-8"))
        dist = self.p.catalogue / "dist"
        if dist.exists():
            shutil.rmtree(dist)
        (dist / "data").mkdir(parents=True)
        (dist / "media").mkdir()
        (dist / "media/a.webp").write_bytes(b"RIFFfake")
        (dist / "index.html").write_text("<html>catalogue</html>", encoding="utf-8")
        site = {**cat["site"], "currency": {**cat["site"]["currency"], "formats": {}}}
        products = []
        for e in cat["products"]:
            if e["status"] != "live":
                continue
            c = BUILD.commercial(e, cat["site"])
            if self.leak:
                c["price"] = 999
            products.append({"sku": e["sku"], "title": e.get("title") or e["sku"], **c,
                             "colourways": [{"id": k, "label": v["label"], "stock": v.get("stock"), "renders": []} for k, v in e["colourways"].items()]})
        out = {"schema": "betterstyle.storefront_products/1", "site": site, "products": products, "looks": []}
        if provisional:
            out["preview_provisional"] = True
        (dist / "data/products.json").write_text(json.dumps(out), encoding="utf-8")
        return "fake build ok"

    def assemble(self, allow_provisional=False):
        import assemble_site
        return assemble_site.assemble(canon=self.p.root, allow_provisional=allow_provisional)

    def git(self, args):
        self.calls.append(("git", *args))
        if args[:1] == ["rev-parse"]:
            return 0, "0123456789abcdef0123456789abcdef01234567\n"
        return 0, ""

    def vercel(self, args, cwd):
        self.vercel_calls.append(args)
        return self.vercel_out[args[0]]


def make_project(tmp):
    root = Path(tmp)
    (root / "catalogue/data").mkdir(parents=True)
    cat = copy.deepcopy(REAL_CAT)
    cat["products"] = [p for p in cat["products"] if p["sku"] == "BS-RINGER-001"]      # no flat product: no source images needed
    for p in cat["products"]:                                                         # the sandbox has no media/maximus_motion
        p.pop("studio", None)
    cat["site"].pop("lookbook_plates", None); cat["site"].pop("styled_looks", None)   # nor landing/assets/editorial (own tests)
    (root / "catalogue/data/catalogue.json").write_text(json.dumps(cat, indent=1), encoding="utf-8", newline="\n")
    (root / "landing/item").mkdir(parents=True); (root / "landing/apply").mkdir(); (root / "landing/media").mkdir()
    for f in ("index.html", "item/index.html", "apply/index.html"):
        (root / "landing" / f).write_text(f"<html>{f}</html>", encoding="utf-8")
    (root / "landing/media/x.webp").write_bytes(b"x")
    shutil.copytree(CANON / "config", root / "config")
    dj = root / "config/deployment.json"                                # the sandbox starts with NO owner decision, whatever the real
    cfg = json.loads(dj.read_text(encoding="utf-8"))                    # project records: each test that needs approval grants its own
    cfg["owner_decisions"].update(B4_live_deployment_approved=None, hosted_database_decision=None)
    dj.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    (root / "publications").mkdir()
    shutil.copytree(CANON / "publications/_baseline", root / "publications/_baseline")
    return pb.Project(root)


def edit_draft(p, fn):
    cat = json.loads(p.cat_json.read_text(encoding="utf-8"))
    fn(cat)
    p.cat_json.write_text(json.dumps(cat, indent=1), encoding="utf-8", newline="\n")


def make_orderable(cat):
    pr = cat["products"][0]
    pr["prices"] = {"NGN": 5000}
    pr["sizes"] = [{"label": "M", "stock": None}]
    for f in ("prices", "sizes"):
        pr["approval"][f] = {"status": "approved", "source": None, "by": "owner", "at": "2026-11-11T10:00:00"}
    pr["sale"] = {"status": "approved_for_sale", "by": "owner", "at": "2026-11-11T10:00:00"}


class PublishingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bs_pub_")
        self.p = make_project(self.tmp)
        self.s = FakeSteps(self.p)
        self.say = lambda *a: None

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cand(self, **k):
        return pb.create_candidate(self.p, mode=k.pop("mode", "full"), steps=self.s, say=self.say, **k)

    def full_publish(self, **k):
        m = self.cand(**k)
        pb.approve(self.p, m["publication_id"], f"APPROVE {m['publication_id']}", say=self.say)
        return pb.publish(self.p, m["publication_id"], "local", "PUBLISH", steps=self.s, say=self.say)

    # ---- candidate ----
    def test_candidate_manifest_has_every_required_field(self):
        m = self.cand()
        for k in ("publication_id", "created_at", "source_revision", "product_data_version", "build", "validation", "deployment_target", "previous_publication", "approval", "status"):
            self.assertIn(k, m)
        self.assertEqual(m["status"], "candidate")
        self.assertEqual(m["approval"]["status"], "pending")
        self.assertIsNone(m["previous_publication"])
        self.assertEqual(m["build"]["result"], "ok"); self.assertEqual(m["validation"]["result"], "ok")
        self.assertEqual(m["source_revision"]["git_commit"], "0123456789abcdef0123456789abcdef01234567")
        self.assertEqual(len(m["product_data_version"]["catalogue_sha256"]), 64)
        d = self.p.pubs / m["publication_id"]
        for f in ("manifest.json", "catalogue.snapshot.json", "public_products.json", "site.zip", "site_manifest.json", "proposed_changes.md", "proposed_changes.json", "validation.json", "build.log"):
            self.assertTrue((d / f).is_file(), f)

    def test_candidate_does_not_publish_or_set_current(self):
        self.cand()
        self.assertFalse(self.p.current.exists())

    def test_proposed_changes_record_customer_visible_differences(self):
        edit_draft(self.p, make_orderable)
        m = self.cand()
        ch = json.loads((self.p.pubs / m["publication_id"] / "proposed_changes.json").read_text(encoding="utf-8"))
        self.assertEqual(ch["summary"]["orderable_after"], ["BS-RINGER-001"])
        self.assertEqual(ch["summary"]["orderable_before"], [])
        self.assertTrue(any(c.get("field") == "price" and c["new"] == 5000 for c in ch["customer_visible_changes"]))

    def test_invalid_draft_is_refused_and_nothing_is_built(self):
        edit_draft(self.p, lambda c: c["site"]["checkout"].update(whatsapp="+234 bad"))
        with self.assertRaises(pb.PublishError):
            self.cand()
        self.assertEqual(self.s.calls, [])
        self.assertEqual([d for d in self.p.pubs.iterdir() if d.name.startswith("PUB-")], [])
        self.assertIn("candidate_refused", self.p.log.read_text(encoding="utf-8"))

    def test_unapproved_data_that_leaks_into_the_build_is_caught(self):
        self.s = FakeSteps(self.p, leak=True)
        with self.assertRaises(pb.PublishError) as cm:
            self.cand()
        self.assertIn("scan", str(cm.exception))
        self.assertEqual([d for d in self.p.pubs.iterdir() if d.name.startswith("PUB-")], [])

    def test_private_text_in_the_public_site_is_caught(self):
        orig = self.s.build
        def leaky(mode, provisional=False):
            out = orig(mode, provisional)
            (self.p.catalogue / "dist/index.html").write_text("see C:\\Users\\USER\\Desktop\\HUMAN_ENGINE", encoding="utf-8")
            return out
        self.s.build = leaky
        with self.assertRaises(pb.PublishError):
            self.cand()

    def test_dry_run_builds_nothing(self):
        r = self.cand(dry_run=True)
        self.assertTrue(r["dry_run"]); self.assertEqual(self.s.calls, [])
        self.assertFalse(self.p.site.exists())

    # ---- approval ----
    def test_approval_needs_the_exact_typed_phrase(self):
        m = self.cand(); pid = m["publication_id"]
        for bad in ("", "APPROVE", "approve " + pid, "APPROVE PUB-other"):
            with self.assertRaises(pb.PublishError):
                pb.approve(self.p, pid, bad, say=self.say)
        self.assertEqual(pb.load_manifest(self.p, pid)["approval"]["status"], "pending")
        a = pb.approve(self.p, pid, f"APPROVE {pid}", say=self.say)
        self.assertEqual(a["status"], "approved"); self.assertTrue(a["approval"]["by"] and a["approval"]["at"])

    def test_fast_preview_build_cannot_be_approved(self):
        m = self.cand(mode="fast")
        with self.assertRaises(pb.PublishError):
            pb.approve(self.p, m["publication_id"], f"APPROVE {m['publication_id']}", say=self.say)

    def test_editing_the_draft_makes_a_candidate_stale(self):
        m = self.cand(); pid = m["publication_id"]
        edit_draft(self.p, lambda c: c["site"].update(section_title="Changed"))
        with self.assertRaises(pb.PublishError) as cm:
            pb.approve(self.p, pid, f"APPROVE {pid}", say=self.say)
        self.assertIn("draft changed", str(cm.exception))

    # ---- publish ----
    def test_publish_requires_approval_and_the_typed_word(self):
        m = self.cand(); pid = m["publication_id"]
        with self.assertRaises(pb.PublishError):
            pb.publish(self.p, pid, "local", "PUBLISH", steps=self.s, say=self.say)             # not approved
        pb.approve(self.p, pid, f"APPROVE {pid}", say=self.say)
        for bad in ("", "publish", "YES"):
            with self.assertRaises(pb.PublishError):
                pb.publish(self.p, pid, "local", bad, steps=self.s, say=self.say)
        self.assertFalse(self.p.current.exists())

    def test_local_publish_sets_current_commits_and_logs(self):
        m = self.full_publish()
        cur = pb.load_current(self.p)
        self.assertEqual(cur["id"], m["publication_id"]); self.assertIsNone(cur["previous"])
        self.assertEqual(pb.load_manifest(self.p, m["publication_id"])["status"], "published")
        self.assertTrue(any(c[:2] == ("git", "commit") for c in self.s.calls))
        self.assertEqual(self.s.vercel_calls, [])
        acts = [json.loads(l)["action"] for l in self.p.log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(acts, ["candidate_created", "approved", "published"])

    def test_publish_restores_the_exact_approved_site_even_if_deploy_was_tampered(self):
        m = self.cand(); pid = m["publication_id"]
        pb.approve(self.p, pid, f"APPROVE {pid}", say=self.say)
        pj = self.p.site / "catalogue/data/products.json"
        d = json.loads(pj.read_text(encoding="utf-8")); d["products"][0]["price"] = 12345
        pj.write_text(json.dumps(d), encoding="utf-8")                                           # someone edits deploy/site by hand
        pb.publish(self.p, pid, "local", "PUBLISH", steps=self.s, say=self.say)
        self.assertIsNone(json.loads(pj.read_text(encoding="utf-8"))["products"][0]["price"])    # the approved build was restored

    def test_draft_edited_after_approval_blocks_publish(self):
        m = self.cand(); pid = m["publication_id"]
        pb.approve(self.p, pid, f"APPROVE {pid}", say=self.say)
        edit_draft(self.p, make_orderable)                                                       # draft edited after approval too
        with self.assertRaises(pb.PublishError):
            pb.publish(self.p, pid, "local", "PUBLISH", steps=self.s, say=self.say)

    def test_provisional_preview_marker_blocks_publish(self):
        m = self.cand(); pid = m["publication_id"]
        pb.approve(self.p, pid, f"APPROVE {pid}", say=self.say)
        (self.p.deploy / ".preview_marker").write_text("x", encoding="utf-8")
        with self.assertRaises(pb.PublishError) as cm:
            pb.publish(self.p, pid, "local", "PUBLISH", steps=self.s, say=self.say)
        self.assertIn("provisional preview", str(cm.exception))

    def test_preview_never_records_a_publication_and_provisional_is_marked(self):
        pb.preview(self.p, provisional=True, steps=self.s, say=self.say)
        self.assertTrue((self.p.deploy / ".preview_marker").exists())
        self.assertEqual([d for d in self.p.pubs.iterdir() if d.name.startswith("PUB-")], [])
        pb.preview(self.p, provisional=False, steps=self.s, say=self.say)
        self.assertFalse((self.p.deploy / ".preview_marker").exists())

    # ---- chain + rollback ----
    def two_publications(self):
        a = self.full_publish()
        first_cat = self.p.cat_json.read_bytes()
        first_site = {f.relative_to(self.p.site).as_posix(): pb.sha_file(f) for f in self.p.site.rglob("*") if f.is_file()}
        edit_draft(self.p, make_orderable)
        b = self.full_publish()
        return a, b, first_cat, first_site

    def test_previous_publication_is_referenced(self):
        a, b, *_ = self.two_publications()
        self.assertEqual(b["previous_publication"], a["publication_id"])
        self.assertEqual(pb.load_current(self.p)["previous"], a["publication_id"])
        self.assertEqual(pb.load_manifest(self.p, a["publication_id"])["status"], "superseded")

    def test_rollback_restores_data_and_site_exactly(self):
        a, b, first_cat, first_site = self.two_publications()
        self.assertNotEqual(self.p.cat_json.read_bytes(), first_cat)
        pb.rollback(self.p, None, "ROLLBACK", steps=self.s, say=self.say)
        self.assertEqual(self.p.cat_json.read_bytes(), first_cat)
        now = {f.relative_to(self.p.site).as_posix(): pb.sha_file(f) for f in self.p.site.rglob("*") if f.is_file()}
        self.assertEqual(now, first_site)
        cur = pb.load_current(self.p)
        self.assertEqual(cur["id"], a["publication_id"])
        self.assertEqual(pb.load_manifest(self.p, b["publication_id"])["status"], "rolled_back")
        self.assertEqual(len(list((self.p.pubs / "_draft_backups").iterdir())), 1)             # the draft it replaced was kept
        self.assertIn("rolled_back", self.p.log.read_text(encoding="utf-8"))
        self.assertEqual(self.s.vercel_calls, [])                                                # local only

    def test_rollback_needs_typed_confirmation_and_dry_run_changes_nothing(self):
        a, b, first_cat, _ = self.two_publications()
        before = self.p.cat_json.read_bytes()
        with self.assertRaises(pb.PublishError):
            pb.rollback(self.p, None, "yes", steps=self.s, say=self.say)
        pb.rollback(self.p, None, "", dry_run=True, steps=self.s, say=self.say)
        self.assertEqual(self.p.cat_json.read_bytes(), before)
        self.assertEqual(pb.load_current(self.p)["id"], b["publication_id"])

    def test_rollback_refuses_a_candidate_and_a_damaged_record(self):
        a, b, *_ = self.two_publications()
        c = self.cand()                                                                          # a candidate, never published
        with self.assertRaises(pb.PublishError):
            pb.rollback(self.p, c["publication_id"], "ROLLBACK", steps=self.s, say=self.say)
        z = self.p.pubs / a["publication_id"] / "site.zip"
        z.write_bytes(z.read_bytes() + b"damage")
        with self.assertRaises(pb.PublishError) as cm:
            pb.rollback(self.p, a["publication_id"], "ROLLBACK", steps=self.s, say=self.say)
        self.assertIn("do not match", str(cm.exception))

    def test_rollback_with_nothing_published(self):
        with self.assertRaises(pb.PublishError):
            pb.rollback(self.p, None, "ROLLBACK", steps=self.s, say=self.say)

    # ---- live (Vercel) gate: no command may run until the owner has recorded approval ----
    def test_vercel_target_is_refused_without_owner_approval_and_runs_no_command(self):
        m = self.cand(); pid = m["publication_id"]
        pb.approve(self.p, pid, f"APPROVE {pid}", say=self.say)
        with self.assertRaises(pb.PublishError) as cm:
            pb.publish(self.p, pid, "vercel", "PUBLISH", steps=self.s, say=self.say)
        self.assertIn("B4", str(cm.exception)); self.assertIn("DATABASE_URL", str(cm.exception))
        self.assertEqual(self.s.vercel_calls, [])
        self.assertEqual(pb.load_manifest(self.p, pid)["status"], "approved")
        self.assertFalse(self.p.current.exists())

    def test_vercel_dry_run_lists_commands_and_blockers_but_runs_nothing(self):
        m = self.cand(); pid = m["publication_id"]
        pb.approve(self.p, pid, f"APPROVE {pid}", say=self.say)
        lines = []
        r = pb.publish(self.p, pid, "vercel", "", steps=self.s, dry_run=True, say=lines.append)
        self.assertEqual(len(r["blockers"]), 2); self.assertEqual(self.s.vercel_calls, [])
        txt = "\n".join(lines)
        self.assertIn("--skip-domain", txt); self.assertIn("vercel promote", txt); self.assertIn("WOULD BE REFUSED", txt)

    def approve_owner(self, db="expendable"):
        cfgp = self.p.config / "deployment.json"
        cfg = json.loads(cfgp.read_text(encoding="utf-8"))
        cfg["owner_decisions"]["B4_live_deployment_approved"] = {"by": "TEST owner", "at": "2026-11-11T12:00:00Z"}
        cfg["owner_decisions"]["hosted_database_decision"] = db
        cfgp.write_text(json.dumps(cfg), encoding="utf-8")

    def test_vercel_sequence_when_gates_are_satisfied(self):
        self.approve_owner()
        orig = pb.smoke; pb.smoke = lambda base, n, t, opener=None: (True, [{"path": "/", "status": 200, "ok": True}])
        try:
            m = self.cand(); pid = m["publication_id"]
            pb.approve(self.p, pid, f"APPROVE {pid}", say=self.say)
            r = pb.publish(self.p, pid, "vercel", "PUBLISH", steps=self.s, say=self.say)
        finally:
            pb.smoke = orig
        self.assertEqual([c[0] for c in self.s.vercel_calls], ["deploy", "promote"])
        dep = self.s.vercel_calls[0]
        self.assertIn("--skip-domain", dep); self.assertIn("--prod", dep); self.assertIn("better-style-shop", dep); self.assertIn("411-004b", dep)
        self.assertEqual(r["deployment"]["previous_deployment_url"], "https://better-style-shop-jbnyptkv3-411-004b.vercel.app")
        self.assertEqual(r["deployment_target"]["type"], "vercel")

    def test_failed_smoke_after_promotion_triggers_rollback_to_the_previous_deployment(self):
        self.approve_owner()
        calls = {"n": 0}
        def smoke(base, n, t, opener=None):
            calls["n"] += 1
            return (calls["n"] == 1), [{"path": "/", "status": 200 if calls["n"] == 1 else 500, "ok": calls["n"] == 1}]
        orig = pb.smoke; pb.smoke = smoke
        try:
            m = self.cand(); pid = m["publication_id"]
            pb.approve(self.p, pid, f"APPROVE {pid}", say=self.say)
            with self.assertRaises(pb.PublishError) as cm:
                pb.publish(self.p, pid, "vercel", "PUBLISH", steps=self.s, say=self.say)
        finally:
            pb.smoke = orig
        self.assertEqual([c[0] for c in self.s.vercel_calls], ["deploy", "promote", "rollback"])
        self.assertIn("https://better-style-shop-jbnyptkv3-411-004b.vercel.app", self.s.vercel_calls[2])
        self.assertIn("rollback done", str(cm.exception))
        self.assertFalse(self.p.current.exists())                                                # not recorded as published

    def test_hosted_database_decision_must_be_a_valid_choice(self):
        self.approve_owner(db="maybe")
        m = self.cand(); pid = m["publication_id"]
        pb.approve(self.p, pid, f"APPROVE {pid}", say=self.say)
        with self.assertRaises(pb.PublishError) as cm:
            pb.publish(self.p, pid, "vercel", "PUBLISH", steps=self.s, say=self.say)
        self.assertIn("DATABASE_URL", str(cm.exception)); self.assertEqual(self.s.vercel_calls, [])


class RealConfigTests(unittest.TestCase):
    def test_the_real_project_records_the_owner_decisions(self):
        """Recorded by the owner on 2026-11-12: B4 approved, hosted database to be discarded. Each is signed and dated."""
        od = json.loads((CANON / "config/deployment.json").read_text(encoding="utf-8"))["owner_decisions"]
        for k in ("B4_live_deployment_approved", "hosted_database_decision"):
            self.assertTrue(od[k]["by"] and od[k]["at"], k)
        self.assertEqual(od["hosted_database_decision"]["action"], "discard")
        _, blockers = pb.deployment_gates(pb.Project())
        self.assertEqual(blockers, [])
        for bad in (None, "maybe", {"action": "discard"}, {"action": "keep", "by": "o", "at": "t"}):   # unsigned / unknown = no decision
            self.assertFalse(pb.hosted_database_decided(bad), bad)


if __name__ == "__main__":
    unittest.main()
