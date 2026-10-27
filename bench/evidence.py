"""Shared response checks and metadata for reproducible benchmark evidence."""

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import time

from bench.stats import check
from demo.e2e_support import digest, file_state
from demo.manifest import create_manifest
from demo.runtime import REPO

SCENARIOS = ("read", "write", "deny")


def contents(size):
    return "A" * (size - 1) + "\n", "B" * (size - 1) + "\n"


def workspace_state(directory):
    return {key: value for key, value in file_state(directory).items() if key.startswith("workspace/")}


def sample_from_request(request, config):
    body = request["body"]
    scenario, mode = request["scenario"], request["mode"]
    check(request["phase"] in {"warmup", "measured"} and scenario in SCENARIOS, "invalid timed request phase/scenario")
    check(type(request["elapsed_ns"]) is int and request["elapsed_ns"] > 0, "invalid client elapsed time")
    check(isinstance(body.get("request_id"), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", body["request_id"]), "missing benchmark request ID")
    for key in ("duration_us", "decision_us"):
        check(type(body.get(key)) is int and body[key] >= 0, "invalid server duration field")
    expected_status = 403 if scenario == "deny" else 200
    check(request["status"] == expected_status and body.get("reason_code") == ("PATH_DENIED" if scenario == "deny" else "OK"), "unexpected benchmark HTTP result")
    check(body.get("allowed") is (scenario != "deny") and body.get("executed") is (scenario != "deny"), "unexpected benchmark execution flags")
    submitted = request["request_body"]
    check(submitted["tool"] == ("write_file" if scenario == "write" else "read_file"), "incorrect scenario tool")
    check(submitted["path"] == ("unlisted.txt" if scenario == "deny" else "src/main.rs"), "incorrect scenario resource")
    remaining = None
    if scenario == "deny":
        check(body.get("data") is None, "denied request returned file content")
    else:
        data = body.get("data")
        check(isinstance(data, dict), "successful benchmark response has no data")
        if scenario == "read":
            proof = request["redactions"].get("/data/content", {})
            check(data.get("bytes_read") == config.file_bytes and proof.get("bytes") == config.file_bytes and proof.get("sha256") == request["expected_content_sha256"], "read content/size mismatch")
        else:
            proof = request["request_redactions"].get("/content", {})
            check(proof.get("bytes") == config.file_bytes and data.get("bytes_written") == config.file_bytes, "write size mismatch")
            if mode == "enforced":
                check(data.get("grant_id") == submitted["grant_id"] and data.get("remaining_uses") == request["expected_remaining"], "dynamic write did not consume the expected use")
                remaining = data["remaining_uses"]
            else:
                check(data.get("grant_id") is None and data.get("remaining_uses") is None, "baseline response should not claim lease consumption")
    return {"sample_id": request["sample_id"], "pair_id": f"{request['round']}:{scenario}:{request['sequence']}",
            "round": request["round"], "mode": mode, "scenario": scenario, "phase": request["phase"],
            "sequence": request["sequence"], "batch_id": request.get("batch_id", ""), "http_status": request["status"],
            "reason_code": body["reason_code"], "request_id": body["request_id"], "elapsed_ns": request["elapsed_ns"],
            "server_duration_us": body["duration_us"], "decision_us": body["decision_us"], "target_file_bytes": config.file_bytes,
            "resource_bytes": request["resource_bytes"],
            "remaining_uses": remaining, "valid": True}


def validate_run(record, requests, events, config):
    from demo.e2e_support import validate_audit, safe_evidence
    check(record.get("server_stopped") is True and record.get("api_matches_disk") is True, "experiment cleanup/audit verification missing")
    check(record["phase"] == "edit" and record["binary_sha256"], "experiment did not use the declared edit/release preparation")
    check(record["experiment"]["mode"] == record["mode"] and record["experiment"]["synthetic_fixture"] is True, "incorrect experiment fixture mode")
    check(record["experiment"]["authorization_mode"] == ("phase_path_lease" if record["mode"] == "enforced" else "static_path_acl"), "incorrect experiment authorization strategy")
    check(record["experiment"]["file_operations"] == "LocalFiles" and record["experiment"]["http"] == "agentfence_server::app" and record["experiment"]["audit"] == "FileSink::append + sync_data", "experiment did not declare shared execution components")
    predicted = dict(record["initial_files"])
    predicted["workspace/src/main.rs"] = {"kind": "file", "sha256": record["final_content_sha256"], "bytes": config.file_bytes}
    check(predicted == record["final_files"], "unexpected final file changes")
    timed = [r for r in requests if r["phase"] in {"warmup", "measured"}]
    for phase, count in (("warmup", config.warmup), ("measured", config.iterations)):
        for scenario in SCENARIOS:
            group = [r for r in timed if r["phase"] == phase and r["scenario"] == scenario]
            check(len(group) == count and [r["sequence"] for r in group] == list(range(1, count + 1)), "warmup/measured group is incomplete")
            for request in group:
                sample_from_request(request, config)
    check(record["scenario_order"] == list(SCENARIOS[(record["round"] - 1) % 3:] + SCENARIOS[:(record["round"] - 1) % 3]), "scenario order was not counterbalanced as declared")
    for batch in record["batches"]:
        uses = batch["uses"]
        check(1 <= uses <= 10 and batch["grant"]["remaining_uses"] == uses, "invalid write batch size")
        check(batch["before"]["status"] == "active" and batch["before"]["remaining_uses"] == uses, "write batch did not start with a valid grant")
        check(batch["before"]["grant_id"] == batch["grant"]["grant_id"] == batch["after"]["grant_id"], "batch snapshots refer to different grants")
        after = batch["after"]
        check(after["remaining_uses"] == (0 if record["mode"] == "enforced" else uses), "batch consumed an incorrect number of lease uses")
        check(after["status"] == ("used_up" if record["mode"] == "enforced" else "active"), "incorrect batch lease status before explicit revocation")
        check(batch["revoked"]["revoked"] is True, "batch was not explicitly revoked")
        calls = [r for r in requests if r.get("batch_id") == batch["batch_id"] and r["path"] == "/v1/tool/execute"]
        check(len(calls) == uses, "batch does not match its tool requests")
        for index, request in enumerate(calls, 1):
            check(request["request_body"]["grant_id"] == batch["grant"]["grant_id"], "batch request used another grant")
            if request["phase"] in {"warmup", "measured"}:
                check(request["expected_remaining"] == uses - index, "incorrect per-write consumption expectation")
    validate_audit(requests, events)
    safe_evidence([record, requests, events], [])


def manifest(binary, fixture, run_id, config):
    value = create_manifest(binary, fixture, run_id)
    for path in sorted((REPO / "bench").rglob("*")):
        if path.is_file() and path.suffix in {".py", ".sh"}:
            value["source_files"][path.relative_to(REPO).as_posix()] = digest(path.read_bytes())
    value["source_tree_sha256"] = hashlib.sha256(json.dumps(value["source_files"], sort_keys=True).encode()).hexdigest()
    model = platform.processor()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.is_file():
        model = next((line.split(":", 1)[1].strip() for line in cpuinfo.read_text().splitlines() if line.startswith("model name")), model)
    value.update(suite="paired Rust HTTP enforced/baseline benchmark", build_profile="release example; bench-harness feature",
                 build_command="cargo build --release --locked -p agentfence-server --features bench-harness --example http_experiment",
                 config=asdict(config), hardware={"processor": model, "architecture": platform.machine(), "logical_cpus": os.cpu_count()},
                 measurement={"clock": "time.perf_counter_ns", "resolution_seconds": time.get_clock_info("perf_counter").resolution,
                              "unit": "integer nanoseconds", "concurrency": 1, "connection": "new TCP connection per request; urllib Connection: close; no pooling or retry",
                              "timed_scope": "HTTP client serialization/request, complete response body read and JSON decoding",
                              "excluded": ["process/fixture startup", "task/phase/grant/revoke preparation", "warmup", "assertions", "file/grant/audit checks", "export/statistics"],
                              "grant_strategy": "both modes issue <=10 uses, query before/after, then explicitly revoke each batch; baseline does not consume",
                              "order": "mode order alternates by round; scenario order rotates by round; processes run sequentially",
                              "host_limit": "development/CI host, not guaranteed dedicated hardware"})
    return value
