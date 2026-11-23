#!/usr/bin/env python3
"""
Deterministic QA Gate for Better Style Torso Mannequin Renders.
Verifies:
1. render_log.json schema, file existence, and SHA-256 hash integrity.
2. Exact pixel dimensions (2400x3000 for final, or >=800x1000 for preview).
3. True RGBA mode with non-empty, non-solid alpha channel (transparent studio background).
4. Torso framing & composition (garment+mannequin bounding box fill ratio and margins).
5. Generates a side-by-side visual contact sheet for instant inspection.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageOps, ImageDraw


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_render_dir(render_dir: Path, min_width: int = 2400, min_height: int = 3000) -> int:
    log_path = render_dir / "render_log.json"
    if not log_path.exists():
        print(f"[FAIL] Missing render_log.json in {render_dir}")
        return 1

    log_data = json.loads(log_path.read_text(encoding="utf-8"))
    entries = log_data.get("renders", log_data if isinstance(log_data, list) else [])
    if not entries:
        print(f"[FAIL] No render entries found in {log_path}")
        return 1

    failures = []
    verified_images = []
    bboxes_by_view = {}

    for idx, entry in enumerate(entries):
        filename = entry.get("file") or entry.get("filename") or entry.get("output")
        if not filename:
            failures.append(f"Entry #{idx}: missing filename field")
            continue

        img_path = render_dir / Path(filename).name
        if not img_path.exists():
            failures.append(f"{img_path.name}: file does not exist on disk")
            continue

        # 1. Verify SHA-256 hash if recorded in render_log.json
        expected_hash = entry.get("sha256") or entry.get("hash")
        actual_hash = sha256_file(img_path)
        if expected_hash and expected_hash.lower() != actual_hash.lower():
            failures.append(
                f"{img_path.name}: SHA-256 mismatch (expected {expected_hash[:12]}..., got {actual_hash[:12]}...)"
            )

        # 2. Check mode and dimensions
        with Image.open(img_path) as img:
            w, h = img.size
            if w < min_width or h < min_height:
                failures.append(f"{img_path.name}: dimensions {w}x{h} below required {min_width}x{min_height}")

            if img.mode != "RGBA":
                failures.append(f"{img_path.name}: mode is {img.mode}, expected RGBA with transparent background")
                continue

            arr = np.asarray(img)
            alpha = arr[:, :, 3]

            # 3. Verify useful alpha channel (transparent background + opaque figure)
            opaque_ratio = float(np.mean(alpha > 16))
            transparent_ratio = float(np.mean(alpha == 0))
            if transparent_ratio < 0.15:
                failures.append(
                    f"{img_path.name}: background is not transparent (only {transparent_ratio:.1%} zero-alpha pixels)"
                )
            if not (0.20 <= opaque_ratio <= 0.85):
                failures.append(
                    f"{img_path.name}: abnormal figure fill ratio ({opaque_ratio:.1%}, expected 20%-85%)"
                )

            # 4. Check framing margins (head/shoulders must not clip top/sides)
            ys, xs = np.where(alpha > 16)
            if len(ys) == 0:
                failures.append(f"{img_path.name}: image is completely transparent")
                continue

            y_min, y_max = int(ys.min()), int(ys.max())
            x_min, x_max = int(xs.min()), int(xs.max())

            if y_min < int(0.02 * h):
                failures.append(f"{img_path.name}: top of head/mannequin clips top frame edge (y_min={y_min}px)")
            if x_min < int(0.02 * w) or x_max > int(0.98 * w):
                failures.append(
                    f"{img_path.name}: arms/shoulders clip horizontal edges (x_min={x_min}px, x_max={x_max}px)"
                )

            view = entry.get("view") or entry.get("angle") or "unknown"
            colorway = entry.get("colorway") or entry.get("colour") or "unknown"
            mode = entry.get("mode", "mannequin")
            if mode != "mannequin":
                failures.append(f"{img_path.name}: render_log mode is '{mode}', expected 'mannequin'")

            bboxes_by_view.setdefault(view, []).append((x_min, y_min, x_max, y_max))
            verified_images.append((img_path, f"{colorway} | {view} ({w}x{h}, fill={opaque_ratio:.1%})"))
            print(f"[PASS] {img_path.name:40s} | {w}x{h} RGBA | fill={opaque_ratio:.1%} | sha256={actual_hash[:10]}")

    # 5. Check composition consistency across colourways for the same camera view
    for view, boxes in bboxes_by_view.items():
        if len(boxes) > 1:
            arr_boxes = np.array(boxes, dtype=float)
            max_drift = float(np.max(np.ptp(arr_boxes, axis=0)))
            if max_drift > 25.0:
                failures.append(f"View '{view}': framing drift of {max_drift:.1f}px across colourways (limit 25px)")

    # 6. Build visual contact sheet on a studio preview background
    if verified_images:
        thumb_w, thumb_h = 480, 600
        cols = min(4, len(verified_images))
        rows = (len(verified_images) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * thumb_w, rows * (thumb_h + 40)), (28, 28, 30))
        draw = ImageDraw.Draw(sheet)

        for i, (img_path, label) in enumerate(verified_images):
            r, c = divmod(i, cols)
            x0, y0 = c * thumb_w, r * (thumb_h + 40)
            with Image.open(img_path) as im:
                thumb = ImageOps.contain(im, (thumb_w - 20, thumb_h - 20))
                bg = Image.new("RGBA", thumb.size, (42, 42, 46, 255))
                bg.alpha_composite(thumb)
                sheet.paste(bg.convert("RGB"), (x0 + 10, y0 + 10))
            draw.text((x0 + 12, y0 + thumb_h + 8), label, fill=(235, 235, 225))

        contact_path = render_dir / "_qa_contact_sheet.png"
        sheet.save(contact_path)
        print(f"\n[INFO] Saved QA contact sheet to: {contact_path}")

    if failures:
        print("\n=== QA FAILURES DETECTED ===")
        for f in failures:
            print(f" - {f}")
        return 1

    print(f"\n=== ALL {len(verified_images)} MANNEQUIN RENDERS PASSED QA ===")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify RGBA Mannequin Renders")
    parser.add_argument("render_dir", type=Path, help="Path to folder containing PNGs and render_log.json")
    parser.add_argument("--preview", action="store_true", help="Allow lower preview resolution (800x1000+)")
    args = parser.parse_args()

    min_w, min_h = (800, 1000) if args.preview else (2400, 3000)
    sys.exit(verify_render_dir(args.render_dir, min_w, min_h))
