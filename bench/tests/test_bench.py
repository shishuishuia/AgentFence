"""Known statistics, protocol shape and real paired-process regression checks."""

from contextlib import redirect_stdout
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from bench.evidence import sample_from_request
from bench.run_bench import run
from bench.runtime import Experiment, default_binary
from bench.stats import BenchError, Config, distribution, percentile, summarize
from bench.verify import verify_run


class StatisticsTests(unittest.TestCase):
    def test_nearest_rank_known_distribution_and_positive_units(self):
        values = list(range(1, 101))
        result = distribution(values)
        self.assertEqual((result["p50_ns"], result["p95_ns"], result["p99_ns"]), (50, 95, 99))
        self.assertEqual(percentile([9], 99), 9)
        self.assertEqual(percentile(values, 100), 100)
        for bad in ([], [0], [-1], [1.5], [True]):
            with self.subTest(bad=bad), self.assertRaises(BenchError):
                distribution(bad)

    def test_formal_sampling_cannot_be_replaced_with_smoke(self):
        for options in ({"iterations": 99}, {"rounds": 2}, {"warmup": 0}, {"file_bytes": 65537}, {"iterations": True}):
            with self.subTest(options=options), self.assertRaises(BenchError):
                Config(**options)
        config = Config(iterations=1, rounds=1, warmup=1, file_bytes=32, formal=False)
        rows = [{"valid": True, "phase": "measured", "round": 1, "mode": mode, "scenario": scenario, "sequence": 1, "elapsed_ns": 100} for mode in ("enforced", "baseline") for scenario in ("read", "write", "deny")]
        result = summarize(rows, config)
        self.assertEqual(result["status"], "smoke_passed")
        self.assertFalse(result["qualified_sampling"])
        rows.pop()
        with self.assertRaises(BenchError):
            summarize(rows, config)

    def test_existing_fixture_and_missing_binary_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            sentinel = directory / "keep.txt"
            sentinel.write_text("keep")
            runtime = Experiment(directory / "missing.exe", directory, "baseline", "x" * 64)
            with self.assertRaises(BenchError):
                with runtime:
                    self.fail("missing binary should not start")
            self.assertEqual(sentinel.read_text(), "keep")
            self.assertIsNone(runtime.process)


@unittest.skipUnless(default_binary().is_file(), "build release http_experiment before real benchmark tests")
class PairedExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.config = Config(iterations=3, rounds=2, warmup=2, file_bytes=64, formal=False)
        with redirect_stdout(io.StringIO()):
            cls.code, cls.directory = run(default_binary(), Path(cls.temp.name), cls.config)
        cls.public = cls.directory / "public"
        cls.summary = json.loads((cls.public / "bench_summary.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_actual_paired_samples_exclude_warmup_and_preparation(self):
        self.assertEqual(self.code, 0, self.summary)
        result = verify_run(self.directory)
        self.assertEqual((result["measured"], result["warmup"], result["runs"]), (36, 24, 4))
        self.assertFalse(result["formal"])
        self.assertEqual(verify_run(self.public, evidence_only=True)["measured"], 36)
        runs = json.loads((self.public / "runs.json").read_text(encoding="utf-8"))
        self.assertEqual([r["mode"] for r in runs], ["enforced", "baseline", "baseline", "enforced"])
        for record in runs:
            for batch in record["batches"]:
                self.assertTrue(batch["revoked"]["revoked"])
                self.assertEqual(batch["after"]["remaining_uses"], 0 if record["mode"] == "enforced" else batch["uses"])

    def test_invalid_baseline_response_is_rejected(self):
        path = self.public / "responses/r01-baseline.jsonl"
        requests = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        row = deepcopy(next(r for r in requests if r["phase"] == "measured" and r["scenario"] == "write"))
        row["body"]["data"]["remaining_uses"] = 1
        with self.assertRaises(BenchError):
            sample_from_request(row, self.config)

    def test_warmup_cannot_be_inserted_into_measured_csv(self):
        path = self.public / "bench_samples.csv"
        original = path.read_bytes()
        try:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            rows[0]["phase"] = "warmup"
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(BenchError, "CSV"):
                verify_run(self.directory)
        finally:
            path.write_bytes(original)

    def test_summary_cannot_claim_faster_unobserved_percentile(self):
        path = self.public / "bench_summary.json"
        original = path.read_bytes()
        try:
            summary = deepcopy(self.summary)
            summary["groups"][0]["p95_ns"] = 1
            path.write_text(json.dumps(summary), encoding="utf-8")
            with self.assertRaisesRegex(BenchError, "summary"):
                verify_run(self.directory)
        finally:
            path.write_bytes(original)

    def test_changed_fixture_and_missing_audit_are_rejected(self):
        target = self.directory / "fixtures/r01-enforced/workspace/src/main.rs"
        original = target.read_bytes()
        try:
            target.write_bytes(b"tamper\n")
            with self.assertRaisesRegex(BenchError, "workspace"):
                verify_run(self.directory)
        finally:
            target.write_bytes(original)
        path = self.directory / "fixtures/r01-enforced/audit.jsonl"
        original = path.read_bytes()
        try:
            path.write_bytes(b"{}\n")
            with self.assertRaisesRegex(BenchError, "audit"):
                verify_run(self.directory)
        finally:
            path.write_bytes(original)

    def test_failed_owned_process_context_is_cleaned_up(self):
        with tempfile.TemporaryDirectory() as temp:
            server = Experiment(default_binary(), Path(temp) / "fixture", "enforced", "x" * 64)
            with self.assertRaisesRegex(RuntimeError, "intentional"):
                with server:
                    raise RuntimeError("intentional")
            self.assertIsNotNone(server.process.poll())
            self.assertTrue(all(not thread.is_alive() for thread in server.readers))
