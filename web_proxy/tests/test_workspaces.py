"""Management HTTP tests with an explicit contract double, not Rust v2 evidence."""

from http.client import HTTPConnection
import json
import threading
import time
import unittest

from web_proxy.config import Config
from web_proxy.server import create_server
from web_proxy.workspaces import WorkspaceManager, ManagementError, preview_shape, workspace_shape

PREVIEW = {"preview_id": "pre_test", "manifest_sha256": "a" * 64, "expires_at": "2026-10-29T00:00:00Z",
           "allowed_file_count": 3, "excluded_file_count": 1, "total_bytes": 50,
           "excluded": [{"path": ".env", "reason_code": "SENSITIVE_FILE"}]}
WORKSPACE = {"workspace_id": "ws_test", "snapshot_id": "snap_test", "display_name": "test",
             "manifest_sha256": "a" * 64, "imported_at": "2026-10-28T00:00:00Z", "import_mode": "readonly_snapshot",
             "allowed_file_count": 3, "excluded_file_count": 1, "total_bytes": 50}


class Backend:
    def __init__(self):
        self.calls = []
        self.fail_confirm = False

    def preview(self, root):
        self.calls.append(("preview", root))
        return PREVIEW.copy()

    def confirm(self, preview_id, name):
        self.calls.append(("confirm", preview_id, name))
        if self.fail_confirm:
            raise ManagementError(502, "BROKER_UNAVAILABLE", "uncertain")
        return {**WORKSPACE, "display_name": name}

    def workspaces(self):
        return {"items": []}

    def create_readonly_task(self, workspace_id, goal):
        self.calls.append(("create", workspace_id, goal))
        return {"task_id": "task_readonly", "workspace_id": workspace_id, "snapshot_id": "snap_test",
                "goal": goal, "permission_profile": "readonly", "phase": "inspect", "agent_token": "private-" + "z" * 40,
                "created_at": "2026-10-28T00:00:00Z", "updated_at": "2026-10-28T00:00:00Z", "instance_id": "inst_test"}


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.backend = Backend()
        self.selected = "C:/fixture"  # A trusted dialog result, not an HTTP root argument.
        self.manager = WorkspaceManager(self.backend, picker=lambda: self.selected, clock=lambda: self.now)
        self.server = create_server(Config("http://127.0.0.1:8080", "x" * 40, port=0, dist=None), manager=self.manager)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cookie = self.csrf = ""
        self.new_session()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, path, method="GET", body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        options = {"Cookie": self.cookie, "Origin": f"http://127.0.0.1:{self.server.server_port}",
                   "X-AgentFence-CSRF": self.csrf, "Content-Type": "application/json"}
        options.update(headers or {})
        raw = body if isinstance(body, str) else json.dumps(body) if body is not None else None
        connection.request(method, path, raw, options)
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), json.loads(response.read())
        connection.close()
        return result

    def new_session(self):
        status, headers, body = self.request("/api/admin/session", "POST", {})
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";")[0]
        self.csrf = body["csrf_token"]

    def select(self):
        status, _, body = self.request("/api/admin/picker/start", "POST", {})
        self.assertEqual(status, 202)
        sid = body["selection_id"]
        for _ in range(100):
            code, _, value = self.request("/api/admin/picker/" + sid)
            self.assertEqual(code, 200)
            if value["state"] != "pending":
                return sid, value
            time.sleep(.005)
        self.fail("dialog worker did not finish")

    def confirm(self, sid):
        return self.request("/api/admin/workspaces/confirm", "POST", {
            "selection_id": sid, "display_name": "project", "profile": "readonly_snapshot"})

    def test_import_and_one_use(self):
        sid, value = self.select()
        self.assertEqual(value["preview"]["allowed_file_count"], 3)
        self.assertNotIn("session", value)
        self.assertEqual(self.confirm(sid)[0], 201)
        self.assertEqual(self.confirm(sid)[0], 409)
        self.assertEqual([x[0] for x in self.backend.calls], ["preview", "confirm"])

    def test_cancel_never_registers(self):
        self.selected = None
        sid, value = self.select()
        self.assertEqual(value["state"], "cancelled")
        self.assertEqual(self.confirm(sid)[0], 409)
        self.assertEqual(self.backend.calls, [])

    def test_browser_root_is_rejected(self):
        self.assertEqual(self.request("/api/admin/picker/start", "POST", {"root": "C:/"})[0], 400)
        self.assertEqual(self.backend.calls, [])

    def test_selection_is_session_bound(self):
        sid, _ = self.select()
        self.cookie = ""
        self.new_session()
        self.assertEqual(self.confirm(sid)[0], 404)

    def test_expired_selection(self):
        sid, _ = self.select()
        self.now = 301
        self.assertEqual(self.confirm(sid)[0], 404)

    def test_expired_session(self):
        self.now = 1801
        self.assertEqual(self.request("/api/admin/workspaces")[0], 401)

    def test_bad_csrf(self):
        self.assertEqual(self.request("/api/admin/picker/start", "POST", {}, {"X-AgentFence-CSRF": "wrong"})[0], 403)

    def test_bad_origin(self):
        self.assertEqual(self.request("/api/admin/picker/start", "POST", {}, {"Origin": "https://evil.example"})[0], 403)

    def test_duplicate_fields(self):
        self.assertEqual(self.request("/api/admin/picker/start", "POST", '{"root":1,"root":2}')[0], 400)

    def test_unknown_management_route(self):
        self.assertEqual(self.request("/api/admin/arbitrary")[0], 404)

    def test_uncertain_confirmation_is_consumed(self):
        self.backend.fail_confirm = True
        sid, _ = self.select()
        self.assertEqual(self.confirm(sid)[0], 502)
        self.assertEqual(self.confirm(sid)[0], 409)

    def test_bootstrap_reuses_session(self):
        previous = self.cookie, self.csrf
        for _ in range(35):
            self.new_session()
        self.assertEqual((self.cookie, self.csrf), previous)
        self.assertEqual(len(self.manager.sessions), 1)

    def test_concurrent_dialog_is_rejected(self):
        gate = threading.Event()
        self.manager.picker = lambda: gate.wait(2) and None
        try:
            self.assertEqual(self.request("/api/admin/picker/start", "POST", {})[0], 202)
            self.assertEqual(self.request("/api/admin/picker/start", "POST", {})[0], 409)
        finally:
            gate.set()

    def test_shape_filters_secrets_and_rejects_limits(self):
        self.assertNotIn("admin_token", preview_shape({**PREVIEW, "admin_token": "secret"}))
        self.assertNotIn("agent_token", workspace_shape({**WORKSPACE, "agent_token": "secret"}))
        for extra in ({"total_bytes": 21 * 1024 * 1024}, {"allowed_file_count": 1001}, {"allowed_file_count": True}, {"allowed_file_count": 0}):
            with self.assertRaises(ManagementError):
                preview_shape({**PREVIEW, **extra})

    def test_readonly_task_never_returns_credentials(self):
        status, _, value = self.request("/api/admin/tasks", "POST", {
            "workspace_id": "ws_test", "goal": "review", "permission_profile": "readonly"})
        self.assertEqual(status, 201)
        self.assertEqual(value["permission_profile"], "readonly")
        self.assertNotIn("agent_token", value)
        self.assertIn("task_readonly", self.manager.tasks)

    def test_task_rejects_edit_and_extra_credentials(self):
        for extra in ({"permission_profile": "controlled_edit"}, {"agent_token": "bad"}, {"goal": ""}, {"workspace_id": "../root"}):
            body = {"workspace_id": "ws_test", "goal": "review", "permission_profile": "readonly", **extra}
            self.assertEqual(self.request("/api/admin/tasks", "POST", body)[0], 400)
        self.assertEqual(self.backend.calls, [])


if __name__ == "__main__":
    unittest.main()
