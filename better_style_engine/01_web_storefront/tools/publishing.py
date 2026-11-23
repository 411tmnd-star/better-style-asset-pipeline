"""Controlled publishing for the Better Style catalogue: candidate -> approve -> publish -> rollback.

Nothing here changes a live site on its own. The live (Vercel) step is refused unless the owner has recorded approval in
config/deployment.json (B4 and the hosted-database decision) AND types the confirmation word. Everything before that is local.

Concepts
  draft        catalogue/data/catalogue.json as saved (edited in Studio). Customers never see it.
  candidate    an immutable build of one exact draft: publications/<id>/ with a snapshot of the product data, the built site
               (site.zip), validation + build logs, and a record of proposed changes versus the last published state.
  approved     the owner approved that exact candidate (typed: APPROVE <id>). A later draft edit makes it stale.
  published    the approved candidate was promoted (target "local" = release-ready; target "vercel" = deployed live).
  rollback     restores a previously published, known-good publication from its own snapshot and site.zip (no reconstruction).

All actions are appended to publications/log.jsonl. Each publication has a versioned manifest.json.
"""
import getpass
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

CANON = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_catalogue as vc  # noqa: E402
import assemble_site  # noqa: E402

MANIFEST_SCHEMA = "betterstyle.publication/1"
PRIVATE_KEYS = {"approval", "sale", "status_note", "by", "at", "source", "_lines", "extra_render_dirs", "editorial_record"}
PRIVATE_TEXT = [re.compile(p) for p in (r"[A-Za-z]:\\+Users", r"HUMAN_ENGINE", r"better_style_canonical_site", r"old_shop", r"studio@localhost")]
ALLOWED_DIRTY = ("catalogue/data/", "catalogue/media/", "catalogue/sources/", "publications/", "evidence/")


class PublishError(Exception):
    pass


# ---------- small helpers ----------
def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def write_json(p, obj):
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def read_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def who():
    try:
        return getpass.getuser()
    except Exception:
        return "owner"


class Project:
    """Paths of one project tree (CANON by default; a temp copy in tests)."""

    def __init__(self, root=CANON):
        self.root = Path(root)
        self.catalogue = self.root / "catalogue"
        self.cat_json = self.catalogue / "data/catalogue.json"
        self.deploy = self.root / "deploy"
        self.site = self.deploy / "site"
        self.pubs = self.root / "publications"
        self.log = self.pubs / "log.jsonl"
        self.current = self.pubs / "CURRENT.json"
        self.config = self.root / "config"


def run_cmd(cmd, cwd, env=None, timeout=1800):
    """Argument-list subprocess (never a shell). Returns (rc, combined output)."""
    e = dict(os.environ)
    e.update(env or {})
    e["PYTHONDONTWRITEBYTECODE"] = "1"
    r = subprocess.run(cmd, cwd=str(cwd), env=e, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, (r.stdout + r.stderr)


class Steps:
    """The external steps. Tests replace this with fakes."""

    def __init__(self, project):
        self.p = project

    def build(self, mode, provisional=False):
        py = [sys.executable, "-I"]
        env = {"BS_PREVIEW_PROVISIONAL": "1"} if provisional else {}
        out = ""
        args = ["--data-only"] if mode in ("fast", "data-only") else []
        for cmd in ([*py, str(self.p.catalogue / "build.py"), *args], [*py, str(self.p.catalogue / "make_dist.py")]):
            rc, o = run_cmd(cmd, self.p.root, env)
            out += f"$ {' '.join(Path(c).name if os.sep in c else c for c in cmd)}\n{o}\n"
            if rc:
                raise PublishError(f"build step failed (rc={rc}):\n{o[-1500:]}")
        return out

    def assemble(self, allow_provisional=False):
        return assemble_site.assemble(canon=self.p.root, allow_provisional=allow_provisional)

    def vercel(self, args, cwd):
        return run_cmd([shutil.which("npx") or "npx", "vercel", *args], cwd, timeout=900)

    def git(self, args):
        return run_cmd(["git", *args], self.p.root)


# ---------- state ----------
def load_current(p):
    return read_json(p.current) if p.current.is_file() else None


def next_data_version(p):
    n = 0
    if p.log.is_file():
        for line in p.log.read_text(encoding="utf-8").splitlines():
            try:
                n = max(n, json.loads(line).get("data_version", 0))
            except ValueError:
                pass
    return n + 1


def log_event(p, **ev):
    p.pubs.mkdir(parents=True, exist_ok=True)
    ev = {"at": now_iso(), "by": who(), **ev}
    with open(p.log, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def pub_dir(p, pid):
    d = p.pubs / pid
    if not d.is_dir():
        raise PublishError(f"unknown publication {pid!r}")
    return d


def load_manifest(p, pid):
    return read_json(pub_dir(p, pid) / "manifest.json")


def save_manifest(p, m):
    write_json(p.pubs / m["publication_id"] / "manifest.json", m)


def list_publications(p):
    out = []
    if p.pubs.is_dir():
        for d in sorted(p.pubs.iterdir()):
            if d.is_dir() and (d / "manifest.json").is_file():
                out.append(read_json(d / "manifest.json"))
    return out


def history(p):
    cur = load_current(p)
    return {"current": cur, "publications": [{k: m.get(k) for k in ("publication_id", "created_at", "status", "approval", "deployment", "previous_publication", "data_version")} | {"summary": m.get("proposed_changes_summary")} for m in list_publications(p)]}


# ---------- source revision, change records ----------
def source_revision(p, steps):
    rc, sha = steps.git(["rev-parse", "HEAD"])
    rc2, st = steps.git(["status", "--porcelain"])
    dirty = [l[3:] for l in st.splitlines()] if rc2 == 0 else []
    code = {}
    for rel in ("catalogue/build.py", "catalogue/make_dist.py", "catalogue/js/store.js", "catalogue/css/store.css", "catalogue/index.html",
                "tools/assemble_site.py", "tools/validate_catalogue.py", "tools/publishing.py", "config/site_vercel.json", "config/currencies.json"):
        f = p.root / rel
        if f.is_file():
            code[rel] = sha_file(f)
    em = p.catalogue / "engine_inputs/MANIFEST.json"
    return {"git_commit": sha.strip() if rc == 0 else None, "dirty_paths": dirty, "code_sha256": code,
            "engine_inputs_manifest_sha256": sha_file(em) if em.is_file() else None}


def flat(o, prefix=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from flat(v, f"{prefix}/{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from flat(v, f"{prefix}[{i}]")
    else:
        yield prefix, o


def diff_catalogues(old, new):
    """Field-level changes between two catalogue.json documents (status_note text excluded as commentary)."""
    a, b = dict(flat(old or {})), dict(flat(new))
    ch = []
    for k in sorted(set(a) | set(b)):
        if "status_note" in k or k.endswith("/_about"):
            continue
        if a.get(k, "<absent>") != b.get(k, "<absent>"):
            ch.append({"path": k, "old": a.get(k, "<absent>"), "new": b.get(k, "<absent>")})
    return ch


CUSTOMER_FIELDS = ("title", "price", "prices", "currency", "sale_status", "sizes", "description", "material_line", "material", "care", "measurements", "size_conversion", "mannequin_measurements", "new_in", "department")


def diff_public(old, new):
    """What a customer would see differently: products, orderability, commercial fields, colourways and stock."""
    om = {x["sku"]: x for x in (old or {}).get("products", [])}
    nm = {x["sku"]: x for x in new.get("products", [])}
    out = []
    for sku in sorted(set(om) | set(nm)):
        if sku not in om:
            out.append({"sku": sku, "change": "added to the public catalogue"})
        elif sku not in nm:
            out.append({"sku": sku, "change": "removed from the public catalogue"})
        else:
            for f in CUSTOMER_FIELDS:
                if om[sku].get(f) != nm[sku].get(f):
                    out.append({"sku": sku, "field": f, "old": om[sku].get(f), "new": nm[sku].get(f)})
            oc = {c["id"]: c.get("stock") for c in om[sku]["colourways"]}
            nc = {c["id"]: c.get("stock") for c in nm[sku]["colourways"]}
            if oc != nc:
                out.append({"sku": sku, "field": "colourways/stock", "old": oc, "new": nc})
    if (old or {}).get("site", {}).get("checkout") != new.get("site", {}).get("checkout"):
        out.append({"field": "site.checkout", "old": (old or {}).get("site", {}).get("checkout"), "new": new.get("site", {}).get("checkout")})
    return out


def orderable_skus(pub):
    return sorted(x["sku"] for x in pub.get("products", []) if x.get("sale_status") == "approved_for_sale" and x.get("price") is not None and x.get("sizes"))


def scan_public(site_dir, cat):
    """Independent check of the built public site against the draft it came from. Returns a list of problems."""
    site_dir = Path(site_dir)
    problems = []
    pj = site_dir / "catalogue/data/products.json"
    data = json.loads(pj.read_text(encoding="utf-8"))
    if data.get("preview_provisional"):
        problems.append("products.json is a provisional-preview build")
    snap = {x["sku"]: x for x in cat["products"]}
    default = (cat["site"].get("currency") or {}).get("default")
    for pr in data["products"]:
        bad = PRIVATE_KEYS & set(pr)
        if bad:
            problems.append(f"{pr['sku']}: private keys in public data: {sorted(bad)}")
        s = snap.get(pr["sku"])
        if not s:
            problems.append(f"{pr['sku']}: not in the draft")
            continue
        appr = lambda f: vc.field_approved(s, f)  # noqa: E731
        sale_ok = (s.get("sale") or {}).get("status") == "approved_for_sale"
        if pr.get("sale_status") == "approved_for_sale" and not sale_ok:
            problems.append(f"{pr['sku']}: public data says approved_for_sale but the draft is {(s.get('sale') or {}).get('status')}")
        for f in vc.GATED:
            if pr.get(f) not in (None, [], {}) and not appr(f):
                problems.append(f"{pr['sku']}: {f} is published but not approved in the draft")
        if pr.get("price") is not None:
            if not (sale_ok and appr("prices")) or (s.get("prices") or {}).get(default) != pr["price"]:
                problems.append(f"{pr['sku']}: published price does not match an approved {default} price")
        for cur, v in (pr.get("prices") or {}).items():
            if (s.get("prices") or {}).get(cur) != v:
                problems.append(f"{pr['sku']}: published {cur} price differs from the draft")
    for f in sorted(site_dir.rglob("*")):
        if f.is_file() and f.suffix.lower() in (".json", ".html", ".js", ".css", ".md", ".txt", ".svg"):
            if ".vercel" in f.relative_to(site_dir).parts:
                problems.append(f"{f.relative_to(site_dir)}: .vercel folder must not be uploaded")
                continue
            text = f.read_text(encoding="utf-8", errors="ignore")
            for rx in PRIVATE_TEXT:
                if rx.search(text):
                    problems.append(f"{f.relative_to(site_dir).as_posix()}: contains private/internal text matching {rx.pattern!r}")
    for f in site_dir.rglob(".env*"):
        problems.append(f"{f.relative_to(site_dir)}: environment file must not be uploaded")
    return problems


def zip_dir(src, dst):
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(Path(src).rglob("*")):
            if f.is_file() and ".vercel" not in f.relative_to(src).parts:
                z.write(f, f.relative_to(src).as_posix())


def restore_site(p, zip_path):
    """Replace deploy/site with the exact contents of a publication's site.zip (keeps the previous one as site.last_good)."""
    last = p.deploy / "site.last_good"
    if p.site.exists():
        if last.exists():
            shutil.rmtree(last)
        shutil.move(str(p.site), str(last))
    p.site.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(p.site)
    marker = p.deploy / ".preview_marker"
    if marker.exists():
        marker.unlink()


def draft_sha(p):
    return sha_file(p.cat_json)


# ---------- check / candidate ----------
def check(p):
    cat = read_json(p.cat_json)
    errors, warnings = vc.validate(cat, p.catalogue)
    return {"errors": errors, "warnings": warnings, "ok": not errors, "draft_sha256": draft_sha(p)}


def create_candidate(p=None, mode="full", steps=None, dry_run=False, say=print):
    """Validate, build, assemble and record a candidate publication for the CURRENT draft. Live site untouched."""
    p = p or Project()
    steps = steps or Steps(p)
    if mode not in ("full", "fast"):
        raise PublishError("mode must be 'full' or 'fast'")
    say("1/7 validating the draft ...")
    v = check(p)
    for w in v["warnings"]:
        say("   warning: " + w)
    if not v["ok"]:
        for e in v["errors"]:
            say("   ERROR: " + e)
        log_event(p, action="candidate_refused", reason="validation", errors=v["errors"])
        raise PublishError(f"validation failed with {len(v['errors'])} error(s); nothing was built")
    cat = read_json(p.cat_json)
    cur = load_current(p)
    prev_id = cur["id"] if cur else None
    prev_pub = None
    if prev_id:
        prev_pub = read_json(p.pubs / prev_id / "public_products.json")
        prev_cat = read_json(p.pubs / prev_id / "catalogue.snapshot.json")
    else:                                             # first publication: compare with the original catalogue as it was live
        base, base_cat = p.pubs / "_baseline/live_catalogue_products.json", p.pubs / "_baseline/original_catalogue.json"
        prev_pub = read_json(base) if base.is_file() else None
        prev_cat = read_json(base_cat) if base_cat.is_file() else None
    if dry_run:
        say("DRY RUN: would build (mode=%s), assemble, scan and record a candidate; previous publication: %s" % (mode, prev_id or "none (baseline: original catalogue)"))
        ch = diff_catalogues(prev_cat, cat)
        say(f"DRY RUN: {len(ch)} product-data field(s) differ from the last published draft")
        return {"dry_run": True, "validation": v, "catalogue_changes": ch}

    base_id = "PUB-" + time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    pid, n = base_id, 1
    while (p.pubs / pid).exists():                    # ids have one-second resolution: never reuse one
        n += 1
        pid = f"{base_id}-{n}"
    d = p.pubs / pid
    d.mkdir(parents=True)
    say(f"2/7 building ({mode}) ...")
    t0 = time.time()
    try:
        build_log = steps.build(mode)
        say("3/7 assembling deploy/site ...")
        steps.assemble()
    except Exception as e:
        shutil.rmtree(d, ignore_errors=True)
        log_event(p, action="candidate_failed", reason=str(e)[:500])
        raise
    build_secs = round(time.time() - t0, 1)
    (d / "build.log").write_text(build_log or "", encoding="utf-8", newline="\n")
    say("4/7 scanning the public output ...")
    problems = scan_public(p.site, cat)
    if problems:
        shutil.rmtree(d, ignore_errors=True)
        for x in problems:
            say("   PROBLEM: " + x)
        log_event(p, action="candidate_refused", reason="public scan", problems=problems)
        raise PublishError(f"public output scan found {len(problems)} problem(s); candidate discarded")
    say("5/7 recording the candidate ...")
    pj = read_json(p.site / "catalogue/data/products.json")
    shutil.copy2(p.cat_json, d / "catalogue.snapshot.json")
    write_json(d / "public_products.json", pj)
    shutil.copy2(p.deploy / "site_manifest.json", d / "site_manifest.json")
    zip_dir(p.site, d / "site.zip")
    cat_changes = diff_catalogues(prev_cat, cat)
    pub_changes = diff_public(prev_pub, pj)
    write_json(d / "validation.json", v)
    summary = {"catalogue_fields_changed": len(cat_changes), "customer_visible_changes": len(pub_changes),
               "orderable_before": orderable_skus(prev_pub or {}), "orderable_after": orderable_skus(pj)}
    write_json(d / "proposed_changes.json", {"compared_with": prev_id or "baseline: original catalogue as live on 2026-11-11", "catalogue_changes": cat_changes, "customer_visible_changes": pub_changes, "summary": summary})
    lines = [f"# Proposed changes in {pid}", "", f"Compared with: {prev_id or 'baseline (original catalogue as live on 2026-11-11)'}", "",
             f"- Product-data fields changed: {len(cat_changes)}", f"- Customer-visible changes: {len(pub_changes)}", f"- Orderable now: {summary['orderable_before'] or 'nothing'} -> after: {summary['orderable_after'] or 'nothing'}", "", "## Customer-visible changes"]
    lines += [f"- `{c.get('sku', 'site')}` {c.get('field', c.get('change'))}: {c.get('old', '')!r} -> {c.get('new', '')!r}" for c in pub_changes] or ["- none"]
    lines += ["", "## Product-data changes (catalogue.json)"] + [f"- `{c['path']}`: {c['old']!r} -> {c['new']!r}" for c in cat_changes[:200]] + ([f"- ... {len(cat_changes) - 200} more"] if len(cat_changes) > 200 else [])
    (d / "proposed_changes.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    tiles = sum(len(x["colourways"]) for x in pj["products"])
    manifest = {
        "schema": MANIFEST_SCHEMA, "publication_id": pid, "created_at": now_iso(), "created_by": who(), "status": "candidate",
        "source_revision": source_revision(p, steps),
        "product_data_version": {"data_version": next_data_version(p), "catalogue_sha256": draft_sha(p), "catalogue_schema": cat["schema"],
                                 "live_products": len(pj["products"]), "tiles": tiles, "public_products_sha256": sha_file(d / "public_products.json")},
        "build": {"result": "ok", "mode": mode, "seconds": build_secs, "site_files": len(read_json(d / "site_manifest.json")["files"]), "site_zip_sha256": sha_file(d / "site.zip")},
        "validation": {"result": "ok", "errors": 0, "warnings": len(v["warnings"]), "public_scan": "clean"},
        "deployment_target": {"type": "none yet", "vercel_project": read_json(p.config / "deployment.json")["vercel"]["project"] if (p.config / "deployment.json").is_file() else None},
        "deployment": None,
        "previous_publication": prev_id,
        "approval": {"status": "pending", "by": None, "at": None},
        "proposed_changes_summary": summary,
    }
    save_manifest(p, manifest)
    log_event(p, action="candidate_created", id=pid, mode=mode, data_version=manifest["product_data_version"]["data_version"], summary=summary)
    say(f"6/7 candidate {pid} recorded ({mode} build, {tiles} tiles, {len(pub_changes)} customer-visible change(s))")
    say("7/7 done. Review publications/%s/proposed_changes.md, preview deploy/site, then approve." % pid)
    return manifest


def preview(p=None, provisional=False, mode="fast", steps=None, say=print):
    """Build and assemble for LOCAL viewing only. Creates no publication record and never touches a live site.
    provisional=True shows DRAFT values (marked so deploy/ can never be published from it)."""
    p = p or Project()
    steps = steps or Steps(p)
    v = check(p)
    for e in v["errors"]:
        say("ERROR: " + e)
    if not v["ok"]:
        raise PublishError("validation failed; nothing was built")
    say(f"building preview ({'draft values' if provisional else 'approved values only'}, {mode}) ...")
    steps.build("fast" if provisional else mode, provisional=provisional)
    steps.assemble(allow_provisional=provisional)
    log_event(p, action="preview_built", provisional=provisional, mode=mode, draft_sha256=v["draft_sha256"])
    say("preview ready in deploy/site (serve it with tools/serve_site.py)")
    return v


# ---------- approve / publish ----------
def _fresh(p, m, what):
    if draft_sha(p) != m["product_data_version"]["catalogue_sha256"]:
        raise PublishError(f"the draft changed after {m['publication_id']} was built: make a new candidate before you {what}")
    cur = load_current(p)
    cur_id = cur["id"] if cur else None
    if cur_id != m["previous_publication"] and cur_id != m["publication_id"]:
        raise PublishError(f"another publication became current after {m['publication_id']} was built: make a new candidate")


def approve(p, pid, confirm, say=print):
    m = load_manifest(p, pid)
    if confirm != f"APPROVE {pid}":
        raise PublishError(f"typed confirmation required: APPROVE {pid}")
    if m["status"] != "candidate":
        raise PublishError(f"{pid} is {m['status']}, not a candidate")
    if m["build"]["mode"] != "full":
        raise PublishError("a fast (preview-only) build cannot be approved: make a full candidate")
    if m["validation"]["result"] != "ok":
        raise PublishError("validation did not pass")
    _fresh(p, m, "approve")
    m["status"], m["approval"] = "approved", {"status": "approved", "by": who(), "at": now_iso()}
    save_manifest(p, m)
    log_event(p, action="approved", id=pid)
    say(f"{pid} approved by {m['approval']['by']}")
    return m


def hosted_database_decided(d):
    """The owner's decision on the old hosted database: the word 'expendable' / 'preserved', or a signed record
    {'action': 'discard' | 'preserve', 'by', 'at'}. Anything else (None, another word, an unsigned record) is no decision."""
    if isinstance(d, dict):
        return d.get("action") in ("discard", "preserve") and bool(d.get("by")) and bool(d.get("at"))
    return d in ("expendable", "preserved")


def deployment_gates(p):
    cfg = read_json(p.config / "deployment.json")
    od = cfg.get("owner_decisions", {})
    blockers = []
    b4 = od.get("B4_live_deployment_approved")
    if not (isinstance(b4, dict) and b4.get("by") and b4.get("at")):
        blockers.append("B4: the owner has not recorded approval of the live deployment (config/deployment.json owner_decisions.B4_live_deployment_approved)")
    if not hosted_database_decided(od.get("hosted_database_decision")):
        blockers.append("The hosted database behind the Vercel project (DATABASE_URL) has no owner decision (owner_decisions.hosted_database_decision: 'expendable' or 'preserved', "
                        "or {'action': 'discard' | 'preserve', 'by', 'at'})")
    return cfg, blockers


def smoke(base, expect_products, expect_tiles, opener=None):
    """HTTP checks of a deployed site. Returns (ok, results)."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    op = opener or urllib.request.build_opener(NoRedirect)
    res, ok = [], True

    def get(path):
        try:
            r = op.open(base.rstrip("/") + path, timeout=30)
            return r.status, r.headers.get("Location"), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Location"), b""
        except Exception as e:  # noqa: BLE001
            return 0, str(e), b""
    for path, want, loc in (("/", 200, None), ("/catalogue/", 200, None), ("/catalogue", 307, "/catalogue/"), ("/shop/", 307, "/catalogue/"), ("/admin/login/", 307, "/")):
        s, l, _ = get(path)
        good = s == want and (loc is None or (l or "").endswith(loc))
        ok &= good
        res.append({"path": path, "status": s, "location": l, "ok": good})
    s, _, body = get("/catalogue/data/products.json")
    good = False
    if s == 200:
        try:
            d = json.loads(body)
            good = len(d["products"]) == expect_products and sum(len(x["colourways"]) for x in d["products"]) == expect_tiles
        except Exception:  # noqa: BLE001
            pass
    ok &= good
    res.append({"path": "/catalogue/data/products.json", "status": s, "ok": good, "sha256": sha_bytes(body) if s == 200 else None})
    return ok, res


def publish(p, pid, target="local", confirm="", steps=None, dry_run=False, say=print):
    p = p or Project()
    steps = steps or Steps(p)
    m = load_manifest(p, pid)
    if target not in ("local", "vercel"):
        raise PublishError("target must be 'local' or 'vercel'")
    if m["status"] != "approved" and not (target == "vercel" and m["status"] == "published" and (m.get("deployment") or {}).get("target") != "vercel"):
        raise PublishError(f"{pid} is {m['status']}: only an approved candidate can be published")
    blockers = []
    if target == "vercel":
        cfg, blockers = deployment_gates(p)
    if dry_run:
        say(f"DRY RUN: would verify {pid} is approved and fresh, restore deploy/site from its site.zip, and publish to target={target}.")
        if target == "vercel":
            d = cfg["vercel"]
            say(f"DRY RUN: would run (cwd deploy/): npx vercel deploy site --prod --skip-domain --yes --project {d['project']} --scope {d['scope']}  ; smoke-check ; npx vercel promote <url> --yes --scope {d['scope']} ; smoke-check {d['production_url']} ; on failure: npx vercel rollback <previous> --yes --scope {d['scope']}")
            for b in blockers:
                say("DRY RUN: WOULD BE REFUSED: " + b)
        return {"dry_run": True, "blockers": blockers}
    if confirm != "PUBLISH":
        raise PublishError("typed confirmation required: PUBLISH")
    _fresh(p, m, "publish")
    if (p.deploy / ".preview_marker").exists():
        raise PublishError("deploy/ is marked as a provisional preview; make a new candidate first")
    if blockers:
        raise PublishError("live deployment refused:\n - " + "\n - ".join(blockers))
    # make deploy/site exactly the approved build
    expected = read_json(p.pubs / pid / "site_manifest.json")["files"]
    restore_site(p, p.pubs / pid / "site.zip")
    actual = {f.relative_to(p.site).as_posix(): sha_file(f) for f in p.site.rglob("*") if f.is_file()}
    if actual != expected:
        raise PublishError("deploy/site does not match the approved candidate's manifest after restore")
    cur = load_current(p)
    deployment = {"target": target, "at": now_iso()}
    if target == "vercel":
        deployment = deploy_vercel(p, m, cfg, steps, say)
    steps.git(["add", "catalogue/data/catalogue.json", "publications"])
    rc, out = steps.git(["commit", "-m", f"Publish {pid} ({target})\n\nCo-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"])
    m["status"], m["deployment"] = "published", deployment
    m["deployment_target"] = {"type": target, **({"vercel_project": cfg["vercel"]["project"], "scope": cfg["vercel"]["scope"], "url": deployment.get("url")} if target == "vercel" else {})}
    save_manifest(p, m)
    if cur and cur["id"] != pid:
        old = load_manifest(p, cur["id"])
        old["status"] = "superseded"
        save_manifest(p, old)
    write_json(p.current, {"id": pid, "previous": cur["id"] if cur else None, "published_at": now_iso(), "target": target})
    log_event(p, action="published", id=pid, target=target, deployment=deployment)
    say(f"{pid} published (target={target})")
    return m


def deploy_vercel(p, m, cfg, steps, say):
    d = cfg["vercel"]
    scope, proj = d["scope"], d["project"]
    prev = None
    for x in reversed(list_publications(p)):
        if (x.get("deployment") or {}).get("target") == "vercel" and x["publication_id"] != m["publication_id"]:
            prev = x["deployment"].get("url")
            break
    prev = prev or cfg.get("current_production_before_replacement", {}).get("deployment_url")
    pj = read_json(p.pubs / m["publication_id"] / "public_products.json")
    n_prod, n_tiles = len(pj["products"]), sum(len(x["colourways"]) for x in pj["products"])
    say("deploying to Vercel (production environment, not yet aliased) ...")
    rc, out = steps.vercel(["deploy", "site", "--prod", "--skip-domain", "--yes", "--project", proj, "--scope", scope], p.deploy)
    urls = re.findall(r"https://[A-Za-z0-9.-]+\.vercel\.app", out)
    if rc or not urls:
        raise PublishError(f"vercel deploy failed (rc={rc}): {out[-800:]}")
    url = urls[-1]
    say("verifying the new deployment before it takes over the domain ...")
    ok, res = smoke(url, n_prod, n_tiles)
    if not ok and all(r["status"] in (401, 403) for r in res):
        say("   the deployment URL is access-protected; will verify after promotion")
        ok = True
    if not ok:
        raise PublishError("pre-promotion smoke check failed; the domain was NOT switched: " + json.dumps(res)[:600])
    rc, out = steps.vercel(["promote", url, "--yes", "--scope", scope], p.deploy)
    if rc:
        raise PublishError(f"vercel promote failed (rc={rc}): {out[-600:]}")
    ok, res = smoke(d["production_url"], n_prod, n_tiles)
    dep = {"target": "vercel", "project": proj, "scope": scope, "url": url, "alias": d["production_url"], "at": now_iso(), "previous_deployment_url": prev, "smoke": res, "smoke_ok": ok}
    if not ok:
        say("post-promotion smoke check FAILED: rolling back ...")
        rc, out = steps.vercel(["rollback", prev, "--yes", "--scope", scope], p.deploy) if prev else (1, "no previous deployment recorded")
        dep["rolled_back"] = rc == 0
        log_event(p, action="deploy_failed_rolled_back" if rc == 0 else "deploy_failed_ROLLBACK_FAILED", id=m["publication_id"], deployment=dep)
        raise PublishError("post-promotion smoke check failed; rollback " + ("done" if rc == 0 else "FAILED: " + out[-300:]))
    return dep


# ---------- rollback ----------
def rollback(p, target_id=None, confirm="", live=False, steps=None, dry_run=False, say=print):
    """Restore a previous, known-good publication: its product data and its built site, from its own records."""
    p = p or Project()
    steps = steps or Steps(p)
    cur = load_current(p)
    if not cur:
        raise PublishError("nothing has been published, so there is nothing to roll back to")
    tid = target_id or cur.get("previous")
    if not tid:
        raise PublishError("there is no earlier publication to restore")
    t = load_manifest(p, tid)
    if t["status"] not in ("published", "superseded"):
        raise PublishError(f"{tid} was never published ({t['status']}); only a previously published state can be restored")
    for f in ("catalogue.snapshot.json", "site.zip", "public_products.json"):
        if not (p.pubs / tid / f).is_file():
            raise PublishError(f"{tid} is missing {f}; cannot restore")
    if sha_file(p.pubs / tid / "site.zip") != t["build"]["site_zip_sha256"] or sha_file(p.pubs / tid / "catalogue.snapshot.json") != t["product_data_version"]["catalogue_sha256"]:
        raise PublishError(f"{tid}'s stored files do not match its manifest hashes: refusing to restore a damaged record")
    if dry_run:
        say(f"DRY RUN: would back up the current draft, restore catalogue.json from {tid}, restore deploy/site from its site.zip, set CURRENT={tid}" + (", and run `vercel rollback` for its recorded deployment" if live else ""))
        return {"dry_run": True, "target": tid}
    if confirm != "ROLLBACK":
        raise PublishError("typed confirmation required: ROLLBACK")
    backups = p.pubs / "_draft_backups"
    backups.mkdir(parents=True, exist_ok=True)
    bk = backups / (time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + ".catalogue.json")
    shutil.copy2(p.cat_json, bk)
    say(f"current draft backed up to {bk.relative_to(p.root).as_posix()}")
    shutil.copy2(p.pubs / tid / "catalogue.snapshot.json", p.cat_json)
    restore_site(p, p.pubs / tid / "site.zip")
    dep = t.get("deployment") or {}
    live_result = None
    if live:
        if dep.get("target") != "vercel" or not dep.get("url"):
            raise PublishError(f"{tid} has no recorded live deployment to restore")
        cfg, blockers = deployment_gates(p)
        if blockers:
            raise PublishError("live rollback refused:\n - " + "\n - ".join(blockers))
        rc, out = steps.vercel(["rollback", dep["url"], "--yes", "--scope", cfg["vercel"]["scope"]], p.deploy)
        live_result = {"rc": rc, "output": out[-400:]}
        if rc:
            raise PublishError("vercel rollback failed: " + out[-400:])
    cur_m = load_manifest(p, cur["id"])
    if cur["id"] != tid:
        cur_m["status"] = "rolled_back"
        cur_m["rolled_back_at"] = now_iso()
        save_manifest(p, cur_m)
    t["status"] = "published"
    save_manifest(p, t)
    write_json(p.current, {"id": tid, "previous": t.get("previous_publication"), "published_at": now_iso(), "target": "rollback", "restored_from_rollback_of": cur["id"]})
    log_event(p, action="rolled_back", id=tid, from_id=cur["id"], live=live, draft_backup=bk.name, live_result=live_result)
    say(f"restored {tid}; catalogue.json and deploy/site now match it. (catalogue/data/products.json is a build output: run build.py to refresh it.)")
    return t
