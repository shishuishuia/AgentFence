"""Independently recheck v2 replay and paired MCP timing evidence.

The files can be copied with their generated fixtures. No credentials, server,
model API, network connection or original host path are needed to verify them.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess


class EvidenceError(ValueError):
    """A safe validation error without dumping evidence or credentials."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def checked_file(root: Path, relative: str) -> Path:
    """Reject evidence paths that would escape the explicitly supplied root."""
    require(isinstance(relative, str), "invalid evidence path")
    name = Path(relative)
    require(not name.is_absolute() and ".." not in name.parts, "invalid evidence path")
    target = (root / name).resolve(strict=True)
    require(target.is_relative_to(root) and target.is_file(), "evidence file escapes root or is absent")
    require(target.stat().st_size <= 64 * 1024 * 1024, "evidence file exceeds verifier limit")
    return target


def read_json(root: Path, name: str):
    return json.loads(checked_file(root, name).read_text(encoding="utf-8"))


def read_lines(root: Path, name: str):
    text = checked_file(root, name).read_text(encoding="utf-8")
    require(text.endswith("\n"), "JSONL has an incomplete trailing record")
    return [json.loads(line) for line in text.splitlines()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_audit(events, disk, task_id):
    ids = [event["event_id"] for event in disk]
    require(ids == list(range(1, len(ids) + 1)), "disk event ids are not complete and ordered")
    require([event for event in disk if event["task_id"] == task_id] == events, "API and disk audit differ")
    indexed = {}
    for event in events:
        indexed.setdefault(event["request_id"], []).append(event)
    return indexed


def verify_benchmark_data(root: Path, meta, samples, events, disk):
    require(meta["status"] == "passed" and meta["dirty_tree"] is False, "benchmark was not captured on a clean source tree")
    require(re.fullmatch(r"[0-9a-f]{40}", meta["source_commit"]) is not None, "invalid source commit")
    require(meta["concurrency"] == 1 and meta["rounds"] == 3 and meta["warmups_per_mode"] == 60 and meta["samples_per_mode_per_round"] == 100, "unexpected benchmark design")
    fixture = checked_file(root, "project/benchmark.rs")
    require(fixture.stat().st_size == meta["fixture_bytes"] == 4096, "incorrect fixture size")
    require(sha256(fixture) == meta["fixture_sha256"], "fixture hash differs")
    require(len(samples) == 720 and len({sample["request_id"] for sample in samples}) == 720, "missing or duplicate samples")
    indexed = validate_audit(events, disk, meta["task_id"])
    pair_keys = set()
    for index in range(0, len(samples), 2):
        a, b = samples[index:index + 2]
        expected = ("direct_http", "mcp_stdio") if index // 2 % 2 == 0 else ("mcp_stdio", "direct_http")
        require((a["mode"], b["mode"]) == expected and (a["order"], b["order"]) == (1, 2), "paired call order differs")
        require((a["kind"], a["round"], a["index"]) == (b["kind"], b["round"], b["index"]), "paired samples do not match")
        pair_keys.add((a["kind"], a["round"], a["index"]))
    expected_keys = {("warmup", 0, i) for i in range(1, 61)}
    expected_keys.update(("measured", r, i) for r in (1, 2, 3) for i in range(1, 101))
    require(pair_keys == expected_keys, "sample indices are incomplete or duplicated")
    for sample in samples:
        require(type(sample["elapsed_ns"]) is int and sample["elapsed_ns"] > 0, "invalid latency")
        require(sample["sha256"] == meta["fixture_sha256"] and sample["bytes"] == 4096, "sample file proof differs")
        pair = indexed.get(sample["request_id"], [])
        require(len(pair) == 2 and pair[0]["event_type"] == "intent" and pair[0]["reason_code"] == "PENDING", "sample intent missing")
        result = pair[1]
        require(result["event_type"] == "result" and result["reason_code"] == "OK" and result["allowed"] is True and result["executed"] is True, "sample result missing")
        require(result["tool"] == "read_file" and result["path"] == "benchmark.rs" and result["snapshot_id"] == meta["snapshot_id"], "sample audit target differs")
    for mode in ("direct_http", "mcp_stdio"):
        require(sum(s["kind"] == "warmup" and s["mode"] == mode and s["round"] == 0 for s in samples) == 60, "incorrect warmup count")
        for round_number in (None, 1, 2, 3):
            selected = sorted(s["elapsed_ns"] for s in samples if s["kind"] == "measured" and s["mode"] == mode and (round_number is None or s["round"] == round_number))
            expected = meta["aggregate"][mode] if round_number is None else meta["per_round"][round_number - 1][mode]
            require(len(selected) == (300 if round_number is None else 100) and expected["count"] == len(selected), "incorrect formal sample count")
            for percentage in (50, 95, 99):
                require(expected[f"p{percentage}_ns"] == selected[math.ceil(percentage * len(selected) / 100) - 1], "percentile differs from raw samples")
    return {"kind": "benchmark", "source_commit": meta["source_commit"], "measured": 600, "warmups": 120, "task_events": len(events)}


def verify_replay_data(root: Path, meta, disk, exported):
    require(meta["passed"] is True and meta["dirty_tree"] is False and meta["model_used"] is False, "replay status or provenance is invalid")
    require(re.fullmatch(r"[0-9a-f]{40}", meta["source_commit"]) is not None, "invalid source commit")
    require(len(meta["runs"]) == 2 and len({run["project"] for run in meta["runs"]}) == 2, "two distinct fixture projects are required")
    for run in meta["runs"]:
        project = run["project"]
        require(project in ("project-a", "project-b"), "unexpected fixture project")
        indexed = validate_audit(exported[project], disk, run["task"]["task_id"])
        require(len(run["files"]) >= 3 and run["source_unchanged"] is True, "fixture file evidence is incomplete")
        for file in run["files"]:
            fixture = checked_file(root, f"{project}/{file['path']}")
            require(sha256(fixture) == file["sha256"] and fixture.stat().st_size == file["bytes"], "replay fixture hash differs")
        require(sum(call["allowed"] is True for call in run["calls"]) >= 3, "fewer than three successful reads")
        require(sum(call["allowed"] is False for call in run["calls"]) >= 5, "required denial evidence is missing")
        for call in run["calls"]:
            result = [event for event in indexed.get(call["request_id"], []) if event["event_type"] == "result"]
            require(len(result) == 1 and result[0]["allowed"] is call["allowed"], "call and audit result differ")
            if call["allowed"]:
                require(result[0]["reason_code"] == "OK" and result[0]["executed"] is True, "read was not executed")
                require(any(file["path"] == call["path"] and file["sha256"] == call["sha256"] for file in run["files"]), "read does not match manifest")
            else:
                require(result[0]["reason_code"] == call["reason_code"] and result[0]["executed"] is False, "denial differs from audit")
        readback = run["verified_read"]
        require(any(event["event_type"] == "result" and event["executed"] is True for event in indexed.get(readback["request_id"], [])), "verify readback evidence is missing")
    return {"kind": "replay", "source_commit": meta["source_commit"], "projects": 2, "disk_events": len(disk)}


def require_source_match(repo: Path, commit: str):
    """Compare the measured Rust project tree with the current checkout."""
    scopes = ["Cargo.toml", "Cargo.lock", "crates"]
    revision = subprocess.run(["git", "cat-file", "-e", commit + "^{commit}"], cwd=repo, capture_output=True)
    require(revision.returncode == 0, "source commit is unavailable in this checkout")
    committed = subprocess.run(["git", "diff", "--quiet", commit, "HEAD", "--", *scopes], cwd=repo)
    working = subprocess.run(["git", "status", "--porcelain", "--", *scopes], cwd=repo, capture_output=True, text=True)
    require(committed.returncode == 0 and working.returncode == 0 and not working.stdout, "current Rust source differs from measured commit")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--source-match", type=Path, help="compare Rust code with this Git checkout")
    parser.add_argument("--self-test", action="store_true", help="confirm that corrupted in-memory evidence is rejected")
    args = parser.parse_args()
    root = args.directory.resolve(strict=True)
    if (root / "summary.json").is_file():
        meta, samples, events, disk = read_json(root, "summary.json"), read_lines(root, "samples.jsonl"), read_json(root, "events.json"), read_lines(root, "audit.jsonl")
        result = verify_benchmark_data(root, meta, samples, events, disk)
        if args.self_test:
            altered = copy.deepcopy(meta)
            altered["aggregate"]["direct_http"]["p50_ns"] += 1
            try:
                verify_benchmark_data(root, altered, samples, events, disk)
            except EvidenceError:
                pass
            else:
                raise EvidenceError("self-test failed to detect a modified percentile")
            altered_samples = copy.deepcopy(samples)
            altered_samples[1]["request_id"] = altered_samples[0]["request_id"]
            try:
                verify_benchmark_data(root, meta, altered_samples, events, disk)
            except EvidenceError:
                pass
            else:
                raise EvidenceError("self-test failed to detect a duplicate request")
    else:
        meta, disk = read_json(root, "evidence.json"), read_lines(root, "audit.jsonl")
        exported = {name: read_json(root, f"events-{name}.json")["events"] for name in ("project-a", "project-b")}
        result = verify_replay_data(root, meta, disk, exported)
        if args.self_test:
            altered = copy.deepcopy(meta)
            altered["runs"][0]["files"][0]["sha256"] = "0" * 64
            try:
                verify_replay_data(root, altered, disk, exported)
            except EvidenceError:
                pass
            else:
                raise EvidenceError("self-test failed to detect a modified fixture hash")
    if args.source_match:
        require_source_match(args.source_match.resolve(strict=True), result["source_commit"])
    print(json.dumps({**result, "verified": True, "self_test": args.self_test}, ensure_ascii=False))


if __name__ == "__main__":
    main()
