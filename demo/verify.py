"""Independently check file evidence and correlate HTTP outcomes with JSONL."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import sys

from .fixture import AFTER, BEFORE, PRIVATE

# Explicit expected outcomes; do not learn expected values from observed output.
EXPECTED = {
    "create_task": (201, "task_created", "inspect", None, None, "OK"),
    "inspect_read": (200, "result", "inspect", "read_file", "src/main.rs", "OK"),
    "inspect_write_denied": (403, "result", "inspect", "write_file", "src/main.rs", "INVALID_STAGE"),
    "advance_edit": (200, "phase_changed", "edit", None, None, "OK"),
    "issue_grant": (201, "grant_issued", "edit", "write_file", "src/main.rs", "OK"),
    "edit_write": (200, "result", "edit", "write_file", "src/main.rs", "OK"),
    "reuse_denied": (403, "result", "edit", "write_file", "src/main.rs", "LEASE_USED_UP"),
    "outside_read_denied": (403, "result", "edit", "read_file", "../simulated-private.txt", "PATH_DENIED"),
    "advance_verify": (200, "phase_changed", "verify", None, None, "OK"),
    "verify_read": (200, "result", "verify", "read_file", "src/main.rs", "OK"),
    "verify_write_denied": (403, "result", "verify", "write_file", "src/main.rs", "INVALID_STAGE"),
    "advance_done": (200, "phase_changed", "done", None, None, "OK"),
    "done_read_denied": (403, "result", "done", "read_file", "src/main.rs", "INVALID_STAGE"),
}


class VerificationError(RuntimeError):
    """A failed invariant; error descriptions contain no file or token values."""


def check(condition: bool, message: str):
    if not condition:
        raise VerificationError(message)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def snapshot(fixture: Path, detail: dict) -> dict:
    """Read target fixtures only in the verifier, never in the Agent adapter."""
    hashes = {}
    for path in sorted((fixture / "workspace").rglob("*")):
        check(not path.is_symlink(), "unexpected symlink in the demo fixture")
        if path.is_file():
            hashes[path.relative_to(fixture).as_posix()] = sha(path.read_bytes())
    private = fixture / "simulated-private.txt"
    hashes["simulated-private.txt"] = sha(private.read_bytes())
    return {"files": hashes, "phase": detail["phase"], "updated_at": detail["updated_at"], "grants": detail["grants"]}


def check_no_sensitive_data(value, secrets=()):
    """Fail on leaks; do not silently redact faulty audit data and mark it passed."""
    forbidden = {"authorization", "agent_token", "admin_token", "content"}

    def visit(item):
        if isinstance(item, dict):
            check(not any(str(key).lower() in forbidden for key in item), "sensitive field found in evidence")
            for nested in item.values():
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)
        elif isinstance(item, str):
            check(not any(secret and secret in item for secret in secrets), "credential found in evidence")
            check(not any(text.strip() in item for text in (BEFORE, AFTER, PRIVATE)), "file content found in evidence")

    visit(value)


def verify_run(report: dict, fixture: Path, events: list[dict], *, secrets=()) -> dict:
    """Recompute checks from stored file/lease proofs, disk audit and API events."""
    checks = report["checks"]
    check([item["case"] for item in checks] == list(EXPECTED), "missing, duplicated or reordered workflow steps")
    check(report.get("server_stopped") is True, "owned server was not confirmed stopped")
    expected_hashes = {
        "workspace/src/main.rs": sha(AFTER.encode("utf-8")),
        "workspace/Cargo.toml": report["initial_files"]["workspace/Cargo.toml"],
        "simulated-private.txt": sha(PRIVATE.encode("utf-8")),
    }
    final = snapshot(fixture, report["final_detail"])
    check(final["files"] == expected_hashes, "final files differ from the expected fixture")
    check(final["phase"] == "done", "final phase is not done")
    check(len(final["grants"]) == 1 and final["grants"][0]["remaining_uses"] == 0, "final grant was not exhausted")
    check(final["grants"][0]["status"] == "stage_inactive", "final lease is not stage-inactive")

    audit = (fixture / "audit.jsonl").read_bytes()
    check(bool(audit) and audit.endswith(b"\n"), "audit has an incomplete final line")
    try:
        records = [json.loads(line) for line in audit.decode("utf-8").splitlines()]
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise VerificationError("audit is not complete UTF-8 JSONL") from None
    check(records == events, "paginated API events differ from the disk audit")
    check([event["event_id"] for event in events] == list(range(1, len(events) + 1)), "audit event IDs are missing, duplicated or reordered")
    check(all(type(event["event_id"]) is int for event in events), "audit event ID is not an integer")
    by_request = defaultdict(list)
    for event in events:
        check(event["instance_id"] == report["instance_id"], "mixed audit instances")
        check(event["task_id"] == report["task_id"], "mixed audit tasks")
        by_request[event["request_id"]].append(event)
    request_ids = [item["request_id"] for item in checks]
    check(len(set(request_ids)) == len(checks) and all(isinstance(rid, str) and rid for rid in request_ids), "missing or duplicated request IDs")
    check(set(by_request) == set(request_ids), "unmatched audit request IDs")
    denied = 0
    for item in checks:
        name = item["case"]
        status, kind, phase, tool, path, reason = EXPECTED[name]
        check(item["pass"] is True and item["http_status"] == status, f"{name}: HTTP check failed")
        group = by_request[item["request_id"]]
        completed = [event for event in group if event["event_type"] != "intent"]
        intents = [event for event in group if event["event_type"] == "intent"]
        check(len(completed) == 1 and len(intents) <= 1, f"{name}: ambiguous intent/result association")
        result = completed[0]
        for key, expected in (("event_type", kind), ("phase", phase), ("tool", tool), ("path", path), ("reason_code", reason)):
            check(result[key] == expected, f"{name}: audit {key} mismatch")
        allowed = status < 300
        check(result["allowed"] is allowed and result["executed"] is allowed, f"{name}: audit decision/execution mismatch")
        if kind == "result":
            check(item["reason_code"] == reason and item["allowed"] is allowed and item["executed"] is allowed, f"{name}: response outcome mismatch")
            if allowed:
                check(len(intents) == 1, f"{name}: successful tool call has no intent")
                data = item["data_summary"]
                content = BEFORE if name == "inspect_read" else AFTER
                if tool == "read_file":
                    check(data.get("content_sha256") == sha(content.encode("utf-8")), f"{name}: read content hash mismatch")
                    check(data.get("bytes_read") == len(content.encode("utf-8")), f"{name}: read UTF-8 byte count mismatch")
                else:
                    check(data.get("bytes_written") == len(AFTER.encode("utf-8")), f"{name}: write UTF-8 byte count mismatch")
                    check(data.get("remaining_uses") == 0 and data.get("grant_id") == result["grant_id"], f"{name}: response/audit lease mismatch")
        else:
            check(not intents, f"{name}: management event unexpectedly has an intent")
        for intent in intents:
            check(intent["reason_code"] == "PENDING" and intent["allowed"] is False and intent["executed"] is False, f"{name}: intent incorrectly implies completion")
            check(intent["event_id"] < result["event_id"], f"{name}: intent is not before result")
            check(all(intent[key] == result[key] for key in ("tool", "path", "phase")), f"{name}: intent resource mismatch")
        if status == 403:
            denied += 1
            proof = item["denial_evidence"]
            check(proof["before"] == proof["after"], f"{name}: denial changed a file, task or lease")
            expected_main = BEFORE if name == "inspect_write_denied" else AFTER
            check(proof["before"]["files"]["workspace/src/main.rs"] == sha(expected_main.encode("utf-8")), f"{name}: incorrect target state before denial")
    write = next(item for item in checks if item["case"] == "edit_write")
    before, after = write["write_evidence"]["before"], write["write_evidence"]["after"]
    changed = {path for path in set(before["files"]) | set(after["files"]) if before["files"].get(path) != after["files"].get(path)}
    check(changed == {"workspace/src/main.rs"}, "successful write changed an unintended file")
    check(before["grants"][0]["remaining_uses"] == 1 and after["grants"][0]["remaining_uses"] == 0, "successful write did not consume exactly one use")
    check(report["initial_files"]["workspace/src/main.rs"] == sha(BEFORE.encode()), "initial target hash mismatch")
    check(report["target_sha256"] == sha(AFTER.encode("utf-8")), "reported target hash mismatch")
    check_no_sensitive_data([report, events, records], secrets)
    return {"passed": True, "workflow_checks": len(checks), "denials_checked": denied,
            "tool_results": sum(event["event_type"] == "result" for event in events),
            "audit_events": len(events), "audit_sha256": sha(audit),
            "api_matches_disk": True, "no_sensitive_data": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    args = parser.parse_args()
    try:
        report = json.loads((args.run_directory / "minimal_demo.json").read_text(encoding="utf-8"))
        events = [json.loads(line) for line in (args.run_directory / "demo_events.jsonl").read_text(encoding="utf-8").splitlines()]
        verify_run(report, args.run_directory / "fixture", events)
    except (Exception, KeyboardInterrupt) as error:
        print(f"VERIFY_FAIL: {type(error).__name__}", file=sys.stderr)
        return 1
    print("MINIMAL_VERIFY_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
