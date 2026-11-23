"""Extract useful business information from the old Django shop into a READ-ONLY, provenance-tagged reference dataset.

    python -I tools/extract_old_shop.py             # first extraction; refuses to overwrite existing reference files
    python -I tools/extract_old_shop.py --refresh   # re-extract (clears the read-only flag on the reference files first)

Reads, never writes, the old shop:
  * shop/management/commands/seed_inventory.py is read as TEXT and parsed with `ast` (Django is never imported);
  * db.sqlite3 is opened with `mode=ro&immutable=1`;
  * the 16 seed images are COPIED into reference/old_shop/images/ (small data files; the Django code is not copied).

Writes only under reference/old_shop/ and marks every file read-only. NOTHING here is merged into catalogue.json: prices are
PROVISIONAL, stock is a PLACEHOLDER, and every proposed mapping to a catalogue SKU is unconfirmed until the owner says so.
"""
import argparse
import ast
import csv
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
import time
from decimal import Decimal
from pathlib import Path

CANON = Path(__file__).resolve().parent.parent
SHOP = Path(os.path.normpath(os.path.expanduser('~/Desktop/4041_MechMix/04_app_repos/better-style-shop')))
SEED = SHOP / "shop/management/commands/seed_inventory.py"
DB = SHOP / "db.sqlite3"
SEED_IMAGES = SHOP / "shop/seed_images"
OUT = CANON / "reference/old_shop"

# Owner-confirmation status of every mapping is UNCONFIRMED. "strong" = the description, colour and construction details in
# catalogue.json agree; "weak" = a garment of the same kind exists but a detail is unconfirmed. Weak ones are UNRESOLVED.
MAPPINGS = {
    "bst-shirt": {"sku": "BS-DENIMSHIRT-001", "colourway": "red", "confidence": "strong",
                  "basis": "Description (long-sleeve button shirt, Betterstyle script on the chest pocket) matches catalogue.json BS-DENIMSHIRT-001 details; colour Red = colourway red.",
                  "conflicts": ["Fabric: catalogue.json status_note says inventory page 8 calls the red shirt corduroy, shown as denim per the owner. Owner to confirm the fabric."]},
    "bst-shirt-black": {"sku": "BS-DENIMSHIRT-001", "colourway": "black", "confidence": "strong",
                        "basis": "Description (long-sleeve black button shirt, Betterstyle script on the chest pocket) matches catalogue.json BS-DENIMSHIRT-001; colour Black = colourway black.",
                        "conflicts": ["Two old products (bst-shirt, bst-shirt-black) map to two colourways of ONE catalogue SKU; the old shop priced them separately (both 35000, provisional)."]},
    "bst-polo": {"sku": "BS-POLO-001", "colourway": "black-white", "confidence": "weak",
                 "basis": "Black polo with white collar = black-white colourway.",
                 "conflicts": ["The 'rider illustration at the hem' in the old description is not confirmed on the BS-POLO-001 render."]},
    "bst-69-polo": {"sku": "BS-POLO-001", "colourway": "burgundy-white", "confidence": "weak",
                    "basis": "Red polo with white collar is the nearest colourway (catalogue label 'Red / White', id burgundy-white).",
                    "conflicts": ["The 'large serif 69 on the chest' is not confirmed on the BS-POLO-001 render; red vs burgundy shade unconfirmed."]},
    "bst-singlet": {"sku": "BS-TANK-001", "colourway": "black", "confidence": "weak",
                    "basis": "Black rib-knit singlet vs the black tank.",
                    "conflicts": ["Old description says BST shield on the chest; catalogue.json says BETTERSTYLE bracket logo (status_note).", "Old category Merchandise vs catalogue family GF-031 Tanks."]},
}
CLASS_KEY = {
    "VERIFIED_SOURCE_UNAPPROVED": "Text found verbatim in the old shop. Not approved as product copy.",
    "PROVISIONAL": "Placeholder price (price_is_provisional = 1 in the DB; seed comment: PRICES AND STOCK ARE PLACEHOLDERS). Never a live price.",
    "INFERRED_PLACEHOLDER_RUN": "A single constant size run reused for every garment; not confirmed sizing.",
    "PLACEHOLDER_NOT_INVENTORY": "Stock 5 is the seed constant PLACEHOLDER_STOCK. Not real inventory.",
    "INFERRED_UNCONFIRMED": "Match proposed from descriptions; the owner has not confirmed it.",
    "UNRESOLVED": "Possible match, but a detail disagrees or is unconfirmed; left for the owner.",
}


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


class Names(ast.NodeTransformer):
    def __init__(self, env):
        self.env = env

    def visit_Name(self, node):
        if node.id in self.env:
            return ast.copy_location(ast.parse(repr(self.env[node.id]), mode="eval").body, node)
        raise ValueError(f"unresolved name {node.id!r} at line {node.lineno}")


def parse_seed():
    tree = ast.parse(SEED.read_text(encoding="utf-8"))
    env, items, meta = {}, [], {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ("CLOTHING", "ONE_SIZE", "PLACEHOLDER_STOCK"):
                env[name] = ast.literal_eval(node.value)
                meta[name] = node.lineno
            elif name == "INVENTORY":
                for el in node.value.elts:
                    v = ast.literal_eval(Names(env).visit(el))
                    v["_lines"] = f"{el.lineno}-{el.end_lineno}"
                    items.append(v)
    return items, env, meta


def unlock(path):
    if path.exists():
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    if OUT.exists() and any(OUT.iterdir()):
        if not a.refresh:
            sys.exit(f"{OUT} already exists (read-only reference data). Use --refresh to re-extract deliberately.")
        for p in OUT.rglob("*"):
            if p.is_file():
                unlock(p)
        shutil.rmtree(OUT)
    (OUT / "images").mkdir(parents=True)

    items, env, meta = parse_seed()
    seed_rel = "shop/management/commands/seed_inventory.py"
    uri = f"file:{DB.as_posix()}?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    cats = {r["id"]: dict(r) for r in con.execute("select * from shop_category")}
    prods = [dict(r) for r in con.execute("select * from shop_product order by id")]
    variants = [dict(r) for r in con.execute("select * from shop_productvariant order by id")]
    images = [dict(r) for r in con.execute("select * from shop_productimage order by product_id, position, id")]
    n_users = con.execute("select count(*) from auth_user").fetchone()[0]
    n_orders = con.execute("select count(*) from shop_order").fetchone()[0]
    con.close()

    by_slug = {p["slug"]: p for p in prods}
    seed_slugs = [i["slug"] for i in items]
    records, csv_rows = [], []
    for it in items:
        p = by_slug.get(it["slug"])
        vs = [v for v in variants if p and v["product_id"] == p["id"]]
        ims = [i for i in images if p and i["product_id"] == p["id"]]
        diffs = []
        if not p:
            diffs.append("product missing from DB")
        else:
            cslug = cats[p["category_id"]]["slug"]
            for label, a_, b_ in (("name", it["name"], p["name"]), ("category", it["category"][0], cslug), ("description", it["description"], p["description"]),
                                  ("price", Decimal(it["price"]), Decimal(str(p["price"]))), ("colours", sorted(it["colors"]), sorted({v["color"] for v in vs})),
                                  ("sizes", sorted(it["sizes"]), sorted({v["size"] for v in vs})), ("image alts", [a2 for _, a2 in it["images"]], [i["alt"] for i in ims])):
                if a_ != b_:
                    diffs.append(f"{label}: seed={a_!r} db={b_!r}")
            if not p["price_is_provisional"]:
                diffs.append("DB price_is_provisional = 0 (owner may have confirmed it)")
        rec_images = []
        for fn, alt in it["images"]:
            src = SEED_IMAGES / fn
            dst = OUT / "images" / fn
            shutil.copy2(src, dst)
            rec_images.append({"file": f"images/{fn}", "alt": alt, "sha256": sha(dst), "bytes": dst.stat().st_size,
                               "original_location": f"shop/seed_images/{fn}"})
        m = MAPPINGS.get(it["slug"])
        rec = {
            "old_product_id": p["id"] if p else None,
            "old_slug": it["slug"], "name": it["name"],
            "category": {"slug": it["category"][0], "name": it["category"][1]},
            "description": {"value": it["description"], "class": "VERIFIED_SOURCE_UNAPPROVED", "source": f"{seed_rel}:{it['_lines']}; db shop_product.description"},
            "price_ngn": {"value": int(Decimal(it["price"])), "currency": "NGN", "class": "PROVISIONAL",
                          "source": f"{seed_rel}:{it['_lines']}; db shop_product.price, price_is_provisional={int(bool(p['price_is_provisional'])) if p else 'n/a'}"},
            "colours": {"value": it["colors"], "class": "VERIFIED_SOURCE_UNAPPROVED", "source": f"{seed_rel}:{it['_lines']}"},
            "sizes": {"value": it["sizes"], "class": "INFERRED_PLACEHOLDER_RUN", "source": f"{seed_rel}:{meta.get('CLOTHING')} (CLOTHING/ONE_SIZE constants)"},
            "variants": [{"old_variant_id": v["id"], "size": v["size"], "colour": v["color"], "old_sku_string": v["sku"],
                          "placeholder_stock": {"value": v["stock"], "class": "PLACEHOLDER_NOT_INVENTORY"}} for v in vs],
            "images": rec_images,
            "db_matches_seed": not diffs, "db_differences": diffs,
            "proposed_match": None if not m else {**m, "class": "INFERRED_UNCONFIRMED" if m["confidence"] == "strong" else "UNRESOLVED", "owner_confirmed": False},
            "verification_status": "UNVERIFIED_BY_OWNER",
        }
        records.append(rec)
        for v in rec["variants"]:
            csv_rows.append([rec["old_product_id"], rec["old_slug"], rec["name"], rec["category"]["name"], rec["price_ngn"]["value"], "PROVISIONAL",
                             v["old_variant_id"], v["colour"], v["size"], v["placeholder_stock"]["value"], "PLACEHOLDER", rec["proposed_match"]["sku"] if rec["proposed_match"] else "",
                             rec["proposed_match"]["class"] if rec["proposed_match"] else "", rec["description"]["value"]])

    out = {
        "schema": "betterstyle.old_shop_extract/1",
        "READ_ONLY": "Reference data. Do not edit; re-extract with tools/extract_old_shop.py --refresh. Nothing here is approved for sale.",
        "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "sources": {
            "seed": {"path": str(SEED), "sha256": sha(SEED), "bytes": SEED.stat().st_size, "opened": "read as text, parsed with ast (Django not imported)"},
            "db": {"path": str(DB), "sha256": sha(DB), "bytes": DB.stat().st_size, "opened": "sqlite mode=ro&immutable=1"},
            "images": {"path": str(SEED_IMAGES), "count": len(list((OUT / 'images').iterdir())), "copied_to": "reference/old_shop/images/"},
        },
        "counts": {"products": len(records), "variants": len(variants), "images": len(images), "categories": len(cats), "users": n_users, "orders": n_orders},
        "classification_key": CLASS_KEY,
        "records": records,
        "db_products_not_in_seed": [p["slug"] for p in prods if p["slug"] not in seed_slugs],
        "not_extracted": ["variant stock as inventory (placeholder constant 5)", "orders (0 rows)", "users (0 rows)", "shipping fee placeholders (settings.py)", "Django models/views/templates/cart/checkout/payment code"],
    }
    (OUT / "old_shop_extract.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
    with open(OUT / "old_shop_variants.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["old_product_id", "old_slug", "name", "category", "price_ngn", "price_class", "old_variant_id", "colour", "size", "stock", "stock_class", "proposed_sku", "mapping_class", "description"])
        w.writerows(csv_rows)

    md = ["# Old-shop to catalogue mapping (reference only)", "",
          "Generated by `tools/extract_old_shop.py` from the old Django shop (read-only). **No value below is approved for sale and none was written to `catalogue.json`.** Every mapping is UNCONFIRMED; the owner decides.", "",
          "| Old product (id) | Provisional NGN | Proposed catalogue SKU / colourway | Confidence | Status |", "|---|---|---|---|---|"]
    for r in records:
        pm = r["proposed_match"]
        md.append(f"| `{r['old_slug']}` ({r['old_product_id']}) {r['name']} | {r['price_ngn']['value']:,} | " + (f"`{pm['sku']}` / `{pm['colourway']}`" if pm else "none: no render in the catalogue (candidate product)") +
                  f" | {pm['confidence'] if pm else '-'} | {pm['class'] if pm else 'NO_MATCH'} |")
    md += ["", "## Conflicts and open questions for the owner"]
    for r in records:
        if r["proposed_match"]:
            for c in r["proposed_match"]["conflicts"]:
                md.append(f"- `{r['old_slug']}` -> `{r['proposed_match']['sku']}`: {c}")
    md += ["- Denim vs corduroy for the red shirt (`catalogue.json` status_note of BS-DENIMSHIRT-001).",
           "- Singlet category Merchandise (old shop) vs GF-031 Tanks (catalogue).",
           "- Two old products map onto two colourways of one SKU; per-colourway pricing does not exist in the catalogue (owner decision D8).",
           f"- Unmatched old products ({sum(1 for r in records if not r['proposed_match'])}): " + ", ".join(f"`{r['old_slug']}`" for r in records if not r["proposed_match"]) + ". They have no renders; they stay reference-only until the owner decides which to photograph or render.",
           "", "## How values may enter the catalogue", "Only through Studio, one field at a time: \"Use this value\" copies it as a **draft** with `source: old_shop:<slug>`; it becomes publishable only when the owner ticks Approved for sale. Provisional prices and placeholder stock are never adopted automatically."]
    (OUT / "MAPPING.md").write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")

    files = sorted(p for p in OUT.rglob("*") if p.is_file())
    man = {"files": {p.relative_to(OUT).as_posix(): {"sha256": sha(p), "bytes": p.stat().st_size} for p in files if p.name != "MANIFEST.json"}}
    (OUT / "MANIFEST.json").write_text(json.dumps(man, indent=1), encoding="utf-8", newline="\n")
    for p in OUT.rglob("*"):
        if p.is_file():
            os.chmod(p, stat.S_IREAD)                     # read-only flag: protection against accidental edits
    print(f"extracted {len(records)} products, {len(variants)} variants, {len(images)} images; {sum(1 for r in records if r['proposed_match'])} proposed mappings; "
          f"db differs from seed for {sum(1 for r in records if not r['db_matches_seed'])} product(s); users={n_users} orders={n_orders}")


if __name__ == "__main__":
    main()
