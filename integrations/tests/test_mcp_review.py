"""Real Rust + official SDK; model responses are controlled, no paid API calls."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import secrets
import tempfile
import unittest

from demo.fixture import prepare_fixture
from demo.runtime import ManagedBroker
from integrations.mcp_review import ReviewClient, ReviewError, child_environment
from web_proxy.config import Config
from web_proxy.workspaces import WorkspaceBackend

REPO = Path(__file__).resolve().parents[2]
SUFFIX = ".exe" if os.name == "nt" else ""
BIN = Path(os.getenv("AGENTFENCE_V2_BIN_DIR", REPO / "target/v2/release"))


class BoundaryTests(unittest.TestCase):
    def test_child_does_not_receive_privileged_secrets(self):
        value = child_environment("http://127.0.0.1:8080", "task_test", "task-only-secret")
        self.assertNotIn("AGENTFENCE_ADMIN_TOKEN", value)
        self.assertNotIn("DEEPSEEK_API_KEY", value)
        self.assertNotIn("PATH", value)

    def test_model_sees_numbered_excerpt_and_truncation(self):
        value = ReviewClient.model_view({"content": "x" * 20000, "numbered_content": "1: " + "x" * 20000})
        self.assertTrue(value["content_truncated"])
        self.assertEqual(len(value["numbered_content"]), 18000)
        self.assertNotIn("content", value)


@unittest.skipUnless(importlib.util.find_spec("mcp") and (BIN / ("agentfence-server" + SUFFIX)).is_file() and (BIN / ("agentfence-mcp-server" + SUFFIX)).is_file(), "requires official MCP SDK and built Rust v2 binaries")
class RustMcpTests(unittest.TestCase):
    def test_real_stdio_three_files_audit_and_task_completion(self):
        with tempfile.TemporaryDirectory(dir=REPO / "tmp" if (REPO / "tmp").exists() else None) as tmp:
            root = Path(tmp)
            prepare_fixture(root / "fixture")
            project = root / "project"
            project.mkdir()
            for name in ("main.rs", "lib.rs", "README.md"):
                (project / name).write_text("// test file\n", encoding="utf-8")
            token = secrets.token_hex(32)
            with ManagedBroker(BIN / ("agentfence-server" + SUFFIX), root / "fixture", token) as broker:
                backend = WorkspaceBackend(Config(broker.base_url, token, dist=None))
                preview = backend.preview(str(project.resolve()))
                workspace = backend.confirm(preview["preview_id"], "test")
                task = backend.create_readonly_task(workspace["workspace_id"], "review")
                client = ReviewClient(BIN / ("agentfence-mcp-server" + SUFFIX), broker.base_url, task, task["agent_token"], token)
                value = asyncio.run(client.run())
                self.assertEqual(value["mode"], "offline_mcp_replay")
                self.assertEqual(len(value["files"]), 3)
                self.assertEqual(len(value["calls"]), 5)
                self.assertFalse(value["report"])
                from integrations.client import AdminClient, BrokerHttp
                self.assertEqual(AdminClient(BrokerHttp(broker.base_url), token).task(task["task_id"]).require(200)["phase"], "done")
                next_task = backend.create_readonly_task(workspace["workspace_id"], "controlled model test")

                class ControlledModel:
                    """Test double only; this is not evidence of a live LLM call."""
                    rounds = 0
                    def complete(self, messages, tools):
                        if token in json.dumps(messages) or next_task["agent_token"] in json.dumps(messages):
                            raise AssertionError("credential entered model context")
                        self.rounds += 1
                        if self.rounds == 1:
                            return {"role": "assistant", "content": None, "tool_calls": [{"id": "call_test", "type": "function", "function": {"name": "agentfence_read_file", "arguments": '{"path":"main.rs"}'}}]}, {}
                        return {"role": "assistant", "content": "Controlled test report: main.rs:1, lib.rs:1, README.md:1."}, {}
                client = ReviewClient(BIN / ("agentfence-mcp-server" + SUFFIX), broker.base_url, next_task, next_task["agent_token"], token, model=ControlledModel())
                result = asyncio.run(client.run())
                self.assertEqual(len(result["calls"]), 6)
                self.assertIn("Controlled test report", result["report"])


if __name__ == "__main__":
    unittest.main()
