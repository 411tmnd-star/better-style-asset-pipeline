# Better Style storefront: catalogue build and publishing tools

The Python side of a static fashion storefront: building the catalogue, validating it and publishing it safely. The front-end itself is not in this repository.

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
