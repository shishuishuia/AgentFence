"""Full-workload Linux RSS observation and independent evidence-corruption checks."""

from contextlib import redirect_stdout
import csv
import io
import json
from pathlib import Path
import platform
import tempfile
import unittest

from bench.resources import parse_status
from bench.run_resources import run
from bench.stats import BenchError
from bench.verify_resources import verify_run
from demo.fixture import prepare_fixture
from demo.runtime import ManagedBroker, default_binary


class MemoryCounterTests(unittest.TestCase):
    def test_proc_units_and_high_water_mark_must_be_consistent(self):
        self.assertEqual(parse_status("Name:\trust\nVmHWM:\t321 kB\nVmRSS:\t123 kB\n"),(123,321))
        for text in ("VmRSS: 1 kB\n", "VmRSS: 2 kB\nVmHWM: 1 kB\n", "VmRSS: -1 kB\nVmHWM: 1 kB\n", "VmRSS: 1 MB\nVmHWM: 2 MB\n", "VmRSS: 1 kB\nVmRSS: 1 kB\nVmHWM: 2 kB\n"):
            with self.subTest(text=text),self.assertRaises(BenchError):
                parse_status(text)


@unittest.skipUnless(platform.system() == "Linux" and default_binary().is_file(),"full RSS acceptance needs Linux/WSL and release Rust")
class ResourceWorkloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        with redirect_stdout(io.StringIO()):
            cls.directory = run(default_binary(),Path(cls.temp.name))
        cls.public = cls.directory / "public"

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_full_34_cases_and_both_restart_instances_are_observed(self):
        result = verify_run(self.directory)
        self.assertEqual((result["cases"],result["processes"]),(34,35))
        self.assertGreater(result["peak_rss_kib"],0)
        self.assertGreaterEqual(result["samples"],70)
        self.assertFalse(verify_run(self.public,evidence_only=True)["fixture_files_checked"])

    def test_summary_cannot_invent_a_lower_peak_or_omit_an_instance(self):
        path = self.public / "rss_summary.json"
        original = path.read_bytes()
        try:
            for change in ("peak","coverage"):
                summary = json.loads(original)
                if change == "peak":
                    summary["peak_rss_kib"] = 1
                else:
                    summary["processes"].pop()
                path.write_text(json.dumps(summary))
                with self.subTest(change=change),self.assertRaises(BenchError):
                    verify_run(self.directory)
        finally:
            path.write_bytes(original)

    def test_duplicate_memory_samples_are_rejected(self):
        path = self.public / "rss_samples.csv"
        original = path.read_bytes()
        try:
            with path.open(encoding="utf-8-sig",newline="") as stream:
                rows = list(csv.DictReader(stream))
            rows[1] = rows[0]
            with path.open("w",encoding="utf-8",newline="") as stream:
                writer = csv.DictWriter(stream,fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaises(BenchError):
                verify_run(self.directory)
        finally:
            path.write_bytes(original)

    def test_sampler_failure_still_cleans_up_the_owned_server(self):
        class BrokenMonitor:
            def start(self):
                raise RuntimeError("monitor start failure")
            def close(self):
                raise RuntimeError("monitor close failure")
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "fixture"
            prepare_fixture(fixture)
            child = ManagedBroker(default_binary(),fixture,"x"*64,monitor_factory=lambda *_:BrokenMonitor())
            with self.assertRaisesRegex(RuntimeError,"monitor"):
                with child:
                    self.fail("failed sampler must not enter workload")
            self.assertIsNotNone(child.process.poll())
