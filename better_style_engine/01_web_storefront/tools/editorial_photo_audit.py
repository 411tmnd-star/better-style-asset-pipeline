"""Read-only forensic audit of Better Style editorial photography on this PC.

    python -I tools/editorial_photo_audit.py scan [--out _audit] [--root LABEL=PATH ...]
    python -I tools/editorial_photo_audit.py finalize --picks _audit/picks.json [--out _audit]

scan      measures every image under the roots (and samples frames from their videos with OpenCV) and writes
          <out>/scan/photo_scan.json plus labelled contact sheets <out>/scan/sheet_<root>_<n>.jpg for visual review.
          Per image: width, height, aspect, file size, Laplacian-variance sharpness (on a copy scaled to 1024 px on the
          long side, so different resolutions compare), mean brightness, contrast (luminance std), warmth (mean R-B),
          SHA-256 (exact copies) and a 64-bit dHash (near-copies: re-saves, WhatsApp re-compressions), plus two hints
          for the reviewer: near-vertical line density in the upper half (architecture) and the sky-like share of the top band.
finalize  reads the reviewer's picks (ids from the scan, grouped A/B/C with notes) and writes
          <out>/editorial_photo_audit.json and the labelled <out>/editorial_contact_sheet.jpg.

Nothing outside --out is written. Source images and videos are only read. Files are written as .tmp then os.replace.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    import cv2
except ImportError:  # sharpness falls back to a numpy Laplacian; videos are skipped
    cv2 = None

CANON = Path(__file__).resolve().parent.parent
DESKTOP = Path.home() / "Desktop"
ART = DESKTOP / "artifacts"
DEFAULT_ROOTS = {
    "whatsapp_batch": ART / "04_brand_and_creative_assets/whatsapp_batch_2026-10-31",
    "landing_media": CANON / "landing/media",
    "curation_top": DESKTOP / "4041_MechMix/02_engines/human_engine/src_catalog/BETTER_STYLE_VISUAL_CURATION/08_TOP_REFERENCE_SELECTION",
    "curation_env": DESKTOP / "4041_MechMix/02_engines/human_engine/src_catalog/BETTER_STYLE_VISUAL_CURATION/07_BEST_ENVIRONMENT_REFERENCES",
    "curation_pose": DESKTOP / "4041_MechMix/02_engines/human_engine/src_catalog/BETTER_STYLE_VISUAL_CURATION/06_BEST_POSE_REFERENCES",
    "curation_garment": DESKTOP / "4041_MechMix/02_engines/human_engine/src_catalog/BETTER_STYLE_VISUAL_CURATION/05_BEST_GARMENT_REFERENCES",
    "curation_photos": DESKTOP / "4041_MechMix/02_engines/human_engine/src_catalog/BETTER_STYLE_VISUAL_CURATION/01_PHOTOGRAPH_INVENTORY",
    "research_visual_inputs": ART / "02_documents_and_research/Research/BETTER RESEARCH/RESEARCH/VISUAL-INPUTS",
    "picds": ART / "04_brand_and_creative_assets/picds",
    "picssilent": ART / "04_brand_and_creative_assets/picssilent",
    "411bttee": ART / "04_brand_and_creative_assets/411bttee",
    "misc_batch": ART / "04_brand_and_creative_assets/misc_batch_new_folder_4",
    "fam": ART / "04_brand_and_creative_assets/fam",
    "kkk444": ART / "04_brand_and_creative_assets/KKK444",
    "wkfll": ART / "04_brand_and_creative_assets/WKFLL",
    "artifacts_images": ART / "IMAGES",
    "gemini_landing_handoff": DESKTOP / "4041_MechMix/02_engines/better_style_engine/03_governance_and_evidence/handoffs/GEMINI_BETTER_STYLE_LANDING_PAGE",
    "better_style_shop": DESKTOP / "4041_MechMix/04_app_repos/better-style-shop",
    "better_style_standalone": DESKTOP / "4041_MechMix/04_app_repos/better-style-standalone",
}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "site-packages", "dist", "build"}
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VID_EXT = {".mp4", ".mov", ".m4v"}
VIDEO_ROOTS = {"whatsapp_batch", "landing_media", "research_visual_inputs", "411bttee", "picds", "picssilent"}
FRAMES_PER_VIDEO = 8


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def atomic_json(path: Path, obj) -> None:
    atomic_bytes(path, json.dumps(obj, indent=1, ensure_ascii=False).encode("utf-8"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def dhash(gray: Image.Image) -> str:
    a = np.asarray(gray.resize((9, 8), Image.Resampling.LANCZOS), dtype=np.int16)
    bits = (a[:, 1:] > a[:, :-1]).flatten()
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def laplacian_var(g: np.ndarray) -> float:
    if cv2 is not None:
        return float(cv2.Laplacian(g, cv2.CV_64F).var())
    k = -4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    return float(k.astype(np.float64).var())


def hints(rgb: np.ndarray, g: np.ndarray) -> tuple[float, float]:
    """(near-vertical line segments per megapixel in the upper half, sky-like share of the top 25 %)."""
    h, w = g.shape
    vert = 0.0
    if cv2 is not None:
        up = g[: h // 2]
        edges = cv2.Canny(up, 60, 160)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=60, minLineLength=max(20, h // 12), maxLineGap=6)
        if lines is not None:   # OpenCV 4 returns (N,1,4), OpenCV 5 (N,4): reshape covers both
            n = sum(1 for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4) if abs(x2 - x1) <= 0.12 * max(1, abs(y2 - y1)))
            vert = n / max(1e-6, (up.size / 1e6))
    top = rgb[: max(1, h // 4)].reshape(-1, 3).astype(np.int16)
    r, gg, b = top[:, 0], top[:, 1], top[:, 2]
    mx, mn = top.max(1), top.min(1)
    sat = (mx - mn) / np.maximum(mx, 1)
    sky = ((b > r + 8) & (b >= gg) & (mx > 90)) | ((mx > 170) & (sat < 0.12))
    return round(vert, 1), round(float(sky.mean()), 3)


def measure(path: Path, img: Image.Image) -> dict:
    img = ImageOps.exif_transpose(img).convert("RGB")
    w, h = img.size
    scale = min(1.0, 1024 / max(w, h))
    small = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS) if scale < 1 else img
    rgb = np.asarray(small)
    g = np.asarray(small.convert("L"))
    vert, sky = hints(rgb, g)
    return {
        "width": w, "height": h, "aspect": round(w / h, 3), "orientation": "portrait" if h > w * 1.05 else ("landscape" if w > h * 1.05 else "square"),
        "sharpness": round(laplacian_var(g), 1), "brightness": round(float(g.mean()), 1), "contrast": round(float(g.std()), 1),
        "warmth": round(float(rgb[..., 0].mean() - rgb[..., 2].mean()), 1), "dhash": dhash(small.convert("L")),
        "vert_lines_upper_per_mp": vert, "sky_top_share": sky,
    }


def walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for f in sorted(filenames):
            yield Path(dirpath) / f


def video_frames(path: Path, out_dir: Path) -> list[tuple[Path, float]]:
    if cv2 is None:
        return []
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    got = []
    if n > 0 and fps > 0:
        for k in range(FRAMES_PER_VIDEO):
            idx = int((k + 0.5) * n / FRAMES_PER_VIDEO)
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok:
                continue
            t = idx / fps
            dest = out_dir / f"{re.sub(r'[^A-Za-z0-9]+', '_', path.stem)[:60]}__t{t:06.2f}.jpg"
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
            if ok:
                atomic_bytes(dest, buf.tobytes())
                got.append((dest, t))
    cap.release()
    return got


def font(size: int):
    for name in ("arial.ttf", "segoeui.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def sheet(items: list[dict], dest: Path, title: str, cols: int = 8, tw: int = 240, th: int = 300, label=None) -> None:
    rows = max(1, -(-len(items) // cols))
    head = 44
    canvas = Image.new("RGB", (cols * tw, head + rows * (th + 34)), (18, 17, 15))
    d = ImageDraw.Draw(canvas)
    d.text((10, 10), title, fill=(240, 236, 228), font=font(20))
    f = font(13)
    for i, it in enumerate(items):
        x, y = (i % cols) * tw, head + (i // cols) * (th + 34)
        try:
            with Image.open(it["_thumb_src"]) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail((tw - 8, th - 8), Image.Resampling.LANCZOS)
                canvas.paste(im, (x + (tw - im.width) // 2, y + (th - im.height) // 2))
        except Exception as e:  # unreadable file: leave a gap with the error
            d.text((x + 6, y + 6), f"unreadable: {type(e).__name__}", fill=(200, 80, 80), font=f)
        text = label(it) if label else f"#{it['id']} {it['width']}x{it['height']} S{it['sharpness']:.0f}"
        d.text((x + 6, y + th + 4), text[:38], fill=(230, 226, 218), font=f)
    buf = dest.with_name(dest.name + ".tmp")
    canvas.save(buf, "JPEG", quality=86)
    os.replace(buf, dest)


def scan(out: Path, roots: dict[str, Path]) -> int:
    scan_dir = out / "scan"
    frames_dir = out / "video_frames"
    records, errors = [], []
    nid = 0
    for label, root in roots.items():
        if not root.exists():
            errors.append(f"{label}: missing {root}")
            continue
        for p in walk(root):
            ext = p.suffix.lower()
            sources: list[tuple[Path, dict]] = []
            if ext in IMG_EXT:
                sources.append((p, {"kind": "image"}))
            elif ext in VID_EXT and label in VIDEO_ROOTS:
                for fp, t in video_frames(p, frames_dir / label):
                    sources.append((fp, {"kind": "video_frame", "video": str(p), "t_s": round(t, 2)}))
            for src, extra in sources:
                try:
                    with Image.open(src) as im:
                        m = measure(src, im)
                except Exception as e:
                    errors.append(f"{src}: {type(e).__name__}: {e}")
                    continue
                nid += 1
                rec = {"id": nid, "root": label, "path": str(src), "file_size_kb": round(src.stat().st_size / 1024, 1),
                       "sha256": sha256(src), **extra, **m}
                records.append(rec)
        print(f"  {label:26s} {sum(1 for r in records if r['root'] == label):5d} items", file=sys.stderr)

    by_sha: dict[str, list[dict]] = {}
    for r in records:
        by_sha.setdefault(r["sha256"], []).append(r)
    for group in by_sha.values():
        keep = max(group, key=lambda r: (r["width"] * r["height"], r["sharpness"]))
        for r in group:
            r["exact_copy_of"] = None if r is keep else keep["id"]
    uniq = [r for r in records if r["exact_copy_of"] is None]
    for i, r in enumerate(uniq):              # near copies: same picture, different file (re-save / re-compression)
        r.setdefault("near_copy_of", None)
        for q in uniq[:i]:
            if q.get("near_copy_of") is None and hamming(r["dhash"], q["dhash"]) <= 5 and abs(r["aspect"] - q["aspect"]) < 0.04:
                better = max((r, q), key=lambda x: (x["width"] * x["height"], x["sharpness"]))
                worse = q if better is r else r
                worse["near_copy_of"] = better["id"]
                if worse is q:
                    q["near_copy_of"] = r["id"]
                break
    for r in records:
        r.setdefault("near_copy_of", None)
        r["_thumb_src"] = r["path"]

    scan_dir.mkdir(parents=True, exist_ok=True)
    per_root: dict[str, list[dict]] = {}
    for r in records:
        if r["exact_copy_of"] is None:
            per_root.setdefault(r["root"], []).append(r)
    sheets = []
    for label, items in per_root.items():
        for k in range(0, len(items), 48):
            dest = scan_dir / f"sheet_{label}_{k // 48 + 1:02d}.jpg"
            sheet(items[k:k + 48], dest, f"{label}  ({k + 1}-{min(k + 48, len(items))} of {len(items)} unique)")
            sheets.append(str(dest))
    clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in records]
    atomic_json(scan_dir / "photo_scan.json", {
        "schema": "betterstyle.editorial_photo_scan/1", "roots": {k: str(v) for k, v in roots.items()},
        "counts": {"items": len(records), "unique_files": len(uniq), "exact_copies": len(records) - len(uniq),
                   "near_copies": sum(1 for r in records if r["near_copy_of"])},
        "metric_notes": {"sharpness": "Laplacian variance on a copy scaled to <=1024 px long side; higher is crisper",
                         "vert_lines_upper_per_mp": "near-vertical Hough segments in the upper half per megapixel (architecture hint)",
                         "sky_top_share": "share of sky-like pixels in the top 25 % (blue-dominant or bright and unsaturated)"},
        "errors": errors, "sheets": sheets, "items": clean})
    print(json.dumps({"ok": not errors, "items": len(records), "unique": len(uniq), "sheets": len(sheets), "errors": len(errors),
                      "scan": str(scan_dir / "photo_scan.json")}))
    return 0


def finalize(out: Path, picks_path: Path) -> int:
    scan_obj = json.loads((out / "scan/photo_scan.json").read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in scan_obj["items"]}
    picks = json.loads(picks_path.read_text(encoding="utf-8"))
    groups, sheet_items, missing = {}, [], []
    for key in ("A_already_used_lookbook", "B_clearest_editorial_selection", "C_detail_and_texture"):
        rows = []
        for p in picks.get(key, []):
            r = by_id.get(p["id"])
            if r is None:
                missing.append(p["id"])
                continue
            rows.append({**{k: r[k] for k in ("id", "root", "path", "kind", "width", "height", "aspect", "file_size_kb", "sharpness",
                                              "brightness", "contrast", "warmth", "sha256", "vert_lines_upper_per_mp", "sky_top_share")},
                         "video": r.get("video"), "t_s": r.get("t_s"), **{k: v for k, v in p.items() if k != "id"}})
            sheet_items.append({**r, "_thumb_src": r["path"], "_group": key[0]})
        groups[key] = rows
    payload = {"schema": "betterstyle.editorial_photo_audit/1", "scan": str(out / "scan/photo_scan.json"),
               "scan_counts": scan_obj["counts"], "reviewer_notes": picks.get("notes", []), "rejected": picks.get("rejected", []),
               "groups": groups, "missing_ids": missing}
    atomic_json(out / "editorial_photo_audit.json", payload)
    sheet(sheet_items, out / "editorial_contact_sheet.jpg", "Better Style editorial audit  |  A = in use   B = selected editorial   C = detail / texture",
          cols=6, tw=300, th=375, label=lambda it: f"{it['_group']}  #{it['id']}  {it['width']}x{it['height']}  S{it['sharpness']:.0f}")
    print(json.dumps({"ok": not missing, "groups": {k: len(v) for k, v in groups.items()}, "missing": missing,
                      "audit": str(out / "editorial_photo_audit.json"), "sheet": str(out / "editorial_contact_sheet.jpg")}))
    return 0 if not missing else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--out", default=str(CANON / "_audit"))
    s.add_argument("--root", action="append", default=[], help="LABEL=PATH (repeatable); replaces the default roots")
    f = sub.add_parser("finalize")
    f.add_argument("--out", default=str(CANON / "_audit"))
    f.add_argument("--picks", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out).resolve()
    if a.cmd == "scan":
        roots = dict(DEFAULT_ROOTS) if not a.root else {k: Path(v) for k, v in (x.split("=", 1) for x in a.root)}
        return scan(out, roots)
    return finalize(out, Path(a.picks).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
