"""Three-round paired HTTP sampling using one release Rust experiment binary."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sys
import time
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bench.evidence import SCENARIOS, contents, manifest, sample_from_request, validate_run, workspace_state
from bench.runtime import Experiment, default_binary
from bench.stats import Config, BenchError, check, summarize
from demo.e2e_support import canonical, digest, sanitize, safe_evidence
from demo.runtime import REPO
from demo.workflow import write_json
from integrations import AdminClient, BrokerHttp


class Session:
    def __init__(self, server, token, config, round_id, mode, samples, warmups):
        self.server, self.config = server, config
        self.secrets = [token]
        self.http = BrokerHttp(server.base_url)
        self.admin = AdminClient(self.http, token)
        self.round, self.mode = round_id, mode
        self.samples, self.warmups = samples, warmups
        self.requests, self.batches = [], []
        self.current = contents(config.file_bytes)[0]
        self.write_index = 0
        self.unlisted_bytes = (server.directory / "workspace/unlisted.txt").stat().st_size

    def call(self, path, body=None, *, method="POST", agent=False, metadata=None):
        meta = {"phase": "setup", "round": self.round, "mode": self.mode, **(metadata or {})}
        if meta["phase"] in {"warmup", "measured"}:
            meta["resource_bytes"] = self.unlisted_bytes if meta["scenario"] == "deny" else self.config.file_bytes
        started = time.perf_counter_ns() if meta["phase"] != "setup" else None
        try:
            response = self.http.request(method, path, token=self.agent_token if agent else self.secrets[0], body=body)
        except Exception as error:
            elapsed = time.perf_counter_ns() - started if started is not None else None
            self.requests.append({**meta, "path": path, "method": method, "status": 0, "elapsed_ns": elapsed, "transport_error": type(error).__name__})
            raise BenchError("HTTP request failed; no automatic retry; run is invalid") from None
        elapsed = time.perf_counter_ns() - started if started is not None else None
        credential = response.payload.get("agent_token")
        if isinstance(credential, str):
            self.secrets.append(credential)
        redactions, request_redactions = {}, {}
        record = {**meta, "path": path, "method": method, "status": response.status, "elapsed_ns": elapsed,
                  "instance_id": self.server.instance_id,
                  "body": sanitize(response.payload, self.secrets, self.server.directory, redactions), "redactions": redactions,
                  "request_body": sanitize(body, self.secrets, self.server.directory, request_redactions), "request_redactions": request_redactions,
                  "response_json_sha256": digest(canonical(response.payload))}
        self.requests.append(record)
        if meta["phase"] in {"warmup", "measured"}:
            row = sample_from_request(record, self.config)
            (self.samples if meta["phase"] == "measured" else self.warmups).append(row)
        return response

    def verify_files(self, initial):
        predicted = dict(initial)
        predicted["workspace/src/main.rs"] = {"kind": "file", "sha256": digest(self.current.encode()), "bytes": self.config.file_bytes}
        check(workspace_state(self.server.directory) == predicted, "actual write or non-target file invariant failed")

    def write_batch(self, phase, count, start_sequence, initial, *, seed=False):
        batch_id = f"r{self.round}-{self.mode}-{phase}-b{len(self.batches) + 1}"
        grant = self.call(f"/v1/admin/tasks/{self.task_id}/grants", {"tool": "write_file", "path": "src/main.rs", "ttl_seconds": 300, "max_uses": count}).require(201)
        gid = grant["grant_id"]
        before = next(g for g in self.admin.task(self.task_id).require(200)["grants"] if g["grant_id"] == gid)
        for offset in range(count):
            if not seed:
                self.write_index += 1
                self.current = contents(self.config.file_bytes)[self.write_index % 2]
            sequence = start_sequence + offset
            meta = {"phase": "setup" if seed else phase, "scenario": "write", "sequence": sequence, "batch_id": batch_id,
                    "expected_remaining": count - offset - 1, "expected_content_sha256": digest(self.current.encode()),
                    "sample_id": f"r{self.round}-{self.mode}-write-{phase}-{sequence:05}"}
            body = {"task_id": self.task_id, "tool": "write_file", "path": "src/main.rs", "content": self.current, "grant_id": gid}
            response = self.call("/v1/tool/execute", body, agent=True, metadata=meta)
            response.require(200)
        after = next(g for g in self.admin.task(self.task_id).require(200)["grants"] if g["grant_id"] == gid)
        self.verify_files(initial)
        revoked = self.call(f"/v1/admin/tasks/{self.task_id}/grants/{gid}", method="DELETE").require(200)
        self.batches.append({"batch_id": batch_id, "phase": phase, "seed": seed, "uses": count,
                             "grant": grant, "before": before, "after": after, "revoked": revoked,
                             "file_sha256_after": digest(self.current.encode())})

    def execute(self, initial):
        created = self.call("/v1/admin/tasks", {"workspace_id": "demo"}).require(201)
        self.task_id, self.agent_token = created["task_id"], created["agent_token"]
        self.call(f"/v1/admin/tasks/{self.task_id}/phase", {"phase": "edit"}).require(200)
        self.write_batch("seed", 1, 1, initial, seed=True)
        shift = (self.round - 1) % 3
        order = list(SCENARIOS[shift:] + SCENARIOS[:shift])
        for scenario in order:
            for phase, count in (("warmup", self.config.warmup), ("measured", self.config.iterations)):
                if scenario == "write":
                    for start in range(1, count + 1, 10):
                        self.write_batch(phase, min(10, count - start + 1), start, initial)
                else:
                    for sequence in range(1, count + 1):
                        meta = {"phase": phase, "scenario": scenario, "sequence": sequence,
                                "expected_content_sha256": digest(self.current.encode()),
                                "sample_id": f"r{self.round}-{self.mode}-{scenario}-{phase}-{sequence:05}"}
                        self.call("/v1/tool/execute", {"task_id": self.task_id, "tool": "read_file", "path": "unlisted.txt" if scenario == "deny" else "src/main.rs"}, agent=True, metadata=meta)
        self.verify_files(initial)
        return order

    def events(self):
        events, cursor = [], None
        for _ in range(1000):
            page = self.admin.events(after=cursor, limit=500).require(200)
            check(page["instance_id"] == self.server.instance_id, "instance changed during sampling")
            events.extend(page["events"])
            if not page["has_more"]:
                return events
            check(page["next_cursor"] != cursor, "audit cursor did not advance")
            cursor = page["next_cursor"]
        raise BenchError("audit pagination exceeded limit")


def write_csv(path, rows):
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(binary, results, config):
    check(binary.is_file(), "build release http_experiment with bench-harness first")
    results.mkdir(parents=True, exist_ok=True)
    run_id = "local-bench-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    directory = results / run_id
    public = directory / "public"
    for path in (directory / "fixtures", public / "responses", public / "events"):
        path.mkdir(parents=True)
    samples, warmups, runs = [], [], []
    info, failure = None, None
    binary_hash = digest(binary.read_bytes())
    try:
        for round_id in range(1, config.rounds + 1):
            modes = ("enforced", "baseline") if round_id % 2 else ("baseline", "enforced")
            for mode in modes:
                check(digest(binary.read_bytes()) == binary_hash, "experiment binary changed during benchmark")
                key = f"r{round_id:02}-{mode}"
                fixture = directory / "fixtures" / key
                token = secrets.token_hex(32)
                server = Experiment(binary, fixture, mode, token)
                session, events = None, []
                record = {"key": key, "round": round_id, "mode": mode, "order_index": len(runs) + 1, "binary_sha256": binary_hash,
                          "server_stopped": False, "api_matches_disk": False}
                runs.append(record)
                try:
                    with server:
                        if info is None:
                            info = manifest(binary, fixture, run_id, config)
                        initial = workspace_state(fixture)
                        policy_hash = digest((fixture / "policy.json").read_bytes())
                        experiment_info = json.loads((fixture / "experiment.json").read_text(encoding="utf-8"))
                        experiment_info["authorization_mode"] = experiment_info.pop("authorization")
                        record.update(initial_files=initial, policy_sha256=policy_hash, instance_id=server.instance_id,
                                      experiment=experiment_info)
                        session = Session(server, token, config, round_id, mode, samples, warmups)
                        record["scenario_order"] = session.execute(initial)
                        record["phase"] = session.admin.task(session.task_id).require(200)["phase"]
                        events = session.events()
                        record.update(final_content_sha256=digest(session.current.encode()), final_files=workspace_state(fixture), batches=session.batches)
                finally:
                    record["server_stopped"] = server.process is None or server.process.poll() is not None
                    if session:
                        if events:
                            raw = (fixture / "audit.jsonl").read_bytes()
                            check(raw.endswith(b"\n"), "audit has incomplete trailing line")
                            disk = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
                            check(disk == events, "API audit differs from disk")
                            record["api_matches_disk"] = True
                        safe = sanitize(events, session.secrets, fixture)
                        safe_evidence([record, session.requests, safe], session.secrets)
                        for folder, values in (("responses", session.requests), ("events", safe)):
                            (public / folder / f"{key}.jsonl").write_text("".join(json.dumps(v, ensure_ascii=False, allow_nan=False) + "\n" for v in values), encoding="utf-8")
                        session.secrets.clear()
                validate_run(record, session.requests, safe, config)
                print(key + " SAMPLED", flush=True)
        check(len({r["policy_sha256"] for r in runs}) == 1 and all(r["initial_files"] == runs[0]["initial_files"] for r in runs), "paired fixtures differ")
        summary = summarize(samples, config)
    except (Exception, KeyboardInterrupt) as error:
        failure = str(error) if isinstance(error, BenchError) else type(error).__name__
        summary = {"status": "failed", "qualified_sampling": False, "sample_count": len(samples), "failure": failure}
    summary.update(run_id=run_id, config=asdict(config), warmup_count=len(warmups))
    write_csv(public / "bench_samples.csv", samples)
    write_csv(public / "warmup_samples.csv", warmups)
    write_json(public / "bench_summary.json", summary)
    write_json(public / "runs.json", runs)
    if info:
        write_json(public / "run_manifest.json", info)
    if not failure:
        from bench.verify import verify_run
        try:
            verify_run(directory)
        except (Exception, KeyboardInterrupt) as error:
            failure = str(error) if isinstance(error, BenchError) else type(error).__name__
            write_json(public / "bench_summary.json", {"status": "failed", "qualified_sampling": False,
                       "sample_count": len(samples), "failure": failure, "run_id": run_id,
                       "config": asdict(config), "warmup_count": len(warmups)})
    return 1 if failure else 0, directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment-bin", type=Path, default=default_binary())
    parser.add_argument("--results-dir", type=Path, default=REPO / "results")
    parser.add_argument("--iterations", type=int)
    parser.add_argument("--rounds", type=int)
    parser.add_argument("--warmup", type=int)
    parser.add_argument("--file-bytes", type=int, default=4096)
    parser.add_argument("--smoke", action="store_true", help="small correctness run; never a qualified performance result")
    args = parser.parse_args()
    try:
        config = Config(args.iterations if args.iterations is not None else 5 if args.smoke else 100,
                        args.rounds if args.rounds is not None else 1 if args.smoke else 3,
                        args.warmup if args.warmup is not None else 2 if args.smoke else 20, args.file_bytes, not args.smoke)
        code, directory = run(args.experiment_bin.resolve(), args.results_dir.resolve(), config)
    except Exception as error:
        print("BENCH_FAIL: " + (str(error) if isinstance(error, BenchError) else type(error).__name__), file=sys.stderr)
        return 1
    print("Results: " + str(directory))
    print("BENCH_FAIL" if code else "BENCH_SMOKE_PASS" if args.smoke else "BENCH_PASS")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
