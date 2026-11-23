"""Build the landing lookbook's editorial images: landing/assets/editorial/<slug>-<width>.webp + editorial.json.

    python -I tools/build_editorial_assets.py [--check]

Sources are the photographs chosen in the editorial photo audit (_audit/picks.json, ids from _audit/scan/photo_scan.json).
For each slug below the source is re-encoded to WebP at the widths in WIDTHS that do not exceed the source width (never
upscaled; the source's own width is always included). Embedded colour profiles are converted to sRGB, and nothing else
is carried over: no EXIF, GPS or camera metadata reaches the public site. Downscaled variants get a light unsharp mask.

editorial.json records, per slug: the variants (file, width, height, bytes), the focal point used for CSS cropping, the
catalogue SKU/colourway the plate links to, and the source file's sha256 (provenance). --check only verifies that every
file listed in editorial.json exists with the recorded size, without touching the sources.
Writes are .tmp + os.replace.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
from pathlib import Path

from PIL import Image, ImageCms, ImageFilter, ImageOps

CANON = Path(__file__).resolve().parent.parent
OUT = CANON / "landing/assets/editorial"
AUDIT = CANON / "_audit"
WIDTHS = (480, 720, 1080)
QUALITY = 82

# slug -> scan id (see _audit/picks.json for the reasons and the SKU mapping)
SLUGS = {
    "marina-69-back": 174,
    "crescent-bracket-tank": 172,
    "street-rugby-69": 169,
    "skyline-silhouette": 167,
    "crescent-lean": 193,
    "marina-rugby-69": 171,
    "cart-rugby-69": 2,
    "cart-script-polo": 176,
    "script-polo-front": 206,
    "cart-69-back": 207,
    "cart-seated-polo": 3,
    "studio-duo": 1403,
    "studio-scarlet-shirt": 1407,
    "studio-ivory-tank": 1401,
    "studio-script-polo-detail": 1405,
    "scarlet-pocket-detail": 1363,
}


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def to_srgb(im: Image.Image) -> Image.Image:
    icc = im.info.get("icc_profile")
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            dst = ImageCms.createProfile("sRGB")
            return ImageCms.profileToProfile(im, src, dst, outputMode="RGB")
        except (ImageCms.PyCMSError, OSError):
            pass
    return im.convert("RGB")


def encode(im: Image.Image, width: int, native: int) -> tuple[bytes, int, int]:
    if width < native:
        h = round(im.height * width / im.width)
        im = im.resize((width, h), Image.Resampling.LANCZOS).filter(ImageFilter.UnsharpMask(radius=0.6, percent=40, threshold=2))
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=QUALITY, method=6)          # no exif=, no icc_profile=: metadata is dropped
    return buf.getvalue(), im.width, im.height


def build() -> int:
    picks = json.loads((AUDIT / "picks.json").read_text(encoding="utf-8"))
    scan = {r["id"]: r for r in json.loads((AUDIT / "scan/photo_scan.json").read_text(encoding="utf-8"))["items"]}
    meta = {p["id"]: p for key in ("B_clearest_editorial_selection", "C_detail_and_texture") for p in picks.get(key, [])}
    manifest, total = {"schema": "betterstyle.editorial_assets/1", "quality": QUALITY, "assets": {}}, 0
    for slug, sid in SLUGS.items():
        src = Path(scan[sid]["path"])
        raw = src.read_bytes()
        with Image.open(io.BytesIO(raw)) as im0:
            im = to_srgb(ImageOps.exif_transpose(im0))
        widths = sorted({w for w in WIDTHS if w < im.width} | {im.width})
        variants = []
        for w in widths:
            data, vw, vh = encode(im, w, im.width)
            dest = OUT / f"{slug}-{vw}.webp"
            atomic_write(dest, data)
            variants.append({"file": dest.relative_to(CANON / "landing").as_posix(), "width": vw, "height": vh, "bytes": len(data)})
            total += len(data)
        p = meta.get(sid, {})
        manifest["assets"][slug] = {"scan_id": sid, "source_name": src.name, "source_sha256": sha256_bytes(raw),
                                    "source_size": [im.width, im.height], "variants": variants,
                                    "focal": p.get("focal", [50, 50]), "sku": p.get("sku"), "colourway": p.get("colourway"),
                                    "set": p.get("set", "detail"), "role": p.get("role")}
        print(f"  {slug:28s} #{sid:<5d} {im.width}x{im.height} -> {', '.join(str(v['width']) for v in variants)}", file=sys.stderr)
    atomic_write(OUT / "editorial.json", json.dumps(manifest, indent=1, ensure_ascii=False).encode("utf-8"))
    print(json.dumps({"ok": True, "assets": len(manifest["assets"]), "files": sum(len(a["variants"]) for a in manifest["assets"].values()),
                      "mb": round(total / 1e6, 2), "manifest": str(OUT / "editorial.json")}))
    return 0


def check() -> int:
    m = json.loads((OUT / "editorial.json").read_text(encoding="utf-8"))
    bad = []
    for slug, a in m["assets"].items():
        for v in a["variants"]:
            f = CANON / "landing" / v["file"]
            if not f.is_file() or f.stat().st_size != v["bytes"]:
                bad.append(v["file"])
                continue
            with Image.open(f) as im:
                if im.size != (v["width"], v["height"]):
                    bad.append(f"{v['file']} size {im.size}")
    print(json.dumps({"ok": not bad, "bad": bad}))
    return 0 if not bad else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true")
    raise SystemExit(check() if ap.parse_args().check else build())
