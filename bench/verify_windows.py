"""Reconstruct permission intervals from HTTP/audit/lease/file proofs, not saved totals."""

import argparse
from datetime import datetime
import json
from pathlib import Path

from bench.evidence import workspace_state
from bench.stats import check
from bench.verify import compare_csv
from bench.window_session import input_signature, stable_detail
from bench.window_stats import CAUSES, WindowConfig, interval_rows, summarize
from demo.e2e_support import sanitize, safe_evidence, validate_audit


def validate_run(record, requests, events, config):
    check(record["server_stopped"] is True and record["api_matches_disk"] is True,"permission server cleanup or audit comparison missing")
    check(record["mode"] in ("enforced","baseline") and record["scenario"] in CAUSES,"invalid permission treatment")
    experiment = record["experiment"]
    check(experiment["mode"] == record["mode"] and experiment["authorization_mode"] == ("phase_path_lease" if record["mode"] == "enforced" else "static_path_acl"),"permission strategy differs")
    check(experiment["http"] == "agentfence_server::app" and experiment["file_operations"] == "LocalFiles" and experiment["audit"] == "FileSink::append + sync_data" and experiment["synthetic_fixture"] is True,"permission experiment execution paths differ")
    start,end = record["window_start_ns"],record["window_end_ns"]
    check(end-start == int(config.window_seconds*1_000_000_000) and record["observation_finished_ns"] >= end,"permission observation did not cover the complete fixed task window")
    previous_end = -1
    grants = [request for request in requests if request["label"].startswith("grant:")]
    check([request["label"] for request in grants] == (["grant:1","grant:2"] if record["scenario"] in ("ttl","revoke") else ["grant:1"]),"permission grant intervals incomplete")
    check(len([request for request in requests if request["path"] == "/v1/tool/execute"]) == len(record["probes"]),"unrecorded permission probe")
    for request in requests:
        check(type(request["started_ns"]) is int and type(request["finished_ns"]) is int and previous_end <= request["started_ns"] < request["finished_ns"],"invalid permission HTTP chronology")
        previous_end = request["finished_ns"]
        check(request["instance_id"] == record["instance_id"],"permission request instance changed")
        if request["path"] == "/v1/admin/tasks" and request["method"] == "POST":
            check(request["status"] == 201 and request["body"]["task_id"] == record["task_id"] and request["body"]["phase"] == "inspect","incorrect permission task creation")
        if request["path"].endswith("/phase"):
            check(request["status"] == 200 and request["body"]["phase"] == request["request_body"]["phase"],"permission phase transition did not succeed")
        if request["method"] == "DELETE":
            check(request["status"] == 200 and request["body"]["revoked"] is True and request["path"].endswith("/grants/"+request["body"]["grant_id"]),"permission revocation did not succeed")
    labels = [probe["label"] for probe in record["probes"]]
    expected = {"ttl":["seed","before_grant","active:1","after_expiry:1","active:2","after_expiry:2","window_tail"],
                "phase":["seed","before_grant","active:1","after_phase","window_tail"],
                "revoke":["seed","before_grant","active:1","after_revoke","active:2","after_phase","window_tail"],
                "uses":["seed","before_grant","first_use","last_use","after_used_up","window_tail"]}[record["scenario"]]
    check(labels == expected,"missing or reordered permission probes")
    previous_files = record["initial_files"]
    for probe in record["probes"]:
        request = requests[probe["request_index"]]
        check(request["label"] == "probe:"+probe["label"] and request["path"] == "/v1/tool/execute" and request["method"] == "POST","permission proof references another request")
        if probe["label"] == "seed":
            check(request["finished_ns"] < start,"seed/preparation entered permission window")
        else:
            check(start <= request["started_ns"] < request["finished_ns"] <= end,"probe outside the common observation window")
        reason = "OK" if record["mode"] == "baseline" else probe["expected_dynamic_reason"]
        check(reason in ("OK","LEASE_REQUIRED","LEASE_EXPIRED","LEASE_REVOKED","LEASE_USED_UP","INVALID_STAGE"),"unknown permission outcome")
        # Expected reasons are fixed by the named probe, not learned from the response.
        expected_reason = ("LEASE_REQUIRED" if probe["label"] == "before_grant" else "LEASE_EXPIRED" if probe["label"].startswith("after_expiry") else "LEASE_REVOKED" if probe["label"] == "after_revoke" else "INVALID_STAGE" if probe["label"] == "after_phase" else "LEASE_USED_UP" if probe["label"] == "after_used_up" else {"ttl":"LEASE_EXPIRED","phase":"INVALID_STAGE","revoke":"INVALID_STAGE","uses":"LEASE_USED_UP"}[record["scenario"]] if probe["label"] == "window_tail" else "OK")
        check(probe["expected_dynamic_reason"] == expected_reason,"permission probe expected outcome was altered")
        allowed = reason == "OK"
        body = request["body"]
        check(request["status"] == (200 if allowed else 403) and body["reason_code"] == reason and body["allowed"] is allowed and body["executed"] is allowed,"permission response does not match fixed expected result")
        before,after = probe["before_detail"],probe["after_detail"]
        before_request,after_request = requests[probe["request_index"]-1],requests[probe["request_index"]+1]
        check(before_request["method"] == after_request["method"] == "GET" and before_request["status"] == after_request["status"] == 200 and before_request["body"] == before and after_request["body"] == after,"permission snapshots differ from actual HTTP queries")
        check(probe["before_files"] == previous_files,"unexplained file change between permission probes")
        previous_files = probe["after_files"]
        if not allowed:
            check(probe["before_files"] == probe["after_files"] and stable_detail(before) == stable_detail(after) and body["data"] is None,"denied permission probe caused a side effect")
        else:
            content = request["request_redactions"]["/content"]
            predicted = dict(probe["before_files"])
            predicted["workspace/src/main.rs"] = {"kind":"file","sha256":content["sha256"],"bytes":config.file_bytes}
            check(content["bytes"] == config.file_bytes and probe["after_files"] == predicted and body["data"]["bytes_written"] == config.file_bytes,"allowed probe file/byte evidence differs")
            if record["mode"] == "enforced":
                gid = request["request_body"]["grant_id"]
                a = next(g for g in before["grants"] if g["grant_id"] == gid)
                b = next(g for g in after["grants"] if g["grant_id"] == gid)
                check(a["status"] == "active" and b["remaining_uses"] == a["remaining_uses"]-1 and body["data"]["remaining_uses"] == b["remaining_uses"] and body["data"]["grant_id"] == gid,"incorrect dynamic lease consumption")
            else:
                check(before["grants"] == after["grants"] and body["data"]["grant_id"] is None and body["data"]["remaining_uses"] is None,"baseline incorrectly consumed a lease")
    for grant in grants:
        submitted,value = grant["request_body"],grant["body"]
        check(grant["status"] == 201 and submitted["path"] == "src/main.rs" and value["path"] == "src/main.rs" and value["status"] == "active" and value["remaining_uses"] == submitted["max_uses"],"invalid permission grant")
        check(submitted["ttl_seconds"] == (config.ttl_seconds if record["scenario"] == "ttl" else 300) and submitted["max_uses"] == (2 if record["scenario"] == "uses" else 10),"permission grant parameters differ from config")
        issued = next(event for event in events if event["request_id"] == value["request_id"] and event["event_type"] == "grant_issued")
        expiry = datetime.fromisoformat(value["expires_at"].replace("Z","+00:00"))
        issued_at = datetime.fromisoformat(issued["timestamp"].replace("Z","+00:00"))
        audit_expiry = datetime.fromisoformat(issued["expires_at"].replace("Z","+00:00"))
        check((expiry-issued_at).total_seconds() == submitted["ttl_seconds"] and audit_expiry == expiry,"server TTL/audit deadline differs from the requested lease")
        if record["scenario"] == "ttl":
            gid = value["grant_id"]
            polls = [(request,next((g for g in request["body"]["grants"] if g["grant_id"] == gid),None)) for request in requests if request["label"] == "poll:expiry"]
            expired = [request for request,lease in polls if lease and lease["status"] == "expired"]
            check(expired and expired[0]["finished_ns"] >= grant["started_ns"]+config.ttl_seconds*1_000_000_000,"TTL interval has no actual expiry observation")
    predicted = dict(record["initial_files"])
    predicted["workspace/src/main.rs"] = {"kind":"file","bytes":config.file_bytes,"sha256":record["final_content_sha256"]}
    check(record["final_files"] == predicted,"permission run changed an unexpected final file")
    check(record["probes"][-1]["after_files"] == record["final_files"],"final files differ from the last real probe")
    validate_audit(requests,events)
    safe_evidence([record,requests,events],[])


def verify_run(directory, *, evidence_only=False):
    directory = Path(directory).resolve()
    public = directory / "public" if (directory / "public").is_dir() else directory
    summary = json.loads((public / "permission_summary.json").read_text(encoding="utf-8"))
    info = json.loads((public / "run_manifest.json").read_text(encoding="utf-8"))
    records = json.loads((public / "runs.json").read_text(encoding="utf-8"))
    config = WindowConfig(**summary["config"])
    check(info["config"] == summary["config"],"permission manifest config differs")
    expected = [(r,c,m) for r in range(1,config.rounds+1) for c in CAUSES[(r-1)%4:]+CAUSES[:(r-1)%4] for m in (("enforced","baseline") if r%2 else ("baseline","enforced"))]
    check([(record["round"],record["scenario"],record["mode"]) for record in records] == expected,"incomplete or reordered permission runs")
    check([record["order_index"] for record in records] == list(range(1,len(records)+1)) and len({record["instance_id"] for record in records}) == len(records),"permission instances were reused")
    check(all(record["binary_sha256"] == info["server_binary_sha256"] for record in records) and len({record["policy_sha256"] for record in records}) == 1 and all(record["initial_files"] == records[0]["initial_files"] for record in records),"permission treatments did not use identical binary/fixture/policy")
    rows,signatures = [],{}
    for record in records:
        key = record["key"]
        check(key == f"r{record['round']:02}-{record['scenario']}-{record['mode']}","invalid permission artifact key")
        requests = [json.loads(line) for line in (public / "responses" / f"{key}.jsonl").read_text(encoding="utf-8").splitlines()]
        events = [json.loads(line) for line in (public / "events" / f"{key}.jsonl").read_text(encoding="utf-8").splitlines()]
        validate_run(record,requests,events,config)
        rows.extend(interval_rows(record,requests))
        signatures[(record["round"],record["scenario"],record["mode"])] = [input_signature(request) for request in requests if not request["label"].startswith("poll:")]
        if not evidence_only:
            fixture = directory / "fixtures" / key
            check(workspace_state(fixture) == record["final_files"],"permission retained files changed")
            raw = (fixture / "audit.jsonl").read_bytes()
            check(raw.endswith(b"\n") and sanitize([json.loads(line) for line in raw.decode().splitlines()],[],fixture) == events,"permission retained audit changed")
    for round_id in range(1,config.rounds+1):
        for scenario in CAUSES:
            check(signatures[(round_id,scenario,"enforced")] == signatures[(round_id,scenario,"baseline")],"paired permission management/probe inputs differ")
    compare_csv(public / "permission_windows.csv",rows)
    predicted = summarize(records,rows,config)
    predicted.update(run_id=info["run_id"],config=summary["config"])
    check(predicted == summary,"permission summary differs from reconstructed interval unions")
    return {"formal":config.formal,"runs":len(records),"intervals":len(rows),"comparisons":len(predicted["comparisons"]),"files_checked":not evidence_only}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory",type=Path)
    parser.add_argument("--evidence-only",action="store_true")
    args = parser.parse_args()
    try:
        result = verify_run(args.directory,evidence_only=args.evidence_only)
    except Exception as error:
        print("PERMISSION_VERIFY_FAIL: "+type(error).__name__)
        return 1
    print("PERMISSION_EVIDENCE_PASS" if args.evidence_only else "PERMISSION_VERIFY_PASS")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
