"""Copy the HUMAN_ENGINE files that catalogue/build.py reads into catalogue/engine_inputs/, with a manifest.

HUMAN_ENGINE is only READ here. Nothing is ever written under it. The catalogue build itself never touches it:
build.py reads catalogue/engine_inputs/ (or the folder named by BS_ENGINE_ROOT, used by tests).

    python -I tools/collect_engine_inputs.py            # collect (re-collect after a new live SKU or new renders)
    python -I tools/collect_engine_inputs.py --list     # show what would be copied, write nothing
    python -I tools/collect_engine_inputs.py --verify   # re-hash engine_inputs against MANIFEST.json (no HUMAN_ENGINE needed)

Which files: derived from catalogue/data/catalogue.json exactly as build.py reads them.
  every live product WITHOUT "source_kind":  configs/skus/<SKU>.json, its archetype_spec file, and, unless the entry has "render_dir",
                                             assets/renders/<SKU>/render_log.json plus every file that log lists (+ "_alpha.png" siblings)
  entries with "mannequin_set":              configs/mannequin_finishes.json and assets/renders/<SKU>/<set>/M*/ (render_log.json + listed files)
  always:                                    garment_registry/garment_families/garment_families.json
"""
import os
import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

CANON = Path(__file__).resolve().parent.parent
CAT = CANON / "catalogue"
DEST = CAT / "engine_inputs"
DEFAULT_ENGINE = Path(os.path.normpath(os.path.expanduser('~/Desktop/4041_MechMix/02_engines/human_engine/src_catalog/better_style_catalog')))


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def discover(engine: Path):
    """Return a sorted list of absolute engine files the live catalogue needs."""
    cat = load(CAT / "data/catalogue.json")
    need = {engine / "garment_registry/garment_families/garment_families.json"}
    for p in cat["products"]:
        if p.get("status") != "live" or p.get("source_kind"):
            continue
        sku = p["sku"]
        cfgp = engine / "configs/skus" / f"{sku}.json"
        need.add(cfgp)
        cfg = load(cfgp)
        need.add(engine / cfg["archetype_spec"])
        if not p.get("render_dir"):
            rd = engine / "assets/renders" / sku
            lp = rd / "render_log.json"
            need.add(lp)
            for r in load(lp)["renders"]:
                f = rd / r["file"]
                need.add(f)
                a = f.with_name(f.stem + "_alpha.png")
                if a.exists():
                    need.add(a)
        if p.get("mannequin_set"):
            need.add(engine / "configs/mannequin_finishes.json")
            base = engine / "assets/renders" / sku / p["mannequin_set"]
            for fdir in sorted(base.glob("M*")):
                lp = fdir / "render_log.json"
                if lp.exists():
                    need.add(lp)
                    for r in load(lp)["renders"]:
                        need.add(fdir / r["file"])
    missing = [str(x) for x in need if not x.exists()]
    if missing:
        sys.exit("MISSING engine input(s):\n  " + "\n  ".join(missing))
    return sorted(need)


def verify():
    man = load(DEST / "MANIFEST.json")
    bad = []
    for f in man["files"]:
        p = DEST / f["path"]
        if not p.exists() or sha256(p) != f["sha256"]:
            bad.append(f["path"])
    print(f"verified {len(man['files'])} engine input file(s); {len(bad)} mismatch(es)")
    for b in bad:
        print("  MISMATCH", b)
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default=str(DEFAULT_ENGINE), help="path to the better_style_catalog folder (read only)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    if a.verify:
        sys.exit(verify())
    engine = Path(a.engine).resolve()
    files = discover(engine)
    total = sum(f.stat().st_size for f in files)
    if a.list:
        for f in files:
            print(f.relative_to(engine).as_posix())
        print(f"{len(files)} files, {total / 1e6:.1f} MB")
        return
    if DEST.exists():
        shutil.rmtree(DEST)
    entries = []
    for f in files:
        rel = f.relative_to(engine)
        dst = DEST / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
        entries.append({"path": rel.as_posix(), "sha256": sha256(dst), "bytes": dst.stat().st_size})
    manifest = {
        "about": "Read-only snapshot of the HUMAN_ENGINE files catalogue/build.py reads. Do not edit; regenerate with tools/collect_engine_inputs.py.",
        "source_root": str(engine),
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "licence_note": "Owner's own project files (Better Style). No third-party licence files accompany them in the source folders.",
        "files": entries,
    }
    (DEST / "MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"collected {len(entries)} files, {total / 1e6:.1f} MB into {DEST}")


if __name__ == "__main__":
    main()
