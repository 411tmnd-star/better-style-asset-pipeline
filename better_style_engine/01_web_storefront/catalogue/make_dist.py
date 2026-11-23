"""Copy the deployable site into storefront/dist/: the page, styles, script, product data and web images only.

Internal material stays out of the public site: catalogue.json (status notes), sources/ and hero/ (full-size
renders), build tooling, and in products.json the held-back list, source file paths/checksums and render records.

    python storefront/build.py && python storefront/make_dist.py
    npx vercel deploy storefront/dist --prod
"""
import json
import shutil
from pathlib import Path

STORE = Path(__file__).resolve().parent
DIST = STORE / "dist"


def main():
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    for rel in ("index.html", "css", "js", "media"):
        src = STORE / rel
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, DIST / rel)
    data = json.loads((STORE / "data/products.json").read_text(encoding="utf-8"))
    data.pop("held_back", None)
    for k in ("editorial_record", "plate_record", "plate_source"):
        (data["site"].get("hero") or {}).pop(k, None)
    for p in data["products"]:
        for cw in p["colourways"]:
            for r in cw["renders"]:
                src = r.pop("source", None)
                if isinstance(src, dict) and src.get("kind") == "owner_reference_flat_front":
                    r["source_kind"] = src["kind"]
    (DIST / "data").mkdir()
    (DIST / "data/products.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    files = [f for f in DIST.rglob("*") if f.is_file()]
    print(f"dist: {len(files)} files, {sum(f.stat().st_size for f in files) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
