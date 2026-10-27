"""Actual permission transitions plus rejection of altered interval/probe evidence."""

from contextlib import redirect_stdout
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest

from bench.run_windows import run
from bench.runtime import default_binary
from bench.stats import BenchError
from bench.verify_windows import verify_run
from bench.window_stats import WindowConfig, interval_rows, reduction, union_ns


class WindowMathTests(unittest.TestCase):
    def test_union_merges_overlap_touching_nesting_and_preserves_gaps(self):
        self.assertEqual(union_ns([(10,20),(0,12),(3,7),(20,25),(30,35)]),30)
        self.assertEqual(union_ns([]),0)
        self.assertEqual(union_ns([(1,1)]),0)
        for values in ([(2,1)],[(True,2)],[(1.0,2)]):
            with self.subTest(values=values),self.assertRaises(BenchError):
                union_ns(values)
        self.assertIsNone(reduction(0,0))
        self.assertEqual(reduction(25,100),.75)

    def test_formal_bounds_and_smoke_are_distinct(self):
        for arguments in ({"rounds":2},{"window_seconds":float("nan")},{"ttl_seconds":3},{"rounds":True}):
            with self.subTest(arguments=arguments),self.assertRaises(BenchError):
                WindowConfig(**arguments)
        self.assertFalse(WindowConfig(1,3,1,formal=False).formal)

    def test_earliest_of_all_invalidation_candidates_is_reconstructed(self):
        record = {"key":"r01-phase-enforced","round":1,"mode":"enforced","scenario":"phase","task_id":"t",
                  "window_start_ns":0,"window_end_ns":6_000_000_000}
        grant = {"label":"grant:1","started_ns":100,"finished_ns":200,"status":201,"method":"POST","path":"/grants",
                 "request_body":{"ttl_seconds":1},"body":{"grant_id":"g"}}
        phase = {"label":"invalidate:phase","started_ns":300,"finished_ns":400,"status":200,"method":"POST","path":"/phase",
                 "request_body":{"phase":"verify"},"body":{}}
        row = interval_rows(record,[grant,phase])[0]
        self.assertEqual((row["end_cause"],row["end_lower_ns"],row["end_upper_ns"]),("phase",300,400))
        ambiguous = deepcopy(phase)
        ambiguous.update(started_ns=1_000_000_150,finished_ns=1_000_000_250)
        with self.assertRaisesRegex(BenchError,"ambiguous"):
            interval_rows(record,[grant,ambiguous])


@unittest.skipUnless(default_binary().is_file(),"build release http_experiment before real permission tests")
class WindowObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        with redirect_stdout(io.StringIO()):
            code,cls.directory = run(default_binary(),Path(cls.temp.name),WindowConfig(1,4,1,formal=False))
        cls.public = cls.directory / "public"
        if code:
            raise RuntimeError((cls.public / "permission_summary.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_actual_four_causes_and_multiple_authorizations(self):
        result = verify_run(self.directory)
        self.assertEqual((result["runs"],result["intervals"],result["comparisons"]),(8,10,4))
        self.assertFalse(result["formal"])
        self.assertEqual(verify_run(self.public,evidence_only=True)["runs"],8)
        summary = json.loads((self.public / "permission_summary.json").read_text())
        self.assertTrue(all(row["dynamic_lower_ns"] <= row["dynamic_estimated_ns"] <= row["dynamic_upper_ns"] < row["static_duration_ns"] for row in summary["comparisons"]))

    def test_interval_csv_cannot_be_shortened_to_claim_a_larger_reduction(self):
        path = self.public / "permission_windows.csv"
        original = path.read_bytes()
        try:
            with path.open(encoding="utf-8-sig",newline="") as stream:
                rows = list(csv.DictReader(stream))
            rows[0]["estimated_duration_ns"] = "1"
            with path.open("w",encoding="utf-8",newline="") as stream:
                writer = csv.DictWriter(stream,fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(BenchError,"CSV"):
                verify_run(self.directory)
        finally:
            path.write_bytes(original)

    def test_probe_expected_outcomes_are_not_learned_from_actual_responses(self):
        path = self.public / "runs.json"
        original = path.read_bytes()
        try:
            records = json.loads(original)
            first = next(record for record in records if record["mode"] == "enforced" and record["scenario"] == "phase")
            first["probes"][-1]["expected_dynamic_reason"] = "OK"
            path.write_text(json.dumps(records))
            with self.assertRaisesRegex(BenchError,"expected outcome"):
                verify_run(self.directory)
        finally:
            path.write_bytes(original)

    def test_removed_second_grant_interval_is_rejected(self):
        path = self.public / "responses/r01-ttl-enforced.jsonl"
        original = path.read_bytes()
        try:
            requests = [json.loads(line) for line in path.read_text().splitlines()]
            next(request for request in requests if request["label"] == "grant:2")["label"] = "hidden"
            path.write_text("".join(json.dumps(request)+"\n" for request in requests))
            with self.assertRaisesRegex(BenchError,"interval"):
                verify_run(self.directory)
        finally:
            path.write_bytes(original)

    def test_changed_retained_target_is_rejected(self):
        path = self.directory / "fixtures/r01-ttl-enforced/workspace/src/main.rs"
        original = path.read_bytes()
        try:
            path.write_bytes(b"tamper")
            with self.assertRaisesRegex(BenchError,"files"):
                verify_run(self.directory)
        finally:
            path.write_bytes(original)
