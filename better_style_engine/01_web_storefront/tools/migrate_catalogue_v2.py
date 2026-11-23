"""One-time, recorded migration of catalogue/data/catalogue.json from schema /1 to /2.

Purely mechanical and text-preserving (the file's hand-formatted layout and comments-in-values are kept, so the git diff
is readable). It invents NOTHING: every value it adds is null or "draft".

  schema                        -> betterstyle.storefront_catalogue/2
  site.currency                 -> {"default": "NGN", "enabled": ["NGN"]}          (NGN is the primary currency)
  site.checkout                 -> whatsapp null, email null, note = DRAFT wording describing what the button does
  each live product:  "price": null, "currency": null  ->  "prices": {"NGN": null}
                      + "sale":     {"status": "draft", ...}                       (product-level sale approval)
                      + "approval": one draft entry per gated field                (field-level approval)

    python -I tools/migrate_catalogue_v2.py            # applies; refuses if price/currency are not both null
"""
import json
import sys
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "catalogue/data/catalogue.json"
GATED = ("prices", "sizes", "description", "material_line", "material", "care",
         "measurements", "size_conversion", "mannequin_measurements")

OLD_SCHEMA = '"schema": "betterstyle.storefront_catalogue/1"'
NEW_SCHEMA = '"schema": "betterstyle.storefront_catalogue/2"'
PRICE_PAIR = '      "price": null,\n      "currency": null,\n'
SITE_ANCHOR = '    "editorial_break_every_rows": 2,\n'

SITE_ADD = (
    '    "currency": { "default": "NGN", "enabled": ["NGN"] },\n'
    '    "checkout": {\n'
    '      "whatsapp": null,\n'
    '      "email": null,\n'
    '      "note": {\n'
    '        "status": "DRAFT, awaiting owner wording",\n'
    '        "text": "This opens a message to Better Style with your bag. It is an order request, not a payment, and it is not confirmed until Better Style replies."\n'
    '      }\n'
    '    },\n'
)


def product_add():
    ap = ",\n".join(f'        "{f}": {" " * (24 - len(f))}{{ "status": "draft", "source": null, "by": null, "at": null }}' for f in GATED)
    return ('      "prices": { "NGN": null },\n'
            '      "sale": { "status": "draft", "by": null, "at": null },\n'
            '      "approval": {\n' + ap + '\n      },\n')


def main():
    t = PATH.read_text(encoding="utf-8")
    if NEW_SCHEMA in t:
        print("already schema /2; nothing to do")
        return
    cat = json.loads(t)
    live = [p for p in cat["products"] if p["status"] == "live"]
    for p in live:
        if p.get("price") is not None or p.get("currency") is not None:
            sys.exit(f"{p['sku']}: price/currency are not null; refusing to drop them silently")
    assert t.count(OLD_SCHEMA) == 1, "schema line not found exactly once"
    assert t.count(PRICE_PAIR) == len(live), f"expected {len(live)} price/currency pairs, found {t.count(PRICE_PAIR)}"
    assert t.count(SITE_ANCHOR) == 1, "site anchor not found exactly once"
    t = t.replace(OLD_SCHEMA, NEW_SCHEMA).replace(PRICE_PAIR, product_add()).replace(SITE_ANCHOR, SITE_ANCHOR + SITE_ADD)
    json.loads(t)   # must still be valid JSON
    PATH.write_text(t, encoding="utf-8")
    print(f"migrated {len(live)} live products to schema /2")


if __name__ == "__main__":
    main()
