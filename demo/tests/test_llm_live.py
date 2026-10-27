"""Script only the model replies; execute the selected tools against real Rust."""

from copy import deepcopy
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from demo.fixture import BEFORE, prepare_fixture
from demo.llm_live import LLMLiveDemo
from demo.runtime import ManagedBroker, default_binary
from integrations import AdminClient, BrokerHttp
from integrations.deepseek import DeepSeekConfig, ModelError
from web_proxy.config import Config
from web_proxy.server import create_server

CODE = 'fn main() { println!("model-selected change"); }\n'


def call(name, args=None, call_id=None):
    return {"role": "assistant", "content": None, "tool_calls": [{"id": call_id or name,
            "type": "function", "function": {"name": name, "arguments": json.dumps(args or {})}}]}


def successful_calls():
    return [call("read_file", {"path": "src/main.rs"}, "read_before"), call("request_write_access"),
            call("write_file", {"path": "src/main.rs", "content": CODE}), call("begin_verification"),
            call("read_file", {"path": "src/main.rs"}, "read_after"), call("finish_task", {"summary": "已按任务修改并读回核对。"})]


class ScriptedModel:
    config = DeepSeekConfig("fake-model-secret")

    def __init__(self, replies):
        self.replies = iter(replies)
        self.seen = []

    def complete(self, messages, tools):
        self.seen.append(deepcopy(messages))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


@unittest.skipUnless(default_binary().is_file(), "build the release Rust service first")
class LLMTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        prepare_fixture(self.root / "fixture")
        self.token = "llm-test-" + "x" * 48
        self.broker = ManagedBroker(default_binary(), self.root / "fixture", self.token)
        self.broker.start()
        self.runners = []

    def tearDown(self):
        for runner in self.runners:
            runner.close()
        self.broker.stop()
        self.temp.cleanup()

    def run_model(self, replies):
        model = ScriptedModel(replies)
        runner = LLMLiveDemo(self.broker.base_url, self.token, self.root / "fixture", self.root,
                             model_client=model, step_delay=0)
        self.runners.append(runner)
        status, state = runner.control("start", "model-test", task="修改目标输出并读回验证")
        self.assertEqual(status, 202)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = runner.snapshot()
            if state["status"] in ("succeeded", "failed"):
                return state, model, runner
            time.sleep(0.02)
        self.fail("model-controlled workflow did not finish")

    def test_model_selected_content_is_written_and_verified_without_credentials(self):
        state, model, runner = self.run_model(successful_calls())
        self.assertEqual(state["status"], "succeeded", state)
        self.assertTrue(state["verified"])
        self.assertIsNone(state["total_steps"])
        self.assertEqual(state["model_round"], 6)
        self.assertEqual(state["usage"]["total_tokens"], 90)
        self.assertEqual((self.root / "fixture/workspace/src/main.rs").read_text(encoding="utf-8"), CODE)
        conversation = json.dumps(model.seen)
        self.assertNotIn(self.token, conversation)
        self.assertNotIn(model.config.key, conversation)
        self.assertNotIn('agent_token', conversation)
        self.assertNotIn(str(self.root), json.dumps(state))
        self.assertEqual(runner.control("start", "model-test", task="different prompt")[0], 409)

    def test_denial_is_real_rust_feedback_and_model_can_recover(self):
        replies = [call("read_file", {"path": "../simulated-private.txt"}, "outside")] + successful_calls()
        state, model, _ = self.run_model(replies)
        self.assertEqual(state["status"], "succeeded", state)
        denied = [s for s in state["steps"] if s["reason_code"] == "PATH_DENIED"]
        self.assertEqual(len(denied), 1)
        self.assertFalse(denied[0]["executed"])
        self.assertIn('PATH_DENIED', json.dumps(model.seen))

    def test_no_tools_or_model_error_never_falls_back_to_fixed_success(self):
        for replies in ([{"role": "assistant", "content": "完成了"}], [ModelError("model unavailable")]):
            state, _, _ = self.run_model(replies)
            self.assertEqual(state["status"], "failed")
            self.assertFalse(state["verified"])
            self.assertEqual((self.root / "fixture/workspace/src/main.rs").read_text(encoding="utf-8"), BEFORE)

    def test_duplicate_tool_id_stops_before_repeating_an_operation(self):
        first = call("read_file", {"path": "src/main.rs"}, "duplicate")
        state, _, _ = self.run_model([first, first])
        self.assertEqual(state["status"], "failed")
        self.assertEqual(sum(s["case"] == "read_file" for s in state["steps"]), 1)

    def test_unknown_tools_and_fake_finish_cannot_execute_shell_or_mark_done(self):
        state, model, _ = self.run_model([call("run_shell", {"command": "bad"}),
            call("finish_task", {"summary": "pretend done"}), {"role": "assistant", "content": "done"}])
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["phase"], "inspect")
        self.assertFalse(state["verified"])
        self.assertEqual(len(state["steps"]), 1)  # Only the trusted create_task reached Rust.
        self.assertIn('Unknown tool', json.dumps(model.seen))

    def test_read_only_task_can_verify_without_grant_or_write(self):
        state, _, _ = self.run_model([
            call("read_file", {"path": "src/main.rs"}, "read_before"), call("begin_verification"),
            call("read_file", {"path": "src/main.rs"}, "read_verify"),
            call("finish_task", {"summary": "目标文件已读取并核对，无需修改。"}),
        ])
        self.assertEqual(state["status"], "succeeded", state)
        self.assertFalse(any(s["case"] == "issue_grant" for s in state["steps"]))
        self.assertEqual((self.root / "fixture/workspace/src/main.rs").read_text(encoding="utf-8"), BEFORE)

    def test_http_accepts_task_text_but_cannot_select_endpoint_or_credentials(self):
        model = ScriptedModel([ModelError("test model stopped")])
        runner = LLMLiveDemo(self.broker.base_url, self.token, self.root / "fixture", self.root,
                            model_client=model, step_delay=0)
        self.runners.append(runner)
        with create_server(Config(self.broker.base_url, self.token, port=0, dist=None), demo=runner) as proxy:
            worker = threading.Thread(target=proxy.serve_forever, daemon=True)
            worker.start()
            try:
                def post(body):
                    http = HTTPConnection("127.0.0.1", proxy.server_port, timeout=5)
                    http.request("POST", "/api/demo/control", body=json.dumps(body), headers={
                        "Origin": f"http://127.0.0.1:{proxy.server_port}",
                        "Content-Type": "application/json", "X-AgentFence-Demo": "1",
                    })
                    response = http.getresponse()
                    status, data = response.status, json.loads(response.read())
                    http.close()
                    return status, data
                self.assertEqual(post({"action": "start", "request_id": "bad", "task": "task", "api_key": "other-key"})[0], 400)
                status, state = post({"action": "start", "request_id": "good", "task": "读取 src/main.rs"})
                self.assertEqual(status, 202)
                self.assertEqual(state["task_prompt"], "读取 src/main.rs")
                self.assertNotIn(self.token, json.dumps(state))
            finally:
                proxy.shutdown()
                worker.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
