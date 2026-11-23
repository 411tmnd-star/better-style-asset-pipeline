"""Make a TEST-ONLY copy of the built site (catalogue/dist) with fake commercial data, for the browser tests.

    python -I tools/make_test_fixture.py <variant> <out_dir>

Variants (all values are obviously fake test values; they are never written to catalogue.json or to dist/):
  plain      priced + sale-approved USD catalogue, no checkout channel      (tests/fixture.mjs, the original suite)
  checkout   as plain, with WhatsApp 2340000000000 and orders@example.test  (tests/checkout.mjs)
  wa_only / email_only   one channel only
  badcontact invalid WhatsApp / email values: both channels must stay off
  dual       NGN (default) + USD; TANK has an NGN price only; ringer is also priced in USD
  gate       prices present but sale_status not approved / unavailable / missing: nothing may be orderable
  flutterwave      as dual, plus a Flutterwave TEST public key: Mode B (Pay with Flutterwave) on priced bags
  flw_placeholder  as dual, with the catalogue's placeholder key: Mode B must stay off (WhatsApp ordering only)
  flw_inquiry      real-style unpriced catalogue (inquiry sizes, no sale approval) with a valid TEST key: Mode A only
"""
import copy
import json
import shutil
import sys
from pathlib import Path

CANON = Path(__file__).resolve().parent.parent
DIST = CANON / "catalogue/dist"

SIZES = {"BS-RINGER-001": [{"label": "S", "stock": None}, {"label": "M", "stock": 0}, {"label": "L", "stock": None}]}
DEFAULT_SIZES = [{"label": "S", "stock": None}, {"label": "M", "stock": None}, {"label": "L", "stock": None}]
USD = {"BS-RINGER-001": 120, "BS-DENIMSHIRT-001": 150, "BS-POLO-001": 90, "BS-TANK-001": 60, "BS-SLV-001": 70, "BS-BSTSET-001": 200, "BS-CAMP-001": 110, "BS-CAMP-002": 110}
NGN = {"BS-RINGER-001": 120000, "BS-DENIMSHIRT-001": 150000, "BS-POLO-001": 90000, "BS-TANK-001": 60000, "BS-SLV-001": 70000, "BS-BSTSET-001": 200000, "BS-CAMP-001": 110000, "BS-CAMP-002": 110000}
FLW_TEST_KEY = "FLWPUBK_TEST-0000fixture0000fixture0000fixture-X"     # fake, shaped like a public test key
FLW_PLACEHOLDER = "FLWPUBK_TEST-REPLACE_WITH_LIVE_KEY"
FORMATS = {"NGN": {"locale": "en-NG", "decimals": 0}, "USD": {"locale": "en-US", "decimals": 2}}


def make(variant, out):
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(DIST, out)
    d = json.loads((out / "data/products.json").read_text(encoding="utf-8"))
    site = d["site"]
    flw = variant in ("flutterwave", "flw_placeholder", "flw_inquiry")
    dual = variant in ("dual", "flutterwave", "flw_placeholder", "flw_inquiry")
    site["currency"] = {"default": "NGN" if dual else "USD", "enabled": ["NGN", "USD"] if dual else ["USD"],
                        "formats": FORMATS if dual else {"USD": FORMATS["USD"]}}
    site["page_size"], site["editorial_break_every_rows"] = 2, 1
    site["benefits"] = [{"text": "TEST BENEFIT"}]
    site["contact"] = {"href": "mailto:test@example.test", "label": "Mail"}
    site["newsletter"] = {"endpoint": "/nope"}
    co = {"whatsapp": None, "email": None, "note": {"status": "TEST", "text": "TEST NOTE"}}
    if variant in ("checkout", "wa_only") or dual:
        co["whatsapp"] = "2340000000000"
    if variant in ("checkout", "email_only") or dual:
        co["email"] = "orders@example.test"
    if variant == "badcontact":
        co["whatsapp"], co["email"] = "+234 080 1234", "nobody"
    if flw:
        co["flutterwave"] = {"public_key": FLW_PLACEHOLDER if variant == "flw_placeholder" else FLW_TEST_KEY,
                             "currencies": ["NGN", "USD"], "payment_options": "card, banktransfer, ussd"}
    site["checkout"] = co

    for i, p in enumerate(d["products"]):
        sku = p["sku"]
        p["sizes"] = copy.deepcopy(SIZES.get(sku, DEFAULT_SIZES))
        p["new_in"] = True
        p["measurements"] = {"unit": "cm", "rows": {"Chest": {"S": 50, "M": 52, "L": 54}}}
        p["size_conversion"] = {"rows": {"EU": {"S": "44", "M": "46", "L": "48"}}}
        if dual:
            p["prices"] = {"NGN": NGN[sku]}
            if sku != "BS-TANK-001":
                p["prices"]["USD"] = USD[sku]
            p["price"], p["currency"] = NGN[sku], "NGN"
        else:
            p["prices"] = {"USD": USD[sku]}
            p["price"], p["currency"] = USD[sku], "USD"
        p["sale_status"] = "approved_for_sale"
        if variant == "gate":
            p["sale_status"] = ["not_for_sale", "unavailable", "draft", None][i % 4]
            if p["sale_status"] is None:
                del p["sale_status"]
        if variant == "flw_inquiry":                        # what the live catalogue is today: nothing priced, sizes for inquiry only
            p.update(sizes=None, prices=None, price=None, currency=None, sale_status="not_for_sale", inquiry_sizes=["L", "XL", "XXL"])
    ringer = next(p for p in d["products"] if p["sku"] == "BS-RINGER-001")
    sold = next(c for c in ringer["colourways"] if c["id"] == "red-white")
    sold["stock"], sold["chromatic_order"] = 0, -1        # a sold-out colourway, listed first so it is on the first pages
    if variant == "plain":                                  # the original suite expects exactly three tiles (one product, three colourways)
        d["products"] = [ringer]
    tiles = [(p["sku"], c["id"]) for p in d["products"] for c in p["colourways"]]
    first = d["products"][0]["colourways"][0]["renders"][0]["tile"]
    d["looks"] = [{"image": first, "alt": "TEST LOOK", "quote": "TEST QUOTE", "items": [{"sku": s, "colourway": c} for s, c in tiles[:2]]}]
    d["_test_fixture"] = variant
    (out / "data/products.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return out


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print("fixture written:", make(sys.argv[1], sys.argv[2]))
