"""Recompute sample CSVs, paired conditions and summaries from saved responses."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import csv
import json
from pathlib import Path
import re

from bench.evidence import sample_from_request, validate_run, workspace_state
from bench.stats import Config, check, summarize
from demo.e2e_support import sanitize


def jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def normalized(value):
    if isinstance(value, dict):
        return {key: normalized(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalized(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"(?:task|grant)_[0-9a-f]{32}", "[ID]", value)
    return value


def input_signature(request):
    return normalized({key: request.get(key) for key in ("phase", "scenario", "sequence", "method", "path", "request_body", "request_redactions")})


def compare_csv(path, predicted):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        actual = list(csv.DictReader(stream))
    check(actual == [{key: "" if value is None else str(value) for key, value in row.items()} for row in predicted], "sample CSV differs from raw timed responses")


def verify_run(directory, *, evidence_only=False):
    directory = Path(directory).resolve()
    public = directory / "public" if (directory / "public").is_dir() else directory
    summary = json.loads((public / "bench_summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((public / "run_manifest.json").read_text(encoding="utf-8"))
    records = json.loads((public / "runs.json").read_text(encoding="utf-8"))
    config = Config(**summary["config"])
    check(manifest["config"] == asdict(config), "manifest and summary configurations differ")
    check(len(records) == config.rounds * 2, "experiment run count is incomplete")
    expected_order = [(r, mode) for r in range(1, config.rounds + 1) for mode in (("enforced", "baseline") if r % 2 else ("baseline", "enforced"))]
    check([(r["round"], r["mode"]) for r in records] == expected_order, "mode order is not alternating by round")
    check([r["order_index"] for r in records] == list(range(1, len(records) + 1)), "run sequence is duplicated or reordered")
    check(len({r["instance_id"] for r in records}) == len(records), "experiments reused a service instance")
    check(all(r["binary_sha256"] == manifest["server_binary_sha256"] for r in records), "paired runs did not use one binary")
    check(len({r["policy_sha256"] for r in records}) == 1 and all(r["initial_files"] == records[0]["initial_files"] for r in records), "paired fixtures or policy differ")
    samples, warmups, signatures = [], [], {}
    for record in records:
        key = record["key"]
        check(re.fullmatch(r"r[0-9]+-(?:enforced|baseline)", key) is not None, "invalid run key")
        requests = jsonl(public / "responses" / (key + ".jsonl"))
        events = jsonl(public / "events" / (key + ".jsonl"))
        validate_run(record, requests, events, config)
        signatures[(record["round"], record["mode"])] = [input_signature(request) for request in requests]
        for request in requests:
            if request["phase"] in {"measured", "warmup"}:
                check(request["mode"] == record["mode"] and request["round"] == record["round"], "mixed mode/round in sample")
                check(request["resource_bytes"] == (record["initial_files"]["workspace/unlisted.txt"]["bytes"] if request["scenario"] == "deny" else config.file_bytes), "incorrect resource byte count")
                row = sample_from_request(request, config)
                (samples if request["phase"] == "measured" else warmups).append(row)
        if not evidence_only:
            fixture = directory / "fixtures" / key
            check(workspace_state(fixture) == record["final_files"], "retained workspace changed after measurement")
            raw = (fixture / "audit.jsonl").read_bytes()
            check(raw.endswith(b"\n"), "retained audit has an incomplete line")
            disk = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
            check(sanitize(disk, [], fixture) == events, "retained audit differs from exported events")
    for round_id in range(1, config.rounds + 1):
        check(signatures[(round_id, "enforced")] == signatures[(round_id, "baseline")], "paired inputs or management preparation differ")
    check(len({r["sample_id"] for r in samples + warmups}) == len(samples) + len(warmups), "duplicate sample IDs")
    compare_csv(public / "bench_samples.csv", samples)
    compare_csv(public / "warmup_samples.csv", warmups)
    expected = summarize(samples, config)
    expected.update(run_id=manifest["run_id"], config=asdict(config), warmup_count=len(warmups))
    check(expected == summary, "summary differs from recomputed raw-sample statistics")
    return {"formal": config.formal, "measured": len(samples), "warmup": len(warmups), "runs": len(records), "files_checked": not evidence_only}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--evidence-only", action="store_true")
    args = parser.parse_args()
    try:
        result = verify_run(args.directory, evidence_only=args.evidence_only)
    except Exception as error:
        print("BENCH_VERIFY_FAIL: " + type(error).__name__)
        return 1
    print("BENCH_EVIDENCE_PASS" if args.evidence_only else "BENCH_VERIFY_PASS")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
