"""Build storefront/data/products.json and web images from the approved pipeline outputs.

The storefront is a downstream consumer: this script only READS configs/skus, configs/archetypes,
garment_registry and assets/renders, and only WRITES inside storefront/ (data/products.json, media/).

A product reaches the page only when storefront/data/catalogue.json marks it "live" AND its renders
exist on disk and are listed in that SKU's render_log.json. Anything missing fails the build rather
than being filled in.

    python storefront/build.py            # build
    python storefront/build.py --check    # validate only, write nothing
"""
import argparse
import hashlib
import json
import math
import os
import random
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

STORE = Path(__file__).resolve().parent
# Engine inputs (configs, garment registry, renders) are read from the project-local snapshot in engine_inputs/,
# refreshed by tools/collect_engine_inputs.py. HUMAN_ENGINE is never read directly. BS_ENGINE_ROOT is for tests only.
ROOT = Path(os.environ.get("BS_ENGINE_ROOT") or (STORE / "engine_inputs"))
MEDIA = STORE / "media"


def rel(p):
    """Provenance path in the engine's original convention (relative to better_style_catalog/).
    engine_inputs sits inside STORE, so ROOT must be tried first."""
    p = Path(p)
    if p.is_relative_to(ROOT):
        return p.relative_to(ROOT).as_posix()
    return "storefront/" + p.relative_to(STORE).as_posix()


# ---- sale approval gate (schema /2) --------------------------------------------------------------------------------
# Commercial/descriptive fields reach the public products.json only when the owner marked them approved in
# catalogue.json. Price and sizes (what makes a piece orderable) additionally need the product's explicit sale approval
# (sale.status == "approved_for_sale"). Anything else is published as null, which the page treats as "not set".
GATED = ("prices", "sizes", "description", "material_line", "material", "care",
         "measurements", "size_conversion", "mannequin_measurements")
PURCHASE_FIELDS = ("prices", "sizes")
# Local-only preview of DRAFT values (never deployable: the output is flagged and publish/assemble refuse it).
PREVIEW_PROVISIONAL = os.environ.get("BS_PREVIEW_PROVISIONAL") == "1"
WA_RE = re.compile(r"\d{8,15}")      # same rule as tools/validate_catalogue.py (digits only, no +)
DEFAULT_CURRENCIES = {"NGN": {"locale": "en-NG", "decimals": 0}, "USD": {"locale": "en-US", "decimals": 2}}


def currency_formats(enabled):
    cfg = STORE.parent / "config/currencies.json"
    table = {c: {"locale": v["locale"], "decimals": v["decimals"]} for c, v in load(cfg)["currencies"].items()} if cfg.is_file() else DEFAULT_CURRENCIES
    return {c: table[c] for c in enabled if c in table}


def commercial(entry, site):
    """Public commercial fields for one product: owner-approved values only (see the gate above)."""
    appr = entry.get("approval") or {}
    sale = (entry.get("sale") or {}).get("status")
    sale_ok = (sale != "unavailable") if PREVIEW_PROVISIONAL else sale == "approved_for_sale"
    out = {}
    for k in GATED:
        ok = PREVIEW_PROVISIONAL or (appr.get(k) or {}).get("status") == "approved"
        if k in PURCHASE_FIELDS and not sale_ok:
            ok = False
        out[k] = entry.get(k) if ok else None
    cur = site.get("currency") or {}
    enabled, default = cur.get("enabled") or [], cur.get("default")
    prices = {c: v for c, v in (out["prices"] or {}).items() if c in enabled and v is not None}
    out["prices"] = prices or None
    out["price"] = prices.get(default)                      # the default-currency price; null if not approved
    out["currency"] = default if out["price"] is not None else None
    out["sale_status"] = "approved_for_sale" if sale_ok else ("unavailable" if sale == "unavailable" else "not_for_sale")
    out["inquiry_sizes"] = inquiry_sizes(entry, site, sale)
    return out


def inquiry_sizes(entry, site, sale):
    """Size labels a shopper may ask about by WhatsApp while the piece is NOT approved for sale (price on inquiry).

    This is deliberately a separate field from the gated "sizes": "sizes" (with price and sale approval) is what makes
    a piece orderable and stays null until the owner approves it. An inquiry is a question, not an order, so the
    size run the owner entered is offered for it as labels only (no stock), and only when an inquiry channel
    (site.checkout.whatsapp) is configured and the piece is not withdrawn."""
    if sale == "unavailable" or not WA_RE.fullmatch(str((site.get("checkout") or {}).get("whatsapp") or "")):
        return None
    labels = [s if isinstance(s, str) else (s or {}).get("label") for s in (entry.get("sizes") or [])]
    return [l for l in labels if isinstance(l, str) and l.strip()] or None


TILE_SIZE = (720, 900)    # 4:5, matches the 1200x1500 renders
FULL_SIZE = (1200, 1500)
VIEW_BY_ANGLE = {0: "front", 30: "three_quarter", 35: "three_quarter", 45: "three_quarter", 180: "back"}   # 45: mannequin catalogue
VIEW_ORDER = ["front", "three_quarter", "back"]
FINISH_ORDER = ["M1", "M2", "M3"]


def mannequin_finishes():
    """The canonical mannequin finishes (engine configs/mannequin_finishes.json): {"M1": {"name", "hex"}, ...}."""
    return load(ROOT / "configs/mannequin_finishes.json")["finishes"]


SKIN_RE = re.compile(r"_skin_(M\d+)\.png$")
FINISH_DIR_RE = re.compile(r"M\d+")


def skin_finish(log):
    """The finish a render_log's run used, from the engine's own provenance: render_sku.py records every image it loaded, and the
    recoloured mannequin skin is _skin_<finish>.png. None when the log names no skin or several (e.g. a merged log)."""
    provs = [log.get("provenance") or {}] + list(log.get("provenance_runs") or [])
    skins = {m.group(1) for p in provs for k in ((p or {}).get("image_files") or {}) for m in [SKIN_RE.search(k)] if m}
    return skins.pop() if len(skins) == 1 else None
NECKLINE = {"crew rib": "Crew rib neckline", "fold-over collar": "Fold-over collar"}
SLEEVE = {"SET_IN": "Set-in sleeves", "RAGLAN": "Raglan sleeves"}
CLOSURE = {"1-button placket": "One-button placket"}


class BuildError(Exception):
    pass


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def lightness(hex_colour):
    """CIE L* of an sRGB hex colour; the chromatic sort key."""
    rgb = [int(hex_colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    y = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return round(116 * (y ** (1 / 3) if y > 0.008856 else 7.787 * y + 16 / 116) - 16, 1)


def family_index():
    data = load(ROOT / "garment_registry/garment_families/garment_families.json")
    families = data["families"] if isinstance(data, dict) else data
    return {f["id"]: f for f in families}


def construction_details(family, colourway):
    c = family.get("construction", {})
    out = [NECKLINE.get(c.get("neckline")), SLEEVE.get(c.get("sleeve_cut")), CLOSURE.get(c.get("closure"))]
    trims = [k for k in ("collar_rib", "left_cuff_rib") if colourway.get(k) and colourway[k] != colourway["main_body"]]
    if trims == ["collar_rib", "left_cuff_rib"]:
        out.append("Contrast rib at the neckline and sleeve cuffs")
    elif trims == ["collar_rib"]:
        out.append("Contrast collar")
    return [d for d in out if d]


def has_native_alpha(path):
    """True when a PNG carries a real RGBA alpha channel with transparent pixels (a cutout even with no _alpha.png companion)."""
    with Image.open(path) as im:
        return im.mode == "RGBA" and im.getchannel("A").getextrema()[0] < 250


def export(src, dst, size, quality, write):
    if write:
        dst.parent.mkdir(parents=True, exist_ok=True)
        im = src if isinstance(src, Image.Image) else Image.open(src)
        im = im.convert("RGBA" if im.mode in ("RGBA", "LA") else "RGB")   # alpha is kept, never flattened
        im.thumbnail(size, Image.LANCZOS)
        im.save(dst, "WEBP", quality=quality, method=6, lossless=False, exact=False)
    return "media/" + dst.relative_to(MEDIA).as_posix()


def hem_trim(src, background, body_hex, margin=0.005):
    """Mannequin render (transparent) -> on the ground colour, cut just below the garment hem so no legs show.

    The hem is found on a band around the figure's centre line: scanning up from the bottom edge, the first row
    whose pixels are mostly nearer the garment's body colour than the mannequin's own colour is the garment. The cut keeps the frame's aspect ratio by
    adding plain ground above the head (the ground is a flat colour, so nothing is invented)."""
    rgba = Image.open(src).convert("RGBA")
    w, h = rgba.size
    a = np.asarray(rgba).astype(float)
    opaque = a[..., 3] > 250
    cols = np.where(opaque[int(h * 0.97):].any(0))[0]
    cx = int(cols.mean()) if cols.size else w // 2
    band = a[:, max(cx - w // 20, 0):cx + w // 20]
    bottom = a[int(h * 0.94):, max(cx - w // 6, 0):cx + w // 6].reshape(-1, 4)   # thighs (the centre line itself can be the gap between them)
    bottom = bottom[bottom[:, 3] > 250][:, :3]
    if not len(bottom):
        return Image.alpha_composite(Image.new("RGBA", (w, h), background), rgba).convert("RGB"), {"hem_px": None, "cut_px": h, "of": h}
    skin = np.median(bottom, 0)
    cloth = np.array([int(body_hex.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)], float)   # the colourway's body colour (renders are calibrated to it)
    hem = h
    for y in range(h - 1, h // 2, -1):                                             # shaded body reads darker than lit body: classify by nearest
        row = band[y]
        px = row[row[:, 3] > 250][:, :3]
        if px.size and (np.abs(px - cloth).sum(1) < np.abs(px - skin).sum(1)).mean() > 0.5:
            hem = y
            break
    cut = min(h, hem + int(h * margin))
    ground = Image.new("RGBA", (w, h), background)
    ground.alpha_composite(rgba.crop((0, 0, w, cut)), (0, h - cut))
    return ground.convert("RGB"), {"hem_px": hem, "cut_px": cut, "of": h}


def mannequin_renders(sku, cfg, set_name, cw_id, write):
    """Renders from assets/renders/<SKU>/<set_name>/<finish>/render_log.json (one finish folder per colourway)."""
    finishes = load(ROOT / "configs/mannequin_finishes.json")["finishes"]
    background = cfg["render"].get("background_hex") or "#FFFFFF"
    out = []
    for fdir in sorted((ROOT / "assets/renders" / sku / set_name).glob("M*")):
        log_path = fdir / "render_log.json"
        if not log_path.exists() or fdir.name not in finishes:
            continue
        for r in load(log_path)["renders"]:
            if r["colorway"] != cw_id or r["mode"] != "mannequin":
                continue
            src = fdir / r["file"]
            if not src.exists():
                raise BuildError(f"{sku}: {rel(log_path)} lists {r['file']} but the file is missing")
            view = VIEW_BY_ANGLE.get(r["angle"], f"angle_{r['angle']}")
            stem = f"{sku}/{cw_id}_mannequin_{view}"
            body = next(c["main_body"] for c in cfg["colorways"] if c["id"] == cw_id)
            im, trim = hem_trim(src, background, body) if write else (None, None)
            out.append({
                "view": view,
                "presentation": "mannequin",
                "crop": "hip",
                "mannequin_finish": finishes[fdir.name]["name"],
                "finish": fdir.name,
                "finish_hex": finishes[fdir.name]["hex"],
                "background": background,
                "tile": export(im, MEDIA / f"{stem}_tile.webp", TILE_SIZE, 82, write),
                "full": export(im, MEDIA / f"{stem}_full.webp", FULL_SIZE, 88, write),
                "source": {"file": rel(src), "sha256": sha256(src), "res": r["res"], "trim": trim},
            })
    return out


def build_product(entry, families, write, site):
    sku = entry["sku"]
    cfg = load(ROOT / "configs/skus" / f"{sku}.json")
    family_id = load(ROOT / cfg["archetype_spec"])["family_id"]
    family = families[family_id]
    render_dir = STORE / entry["render_dir"] if entry.get("render_dir") else ROOT / "assets/renders" / sku   # storefront-side renders (e.g. from a candidate master)
    log_path = render_dir / "render_log.json"
    if not log_path.exists():
        raise BuildError(f"{sku}: live but has no {rel(log_path)}")
    def read_log(d):
        lg = load(d / "render_log.json")
        evidence = {"log skin": skin_finish(lg), "folder": d.name if FINISH_DIR_RE.fullmatch(d.name) else None}
        return [dict(r, _dir=d, _finish_evidence=evidence) for r in lg["renders"]]
    log = read_log(render_dir)
    for extra in entry.get("extra_render_dirs", []):              # extra views rendered later into the storefront (e.g. backs)
        log += read_log(STORE / extra)
    config_cws = {c["id"]: c for c in cfg["colorways"]}
    finishes = mannequin_finishes()
    cfg_finish = (cfg["render"].get("mannequin") or {}).get("finish")      # the SKU's configured finish (older logs do not record one per render)

    colourways = []
    for cw_id, merch in entry["colourways"].items():
        if cw_id not in config_cws:
            raise BuildError(f"{sku}: colourway {cw_id} is not in configs/skus/{sku}.json")
        cw = config_cws[cw_id]
        renders, seen = [], {}
        for r in log:
            if r["colorway"] != cw_id:
                continue
            src = r["_dir"] / r["file"]
            if not src.exists():
                raise BuildError(f"{sku}: render_log lists {r['file']} but the file is missing")
            view = VIEW_BY_ANGLE.get(r["angle"], f"angle_{r['angle']}")
            fid = None
            if r["mode"] == "mannequin":
                # the finish: the render's own "finish" (render_sku.py since 2026-11-15), else the skin its run loaded (provenance),
                # else the M<n> folder it is filed in, else the SKU config. Any two that disagree stop the build.
                said = {k: v for k, v in [("render_log finish", r.get("finish")), *r["_finish_evidence"].items()] if v}
                if len(set(said.values())) > 1:
                    raise BuildError(f"{sku}: {rel(r['_dir'] / r['file'])} has conflicting finishes {said}")
                fid = next(iter(said.values()), None) or cfg_finish
                if fid not in finishes:
                    raise BuildError(f"{sku}: {r['file']} is a mannequin render with no known finish ({fid!r}); expected one of {sorted(finishes)}")
            key = (r["mode"], fid, view)
            if key in seen:                 # two renders claiming the same slot: the catalogue wiring is ambiguous, never pick one silently
                raise BuildError(f"{sku}/{cw_id}: {r['file']} and {seen[key]} are both the {r['mode']} {fid or ''} {view} view; "
                                 f"remove one folder from extra_render_dirs")
            seen[key] = r["file"]
            stem = f"{sku}/{cw_id}_{r['mode']}_{fid + '_' if fid else ''}{view}"   # one file per finish
            alpha = src.with_name(src.stem + "_alpha.png")
            plain = alpha.exists()          # silent technicality: the garment sits on the page itself, no tile ground
            native = not plain and has_native_alpha(src)   # transparent studio render (e.g. torso mannequin): the CSS studio shows behind it
            q_tile, q_full = (90, 90) if native else (82, 88)
            rec = {
                "view": view,
                "presentation": r["mode"],  # floating | mannequin
                "crop": "floating" if r["mode"] == "floating" else ((cfg["render"].get("framing") or {}).get("mannequin_profile")
                                                                    or ("torso" if r.get("framing") else "full")),
                "mannequin_finish": finishes[fid]["name"] if fid else None,
                "finish": fid,
                "finish_hex": finishes[fid]["hex"] if fid else None,
                "background": None if plain or native else (r.get("background_hex") or cfg["render"].get("background_hex")),
                "tile": export(alpha if plain else src, MEDIA / f"{stem}_tile.webp", TILE_SIZE, q_tile, write),
                "full": export(alpha if plain else src, MEDIA / f"{stem}_full.webp", FULL_SIZE, q_full, write),
                "source": {"file": rel(src), "sha256": sha256(src), "res": r["res"]},
            }
            if plain:
                rec["cutout"] = export(alpha, MEDIA / f"{stem}_cutout.webp", FULL_SIZE, 88, write)
            renders.append(rec)
        if not renders:
            raise BuildError(f"{sku}: colourway {cw_id} has no renders in render_log.json")
        if entry.get("mannequin_set"):
            renders += mannequin_renders(sku, cfg, entry["mannequin_set"], cw_id, write)
        renders.sort(key=lambda x: (x["presentation"] != "floating",
                                    FINISH_ORDER.index(x["finish"]) if x.get("finish") in FINISH_ORDER else -1,
                                    VIEW_ORDER.index(x["view"]) if x["view"] in VIEW_ORDER else 99))
        colourways.append({
            "id": cw_id,
            "label": merch["label"],
            "colour_family": merch["label"].split("/")[0].strip(),
            "hex": cw["main_body"],
            "trim_hex": cw.get("collar_rib"),
            "chromatic_order": merch.get("chromatic_order", lightness(cw["main_body"])),
            "stock": merch.get("stock"),
            "details": construction_details(family, cw),
            "renders": renders,
        })

    shown = sorted({r["finish"] for c in colourways for r in c["renders"] if r.get("finish")}, key=FINISH_ORDER.index)
    want = entry.get("mannequin_default_finish") or cfg_finish or "M2"   # the product page opens on this finish when it has it
    return {
        "sku": sku,
        "title": entry.get("title") or cfg["name"],
        "family": {"id": family_id, "name": family["name"]},
        "category": family["group"],
        "department": entry.get("department"),
        "featured_rank": entry.get("featured_rank", 999),
        "new_in": entry.get("new_in"),
        "mannequin_default_finish": (want if want in shown else shown[0]) if shown else None,
        **commercial(entry, site),
        "colourways": colourways,
    }


def flat_on_ground(src, ground_hex, occupancy=0.88):
    """Owner product cut-out (RGBA) centred on the catalogue ground in a 4:5 frame, at its NATIVE pixel size
    (the canvas is sized around the image; nothing is upscaled). Returns an RGB image."""
    im = Image.open(src).convert("RGBA")
    im = im.crop(im.getchannel("A").getbbox())
    k = math.ceil(max(im.height / occupancy / 5, im.width / occupancy / 4))   # frame = 4k x 5k: exactly 4:5, fits both dimensions
    w, h = 4 * k, 5 * k
    canvas = Image.new("RGBA", (w, h), ground_hex or (0, 0, 0, 0))
    canvas.alpha_composite(im, ((w - im.width) // 2, (h - im.height) // 2))
    return canvas if ground_hex is None else canvas.convert("RGB")


def body_hex(src, ground=None):
    """Median colour of the fully opaque pixels: a measured swatch, not a chosen one. For an opaque set sheet, pass the
    sheet's own ground colour: pixels near it are left out, so the swatch is the garment and not the paper around it."""
    import numpy as np
    a = np.asarray(Image.open(src).convert("RGBA")).reshape(-1, 4)
    px = a[a[:, 3] > 250][:, :3].astype(int)
    if ground:
        g = np.array([int(ground.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)])
        cloth = px[np.abs(px - g).sum(1) > 90]
        px = cloth if len(cloth) else px
    rgb = np.median(px, axis=0).astype(int)
    return "#%02X%02X%02X" % tuple(rgb)


def model_render(sku, cw_id, rel_path, poster, write):
    """Interactive 3D view of a colourway: the .glb in storefront/sources/<SKU>/ is copied unchanged to media/<SKU>/<cw>_model.glb.
    It is listed as one more view (presentation "model") after the images; its poster is the colourway's first image, so the
    gallery, the grid and no-WebGL devices keep showing the approved still. A sidecar <name>.report.json (export_set_glb.py),
    when present, supplies the stone count shown on the page; nothing else from it is published."""
    src = STORE / rel_path
    if not src.is_file():
        raise BuildError(f"{sku}: model {rel_path} is listed but missing")
    if src.read_bytes()[:4] != b"glTF":
        raise BuildError(f"{sku}: model {rel_path} is not a binary glTF (.glb)")
    dst = MEDIA / sku / f"{cw_id}_model.glb"
    if write:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
    side = src.with_suffix(".report.json")
    stones = load(side).get("stones_total") if side.is_file() else None
    return {
        "view": "3d", "presentation": "model", "crop": "floating", "mannequin_finish": None,
        "background": poster["background"], "sheet_ground": poster.get("sheet_ground"),
        "tile": poster["tile"], "full": poster["full"],
        "model": {"src": "media/" + dst.relative_to(MEDIA).as_posix(), "bytes": src.stat().st_size, "stones": stones},
        "source": {"file": "storefront/" + rel_path, "sha256": sha256(src), "kind": "engine_set_glb"},
    }


def build_flat_product(entry, families, write, site):
    """Products shown from the owner's own cut-out product images (no 3D master yet). Images live in
    storefront/sources/<SKU>/ as copies; their provenance is recorded in the catalogue entry."""
    sku = entry["sku"]; ground = entry.get("ground_hex")   # None = transparent, on the page itself
    family = families[entry["family_id"]] if entry.get("family_id") else {"id": None, "name": None, "group": entry["category"]}
    colourways = []
    for cw_id, merch in entry["colourways"].items():
        renders = []
        for view in VIEW_ORDER:
            rel = merch.get("views", {}).get(view)
            if not rel:
                continue
            src = STORE / rel
            if not src.exists():
                raise BuildError(f"{sku}: {rel} is listed but missing")
            stem = MEDIA / sku / f"{cw_id}_flat_{view}"
            if write:
                stem.parent.mkdir(parents=True, exist_ok=True)
                comp = flat_on_ground(src, ground)
                comp.save(stem.with_name(stem.name + "_full.webp"), "WEBP", quality=88, method=6)
                t = comp.copy(); t.thumbnail(TILE_SIZE, Image.LANCZOS); t.save(stem.with_name(stem.name + "_tile.webp"), "WEBP", quality=82, method=6)
            with Image.open(src) as im:
                res = list(im.size)
                # a composed front-and-back set sheet is an opaque plate with its own ground: measure that ground (corner
                # pixel) so the page can continue it around the sheet instead of showing a plate inside a plate
                sheet_ground = "#%02X%02X%02X" % im.convert("RGB").getpixel((2, 2)) if entry.get("source_kind") == "engine_set_sheet" else None
            renders.append({
                "view": view, "presentation": entry.get("presentation", "flat"), "crop": "floating", "mannequin_finish": None, "background": ground,
                "sheet_ground": sheet_ground,
                "tile": "media/" + stem.relative_to(MEDIA).as_posix() + "_tile.webp",
                "full": "media/" + stem.relative_to(MEDIA).as_posix() + "_full.webp",
                "source": {"file": "storefront/" + rel, "sha256": sha256(src), "res": res, "kind": entry.get("source_kind")},
            })
        if not renders:
            raise BuildError(f"{sku}: colourway {cw_id} has no images")
        if merch.get("model"):
            renders.append(model_render(sku, cw_id, merch["model"], renders[0], write))
        hx = merch.get("hex") or body_hex(STORE / merch["views"][renders[0]["view"]], renders[0]["sheet_ground"])
        colourways.append({
            "id": cw_id, "label": merch["label"], "colour_family": merch["label"].split("/")[0].strip(),
            "hex": hx, "trim_hex": None, "chromatic_order": merch.get("chromatic_order", lightness(hx)),
            "stock": merch.get("stock"), "details": entry.get("details") or [], "renders": renders,
        })
    return {
        "sku": sku, "title": entry["title"], "family": {"id": family["id"], "name": family["name"]},
        "category": family["group"], "department": entry.get("department"),
        "featured_rank": entry.get("featured_rank", 999), "new_in": entry.get("new_in"),
        "mannequin_default_finish": None,                  # owner cut-outs / set sheets: no mannequin views
        **commercial(entry, site),
        "colourways": colourways,
    }


def torn_edge_svg(seed=184, width=1600, height=64):
    """Uneven torn-paper edge: paper-coloured shape whose top contour is multi-scale jitter."""
    rnd = random.Random(seed)
    pts, x, y = [], 0.0, height * 0.45
    while x <= width:
        y += rnd.uniform(-5, 5) + (height * 0.45 - y) * 0.12   # coarse wander, pulled to the mid line
        fine = rnd.uniform(-2.2, 2.2)                           # fibre-scale jaggies
        pts.append(f"{x:.1f},{max(4, min(height - 10, y + fine)):.1f}")
        x += rnd.uniform(3, 9)
    pts.append(f"{width},{max(4, min(height - 10, y)):.1f}")  # close the contour exactly at the right edge
    contour = " ".join(pts)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" preserveAspectRatio="none">'
            f'<polygon points="0,{height} {contour} {width},{height}" fill="#F2EEE7"/>'
            f'<polyline points="{contour}" fill="none" stroke="#ffffff" stroke-opacity=".55" stroke-width="1.4"/></svg>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="validate only, write nothing")
    ap.add_argument("--data-only", action="store_true",
                    help="rewrite data/products.json from catalogue.json WITHOUT re-encoding images (they must already be built); fast path for previews")
    args = ap.parse_args()
    write = not args.check and not args.data_only     # export images
    write_data = not args.check                       # write data/products.json

    cat = load(STORE / "data/catalogue.json")
    families = family_index()
    live = [p for p in cat["products"] if p["status"] == "live"]
    held = [p for p in cat["products"] if p["status"] != "live"]
    if args.data_only and any(p.get("mannequin_set") for p in live):
        sys.exit("BUILD FAILED: --data-only does not support mannequin_set products; run a full build")
    try:
        products = [(build_flat_product if p.get("source_kind") else build_product)(p, families, write, cat["site"]) for p in live]
        hero = cat["site"]["hero"]
        if hero:
            prod = next((p for p in products if p["sku"] == hero["sku"]), None)
            cw = prod and next((c for c in prod["colourways"] if c["id"] == hero["colourway"]), None)
            r = cw and next((r for r in cw["renders"] if r["view"] == hero["view"]), None)
            if not r:
                raise BuildError(f"hero points at {hero} which is not a live render")
            hero = {**hero, "image": r.get("cutout") or r["full"], "cutout": bool(r.get("cutout")), "editorial": False}
            ed = hero.get("editorial_source")                       # storefront/hero/build_hero.py output
            if ed:
                src = STORE / ed
                record = src.with_suffix(".json")
                if src.exists() and record.exists():
                    rec = load(record)
                    if rec["garment"]["sku"] != hero["sku"] or rec["garment"]["colourway"] != hero["colourway"]:
                        raise BuildError(f"hero editorial {ed} shows {rec['garment']}, not {hero['sku']}/{hero['colourway']}")
                    stem = MEDIA / "hero" / src.stem
                    hero.update(editorial=True, cutout=False,
                                image=export(src, stem.with_name(src.stem + "_2400.webp"), (2400, 2400), 84, write),
                                image_small=export(src, stem.with_name(src.stem + "_1200.webp"), (1200, 1200), 82, write),
                                editorial_record={"file": ed, "sha256": sha256(src), **{k: rec[k] for k in ("what", "props", "params")}})
                else:
                    print(f"WARNING: hero editorial {ed} (or its .json record) is missing; using the approved cut-out render")
    except BuildError as e:
        sys.exit(f"BUILD FAILED: {e}")

    site = {**cat["site"], "hero": hero}
    # the finish switcher's labels and swatches (ordered M1, M2, M3); a product offers only the finishes it has renders in
    site["mannequin_finishes"] = [{"id": k, **v} for k, v in sorted(mannequin_finishes().items(), key=lambda kv: FINISH_ORDER.index(kv[0]) if kv[0] in FINISH_ORDER else 99)]
    cur = site.get("currency") or {}
    site["currency"] = {**cur, "formats": currency_formats(cur.get("enabled") or [])}
    out = {
        "schema": "betterstyle.storefront_products/1",
        "site": site,
        "products": products,
        "looks": cat.get("looks", []),
        "held_back": [{"sku": p["sku"], "status": p["status"], "note": p.get("status_note")} for p in held],
    }
    if PREVIEW_PROVISIONAL:
        out["preview_provisional"] = True
        print("WARNING: provisional preview build (draft values shown); NOT deployable")
    if args.data_only:                                # images were not exported: every one the data points at must already exist
        refs = [r[k] for p in products for cw in p["colourways"] for r in cw["renders"] for k in ("tile", "full", "cutout") if r.get(k)]
        refs += [r["model"]["src"] for p in products for cw in p["colourways"] for r in cw["renders"] if r.get("model")]
        refs += [hero[k] for k in ("image", "image_small") if hero and hero.get(k)]
        missing = [m for m in refs if not (STORE / m).is_file()]
        if missing:
            sys.exit(f"BUILD FAILED: --data-only needs images that are not built yet (run a full build): {missing[:3]}")
    if write_data:
        MEDIA.mkdir(exist_ok=True)
        if write:
            (MEDIA / "torn-edge.svg").write_text(torn_edge_svg(), encoding="utf-8")
        (STORE / "data/products.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    n_tiles = sum(len(p["colourways"]) for p in products)
    print(f"{'checked' if args.check else 'built (data only)' if args.data_only else 'built'}: {len(products)} live product(s), {n_tiles} colourway tile(s); "
          f"held back: {', '.join(p['sku'] for p in held) or 'none'}")


if __name__ == "__main__":
    main()
