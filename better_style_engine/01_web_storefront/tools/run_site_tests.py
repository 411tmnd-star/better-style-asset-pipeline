"""Assemble the site and run the integration journey tests against it (preview server on loopback).

    python -I tools/run_site_tests.py --out evidence/phase_06 [--no-assemble] [--compare-original-landing]
"""
import os
import argparse
import functools
import http.server
import subprocess
import sys
import threading
from pathlib import Path

CANON = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CANON / "tools"))
import serve_site  # noqa: E402
import assemble_site  # noqa: E402

ORIGINAL_LANDING = Path(os.path.normpath(os.path.expanduser('~/Desktop/artifacts/02_documents_and_research/Research/BETTER RESEARCH/RESEARCH/04-TECHNOLOGY-VISUAL-EXPERIMENTS/connected-prototype')))


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-assemble", action="store_true")
    ap.add_argument("--compare-original-landing", action="store_true")
    a = ap.parse_args()
    out = CANON / a.out
    (out / "shots").mkdir(parents=True, exist_ok=True)
    if not a.no_assemble:
        assemble_site.assemble()
    srv = serve_site.serve(CANON / "deploy/site", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    args = ["site.mjs", f"http://127.0.0.1:{port}/", str(out / "shots")]
    orig = None
    if a.compare_original_landing:
        orig = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(ORIGINAL_LANDING)))
        threading.Thread(target=orig.serve_forever, daemon=True).start()
        args.append(f"http://127.0.0.1:{orig.server_address[1]}/")
    r = subprocess.run(["node", *args], cwd=CANON / "catalogue/tests", capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    text = f"$ node {' '.join(args)}\n{r.stdout}{r.stderr}\nexit={r.returncode}\n"
    (out / "site_output.txt").write_text(text, encoding="utf-8")
    srv.shutdown()
    if orig:
        orig.shutdown()
    print(text)
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
