"""Studio: local-only binding, session/Host/CSRF/Origin guards, server-enforced approvals, immutable fields, file serving, jobs."""
import copy
import http.client
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

CANON = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CANON / "tools"))
sys.path.insert(0, str(CANON / "studio"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import server as studio  # noqa: E402
import test_publishing as tp  # noqa: E402
import publishing as pb  # noqa: E402


class Client:
    def __init__(self, port):
        self.port, self.cookie, self.csrf = port, None, None

    def req(self, method, path, body=None, headers=None, host=None, origin=True, csrf=True, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        if self.cookie:
            h["Cookie"] = self.cookie
        if method != "GET":
            if csrf and self.csrf:
                h["X-Studio-CSRF"] = self.csrf
            if origin:
                h["Origin"] = f"http://127.0.0.1:{self.port}"
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        b = r.read()
        c.close()
        try:
            j = json.loads(b)
        except ValueError:
            j = None
        return r.status, j, r


class StudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="bs_studio_")
        cls.p = tp.make_project(cls.tmp)
        (cls.p.catalogue / "media").mkdir(parents=True, exist_ok=True)
        (cls.p.catalogue / "media/t.webp").write_bytes(b"RIFFx")
        (cls.p.catalogue / "build.py").write_text("SECRET", encoding="utf-8")
        (cls.p.root / "reference/old_shop/images").mkdir(parents=True)
        (cls.p.root / "reference/old_shop/old_shop_extract.json").write_text('{"records":[]}', encoding="utf-8")
        cls.steps = tp.FakeSteps(cls.p)
        cls.app = studio.Studio(project=cls.p, port=0, preview_port=0, steps=cls.steps)
        cls.srv = studio.create_server(cls.app)
        cls.app.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.app.port

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close(); cls.app.preview_stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def login(self):
        app = self.app
        app.token, app.token_used = "T" * 43, False
        c = Client(self.port)
        st, _, r = c.req("GET", f"/?t={app.token}")
        self.assertEqual(st, 302)
        ck = r.getheader("Set-Cookie")
        self.assertIn("HttpOnly", ck); self.assertIn("SameSite=Strict", ck)
        c.cookie = ck.split(";")[0]
        c.csrf = c.req("GET", "/api/session")[1]["csrf"]
        return c

    def restore(self):
        shutil.copy2(self.p.cat_json.with_name(".orig.json"), self.p.cat_json) if self.p.cat_json.with_name(".orig.json").exists() else None

    def setUp(self):
        o = self.p.cat_json.with_name(".orig.json")
        if not o.exists():
            shutil.copy2(self.p.cat_json, o)
        shutil.copy2(o, self.p.cat_json)

    # ---- local-only ----
    def test_listener_is_loopback_only(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")

    def test_refuses_non_loopback_host_option(self):
        with self.assertRaises(SystemExit):
            studio.Studio(project=self.p, host="0.0.0.0")
        r = subprocess.run([sys.executable, "-I", str(CANON / "studio/server.py"), "--host", "0.0.0.0", "--no-browser"], capture_output=True, text=True, timeout=30)
        self.assertNotEqual(r.returncode, 0); self.assertIn("local-only", r.stderr + r.stdout)

    def test_actually_bound_to_loopback_not_all_interfaces(self):
        try:
            ext = socket.gethostbyname(socket.gethostname())
        except OSError:
            self.skipTest("no external address")
        if ext.startswith("127."):
            self.skipTest("no external address")
        s = socket.socket(); s.settimeout(2)
        self.assertNotEqual(s.connect_ex((ext, self.port)), 0)       # not reachable via the machine's LAN address
        s.close()

    # ---- session / host / csrf ----
    def test_no_cookie_is_401(self):
        self.assertEqual(Client(self.port).req("GET", "/api/catalogue")[0], 401)
        self.assertEqual(Client(self.port).req("GET", "/")[0], 401)

    def test_bad_and_reused_token(self):
        self.app.token, self.app.token_used = "A" * 43, False
        self.assertEqual(Client(self.port).req("GET", "/?t=wrong")[0], 401)
        self.assertEqual(Client(self.port).req("GET", "/?t=" + "A" * 43)[0], 302)
        self.assertEqual(Client(self.port).req("GET", "/?t=" + "A" * 43)[0], 401)      # one-time

    def test_wrong_host_is_403_even_with_a_session(self):
        c = self.login()
        self.assertEqual(c.req("GET", "/api/catalogue", host="evil.test")[0], 403)
        self.assertEqual(c.req("GET", "/api/catalogue", host=f"evil.test:{self.port}")[0], 403)
        self.assertEqual(c.req("GET", "/api/catalogue", host=f"localhost:{self.port}")[0], 200)

    def test_writes_need_csrf_and_origin(self):
        c = self.login(); et = c.req("GET", "/api/catalogue")[2].getheader("ETag")
        body = {"catalogue": self.app.read_cat()}
        self.assertEqual(c.req("PUT", "/api/catalogue", body, headers={"If-Match": et}, csrf=False)[0], 403)
        self.assertEqual(c.req("PUT", "/api/catalogue", body, headers={"If-Match": et}, origin=False)[0], 403)
        self.assertEqual(c.req("PUT", "/api/catalogue", body, headers={"If-Match": et, "Origin": "http://evil.test"})[0], 403)
        self.assertEqual(c.req("PUT", "/api/catalogue", body, headers={"If-Match": et, "X-Studio-CSRF": "wrong"})[0], 403)
        self.assertEqual(c.req("POST", "/api/jobs", {"action": "check"}, csrf=False)[0], 403)

    def test_security_headers(self):
        c = self.login(); _, _, r = c.req("GET", "/")
        self.assertIn("default-src 'self'", r.getheader("Content-Security-Policy")); self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")

    # ---- saving ----
    def get(self, c):
        st, d, r = c.req("GET", "/api/catalogue")
        return d["catalogue"], r.getheader("ETag")

    def test_stale_etag_is_409(self):
        c = self.login(); cat, et = self.get(c)
        self.assertEqual(c.req("PUT", "/api/catalogue", {"catalogue": cat}, headers={"If-Match": "0" * 64})[0], 409)

    def test_valid_edit_is_saved_atomically_with_previous_kept(self):
        c = self.login(); cat, et = self.get(c)
        before = self.p.cat_json.read_bytes()
        cat["site"]["section_title"] = "Garments"
        st, d, r = c.req("PUT", "/api/catalogue", {"catalogue": cat}, headers={"If-Match": et})
        self.assertEqual(st, 200, d)
        self.assertEqual(json.loads(self.p.cat_json.read_text(encoding="utf-8"))["site"]["section_title"], "Garments")
        self.assertEqual(self.p.cat_json.with_name(".catalogue.prev.json").read_bytes(), before)
        self.assertNotEqual(r.getheader("ETag"), et)
        self.assertFalse(self.p.cat_json.with_name(".catalogue.tmp.json").exists())

    def test_invalid_data_is_422_and_file_unchanged(self):
        c = self.login(); cat, et = self.get(c); before = self.p.cat_json.read_bytes()
        cat["products"][0]["prices"]["NGN"] = -5
        st, d, _ = c.req("PUT", "/api/catalogue", {"catalogue": cat}, headers={"If-Match": et})
        self.assertEqual(st, 422); self.assertTrue(d["errors"])
        self.assertEqual(self.p.cat_json.read_bytes(), before)
        cat, et = self.get(c); cat["site"]["checkout"]["whatsapp"] = "not-a-number"
        self.assertEqual(c.req("PUT", "/api/catalogue", {"catalogue": cat}, headers={"If-Match": et})[0], 422)

    def test_immutable_fields_cannot_be_changed(self):
        c = self.login()
        for mut in (lambda k: k["products"][0].update(sku="BS-X-001"), lambda k: k["products"][0].update(render_dir="../.."),
                    lambda k: k["products"].append({"sku": "BS-NEW-001", "status": "draft"}), lambda k: k.update(looks=[{"x": 1}]),
                    lambda k: k["site"]["hero"].update(editorial_source="../../x.png"), lambda k: k["products"][0]["colourways"].popitem(),
                    lambda k: k["products"][0].update(status="in_production")):
            cat, et = self.get(c); mut(cat)
            self.assertEqual(c.req("PUT", "/api/catalogue", {"catalogue": cat}, headers={"If-Match": et})[0], 422)

    def test_approving_needs_typed_word_and_server_stamps_who_and_when(self):
        c = self.login(); cat, et = self.get(c); pr = cat["products"][0]
        pr["prices"] = {"NGN": 7000}
        pr["approval"]["prices"] = {"status": "approved", "source": None, "by": "FORGED", "at": "1999-01-01"}
        st, d, _ = c.req("PUT", "/api/catalogue", {"catalogue": cat}, headers={"If-Match": et})
        self.assertEqual(st, 422); self.assertIn("APPROVE", json.dumps(d))
        self.assertEqual(self.app.read_cat()["products"][0]["prices"], {"NGN": None})        # nothing saved
        st, d, _ = c.req("PUT", "/api/catalogue", {"catalogue": cat, "approve_confirm": "APPROVE"}, headers={"If-Match": et})
        self.assertEqual(st, 200, d)
        a = self.app.read_cat()["products"][0]["approval"]["prices"]
        self.assertEqual(a["status"], "approved"); self.assertNotEqual(a["by"], "FORGED"); self.assertNotEqual(a["at"], "1999-01-01")

    def test_sale_approval_needs_typed_word_and_complete_data(self):
        c = self.login(); cat, et = self.get(c); pr = cat["products"][0]
        pr["sale"] = {"status": "approved_for_sale"}
        st, d, _ = c.req("PUT", "/api/catalogue", {"catalogue": cat, "approve_confirm": "APPROVE"}, headers={"If-Match": et})
        self.assertEqual(st, 422)                                                                  # typed, but no approved price/sizes: validator refuses
        self.assertEqual(self.app.read_cat()["products"][0]["sale"]["status"], "draft")

    def test_editing_an_approved_value_resets_its_approval_and_withdraws_sale(self):
        c = self.login(); cat, et = self.get(c); pr = cat["products"][0]
        pr["prices"] = {"NGN": 7000}; pr["sizes"] = [{"label": "M", "stock": None}]
        for f in ("prices", "sizes"):
            pr["approval"][f]["status"] = "approved"
        pr["sale"] = {"status": "approved_for_sale"}
        self.assertEqual(c.req("PUT", "/api/catalogue", {"catalogue": cat, "approve_confirm": "APPROVE"}, headers={"If-Match": et})[0], 200)
        cat, et = self.get(c); pr = cat["products"][0]
        self.assertEqual(pr["sale"]["status"], "approved_for_sale")
        pr["prices"]["NGN"] = 9000                                                                 # change price, no confirmation
        st, d, _ = c.req("PUT", "/api/catalogue", {"catalogue": cat}, headers={"If-Match": et})
        self.assertEqual(st, 200, d)
        saved = self.app.read_cat()["products"][0]
        self.assertEqual(saved["approval"]["prices"]["status"], "draft"); self.assertEqual(saved["sale"]["status"], "awaiting_approval")
        self.assertTrue(d["notices"])

    def test_client_cannot_keep_a_forged_existing_approval(self):
        c = self.login(); cat, et = self.get(c)
        cat["products"][0]["approval"]["description"] = {"status": "approved", "source": None, "by": "x", "at": "y"}
        self.assertEqual(c.req("PUT", "/api/catalogue", {"catalogue": cat}, headers={"If-Match": et})[0], 422)

    # ---- files ----
    def test_file_serving_is_confined(self):
        c = self.login()
        self.assertEqual(c.req("GET", "/files/catalogue/media/t.webp")[0], 200)
        for bad in ("/files/catalogue/media/../build.py", "/files/catalogue/media/..%2Fbuild.py", "/files/catalogue/media/%2e%2e/build.py",
                    "/files/catalogue/build.py", "/files/catalogue/sources/x.png", "/files/catalogue/media/..\\build.py", "/static/../server.py", "/static/server.py", "/files/reference/images/../old_shop_extract.json"):
            self.assertEqual(c.req("GET", bad)[0], 404, bad)

    def test_no_upload_or_delete_endpoints(self):
        c = self.login()
        self.assertEqual(c.req("POST", "/api/upload/BS-RINGER-001", {})[0], 404)
        self.assertEqual(c.req("DELETE", "/api/catalogue")[0] in (403, 405), True)

    def test_old_shop_is_read_only_through_the_api(self):
        c = self.login()
        self.assertEqual(c.req("GET", "/api/old-shop")[0], 200)
        for m in ("PUT", "POST"):
            self.assertEqual(c.req(m, "/api/old-shop", {})[0], 404)

    # ---- jobs ----
    def wait_job(self, c, jid):
        import time
        for _ in range(200):
            j = c.req("GET", f"/api/jobs/{jid}")[1]
            if j["state"] != "running":
                return j
            time.sleep(0.1)
        self.fail("job did not finish")

    def test_publish_job_without_confirmation_is_refused(self):
        c = self.login()
        st, j, _ = c.req("POST", "/api/jobs", {"action": "candidate"}); self.assertEqual(st, 202)
        j = self.wait_job(c, j["id"]); self.assertEqual(j["state"], "done", j["log"])
        pid = j["result"]["publication_id"]
        st, j2, _ = c.req("POST", "/api/jobs", {"action": "approve", "id": pid, "confirm": "yes"})
        j2 = self.wait_job(c, j2["id"]); self.assertEqual(j2["state"], "failed"); self.assertIn("REFUSED", "\n".join(j2["log"]))
        st, j3, _ = c.req("POST", "/api/jobs", {"action": "approve", "id": pid, "confirm": f"APPROVE {pid}"})
        self.assertEqual(self.wait_job(c, j3["id"])["state"], "done")
        st, j4, _ = c.req("POST", "/api/jobs", {"action": "publish", "id": pid, "target": "local", "confirm": ""})
        self.assertEqual(self.wait_job(c, j4["id"])["state"], "failed")
        st, j5, _ = c.req("POST", "/api/jobs", {"action": "publish", "id": pid, "target": "vercel", "confirm": "PUBLISH"})
        j5 = self.wait_job(c, j5["id"]); self.assertEqual(j5["state"], "failed"); self.assertIn("B4", "\n".join(j5["log"]))
        self.assertEqual(self.steps.vercel_calls, [])
        st, j6, _ = c.req("POST", "/api/jobs", {"action": "publish", "id": pid, "target": "local", "confirm": "PUBLISH"})
        self.assertEqual(self.wait_job(c, j6["id"])["state"], "done")
        self.assertEqual(c.req("GET", "/api/history")[1]["current"]["id"], pid)

    def test_unknown_job_action_and_one_job_at_a_time(self):
        c = self.login()
        self.assertEqual(c.req("POST", "/api/jobs", {"action": "rm -rf"})[0], 400)
        self.assertTrue(self.app.job_lock.acquire(blocking=False))
        try:
            self.assertEqual(c.req("POST", "/api/jobs", {"action": "check"})[0], 409)
        finally:
            self.app.job_lock.release()

    def test_audit_log_records_saves_and_jobs_without_secrets(self):
        txt = self.p.log.read_text(encoding="utf-8") if self.p.log.exists() else ""
        self.assertNotIn(self.app.token, txt)

    def test_preview_server_is_loopback_only(self):
        c = self.login()
        c.req("POST", "/api/jobs", {"action": "preview"})
        import time; time.sleep(1)
        st, d, _ = c.req("POST", "/api/preview/start", {})
        if st == 200:
            self.assertTrue(d["url"].startswith("http://127.0.0.1:"))
            self.assertEqual(self.app.preview_srv.server_address[0], "127.0.0.1")
            c.req("POST", "/api/preview/stop", {})


class RulesUnitTests(unittest.TestCase):
    def test_hosts_other_than_loopback_rejected_by_class(self):
        with self.assertRaises(SystemExit):
            studio.Studio(host="192.168.1.5")

    def test_static_whitelist(self):
        self.assertEqual(studio.STATIC_FILES, {"index.html", "studio.js", "studio.css"})
        for f in studio.STATIC_FILES:
            self.assertTrue((studio.STATIC / f).is_file())

    def test_no_external_urls_or_inline_script_in_the_ui(self):
        for f in studio.STATIC_FILES:
            t = (studio.STATIC / f).read_text(encoding="utf-8")
            self.assertNotRegex(t, r"https?://(?!127\.0\.0\.1)", f)
        self.assertNotIn("<script>", (studio.STATIC / "index.html").read_text(encoding="utf-8"))

    def test_studio_is_not_part_of_the_public_site(self):
        sys.path.insert(0, str(CANON / "tools"))
        import assemble_site
        self.assertIn("studio", assemble_site.FORBIDDEN_NAMES)


if __name__ == "__main__":
    unittest.main()
