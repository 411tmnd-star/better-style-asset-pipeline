# Better Style storefront: catalogue build and publishing tools

The Python side of a static fashion storefront: building the catalogue, validating it and publishing it safely. The front-end itself is not in this repository.

## Live Production Output -> Source Code

| Live page | Source |
|---|---|
| [Catalogue](https://better-style-shop.vercel.app/catalogue/) | Product data and web images: [`build.py`](better_style_engine/01_web_storefront/catalogue/build.py) |
| [Fitting / Maximus Motion Studio](https://better-style-shop.vercel.app/catalogue/#/fitting) | Product data it shows: [`build.py`](better_style_engine/01_web_storefront/catalogue/build.py). Its browser code is not in this repository. |
| Checks before anything goes live | [`validate_catalogue.py`](better_style_engine/01_web_storefront/tools/validate_catalogue.py), [`verify_mannequin_renders.py`](better_style_engine/01_web_storefront/tools/verify_mannequin_renders.py), [`test_validate_and_gating.py`](better_style_engine/01_web_storefront/tools/tests/test_validate_and_gating.py) |
| Assembly and publishing | [`assemble_site.py`](better_style_engine/01_web_storefront/tools/assemble_site.py), [`publishing.py`](better_style_engine/01_web_storefront/tools/publishing.py) |

## Main parts (`better_style_engine/01_web_storefront/`)
- `catalogue/build.py`: builds product data and web images from approved renders
- `tools/publishing.py`, `tools/publish.py`: candidate, approve, publish and rollback flow with typed confirmation
- `tools/validate_catalogue.py`: schema validation
- `tools/assemble_site.py`, `tools/serve_site.py`: assemble and preview the deployable site
- `tools/run_browser_tests.py`, `tools/run_site_tests.py`: browser test runners
- `tools/tests/`: integrity tests, including checks that heavy 3D libraries never reach the landing page
- `studio/server.py`: local-only catalogue admin

## About this repository
Published from a larger private workspace. Third-party code, large binaries and generated data are left out, so some scripts and tests need inputs that are not included here. Paths under `~/Desktop/...` are the author's defaults; change them for your machine.
