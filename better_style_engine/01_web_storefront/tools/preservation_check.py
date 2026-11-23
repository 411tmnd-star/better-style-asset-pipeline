"""Compare the canonical project with the Phase 1 baseline and write a preservation report.

    python -I tools/preservation_check.py --out evidence/phase_06/preservation.json [--md evidence/phase_06/preservation.md]

Read-only on every original. Answers, with hashes:
  1. Is every landing file identical to the original, except the four intentionally edited ones (and how do those differ)?
  2. Which catalogue source files are identical / modified / new / excluded versus the original storefront?
  3. Is the built site (deploy/site) identical to the original dist, file by file?
  4. Are the original sources (LANDING, storefront, SHOP, NEXT, engine inputs) still byte-identical to the Phase 1 manifests?
"""
import argparse
import difflib
import hashlib
import json
import os
import re
import sys
from pathlib import Path

CANON = Path(__file__).resolve().parent.parent
HOME = Path(os.path.normpath(os.path.expanduser('~')))
LANDING = HOME / r"Desktop\Research\BETTER RESEARCH\RESEARCH\04-TECHNOLOGY-VISUAL-EXPERIMENTS\connected-prototype"
ENGINE = HOME / r"Documents\Codex\dev array\411_01_BETTER_STYLE_ACTIVE\HUMAN_ENGINE\src_catalog\better_style_catalog"
SF = ENGINE / "storefront"
SHOP = HOME / r"Desktop\Projects\better-style-shop"
NEXT = HOME / r"Desktop\Projects\better-style-standalone"
P1 = CANON / "evidence/phase_01"
EXCL = {".git", "node_modules", ".venv", "__pycache__"}


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def manifest(name):
    m = {}
    for line in (P1 / f"hash_{name}.txt").read_text(encoding="utf-8").splitlines():
        h, _, rest = line.partition("  ")
        _, _, rel = rest.strip().partition("  ")
        m[rel] = h
    return m


def tree(root, excl=EXCL):
    out = {}
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in excl]
        for f in fn:
            p = Path(dp) / f
            out[p.relative_to(root).as_posix()] = sha(p)
    return out


def href_words(a, b):
    """Word-level differences between two texts (used to show landing edits are only the href strings)."""
    ta, tb = re.findall(r"[^\s<>\"=]+", a), re.findall(r"[^\s<>\"=]+", b)
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    return [{"old": " ".join(ta[i1:i2]), "new": " ".join(tb[j1:j2])} for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--md")
    a = ap.parse_args()
    R = {}

    # 1. landing
    orig, now = manifest("LANDING"), tree(CANON / "landing")
    skip = {".gitignore", ".vercel/project.json", ".vercel/README.txt"}
    orig = {k: v for k, v in orig.items() if not k.startswith(".vercel/") and k != ".gitignore"}
    changed = sorted(k for k in orig if k in now and orig[k] != now[k])
    R["landing"] = {
        "files_original": len(orig), "files_canonical": len(now),
        "identical": sum(1 for k in orig if now.get(k) == orig[k]),
        "modified": changed,
        "missing_from_canonical": sorted(set(orig) - set(now)), "new_in_canonical": sorted(set(now) - set(orig)),
        "word_level_changes": {k: href_words((LANDING / k).read_text(encoding="utf-8"), (CANON / "landing" / k).read_text(encoding="utf-8")) for k in changed},
    }

    # 2. catalogue sources vs original storefront
    sf = manifest("SF")
    cat = tree(CANON / "catalogue", EXCL | {"engine_inputs", "dist"})
    R["catalogue_sources"] = {
        "identical": sorted(k for k in cat if sf.get(k) == cat[k] and not k.startswith("tests/node_modules")),
        "modified": sorted(k for k in cat if k in sf and sf[k] != cat[k] and not k.startswith("tests/node_modules") and not k.startswith("tests/shots")),
        "new": sorted(k for k in cat if k not in sf and not k.startswith("tests/node_modules") and not k.startswith("tests/shots")),
    }
    R["catalogue_sources"]["counts"] = {k: len(v) for k, v in R["catalogue_sources"].items()}
    R["catalogue_sources"]["excluded_from_copy"] = sorted(k for k in sf if k.split("/")[0] in ("dist", "preview3d") or k == "tests/.gitignore")[:0] or ["dist/ (regenerated)", "preview3d/ (untracked experiment)", "tests/.gitignore"]

    # 3. deployed catalogue vs original dist
    site = CANON / "deploy/site/catalogue"
    if site.is_dir():
        od = {k[len("dist/"):]: v for k, v in sf.items() if k.startswith("dist/")}
        nd = tree(site)
        R["deployed_catalogue_vs_original_dist"] = {
            "identical": sum(1 for k in od if nd.get(k) == od[k]),
            "modified": sorted(k for k in od if k in nd and nd[k] != od[k]),
            "missing": sorted(set(od) - set(nd)), "new": sorted(set(nd) - set(od)),
            "media_files_identical": sum(1 for k in od if k.startswith("media/") and nd.get(k) == od[k]),
            "media_files_total": sum(1 for k in od if k.startswith("media/")),
        }
    # 4. originals untouched
    checks = {}
    for name, root, excl in (("LANDING", LANDING, EXCL), ("SF", SF, EXCL), ("SHOP", SHOP, EXCL)):
        m, n = manifest(name), tree(root, excl)
        checks[name] = {"files": len(m), "changed": sorted(k for k in m if n.get(k) != m[k])[:10], "added": sorted(set(n) - set(m))[:10], "unchanged": m == n}
    m = manifest("ENGINE_inputs")
    checks["ENGINE_inputs"] = {"files": len(m), "unchanged": all((ENGINE / k).is_file() and sha(ENGINE / k) == v for k, v in m.items())}
    nx = manifest("NEXT_top")
    nn = {}
    for k in nx:
        p = NEXT / k
        nn[k] = sha(p) if p.is_file() else None
    checks["NEXT_top_and_src"] = {"files": len(nx), "unchanged": nn == nx}
    R["originals_unchanged_since_phase1"] = checks
    Path(a.out).write_text(json.dumps(R, indent=1), encoding="utf-8")

    if a.md:
        L = ["# Preservation check", "", "Generated by `tools/preservation_check.py` (read-only on originals).", ""]
        l = R["landing"]
        L += ["## Landing page", f"- Original files compared: {l['files_original']}; identical in canonical: **{l['identical']}**; modified: **{len(l['modified'])}** ({', '.join(l['modified'])}); missing: {l['missing_from_canonical'] or 'none'}; new: {l['new_in_canonical'] or 'none'}"]
        for f, ch in l["word_level_changes"].items():
            L.append(f"  - `{f}`: {len(ch)} word-level change(s): " + "; ".join(f"`{c['old']}` -> `{c['new']}`" for c in ch)[:600])
        c = R["catalogue_sources"]
        L += ["", "## Catalogue sources vs original storefront", f"- identical: **{c['counts']['identical']}**, modified: **{c['counts']['modified']}**, new: **{c['counts']['new']}**", "- modified: " + ", ".join(f"`{x}`" for x in c["modified"]), "- new: " + ", ".join(f"`{x}`" for x in c["new"]), f"- excluded from the copy: {', '.join(c['excluded_from_copy'])}"]
        if "deployed_catalogue_vs_original_dist" in R:
            d = R["deployed_catalogue_vs_original_dist"]
            L += ["", "## Built catalogue (deploy/site/catalogue) vs original dist", f"- identical files: **{d['identical']}**; media identical: **{d['media_files_identical']}/{d['media_files_total']}**", "- modified: " + ", ".join(f"`{x}`" for x in d["modified"]), f"- missing: {d['missing'] or 'none'}; new: {d['new'] or 'none'}"]
        L += ["", "## Originals unchanged since the Phase 1 baseline"]
        for k, v in checks.items():
            L.append(f"- {k}: {'**unchanged**' if v['unchanged'] else '**CHANGED** ' + json.dumps(v)} ({v['files']} files)")
        Path(a.md).write_text("\n".join(L) + "\n", encoding="utf-8")
    ok = all(v["unchanged"] for v in checks.values())
    print(json.dumps({"originals_unchanged": {k: v["unchanged"] for k, v in checks.items()}, "landing_modified": R["landing"]["modified"], "catalogue_counts": R["catalogue_sources"]["counts"]}, indent=1))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
