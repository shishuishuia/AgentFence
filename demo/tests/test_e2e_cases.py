"""Real full-suite execution plus deliberate corruption of stored evidence."""

from contextlib import redirect_stdout
from copy import deepcopy
import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

from demo.e2e_cases import run
from demo.e2e_spec import CASES
from demo.e2e_support import CaseFailure, safe_evidence
from demo.e2e_verify import validate_case, verify_run
from demo.runtime import default_binary


@unittest.skipUnless(default_binary().is_file(), "build release Rust before real HTTP black-box tests")
class BlackboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        with redirect_stdout(io.StringIO()):
            cls.code, cls.directory = run(default_binary(), Path(cls.temp.name))
        cls.public = cls.directory / "public"
        cls.report = json.loads((cls.public / "security_report.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def evidence(self, case_id):
        case = deepcopy(next(case for case in self.report["cases"] if case["case_id"] == case_id))
        requests = [json.loads(line) for line in (self.public / "responses" / f"{case_id}.jsonl").read_text(encoding="utf-8").splitlines()]
        events = [json.loads(line) for line in (self.public / "events" / f"{case_id}.jsonl").read_text(encoding="utf-8").splitlines()]
        return case, requests, events

    def test_all_fixed_cases_and_required_variants(self):
        self.assertEqual([case["case_id"] for case in self.report["cases"]], list(CASES))
        self.assertEqual(self.report["counts"]["failed"], 0, [(case["case_id"], case.get("failure")) for case in self.report["cases"] if case["verdict"] == "failed"])
        skipped = {case["case_id"] for case in self.report["cases"] if case["verdict"] == "skipped"}
        self.assertLessEqual(skipped, {"D17", "D18"})
        if os.name != "nt":
            self.assertEqual(skipped, set(), "Linux acceptance must execute actual file and directory symlinks")
        if skipped:
            self.assertEqual(self.code, 2)
            self.assertEqual(self.report["status"], "partial")
            with self.assertRaisesRegex(CaseFailure, "skipped"):
                verify_run(self.directory)
        else:
            self.assertEqual(self.code, 0)
            self.assertEqual(verify_run(self.directory)["passed"], 34)
            self.assertEqual(verify_run(self.public, evidence_only=True)["passed"], 34)
        for case in self.report["cases"]:
            if case["verdict"] == "passed":
                validate_case(*self.evidence(case["case_id"]))

    def test_response_and_request_redactions_retain_hashes_not_values(self):
        _, requests, _ = self.evidence("A01")
        created = next(request for request in requests if request["label"] == "case:main")
        self.assertEqual(created["body"]["agent_token"], "[REDACTED]")
        self.assertTrue(created["redactions"]["/agent_token"]["hex256"])
        _, requests, _ = self.evidence("A05")
        write = next(request for request in requests if request["label"] == "case:main")
        self.assertEqual(write["request_body"]["content"], "[REDACTED]")
        self.assertIn("sha256", write["request_redactions"]["/content"])

    def test_actual_secret_or_content_in_export_is_rejected(self):
        for value in ({"agent_token": "not redacted"}, {"content": "not redacted"}, {"message": "actual-secret"}):
            with self.subTest(value=value), self.assertRaises(CaseFailure):
                safe_evidence(value, ["actual-secret"])

    def test_changed_execution_flag_and_grant_consumption_are_rejected(self):
        case, requests, events = self.evidence("A05")
        requests[case["probes"][0]["request_index"]]["body"]["executed"] = False
        with self.assertRaises(CaseFailure):
            validate_case(case, requests, events)
        case, requests, events = self.evidence("A05")
        task = case["probes"][0]["task_id"]
        case["probes"][0]["after"]["tasks"][task]["grants"][0]["remaining_uses"] = 1
        with self.assertRaises(CaseFailure):
            validate_case(case, requests, events)

    def test_unintended_file_change_and_lost_variant_are_rejected(self):
        case, requests, events = self.evidence("A05")
        case["probes"][0]["after"]["files"]["workspace/Cargo.toml"]["sha256"] = "tampered"
        with self.assertRaises(CaseFailure):
            validate_case(case, requests, events)
        case, requests, events = self.evidence("D08")
        case["probes"].pop()
        with self.assertRaisesRegex(CaseFailure, "variant"):
            validate_case(case, requests, events)

    def test_duplicate_pagination_and_missing_audit_are_rejected(self):
        case, requests, events = self.evidence("A10")
        first = requests[case["probes"][0]["request_index"]]["body"]["events"]
        first.append(deepcopy(first[0]))
        with self.assertRaises(CaseFailure):
            validate_case(case, requests, events)
        case, requests, events = self.evidence("A05")
        rid = requests[case["probes"][0]["request_index"]]["body"]["request_id"]
        events = [event for event in events if not (event["request_id"] == rid and event["event_type"] == "result")]
        with self.assertRaises(CaseFailure):
            validate_case(case, requests, events)

    def test_retained_fixture_tampering_is_rejected(self):
        target = self.directory / "fixtures/A05/workspace/src/main.rs"
        original = target.read_bytes()
        try:
            target.write_bytes(b"tampered\n")
            with self.assertRaisesRegex(CaseFailure, "fixture files"):
                verify_run(self.directory)
        finally:
            target.write_bytes(original)

    def test_csv_must_match_all_response_and_proof_fields(self):
        path = self.public / "security_cases.csv"
        original = path.read_bytes()
        try:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            rows[0]["actual_http_status"] = "500"
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(CaseFailure, "CSV"):
                verify_run(self.directory)
        finally:
            path.write_bytes(original)

    def test_retained_disk_audit_tampering_is_rejected(self):
        path = self.directory / "fixtures/A01/audit.jsonl"
        original = path.read_bytes()
        try:
            path.write_bytes(b"{}\n")
            with self.assertRaisesRegex(CaseFailure, "disk audit"):
                verify_run(self.directory)
        finally:
            path.write_bytes(original)
