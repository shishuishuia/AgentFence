"""Exercise live controls against real Rust and the same-origin HTTP boundary."""

from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from demo.fixture import prepare_fixture
from demo.live import LiveDemo
from demo.runtime import ManagedBroker, default_binary
from integrations import AdminClient, BrokerHttp
from web_proxy.config import Config
from web_proxy.server import create_server


@unittest.skipUnless(default_binary().is_file(), "build the release Rust service first")
class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        prepare_fixture(self.root / "fixture")
        self.token = "live-test-" + "x" * 48
        self.broker = ManagedBroker(default_binary(), self.root / "fixture", self.token)
        self.broker.start()
        self.runner = LiveDemo(self.broker.base_url, self.token, self.root / "fixture", self.root, step_delay=0.02)
        self.proxy = create_server(Config(self.broker.base_url, self.token, port=0, dist=None), demo=self.runner)
        self.thread = threading.Thread(target=self.proxy.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.proxy.shutdown()
        self.thread.join(timeout=3)
        self.proxy.server_close()
        self.runner.close()
        self.broker.stop()
        self.temp.cleanup()

    def request(self, path="/api/demo/control", method="POST", body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.proxy.server_port, timeout=6)
        values = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{self.proxy.server_port}", "X-AgentFence-Demo": "1"}
        if headers is not None:
            values = headers
        connection.request(method, path, body=json.dumps(body) if body is not None else None, headers=values)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def wait_finished(self):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = self.runner.snapshot()
            if state["status"] in ("succeeded", "failed"):
                return state
            time.sleep(0.03)
        self.fail("live task did not finish")

    def test_real_run_deduplicates_and_replays_with_fresh_task(self):
        self.assertEqual(self.request(method="GET", path="/api/demo/state")[1]["status"], "idle")
        status, started = self.request(body={"action": "start", "request_id": "first"})
        self.assertEqual(status, 202)
        self.assertEqual(self.request(body={"action": "start", "request_id": "first"})[1]["run_id"], started["run_id"])
        self.assertEqual(self.request(body={"action": "start", "request_id": "another"})[0], 409)
        state = self.wait_finished()
        self.assertEqual(state["status"], "succeeded", state)
        self.assertTrue(state["verified"])
        self.assertEqual(state["completed_steps"], 13)
        self.assertEqual(sum(s["http_status"] == 403 for s in state["steps"]), 5)
        self.assertNotIn(self.token, json.dumps(state))
        self.assertNotIn(str(self.root), json.dumps(state))
        self.assertEqual(self.request(body={"action": "start", "request_id": "first"})[0], 200)
        self.assertEqual(self.request(body={"action": "start", "request_id": "second"})[0], 202)
        again = self.wait_finished()
        self.assertEqual(again["status"], "succeeded", again)
        self.assertNotEqual(again["task_id"], state["task_id"])
        self.assertEqual(self.request(body={"action": "start", "request_id": "first"})[0], 409)
        self.assertEqual(AdminClient(BrokerHttp(self.broker.base_url), self.token).tasks().require(200)["total"], 2)

    def test_pause_resume_stale_controls_and_close(self):
        self.runner.delay = 0.2
        _, state = self.request(body={"action": "start", "request_id": "pause-test"})
        self.assertEqual(self.request(body={"action": "pause", "request_id": "old_run"})[0], 409)
        self.assertEqual(self.request(body={"action": "pause", "request_id": state["run_id"]})[0], 200)
        time.sleep(0.2)  # An already-dispatched HTTP request may finish before the gate.
        first = self.runner.snapshot()["completed_steps"]
        time.sleep(0.3)
        self.assertEqual(self.runner.snapshot()["completed_steps"], first)
        self.assertEqual(self.request(body={"action": "resume", "request_id": state["run_id"]})[0], 200)
        self.assertEqual(self.wait_finished()["status"], "succeeded")
        self.request(body={"action": "start", "request_id": "close-test"})
        self.runner.close()
        self.assertFalse(self.runner._thread.is_alive())

    def test_http_cannot_select_tools_paths_credentials_or_cross_origin(self):
        invalid = [
            ({"action": "start", "request_id": "a", "path": "../file"}, None, 400),
            ({"action": "write_file", "request_id": "a"}, None, 400),
            ({"action": "start", "request_id": "a"}, {"Content-Type": "application/json"}, 403),
            ({"action": "start", "request_id": "a"}, {"Content-Type": "application/json", "Origin": "https://attacker.invalid", "X-AgentFence-Demo": "1"}, 403),
        ]
        for body, headers, status in invalid:
            self.assertEqual(self.request(body=body, headers=headers)[0], status)
        self.assertEqual(self.request(path="/api/demo/control?path=x", body={"action": "start", "request_id": "a"})[0], 400)
        self.assertEqual(self.request(path="/api/demo/control", method="GET")[0], 405)
        self.assertEqual(self.runner.snapshot()["status"], "idle")

    def test_worker_failure_does_not_publish_success_or_sensitive_exception(self):
        (self.root / "fixture/workspace/src/main.rs").unlink()
        self.request(body={"action": "start", "request_id": "failed-run"})
        state = self.wait_finished()
        self.assertEqual(state["status"], "failed")
        self.assertFalse(state["verified"])
        self.assertNotIn(str(self.root), json.dumps(state))
        self.assertNotIn(self.token, json.dumps(state))

    def test_unexpected_http_denial_is_failed_instead_of_expected_interception(self):
        self.runner.delay = 0.2
        self.runner.control("start", "revoke-during-demo")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = self.runner.snapshot()
            if state["completed_steps"] == 5:
                break
            time.sleep(0.02)
        self.assertEqual(state["completed_steps"], 5)
        self.assertEqual(self.runner.control("pause", state["run_id"])[0], 200)
        admin = AdminClient(BrokerHttp(self.broker.base_url), self.token)
        detail = admin.task(state["task_id"]).require(200)
        admin.revoke_grant(state["task_id"], detail["grants"][0]["grant_id"]).require(200)
        self.runner.control("resume", state["run_id"])
        failed = self.wait_finished()
        self.assertEqual(failed["status"], "failed")
        self.assertFalse(failed["steps"][-1]["pass"])
        self.assertEqual(failed["steps"][-1]["reason_code"], "LEASE_REVOKED")
        self.assertIn("LEASE_REVOKED", failed["failure"])


if __name__ == "__main__":
    unittest.main()
