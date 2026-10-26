"""Run the deterministic file-tool workflow against a fresh release Rust server."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sys
import uuid

from integrations import AdminClient, AgentClient, ApiResponse, BrokerHttp
from .fixture import AFTER, BEFORE, PRIVATE, prepare_fixture
from .runtime import ManagedBroker, REPO, default_binary


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(condition: bool, message: str):
    # Do not use `assert`: demo validation must also run under `python -O`.
    if not condition:
        raise RuntimeError(message)


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def execute_flow(http: BrokerHttp, admin_token: str, fixture: Path, checks: list[dict]) -> tuple[str, list[dict]]:
    admin = AdminClient(http, admin_token)

    def record(name: str, response: ApiResponse, status: int, reason: str | None = None) -> dict:
        body = response.payload
        entry = {
            "case": name, "http_status": response.status, "expected_status": status,
            "reason_code": body.get("reason_code"), "expected_reason": reason,
            "request_id": body.get("request_id"), "pass": False,
        }
        checks.append(entry)
        require(response.status == status, f"{name}: expected HTTP {status}, received {response.status}")
        if reason is not None:
            require(body.get("reason_code") == reason, f"{name}: unexpected reason_code; request_id={body.get('request_id')}")
            require(body.get("allowed") is (status == 200), f"{name}: incorrect allowed flag")
            require(body.get("executed") is (status == 200), f"{name}: incorrect executed flag")
        entry["pass"] = True
        return body

    created = record("create_task", admin.create_task("demo"), 201)
    require(created.get("phase") == "inspect", "create_task: initial phase is not inspect")
    task_id = created["task_id"]
    agent = AgentClient(http, task_id, created["agent_token"])
    target = fixture / "workspace/src/main.rs"

    def read(name: str, path: str, status: int, reason: str, content: str | None = None):
        body = record(name, agent.read_file(path), status, reason)
        if content is not None:
            require(body.get("data", {}).get("content") == content, f"{name}: content mismatch")
            require(body["data"].get("bytes_read") == len(content.encode("utf-8")), f"{name}: UTF-8 byte count mismatch")
        else:
            require(body.get("data") is None, f"{name}: denied read leaked data")

    read("inspect_read", "src/main.rs", 200, "OK", BEFORE)
    record("inspect_write_denied", agent.write_file("src/main.rs", AFTER), 403, "INVALID_STAGE")
    require(target.read_bytes() == BEFORE.encode(), "inspect_write_denied: target changed")

    record("advance_edit", admin.advance(task_id, "edit"), 200)
    grant = record("issue_grant", admin.issue_grant(task_id, "src/main.rs"), 201)
    require(grant.get("remaining_uses") == 1 and grant.get("status") == "active", "issue_grant: invalid snapshot")
    grant_id = grant["grant_id"]
    written = record("edit_write", agent.write_file("src/main.rs", AFTER, grant_id=grant_id), 200, "OK")
    require(written.get("data", {}).get("remaining_uses") == 0, "edit_write: lease was not consumed")
    require(written["data"].get("bytes_written") == len(AFTER.encode("utf-8")), "edit_write: UTF-8 byte count mismatch")
    require(target.read_bytes() == AFTER.encode("utf-8"), "edit_write: actual target differs")

    record("reuse_denied", agent.write_file("src/main.rs", "must not replace\n", grant_id=grant_id), 403, "LEASE_USED_UP")
    require(target.read_bytes() == AFTER.encode("utf-8"), "reuse_denied: target changed")
    read("outside_read_denied", "../simulated-private.txt", 403, "PATH_DENIED")
    require((fixture / "simulated-private.txt").read_bytes() == PRIVATE.encode(), "outside private fixture changed")

    record("advance_verify", admin.advance(task_id, "verify"), 200)
    read("verify_read", "src/main.rs", 200, "OK", AFTER)
    record("verify_write_denied", agent.write_file("src/main.rs", "must not replace\n", grant_id=grant_id), 403, "INVALID_STAGE")
    record("advance_done", admin.advance(task_id, "done"), 200)
    read("done_read_denied", "src/main.rs", 403, "INVALID_STAGE")
    require(target.read_bytes() == AFTER.encode("utf-8"), "final target changed unexpectedly")
    detail = admin.task(task_id).require(200)
    require(detail.get("phase") == "done", "task did not reach done")
    require(len(detail.get("grants", [])) == 1 and detail["grants"][0].get("remaining_uses") == 0, "final lease mismatch")

    events = []
    cursor = "0"
    for _ in range(100):
        page = admin.events(task_id=task_id, after=cursor, limit=2).require(200)
        events.extend(page["events"])
        if not page["has_more"]:
            break
        require(page["next_cursor"] != cursor, "event pagination did not advance")
        cursor = page["next_cursor"]
    else:
        raise RuntimeError("event pagination exceeded safety limit")
    return task_id, events


def run(binary: Path, results_parent: Path) -> tuple[int, Path]:
    results_parent.mkdir(parents=True, exist_ok=True)
    run_id = "local-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    directory = results_parent / run_id
    directory.mkdir(exist_ok=False)
    fixture = directory / "fixture"
    prepare_fixture(fixture)
    report = {"run_id": run_id, "status": "running", "checks": [], "started_at": datetime.now(timezone.utc).isoformat()}
    report_path = directory / "minimal_demo.json"
    admin_token = secrets.token_hex(32)
    server = ManagedBroker(binary, fixture, admin_token)
    try:
        with server:
            http = BrokerHttp(server.base_url)
            report["instance_id"] = http.health().require(200)["instance_id"]
            task_id, events = execute_flow(http, admin_token, fixture, report["checks"])
            report["task_id"] = task_id
        require(server.process.poll() is not None, "owned server did not stop")
        (directory / "demo_events.jsonl").write_text(
            "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8",
        )
        report["server_stopped"] = True
        report["target_sha256"] = digest((fixture / "workspace/src/main.rs").read_bytes())
        report["status"] = "flow_passed"
        code = 0
    except (Exception, KeyboardInterrupt) as error:
        report["status"] = "failed"
        # Never print arbitrary server bodies, process environments, or tokens.
        report["failure"] = str(error).replace(admin_token, "[REDACTED]")
        report["server_stopped"] = server.process is None or server.process.poll() is not None
        print(f"MINIMAL_DEMO_FAIL: {report['failure']}", file=sys.stderr)
        code = 1
    finally:
        admin_token = ""
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json(report_path, report)
    return code, directory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-bin", type=Path, default=default_binary())
    parser.add_argument("--results-dir", type=Path, default=REPO / "results")
    args = parser.parse_args()
    try:
        code, directory = run(args.server_bin, args.results_dir.resolve())
    except (Exception, KeyboardInterrupt) as error:
        print(f"MINIMAL_DEMO_FAIL: {type(error).__name__}; cannot prepare demo run", file=sys.stderr)
        return 1
    print(f"Results: {directory}")
    if code == 0:
        print("MINIMAL_FLOW_PASS")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
