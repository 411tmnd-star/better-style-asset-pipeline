# Better Style storefront (catalogue page)

A static, data-driven catalogue page that implements the catalogue reference document (`KKK444/VERBATIM_BY_SITE.html`). Plain HTML, CSS and JS with no framework or build tooling beyond one Python script. It is a downstream consumer of the 3D pipeline:
- it reads `configs/skus`, `configs/archetypes`, `garment_registry` and `assets/renders` **from the project-local snapshot in `engine_inputs/`** (refreshed with `tools/collect_engine_inputs.py`; HUMAN_ENGINE is never read directly);
- it never writes outside this folder.

> Canonical copy (2026-11-11): this folder lives in `better_style_canonical_site/catalogue/`. Paths in this README written as `storefront/...` mean this folder.

## Run
```bash
cd better_style_catalog
python storefront/build.py                         # validate + write data/products.json and media/*.webp
python -m http.server 8765 -d storefront           # then open http://127.0.0.1:8765/
python storefront/build.py --check                 # validate only
```

## Where things come from
| On the page | Source |
|---|---|
| Which products appear | `data/catalogue.json` → `status: "live"` (everything else is held back, listed in `products.json.held_back`) |
| Title, colourways, colour hexes | `configs/skus/<SKU>.json` |
| Category, construction details | `garment_registry/garment_families` via the archetype's `family_id` |
| Images | `assets/renders/<SKU>/render_log.json`, files checked on disk, re-encoded to WebP |
| Price, sizes, measurements, material, care, stock, NEW IN, department | `data/catalogue.json`, **null until established**. Null means hidden or stated as pending, never filled in |

The build fails if a live product or colourway has no render, or if the hero points at a render that does not exist.

## Publishing a garment when its renders are approved
1. In `data/catalogue.json`, set the product's `status` to `"live"`.
2. Add its `colourways` (`id` must match the SKU config; `label` is the display name).
3. Run `python storefront/build.py`.

Commercial fields switch features on by themselves:
- `price` + `sizes` → Add to bag and Quick Add, plus price sorting once every piece has a price.
- `stock: 0` on a colourway → blurred SOLD OUT tile.
- `measurements` / `size_conversion` → Size Guide link and tables.
- `new_in: true` → NEW IN badge.
- `department` → department navigation.
- `looks` → editorial breaks in the grid.

Data shapes:
```json
"sizes": [{"label": "S", "stock": 3}, {"label": "M", "stock": 0}],
"measurements": {"unit": "cm", "rows": {"Chest": {"S": 0, "M": 0}}},
"size_conversion": {"rows": {"EU": {"S": "44", "M": "46"}}},
"mannequin_measurements": {"finish": "Obsidian", "height_cm": 0, "wearing_size": "L"},
"looks": [{"image": "media/...", "alt": "", "quote": "", "items": [{"sku": "", "colourway": ""}]}]
```
Mannequin renders (`mode: "mannequin"` in a render log) are carried through with their finish and crop. Tiles then use the mannequin hover (push-in) instead of the floating motion.

Mannequin finishes (M1 Obsidian Noir, M2 Alabaster Chalk, M3 Travertine Warm; `engine_inputs/configs/mannequin_finishes.json`): each finish's renders live in their own folder, `sources/<SKU>-MANNEQUIN/<finish>/` with its own `render_log.json` (render_sku.py names files without the finish), and each folder is listed in the product's `extra_render_dirs`. A render's finish is its log entry's `finish`, else the SKU config's `render.mannequin.finish`. Media files carry the finish (`<colourway>_mannequin_<finish>_<view>`). Two renders for the same colourway / presentation / finish / view fail the build. `products.json` gives each render `finish`, `finish_hex` and `mannequin_finish` (the name), each product `mannequin_default_finish` (catalogue `mannequin_default_finish`, else the SKU config's finish, else M2, if the product has it), and the site `mannequin_finishes`. The grid stays floating-first (hover: the default finish's mannequin front); the product page opens on the mannequin front and, when a colourway has two or more finishes, shows an M1 / M2 / M3 switcher that keeps the current angle. On the product page each finish has its own CSS stage (`css/store.css`, "One stage per canonical finish"): `js/store.js` writes the render's finish as `data-stage` on the figure, its thumb and `.pdp-media` (not `data-finish`, which marks the switcher pills). M1 stands on light stone, M2 on the gallery dark, M3 on putty; a mannequin view with no known finish keeps the light stone. The figure fades out below the garment from 82% of the image height (hems sit at 77-80% in the torso framing; re-measure if `below_hem_m` changes). The Colab job `02_engine_3d/colab_heavy/run_all.py --jobs mannequin` renders the sets and `apply_results.py` copies and wires them.

## Products without a 3D master (owner product images)
An entry with `"source_kind"` is built from the owner's own cut-out product images in `storefront/sources/<SKU>/`, not from `assets/renders`. There is one such entry so far: `BS-DENIMSHIRT-001`, black and red, from `BETTER_STYLE_VISUAL_CURATION/13_411_ENGINE_LAUNCH_FILM/02_CARD_TEXTURES`.

How they appear:
- Images keep their native pixels. They are centred on the same ground as the renders, in an exact 4:5 frame, and never upscaled.
- Category comes from the entry's `family_id` (GF-019, Shirts).
- Swatch colours are the measured median of each image.
- Each colourway lists its views as `"views": {"front": ..., "back": ...}`.
- The product page says the piece is shown as a cut-out product image.

When a 3D master exists, drop `source_kind` and give the product a SKU config and render log like the ringer tee.

## Editorial hero (Blender)
[hero/build_hero.py](hero/build_hero.py) renders the approved ringer tee behind navy chenille yarn hanging from bleached branches. This is the Veilance F26 reference, `KKK444/selected_frames/10_090` and `10_093`.

The garment is prepared by the pipeline's own `engine/render_sku.py --keep-blend`, unchanged. The script only adds props, light and a camera.

The yarn is about 1,300 strands simulated as hanging ropes:
- forces: gravity, fixed-length segments, pinned ends, and push-out from the garment;
- surface: lumpy pile from a displacement, plus sheen.

The yarn and branches are props, not merchandise. Every render writes a `.json` record (garment master sha256, seed, parameters, timings). `build.py` checks that record before using the image, and falls back to the cut-out render if it is missing. The commands are in the script's header. A final 2400×1350 render takes about 20–30 minutes on the CPU.

## Schema /2: prices, sale approval, checkout contact (`data/catalogue.json`)
`"schema": "betterstyle.storefront_catalogue/2"`. Validate with `python -I tools/validate_catalogue.py` (run from the project root).

| Where | Field | Rule |
|---|---|---|
| `site.currency` | `default`, `enabled` | NGN is the primary currency. Other currencies (e.g. USD) must be defined in `config/currencies.json` and listed in `enabled`. **No exchange rate exists anywhere**: each currency has its own owner-entered price. |
| `site.checkout` | `whatsapp`, `email`, `note` | `whatsapp`: null or 8-15 digits, international format, no `+` (used as `https://wa.me/<digits>`). `email`: null or an address. Null = not configured: the bag shows "Checkout is not connected yet". `note.text` is the wording shown under the order buttons (DRAFT until the owner supplies it). |
| product | `prices` | `{ "NGN": 35000, "USD": 24.99 }`. NGN: whole positive number. Others: at most the currency's decimals. `null` = not set. The old `price` / `currency` keys are gone (the build still emits them, derived, for the page). |
| product | `sale` | `{ "status": "draft" \| "awaiting_approval" \| "approved_for_sale" \| "unavailable", "by", "at" }`. **Only `approved_for_sale` can be ordered.** |
| product | `approval.<field>` | For `prices, sizes, description, material_line, material, care, measurements, size_conversion, mannequin_measurements`: `{ "status": "draft" \| "approved", "source", "by", "at" }`. A value reaches the public `products.json` only when its field is `approved`. An `approved` entry must record `by` and `at`. |

Public output rule (`build.py` `commercial()`): a field not approved is published as `null`. `prices` and `sizes` additionally require `sale.status == "approved_for_sale"`. The page repeats the rule (`sale_status`), so a product cannot be ordered, or show a price, unless both layers agree. The build adds `prices` (approved currencies only), `price`/`currency` (default currency), `sale_status` and `site.currency.formats` to `products.json`.

`python -I catalogue/build.py --data-only` rewrites `products.json` without re-encoding images (preview speed path; the images must already be built). `BS_PREVIEW_PROVISIONAL=1` shows draft values in a local preview and flags the output so it can never be assembled for deployment.

### Inquiry mode (price on request)

A piece that is **not** approved for sale has no price and no gated `sizes` in `products.json`, and cannot be ordered. While `site.checkout.whatsapp` is set, the build also publishes `inquiry_sizes`: the size labels entered on the product (labels only, no stock), in a field of its own so the sale gate above is unchanged. It is `null` when there is no WhatsApp number or the piece is `unavailable`.

On the page (`js/store.js`, "inquiry mode"), a piece with no approved price in any enabled currency and with `inquiry_sizes`:

- shows **Price on Request** wherever a price would be (tile, product sheet, bag row); the formatter never prints `NaN`, a zero or a bare currency sign for a missing amount;
- offers its sizes on the product sheet (the first is selected), with the add button enabled and labelled **Inquire** (it adds the piece to the bag), and a direct **Message us on WhatsApp** link: `https://wa.me/<number>?text=Hello Better Style, I would like to inquire about <title> in <colour>, Size <size>.` (no SKU);
- in the bag, the total row reads **Subtotal TBD** (with priced pieces also in the bag, the subtotal of those only) and the button **Inquire via WhatsApp** opens a message in the same wording: one piece is one sentence, several are a bulleted list, one per line (`• <title> in <colour>, Size <size>`, plus `, Quantity <n>` above one). No SKU, and no total, is sent, and the page says it is an inquiry, not an order.

A piece that is priced, but not in the currently selected currency, is not an inquiry piece: it keeps "Not priced in <currency>". Approving a piece for sale (price, sizes, sale) switches it to the normal order flow with no page change.

## Deploy (Vercel)
The live site is https://better-style-catalogue.vercel.app, project `better-style-catalogue` on team `411`.

1. Run `python storefront/build.py`, then `python storefront/make_dist.py`.
2. Deploy `storefront/dist`. The project link was made from a folder named `better-style-catalogue`, so deploy from a folder with that name:
   ```bash
   cp -r storefront/dist/. <folder named better-style-catalogue>/
   cd <that folder> && npx vercel deploy --prod --yes --scope 411-004b
   ```

`dist/` holds only the page, styles, script, product data and web images. `make_dist.py` strips internal notes, source paths and checksums from the product data.

## Not connected
- **Bag:** kept client-side in localStorage. **Checkout** sends an order *request* by WhatsApp or email when `site.checkout` has a valid number/address; with neither it is disabled ("Checkout is not connected yet"). It is not a payment and never says an order was placed.
- **Newsletter:** posts to `site.newsletter.endpoint` and reports success only on a 2xx response. The endpoint is currently null.
- **Contact:** `site.contact.href` is null.
- **Legal pages:** none written yet.
- **Try-on:** not implemented; there are no assets for it.

## Browser checks
```bash
cd storefront/tests && npm install && npx playwright install chromium
python -m http.server 8765 --bind 127.0.0.1 -d ..      # serve the storefront in another terminal
node run.mjs http://127.0.0.1:8765/ shots               # 45 checks, desktop + mobile; screenshots in shots/
```
`fixture.mjs` tests price, sizes, sold-out, NEW IN, Quick Add and the bag against a copy of the site with a test-only products.json. Make that copy with fake data first; never point it at the real data.
