"""Management HTTP tests with an explicit contract double, not Rust v2 evidence."""

from http.client import HTTPConnection
import json
import threading
import time
import unittest
from unittest.mock import Mock, patch
import subprocess
import os

from web_proxy.config import Config
from web_proxy.server import create_server
from web_proxy.workspaces import WorkspaceManager, ManagementError, NativePicker, preview_shape, workspace_shape

PREVIEW = {"preview_id": "pre_test", "manifest_sha256": "a" * 64, "expires_at": "2026-10-29T00:00:00Z",
           "allowed_file_count": 3, "excluded_entry_count": 1, "total_bytes": 50,
           "files": [{"path": p, "bytes": 10, "sha256": "a" * 64} for p in ("Cargo.toml", "src/main.rs", "src/lib.rs")],
           "excluded": [{"path": ".env", "reason": "sensitive_name"}]}
WORKSPACE = {"workspace_id": "ws_test", "snapshot_id": "snap_test", "display_name": "test",
             "manifest_sha256": "a" * 64, "imported_at": "2026-10-28T00:00:00Z", "import_mode": "readonly_snapshot",
             "allowed_file_count": 3, "excluded_entry_count": 1, "total_bytes": 50}


class Backend:
    def __init__(self):
        self.calls = []
        self.fail_confirm = False

    def preview(self, root):
        self.calls.append(("preview", root))
        return preview_shape(PREVIEW)

    def confirm(self, preview_id, name):
        self.calls.append(("confirm", preview_id, name))
        if self.fail_confirm:
            raise ManagementError(502, "BROKER_UNAVAILABLE", "uncertain")
        return workspace_shape({**WORKSPACE, "display_name": name})

    def workspaces(self):
        return {"items": []}

    def create_edit_task(self, workspace_id, goal):
        self.calls.append(("create_edit", workspace_id, goal))
        return {"task_id": "task_edit", "workspace_id": "ws_isolated", "snapshot_id": "snap_test",
                "goal": goal, "permission_profile": "controlled_edit", "phase": "inspect", "agent_token": "private-" + "z" * 40,
                "created_at": "2026-10-28T00:00:00Z", "updated_at": "2026-10-28T00:00:00Z", "instance_id": "inst_test"}

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

    def test_agent_availability_tracks_configured_factories_and_requires_session(self):
        status, _, result = self.request('/api/admin/agents')
        self.assertEqual(status, 200)
        self.assertEqual(result, {'builtin': False, 'deepseek_harness': False, 'builtin_mode': 'unknown'})
        self.manager.review_factory = lambda *_: None
        self.manager.external_review_factory = lambda *_: None
        self.assertEqual(self.request('/api/admin/agents')[2], {'builtin': True, 'deepseek_harness': True, 'builtin_mode': 'unknown'})
        self.assertEqual(self.request('/api/admin/agents', headers={'Cookie': ''})[0], 401)

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

    def test_task_rejects_invalid_profile_and_extra_credentials(self):
        for extra in ({"permission_profile": "invalid"}, {"agent_token": "bad"}, {"goal": ""}, {"workspace_id": "../root"}):
            body = {"workspace_id": "ws_test", "goal": "review", "permission_profile": "readonly", **extra}
            self.assertEqual(self.request("/api/admin/tasks", "POST", body)[0], 400)
        self.assertEqual(self.backend.calls, [])

    def test_edit_task_keeps_credentials_server_side_and_uses_copy(self):
        status, _, value = self.request("/api/admin/tasks", "POST", {
            "workspace_id": "ws_test", "goal": "edit", "permission_profile": "controlled_edit"})
        self.assertEqual(status, 201)
        self.assertEqual(value["permission_profile"], "controlled_edit")
        self.assertEqual(value["workspace_id"], "ws_isolated")
        self.assertNotIn("agent_token", value)
        self.assertEqual(self.backend.calls, [("create_edit", "ws_test", "edit")])
        self.assertEqual(self.request("/api/admin/tasks/task_edit/changes")[0], 409)
        self.assertEqual(self.request("/api/admin/tasks/task_edit/apply", "POST", {"path":"a.rs","after_sha256":"a"*64}, {"X-AgentFence-CSRF":"bad"})[0], 403)

    def test_external_harness_requires_safe_configuration_without_consuming_task(self):
        self.request("/api/admin/tasks", "POST", {"workspace_id":"ws_test", "goal":"review", "permission_profile":"readonly"})
        status, _, value = self.request("/api/admin/tasks/task_readonly/review/start", "POST", {"agent_type":"deepseek_harness"})
        self.assertEqual(status, 503)
        self.assertEqual(value["reason_code"], "HARNESS_NOT_CONFIGURED")
        self.assertEqual(self.manager.review_status("task_readonly")["state"], "idle")

    def test_external_harness_can_start_an_isolated_edit_task(self):
        class External:
            async def run(self):
                return {"mode":"external_agent", "report":"Edit report", "calls":[]}
        called = []
        def make(summary, token, progress):
            called.append(summary["permission_profile"])
            return External()
        self.manager.external_review_factory = make
        self.request("/api/admin/tasks", "POST", {"workspace_id":"ws_test", "goal":"edit", "permission_profile":"controlled_edit"})
        status, _, _ = self.request("/api/admin/tasks/task_edit/review/start", "POST", {"agent_type":"deepseek_harness"})
        self.assertEqual(status, 202)
        for _ in range(100):
            if self.manager.review_status("task_edit")["state"] != "running":
                break
            time.sleep(.01)
        self.assertEqual(called, ["controlled_edit"])
        self.assertEqual(self.manager.review_status("task_edit")["state"], "completed")

    def test_external_harness_type_and_progress_are_visible_without_tokens(self):
        class External:
            async def run(self):
                return {"mode":"external_agent", "report":"Fixture report", "calls":[]}
        received = []
        def make(summary, token, progress):
            received.append((summary["task_id"], token))
            progress({"call_count":1, "read_file_count":1, "progress_text":"正在处理"})
            return External()
        self.manager.external_review_factory = make
        self.request("/api/admin/tasks", "POST", {"workspace_id":"ws_test", "goal":"review", "permission_profile":"readonly"})
        status, _, value = self.request("/api/admin/tasks/task_readonly/review/start", "POST", {"agent_type":"deepseek_harness"})
        self.assertEqual(status,202)
        self.assertEqual(value["agent_type"], "deepseek_harness")
        for _ in range(100):
            _, _, state = self.request("/api/admin/tasks/task_readonly/review")
            if state["state"] != "running": break
            time.sleep(.01)
        self.assertEqual(state["state"], "completed")
        self.assertEqual(state["agent_type"], "deepseek_harness")
        self.assertEqual(state["call_count"], 1)
        self.assertNotIn(received[0][1], json.dumps(state))
        self.assertEqual(self.request("/api/admin/tasks/task_readonly/review/start", "POST", {"agent_type":"deepseek_harness"})[0], 409)

    def test_external_harness_output_cannot_leak_token_or_override_state(self):
        class External:
            def __init__(self, token, progress): self.token, self.progress = token, progress
            async def run(self):
                self.progress({"progress_text": self.token, "agent_type": "builtin", "state": "completed"})
                return {"mode":"external_agent", "report":self.token, "calls":[], "agent_type":"builtin"}
        self.manager.external_review_factory = lambda summary, token, progress: External(token, progress)
        self.request("/api/admin/tasks", "POST", {"workspace_id":"ws_test", "goal":"review", "permission_profile":"readonly"})
        self.assertEqual(self.request("/api/admin/tasks/task_readonly/review/start", "POST", {"agent_type":"deepseek_harness"})[0], 202)
        for _ in range(100):
            _, _, state = self.request("/api/admin/tasks/task_readonly/review")
            if state["state"] != "running": break
            time.sleep(.01)
        self.assertEqual(state["state"], "failed")
        self.assertEqual(state["agent_type"], "deepseek_harness")
        self.assertNotIn(self.manager.tasks["task_readonly"]["token"], json.dumps(state))
        self.assertNotIn("report", state)

    def test_external_harness_request_rejects_extra_arguments(self):
        self.request("/api/admin/tasks", "POST", {"workspace_id":"ws_test", "goal":"review", "permission_profile":"readonly"})
        for body in ({"agent_type":"unknown"}, {"agent_type":"deepseek_harness", "command":"arbitrary"}, {"agent_type":"deepseek_harness", "env":{}}):
            self.assertEqual(self.request("/api/admin/tasks/task_readonly/review/start", "POST", body)[0], 400)
        self.assertEqual(self.manager.review_status("task_readonly")["state"], "idle")

    def test_workspace_name_enforces_rust_byte_bound(self):
        sid, _ = self.select()
        status, _, _ = self.request("/api/admin/workspaces/confirm", "POST", {
            "selection_id": sid, "display_name": "😀" * 80, "profile": "readonly_snapshot"})
        self.assertEqual(status, 400)
        self.assertEqual([x[0] for x in self.backend.calls], ["preview"])


class PickerLifecycleTests(unittest.TestCase):
    def test_timed_out_dialog_is_reaped_and_can_be_opened_again(self):
        first, second = Mock(), Mock()
        first.communicate.side_effect = subprocess.TimeoutExpired("owned-picker", 180)
        first.poll.return_value = None
        second.communicate.return_value = (b'{"path":null}', None)
        second.returncode = 0
        picker = NativePicker()
        with patch.dict(os.environ, {"SystemDrive": "C:", "APPDATA": "C:/fixture/AppData", "AGENTFENCE_ADMIN_TOKEN": "not-for-child"}), patch("web_proxy.workspaces.subprocess.Popen", side_effect=[first, second]) as launch:
            with self.assertRaises(ManagementError):
                picker()
            first.terminate.assert_called_once()
            self.assertFalse(picker.closed)
            self.assertIsNone(picker())
            env = {k.upper(): v for k, v in launch.call_args.kwargs["env"].items()}
            self.assertEqual(env["SYSTEMDRIVE"], "C:")
            self.assertIn("APPDATA", env)
            self.assertNotIn("AGENTFENCE_ADMIN_TOKEN", env)


if __name__ == "__main__":
    unittest.main()
