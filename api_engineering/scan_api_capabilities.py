#!/usr/bin/env python3
"""
scan_api_capabilities.py — Deterministic AST + Pathlib API Capability Census
Scans 411_CODE_LIBRARY and 411is_py for HTTP, REST, GraphQL, WebSocket, FastAPI,
Django, Auth/Security, and Webhook code, deduplicating by SHA-256 and emitting
atomic JSON and CSV crosswalk reports mapped to the api_engineering/ taxonomy.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# EC-01: Enforce UTF-8 stdio on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

TOOL_NAME = "scan_api_capabilities"

IGNORE_DIRS: frozenset[str] = frozenset({
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "env",
    ".env",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "dist",
    "build",
    "site-packages",
    "_pre_corpus_backup",
})

# (module_prefix) -> (category, weight)
IMPORT_RULES: dict[str, tuple[str, int]] = {
    # HTTP Clients & Transport
    "httpx": ("http_client", 4),
    "aiohttp": ("http_client", 4),
    "requests": ("http_client", 4),
    "curl_cffi": ("http_client", 4),
    "urllib3": ("http_client", 3),
    "urllib.request": ("http_client", 3),
    "http.client": ("http_client", 3),
    "http.server": ("http_client", 3),
    "tenacity": ("http_client", 2),
    "backoff": ("http_client", 2),
    "hishel": ("http_client", 3),
    "respx": ("http_client", 3),
    # REST & Schema / Serialization
    "fastapi": ("fastapi", 5),
    "starlette": ("fastapi", 4),
    "uvicorn": ("fastapi", 3),
    "pydantic": ("rest_api", 2),
    "msgspec": ("rest_api", 2),
    "orjson": ("rest_api", 2),
    "flask": ("rest_api", 4),
    "flask_restful": ("rest_api", 4),
    "litestar": ("rest_api", 4),
    "sanic": ("rest_api", 4),
    "tornado": ("rest_api", 3),
    "grpc": ("rest_api", 4),
    "schemathesis": ("rest_api", 3),
    "openapi_spec_validator": ("rest_api", 3),
    # Django & DRF
    "django": ("django", 5),
    "rest_framework": ("django", 5),
    "ninja": ("django", 4),
    # GraphQL
    "gql": ("graphql", 5),
    "graphene": ("graphql", 5),
    "graphene_django": ("graphql", 5),
    "strawberry": ("graphql", 5),
    "ariadne": ("graphql", 5),
    "sgqlc": ("graphql", 5),
    "tartiflette": ("graphql", 5),
    # WebSockets & Real-Time Streaming
    "websockets": ("websocket_streaming", 5),
    "wsproto": ("websocket_streaming", 5),
    "socketio": ("websocket_streaming", 4),
    "sse_starlette": ("websocket_streaming", 4),
    # Auth, Identity & Cryptography
    "jwt": ("auth_security", 4),
    "jose": ("auth_security", 4),
    "authlib": ("auth_security", 5),
    "oauthlib": ("auth_security", 5),
    "requests_oauthlib": ("auth_security", 5),
    "passlib": ("auth_security", 4),
    "cryptography": ("auth_security", 3),
    "hmac": ("auth_security", 2),
    "secrets": ("auth_security", 1),
    # Webhooks & Payment/Messaging Gateways
    "stripe": ("webhook", 4),
    "svix": ("webhook", 5),
}

ROUTE_DECORATOR_ATTRS: frozenset[str] = frozenset({
    "get", "post", "put", "patch", "delete", "options", "head",
    "api_route", "route", "api_view", "action", "websocket",
    "on_event", "middleware", "exception_handler",
})

AUTH_DECORATOR_NAMES: frozenset[str] = frozenset({
    "login_required", "permission_required", "user_passes_test",
    "csrf_exempt", "csrf_protect", "require_POST", "require_http_methods",
    "jwt_required", "requires_auth", "Depends", "Security",
})

GRAPHQL_DECORATOR_ATTRS: frozenset[str] = frozenset({
    "type", "input", "interface", "enum", "field", "mutation", "subscription",
})

API_BASE_CLASSES: dict[str, tuple[str, int]] = {
    "APIRouter": ("fastapi", 5),
    "FastAPI": ("fastapi", 5),
    "BaseHTTPMiddleware": ("fastapi", 4),
    "WebSocketEndpoint": ("websocket_streaming", 5),
    "APIView": ("django", 5),
    "GenericAPIView": ("django", 5),
    "ViewSet": ("django", 5),
    "ModelViewSet": ("django", 5),
    "ReadOnlyModelViewSet": ("django", 5),
    "Serializer": ("django", 4),
    "ModelSerializer": ("django", 4),
    "View": ("django", 3),
    "BaseHTTPRequestHandler": ("http_client", 4),
    "ObjectType": ("graphql", 4),
    "Mutation": ("graphql", 4),
    "OAuth2Session": ("auth_security", 5),
}

CALL_SIGNALS: dict[str, tuple[str, int]] = {
    "AsyncClient": ("http_client", 4),
    "ClientSession": ("http_client", 4),
    "Session": ("http_client", 3),
    "HTTPBasicAuth": ("auth_security", 4),
    "HTTPDigestAuth": ("auth_security", 4),
    "OAuth2PasswordBearer": ("auth_security", 5),
    "APIKeyHeader": ("auth_security", 5),
    "HTTPBearer": ("auth_security", 5),
    "compare_digest": ("auth_security", 4),
    "getResponseBody": ("websocket_streaming", 4),
    "requestWillBeSent": ("http_client", 3),
    "responseReceived": ("http_client", 3),
    "websocket_connect": ("websocket_streaming", 5),
}

STRING_PATTERNS: list[tuple[re.Pattern[str], str, int, str]] = [
    (re.compile(r"/webhooks?\b|x-hub-signature|x-signature|verif-hash|webhook[_-]?secret", re.I), "webhook", 4, "webhook_endpoint_or_sig"),
    (re.compile(r"/graphql\b|__schema\b|introspectionquery|persistedquery", re.I), "graphql", 4, "graphql_endpoint_or_query"),
    (re.compile(r"\bwss?://|text/event-stream|sec-websocket", re.I), "websocket_streaming", 4, "ws_or_sse_protocol"),
    (re.compile(r"/api/v\d+|openapi\.json|/swagger|/redoc|/wp-json/|/products\.json", re.I), "rest_api", 3, "rest_or_openapi_route"),
    (re.compile(r"socks5h?://|x-ratelimit|retry-after|if-none-match|\betag\b", re.I), "http_client", 3, "transport_header_or_proxy"),
    (re.compile(r"\bbearer\s+|oauth2?|client_credentials|authorization_code|\bpkce\b", re.I), "auth_security", 3, "oauth_or_bearer_token"),
]

# Maps primary category -> target api_engineering/ parent sub-domain
TARGET_SUBDOMAIN_MAP: dict[str, str] = {
    "http_client": "03_python_clients_and_async",
    "rest_api": "02_api_paradigms_and_schemas",
    "graphql": "02_api_paradigms_and_schemas",
    "websocket_streaming": "02_api_paradigms_and_schemas",
    "auth_security": "04_auth_and_session_lifecycle",
    "fastapi": "08_outbound_api_services",
    "django": "08_outbound_api_services",
    "webhook": "02_api_paradigms_and_schemas",
}

ALL_CATEGORIES: tuple[str, ...] = (
    "http_client",
    "rest_api",
    "graphql",
    "websocket_streaming",
    "fastapi",
    "django",
    "auth_security",
    "webhook",
)


@dataclass
class FileInspection:
    abs_path: Path
    root_name: str
    rel_path: str
    sha256: str
    line_count: int
    ownership: str
    priority_rank: int
    scores: dict[str, int] = field(default_factory=lambda: {c: 0 for c in ALL_CATEGORIES})
    imports: list[str] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    substrate_tags: list[str] = field(default_factory=list)
    parse_error: str | None = None

    @property
    def total_score(self) -> int:
        return sum(self.scores.values())

    @property
    def matched_categories(self) -> list[str]:
        return [c for c, s in sorted(self.scores.items(), key=lambda kv: (-kv[1], kv[0])) if s > 0]

    @property
    def primary_category(self) -> str:
        ranked = self.matched_categories
        return ranked[0] if ranked else "unclassified"

    @property
    def target_api_subdomain(self) -> str:
        # Promote CDP/network-capture scripts to 06_api_discovery_and_telemetry
        if any(tag in self.substrate_tags for tag in ("Cap_21_Playwright", "Cap_27_CDP", "Cap_28_BiDi")):
            return "06_api_discovery_and_telemetry"
        if "socks5h" in self.signals or "transport_header_or_proxy" in self.signals:
            if self.primary_category == "http_client" and self.scores["http_client"] <= 4:
                return "01_protocols_and_transport"
        return TARGET_SUBDOMAIN_MAP.get(self.primary_category, "00_index_and_411_crosswalk")


class ApiAstVisitor(ast.NodeVisitor):
    """Deterministic AST analyzer extracting imports, routes, classes, calls, and literals."""

    def __init__(self, inspection: FileInspection) -> None:
        self.ins = inspection
        self._imports_set: set[str] = set()
        self._endpoints_set: set[str] = set()
        self._symbols_set: set[str] = set()
        self._signals_set: set[str] = set()
        self._substrate_set: set[str] = set()

    def _add_score(self, category: str, points: int, signal: str | None = None) -> None:
        self.ins.scores[category] = self.ins.scores.get(category, 0) + points
        if signal:
            self._signals_set.add(signal)

    def _match_module(self, mod_name: str) -> None:
        if not mod_name:
            return
        self._imports_set.add(mod_name)
        parts = mod_name.split(".")
        for i in range(len(parts), 0, -1):
            prefix = ".".join(parts[:i])
            if prefix in IMPORT_RULES:
                cat, weight = IMPORT_RULES[prefix]
                self._add_score(cat, weight, f"import:{prefix}")
                break

        if mod_name.startswith("playwright"):
            self._substrate_set.add("Cap_21_Playwright")
        elif mod_name.startswith("google.genai"):
            self._substrate_set.add("AI_GenAI_SDK")
        elif mod_name == "sqlite3":
            self._substrate_set.add("EC-22_SQLite")
        elif mod_name == "subprocess":
            self._substrate_set.add("EC-04_Subprocess")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._match_module(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self._match_module(node.module)
            for alias in node.names:
                self._match_module(f"{node.module}.{alias.name}")
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        base_names: list[str] = []
        for base in node.bases:
            bname = self._expr_name(base)
            if bname:
                base_names.append(bname)
                short = bname.split(".")[-1]
                if short in API_BASE_CLASSES:
                    cat, weight = API_BASE_CLASSES[short]
                    self._add_score(cat, weight, f"class:{node.name}({short})")
                    self._symbols_set.add(f"class {node.name}({short})")
        if any(k in node.name.lower() for k in ("webhook", "graphql", "oauth", "client", "router", "api")):
            self._symbols_set.add(f"class {node.name}")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._inspect_function(node, is_async=False)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._inspect_function(node, is_async=True)
        self.generic_visit(node)

    def _inspect_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, is_async: bool) -> None:
        prefix = "async def" if is_async else "def"
        fname_lower = node.name.lower()

        if "webhook" in fname_lower:
            self._add_score("webhook", 3, f"func:{node.name}")
            self._symbols_set.add(f"{prefix} {node.name}()")
        elif "graphql" in fname_lower:
            self._add_score("graphql", 3, f"func:{node.name}")
            self._symbols_set.add(f"{prefix} {node.name}()")
        elif any(k in fname_lower for k in ("oauth", "verify_sig", "hmac", "jwt", "refresh_token")):
            self._add_score("auth_security", 3, f"func:{node.name}")
            self._symbols_set.add(f"{prefix} {node.name}()")

        for dec in node.decorator_list:
            dname = self._expr_name(dec)
            if not dname:
                continue
            short = dname.split(".")[-1]

            if short in ROUTE_DECORATOR_ATTRS:
                route_path = self._first_str_arg(dec) or "<dynamic>"
                method = short.upper()
                if short == "websocket":
                    self._add_score("websocket_streaming", 5, f"ws_route:{route_path}")
                elif "django" in self._imports_set or "rest_framework" in self._imports_set or short in {"api_view", "action"}:
                    self._add_score("django", 4, f"django_route:{method} {route_path}")
                elif "fastapi" in self._imports_set or "starlette" in self._imports_set:
                    self._add_score("fastapi", 4, f"fastapi_route:{method} {route_path}")
                else:
                    self._add_score("rest_api", 4, f"route:{method} {route_path}")

                self._endpoints_set.add(f"{method} {route_path} -> {node.name}()")
                self._symbols_set.add(f"{prefix} {node.name}()")

                if "webhook" in route_path.lower():
                    self._add_score("webhook", 4, f"webhook_route:{route_path}")
                if "graphql" in route_path.lower():
                    self._add_score("graphql", 4, f"graphql_route:{route_path}")

            elif short in AUTH_DECORATOR_NAMES:
                self._add_score("auth_security", 3, f"auth_dec:{short}")
                self._symbols_set.add(f"{prefix} {node.name}()")

            elif short in GRAPHQL_DECORATOR_ATTRS and any(
                g in dname.lower() for g in ("strawberry", "graphene", "ariadne")
            ):
                self._add_score("graphql", 4, f"gql_dec:{dname}")
                self._symbols_set.add(f"{prefix} {node.name}()")

    def visit_Call(self, node: ast.Call) -> None:
        cname = self._expr_name(node.func)
        if cname:
            short = cname.split(".")[-1]
            if short in CALL_SIGNALS:
                cat, weight = CALL_SIGNALS[short]
                self._add_score(cat, weight, f"call:{cname}")

            # Detect 411 Execution Substrate conventions
            if short == "replace" and "os.replace" in cname:
                self._substrate_set.add("EC-05_AtomicWrite")
            elif cname in {"json.dumps", "orjson.dumps"}:
                self._substrate_set.add("EC-02_JSON")
            elif "new_cdp_session" in cname or "Network.getResponseBody" in cname:
                self._substrate_set.add("Cap_27_CDP")
            elif short in {"get", "post", "put", "patch", "delete", "request", "stream"}:
                if any(h in cname.lower() for h in ("httpx", "requests", "aiohttp", "session", "client")):
                    self._add_score("http_client", 2, f"http_call:{cname}")

        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and 4 <= len(node.value) <= 500:
            val = node.value
            for pat, cat, weight, label in STRING_PATTERNS:
                if pat.search(val):
                    self._add_score(cat, weight, label)
            if "Network.getResponseBody" in val or "Network.requestWillBeSent" in val:
                self._substrate_set.add("Cap_27_CDP")
            if "socks5h://" in val.lower():
                self._signals_set.add("socks5h")
        self.generic_visit(node)

    def finalize(self) -> None:
        self.ins.imports = sorted(self._imports_set)[:30]
        self.ins.endpoints = sorted(self._endpoints_set)[:25]
        self.ins.symbols = sorted(self._symbols_set)[:25]
        self.ins.signals = sorted(self._signals_set)[:25]
        self.ins.substrate_tags = sorted(self._substrate_set)

    @staticmethod
    def _expr_name(expr: ast.AST) -> str:
        if isinstance(expr, ast.Call):
            return ApiAstVisitor._expr_name(expr.func)
        if isinstance(expr, ast.Name):
            return expr.id
        if isinstance(expr, ast.Attribute):
            parent = ApiAstVisitor._expr_name(expr.value)
            return f"{parent}.{expr.attr}" if parent else expr.attr
        return ""

    @staticmethod
    def _first_str_arg(expr: ast.AST) -> str | None:
        if isinstance(expr, ast.Call) and expr.args:
            first = expr.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                return first.value
        return None


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def classify_ownership(root_name: str, rel_posix: str) -> tuple[str, int]:
    upper = f"{root_name}/{rel_posix}".upper()
    if "411IS_PY" in upper:
        return "owned_corpus", 1
    if "1_MINE" in upper:
        return "owned_mine", 2
    if "4_THIRD_PARTY" in upper:
        return "extracted_third_party", 3
    if "3_LEARNING" in upper:
        return "learning_reference", 4
    if "2_OLDER_VERSIONS" in upper:
        return "older_version", 5
    return "workspace_other", 6


def inspect_python_file(path: Path, root: Path, min_score: int) -> FileInspection | None:
    rel_posix = path.relative_to(root).as_posix()
    ownership, rank = classify_ownership(root.name, rel_posix)

    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return None

    if not raw_bytes.strip():
        return None

    sha = hashlib.sha256(raw_bytes).hexdigest()
    source_text = raw_bytes.decode("utf-8", errors="replace")
    line_count = source_text.count("\n") + 1

    ins = FileInspection(
        abs_path=path.resolve(),
        root_name=root.name,
        rel_path=rel_posix,
        sha256=sha,
        line_count=line_count,
        ownership=ownership,
        priority_rank=rank,
    )

    # Path-level signal boost (e.g., urls.py, views.py, webhook*.py, graphql*.py)
    fname_lower = path.name.lower()
    if fname_lower in {"urls.py", "views.py", "serializers.py", "asgi.py", "wsgi.py"}:
        ins.scores["django"] += 2
        ins.signals.append(f"filename:{fname_lower}")
    if "webhook" in rel_posix.lower():
        ins.scores["webhook"] += 3
        ins.signals.append("path:webhook")
    if "graphql" in rel_posix.lower():
        ins.scores["graphql"] += 3
        ins.signals.append("path:graphql")

    try:
        tree = ast.parse(source_text, filename=str(path))
        visitor = ApiAstVisitor(ins)
        visitor.visit(tree)
        visitor.finalize()
    except SyntaxError as exc:
        ins.parse_error = f"SyntaxError:{exc.lineno}"
        return None

    if ins.total_score < min_score:
        return None
    return ins


def atomic_write_text(target: Path, content: str) -> None:
    """EC-05: Atomic write via temporary sibling file + os.replace()."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    with tmp_path.open("w", encoding="utf-8", newline="") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, target)


def discover_default_roots() -> list[Path]:
    home = Path.home()
    candidates = [
        home / "Desktop" / "411_CODE_LIBRARY",
        home / "Desktop" / "411is_py",
        Path.cwd() / "411_CODE_LIBRARY",
        Path.cwd() / "411is_py",
    ]
    found: list[Path] = []
    seen_resolved: set[Path] = set()
    for c in candidates:
        if c.exists() and c.is_dir():
            r = c.resolve()
            if r not in seen_resolved:
                seen_resolved.add(r)
                found.append(r)
    return found


def run_census(roots: list[Path], out_dir: Path, min_score: int) -> dict[str, Any]:
    total_py_scanned = 0
    matched_raw: list[FileInspection] = []

    for root in roots:
        if not root.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            # Deterministic sorted walk + prune noise directories in-place
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS and not d.endswith(".egg-info"))
            for fname in sorted(filenames):
                if not fname.endswith(".py"):
                    continue
                total_py_scanned += 1
                fpath = Path(dirpath) / fname
                hit = inspect_python_file(fpath, root, min_score=min_score)
                if hit is not None:
                    matched_raw.append(hit)

    # Deduplicate by SHA-256 while preserving ownership priority & full provenance
    by_hash: dict[str, list[FileInspection]] = {}
    for item in matched_raw:
        by_hash.setdefault(item.sha256, []).append(item)

    dedup_records: list[dict[str, Any]] = []
    category_counts: dict[str, int] = {c: 0 for c in ALL_CATEGORIES}
    subdomain_counts: dict[str, int] = {}

    for sha in sorted(by_hash.keys()):
        group = sorted(by_hash[sha], key=lambda x: (x.priority_rank, x.root_name, x.rel_path))
        canonical = group[0]
        duplicates = [f"{g.root_name}/{g.rel_path}" for g in group[1:]]

        for cat in canonical.matched_categories:
            category_counts[cat] += 1
        sub = canonical.target_api_subdomain
        subdomain_counts[sub] = subdomain_counts.get(sub, 0) + 1

        dedup_records.append({
            "sha256": canonical.sha256,
            "canonical_root": canonical.root_name,
            "canonical_relpath": canonical.rel_path,
            "canonical_abspath": str(canonical.abs_path),
            "ownership": canonical.ownership,
            "line_count": canonical.line_count,
            "primary_category": canonical.primary_category,
            "matched_categories": canonical.matched_categories,
            "category_scores": {k: v for k, v in canonical.scores.items() if v > 0},
            "total_score": canonical.total_score,
            "target_api_subdomain": sub,
            "substrate_tags": canonical.substrate_tags,
            "endpoints": canonical.endpoints,
            "symbols": canonical.symbols,
            "imports": canonical.imports,
            "signals": canonical.signals,
            "duplicate_count": len(duplicates),
            "duplicate_locations": duplicates,
        })

    # Sort final crosswalk by total_score descending, then canonical path
    dedup_records.sort(key=lambda r: (-r["total_score"], r["canonical_root"], r["canonical_relpath"]))

    json_path = out_dir / "api_capability_crosswalk.json"
    csv_path = out_dir / "api_capability_crosswalk.csv"

    report_payload = {
        "schema_version": "1.0.0",
        "scanned_roots": [str(r) for r in roots],
        "min_score_threshold": min_score,
        "summary": {
            "total_py_files_scanned": total_py_scanned,
            "raw_api_matches": len(matched_raw),
            "unique_api_files": len(dedup_records),
            "duplicates_collapsed": len(matched_raw) - len(dedup_records),
            "by_category": category_counts,
            "by_target_api_subdomain": dict(sorted(subdomain_counts.items())),
        },
        "records": dedup_records,
    }

    atomic_write_text(json_path, json.dumps(report_payload, indent=2, ensure_ascii=False))

    # Write flat CSV crosswalk
    import io
    csv_buf = io.StringIO()
    fieldnames = [
        "sha256",
        "ownership",
        "canonical_root",
        "canonical_relpath",
        "line_count",
        "primary_category",
        "matched_categories",
        "total_score",
        "target_api_subdomain",
        "substrate_tags",
        "endpoints",
        "symbols",
        "imports",
        "duplicate_count",
        "duplicate_locations",
    ]
    writer = csv.DictWriter(csv_buf, fieldnames=fieldnames)
    writer.writeheader()
    for rec in dedup_records:
        writer.writerow({
            "sha256": rec["sha256"],
            "ownership": rec["ownership"],
            "canonical_root": rec["canonical_root"],
            "canonical_relpath": rec["canonical_relpath"],
            "line_count": rec["line_count"],
            "primary_category": rec["primary_category"],
            "matched_categories": "|".join(rec["matched_categories"]),
            "total_score": rec["total_score"],
            "target_api_subdomain": rec["target_api_subdomain"],
            "substrate_tags": "|".join(rec["substrate_tags"]),
            "endpoints": " ; ".join(rec["endpoints"]),
            "symbols": " ; ".join(rec["symbols"]),
            "imports": "|".join(rec["imports"]),
            "duplicate_count": rec["duplicate_count"],
            "duplicate_locations": "|".join(rec["duplicate_locations"]),
        })
    atomic_write_text(csv_path, csv_buf.getvalue())

    return {
        "json_report": str(json_path.resolve()),
        "csv_report": str(csv_path.resolve()),
        "summary": report_payload["summary"],
    }


def main() -> None:
    t0 = time.perf_counter()
    parser = argparse.ArgumentParser(
        description="Deterministic AST + Pathlib scanner for HTTP, REST, GraphQL, WebSocket, FastAPI, Django, Auth, and Webhook code."
    )
    parser.add_argument(
        "--roots",
        nargs="+",
        type=Path,
        default=None,
        help="Directories to scan (defaults to Desktop/4041_MechMix/01_code_library and Desktop/4041_MechMix/01_code_library/80_reference_and_learning/411is_py).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("api_engineering") / "00_index_and_411_crosswalk",
        help="Destination directory for api_capability_crosswalk.json and .csv.",
    )
    parser.add_argument(
        "--min-score",
        type=int,
        default=3,
        help="Minimum AST signal score required to include a file (default: 3).",
    )
    args = parser.parse_args()

    roots = [r.resolve() for r in args.roots] if args.roots else discover_default_roots()
    if not roots:
        envelope = {
            "ok": False,
            "tool": TOOL_NAME,
            "error": "No target roots found. Pass --roots <path_to_411_CODE_LIBRARY> <path_to_411is_py>.",
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        }
        print(json.dumps(envelope, ensure_ascii=False))
        sys.exit(1)

    result = run_census(roots=roots, out_dir=args.out_dir, min_score=args.min_score)
    envelope = {
        "ok": True,
        "tool": TOOL_NAME,
        "data": result,
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
    }
    # EC-02: Single-line JSON envelope on stdout
    print(json.dumps(envelope, ensure_ascii=False))


if __name__ == "__main__":
    main()
