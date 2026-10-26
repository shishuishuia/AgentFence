"""Fixture preservation and real-process cleanup checks for the demo runner."""

from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import unittest

from demo.fixture import BEFORE, prepare_fixture
from demo.run_minimal import run
from demo.runtime import ManagedBroker, default_binary
from integrations import BrokerHttp


class FixtureTests(unittest.TestCase):
    def test_existing_directory_and_file_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sentinel = root / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            for existing in (root, sentinel):
                with self.subTest(existing=existing), self.assertRaises(FileExistsError):
                    prepare_fixture(existing)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_fixture_is_self_contained_and_exact(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp) / "new"
            policy = prepare_fixture(fixture)
            self.assertEqual((fixture / "workspace/src/main.rs").read_bytes(), BEFORE.encode())
            self.assertEqual(json.loads(policy.read_text())["workspaces"]["demo"]["root"], "workspace")
            self.assertTrue((fixture / "simulated-private.txt").is_file())
            self.assertFalse((fixture / "audit.jsonl").exists())

    def test_missing_binary_returns_failure_and_saves_report(self):
        with tempfile.TemporaryDirectory() as temp, redirect_stderr(io.StringIO()):
            code, directory = run(Path(temp) / "missing", Path(temp) / "results")
            self.assertEqual(code, 1)
            report = json.loads((directory / "minimal_demo.json").read_text())
            self.assertEqual(report["status"], "failed")
            self.assertTrue(report["server_stopped"])


@unittest.skipUnless(default_binary().is_file(), "build the release Rust server before real-process tests")
class RealServerTests(unittest.TestCase):
    def test_owned_process_is_stopped_on_flow_exception(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp) / "fixture"
            prepare_fixture(fixture)
            server = ManagedBroker(default_binary(), fixture, "test-only-admin-token-" + "a" * 32)
            with self.assertRaisesRegex(RuntimeError, "intentional"):
                with server:
                    self.assertEqual(BrokerHttp(server.base_url).health().status, 200)
                    raise RuntimeError("intentional flow failure")
            self.assertIsNotNone(server.process.poll())
            self.assertFalse(server._reader.is_alive())

    def test_startup_failure_is_cleaned_up(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = Path(temp) / "fixture"
            prepare_fixture(fixture)
            (fixture / "policy.json").write_text("{}", encoding="utf-8")
            server = ManagedBroker(default_binary(), fixture, "test-only-admin-token-" + "b" * 32)
            with self.assertRaisesRegex(RuntimeError, "before readiness"):
                with server:
                    self.fail("invalid configuration must not become ready")
            self.assertIsNotNone(server.process.poll())

    def test_repeated_runs_get_distinct_instances_and_preserve_source(self):
        repo_source = Path(__file__).resolve().parents[2] / "crates/server/src/main.rs"
        original = repo_source.read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            instances = set()
            for _ in range(2):
                code, directory = run(default_binary(), Path(temp))
                self.assertEqual(code, 0)
                report = json.loads((directory / "minimal_demo.json").read_text(encoding="utf-8"))
                self.assertTrue(report["server_stopped"])
                self.assertTrue(all(check["pass"] for check in report["checks"]))
                instances.add(report["instance_id"])
            self.assertEqual(len(instances), 2)
        self.assertEqual(repo_source.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
