"""Unit tests for explicit Harness configuration and audit reconciliation."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from integrations.external_review import (
    ALLOWED_TOOLS,
    EDIT_CONSTRAINT,
    ExternalReview,
    HarnessConfig,
    compose_goal,
    guard_patch,
    summarize_events,
)
from integrations.harness_pipe import TaskPipeRelay
from integrations.mcp_review import ReviewError


class HarnessConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "@deepseek-ai" / "dsh"
        (self.package / "lib").mkdir(parents=True)
        self.entry = self.package / "lib" / "bin.js"
        self.entry.write_text("// fake entry\n", encoding="utf-8")
        self.manifest = self.package / "package.json"
        self.manifest.write_text(json.dumps({"name": "@deepseek-ai/dsh", "version": "0.2.1-rc.2"}), encoding="utf-8")
        self.node = Path(sys.executable).resolve()

    def config(self, **overrides):
        values = {"entry": self.entry, "node": self.node, "mcp_binary": self.node}
        values.update(overrides)
        return HarnessConfig(**values)

    def test_accepts_the_pinned_harness(self):
        config = self.config()
        self.assertEqual(config.model, "deepseek-flash")
        self.assertEqual(config.timeout, 600.0)
        self.assertIn("0.2.1-rc.2", config.describe()["harness"])

    def test_rejects_another_harness_version(self):
        self.manifest.write_text(json.dumps({"name": "@deepseek-ai/dsh", "version": "9.9.9"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.config()

    def test_rejects_relative_or_missing_executables(self):
        with self.assertRaises(ValueError):
            self.config(entry=Path("bin.js"))
        with self.assertRaises(ValueError):
            self.config(node=self.root / "missing-node.exe")

    def test_rejects_invalid_model_and_timeout(self):
        with self.assertRaises(ValueError):
            self.config(model="bad model!")
        with self.assertRaises(ValueError):
            self.config(timeout=601)

    def test_environment_resolution_requires_an_explicit_entry(self):
        with mock.patch.dict("os.environ", {"AGENTFENCE_DSH_ENTRY": "", "AGENTFENCE_DSH_NODE": ""}, clear=False):
            with self.assertRaises(ValueError):
                HarnessConfig.from_environment(mcp_binary=self.node)


class GuardPatchTests(unittest.TestCase):
    def test_default_patch_only_inserts_the_guard(self):
        patch = guard_patch()
        self.assertIn("- insert:", patch)
        self.assertIn("agentfence-readonly-guard", patch)
        self.assertNotIn("agent-default-model", patch)

    def test_custom_model_patch_targets_the_default_model_row(self):
        patch = guard_patch("deepseek-pro")
        self.assertIn("- id: agent-default-model", patch)
        self.assertIn('"deepseek-pro"', patch)


class SummarizeEventsTests(unittest.TestCase):
    snapshot = "snap_test"

    def result(self, tool, allowed=True, executed=True, path="src/main.rs", request_id="req_1", snapshot=None):
        return {
            "event_type": "result",
            "tool": tool,
            "allowed": allowed,
            "executed": executed,
            "path": path,
            "request_id": request_id,
            "snapshot_id": self.snapshot if snapshot is None else snapshot,
        }

    def test_three_snapshot_reads_are_accepted(self):
        events = [self.result("read_file", path=f"src/f{i}.rs", request_id=f"req_{i}") for i in range(3)]
        calls, reads = summarize_events(events, self.snapshot)
        self.assertEqual(len(calls), 3)
        self.assertEqual(reads, {"src/f0.rs", "src/f1.rs", "src/f2.rs"})
        self.assertTrue(all(call["ok"] for call in calls))

    def test_intents_are_not_calls(self):
        events = [{"event_type": "intent", "tool": "read_file", "request_id": "req_1", "allowed": False, "executed": False}]
        calls, reads = summarize_events(events, self.snapshot)
        self.assertEqual((calls, reads), ([], set()))

    def test_successful_write_is_rejected_for_readonly(self):
        events = [self.result("write_file")]
        with self.assertRaises(ReviewError):
            summarize_events(events, self.snapshot)

    def test_successful_write_is_a_call_for_editable_tasks(self):
        events = [self.result("write_file"), self.result("read_file")]
        calls, reads = summarize_events(events, self.snapshot, editable=True)
        self.assertEqual([c["tool"] for c in calls], ["write_file", "read_file"])
        self.assertEqual(reads, {"src/main.rs"})
        self.assertTrue(calls[0]["ok"])

    def test_read_from_another_snapshot_is_rejected(self):
        events = [self.result("read_file", snapshot="snap_other")]
        with self.assertRaises(ReviewError):
            summarize_events(events, self.snapshot)

    def test_denied_read_is_reported_without_counting(self):
        events = [self.result("read_file", allowed=False, executed=False)]
        calls, reads = summarize_events(events, self.snapshot)
        self.assertEqual(reads, set())
        self.assertFalse(calls[0]["ok"])


class ExternalReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "@deepseek-ai" / "dsh"
        (self.package / "lib").mkdir(parents=True)
        self.entry = self.package / "lib" / "bin.js"
        self.entry.write_text("// fake\n", encoding="utf-8")
        (self.package / "package.json").write_text(json.dumps({"name": "@deepseek-ai/dsh", "version": "0.2.1-rc.2"}), encoding="utf-8")
        node = Path(sys.executable).resolve()
        self.config = HarnessConfig(entry=self.entry, node=node, mcp_binary=node)
        self.summary = {
            "task_id": "task_1",
            "snapshot_id": "snap_1",
            "instance_id": "inst_1",
            "goal": "检查项目中的代码问题，并提出修改建议",
            "permission_profile": "readonly",
        }

    def review(self, **overrides):
        summary = dict(self.summary)
        summary.update(overrides)
        return ExternalReview(self.config, "http://127.0.0.1:1", summary, "token", "admin", api_key="key")

    def test_known_profiles_require_a_snapshot(self):
        self.assertFalse(self.review(permission_profile="readonly").editable)
        self.assertTrue(self.review(permission_profile="controlled_edit").editable)
        with self.assertRaises(ReviewError):
            self.review(permission_profile="admin")
        with self.assertRaises(ReviewError):
            self.review(snapshot_id=None)

    def test_prepare_home_injects_the_guard_without_the_harness(self):
        root = self.root / "run"
        root.mkdir()
        review = self.review()
        with mock.patch("integrations.external_review.subprocess.run") as run:
            def seed(command, **kwargs):
                profile = Path(kwargs["env"]["DSH_HOME"]) / "profiles" / "acp"
                profile.mkdir(parents=True, exist_ok=True)
                return subprocess.CompletedProcess(command, 0)
            run.side_effect = seed
            home = review._prepare_home(root)
        plugin = home / "profiles" / "acp" / "node_modules" / "@agentfence" / "readonly-guard"
        source = (plugin / "index.js").read_text(encoding="utf-8")
        for tool in ALLOWED_TOOLS:
            self.assertIn(tool, source)
        patch = (home / "profiles" / "acp" / "cordis.patch.yml").read_text(encoding="utf-8")
        self.assertIn("agentfence-readonly-guard", patch)


class ComposeGoalTests(unittest.TestCase):
    def test_readonly_goal_is_unchanged(self):
        self.assertEqual(compose_goal("review", False), "review")

    def test_edit_goal_gains_the_preservation_constraint(self):
        value = compose_goal("fix it", True)
        self.assertTrue(value.startswith("fix it"))
        self.assertIn("换行符", value)

    def test_goal_near_the_limit_is_left_alone(self):
        goal = "x" * (1000 - len(EDIT_CONSTRAINT) + 1)
        self.assertEqual(compose_goal(goal, True), goal)


@unittest.skipUnless(os.name == "nt", "pipe relay is Windows-only")
class RelayInjectionTests(unittest.TestCase):
    def setUp(self):
        self.relay = TaskPipeRelay(
            Path(sys.executable).resolve(),
            "http://127.0.0.1:1",
            "task_1",
            "t" * 32,
            grant_hook=lambda path: "grant-" + path,
        )

    @staticmethod
    def frame(arguments):
        return json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "agentfence_write_file", "arguments": arguments},
        }).encode()

    def test_injects_a_launcher_grant(self):
        value = json.loads(self.relay._inject(self.frame({"path": "src/main.rs", "content": "x"})))
        self.assertEqual(value["params"]["arguments"]["grant_id"], "grant-src/main.rs")
        self.assertEqual(value["params"]["arguments"]["content"], "x")

    def test_replaces_a_model_supplied_grant(self):
        value = json.loads(self.relay._inject(self.frame({"path": "a.py", "content": "y", "grant_id": "forged"})))
        self.assertEqual(value["params"]["arguments"]["grant_id"], "grant-a.py")

    def test_non_write_frames_are_untouched(self):
        line = json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "agentfence_read_file", "arguments": {"path": "a.py"}},
        }).encode()
        self.assertIs(self.relay._inject(line), line)

    def test_invalid_json_is_untouched(self):
        line = b"not json"
        self.assertIs(self.relay._inject(line), line)


if __name__ == "__main__":
    unittest.main()
