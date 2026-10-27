"""Recompute RSS peaks and prove complete workload/owned-instance coverage."""

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import re

from bench.stats import check
from demo.e2e_spec import CASES
from demo.e2e_verify import verify_run as verify_blackbox


def verify_run(directory, *, evidence_only=False):
    directory = Path(directory).resolve()
    public = directory / "public" if (directory / "public").is_dir() else directory
    summary = json.loads((public / "rss_summary.json").read_text(encoding="utf-8"))
    check(isinstance(summary.get("suite_directory"),str) and re.fullmatch(r"local-e2e-[0-9TZ]+-[a-f0-9]{8}",summary["suite_directory"]) is not None,"invalid RSS workload directory")
    # The portable archive includes this subdirectory's public evidence only.
    suite = public / "blackbox" if evidence_only else directory / summary["suite_directory"]
    result = verify_blackbox(suite, evidence_only=evidence_only)
    suite_public = suite / "public" if (suite / "public").is_dir() else suite
    info = json.loads((public / "run_manifest.json").read_text(encoding="utf-8"))
    workload_info = json.loads((suite_public / "run_manifest.json").read_text(encoding="utf-8"))
    check(info["server_binary_sha256"] == workload_info["server_binary_sha256"] and info["git_commit"] == workload_info["git_commit"],"RSS workload/binary provenance differs")
    check(result["passed"] == 34 and summary["case_counts"] == {"passed":34,"failed":0,"skipped":0}, "incomplete RSS workload")
    with (public / "rss_samples.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    check(len(rows) == summary["sample_count"], "RSS sample count differs")
    coverage = Counter(record["case_id"] for record in summary["processes"])
    check(dict(coverage) == {case:2 if case == "D23" else 1 for case in CASES}, "missing or duplicate RSS case/instance coverage")
    check(summary["monitor_count"] == 35 and len({record["monitor_id"] for record in summary["processes"]}) == 35, "incorrect Rust monitor count")
    total = 0
    for record in summary["processes"]:
        samples = [row for row in rows if row["monitor_id"] == record["monitor_id"]]
        check(len(samples) == record["sample_count"] and samples and record["server_stopped"] is True, "incomplete RSS monitor")
        stamps, highs = [], []
        for row in samples:
            timestamp, rss, hwm = (int(row[name]) for name in ("monotonic_ns","rss_kib","hwm_kib"))
            check(row["case_id"] == record["case_id"] and int(row["pid"]) == record["pid"], "RSS assigned to another process")
            check(record["started_ns"] <= timestamp <= record["finished_ns"] and 0 <= rss <= hwm, "invalid RSS units/time/counters")
            stamps.append(timestamp)
            highs.append(hwm)
        # Linux procfs counters are approximate snapshots and can fluctuate.
        # Preserve every raw value and take the maximum; never "repair" a dip.
        check(all(a < b for a,b in zip(stamps,stamps[1:])), "nonmonotonic RSS timestamps")
        check(max(highs) == record["peak_rss_kib"] and max(highs) > 0, "incorrect per-process RSS peak")
        total += len(samples)
    check(total == len(rows), "unassigned RSS samples")
    check(summary["status"] == "passed" and summary["peak_rss_kib"] == max(record["peak_rss_kib"] for record in summary["processes"]), "incorrect workload RSS peak")
    return {"peak_rss_kib":summary["peak_rss_kib"],"cases":34,"processes":35,"samples":len(rows),"fixture_files_checked":not evidence_only}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--evidence-only", action="store_true")
    args = parser.parse_args()
    try:
        result = verify_run(args.directory, evidence_only=args.evidence_only)
    except Exception as error:
        print("RSS_VERIFY_FAIL: " + type(error).__name__)
        return 1
    print("RSS_EVIDENCE_PASS" if args.evidence_only else "RSS_VERIFY_PASS")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
