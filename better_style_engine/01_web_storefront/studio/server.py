"""Better Style Studio: the private, local-only Admin for the SRC Catalogue.

    python -I studio/server.py            (or double-click studio.cmd)

Local by construction: binds to 127.0.0.1 only and refuses any other host. It is never deployed; the public site contains no
admin routes. It edits ONE record, catalogue/data/catalogue.json (the catalogue's own data model), and drives the controlled
publishing workflow in tools/publishing.py. It holds no secrets (Vercel credentials stay in the Vercel CLI).

Security
  * token printed to the console and opened in the browser once; it becomes an HttpOnly, SameSite=Strict session cookie
  * every request must carry Host 127.0.0.1:<port> or localhost:<port> (DNS-rebinding guard)
  * every non-GET needs header X-Studio-CSRF (per-session) and a same-origin Origin header
  * approvals are stamped by the SERVER (who, when) and need the typed word APPROVE; live actions need typed PUBLISH / ROLLBACK
  * only the whitelisted files are served; reference data is read-only; no file upload endpoint exists
"""
import argparse
import hashlib
import http.server
import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from pathlib import Path

CANON = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CANON / "tools"))
import publishing as pb  # noqa: E402
import serve_site  # noqa: E402
import validate_catalogue as vc  # noqa: E402

STATIC = Path(__file__).resolve().parent / "static"
STATIC_FILES = {"index.html", "studio.js", "studio.css"}
MAX_BODY = 8 * 1024 * 1024
HREF_RE = re.compile(r"^(#/|https://|mailto:|tel:)")
IMMUTABLE_PRODUCT = ("sku", "extra_render_dirs", "render_dir", "source_kind", "family_id", "ground_hex", "presentation", "category", "status_note", "mannequin_set")
EDITABLE_STATUS = {"live", "held"}


# ---------- editing rules (pure functions, unit-tested) ----------
def immutable_errors(old, new):
    """Fields Studio never changes: ids, render/source wiring, the hero editorial source, looks, and the set of products."""
    errs = []
    if set(new) != set(old):
        errs.append("unknown or removed top-level keys: " + ", ".join(sorted(set(new) ^ set(old))))
    for k in ("schema", "_about"):
        if new.get(k) != old.get(k):
            errs.append(f"{k} cannot be edited in Studio")
    if new.get("looks") != old.get("looks"):
        errs.append("looks cannot be edited in Studio")
    osku, nsku = [p["sku"] for p in old["products"]], [p.get("sku") for p in new["products"]]
    if osku != nsku:
        errs.append("products cannot be added, removed, renamed or re-ordered in Studio")
        return errs
    for o, n in zip(old["products"], new["products"]):
        for k in IMMUTABLE_PRODUCT:
            if n.get(k) != o.get(k):
                errs.append(f"{o['sku']}: {k} cannot be edited in Studio")
        if n.get("status") != o.get("status"):
            if not ({n.get("status"), o.get("status")} <= EDITABLE_STATUS):
                errs.append(f"{o['sku']}: status can only be switched between live and held")
        oc, nc = o.get("colourways") or {}, n.get("colourways") or {}
        if list(oc) != list(nc):
            errs.append(f"{o['sku']}: colourways cannot be added or removed in Studio")
            continue
        for cid in oc:
            for k in set(oc[cid]) | set(nc[cid]):
                if k not in ("label", "stock") and oc[cid].get(k) != nc[cid].get(k):
                    errs.append(f"{o['sku']}/{cid}: {k} cannot be edited in Studio")
    oh, nh = (old["site"].get("hero") or {}), (new.get("site", {}).get("hero") or {})
    for k in ("editorial_source", "plate_source"):
        if oh.get(k) != nh.get(k):
            errs.append(f"site.hero.{k} cannot be edited in Studio")
    for h in ((new.get("site", {}).get("newsletter") or {}).get("endpoint"),):
        if h is not None and not str(h).startswith("https://"):
            errs.append("site.newsletter.endpoint must be null or an https:// address")
    for col in (new.get("site", {}).get("footer") or []):
        for l in col.get("links", []):
            if l.get("href") is not None and not HREF_RE.match(str(l["href"])):
                errs.append(f"footer link '{l.get('label')}': href must be null, #/..., https://, mailto: or tel:")
    return errs


def normalize_approvals(old, new, confirm, who, now):
    """Server-side approval rules. Returns (new, errors, notices).
    - 'by'/'at' are stamped here; whatever the client sent is ignored.
    - A field goes draft -> approved only with the typed word APPROVE.
    - Changing an approved field's value resets it to draft (unless re-approved with APPROVE).
    - Changing prices or sizes of a sale-approved piece moves it to awaiting_approval (unless re-approved with APPROVE)."""
    errs, notes = [], []
    om = {p["sku"]: p for p in old["products"]}
    for p in new["products"]:
        o = om[p["sku"]]
        if p.get("status") != "live" and "approval" not in p:
            continue
        appr, oappr = p.setdefault("approval", {}), o.get("approval") or {}
        for f in vc.GATED:
            a = dict(appr.get(f) or {"status": "draft", "source": None})
            oa = oappr.get(f) or {"status": "draft", "source": None, "by": None, "at": None}
            changed = p.get(f) != o.get(f)
            if a.get("status") == "approved":
                if oa.get("status") == "approved" and not changed:
                    a = dict(oa)
                elif oa.get("status") != "approved":
                    if confirm != "APPROVE":
                        errs.append(f"{p['sku']}: approving '{f}' needs the typed word APPROVE")
                        a = {"status": "draft", "source": a.get("source"), "by": None, "at": None}
                    else:
                        a = {"status": "approved", "source": a.get("source"), "by": who, "at": now}
                else:   # was approved, value changed, still ticked
                    if confirm == "APPROVE":
                        a = {"status": "approved", "source": a.get("source"), "by": who, "at": now}
                    else:
                        a = {"status": "draft", "source": a.get("source"), "by": None, "at": None}
                        notes.append(f"{p['sku']}: '{f}' changed, so its approval was reset to draft")
            else:
                a = {"status": "draft", "source": a.get("source"), "by": None, "at": None}
            appr[f] = a
        s, os_ = p.get("sale") or {}, o.get("sale") or {}
        st = s.get("status", "draft")
        if st == "approved_for_sale":
            same = os_.get("status") == "approved_for_sale" and p.get("prices") == o.get("prices") and p.get("sizes") == o.get("sizes")
            if same:
                p["sale"] = dict(os_)
            elif confirm == "APPROVE":
                p["sale"] = {"status": "approved_for_sale", "by": who, "at": now}
            elif os_.get("status") == "approved_for_sale":
                p["sale"] = {"status": "awaiting_approval", "by": None, "at": None}
                notes.append(f"{p['sku']}: price or sizes changed, so sale approval was withdrawn (awaiting approval)")
            else:
                errs.append(f"{p['sku']}: approving for sale needs the typed word APPROVE")
                p["sale"] = {"status": "awaiting_approval", "by": None, "at": None}
        else:
            p["sale"] = {"status": st, "by": None, "at": None}
    return new, errs, notes


def dump_catalogue(cat):
    return json.dumps(cat, indent=2, ensure_ascii=False) + "\n"


# ---------- application ----------
class Studio:
    def __init__(self, project=None, port=8770, preview_port=8771, steps=None, host="127.0.0.1"):
        if host != "127.0.0.1":
            raise SystemExit("Studio is local-only: it refuses to bind to anything but 127.0.0.1")
        self.p = project or pb.Project()
        self.port, self.preview_port, self.host, self.steps = port, preview_port, host, steps
        self.token = secrets.token_urlsafe(32)
        self.token_used = False
        self.sessions = {}
        self.jobs = {}
        self.job_lock = threading.Lock()
        self.preview_srv = None
        self.lock = threading.Lock()

    # -- helpers
    def etag(self):
        return pb.sha_file(self.p.cat_json)

    def read_cat(self):
        return json.loads(self.p.cat_json.read_text(encoding="utf-8"))

    def thumbs(self):
        out = {}
        pj = self.p.catalogue / "data/products.json"
        if pj.is_file():
            try:
                for pr in json.loads(pj.read_text(encoding="utf-8"))["products"]:
                    out[pr["sku"]] = {cw["id"]: cw["renders"][0]["tile"] for cw in pr["colourways"] if cw["renders"]}
            except Exception:  # noqa: BLE001
                pass
        return out

    def catalogue_payload(self):
        cat = self.read_cat()
        errs, warns = vc.validate(cat, self.p.catalogue)
        return {"catalogue": cat, "etag": self.etag(), "readiness": vc.readiness(cat), "validation": {"errors": errs, "warnings": warns},
                "thumbs": self.thumbs(), "currencies": vc.load_currencies(self.p.config / "currencies.json"), "gated": list(vc.GATED), "sale_states": list(vc.SALE_STATES)}

    def families(self):
        f = self.p.catalogue / "engine_inputs/garment_registry/garment_families/garment_families.json"
        if not f.is_file():
            return []
        d = json.loads(f.read_text(encoding="utf-8"))
        return [{"id": x["id"], "name": x["name"], "group": x["group"]} for x in (d["families"] if isinstance(d, dict) else d)]

    def status(self):
        cfg, blockers = pb.deployment_gates(self.p)
        pubs = pb.list_publications(self.p)
        cat = self.read_cat()
        co = (cat["site"].get("checkout") or {})
        marker = (self.p.deploy / ".preview_marker").exists()
        return {
            "draft_sha256": self.etag(), "current": pb.load_current(self.p),
            "candidates": [{k: m.get(k) for k in ("publication_id", "created_at", "status", "approval", "build", "validation", "proposed_changes_summary", "previous_publication")} for m in pubs if m["status"] in ("candidate", "approved")],
            "preview": {"running": self.preview_srv is not None, "url": f"http://127.0.0.1:{self.preview_port}/" if self.preview_srv else None, "draft_values": marker, "site_built": (self.p.site / "index.html").is_file()},
            "live_blockers": blockers, "vercel": cfg["vercel"], "checkout": {"whatsapp": bool(co.get("whatsapp")), "email": bool(co.get("email"))},
            "job": self.running_job(), "user": pb.who(),
        }

    def running_job(self):
        for j in self.jobs.values():
            if j["state"] == "running":
                return {"id": j["id"], "action": j["action"]}
        return None

    # -- jobs
    def start_job(self, spec):
        action = spec.get("action")
        allowed = {"check", "preview", "preview-draft", "candidate", "candidate-fast", "approve", "publish", "rollback", "dry-run-publish-live"}
        if action not in allowed:
            raise ValueError("unknown action")
        if not self.job_lock.acquire(blocking=False):
            raise RuntimeError("another job is running")
        jid = secrets.token_hex(6)
        job = {"id": jid, "action": action, "state": "running", "log": [], "result": None, "started": pb.now_iso()}
        self.jobs[jid] = job
        say = lambda *a: job["log"].append(" ".join(str(x) for x in a))  # noqa: E731

        def run():
            try:
                p, st = self.p, self.steps
                if action == "check":
                    v = pb.check(p)
                    say(f"{len(v['errors'])} error(s), {len(v['warnings'])} warning(s)")
                    job["result"] = v
                elif action in ("preview", "preview-draft"):
                    job["result"] = pb.preview(p, provisional=action == "preview-draft", steps=st, say=say)
                elif action in ("candidate", "candidate-fast"):
                    m = pb.create_candidate(p, mode="fast" if action == "candidate-fast" else "full", steps=st, say=say)
                    job["result"] = {"publication_id": m["publication_id"]}
                elif action == "approve":
                    pb.approve(p, spec["id"], spec.get("confirm", ""), say=say)
                elif action == "publish":
                    pb.publish(p, spec["id"], spec.get("target", "local"), spec.get("confirm", ""), steps=st, say=say)
                elif action == "dry-run-publish-live":
                    job["result"] = pb.publish(p, spec["id"], "vercel", "", steps=st, dry_run=True, say=say)
                elif action == "rollback":
                    pb.rollback(p, spec.get("id"), spec.get("confirm", ""), live=bool(spec.get("live")), steps=st, say=say)
                job["state"] = "done"
            except pb.PublishError as e:
                say("REFUSED: " + str(e))
                job["state"] = "failed"
            except Exception as e:  # noqa: BLE001
                say("ERROR: " + repr(e))
                job["log"].append(traceback.format_exc()[-1500:])
                job["state"] = "failed"
            finally:
                job["finished"] = pb.now_iso()
                pb.log_event(self.p, action="studio_job", job=action, state=job["state"], id=spec.get("id"))
                self.job_lock.release()

        threading.Thread(target=run, daemon=True).start()
        return job

    # -- preview server (loopback only)
    def preview_start(self):
        with self.lock:
            if not self.preview_srv:
                self.preview_srv = serve_site.serve(self.p.site, self.preview_port)
                threading.Thread(target=self.preview_srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.preview_port}/"

    def preview_stop(self):
        with self.lock:
            if self.preview_srv:
                self.preview_srv.shutdown()
                self.preview_srv.server_close()
                self.preview_srv = None


def make_handler(app):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "BetterStyleStudio"
        sys_version = ""

        # -- plumbing
        def log_message(self, fmt, *args):      # never log the token
            line = fmt % args
            sys.stderr.write("studio: " + re.sub(r"t=[A-Za-z0-9_-]+", "t=…", line) + "\n")

        def _send(self, code, body=b"", ctype="application/json", headers=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            elif isinstance(body, str):
                body = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _err(self, code, msg, **extra):
            self._send(code, {"error": msg, **extra})

        def _host_ok(self):
            return self.headers.get("Host", "") in (f"127.0.0.1:{app.port}", f"localhost:{app.port}")

        def _session(self):
            ck = self.headers.get("Cookie", "")
            m = re.search(r"(?:^|;\s*)studio_session=([A-Za-z0-9_-]+)", ck)
            return app.sessions.get(m.group(1)) if m else None

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("request too large")
            return self.rfile.read(n) if n else b""

        def _guard(self, write):
            """Host, session and (for writes) CSRF + Origin checks. Returns the session or None (response already sent)."""
            if not self._host_ok():
                self._err(403, "bad Host header")
                return None
            sess = self._session()
            if not sess:
                self._err(401, "no session: open Studio from studio.cmd")
                return None
            if write:
                if self.headers.get("X-Studio-CSRF") != sess["csrf"]:
                    self._err(403, "missing or wrong CSRF header")
                    return None
                if self.headers.get("Origin") not in (f"http://127.0.0.1:{app.port}", f"http://localhost:{app.port}"):
                    self._err(403, "bad Origin")
                    return None
            return sess

        # -- GET
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            path, q = u.path, urllib.parse.parse_qs(u.query)
            if not self._host_ok():
                return self._err(403, "bad Host header")
            if path == "/" and "t" in q:                                # one-time bootstrap
                if app.token_used or not secrets.compare_digest(q["t"][0], app.token):
                    return self._err(401, "invalid or already used token: restart Studio")
                app.token_used = True
                sid = secrets.token_urlsafe(32)
                app.sessions[sid] = {"csrf": secrets.token_urlsafe(24)}
                return self._send(302, b"", "text/plain", {"Location": "/", "Set-Cookie": f"studio_session={sid}; HttpOnly; SameSite=Strict; Path=/"})
            sess = self._guard(False)
            if not sess:
                return
            try:
                if path == "/":
                    return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
                if path.startswith("/static/"):
                    name = path[len("/static/"):]
                    if name not in STATIC_FILES:
                        return self._err(404, "not found")
                    return self._send(200, (STATIC / name).read_bytes(), mimetypes.guess_type(name)[0] or "text/plain")
                if path == "/api/session":
                    return self._send(200, {"csrf": sess["csrf"], "user": pb.who()})
                if path == "/api/catalogue":
                    d = app.catalogue_payload()
                    return self._send(200, d, headers={"ETag": d["etag"]})
                if path == "/api/families":
                    return self._send(200, app.families())
                if path == "/api/status":
                    return self._send(200, app.status())
                if path == "/api/old-shop":
                    return self._send(200, (app.p.root / "reference/old_shop/old_shop_extract.json").read_bytes())
                if path == "/api/history":
                    h = pb.history(app.p)
                    tail = []
                    if app.p.log.is_file():
                        for l in app.p.log.read_text(encoding="utf-8").splitlines()[-80:]:
                            try:
                                tail.append(json.loads(l))
                            except ValueError:
                                pass
                    return self._send(200, {**h, "log": tail})
                if path.startswith("/api/changes/"):
                    pid = path.rsplit("/", 1)[1]
                    if not re.fullmatch(r"PUB-[0-9A-Za-z-]+", pid):
                        return self._err(404, "not found")
                    f = app.p.pubs / pid / "proposed_changes.md"
                    return self._send(200, f.read_bytes() if f.is_file() else b"", "text/plain; charset=utf-8")
                m = re.fullmatch(r"/api/jobs/([0-9a-f]{12})", path)
                if m:
                    j = app.jobs.get(m.group(1))
                    return self._send(200, j) if j else self._err(404, "no such job")
                if path.startswith("/files/catalogue/media/") or path.startswith("/files/reference/images/"):
                    return self._file(path)
                return self._err(404, "not found")
            except Exception as e:  # noqa: BLE001
                return self._err(500, repr(e))

        def _file(self, path):
            if path.startswith("/files/catalogue/media/"):
                root, rel = app.p.catalogue / "media", urllib.parse.unquote(path[len("/files/catalogue/media/"):])
            else:
                root, rel = app.p.root / "reference/old_shop/images", urllib.parse.unquote(path[len("/files/reference/images/"):])
            if not rel or "\x00" in rel or "\\" in rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel) or ".." in Path(rel).parts:
                return self._err(404, "not found")
            f = (root / rel).resolve()
            try:
                f.relative_to(root.resolve())
            except ValueError:
                return self._err(404, "not found")
            if not f.is_file() or f.suffix.lower() not in (".webp", ".png", ".jpg", ".jpeg", ".svg"):
                return self._err(404, "not found")
            return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")

        # -- PUT / POST
        def do_PUT(self):
            sess = self._guard(True)
            if not sess:
                return
            path = urllib.parse.urlsplit(self.path).path
            try:
                if path == "/api/catalogue":
                    return self._put_catalogue()
                return self._err(404, "not found")
            except ValueError as e:
                return self._err(400, str(e))
            except Exception as e:  # noqa: BLE001
                return self._err(500, repr(e))

        def _put_catalogue(self):
            raw = self._body()
            if self.headers.get("If-Match") != app.etag():
                return self._err(409, "catalogue.json changed since you loaded it: reload", etag=app.etag())
            if app.running_job():
                return self._err(409, "a publishing job is running: wait for it to finish")
            try:
                data = json.loads(raw)
                new = data["catalogue"]
            except Exception:  # noqa: BLE001
                return self._err(400, "body must be JSON {catalogue: ...}")
            old = app.read_cat()
            errs = immutable_errors(old, new)
            if errs:
                return self._err(422, "rejected", errors=errs)
            new, aerrs, notes = normalize_approvals(old, new, data.get("approve_confirm"), pb.who(), pb.now_iso())
            if aerrs:
                return self._err(422, "approval rejected", errors=aerrs)
            verrs, warns = vc.validate(new, app.p.catalogue)
            if verrs:
                return self._err(422, "validation failed: nothing was saved", errors=verrs, warnings=warns)
            prev = app.p.cat_json.with_name(".catalogue.prev.json")
            prev.write_bytes(app.p.cat_json.read_bytes())
            tmp = app.p.cat_json.with_name(".catalogue.tmp.json")
            tmp.write_bytes(dump_catalogue(new).encode("utf-8"))
            os.replace(tmp, app.p.cat_json)
            changed = [c["path"] for c in pb.diff_catalogues(old, new)][:60]
            pb.log_event(app.p, action="studio_save", fields_changed=len(changed), sample=changed[:12], approvals_stamped=bool(data.get("approve_confirm")))
            return self._send(200, {**app.catalogue_payload(), "notices": notes}, headers={"ETag": app.etag()})

        def do_POST(self):
            sess = self._guard(True)
            if not sess:
                return
            path = urllib.parse.urlsplit(self.path).path
            try:
                body = json.loads(self._body() or b"{}")
                if path == "/api/jobs":
                    try:
                        return self._send(202, app.start_job(body))
                    except RuntimeError as e:
                        return self._err(409, str(e))
                    except ValueError as e:
                        return self._err(400, str(e))
                if path == "/api/preview/start":
                    if not (app.p.site / "index.html").is_file():
                        return self._err(409, "nothing has been built yet: run Preview first")
                    return self._send(200, {"url": app.preview_start()})
                if path == "/api/preview/stop":
                    app.preview_stop()
                    return self._send(200, {"ok": True})
                return self._err(404, "not found")
            except json.JSONDecodeError:
                return self._err(400, "body must be JSON")
            except ValueError as e:
                return self._err(400, str(e))
            except Exception as e:  # noqa: BLE001
                return self._err(500, repr(e))

        def do_DELETE(self):
            self._err(405, "method not allowed")

    return Handler


def create_server(app):
    return http.server.ThreadingHTTPServer((app.host, app.port), make_handler(app))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    if a.host != "127.0.0.1":
        sys.exit("Studio is local-only: it refuses to bind to anything but 127.0.0.1")
    app = Studio(port=a.port, host=a.host)
    srv = create_server(app)
    app.port = srv.server_address[1]
    url = f"http://127.0.0.1:{app.port}/?t={app.token}"
    print("Better Style Studio (local only)")
    print(f"  Open: {url}")
    print("  The link works once. Press Ctrl+C to stop.")
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.preview_stop()


if __name__ == "__main__":
    main()
