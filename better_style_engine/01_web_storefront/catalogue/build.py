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


TILE_SIZE = (720, 1000)   # 0.72 portrait grid ratio
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


def export_tile(src, dst, quality, write, bottom_align=False):
    """Export an exact 720x1000 (0.72) grid tile preserving aspect without stretch or crop."""
    if write:
        dst.parent.mkdir(parents=True, exist_ok=True)
        im = src if isinstance(src, Image.Image) else Image.open(src)
        is_alpha = im.mode in ("RGBA", "LA")
        im = im.convert("RGBA" if is_alpha else "RGB")
        im.thumbnail(TILE_SIZE, Image.LANCZOS)
        x = (TILE_SIZE[0] - im.width) // 2
        y = (TILE_SIZE[1] - im.height) if bottom_align else (TILE_SIZE[1] - im.height) // 2
        if is_alpha:
            canvas = Image.new("RGBA", TILE_SIZE, (0, 0, 0, 0))
            canvas.alpha_composite(im, (x, y))
        else:
            canvas = Image.new("RGB", TILE_SIZE, im.getpixel((2, 2)))
            canvas.paste(im, (x, y))
        canvas.save(dst, "WEBP", quality=quality, method=6, lossless=False, exact=False)
    return "media/" + dst.relative_to(MEDIA).as_posix()


def _legacy_mannequin_set_removed(sku):
    raise BuildError(
        f"{sku}: legacy 'mannequin_set' / hem_trim() path was removed in favor of 'extra_render_dirs' "
        f"(sources/<SKU>-MANNEQUIN/M1..M3 with CSS 82% bust fade)"
    )


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
                "tile": export_tile(alpha if plain else src, MEDIA / f"{stem}_tile.webp", q_tile, write, bottom_align=(r["mode"] == "mannequin")),
                "full": export(alpha if plain else src, MEDIA / f"{stem}_full.webp", FULL_SIZE, q_full, write),
                "source": {"file": rel(src), "sha256": sha256(src), "res": r["res"]},
            }
            if plain:
                rec["cutout"] = export(alpha, MEDIA / f"{stem}_cutout.webp", FULL_SIZE, 88, write)
            renders.append(rec)
        if not renders:
            raise BuildError(f"{sku}: colourway {cw_id} has no renders in render_log.json")
        if entry.get("mannequin_set"):
            _legacy_mannequin_set_removed(sku)
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
        "hardware_macro": entry.get("hardware_macro") is True,    # opt-in: the zipper / hardware macro accordion (outerwear only)
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


# Standalone product 3D views (presentation "model", a per-colourway .glb copied into media/) were removed on 2026-11-22
# (Stage 1B): the Maximus Motion Studio (/fitting/, #/fitting) is the only interactive 3D / splat stage on the site.
# validate_catalogue.py rejects a colourway "model" key; build_flat_product() refuses one as well.


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
                export_tile(comp, stem.with_name(stem.name + "_tile.webp"), 82, write, bottom_align=False)
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
            raise BuildError(f"{sku}/{cw_id}: standalone 3D models are retired; the Maximus Motion Studio is the only 3D stage")
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
        "hardware_macro": entry.get("hardware_macro") is True,
        "mannequin_default_finish": None,                  # owner cut-outs / set sheets: no mannequin views
        **commercial(entry, site),
        "colourways": colourways,
    }


# ---- canonical product identity (Stage 1B, config/product_identity.schema.json) -------------------------------------
# ONE record per (sku, colourway) drives Page 1 (/: unworn merch) and Page 2 (/fitting/, #/fitting: Maximus Motion Studio).
# products.json keeps the "renders" list the pages already use and gains, per colourway: merch_views, mannequin_views,
# studio_assets, readiness and links. data/identity.json is the strict schema projection of the same objects.
ROUTING = {
    "page1_root": "/",
    "page2_fitting_path": "/fitting/",
    "page2_fitting_hash": "#/fitting",
    "product_hash_template": "#/p/{sku}/{colourway}",
    "fitting_query_template": "/fitting/?sku={sku}&color={colourway}",
    "canonical_resolution_rule": "Both /fitting/?sku=<SKU>&color=<COLOR> (static route reading query string) and #/fitting?sku=<SKU>&color=<COLOR> "
                                 "resolve to Page 2 Maximus Studio with that exact SKU/colourway pre-loaded; Page 1 deep links #/p/<SKU>/<COLOR> "
                                 "open Page 1 quick-view or jump smoothly into /fitting/?sku=<SKU>&color=<COLOR>.",
}
READINESS_STATES = ("certified_live", "c0_only_pending_splat", "pending_dress", "quarantined")
DRESSED_STATES = ("certified_live", "c0_only_pending_splat")     # a certified dressed Maximus GLB exists for the colourway
GLB_MAX_BYTES = 1258291
ANCHOR_KEYS = ("id", "title", "spec", "primary", "cam_preset", "anchor_xyz_m", "anchor_y_frac", "anchor_x_frac", "anchor_z_offset_m", "normal")
INQUIRY_TEMPLATE = "Hello Better Style, I would like to inquire about {title} in {colour}, Size {size}."


def glb_json(path):
    """The JSON chunk of a binary glTF (header 12 bytes, chunk length + type 8 bytes)."""
    b = path.read_bytes()
    if b[:4] != b"glTF":
        raise BuildError(f"{rel(path)} is not a binary glTF")
    n = int.from_bytes(b[12:16], "little")
    return json.loads(b[20:20 + n])


def sog_meta(path):
    import zipfile
    with zipfile.ZipFile(path) as z:
        return json.loads(z.read("meta.json"))


def media_view(r, view=None):
    """A render record as a schema MediaView (Page 1 merch or Page 2 mannequin)."""
    return {"view": view or ("sheet" if r.get("sheet_ground") else r["view"]), "crop": r.get("crop") or "floating", "aspect": 0.72,
            "finish": r.get("finish"), "finish_name": r.get("mannequin_finish") or r.get("finish_name"), "finish_hex": r.get("finish_hex"),
            "background": r.get("background"), "sheet_ground": r.get("sheet_ground"), "tile": r["tile"], "full": r["full"], "cutout": r.get("cutout")}


def maximus_stills(sku, cw_id, block, manifest, write):
    """Stills of Maximus wearing the colourway, captured from the certified dressed GLB (catalogue/tests/capture_maximus.mjs).
    Each sources/<SKU>-MAXIMUS/<file>.png carries a <file>.json record; it is accepted only when that record names this
    sku / colourway / finish / view, the manifest's GLB sha256, and the image's own sha256. Nothing is ever substituted."""
    out = []
    for s in block.get("stills") or []:
        src = STORE / s["file"]
        record = src.with_suffix(".json")
        if not (src.is_file() and record.is_file()):
            raise BuildError(f"{sku}/{cw_id}: Maximus still {s['file']} (or its .json record) is missing")
        rec = load(record)
        want = {"kind": "maximus_studio_still", "sku": sku, "colourway": cw_id, "finish": s["finish"], "view": s["view"], "glb_sha256": manifest["glb_sha256"]}
        bad = {k: rec.get(k) for k, v in want.items() if rec.get(k) != v}
        if bad or rec.get("output_sha256") != sha256(src):
            raise BuildError(f"{sku}/{cw_id}: Maximus still {s['file']} does not match its record or the certified GLB ({bad or 'output sha256'})")
        stem = MEDIA / sku / f"{cw_id}_maximus_{s['finish']}_{s['view']}"
        fin = mannequin_finishes()[s["finish"]]
        out.append({"view": s["view"], "crop": "full", "aspect": 0.72, "finish": s["finish"], "finish_name": fin["name"], "finish_hex": fin["hex"],
                    "background": None, "sheet_ground": None, "cutout": None,
                    "tile": export_tile(src, stem.with_name(stem.name + "_tile.webp"), 88, write),
                    "full": export(src, stem.with_name(stem.name + "_full.webp"), FULL_SIZE, 88, write)})
    return out


def studio_assets(sku, cw_id, block, write):
    """(studio_assets, mannequin_views) for one colourway. No block: pending_dress with every asset null."""
    pending = {"readiness_state": "pending_dress", "glb": None, "sog": None, "morph_pack": None, "av1_loop": None, "anchors": [],
               "theta_fit_url": None, "certified_gates": None}
    if not block:
        return pending, []
    state = block.get("readiness_state")
    if state not in READINESS_STATES:
        raise BuildError(f"{sku}/{cw_id}: studio readiness_state {state!r} is not one of {READINESS_STATES}")
    if state not in DRESSED_STATES:
        return {**pending, "readiness_state": state}, []
    man_path = STORE / block["manifest"]
    if not man_path.is_file():
        raise BuildError(f"{sku}/{cw_id}: studio manifest {block['manifest']} is missing")
    man = load(man_path)
    if man.get("certified") is not True or man.get("sku") != sku:
        raise BuildError(f"{sku}/{cw_id}: {block['manifest']} is not a certified manifest for {sku}")
    glb_path = STORE / man["glb"]
    if not glb_path.is_file() or sha256(glb_path) != man["glb_sha256"]:
        raise BuildError(f"{sku}/{cw_id}: dressed GLB {man['glb']} is missing or does not match the manifest sha256")
    if glb_path.stat().st_size > GLB_MAX_BYTES:
        raise BuildError(f"{sku}/{cw_id}: dressed GLB is {glb_path.stat().st_size} bytes, over the {GLB_MAX_BYTES} byte budget")
    gj = glb_json(glb_path)
    garment = next((m for m in gj.get("meshes", []) if re.search(r"BS_RINGER|tee|shirt|garment", m.get("name", ""), re.I)), None)
    glb = {"url": man["glb"], "bytes": glb_path.stat().st_size, "sha256": man["glb_sha256"],
           "meshopt": "EXT_meshopt_compression" in (gj.get("extensionsUsed") or []),
           "morph_targets": list(((garment or {}).get("extras") or {}).get("targetNames") or [])}
    sog = None
    if man.get("sog") and (STORE / man["sog"]).is_file():
        meta = sog_meta(STORE / man["sog"])
        sog = {"url": man["sog"], "bytes": (STORE / man["sog"]).stat().st_size, "n_splats": int(meta["count"]),
               "sh_degree": 3 if meta.get("shN") else 0, "rot_x_rad": man.get("sog_rot_x_rad", -math.pi / 2),
               "part_id_webp": man.get("garment_id_webp")}
    anchors = [{k: a[k] for k in ANCHOR_KEYS if k in a} | ({"price_label": a["price_label"]} if a.get("price_label") else {})
               for a in (block.get("anchors") or man.get("callouts") or [])]
    gates = man.get("certified_gates")
    return {"readiness_state": state, "glb": glb, "sog": sog, "morph_pack": None,
            "av1_loop": None,           # the Burst 1A swag01 reel is a QA composite (heatmap + debug text), not a shopper loop
            "anchors": anchors, "theta_fit_url": man.get("theta_fit_pose"), "certified_gates": gates,
            "manifest": block["manifest"]}, maximus_stills(sku, cw_id, block, man, write)


def canonicalise(products, cat, write):
    """Add the canonical identity fields to every built product (in place) and return the schema projection."""
    entries = {p["sku"]: p for p in cat["products"]}
    site = cat["site"]
    wa = (site.get("checkout") or {}).get("whatsapp")
    for p in products:
        entry = entries[p["sku"]]
        studio = {k: v for k, v in (entry.get("studio") or {}).items() if not k.startswith("_")}
        unknown = set(studio) - {c["id"] for c in p["colourways"]}
        if unknown:
            raise BuildError(f"{p['sku']}: studio block names colourway(s) {sorted(unknown)} that are not live")
        p["fabric_spec"] = entry.get("fabric_spec")
        p["status"] = entry["status"]
        for cw in p["colourways"]:
            merch = [media_view(r) for r in cw["renders"] if r["presentation"] in ("floating", "flat")]
            if not merch:
                raise BuildError(f"{p['sku']}/{cw['id']}: no unworn merch view (Page 1 needs at least one)")
            assets, maximus = studio_assets(p["sku"], cw["id"], studio.get(cw["id"]), write)
            cw["merch_views"] = merch
            cw["mannequin_views"] = maximus                                   # Maximus only
            cw["studio_mannequin_views"] = [media_view(r) for r in cw["renders"] if r["presentation"] == "mannequin"]   # the older studio mannequin
            cw["studio_assets"] = assets
            cw["readiness"] = "dressed_ready" if assets["readiness_state"] in DRESSED_STATES else "dressing_pending"
            cw["links"] = {"page1_hash": f"#/p/{p['sku']}/{cw['id']}",
                           "page2_fitting_url": f"/fitting/?sku={p['sku']}&color={cw['id']}",
                           "page2_fitting_hash": f"catalogue/#/fitting?sku={p['sku']}&color={cw['id']}"}

    def ident_cw(cw):
        a = {k: v for k, v in cw["studio_assets"].items() if k != "manifest"}
        a["anchors"] = [{k: x[k] for k in ANCHOR_KEYS if k in x} for x in a["anchors"]]
        return {"id": cw["id"], "label": cw["label"], "colour_family": cw["colour_family"], "hex": cw["hex"].upper(),
                "trim_hex": cw["trim_hex"].upper() if cw.get("trim_hex") else None, "chromatic_order": cw["chromatic_order"],
                "stock": cw["stock"], "merch_views": cw["merch_views"], "mannequin_views": cw["mannequin_views"],
                "studio_assets": a, "links": cw["links"]}

    def ident(p):
        approved = [s if isinstance(s, dict) else {"label": s, "stock": None} for s in p["sizes"]] if p.get("sizes") else None
        return {"sku": p["sku"], "title": p["title"], "family_name": p["family"]["name"], "category": p["category"],
                "department": p["department"], "status": p["status"], "featured_rank": int(p["featured_rank"]),
                "fabric_spec": p["fabric_spec"],
                "sizes": {"approved_sizes": approved, "inquiry_sizes": [s for s in (p.get("inquiry_sizes") or ["L", "XL", "XXL"]) if s in ("L", "XL", "XXL")]},
                "pricing_or_inquiry": {"mode": "orderable" if p["sale_status"] == "approved_for_sale" else "inquiry_only",
                                       "sale_status": p["sale_status"], "currency": p["currency"], "price": p["price"], "prices": p["prices"],
                                       "whatsapp_inquiry_template": INQUIRY_TEMPLATE},
                "colourways": [ident_cw(c) for c in p["colourways"]]}

    import datetime
    return {"schema": "betterstyle.canonical_identity/1",
            "generated_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "site": {"brand": site.get("wordmark") or "BETTER STYLE",
                     "currency": {k: v for k, v in (site.get("currency") or {}).items() if k in ("default", "enabled", "formats")},
                     "checkout": {"whatsapp": wa, "email": (site.get("checkout") or {}).get("email")},
                     "mannequin_finishes": [{"id": k, "name": v["name"], "hex": v["hex"]} for k, v in
                                            sorted(mannequin_finishes().items(), key=lambda kv: FINISH_ORDER.index(kv[0]) if kv[0] in FINISH_ORDER else 99)]},
            "routing": ROUTING,
            "products": [ident(p) for p in products]}


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
        identity = canonicalise(products, cat, write)
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
            pl = None if hero["editorial"] else hero.get("plate_source")   # storefront/hero/grade_horse_hero.py output
            if pl:                                                  # a scene plate BEHIND the approved cut-out (.hero-plate .bg / .fg)
                src = STORE / pl
                record = src.with_suffix(".json")
                if not (src.resolve().is_relative_to((STORE / "hero/renders").resolve()) and src.suffix == ".png"):
                    raise BuildError(f"hero plate_source {pl!r} must be a .png under hero/renders/")
                if src.exists() and record.exists():
                    rec = load(record)
                    if rec.get("kind") != "hero_plate" or (rec.get("output") or {}).get("sha256") != sha256(src):
                        raise BuildError(f"hero plate {pl} does not match its record {record.name}: re-run hero/grade_horse_hero.py")
                    if not r.get("cutout"):
                        raise BuildError(f"hero plate needs a cut-out (alpha) render of {hero['sku']}/{hero['colourway']} {hero['view']} to float in front of it")
                    stem = MEDIA / "hero" / src.stem
                    hero.update(plate=export(src, stem.with_name(src.stem + "_2400.webp"), (2400, 2400), 84, write),
                                plate_small=export(src, stem.with_name(src.stem + "_1200.webp"), (1200, 1200), 82, write),
                                plate_record={"file": pl, "sha256": sha256(src), **{k: rec[k] for k in ("what", "params")}})
                else:
                    print(f"WARNING: hero plate {pl} (or its .json record) is missing; using the cut-out on the plain ground")
    except BuildError as e:
        sys.exit(f"BUILD FAILED: {e}")

    site = {**cat["site"], "hero": hero}
    # the finish switcher's labels and swatches (ordered M1, M2, M3); a product offers only the finishes it has renders in
    site["mannequin_finishes"] = [{"id": k, **v} for k, v in sorted(mannequin_finishes().items(), key=lambda kv: FINISH_ORDER.index(kv[0]) if kv[0] in FINISH_ORDER else 99)]
    cur = site.get("currency") or {}
    site["currency"] = {**cur, "formats": currency_formats(cur.get("enabled") or [])}
    identity["site"]["currency"]["formats"] = site["currency"]["formats"]
    out = {
        "schema": "betterstyle.storefront_products/1",
        "routing": ROUTING,
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
        refs += [v[k] for p in products for cw in p["colourways"] for v in cw["mannequin_views"] for k in ("tile", "full")]
        refs += [hero[k] for k in ("image", "image_small", "plate", "plate_small") if hero and hero.get(k)]
        missing = [m for m in refs if not (STORE / m).is_file()]
        if missing:
            sys.exit(f"BUILD FAILED: --data-only needs images that are not built yet (run a full build): {missing[:3]}")
    if write_data:
        MEDIA.mkdir(exist_ok=True)
        if write:
            (MEDIA / "torn-edge.svg").write_text(torn_edge_svg(), encoding="utf-8")
        (STORE / "data/products.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
        (STORE / "data/identity.json").write_text(json.dumps(identity, indent=1, ensure_ascii=False), encoding="utf-8")
    n_tiles = sum(len(p["colourways"]) for p in products)
    print(f"{'checked' if args.check else 'built (data only)' if args.data_only else 'built'}: {len(products)} live product(s), {n_tiles} colourway tile(s); "
          f"held back: {', '.join(p['sku'] for p in held) or 'none'}")


if __name__ == "__main__":
    main()
