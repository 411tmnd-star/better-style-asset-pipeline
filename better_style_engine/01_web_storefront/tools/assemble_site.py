"""Assemble the deployable static site into deploy/site/ from landing/ and catalogue/dist/.

    python -I tools/assemble_site.py [--allow-provisional] [--out DIR]

deploy/site/            /               landing (index.html, item/, apply/, media/; and when present film/, assets/, vendor/)
                        /catalogue/     catalogue/dist/*
                        vercel.json     config/site_vercel.json  (/shop -> /catalogue/, /admin -> /, cache headers)

Safety:
  * Refuses a catalogue built in provisional-preview mode unless --allow-provisional (then writes deploy/.preview_marker,
    which publish.py treats as "never deployable").
  * Refuses to emit private/internal material (catalogue.json, sources, hero, engine_inputs, build scripts, studio, any .py).
  * The previous assembly is kept as deploy/site.last_good; an existing deploy/site/.vercel link is preserved.
  * Writes deploy/site_manifest.json (sha256 of every file) used by the publishing manifest.
"""
import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

CANON = Path(__file__).resolve().parent.parent
FORBIDDEN_NAMES = {".env", ".env.local", "catalogue.json", "sources", "engine_inputs", "build.py", "make_dist.py", "studio", "tools", "tests", "publications"}
FORBIDDEN_PATHS = {"catalogue/hero"}   # source renders; the public hero image lives in catalogue/media/hero (allowed)
OPTIONAL_LANDING = ("film", "assets", "vendor", "fitting")
LANDING_PRIVATE = ("editorial.json",)  # provenance of the editorial images (source file names): kept in the repo, not published


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def assemble(canon=CANON, out=None, allow_provisional=False):
    canon = Path(canon)
    out = Path(out) if out else canon / "deploy/site"
    deploy = out.parent
    dist = canon / "catalogue/dist"
    land = canon / "landing"
    pj = dist / "data/products.json"
    if not pj.is_file():
        raise SystemExit(f"assemble: {pj} not found. Run catalogue/build.py and catalogue/make_dist.py first.")
    provisional = bool(json.loads(pj.read_text(encoding="utf-8")).get("preview_provisional"))
    if provisional and not allow_provisional:
        raise SystemExit("assemble: catalogue/dist was built in provisional-preview mode (draft values). Rebuild without BS_PREVIEW_PROVISIONAL, or pass --allow-provisional for a local preview that can never be deployed.")

    keep_vercel = None
    if out.exists():
        if (out / ".vercel").is_dir():
            keep_vercel = canon / "deploy/.vercel_keep"
            if keep_vercel.exists():
                shutil.rmtree(keep_vercel)
            shutil.copytree(out / ".vercel", keep_vercel)
        last = deploy / "site.last_good"
        if last.exists():
            shutil.rmtree(last)
        shutil.move(str(out), str(last))
    out.mkdir(parents=True)

    for item in ("index.html", "item", "apply", "media"):
        s = land / item
        if not s.exists():
            raise SystemExit(f"assemble: landing/{item} missing")
        (shutil.copytree if s.is_dir() else shutil.copy2)(s, out / item)
    for item in OPTIONAL_LANDING:          # the lookbook's images, vendored libraries and the preserved film, when present
        s = land / item
        if s.is_dir():
            shutil.copytree(s, out / item, ignore=shutil.ignore_patterns(*LANDING_PRIVATE))
    shutil.copytree(dist, out / "catalogue")
    shutil.copy2(canon / "config/site_vercel.json", out / "vercel.json")
    if keep_vercel:
        shutil.copytree(keep_vercel, out / ".vercel")
        shutil.rmtree(keep_vercel)

    bad = [p for p in out.rglob("*") if p.name in FORBIDDEN_NAMES or p.suffix == ".py" or p.relative_to(out).as_posix() in FORBIDDEN_PATHS]
    if bad:
        shutil.rmtree(out)
        last = deploy / "site.last_good"
        if last.exists():
            shutil.move(str(last), str(out))
        raise SystemExit("assemble: refused, internal files would be published: " + ", ".join(str(b.relative_to(out)) for b in bad))

    marker = deploy / ".preview_marker"
    if provisional:
        marker.write_text("provisional preview build: never deployable\n", encoding="utf-8")
    elif marker.exists():
        marker.unlink()

    files = sorted(p for p in out.rglob("*") if p.is_file() and ".vercel" not in p.relative_to(out).parts)
    manifest = {"files": {p.relative_to(out).as_posix(): sha256(p) for p in files}, "provisional": provisional}
    (deploy / "site_manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    size = sum(p.stat().st_size for p in files)
    print(f"assembled {len(files)} files, {size / 1e6:.1f} MB into {out}" + ("  [PROVISIONAL PREVIEW]" if provisional else ""))
    return manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-provisional", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()
    assemble(out=a.out, allow_provisional=a.allow_provisional)
