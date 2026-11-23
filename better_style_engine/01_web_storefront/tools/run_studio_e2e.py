"""Run the Studio UI end-to-end test on a temporary project (fake build steps, fake test price).

    python -I tools/run_studio_e2e.py --out evidence/phase_09
"""
import argparse
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

CANON = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CANON / "tools"))
sys.path.insert(0, str(CANON / "tools/tests"))
sys.path.insert(0, str(CANON / "studio"))
import server as studio  # noqa: E402
import test_publishing as tp  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = CANON / a.out
    (out / "shots").mkdir(parents=True, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="bs_studio_e2e_")
    try:
        p = tp.make_project(tmp)
        (p.catalogue / "media").mkdir(parents=True, exist_ok=True)
        (p.root / "reference/old_shop/images").mkdir(parents=True)
        shutil.copy2(CANON / "reference/old_shop/old_shop_extract.json", p.root / "reference/old_shop/old_shop_extract.json")
        app = studio.Studio(project=p, port=0, preview_port=0, steps=tp.FakeSteps(p))
        srv = studio.create_server(app)
        app.port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{app.port}/?t={app.token}"
        r = subprocess.run(["node", "studio.mjs", url, str(out / "shots"), str(p.root)], cwd=CANON / "catalogue/tests", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        text = f"$ node studio.mjs <studio url> <out> <temp project>\n{r.stdout}{r.stderr}\nexit={r.returncode}\n"
        (out / "studio_e2e_output.txt").write_text(text, encoding="utf-8")
        print(text)
        srv.shutdown()
        sys.exit(r.returncode)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
