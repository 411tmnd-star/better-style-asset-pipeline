"""Grade the brand-owned horse campaign plate into the storefront hero background (BS-RINGER-001 floats in front of it).

    python -I catalogue/hero/grade_horse_hero.py [--check]

Input   hero/sources/horse_plate_raw.jpg     logo-free, brand-owned plate: mineral slate and burnished ochre, the rider on
                                             the right third, open mountain on the left where the tee floats
Output  hero/renders/ringer_horse_hero.png   lossless master (exact 3:2), read by catalogue/build.py (site.hero.plate_source)
        hero/renders/ringer_horse_hero.webp  web-optimised copy of the master (hand-off / preview; build.py makes the served sizes)
        hero/renders/ringer_horse_hero.json  record: source and output sha256, every grade parameter

A light finishing pass only. Nothing is relit, recoloured or sharpened, and the rider is never blurred:
  1. exact 3:2 centre crop (the source is 2528x1696, 1.4906:1; this trims 1 px a side and 6 px top and bottom)
  2. depth soften: Gaussian sigma 1.0 behind the left/centre zone where the tee floats, feathered to zero before the
     horse's muzzle (at 46% of the width), so the horse, the rider, the crown and the banner keep their full detail
  3. bottom-up scrim: rgba(17,17,15,0) at 50% height -> rgba(17,17,15,0.52) at the bottom edge (smoothstep), composited
     in sRGB exactly as the CSS gradient would be, so the ivory hero copy holds >= 7:1 on the lower plate
  4. luma-only film grain, sigma 2.5 (8-bit units), seeded: the same offset is added to R, G and B, which moves Y and
     leaves Cb/Cr untouched, so the grain carries no colour noise
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parent
SRC = HERE / "sources" / "horse_plate_raw.jpg"
OUT = HERE / "renders" / "ringer_horse_hero"

PARAMS = {
    "aspect": [3, 2],
    "soften_sigma": 1.0,                 # brief: 0.8-1.2
    "soften_zone_x": [0.30, 0.44],       # full strength left of 30% width, feathered to zero by 44% (muzzle is at 46%)
    "scrim_rgb": [17, 17, 15],           # --deep
    "scrim_alpha": [0.0, 0.52],          # top of the ramp -> bottom edge
    "scrim_from_y": 0.50,                # the ramp starts at half height
    "grain_sigma": 2.5,                  # brief: 2.0-3.0, 8-bit units, luma only
    "grain_size_sigma": 0.55,            # pre-blur of the noise field: a fine clump, not single-pixel hash
    "seed": 184,
    "webp_quality": 86,
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def gauss_sep(a, sigma):
    """Separable Gaussian on a float 2-D array (Pillow's GaussianBlur does not take mode "F")."""
    r = max(1, int(np.ceil(3 * sigma)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1, dtype=np.float32) / sigma) ** 2); k /= k.sum()
    h, w = a.shape
    p = np.pad(a, ((0, 0), (r, r)), mode="reflect")
    a = sum(k[i] * p[:, i:i + w] for i in range(2 * r + 1))
    p = np.pad(a, ((r, r), (0, 0)), mode="reflect")
    return sum(k[i] * p[i:i + h, :] for i in range(2 * r + 1))


def crop_3x2(im):
    w, h = im.size
    tw, th = (w // 3) * 3, (w // 3) * 2
    if th > h:                                   # taller than 3:2 is not the case here, but stay exact either way
        th = (h // 2) * 2; tw = th // 2 * 3
    tw -= tw % 6; th = tw // 3 * 2               # keep both even
    x0, y0 = (w - tw) // 2, (h - th) // 2
    return im.crop((x0, y0, x0 + tw, y0 + th))


def grade(im, p=PARAMS):
    im = crop_3x2(im.convert("RGB"))
    w, h = im.size
    a = np.asarray(im, dtype=np.float32)

    # 2. depth soften behind the tee's zone (left/centre), horizontal feather that ends before the horse
    soft = np.asarray(im.filter(ImageFilter.GaussianBlur(p["soften_sigma"])), dtype=np.float32)   # Pillow: radius = sigma
    x = (np.arange(w, dtype=np.float32) + 0.5) / w
    m = 1.0 - smoothstep(*p["soften_zone_x"], x)                       # 1 on the left, 0 from 44% rightwards
    a = a + (soft - a) * m[None, :, None]

    # 3. bottom-up scrim (sRGB "over", as the CSS gradient would composite)
    y = (np.arange(h, dtype=np.float32) + 0.5) / h
    a0, a1 = p["scrim_alpha"]
    alpha = a0 + (a1 - a0) * smoothstep(p["scrim_from_y"], 1.0, y)
    scrim = np.asarray(p["scrim_rgb"], dtype=np.float32)
    a = a * (1.0 - alpha[:, None, None]) + scrim[None, None, :] * alpha[:, None, None]

    # 4. luma-only grain: one zero-mean field, pre-blurred to a fine clump, renormalised to the target sigma
    rng = np.random.default_rng(p["seed"])
    n = gauss_sep(rng.standard_normal((h, w)).astype(np.float32), p["grain_size_sigma"])
    n = (n - n.mean()) / (n.std() + 1e-8) * p["grain_sigma"]
    a = a + n[:, :, None]

    return Image.fromarray(np.clip(np.rint(a), 0, 255).astype(np.uint8))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="grade in memory and report, write nothing")
    args = ap.parse_args()
    if not SRC.is_file():
        sys.exit(f"missing source plate: {SRC}")
    with Image.open(SRC) as raw:
        src_size = raw.size
        out = grade(raw)
    w, h = out.size
    assert w * 2 == h * 3, (w, h)
    if args.check:
        print(f"checked: {SRC.name} {src_size[0]}x{src_size[1]} -> {w}x{h} (3:2), nothing written")
        return
    OUT.parent.mkdir(parents=True, exist_ok=True)
    png, webp, rec = OUT.with_suffix(".png"), OUT.with_suffix(".webp"), OUT.with_suffix(".json")
    out.save(png, "PNG", optimize=True)
    out.save(webp, "WEBP", quality=PARAMS["webp_quality"], method=6)
    record = {
        "kind": "hero_plate",
        "what": "graded campaign plate behind the floating BS-RINGER-001 cut-out: a rider carrying a pale banner on a dark horse, misty mountains (brand-owned, logo-free)",
        "source": {"file": "hero/sources/horse_plate_raw.jpg", "sha256": sha256(SRC), "size": list(src_size)},
        "output": {"file": "hero/renders/ringer_horse_hero.png", "sha256": sha256(png), "size": [w, h],
                   "webp": {"file": "hero/renders/ringer_horse_hero.webp", "sha256": sha256(webp), "bytes": webp.stat().st_size}},
        "floats_in_front": {"sku": "BS-RINGER-001", "colourway": "white-black", "view": "front", "layer": "cut-out (.hero-plate .fg)"},
        "params": PARAMS,
        "script": "catalogue/hero/grade_horse_hero.py",
    }
    rec.write_text(json.dumps(record, indent=1) + "\n", encoding="utf-8")
    print(f"graded: {SRC.name} {src_size[0]}x{src_size[1]} -> {png.name} {w}x{h} ({png.stat().st_size // 1024} KB), "
          f"{webp.name} ({webp.stat().st_size // 1024} KB), {rec.name}")


if __name__ == "__main__":
    main()
