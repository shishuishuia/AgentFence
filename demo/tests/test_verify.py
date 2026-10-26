"""Corrupt independently produced real-run evidence and require verification failure."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from demo.run_minimal import run
from demo.runtime import default_binary
from demo.verify import VerificationError, verify_run


@unittest.skipUnless(default_binary().is_file(), "build the release Rust server before evidence tests")
class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        code, cls.directory = run(default_binary(), Path(cls.temp.name))
        if code != 0:
            cls.temp.cleanup()
            raise RuntimeError("real demo evidence setup failed")
        cls.original_report = json.loads((cls.directory / "minimal_demo.json").read_text(encoding="utf-8"))
        cls.original_events = [json.loads(line) for line in (cls.directory / "demo_events.jsonl").read_text(encoding="utf-8").splitlines()]
        cls.fixture = cls.directory / "fixture"
        cls.original_files = {path: path.read_bytes() for path in cls.fixture.rglob("*") if path.is_file()}

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.report = copy.deepcopy(self.original_report)
        self.events = copy.deepcopy(self.original_events)

    def tearDown(self):
        for path, data in self.original_files.items():
            path.write_bytes(data)

    def write_audit(self):
        for index, event in enumerate(self.events, 1):
            event["event_id"] = index
        (self.fixture / "audit.jsonl").write_text("".join(json.dumps(event) + "\n" for event in self.events), encoding="utf-8")

    def verify(self, **kwargs):
        return verify_run(self.report, self.fixture, self.events, **kwargs)

    def test_real_evidence_passes(self):
        summary = self.verify()
        self.assertEqual(summary["workflow_checks"], 13)
        self.assertEqual(summary["denials_checked"], 5)
        self.assertEqual(summary["tool_results"], 8)
        self.assertTrue(summary["api_matches_disk"])

    def test_changed_target_is_rejected(self):
        (self.fixture / "workspace/src/main.rs").write_text("wrong", encoding="utf-8")
        with self.assertRaisesRegex(VerificationError, "final files"):
            self.verify()

    def test_changed_private_file_is_rejected(self):
        (self.fixture / "simulated-private.txt").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(VerificationError, "final files"):
            self.verify()

    def test_missing_disk_event_is_rejected(self):
        audit = self.fixture / "audit.jsonl"
        audit.write_bytes(b"\n".join(audit.read_bytes().splitlines()[:-1]) + b"\n")
        with self.assertRaisesRegex(VerificationError, "differ from the disk"):
            self.verify()

    def test_incomplete_jsonl_tail_is_rejected(self):
        audit = self.fixture / "audit.jsonl"
        audit.write_bytes(audit.read_bytes().rstrip(b"\n"))
        with self.assertRaisesRegex(VerificationError, "incomplete final line"):
            self.verify()

    def test_duplicate_event_ids_are_rejected_even_if_api_matches_disk(self):
        self.events[-1]["event_id"] = self.events[-2]["event_id"]
        (self.fixture / "audit.jsonl").write_text("".join(json.dumps(event) + "\n" for event in self.events), encoding="utf-8")
        with self.assertRaisesRegex(VerificationError, "event IDs"):
            self.verify()

    def test_incorrect_reason_is_rejected_even_if_api_matches_disk(self):
        event = next(event for event in self.events if event["reason_code"] == "LEASE_USED_UP")
        event["reason_code"] = "OK"
        self.write_audit()
        with self.assertRaisesRegex(VerificationError, "reason_code mismatch"):
            self.verify()

    def test_denial_lease_consumption_is_rejected(self):
        item = next(item for item in self.report["checks"] if item["case"] == "reuse_denied")
        item["denial_evidence"]["after"]["grants"][0]["remaining_uses"] = 1
        with self.assertRaisesRegex(VerificationError, "denial changed"):
            self.verify()

    def test_unintended_successful_write_is_rejected(self):
        item = next(item for item in self.report["checks"] if item["case"] == "edit_write")
        item["write_evidence"]["after"]["files"]["workspace/Cargo.toml"] = "wrong"
        with self.assertRaisesRegex(VerificationError, "unintended file"):
            self.verify()

    def test_missing_success_intent_is_rejected(self):
        self.events = [event for event in self.events if event["event_type"] != "intent"]
        self.write_audit()
        with self.assertRaisesRegex(VerificationError, "has no intent"):
            self.verify()

    def test_incorrect_utf8_byte_count_is_rejected(self):
        item = next(item for item in self.report["checks"] if item["case"] == "edit_write")
        item["data_summary"]["bytes_written"] -= 1
        with self.assertRaisesRegex(VerificationError, "UTF-8 byte count"):
            self.verify()

    def test_credential_leak_is_rejected(self):
        secret = "ephemeral-private-credential"
        self.events[0]["unexpected_note"] = secret
        self.write_audit()
        with self.assertRaisesRegex(VerificationError, "credential"):
            self.verify(secrets=[secret])

    def test_content_field_is_rejected(self):
        self.events[0]["content"] = "unexpected file text"
        self.write_audit()
        with self.assertRaisesRegex(VerificationError, "sensitive field"):
            self.verify()


if __name__ == "__main__":
    unittest.main()
