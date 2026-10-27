"""Use real Rust and proxy children to verify display and ownership cleanup."""

from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import json
from pathlib import Path
import tempfile
import unittest

from demo.run_dashboard import Dashboard
from demo.runtime import default_binary
from integrations import BrokerHttp


@unittest.skipUnless(default_binary().is_file(), "build release Rust before dashboard tests")
class DashboardTests(unittest.TestCase):
    def test_live_stages_retention_and_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            dashboard = Dashboard(default_binary(), Path(temp), "dev", port=0)
            with dashboard:
                transport = BrokerHttp(dashboard.url)
                observed = []

                def checkpoint(name, task_id):
                    detail = transport.request("GET", "/api/tasks/" + task_id).require(200)
                    observed.append((name, detail["phase"], detail["grants"]))

                report = dashboard.play(checkpoint)
                self.assertEqual(report["status"], "passed")
                states = {name: (phase, grants) for name, phase, grants in observed}
                self.assertEqual(states["grant_active"][1][0]["status"], "active")
                self.assertEqual(states["grant_used_up"][1][0]["status"], "used_up")
                self.assertEqual(states["verify"][1][0]["status"], "stage_inactive")
                self.assertEqual(transport.request("GET", "/api/tasks").require(200)["total"], 1)
                self.assertIsNone(dashboard.broker.process.poll())
                self.assertIsNone(dashboard.proxy.poll())
                saved = (dashboard.directory / "dashboard_demo.json").read_text(encoding="utf-8")
                self.assertNotIn("agent_token", saved)
            self.assertIsNotNone(dashboard.broker.process.poll())
            self.assertIsNotNone(dashboard.proxy.poll())

    def test_port_conflict_cleans_only_owned_services(self):
        class Handler(BaseHTTPRequestHandler):
            pass
        with tempfile.TemporaryDirectory() as temp, ThreadingHTTPServer(("127.0.0.1", 0), Handler) as existing:
            dashboard = Dashboard(default_binary(), Path(temp), "dev", port=existing.server_port)
            with self.assertRaises(RuntimeError):
                with dashboard:
                    self.fail("occupied port must fail")
            self.assertIsNotNone(dashboard.broker.process.poll())
            self.assertIsNotNone(dashboard.proxy.poll())
            self.assertGreater(existing.fileno(), -1)

    def test_exception_stops_both_and_new_run_has_new_instance(self):
        with tempfile.TemporaryDirectory() as temp:
            first = Dashboard(default_binary(), Path(temp), "dev", port=0)
            with self.assertRaisesRegex(RuntimeError, "intentional"):
                with first:
                    raise RuntimeError("intentional")
            with Dashboard(default_binary(), Path(temp), "dev", port=0) as second:
                self.assertNotEqual(first.instance, second.instance)
                self.assertNotEqual(first.directory, second.directory)
                self.assertEqual(BrokerHttp(second.url).request("GET", "/api/tasks").require(200)["total"], 0)
            self.assertIsNotNone(first.broker.process.poll())
            self.assertIsNotNone(first.proxy.poll())
