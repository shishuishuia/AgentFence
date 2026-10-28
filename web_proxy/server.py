"""Four GET mappings and confined SPA hosting, with no browser credentials."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import PurePosixPath
import re
from urllib.parse import parse_qsl, unquote, urlsplit
import uuid

from integrations.client import (
    AdminClient, BrokerHttp, BrokerInvalidResponse, BrokerTimeout, BrokerUnavailable,
)
from .config import Config

IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def valid_shape(kind: str, status: int, body: dict) -> bool:
    """Check the contract boundary without inventing missing success fields."""
    if status != 200:
        return status >= 400 and all(isinstance(body.get(k), str) for k in ("request_id", "reason_code", "message")) and (body.get("instance_id") is None or isinstance(body.get("instance_id"), str))
    if not isinstance(body.get("instance_id"), str):
        return False
    if kind == "health":
        return body.get("status") == "ok"
    if kind == "tasks":
        return isinstance(body.get("items"), list) and type(body.get("total")) is int and type(body.get("truncated")) is bool and all(isinstance(t, dict) and all(isinstance(t.get(k), str) for k in ("task_id", "workspace_id", "phase", "created_at", "updated_at")) for t in body["items"])
    if kind == "task":
        return all(isinstance(body.get(k), str) for k in ("task_id", "workspace_id", "phase", "created_at", "updated_at", "server_time")) and isinstance(body.get("allowed_paths"), list) and all(isinstance(p, str) for p in body["allowed_paths"]) and isinstance(body.get("grants"), list) and all(isinstance(g, dict) and all(isinstance(g.get(k), str) for k in ("grant_id", "task_id", "tool", "path", "expires_at", "status")) and type(g.get("remaining_uses")) is int and type(g.get("revoked")) is bool for g in body["grants"])
    return isinstance(body.get("events"), list) and isinstance(body.get("next_cursor"), str) and type(body.get("has_more")) is bool and all(isinstance(e, dict) and type(e.get("event_id")) is int and all(isinstance(e.get(k), str) for k in ("instance_id", "request_id", "timestamp", "event_type", "reason_code")) and type(e.get("allowed")) is bool and type(e.get("executed")) is bool for e in body["events"])


def create_server(config: Config, *, demo=None) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "AgentFenceProxy"

        def log_message(self, *_args):
            pass  # No URLs, credentials, response contents or host paths in logs.

        def __getattr__(self, name):
            if name.startswith("do_"):
                return self.do_GET
            raise AttributeError(name)

        def reply(self, status, data, content_type="application/json; charset=utf-8", request_id=None, allow=None):
            raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode() if isinstance(data, dict) else data
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if request_id:
                self.send_header("X-Request-Id", request_id)
            if allow:
                self.send_header("Allow", allow)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(raw)

        def error(self, status, reason, message, allow=None):
            request_id = "proxy_" + uuid.uuid4().hex
            self.reply(status, {"request_id": request_id, "instance_id": None, "reason_code": reason, "message": message}, request_id=request_id, allow=allow)

        def handle_local(self):
            self.connection.settimeout(6)
            hosts = {f"127.0.0.1:{self.server.server_port}", "127.0.0.1:5173"}
            if len(self.headers.get_all("Host", [])) != 1 or self.headers.get("Host") not in hosts:
                return self.error(403, "HOST_DENIED", "Host is not an allowed local address")
            origins = self.headers.get_all("Origin", [])
            if len(origins) > 1 or (origins and origins[0] not in {"http://" + h for h in hosts}):
                return self.error(403, "ORIGIN_DENIED", "Origin is not an allowed local address")
            if len(self.path) > 8192 or re.search(r"%(?![a-fA-F0-9]{2})", self.path):
                return self.error(400, "INVALID_REQUEST", "Invalid request target")
            parsed = urlsplit(self.path)
            if parsed.scheme or parsed.netloc or parsed.fragment or not self.path.startswith("/") or self.path.startswith("//"):
                return self.error(400, "INVALID_REQUEST", "Only local paths are accepted")
            try:
                path = unquote(parsed.path, errors="strict")
            except UnicodeError:
                return self.error(400, "INVALID_REQUEST", "Invalid path encoding")
            if "\\" in path or any(ord(c) < 32 or ord(c) == 127 for c in path) or any(p in (".", "..") or ":" in p for p in path.split("/")):
                return self.error(400, "INVALID_REQUEST", "Invalid path")
            if path == "/api" or path.startswith("/api/"):
                return self.api(path, parsed.query)
            if self.command not in ("GET", "HEAD"):
                return self.error(405, "METHOD_NOT_ALLOWED", "Only GET and HEAD are supported", "GET, HEAD")
            return self.static(path)

        def api(self, path, query):
            if path in ("/api/demo/state", "/api/demo/control") and demo is not None:
                return self.demo_api(path, query)
            match = re.fullmatch(r"/api/tasks/([A-Za-z0-9_-]{1,128})", path)
            kind = {"/api/health": "health", "/api/tasks": "tasks", "/api/events": "events"}.get(path)
            if match:
                kind = "task"
            if kind is None:
                return self.error(404, "ROUTE_NOT_FOUND", "Unknown API route")
            if self.command != "GET":
                return self.error(405, "METHOD_NOT_ALLOWED", "API queries only support GET", "GET")
            try:
                pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True, max_num_fields=8, errors="strict")
                params = dict(pairs)
                allowed = {"tasks": {"limit"}, "events": {"task_id", "after", "limit"}}.get(kind, set())
                if len(params) != len(pairs) or not params.keys() <= allowed:
                    raise ValueError
                if "limit" in params and (not re.fullmatch(r"[0-9]{1,3}", params["limit"]) or not 1 <= int(params["limit"]) <= (500 if kind == "events" else 100)):
                    raise ValueError
                if "task_id" in params and not IDENTIFIER.fullmatch(params["task_id"]):
                    raise ValueError
                if "after" in params and (not params["after"] or len(params["after"]) > 1024 or any(ord(c) < 32 or ord(c) == 127 for c in params["after"])):
                    raise ValueError
            except (ValueError, UnicodeError):
                return self.error(400, "INVALID_REQUEST", "Unknown, duplicate or invalid query parameters")
            try:
                # Each handler owns a transport; opener state is never shared across threads.
                http = BrokerHttp(config.upstream, timeout=config.timeout)
                admin = AdminClient(http, config.token)
                if kind == "health":
                    response = http.health()
                elif kind == "tasks":
                    response = admin.tasks(limit=int(params.get("limit", 50)))
                elif kind == "task":
                    response = admin.task(match.group(1))
                else:
                    response = admin.events(task_id=params.get("task_id"), after=params.get("after"), limit=int(params.get("limit", 100)))
                if not valid_shape(kind, response.status, response.payload):
                    raise BrokerInvalidResponse("invalid response shape")
                self.reply(response.status, response.payload, request_id=response.request_id)
            except BrokerTimeout:
                self.error(504, "BROKER_TIMEOUT", "Rust broker request timed out")
            except BrokerUnavailable:
                self.error(502, "BROKER_UNAVAILABLE", "Rust broker is unavailable")
            except BrokerInvalidResponse:
                self.error(502, "BROKER_INVALID_RESPONSE", "Rust broker returned an invalid response")

        def demo_api(self, path, query):
            # Only the explicitly started, fixed-fixture demo runner installs this capability.
            if query:
                return self.error(400, "INVALID_REQUEST", "Demo routes do not accept query parameters")
            expected = "GET" if path.endswith("/state") else "POST"
            if self.command != expected:
                return self.error(405, "METHOD_NOT_ALLOWED", "Unsupported demo method", expected)
            if expected == "GET":
                return self.reply(200, demo.snapshot())
            origins = {f"http://127.0.0.1:{self.server.server_port}", "http://127.0.0.1:5173"}
            if self.headers.get("Origin") not in origins or self.headers.get_all("X-AgentFence-Demo", []) != ["1"]:
                return self.error(403, "DEMO_ORIGIN_REQUIRED", "Use the local demo console")
            lengths = self.headers.get_all("Content-Length", [])
            task_enabled = getattr(demo, "accepts_task", False)
            if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,5}", lengths[0]) or not 0 < int(lengths[0]) <= (8192 if task_enabled else 256):
                return self.error(400, "INVALID_REQUEST", "Invalid demo body length")
            if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
                return self.error(415, "INVALID_REQUEST", "Demo controls require JSON")
            try:
                def unique(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError
                        result[key] = value
                    return result
                body = json.loads(self.rfile.read(int(lengths[0])), object_pairs_hook=unique)
                if not isinstance(body, dict) or not {"action", "request_id"} <= set(body) or not set(body) <= {"action", "request_id", "task"} or body["action"] not in ("start", "pause", "resume") or not isinstance(body["request_id"], str) or not IDENTIFIER.fullmatch(body["request_id"]):
                    raise ValueError
                if "task" in body and (not task_enabled or body["action"] != "start" or not isinstance(body["task"], str) or not body["task"].strip() or len(body["task"]) > 1000 or len(body["task"].encode()) > 4000):
                    raise ValueError
            except (ValueError, UnicodeError):
                return self.error(400, "INVALID_REQUEST", "Only fixed demo actions are accepted")
            options = {"task": body["task"]} if "task" in body else {}
            status, state = demo.control(body["action"], body["request_id"], **options)
            self.reply(status, state)

        def static(self, path):
            if config.dist is None:
                return self.error(404, "NOT_FOUND", "Frontend hosting is disabled in dev mode")
            root = config.dist.resolve()
            target = (root / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
            if not target.is_relative_to(root):
                return self.error(403, "PATH_DENIED", "Static path is outside the frontend")
            if not target.is_file():
                if PurePosixPath(path).suffix or path.startswith("/assets/") or "text/html" not in self.headers.get("Accept", ""):
                    return self.error(404, "NOT_FOUND", "Static resource not found")
                target = (root / "index.html").resolve()
                if not target.is_relative_to(root):
                    return self.error(403, "PATH_DENIED", "Frontend entry is outside the static root")
            try:
                self.reply(200, target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            except OSError:
                self.error(404, "NOT_FOUND", "Static resource not found")

        def do_GET(self):
            try:
                self.handle_local()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, TimeoutError):
                pass
            except ValueError:
                self.error(400, "INVALID_REQUEST", "Invalid request target")

        do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = do_CONNECT = do_GET

    return ThreadingHTTPServer(("127.0.0.1", config.port), Handler)
