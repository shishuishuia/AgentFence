"""Measure Rust service RSS while running the entire strict 34-case HTTP suite."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bench.resources import MemoryRegistry, RSS_FIELDS
from bench.stats import BenchError, check
from demo.e2e_cases import run as run_blackbox
from demo.e2e_support import digest
from demo.manifest import create_manifest
from demo.runtime import REPO, default_binary
from demo.workflow import write_json


def run(binary, results, *, interval=0.005):
    check(platform.system() == "Linux" and Path("/proc/self/status").is_file(), "formal RSS acceptance requires Linux/WSL procfs")
    check(binary.is_file(), "build release agentfence-server first")
    results.mkdir(parents=True, exist_ok=True)
    directory = results / ("local-rss-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    directory.mkdir()
    registry = MemoryRegistry(interval)
    code, suite = run_blackbox(binary, directory, monitor_factory=registry)
    check(code == 0, "RSS workload did not pass all 34 cases without skips")
    public = directory / "public"
    public.mkdir()
    shutil.copytree(suite / "public",public / "blackbox")
    records = [monitor.record() for monitor in registry.monitors]
    rows = [row for monitor in registry.monitors for row in monitor.rows]
    with (public / "rss_samples.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=RSS_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    workload = json.loads((suite / "public/security_report.json").read_text(encoding="utf-8"))
    summary = {"status":"passed", "workload":"full A01..A10 D01..D24 real HTTP suite", "case_counts":workload["counts"],
               "suite_directory":suite.relative_to(directory).as_posix(), "sample_count":len(rows),
               "monitor_count":len(records), "poll_interval_seconds":interval,
               "peak_rss_kib":max(record["peak_rss_kib"] for record in records), "processes":records,
               "source":"/proc/<owned Rust pid>/status: VmRSS and kernel lifetime VmHWM, kB interpreted as KiB",
               "scope":"Rust service only, each case from spawn through final HTTP/audit collection; D23 includes both instances",
               "aggregation":"maximum individual service VmHWM, not a sum and not Python/Node/system RSS",
               "capture_boundary":"last sample before terminating the already-completed owned server",
               "limitations":"procfs RSS/HWM values are approximate and may fluctuate; report the maximum observed value, without claiming every transient peak was captured"}
    write_json(public / "rss_summary.json", summary)
    info = create_manifest(binary, suite / "fixtures/A01", directory.name)
    for path in sorted((REPO / "bench").rglob("*.py")):
        info["source_files"][path.relative_to(REPO).as_posix()] = digest(path.read_bytes())
    info["source_tree_sha256"] = digest(json.dumps(info["source_files"], sort_keys=True).encode())
    info.update(suite="Rust RSS across the complete strict HTTP black-box suite", logical_cpus=os.cpu_count(),
                observation={"poll_interval_seconds":interval,"source":summary["source"],"scope":summary["scope"]})
    info["cpu_model"] = next((line.split(":",1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines() if line.startswith("model name")), "unavailable")
    write_json(public / "run_manifest.json", info)
    from bench.verify_resources import verify_run
    try:
        verify_run(directory)
    except Exception as error:
        summary.update(status="failed",failure=str(error) if isinstance(error,BenchError) else type(error).__name__)
        write_json(public / "rss_summary.json",summary)
        raise
    return directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-bin", type=Path, default=default_binary())
    parser.add_argument("--results-dir", type=Path, default=REPO / "results")
    parser.add_argument("--interval", type=float, default=0.005)
    args = parser.parse_args()
    try:
        directory = run(args.server_bin.resolve(), args.results_dir.resolve(), interval=args.interval)
    except Exception as error:
        print("RSS_FAIL: " + (str(error) if isinstance(error,BenchError) else type(error).__name__), file=sys.stderr)
        return 1
    print("Results: " + str(directory))
    print("RSS_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
