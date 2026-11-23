"""Validate catalogue/data/catalogue.json (schema betterstyle.storefront_catalogue/2).

    python -I tools/validate_catalogue.py [path-to-catalogue.json]      # exit 1 on errors

Library use:  validate(cat, store) -> (errors, warnings);  readiness(cat) -> {sku: [what is missing before it can be ordered]}

Rules (see catalogue/README.md "Schema /2"):
  * Prices are per currency, in a currency that is enabled for the site. No exchange rate exists anywhere.
  * Every commercial/descriptive field has an approval entry. Only "approved" values can reach the public site (catalogue/build.py).
  * A product is purchasable only when sale.status == "approved_for_sale" AND the data it needs is present, valid and approved.
  * Checkout contact details are null (unconfigured) or syntactically valid. Nothing is ever invented.
  * Mannequin imagery (a non-empty mannequin_set, a mannequin render file, or a .glb/.gltf 3D model reference) is an error
    unless config/deployment.json owner_decisions.mannequin_imagery_approved is a dict with non-empty "by" and "at".
  * No standalone product 3D: a colourway "model" key is an error. The Maximus Motion Studio (/fitting/) is the only 3D
    stage; a product's "studio" block lists the colourways that have a certified dressed Maximus GLB (dressed_ready).
  * site.lookbook_plates / site.styled_looks link only to live SKUs and colourways; plate images exist under landing/.
  * A built catalogue/data/identity.json must conform to config/product_identity.schema.json.
  * hardware_macro (optional, boolean) opts a product into the zipper "Hardware & Macro Craft" panel; absent means no panel.
"""
import json
import re
import sys
from pathlib import Path

SCHEMA = "betterstyle.storefront_catalogue/2"
STATUSES = {"live", "draft", "in_production", "held"}
SALE_STATES = ("draft", "awaiting_approval", "approved_for_sale", "unavailable")
APPROVAL_STATES = {"draft", "approved"}
GATED = ("prices", "sizes", "description", "material_line", "material", "care",
         "measurements", "size_conversion", "mannequin_measurements")
SKU_RE = re.compile(r"^BS-[A-Z0-9]+-\d{3}$")
WA_RE = re.compile(r"^\d{8,15}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CANON = Path(__file__).resolve().parent.parent
MODEL_EXTS = (".glb", ".gltf")
FILE_REF_RE = re.compile(r"^\S+\.[A-Za-z0-9]{2,5}$")   # a path-like value: no spaces, ends in a file extension


def load_owner_decisions(path=None):
    p = Path(path) if path else CANON / "config/deployment.json"
    return json.loads(p.read_text(encoding="utf-8")).get("owner_decisions") or {}


def mannequin_imagery_approved(owner_decisions):
    """True only when owner_decisions.mannequin_imagery_approved is a dict with non-empty 'by' and 'at'."""
    a = (owner_decisions or {}).get("mannequin_imagery_approved")
    return isinstance(a, dict) and bool(str(a.get("by") or "").strip()) and bool(str(a.get("at") or "").strip())


def _nonempty(v):
    return v not in (None, "", [], {}, False)


def _string_values(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _string_values(v, f"{path}.{k}" if path else str(k))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _string_values(v, f"{path}[{i}]")
    elif isinstance(o, str):
        yield path, o


def mannequin_gate_problems(product):
    """[(field, reason)] for a non-empty mannequin_set, a 3D model reference, or a mannequin render file."""
    out = []
    if _nonempty(product.get("mannequin_set")):
        out.append(("mannequin_set", "is non-empty"))
    for field, s in _string_values(product):
        if s.lower().endswith(MODEL_EXTS):
            out.append((field, f"references a 3D model file ({s})"))
        elif FILE_REF_RE.match(s) and "mannequin" in s.lower():
            out.append((field, f"names a mannequin render ({s})"))
    return out


def load_currencies(path=None):
    p = Path(path) if path else CANON / "config/currencies.json"
    return json.loads(p.read_text(encoding="utf-8"))["currencies"]


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def price_error(code, v, currencies):
    """None if v is an acceptable price for currency `code`, else a message."""
    if v is None:
        return None
    if not _is_num(v) or v <= 0:
        return f"{code} price must be a positive number or null (got {v!r})"
    dec = currencies[code]["decimals"]
    if dec == 0 and (isinstance(v, float) and not v.is_integer()):
        return f"{code} price must be a whole number (got {v!r})"
    if dec and round(v, dec) != v:
        return f"{code} price may have at most {dec} decimals (got {v!r})"
    return None


def size_list(sizes):
    return [{"label": s, "stock": None} if isinstance(s, str) else s for s in (sizes or [])]


def field_approved(entry, field):
    return ((entry.get("approval") or {}).get(field) or {}).get("status") == "approved"


def readiness(cat):
    """Per live product: what is still missing before it can be ordered on the public site. [] means orderable."""
    site = cat.get("site", {})
    cur = (site.get("currency") or {}).get("default")
    out = {}
    for p in cat.get("products", []):
        if p.get("status") != "live":
            continue
        miss = []
        price = (p.get("prices") or {}).get(cur) if cur else None
        if price is None:
            miss.append(f"{cur or 'default-currency'} price")
        elif not field_approved(p, "prices"):
            miss.append(f"{cur} price approval")
        sizes = size_list(p.get("sizes"))
        if not sizes:
            miss.append("sizes")
        elif not field_approved(p, "sizes"):
            miss.append("sizes approval")
        st = (p.get("sale") or {}).get("status", "draft")
        if st != "approved_for_sale":
            miss.append(f"sale approval (currently {st})")
        out[p["sku"]] = miss
    return out


READINESS_STATES = ("certified_live", "c0_only_pending_splat", "pending_dress", "quarantined")
DRESSED_STATES = ("certified_live", "c0_only_pending_splat")
PLATE_TYPES = ("editorial_photo", "maximus_editorial")
CROP_RE = re.compile(r"^\d{1,3}% \d{1,3}%$")
CAM_PRESETS = ("monolith", "drape34", "macro", "back")
WOOL_RE = re.compile(r"wool|knit", re.I)


def _validate_studio(p, where, cws, store, E):
    raw = p.get("studio")
    if raw is None:
        return
    if not isinstance(raw, dict):
        E(f"{where}: studio must be an object keyed by colourway id")
        return
    studio = {k: v for k, v in raw.items() if not k.startswith("_")}
    cotton = "cotton" in str(p.get("fabric_spec") or "").lower()
    for cid, b in studio.items():
        w = f"{where}/studio.{cid}"
        if cid not in cws:
            E(f"{w}: colourway {cid!r} is not in this product")
            continue
        if not isinstance(b, dict):
            E(f"{w}: must be an object")
            continue
        st = b.get("readiness_state")
        if st not in READINESS_STATES:
            E(f"{w}: readiness_state must be one of {READINESS_STATES} (got {st!r})")
            continue
        if st in DRESSED_STATES:
            man = b.get("manifest")
            if not (isinstance(man, str) and man.startswith("media/maximus_motion/") and man.endswith(".json")):
                E(f"{w}: a dressed colourway needs manifest: media/maximus_motion/<name>.json")
            elif not (store / man).is_file():
                E(f"{w}: manifest not found: {man}")
            else:
                m = json.loads((store / man).read_text(encoding="utf-8"))
                if m.get("certified") is not True or m.get("sku") != p.get("sku"):
                    E(f"{w}: {man} is not a certified manifest for {p.get('sku')}")
                elif not (store / str(m.get("glb"))).is_file():
                    E(f"{w}: the manifest's dressed GLB {m.get('glb')} is missing")
        for s in b.get("stills") or []:
            s = s or {}
            f = s.get("file")
            if s.get("finish") not in ("M1", "M2", "M3") or s.get("view") not in ("front", "three_quarter", "back"):
                E(f"{w}: every still needs finish M1|M2|M3 and view front|three_quarter|back")
            if not (isinstance(f, str) and f.startswith(f"sources/{p.get('sku')}-MAXIMUS/") and (store / f).is_file()
                    and (store / f).with_suffix(".json").is_file()):
                E(f"{w}: still {f!r} must be an existing file under sources/{p.get('sku')}-MAXIMUS/ with its .json record")
        for a in b.get("anchors") or []:
            a = a or {}
            missing = [k for k in ("id", "title", "spec", "primary", "cam_preset", "normal") if k not in a]
            if missing:
                E(f"{w}: anchor {a.get('id')!r} is missing {missing}")
            elif a["cam_preset"] not in CAM_PRESETS:
                E(f"{w}: anchor {a['id']!r} cam_preset must be one of {CAM_PRESETS}")
            if cotton and WOOL_RE.search(f"{a.get('title', '')} {a.get('spec', '')}"):
                E(f"{w}: anchor {a.get('id')!r} says wool/knit but the garment is {p.get('fabric_spec')}")


def _validate_page1(site, live, store, E):
    """lookbook_plates (the stationary deck on /) and styled_looks (the "Styled on Maximus" pairing module)."""
    landing = Path(store).parent / "landing"

    def linked(w, sku, cw):
        if sku not in live:
            E(f"{w}: linked SKU {sku!r} is not a live product")
            return False
        if cw not in (live[sku].get("colourways") or {}):
            E(f"{w}: colourway {cw!r} is not in {sku}")
            return False
        return True

    plates = site.get("lookbook_plates")
    if plates is not None:
        if not isinstance(plates, list) or not plates:
            E("site.lookbook_plates must be a non-empty list")
            plates = []
        ids = [x.get("id") for x in plates if isinstance(x, dict)]
        if len(ids) != len(set(ids)):
            E("site.lookbook_plates ids must be unique")
        for i, pl in enumerate(plates):
            w = f"site.lookbook_plates[{i}] {pl.get('id')}"
            if pl.get("plate_type") not in PLATE_TYPES:
                E(f"{w}: plate_type must be one of {PLATE_TYPES}")
            for k in ("desktop_crop", "mobile_crop"):
                if not CROP_RE.match(str(pl.get(k) or "")):
                    E(f"{w}: {k} must be an object-position like '50% 40%'")
            if not str(pl.get("alt") or "").strip():
                E(f"{w}: alt text is required")
            for f in [pl.get("image")] + list((pl.get("srcset") or {}).values()):
                if not (isinstance(f, str) and (landing / f).is_file()):
                    E(f"{w}: image {f!r} not found under landing/")
            ok = linked(w, pl.get("linked_sku"), pl.get("linked_color"))
            if ok and pl.get("plate_type") == "maximus_editorial":
                b = (live[pl["linked_sku"]].get("studio") or {}).get(pl["linked_color"]) or {}
                if b.get("readiness_state") not in DRESSED_STATES:
                    E(f"{w}: a maximus_editorial plate needs a dressed colourway (studio readiness certified_live or c0_only_pending_splat)")
    looks = site.get("styled_looks")
    if looks is not None:
        if not isinstance(looks, list):
            E("site.styled_looks must be a list")
            looks = []
        for i, lk in enumerate(looks):
            w = f"site.styled_looks[{i}] {lk.get('id')}"
            items = lk.get("items") or []
            if not items:
                E(f"{w}: needs at least one item")
            for it in items:
                linked(w, (it or {}).get("linked_sku"), (it or {}).get("linked_color"))


def validate_identity(identity, schema_path=None):
    """Schema errors for a built data/identity.json against config/product_identity.schema.json ([] when it conforms).
    Needs the jsonschema package; without it, returns a single message saying the check could not run."""
    try:
        import jsonschema
    except ImportError:
        return ["identity: the jsonschema package is not installed, so data/identity.json could not be checked"]
    schema = json.loads(Path(schema_path or CANON / "config/product_identity.schema.json").read_text(encoding="utf-8"))
    v = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    return [f"identity {'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in v.iter_errors(identity)]


def validate(cat, store, currencies=None, owner_decisions=None):
    errors, warnings = [], []
    E, W = errors.append, warnings.append
    store = Path(store)
    currencies = currencies or load_currencies()
    if owner_decisions is None:
        owner_decisions = load_owner_decisions()

    if cat.get("schema") != SCHEMA:
        E(f"schema must be {SCHEMA!r} (got {cat.get('schema')!r})")
    site = cat.get("site")
    if not isinstance(site, dict):
        E("site block is missing")
        return errors, warnings

    cur = site.get("currency") or {}
    default, enabled = cur.get("default"), cur.get("enabled")
    if not isinstance(enabled, list) or not enabled:
        E("site.currency.enabled must be a non-empty list")
        enabled = []
    for c in enabled:
        if c not in currencies:
            E(f"site.currency.enabled contains {c!r}, which is not defined in config/currencies.json")
    if default not in enabled:
        E(f"site.currency.default {default!r} must be one of site.currency.enabled {enabled}")

    co = site.get("checkout")
    if not isinstance(co, dict):
        E("site.checkout block is missing")
        co = {}
    wa, em = co.get("whatsapp"), co.get("email")
    if wa is not None and not (isinstance(wa, str) and WA_RE.match(wa)):
        E("site.checkout.whatsapp must be null or 8-15 digits in international format without '+' or spaces")
    if em is not None and not (isinstance(em, str) and EMAIL_RE.match(em)):
        E("site.checkout.email must be null or a valid email address")
    if wa is None and em is None:
        W("site.checkout has no WhatsApp number or email: the bag shows 'Checkout is not connected yet' (owner to supply)")
    flw = co.get("flutterwave")
    if flw is not None:   # Mode B (Flutterwave Inline v3): public key only, never a secret key
        if not isinstance(flw, dict):
            E("site.checkout.flutterwave must be an object or absent")
        else:
            key = flw.get("public_key")
            if not (isinstance(key, str) and re.fullmatch(r"FLWPUBK(_TEST)?-[A-Za-z0-9_-]+(-X)?", key)):
                E("site.checkout.flutterwave.public_key must be a Flutterwave PUBLIC key (FLWPUBK-... or FLWPUBK_TEST-...)")
            if isinstance(key, str) and re.match(r"FLWSECK", key):
                E("site.checkout.flutterwave.public_key holds a SECRET key: remove it from the catalogue immediately")
            fc = flw.get("currencies")
            if not (isinstance(fc, list) and fc and all(c in currencies for c in fc)):
                E("site.checkout.flutterwave.currencies must be a non-empty list of codes defined in config/currencies.json")
            if not (isinstance(flw.get("payment_options"), str) and flw["payment_options"].strip()):
                E("site.checkout.flutterwave.payment_options must be a non-empty string, e.g. 'card, banktransfer, ussd'")
            if isinstance(key, str) and ("REPLACE" in key or key.startswith("FLWPUBK_TEST-")):
                W("site.checkout.flutterwave.public_key is a test / placeholder key: replace it with the live public key before any piece is priced")

    contact = (site.get("contact") or {}).get("href")
    if contact is not None and not re.match(r"^(https://|mailto:|tel:)", str(contact)):
        E("site.contact.href must be null or start with https://, mailto: or tel:")

    ep = (site.get("newsletter") or {}).get("endpoint")
    if ep is not None and not str(ep).startswith("https://"):
        E("site.newsletter.endpoint must be null or an https:// address")
    for col in site.get("footer") or []:
        for l in col.get("links", []):
            if l.get("href") is not None and not re.match(r"^(#/|https://|mailto:|tel:)", str(l["href"])):
                E(f"footer link {l.get('label')!r}: href must be null, #/..., https://, mailto: or tel:")

    seen = set()
    live = {}
    for i, p in enumerate(cat.get("products", [])):
        sku = p.get("sku")
        where = f"products[{i}] {sku}"
        if not isinstance(sku, str) or not sku:
            E(f"products[{i}]: sku missing")
            continue
        if sku in seen:
            E(f"{where}: duplicate SKU")
        seen.add(sku)
        if not SKU_RE.match(sku):
            W(f"{where}: SKU does not match the pattern BS-<CODE>-NNN (existing id kept as is)")
        # Owner rule 1: no mannequin imagery or 3D model reaches publication without the owner's recorded approval.
        if not mannequin_imagery_approved(owner_decisions):
            for field, reason in mannequin_gate_problems(p):
                E(f"{where}: field {field} {reason}; mannequin imagery and 3D models need "
                  f"config/deployment.json owner_decisions.mannequin_imagery_approved with 'by' and 'at'")
        if p.get("status") not in STATUSES:
            E(f"{where}: status must be one of {sorted(STATUSES)} (got {p.get('status')!r})")
            continue
        if p["status"] != "live":
            continue
        live[sku] = p
        if "hardware_macro" in p and not isinstance(p["hardware_macro"], bool):
            E(f"{where}: hardware_macro must be true or false (it opts a zipper / hardware piece into the Hardware & Macro Craft panel)")

        # prices
        prices = p.get("prices")
        if not isinstance(prices, dict):
            E(f"{where}: 'prices' must be an object of currency -> number|null")
            prices = {}
        for code, v in prices.items():
            if code not in enabled:
                E(f"{where}: price given for {code}, which is not an enabled site currency {enabled}")
            elif (m := price_error(code, v, currencies)):
                E(f"{where}: {m}")
        if "price" in p or "currency" in p:
            E(f"{where}: legacy 'price'/'currency' keys are not allowed in schema /2 (use 'prices')")

        # sizes
        sizes = p.get("sizes")
        if sizes is not None:
            if not isinstance(sizes, list):
                E(f"{where}: sizes must be a list or null")
            else:
                labels = []
                for s in size_list(sizes):
                    lab = s.get("label") if isinstance(s, dict) else None
                    if not isinstance(lab, str) or not lab.strip():
                        E(f"{where}: every size needs a non-empty label")
                        continue
                    labels.append(lab)
                    st = s.get("stock")
                    if st is not None and not (isinstance(st, int) and not isinstance(st, bool) and st >= 0):
                        E(f"{where}: size {lab} stock must be an integer >= 0 or null")
                if len(labels) != len(set(labels)):
                    E(f"{where}: size labels must be unique")

        # colourways
        cws = p.get("colourways")
        if not isinstance(cws, dict) or not cws:
            E(f"{where}: colourways missing")
            cws = {}
        for cid, cw in cws.items():
            st = cw.get("stock")
            if st is not None and not (isinstance(st, int) and not isinstance(st, bool) and st >= 0):
                E(f"{where}/{cid}: stock must be an integer >= 0 or null")
            if p.get("source_kind"):
                views = cw.get("views") or {}
                if not views:
                    E(f"{where}/{cid}: flat product colourway has no views")
                for v, relp in views.items():
                    if not (store / relp).is_file():
                        E(f"{where}/{cid}: view '{v}' file not found: {relp}")
            if "model" in cw:          # retired 2026-11-22 (Stage 1B): no standalone product 3D viewer anywhere on the site
                E(f"{where}/{cid}: standalone 3D 'model' keys are retired; the Maximus Motion Studio (/fitting/) is the only 3D stage")

        # Maximus Motion Studio block (Page 2): only these colourways may be dressed_ready
        _validate_studio(p, where, cws, store, E)

        # approval + sale
        appr = p.get("approval")
        if not isinstance(appr, dict):
            E(f"{where}: 'approval' block missing")
            appr = {}
        for f in GATED:
            a = appr.get(f)
            if a is None:
                E(f"{where}: approval.{f} missing")
                continue
            if a.get("status") not in APPROVAL_STATES:
                E(f"{where}: approval.{f}.status must be draft|approved")
                continue
            if a["status"] == "approved":
                if not a.get("by") or not a.get("at"):
                    E(f"{where}: approval.{f} is 'approved' but has no 'by'/'at' (approvals must be recorded)")
                val = p.get(f)
                if f == "prices":
                    if not any(v is not None for v in (val or {}).values()):
                        E(f"{where}: prices approved but every price is null")
                elif val in (None, [], {}):
                    W(f"{where}: {f} is approved but empty")
            if a.get("source") and str(a["source"]).startswith("old_shop:") and a["status"] == "approved" and not a.get("by"):
                E(f"{where}: {f} came from the old shop and is approved without an owner approval record")

        sale = p.get("sale")
        if not isinstance(sale, dict):
            E(f"{where}: 'sale' block missing")
            sale = {"status": "draft"}
        sst = sale.get("status")
        if sst not in SALE_STATES:
            E(f"{where}: sale.status must be one of {SALE_STATES} (got {sst!r})")
        if sst == "approved_for_sale":
            if not sale.get("by") or not sale.get("at"):
                E(f"{where}: sale approved but has no 'by'/'at' record")
            p_def = prices.get(default)
            if p_def is None or not field_approved(p, "prices"):
                E(f"{where}: approved_for_sale requires an approved {default} price")
            if not size_list(sizes) or not field_approved(p, "sizes"):
                E(f"{where}: approved_for_sale requires approved sizes")
            if p.get("status") != "live":
                E(f"{where}: approved_for_sale requires status 'live'")

    # hero
    hero = site.get("hero")
    if hero:
        hp = live.get(hero.get("sku"))
        if not hp:
            E(f"site.hero.sku {hero.get('sku')!r} is not a live product")
        elif hero.get("colourway") not in (hp.get("colourways") or {}):
            E(f"site.hero.colourway {hero.get('colourway')!r} not found in {hero['sku']}")
        if hero.get("theme") not in (None, "light", "dark"):
            E(f"site.hero.theme {hero.get('theme')!r} must be 'light' or 'dark'")
        ps = hero.get("plate_source")
        if ps is not None and not re.fullmatch(r"hero/renders/[A-Za-z0-9._-]+\.png", str(ps)):
            E(f"site.hero.plate_source {ps!r} must be a .png file directly under hero/renders/")
        if ps and hero.get("editorial_source"):
            W("site.hero sets both editorial_source and plate_source: the editorial render wins and the plate is not shown")

    _validate_page1(site, live, store, E)

    for sku, miss in readiness(cat).items():
        if miss:
            W(f"{sku}: not orderable yet, missing: {', '.join(miss)}")
    return errors, warnings


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else CANON / "catalogue/data/catalogue.json"
    cat = json.loads(path.read_text(encoding="utf-8"))
    store = path.parent.parent
    errors, warnings = validate(cat, store)
    ident = store / "data/identity.json"
    if ident.is_file():                       # the built canonical projection (catalogue/build.py), when present
        errors += validate_identity(json.loads(ident.read_text(encoding="utf-8")))
    for w in warnings:
        print("WARNING:", w)
    for e in errors:
        print("ERROR:  ", e)
    print(f"validate_catalogue: {len(errors)} error(s), {len(warnings)} warning(s)")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
