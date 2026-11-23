"""Local preview server for deploy/site: static files plus the redirect and header rules from its vercel.json.

    python -I tools/serve_site.py [--dir deploy/site] [--port 8771]

Binds to 127.0.0.1 ONLY. It emulates the subset of Vercel routing this site uses (exact sources, and `:name*` suffix
sources, temporary/permanent redirects, header rules). It is an EMULATION used for preview and tests: the real behaviour is
confirmed only by a deployment.
"""
import argparse
import http.server
import json
import re
import sys
from pathlib import Path

CANON = Path(__file__).resolve().parent.parent


def compile_source(src):
    """Strict match, as @vercel/routing-utils generates (path-to-regexp with strict:true): '/catalogue' matches only
    '/catalogue' (NOT '/catalogue/'), and '(.*)' matches anything, including a trailing slash. Checked against the real
    converter in evidence/phase_06/vercel_routing_utils_check.txt."""
    return re.compile("^" + re.escape(src).replace(r"\(\.\*\)", "(.*)") + "$")


def make_handler(site_dir):
    cfg_path = site_dir / "vercel.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
    redirects = [(compile_source(r["source"]), r["destination"], 308 if r.get("permanent") else 307) for r in cfg.get("redirects", [])]
    headers = [(compile_source(h["source"]), h["headers"]) for h in cfg.get("headers", [])]

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(site_dir), **k)

        def log_message(self, fmt, *args):
            sys.stderr.write("preview: " + (fmt % args) + "\n")

        def do_GET(self):
            path = self.path.split("?")[0]
            for rx, dest, code in redirects:
                if rx.match(path):
                    self.send_response(code)
                    self.send_header("Location", dest)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
            super().do_GET()

        do_HEAD = do_GET

        def end_headers(self):
            path = self.path.split("?")[0]
            for rx, hs in headers:
                if rx.match(path):
                    for h in hs:
                        self.send_header(h["key"], h["value"])
            super().end_headers()

        def do_POST(self):
            self.send_error(405)

    return H


class _StudioHTTPServer(http.server.ThreadingHTTPServer):
    request_queue_size = 128
    allow_reuse_address = True


def serve(site_dir, port, host="127.0.0.1"):
    if host not in ("127.0.0.1", "localhost"):
        raise SystemExit("serve_site: refuses to bind to anything but loopback")
    srv = _StudioHTTPServer((host, port), make_handler(Path(site_dir)))
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(CANON / "deploy/site"))
    ap.add_argument("--port", type=int, default=8771)
    a = ap.parse_args()
    srv = serve(a.dir, a.port)
    print(f"preview of {a.dir}: http://127.0.0.1:{a.port}/   (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
