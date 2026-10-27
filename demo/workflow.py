"""Shared, asserted HTTP workflow; optional safe checkpoints for live display."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Callable

from integrations import AdminClient, AgentClient, ApiResponse, BrokerHttp
from .fixture import AFTER, BEFORE, PRIVATE
from .verify import snapshot


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require(condition: bool, message: str):
    # Do not use `assert`: demo validation must also run under `python -O`.
    if not condition:
        raise RuntimeError(message)


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def execute_flow(http: BrokerHttp, admin_token: str, fixture: Path, checks: list[dict], credentials: list[str], checkpoint: Callable[[str, str], None] | None = None) -> tuple[str, list[dict], dict, int]:
    admin = AdminClient(http, admin_token)

    def record(name: str, response: ApiResponse, status: int, reason: str | None = None) -> dict:
        body = response.payload
        request_id = body.get("request_id")
        request_id = request_id if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_id) else "INVALID_REQUEST_ID"
        actual_reason = body.get("reason_code")
        actual_reason = actual_reason if isinstance(actual_reason, str) and re.fullmatch(r"[A-Z_]+", actual_reason) else None
        entry = {
            "case": name, "http_status": response.status, "expected_status": status,
            "reason_code": actual_reason, "expected_reason": reason,
            "request_id": request_id, "pass": False,
            "allowed": body.get("allowed"), "executed": body.get("executed"),
        }
        data = body.get("data")
        if isinstance(data, dict):
            entry["data_summary"] = {key: data[key] for key in ("bytes_read", "bytes_written", "grant_id", "remaining_uses") if key in data}
            if isinstance(data.get("content"), str):
                entry["data_summary"]["content_sha256"] = digest(data["content"].encode("utf-8"))
        checks.append(entry)
        require(response.status == status, f"{name}: expected HTTP {status}, received {response.status}; request_id={request_id}")
        require(request_id != "INVALID_REQUEST_ID", f"{name}: missing or malformed request_id")
        if reason is not None:
            require(actual_reason == reason, f"{name}: expected {reason}, received {actual_reason}; request_id={request_id}")
            require(body.get("allowed") is (status == 200), f"{name}: incorrect allowed flag")
            require(body.get("executed") is (status == 200), f"{name}: incorrect executed flag")
        entry["pass"] = True
        return body

    created = record("create_task", admin.create_task("demo"), 201)
    require(created.get("phase") == "inspect", "create_task: initial phase is not inspect")
    task_id = created["task_id"]
    credentials.append(created["agent_token"])
    agent = AgentClient(http, task_id, created["agent_token"])
    target = fixture / "workspace/src/main.rs"

    def pause(name):
        if checkpoint is not None:
            checkpoint(name, task_id)

    pause("task_ready")

    def denied(name: str, operation, reason: str) -> dict:
        before = snapshot(fixture, admin.task(task_id).require(200))
        body = record(name, operation(), 403, reason)
        after = snapshot(fixture, admin.task(task_id).require(200))
        checks[-1]["denial_evidence"] = {"before": before, "after": after}
        if before != after:
            checks[-1]["pass"] = False
            raise RuntimeError(f"{name}: rejected call changed files, task or lease; request_id={body.get('request_id')}")
        require(body.get("data") is None, f"{name}: denied call leaked data")
        return body

    def read(name: str, path: str, status: int, reason: str, content: str | None = None):
        body = (record(name, agent.read_file(path), status, reason) if status == 200
                else denied(name, lambda: agent.read_file(path), reason))
        if content is not None:
            require(body.get("data", {}).get("content") == content, f"{name}: content mismatch")
            require(body["data"].get("bytes_read") == len(content.encode("utf-8")), f"{name}: UTF-8 byte count mismatch")
        else:
            require(body.get("data") is None, f"{name}: denied read leaked data")

    read("inspect_read", "src/main.rs", 200, "OK", BEFORE)
    denied("inspect_write_denied", lambda: agent.write_file("src/main.rs", AFTER), "INVALID_STAGE")
    require(target.read_bytes() == BEFORE.encode(), "inspect_write_denied: target changed")

    pause("inspect")
    record("advance_edit", admin.advance(task_id, "edit"), 200)
    grant = record("issue_grant", admin.issue_grant(task_id, "src/main.rs"), 201)
    require(grant.get("remaining_uses") == 1 and grant.get("status") == "active", "issue_grant: invalid snapshot")
    grant_id = grant["grant_id"]
    pause("grant_active")
    before_write = snapshot(fixture, admin.task(task_id).require(200))
    written = record("edit_write", agent.write_file("src/main.rs", AFTER, grant_id=grant_id), 200, "OK")
    after_write = snapshot(fixture, admin.task(task_id).require(200))
    checks[-1]["write_evidence"] = {"before": before_write, "after": after_write}
    require(written.get("data", {}).get("remaining_uses") == 0, "edit_write: lease was not consumed")
    require(written["data"].get("bytes_written") == len(AFTER.encode("utf-8")), "edit_write: UTF-8 byte count mismatch")
    require(target.read_bytes() == AFTER.encode("utf-8"), "edit_write: actual target differs")

    denied("reuse_denied", lambda: agent.write_file("src/main.rs", "must not replace\n", grant_id=grant_id), "LEASE_USED_UP")
    require(target.read_bytes() == AFTER.encode("utf-8"), "reuse_denied: target changed")
    read("outside_read_denied", "../simulated-private.txt", 403, "PATH_DENIED")
    require((fixture / "simulated-private.txt").read_bytes() == PRIVATE.encode(), "outside private fixture changed")

    pause("grant_used_up")
    record("advance_verify", admin.advance(task_id, "verify"), 200)
    read("verify_read", "src/main.rs", 200, "OK", AFTER)
    denied("verify_write_denied", lambda: agent.write_file("src/main.rs", "must not replace\n", grant_id=grant_id), "INVALID_STAGE")
    pause("verify")
    record("advance_done", admin.advance(task_id, "done"), 200)
    read("done_read_denied", "src/main.rs", 403, "INVALID_STAGE")
    require(target.read_bytes() == AFTER.encode("utf-8"), "final target changed unexpectedly")
    detail = admin.task(task_id).require(200)
    require(detail.get("phase") == "done", "task did not reach done")
    require(len(detail.get("grants", [])) == 1 and detail["grants"][0].get("remaining_uses") == 0, "final lease mismatch")

    pause("done")
    events = []
    cursor = "0"
    pages = 0
    for _ in range(100):
        page = admin.events(task_id=task_id, after=cursor, limit=2).require(200)
        pages += 1
        require(page.get("instance_id") == created.get("instance_id"), "server instance changed during pagination")
        events.extend(page["events"])
        if not page["has_more"]:
            break
        require(page["next_cursor"] != cursor, "event pagination did not advance")
        cursor = page["next_cursor"]
    else:
        raise RuntimeError("event pagination exceeded safety limit")
    return task_id, events, detail, pages


