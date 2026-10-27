"""Exercise the real HTTP boundary, including rejected requests and static files."""

from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import URLError

from integrations.client import BrokerHttp, BrokerTimeout
from web_proxy.config import Config
from web_proxy.server import create_server

TOKEN = "proxy-test-token-" + "x" * 32
HEALTH = {"status": "ok", "instance_id": "inst_test"}
TASKS = {"items": [], "total": 0, "truncated": False, "instance_id": "inst_test"}
ERROR = {"reason_code": "AUDIT_UNAVAILABLE", "message": "audit unavailable", "request_id": "req_test", "instance_id": "inst_test"}


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.response = None
        self.delay = 0
        owner = self

        class Upstream(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append((self.path, self.headers.get("Authorization"), self.headers.get("Cookie")))
                time.sleep(owner.delay)
                status, payload = owner.response or (200, HEALTH if self.path == "/healthz" else TASKS)
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("X-Request-Id", "req_test")
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (OSError, TimeoutError):
                    pass

            def log_message(self, *_args):
                pass

        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        self.upthread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.upthread.start()
        self.temp = tempfile.TemporaryDirectory()
        self.dist = Path(self.temp.name) / "dist"
        self.dist.mkdir()
        (self.dist / "index.html").write_text("<html>frontend</html>", encoding="utf-8")
        (self.dist / "app.js").write_text("console.log('ok')", encoding="utf-8")
        (Path(self.temp.name) / "private.txt").write_text("not public", encoding="utf-8")
        self.proxy = create_server(Config(f"http://127.0.0.1:{self.upstream.server_port}", TOKEN, port=0, dist=self.dist, timeout=0.1))
        self.thread = threading.Thread(target=self.proxy.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        for server, thread in ((self.proxy, self.thread), (self.upstream, self.upthread)):
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.temp.cleanup()

    def request(self, path, method="GET", headers=None):
        connection = HTTPConnection("127.0.0.1", self.proxy.server_port, timeout=6)
        connection.request(method, path, headers=headers or {})
        response = connection.getresponse()
        raw = response.read()
        result = response.status, dict(response.getheaders()), raw
        connection.close()
        return result

    def reason(self, path, status, reason, **kwargs):
        code, headers, raw = self.request(path, **kwargs)
        self.assertEqual(code, status)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(json.loads(raw)["reason_code"], reason)

    def test_four_mappings_and_browser_credentials(self):
        self.assertEqual(self.request("/api/health")[0], 200)
        self.assertEqual(self.requests[-1], ("/healthz", None, None))
        self.request("/api/tasks?limit=2", headers={"Authorization": "Bearer browser-secret", "Cookie": "secret=browser"})
        self.assertEqual(self.requests[-1], ("/v1/admin/tasks?limit=2", "Bearer " + TOKEN, None))
        self.request("/api/tasks/task_123")
        self.assertEqual(self.requests[-1][0], "/v1/admin/tasks/task_123")
        self.request("/api/events?task_id=task_123&after=opaque%3Avalue%2B1&limit=3")
        self.assertEqual(self.requests[-1][0], "/v1/admin/events?limit=3&task_id=task_123&after=opaque%3Avalue%2B1")

    def test_query_rejections_never_contact_upstream(self):
        for path in ("/api/health?x=1", "/api/tasks?limit=0", "/api/tasks?limit=101", "/api/tasks?limit=1&limit=2", "/api/events?limit=501", "/api/events?result=denied", "/api/events?after=", "/api/events?task_id=../x", "/api/tasks?upstream=http://example.com"):
            with self.subTest(path=path):
                self.reason(path, 400, "INVALID_REQUEST")
        self.assertEqual(self.requests, [])

    def test_methods_and_unknown_routes(self):
        for method in ("POST", "PUT", "DELETE", "OPTIONS", "TRACE", "BREW"):
            self.reason("/api/tasks", 405, "METHOD_NOT_ALLOWED", method=method)
        status, headers, raw = self.request("/api/tasks", method="HEAD")
        self.assertEqual((status, headers["Allow"], raw), (405, "GET", b""))
        self.reason("/api/unknown", 404, "ROUTE_NOT_FOUND", method="POST")
        self.reason("/api", 404, "ROUTE_NOT_FOUND")
        self.assertEqual(self.requests, [])

    def test_host_origin(self):
        self.reason("/api/tasks", 403, "HOST_DENIED", headers={"Host": "evil.example"})
        self.reason("/api/tasks", 403, "ORIGIN_DENIED", headers={"Origin": "http://evil.example"})
        self.assertEqual(self.request("/api/health", headers={"Host": "127.0.0.1:5173", "Origin": "http://127.0.0.1:5173"})[0], 200)

    def test_preserve_upstream_errors_and_request_id(self):
        for status, reason in ((401, "INVALID_TOKEN"), (403, "ROLE_DENIED"), (503, "AUDIT_UNAVAILABLE"), (400, "INVALID_REQUEST")):
            self.response = status, {**ERROR, "reason_code": reason}
            code, headers, raw = self.request("/api/events?after=old_cursor")
            self.assertEqual(code, status)
            self.assertEqual(json.loads(raw), self.response[1])
            self.assertEqual(headers["X-Request-Id"], "req_test")

    def test_invalid_json_shape_and_redirect(self):
        for payload in (b"<html>oops</html>", b'{"status":"ok","instance_id":"inst_test","value":NaN}', {}, {"status": "ok"}):
            self.response = 200, payload
            self.reason("/api/health", 502, "BROKER_INVALID_RESPONSE")
        self.response = 302, HEALTH
        self.reason("/api/health", 502, "BROKER_INVALID_RESPONSE")

    def test_timeout(self):
        self.delay = 0.3
        self.reason("/api/health", 504, "BROKER_TIMEOUT")

    def test_disconnected_upstream(self):
        self.upstream.shutdown()
        self.upstream.server_close()
        # Windows can take about a second to report connection refusal. Do not
        # confuse a deliberately tiny test timeout with an observed refusal.
        self.proxy.shutdown()
        self.proxy.server_close()
        self.thread.join(timeout=3)
        self.proxy = create_server(Config(f"http://127.0.0.1:{self.upstream.server_port}", TOKEN, port=0, dist=self.dist, timeout=3))
        self.thread = threading.Thread(target=self.proxy.serve_forever, daemon=True)
        self.thread.start()
        self.reason("/api/health", 502, "BROKER_UNAVAILABLE")

    def test_static_and_history_fallback(self):
        self.assertEqual(self.request("/")[2], b"<html>frontend</html>")
        self.assertEqual(self.request("/tasks/task_123", headers={"Accept": "text/html"})[2], b"<html>frontend</html>")
        self.assertEqual(self.request("/app.js")[0], 200)
        self.reason("/missing.js", 404, "NOT_FOUND", headers={"Accept": "text/html"})
        self.reason("/assets/missing", 404, "NOT_FOUND", headers={"Accept": "text/html"})
        self.assertEqual(self.request("/app.js", method="HEAD")[2], b"")

    def test_path_traversal(self):
        for path in ("/../private.txt", "/%2e%2e/private.txt", "/%5cprivate.txt", "/C:/private.txt", "/bad%00name", "/bad%ZZ"):
            self.reason(path, 400, "INVALID_REQUEST")

    def test_external_symlink(self):
        try:
            (self.dist / "link.txt").symlink_to(Path(self.temp.name) / "private.txt")
        except OSError:
            self.skipTest("OS does not permit creating symlinks")
        self.reason("/link.txt", 403, "PATH_DENIED")

    def test_wrapped_timeout_is_typed(self):
        http = BrokerHttp("http://127.0.0.1:8080")
        with patch.object(http._opener, "open", side_effect=URLError(TimeoutError())):
            with self.assertRaises(BrokerTimeout):
                http.health()

    def test_real_rust_queries(self):
        from demo.fixture import prepare_fixture
        from demo.runtime import ManagedBroker, default_binary
        from integrations import AdminClient

        if not default_binary().is_file():
            self.skipTest("build release Rust before integration validation")
        fixture = Path(self.temp.name) / "rust-fixture"
        prepare_fixture(fixture)
        with ManagedBroker(default_binary(), fixture, TOKEN) as rust:
            http = BrokerHttp(rust.base_url)
            admin = AdminClient(http, TOKEN)
            task = admin.create_task("demo").require(201)
            with create_server(Config(rust.base_url, TOKEN, port=0, dist=None)) as proxy:
                worker = threading.Thread(target=proxy.serve_forever, daemon=True)
                worker.start()
                try:
                    transport = BrokerHttp(f"http://127.0.0.1:{proxy.server_port}")
                    self.assertEqual(transport.request("GET", "/api/health").require(200), http.health().require(200))
                    self.assertEqual(transport.request("GET", "/api/tasks").require(200), admin.tasks().require(200))
                    self.assertEqual(transport.request("GET", "/api/tasks/" + task["task_id"]).require(200)["phase"], "inspect")
                    self.assertEqual(transport.request("GET", "/api/events").require(200), admin.events().require(200))
                finally:
                    proxy.shutdown()
                    worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
