"""Run A01..A10 and D01..D24 over real HTTP in isolated Rust subprocesses."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sys
import time
from urllib.parse import urlencode
import uuid

# Support both python -m demo.e2e_cases and python demo/e2e_cases.py.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from demo.e2e_spec import CASES, rule
from demo.e2e_support import ALLOWED, CaseContext, CaseFailure, PlatformSkip, check, file_state, prepare, safe_evidence, csv_rows
from demo.e2e_verify import validate_case, validate_probe, verify_run
from demo.fixture import AFTER
from demo.manifest import create_manifest
from demo.runtime import REPO, default_binary
from demo.workflow import write_json


def probe(ctx, case_id, label, operation, task=None):
    before = ctx.state()
    index = len(ctx.requests)
    response = operation()
    check(len(ctx.requests) == index + 1, "a probe must capture exactly one HTTP request")
    ctx.requests[index]["label"] = "case:" + label
    item = {"label": label, "request_index": index, "task_id": task[0] if task else None, "before": before, "after": ctx.state()}
    ctx.probes.append(item)
    validate_probe(case_id, item, ctx.requests)
    return response.payload


def execute(ctx, case_id, metadata):
    def measured(operation, task=None, label="main"):
        return probe(ctx, case_id, label, operation, task)

    if case_id == "A01":
        measured(lambda: ctx.call("create", "POST", "/v1/admin/tasks", {"workspace_id": "demo"}))
        return
    if case_id == "A10":
        task = ctx.create()
        other = ctx.create()
        ctx.tool(task).require(200)
        ctx.tool(task).require(200)
        ctx.tool(other).require(200)
        metadata["task_id"] = task[0]
        cursor = None
        for page in range(1, 101):
            query = {"task_id": task[0], "limit": 2}
            if cursor is not None:
                query["after"] = cursor
            value = measured(lambda: ctx.call("page", "GET", "/v1/admin/events?" + urlencode(query)), task, f"page_{page}")
            if not value["has_more"]:
                return
            check(value["next_cursor"] != cursor, "A10 cursor did not advance")
            cursor = value["next_cursor"]
        raise CaseFailure("A10 page limit exceeded")
    if case_id in {"A02", "A03", "A04", "A09", "D05", "D07", "D08"}:
        task = ctx.create()
        if case_id == "A02":
            measured(lambda: ctx.tool(task), task)
        elif case_id == "A03":
            measured(lambda: ctx.call("phase", "POST", f"/v1/admin/tasks/{task[0]}/phase", {"phase": "edit"}), task)
        elif case_id == "A04":
            ctx.phase(task, "edit")
            measured(lambda: ctx.call("grant", "POST", f"/v1/admin/tasks/{task[0]}/grants", {"tool": "write_file", "path": "src/main.rs", "ttl_seconds": 60, "max_uses": 1}), task)
        elif case_id in {"A09", "D07"}:
            ctx.phase(task, "edit")
            ctx.phase(task, "verify")
            if case_id == "A09":
                measured(lambda: ctx.call("phase", "POST", f"/v1/admin/tasks/{task[0]}/phase", {"phase": "done"}), task)
            else:
                ctx.phase(task, "done")
                measured(lambda: ctx.tool(task), task)
        elif case_id == "D05":
            measured(lambda: ctx.tool(task, "write_file", content=AFTER, grant="grant_missing"), task)
        else:
            route = f"/v1/admin/tasks/{task[0]}/phase"
            measured(lambda: ctx.call("skip", "POST", route, {"phase": "verify"}), task, "skip")
            ctx.phase(task, "edit")
            measured(lambda: ctx.call("back", "POST", route, {"phase": "inspect"}), task, "back")
            measured(lambda: ctx.call("repeat", "POST", route, {"phase": "edit"}), task, "repeat")
        return

    task, grant = ctx.edit(ttl=1 if case_id == "D10" else 300)
    gid = grant["grant_id"]
    if case_id == "A05":
        measured(lambda: ctx.tool(task, "write_file", content=AFTER, grant=gid), task)
    elif case_id in {"A06", "A08", "D12"}:
        ctx.tool(task, "write_file", content=AFTER, grant=gid, label="setup:write").require(200)
        if case_id == "A08":
            ctx.phase(task, "verify")
        if case_id == "D12":
            measured(lambda: ctx.tool(task, "write_file", content="must not replace\n", grant=gid), task)
        else:
            measured(lambda: ctx.tool(task), task)
    elif case_id in {"A07", "D06"}:
        if case_id == "A07":
            measured(lambda: ctx.call("phase", "POST", f"/v1/admin/tasks/{task[0]}/phase", {"phase": "verify"}), task)
        else:
            ctx.phase(task, "verify")
            measured(lambda: ctx.tool(task, "write_file", content=AFTER, grant=gid), task)
    elif case_id == "D01":
        body = {"task_id": task[0], "tool": "write_file", "path": "src/main.rs", "content": AFTER, "grant_id": gid}
        measured(lambda: ctx.call("missing", "POST", "/v1/tool/execute", body, None), task, "missing")
        measured(lambda: ctx.call("invalid", "POST", "/v1/tool/execute", body, "invalid-test-credential"), task, "invalid")
    elif case_id == "D02":
        measured(lambda: ctx.call("agent_admin", "POST", "/v1/admin/tasks", {"workspace_id": "demo"}, task[1]), task)
    elif case_id == "D03":
        other = ctx.create()
        measured(lambda: ctx.call("mismatch", "POST", "/v1/tool/execute", {"task_id": other[0], "tool": "write_file", "path": "src/main.rs", "content": AFTER, "grant_id": gid}, task[1]), task)
    elif case_id == "D04":
        measured(lambda: ctx.tool(task, "unknown_tool"), task)
    elif case_id == "D09":
        measured(lambda: ctx.tool(task, "write_file", content=AFTER), task)
    elif case_id == "D10":
        start = time.monotonic()
        time.sleep(1.15)
        metadata["expiry_wait_seconds"] = time.monotonic() - start
        measured(lambda: ctx.tool(task, "write_file", content=AFTER, grant=gid), task)
    elif case_id == "D11":
        ctx.call("setup:revoke", "DELETE", f"/v1/admin/tasks/{task[0]}/grants/{gid}").require(200)
        measured(lambda: ctx.tool(task, "write_file", content=AFTER, grant=gid), task)
    elif case_id == "D13":
        measured(lambda: ctx.tool(task, "write_file", path="Cargo.toml", content=AFTER, grant=gid), task)
    elif case_id in {"D14", "D15", "D16", "D17", "D18", "D19", "D22"}:
        path = {"D14": "unlisted.txt", "D15": str(ctx.fixture / "simulated-private.txt"), "D16": "../simulated-private.txt", "D17": "link-file", "D18": "link-dir/file.rs", "D19": "src/main.rs.bak", "D22": "text.bin"}[case_id]
        measured(lambda: ctx.tool(task, path=path), task)
    elif case_id == "D20":
        measured(lambda: ctx.tool(task, path="src\\main.rs"), task, "backslash")
        measured(lambda: ctx.tool(task, path="src/main.rs\x00"), task, "nul")
    elif case_id == "D21":
        measured(lambda: ctx.tool(task, "write_file", content="x" * 65537, grant=gid), task, "file_limit")
        measured(lambda: ctx.tool(task, "write_file", content="x" * 524288, grant=gid), task, "body_limit")
    elif case_id == "D23":
        ctx.tool(task, label="setup:old_token_works").require(200)
        metadata["old_instance"] = ctx.instance
        ctx.restart()
        metadata["new_instance"] = ctx.instance
        measured(lambda: ctx.tool(task), task)
    elif case_id == "D24":
        measured(lambda: ctx.call("conflict", "POST", f"/v1/admin/tasks/{task[0]}/grants", {"tool": "write_file", "path": "src/main.rs", "ttl_seconds": 300, "max_uses": 1}), task)
    else:
        raise CaseFailure("unimplemented fixed case")


def run(binary: Path, results: Path, *, monitor_factory=None):
    check(binary.is_file(), "release server binary missing; build agentfence-server first")
    results.mkdir(parents=True, exist_ok=True)
    run_id = "local-e2e-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    directory = results / run_id
    public = directory / "public"
    for folder in (directory / "fixtures", public / "responses", public / "events"):
        folder.mkdir(parents=True)
    cases, all_requests = [], {}
    report = {"run_id": run_id, "requested_cases": list(CASES), "cases": cases, "started_at": datetime.now(timezone.utc).isoformat()}
    for case_id, name in CASES.items():
        entry = {"case_id": case_id, "name": name, "verdict": "failed", "metadata": {}, "probes": [], "server_stopped": False, "api_matches_disk": False}
        cases.append(entry)
        fixture = directory / "fixtures" / case_id
        ctx, events = None, []
        try:
            prepare(fixture, case_id)
            ctx = CaseContext(binary, fixture, secrets.token_hex(32), monitor_factory=monitor_factory)
            ctx.start()
            execute(ctx, case_id, entry["metadata"])
        except PlatformSkip:
            entry.update(verdict="skipped", failure="required symbolic-link capability unavailable; run on Linux")
        except KeyboardInterrupt:
            entry["failure"] = "interrupted"
        except Exception as error:
            entry["failure"] = str(error) if isinstance(error, CaseFailure) else type(error).__name__
        finally:
            if ctx:
                entry["probes"] = ctx.probes
                try:
                    events = ctx.finish()
                    entry.update(server_stopped=True, api_matches_disk=True)
                    if "failure" not in entry:
                        validate_case(entry, ctx.requests, events)
                        entry["verdict"] = "passed"
                except Exception as error:
                    entry["failure"] = str(error) if isinstance(error, CaseFailure) else type(error).__name__
                entry["server_stopped"] = all(server.process is None or server.process.poll() is not None for server in ctx.servers)
                all_requests[case_id] = ctx.requests
                safe_evidence([entry, ctx.requests, events], ctx.secrets)
                ctx.secrets.clear()
            entry["final_files"] = file_state(fixture) if fixture.exists() else {}
            for folder, values in (("responses", all_requests.get(case_id, [])), ("events", events)):
                (public / folder / f"{case_id}.jsonl").write_text("".join(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n" for value in values), encoding="utf-8")
            print(case_id + " " + entry["verdict"].upper(), flush=True)
        if entry.get("failure") == "interrupted":
            break
    counts = {status: sum(case["verdict"] == status for case in cases) for status in ("passed", "failed", "skipped")}
    report.update(counts=counts, status="passed" if counts["passed"] == 34 and len(cases) == 34 else "failed" if counts["failed"] else "partial", finished_at=datetime.now(timezone.utc).isoformat())
    write_json(public / "security_report.json", report)
    rows = csv_rows(cases, all_requests)
    with (public / "security_cases.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    manifest = create_manifest(binary, directory / "fixtures/A01", run_id)
    manifest.update(suite="independent real-HTTP A01..A10 D01..D24", config={"allowed_paths": ALLOWED, "max_content_bytes": 65536, "isolated_fixture_per_case": True, "ttl_expiry_wait_seconds": 1.15, "exact_ttl_boundary_claimed": False}, counts=counts)
    write_json(public / "run_manifest.json", manifest)
    if report["status"] == "passed":
        verify_run(directory)
        return 0, directory
    return 1 if counts["failed"] else 2, directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-bin", type=Path, default=default_binary())
    parser.add_argument("--results-dir", type=Path, default=REPO / "results")
    args = parser.parse_args()
    try:
        code, directory = run(args.server_bin.resolve(), args.results_dir.resolve())
    except Exception as error:
        print("BLACKBOX_FAIL: " + (str(error) if isinstance(error, CaseFailure) else type(error).__name__), file=sys.stderr)
        return 1
    print("Results: " + str(directory))
    print("BLACKBOX_PASS" if code == 0 else "BLACKBOX_PARTIAL" if code == 2 else "BLACKBOX_FAIL")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
