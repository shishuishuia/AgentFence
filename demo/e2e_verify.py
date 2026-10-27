"""Recheck stored HTTP evidence and, when available, the owned fixture files."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from demo.e2e_spec import CASES, LABELS, rule
from demo.e2e_support import check, csv_rows, digest, file_state, safe_evidence, sanitize, validate_audit
from demo.fixture import AFTER, BEFORE


def validate_probe(case_id, probe, requests):
    expected = rule(case_id)
    response = requests[probe["request_index"]]
    body = response["body"]
    check(response["label"] == "case:" + probe["label"], "probe refers to a different request")
    check(response["status"] == expected["status"], "incorrect HTTP status")
    if "reason_code" in body:
        check(body["reason_code"] == expected["reason"], "incorrect response reason_code")
    if response["path"] == "/v1/tool/execute":
        check(body.get("allowed") is (case_id.startswith("A") or case_id == "D22"), "incorrect allowed flag")
        check(body.get("executed") is case_id.startswith("A"), "incorrect executed flag")
        if not case_id.startswith("A"):
            check(body.get("data") is None, "failed tool call returned file data")
    before, after = probe["before"], probe["after"]
    effect = expected["effect"]
    task_id = probe.get("task_id")
    submitted = response.get("request_body") or {}
    if case_id == "D03":
        check(response["identity"]["role"] == "agent" and response["identity"]["bound_task_id"] != submitted["task_id"], "D03 did not use another task identity")
    paths = {"D13": "Cargo.toml", "D14": "unlisted.txt", "D16": "../simulated-private.txt", "D17": "link-file", "D18": "link-dir/file.rs", "D19": "src/main.rs.bak"}
    if case_id in paths:
        check(submitted.get("path") == paths[case_id], "incorrect fixed path variant")
    if case_id in {"D17", "D18"}:
        link = "link-file" if case_id == "D17" else "link-dir"
        check(before["files"]["workspace/" + link]["kind"] == "link", "symlink case did not contain a real link")
        check(submitted["path"] in before["tasks"][task_id]["allowed_paths"], "symlink request was not explicitly whitelisted")
    if case_id in {"D10", "D11", "D12"}:
        grants = before["tasks"][task_id]["grants"]
        check(len(grants) == 1 and grants[0]["status"] == {"D10": "expired", "D11": "revoked", "D12": "used_up"}[case_id], "incorrect lease precondition")
    if case_id == "D20":
        check(submitted["path"] == ("src\\main.rs" if probe["label"] == "backslash" else "src/main.rs\x00"), "incorrect control/backslash variant")
    if case_id == "D21":
        check(response["request_redactions"]["/content"]["bytes"] > 65536, "D21 did not exceed file limit")
        check((response["request_json_bytes"] > 524288) is (probe["label"] == "body_limit"), "D21 did not exercise distinct file/transport limits")
    if effect in {"unchanged", "read_before", "read_after"}:
        check(before == after, "read/rejected/query request changed file, phase, timestamp or lease")
    else:
        predicted = deepcopy(before)
        if effect == "create":
            task_id = body["task_id"]
            check(set(after["tasks"]) - set(before["tasks"]) == {task_id}, "create did not add exactly one task")
            task = after["tasks"][task_id]
            check(task["phase"] == "inspect" and task["grants"] == [], "incorrect initial task state")
            for key in ("task_id", "workspace_id", "phase", "created_at", "updated_at"):
                check(task[key] == body[key], "created response and query snapshot disagree")
            check(response["redactions"].get("/agent_token", {}).get("hex256") is True, "task credential was not 256-bit hex")
            predicted["tasks"][task_id] = task
        else:
            task = predicted["tasks"][task_id]
            observed = after["tasks"][task_id]
            task["updated_at"] = observed["updated_at"]
            if effect in {"edit", "verify", "done"}:
                check(before["tasks"][task_id]["phase"] == {"edit": "inspect", "verify": "edit", "done": "verify"}[effect], "incorrect phase precondition")
                task["phase"] = effect
                for grant in task["grants"]:
                    grant["status"] = "stage_inactive"
                check(body["phase"] == effect, "phase response is incorrect")
            elif effect == "grant":
                grant = {key: value for key, value in body.items() if key != "request_id"}
                check(task["phase"] == "edit" and grant["remaining_uses"] == 1 and grant["status"] == "active" and not grant["revoked"], "incorrect grant snapshot")
                expiry = datetime.fromisoformat(grant["expires_at"].replace("Z", "+00:00"))
                issued = datetime.fromisoformat(observed["updated_at"].replace("Z", "+00:00"))
                check((expiry - issued).total_seconds() == 60, "issued grant TTL differs from requested 60 seconds")
                task["grants"].append(grant)
            elif effect == "write":
                check(task["phase"] == "edit" and len(task["grants"]) == 1 and task["grants"][0]["remaining_uses"] == 1, "write precondition is not one valid use")
                grant = task["grants"][0]
                grant["remaining_uses"] = 0
                grant["status"] = "used_up"
                predicted["files"]["workspace/src/main.rs"] = {"kind": "file", "sha256": digest(AFTER.encode()), "bytes": len(AFTER.encode())}
                check(body["data"]["bytes_written"] == len(AFTER.encode()) and body["data"]["remaining_uses"] == 0 and body["data"]["grant_id"] == grant["grant_id"], "incorrect write response/consumption")
        check(predicted == after, "operation changed an unexpected file/task/lease or omitted its intended change")
    if effect in {"read_before", "read_after"}:
        content = BEFORE if effect == "read_before" else AFTER
        proof = response["redactions"].get("/data/content", {})
        check(proof.get("sha256") == digest(content.encode()) and proof.get("bytes") == len(content.encode()), "read content hash mismatch")
        check(body["data"]["bytes_read"] == len(content.encode()), "incorrect UTF-8 byte count")


def validate_case(case, requests, events):
    case_id = case["case_id"]
    check(case_id in CASES, "unknown fixed case")
    labels = [p["label"] for p in case["probes"]]
    if case_id == "A10":
        check(len(labels) >= 2 and labels == [f"page_{i}" for i in range(1, len(labels) + 1)], "A10 did not exercise multiple pages")
        pages = [requests[p["request_index"]] for p in case["probes"]]
        task_id = case["metadata"]["task_id"]
        cursor = None
        returned = []
        for index, page in enumerate(pages):
            params = parse_qs(urlsplit(page["path"]).query)
            check(params.get("task_id") == [task_id], "pagination lost task filter")
            check(params.get("after", [None])[0] == cursor, "pagination did not use the server cursor verbatim")
            returned.extend(page["body"]["events"])
            check(page["body"]["has_more"] is (index < len(pages) - 1), "incorrect has_more/termination")
            cursor = page["body"]["next_cursor"]
        check(returned == [event for event in events if event["task_id"] == task_id], "pagination omitted, duplicated or mixed task events")
    else:
        check(labels == LABELS[case_id], "required input variant is missing, duplicated or reordered")
    for probe in case["probes"]:
        validate_probe(case_id, probe, requests)
    if case_id == "D23":
        check(case["metadata"]["old_instance"] != case["metadata"]["new_instance"], "D23 did not restart the service")
        check(case["probes"][0]["before"]["tasks"] == {}, "old tasks survived restart")
    validate_audit(requests, events)
    check(case.get("server_stopped") is True and case.get("api_matches_disk") is True, "process/disk audit verification is missing")
    safe_evidence([case, requests, events], [])


def verify_run(directory: Path, *, evidence_only=False):
    directory = directory.resolve()
    public = directory / "public" if (directory / "public").is_dir() else directory
    report = json.loads((public / "security_report.json").read_text(encoding="utf-8"))
    check(report["requested_cases"] == list(CASES), "suite does not contain all 34 fixed cases")
    check([case["case_id"] for case in report["cases"]] == list(CASES), "case list is incomplete or duplicated")
    all_requests = {}
    for case in report["cases"]:
        case_id = case["case_id"]
        all_requests[case_id] = [json.loads(line) for line in (public / "responses" / f"{case_id}.jsonl").read_text(encoding="utf-8").splitlines()]
    import csv
    with (public / "security_cases.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    predicted_rows = [{key: str(value) for key, value in row.items()} for row in csv_rows(report["cases"], all_requests)]
    check(rows == predicted_rows, "CSV disagrees with raw response/proof evidence")
    for case in report["cases"]:
        check(case["verdict"] == "passed", "a required case failed or was skipped")
        case_id = case["case_id"]
        requests = all_requests[case_id]
        events = [json.loads(line) for line in (public / "events" / f"{case_id}.jsonl").read_text(encoding="utf-8").splitlines()]
        validate_case(case, requests, events)
        if not evidence_only:
            fixture = directory / "fixtures" / case_id
            check(file_state(fixture) == case["final_files"], "retained fixture files changed after verification")
            raw = (fixture / "audit.jsonl").read_bytes()
            check(raw.endswith(b"\n"), "retained audit has incomplete trailing line")
            disk = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
            check(sanitize(disk, [], fixture) == events, "retained disk audit differs from exported events")
    check(report["status"] == "passed" and report["counts"] == {"passed": 34, "failed": 0, "skipped": 0}, "suite summary disagrees with verified cases")
    return {"passed": 34, "failed": 0, "skipped": 0, "fixture_files_checked": not evidence_only}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--evidence-only", action="store_true", help="portable export only; does not recheck retained fixture files")
    args = parser.parse_args()
    try:
        verify_run(args.directory, evidence_only=args.evidence_only)
    except Exception as error:
        print("BLACKBOX_VERIFY_FAIL: " + type(error).__name__)
        return 1
    print("BLACKBOX_EVIDENCE_PASS" if args.evidence_only else "BLACKBOX_VERIFY_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
