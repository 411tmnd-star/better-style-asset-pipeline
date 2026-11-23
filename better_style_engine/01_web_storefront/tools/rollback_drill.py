"""Safe rollback drill on a SCRATCH COPY of the project (never on the real project, never on a live site).

    python -I tools/rollback_drill.py --out evidence/phase_10/rollback_drill.txt

Uses the REAL candidate already built in publications/ as release A, then builds a real release B from a changed draft in the
scratch copy, publishes both locally, rolls back to A, and verifies byte-for-byte that data and site match A again.
"""
import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

CANON = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CANON / "tools"))
import publishing as pb  # noqa: E402

lines = []


def say(*a):
    s = " ".join(str(x) for x in a)
    lines.append(s)
    print(s, flush=True)


def tree(root):
    return {f.relative_to(root).as_posix(): pb.sha_file(f) for f in sorted(Path(root).rglob("*")) if f.is_file() and ".vercel" not in f.parts}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    real_pubs = sorted(d.name for d in (CANON / "publications").iterdir() if d.name.startswith("PUB-"))
    real_before = {n: json.loads((CANON / "publications" / n / "manifest.json").read_text(encoding="utf-8"))["status"] for n in real_pubs}
    tmp = Path(tempfile.mkdtemp(prefix="bs_drill_"))
    root = tmp / "proj"
    ok = True

    def check(name, cond, extra=""):
        nonlocal ok
        ok &= bool(cond)
        say(f"{'PASS' if cond else 'FAIL'}  {name}{('  - ' + str(extra)) if extra else ''}")
    try:
        say(f"scratch copy: {root}")
        shutil.copytree(CANON, root, ignore=shutil.ignore_patterns(".git", "node_modules", "dist", "deploy", "shots", "evidence", "__pycache__", "recovery"))
        p = pb.Project(root)
        a_id = real_pubs[0]
        say(f"release A = the real candidate {a_id} (copied into the scratch project)")
        pb.approve(p, a_id, f"APPROVE {a_id}", say=say)
        pb.publish(p, a_id, "local", "PUBLISH", say=say)
        a_cat = p.cat_json.read_bytes()
        a_site = tree(p.site)
        check("A published; CURRENT = A", pb.load_current(p)["id"] == a_id)

        cat = json.loads(p.cat_json.read_text(encoding="utf-8"))
        cat["site"]["section_title"] = "DRILL CHANGE"
        p.cat_json.write_text(json.dumps(cat, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        say("changed the scratch draft (section_title) and building release B with the REAL full build (about 10 minutes) ...")
        t0 = time.time()
        b = pb.create_candidate(p, mode="full", say=say)
        b_id = b["publication_id"]
        pb.approve(p, b_id, f"APPROVE {b_id}", say=say)
        pb.publish(p, b_id, "local", "PUBLISH", say=say)
        say(f"B built and published in {time.time() - t0:.0f}s")
        pj = json.loads((p.site / "catalogue/data/products.json").read_text(encoding="utf-8"))
        check("B is live in deploy/site (changed title)", pj["site"]["section_title"] == "DRILL CHANGE")
        check("B's manifest references A as previous", pb.load_manifest(p, b_id)["previous_publication"] == a_id)
        check("data and site now differ from A", p.cat_json.read_bytes() != a_cat and tree(p.site) != a_site)

        say("--- rollback to A (typed ROLLBACK) ---")
        try:
            pb.rollback(p, a_id, "rollback", say=say)
            check("wrong confirmation refused", False)
        except pb.PublishError as e:
            check("wrong confirmation refused", True, e)
        pb.rollback(p, a_id, "ROLLBACK", say=say)
        check("catalogue.json byte-identical to A's snapshot", p.cat_json.read_bytes() == a_cat)
        check("deploy/site file-by-file identical to A's published site", tree(p.site) == a_site, f"{len(a_site)} files")
        check("CURRENT = A", pb.load_current(p)["id"] == a_id)
        check("B marked rolled_back, A published", pb.load_manifest(p, b_id)["status"] == "rolled_back" and pb.load_manifest(p, a_id)["status"] == "published")
        check("the draft that was replaced was backed up", len(list((p.pubs / "_draft_backups").iterdir())) == 1)
        acts = [json.loads(l)["action"] for l in p.log.read_text(encoding="utf-8").splitlines()]
        check("audit log records the sequence", acts[-1] == "rolled_back" and "published" in acts, acts)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    real_after = {n: json.loads((CANON / "publications" / n / "manifest.json").read_text(encoding="utf-8"))["status"] for n in real_pubs}
    check("the REAL project's publications were not touched (still unapproved candidate)", real_before == real_after, real_after)
    say("DRILL " + ("PASSED" if ok else "FAILED"))
    Path(CANON / a.out).parent.mkdir(parents=True, exist_ok=True)
    (CANON / a.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
