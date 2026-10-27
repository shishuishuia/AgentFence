"""Observe TTL/phase/revocation/use-limited permission time with matched Rust treatments."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sys
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bench.evidence import manifest, workspace_state
from bench.run_bench import write_csv
from bench.runtime import Experiment, default_binary
from bench.stats import BenchError, check
from bench.window_session import WindowSession
from bench.window_stats import CAUSES, WindowConfig, interval_rows, summarize
from demo.e2e_support import CaseFailure, digest, sanitize, safe_evidence
from demo.runtime import REPO
from demo.workflow import write_json


def run(binary, results, config):
    check(binary.is_file(), "build the shared release http_experiment first")
    results.mkdir(parents=True, exist_ok=True)
    run_id = "local-permission-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    directory = results / run_id
    public = directory / "public"
    for path in (directory / "fixtures",public / "responses",public / "events"):
        path.mkdir(parents=True)
    records, rows, info = [], [], None
    binary_hash = digest(binary.read_bytes())
    try:
        for round_id in range(1,config.rounds+1):
            shift = (round_id-1) % len(CAUSES)
            for scenario in CAUSES[shift:]+CAUSES[:shift]:
                for mode in (("enforced","baseline") if round_id % 2 else ("baseline","enforced")):
                    check(digest(binary.read_bytes()) == binary_hash, "permission binary changed during observation")
                    key = f"r{round_id:02}-{scenario}-{mode}"
                    fixture = directory / "fixtures" / key
                    token = secrets.token_hex(32)
                    server = Experiment(binary,fixture,mode,token)
                    record = {"key":key,"round":round_id,"scenario":scenario,"mode":mode,"order_index":len(records)+1,
                              "binary_sha256":binary_hash,"server_stopped":False,"api_matches_disk":False}
                    records.append(record)
                    session, events = None, []
                    try:
                        with server:
                            if info is None:
                                info = manifest(binary,fixture,run_id,config)
                                info.update(suite="static/dynamic permission-time windows", config=asdict(config),
                                            measurement={"clock":"time.perf_counter_ns","unit":"integer nanoseconds",
                                                "window":"fixed task observation duration, actual monotonic start and configured deadline; every probe must finish inside it",
                                                "preparation":"identical seed write and explicit revocation before each window",
                                                "start_bounds":"grant HTTP request/response; activation occurs within this bracket",
                                                "end_bounds":"earliest TTL deadline bracket, successful phase/revoke request, or successful exhausted-use write",
                                                "TTL_bounds":"grant request bracket plus TTL, checked against actual expiry snapshots",
                                                "estimate":"midpoints; conservative lower/upper union durations are also exported",
                                                "policy_delta":"static ACL ignores phase/leases; same Rust HTTP/file/audit path",
                                                "not_claimed":["exact internal commit timestamps","attack probability","statistical confidence intervals"]})
                            experiment = json.loads((fixture / "experiment.json").read_text(encoding="utf-8"))
                            experiment["authorization_mode"] = experiment.pop("authorization")
                            record.update(instance_id=server.instance_id,initial_files=workspace_state(fixture),
                                          policy_sha256=digest((fixture / "policy.json").read_bytes()),experiment=experiment)
                            session = WindowSession(server,token,config,scenario)
                            session.execute()
                            events = session.events()
                            record.update(task_id=session.task_id,window_start_ns=session.window_start_ns,window_end_ns=session.window_end_ns,
                                          observation_finished_ns=session.observation_finished_ns,window_start_utc=session.window_start_utc,
                                          window_end_utc_observed=session.window_end_utc_observed,probes=session.probes,
                                          final_files=workspace_state(fixture),final_content_sha256=digest(session.current.encode()))
                    finally:
                        record["server_stopped"] = server.process is not None and server.process.poll() is not None
                        if session:
                            if events:
                                raw = (fixture / "audit.jsonl").read_bytes()
                                check(raw.endswith(b"\n"), "permission audit has incomplete trailing line")
                                check([json.loads(line) for line in raw.decode().splitlines()] == events, "permission API audit differs from disk")
                                record["api_matches_disk"] = True
                            exported = sanitize(events,session.secrets,fixture)
                            safe_evidence([record,session.requests,exported],session.secrets)
                            for folder, values in (("responses",session.requests),("events",exported)):
                                (public / folder / f"{key}.jsonl").write_text("".join(json.dumps(value,ensure_ascii=False,allow_nan=False)+"\n" for value in values),encoding="utf-8",newline="\n")
                            session.secrets.clear()
                    from bench.verify_windows import validate_run
                    validate_run(record,session.requests,exported,config)
                    rows.extend(interval_rows(record,session.requests))
                    print(key+" OBSERVED",flush=True)
        summary = summarize(records,rows,config)
        summary.update(run_id=run_id,config=asdict(config))
        write_csv(public / "permission_windows.csv",rows)
        write_json(public / "permission_summary.json",summary)
        write_json(public / "runs.json",records)
        write_json(public / "run_manifest.json",info)
        from bench.verify_windows import verify_run
        verify_run(directory)
        return 0,directory
    except (Exception,KeyboardInterrupt) as error:
        write_json(public / "permission_summary.json",{"status":"failed","qualified_observation":False,
                   "failure":str(error) if isinstance(error,(BenchError,CaseFailure)) else type(error).__name__,"config":asdict(config),"run_id":run_id})
        write_json(public / "runs.json",records)
        if info:
            write_json(public / "run_manifest.json",info)
        return 1,directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-bin",type=Path,default=default_binary())
    parser.add_argument("--results-dir",type=Path,default=REPO / "results")
    parser.add_argument("--rounds",type=int)
    parser.add_argument("--window-seconds",type=float)
    parser.add_argument("--ttl-seconds",type=int)
    parser.add_argument("--smoke",action="store_true")
    args = parser.parse_args()
    try:
        config = WindowConfig(args.rounds if args.rounds is not None else 1 if args.smoke else 3,
                              args.window_seconds if args.window_seconds is not None else 4 if args.smoke else 6,
                              args.ttl_seconds if args.ttl_seconds is not None else 1 if args.smoke else 2,formal=not args.smoke)
        code,directory = run(args.experiment_bin.resolve(),args.results_dir.resolve(),config)
    except Exception as error:
        print("PERMISSION_FAIL: "+type(error).__name__,file=sys.stderr)
        return 1
    print("Results: "+str(directory))
    print("PERMISSION_FAIL" if code else "PERMISSION_SMOKE_PASS" if args.smoke else "PERMISSION_PASS")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
