"""Exercise the HTTP wire contract, not just mocked client method calls."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
import unittest

from integrations import AdminClient, AgentClient, ApiResponse, BrokerHttp, ClientError
from integrations.client import MAX_RESPONSE_BYTES


class WireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.requests = []
        cls.reply = (200, {"status": "ok", "instance_id": "inst_test"}, {})

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.handle_request()

            do_POST = do_GET
            do_DELETE = do_GET

            def handle_request(self):
                size = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(size)
                cls.requests.append((self.command, self.path, self.headers.get("Authorization"), body))
                status, payload, headers = cls.reply
                if self.path == "/slow":
                    time.sleep(0.15)
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                if "Content-Length" not in headers:
                    self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

            def log_message(self, *_args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def setUp(self):
        self.requests.clear()
        type(self).reply = (200, {"status": "ok", "instance_id": "inst_test"}, {})
        self.http = BrokerHttp(self.url, timeout=2)
        self.admin = AdminClient(self.http, "admin-test-secret")
        self.agent = AgentClient(self.http, "task_test", "agent-test-secret")

    def test_read_omits_write_fields_and_uses_agent_identity(self):
        self.agent.read_file("src/main.rs")
        method, path, auth, body = self.requests[0]
        self.assertEqual((method, path, auth), ("POST", "/v1/tool/execute", "Bearer agent-test-secret"))
        self.assertEqual(json.loads(body), {"task_id": "task_test", "tool": "read_file", "path": "src/main.rs"})

    def test_empty_write_and_optional_grant_are_not_null(self):
        self.agent.write_file("src/main.rs", "")
        first = json.loads(self.requests[-1][3])
        self.assertEqual(first["content"], "")
        self.assertNotIn("grant_id", first)
        self.agent.write_file("src/main.rs", "中文", grant_id="grant_test")
        second = json.loads(self.requests[-1][3])
        self.assertEqual(second["content"], "中文")
        self.assertEqual(second["grant_id"], "grant_test")

    def test_admin_identity_and_empty_revoke_body(self):
        self.admin.revoke_grant("task_test", "grant_test")
        self.assertEqual(self.requests[-1], ("DELETE", "/v1/admin/tasks/task_test/grants/grant_test", "Bearer admin-test-secret", b""))

    def test_health_has_no_credentials(self):
        self.http.health().require(200)
        self.assertIsNone(self.requests[-1][2])

    def test_cursor_is_encoded_without_reinterpretation(self):
        self.admin.events(task_id="task_test", after="inst_test:42", limit=2)
        self.assertEqual(self.requests[-1][1], "/v1/admin/events?limit=2&task_id=task_test&after=inst_test%3A42")

    def test_denied_and_executed_audit_failure_are_preserved_without_retry(self):
        for status, payload in [
            (403, {"reason_code": "LEASE_USED_UP", "allowed": False, "executed": False}),
            (503, {"reason_code": "AUDIT_UNAVAILABLE", "allowed": True, "executed": True}),
        ]:
            with self.subTest(status=status):
                type(self).reply = (status, payload, {})
                before = len(self.requests)
                response = self.agent.write_file("src/main.rs", "after", grant_id="grant_test")
                self.assertEqual(response.status, status)
                self.assertEqual(response.payload, payload)
                self.assertEqual(len(self.requests), before + 1)

    def test_redirect_does_not_forward_credentials_or_write(self):
        type(self).reply = (307, b"", {"Location": self.url + "/other"})
        with self.assertRaises(ClientError):
            self.agent.write_file("src/main.rs", "private-content")
        self.assertEqual(len(self.requests), 1)

    def test_html_and_non_object_json_are_protocol_errors(self):
        for payload in (b"<html>error</html>", b"[]", b"\xff"):
            with self.subTest(payload=payload):
                type(self).reply = (502, payload, {})
                with self.assertRaises(ClientError):
                    self.http.health()

    def test_repr_and_status_exception_do_not_reveal_payload(self):
        response = ApiResponse(503, {"agent_token": "private-token", "message": "private-content", "reason_code": "AUDIT_UNAVAILABLE"})
        self.assertNotIn("private", repr(response))
        with self.assertRaises(ClientError) as caught:
            response.require(200)
        self.assertNotIn("private", str(caught.exception))

    def test_oversized_response_is_rejected(self):
        type(self).reply = (200, b"x" * (MAX_RESPONSE_BYTES + 1), {})
        with self.assertRaisesRegex(ClientError, "size limit"):
            self.http.health()

    def test_timeout_does_not_retry(self):
        http = BrokerHttp(self.url, timeout=0.02)
        with self.assertRaisesRegex(ClientError, "timed out"):
            http.request("POST", "/slow", body={"content": "private"})
        self.assertEqual(len(self.requests), 1)

    def test_truncated_response_cannot_be_mistaken_for_success(self):
        type(self).reply = (200, b'{"status":"ok"}', {"Content-Length": "1000"})
        with self.assertRaisesRegex(ClientError, "incomplete"):
            self.http.health()

    def test_bad_origins_timeouts_and_route_ids_are_rejected_before_request(self):
        for url in ("https://127.0.0.1", "http://example.com", "http://user:pass@127.0.0.1", "http://127.0.0.1/path", "http://127.0.0.1?url=x"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                BrokerHttp(url)
        for timeout in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                BrokerHttp(self.url, timeout=timeout)
        with self.assertRaises(ValueError):
            self.admin.task("../other")
        with self.assertRaises(ValueError):
            self.admin.tasks(limit=True)
        self.assertFalse(self.requests)


if __name__ == "__main__":
    unittest.main()
