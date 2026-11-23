"""Run the browser suites against the canonical build and test fixtures, saving all output.

    python -I tools/run_browser_tests.py --out evidence/phase_05 [--only run|fixture|checkout|site]

Needs: catalogue/dist (python -I catalogue/make_dist.py) and Playwright in catalogue/tests (npm install).
  run.mjs      the original 45-check suite against the REAL built data (nothing priced, nothing orderable)
  fixture.mjs  the original fixture suite against a fake priced catalogue ("plain" variant)
  checkout.mjs new: sale gate, order-by-message checkout, contact validation, currency handling
  site         (--site-url) landing + catalogue journey on the assembled site, see tools/run_site_tests.py
"""
import argparse
import functools
import http.server
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

CANON = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CANON / "tools"))
import make_test_fixture  # noqa: E402

VARIANTS = ["plain", "checkout", "wa_only", "email_only", "badcontact", "dual", "gate", "flutterwave", "flw_placeholder", "flw_inquiry"]


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):          # the newsletter test POSTs to a dead endpoint and expects a failure (as a 404 would give)
        self.send_error(404)


def serve(directory):
    h = functools.partial(Quiet, directory=str(directory))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def node(args, cwd, outfile):
    r = subprocess.run(["node", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    text = f"$ node {' '.join(args)}\n{r.stdout}{r.stderr}\nexit={r.returncode}\n"
    Path(outfile).write_text(text, encoding="utf-8")
    passes, fails = text.count("\nPASS"), text.count("\nFAIL")
    return r.returncode, passes + (1 if text.startswith("PASS") else 0), fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--only")
    a = ap.parse_args()
    out = (CANON / a.out) if not Path(a.out).is_absolute() else Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    shots = out / "shots"
    shots.mkdir(exist_ok=True)
    tests = CANON / "catalogue/tests"
    if not (CANON / "catalogue/dist/index.html").is_file():
        sys.exit("catalogue/dist missing: run catalogue/build.py and catalogue/make_dist.py")

    tmp = Path(tempfile.mkdtemp(prefix="bs_fixtures_"))
    summary = []
    try:
        for v in VARIANTS:
            make_test_fixture.make(v, tmp / v)
        s_fix, p_fix = serve(tmp)
        s_real, p_real = serve(CANON / "catalogue/dist")
        plan = [
            ("run", ["run.mjs", f"http://127.0.0.1:{p_real}/", str(shots)]),
            ("fixture", ["fixture.mjs", f"http://127.0.0.1:{p_fix}/plain/", str(shots)]),
            ("checkout", ["checkout.mjs", f"http://127.0.0.1:{p_fix}/"]),
        ]
        for name, args in plan:
            if a.only and a.only != name:
                continue
            rc, ok, bad = node(args, tests, out / f"{name}_output.txt")
            summary.append(f"{name:9s} exit={rc} PASS={ok} FAIL={bad}  -> {out / (name + '_output.txt')}")
        s_fix.shutdown(); s_real.shutdown()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    (out / "browser_summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print("\n".join(summary))


if __name__ == "__main__":
    main()
