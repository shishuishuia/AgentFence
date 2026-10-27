"""Run the deterministic file-tool workflow against a fresh release Rust server."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sys
import uuid

from integrations import BrokerHttp
from .fixture import prepare_fixture
from .manifest import create_manifest
from .runtime import ManagedBroker, REPO, default_binary
from .verify import check_no_sensitive_data, verify_run


from .workflow import digest, execute_flow, require, write_json


def run(binary: Path, results_parent: Path) -> tuple[int, Path]:
    results_parent.mkdir(parents=True, exist_ok=True)
    run_id = "local-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    directory = results_parent / run_id
    directory.mkdir(exist_ok=False)
    fixture = directory / "fixture"
    prepare_fixture(fixture)
    report = {"run_id": run_id, "status": "running", "checks": [], "started_at": datetime.now(timezone.utc).isoformat()}
    report_path = directory / "minimal_demo.json"
    admin_token = secrets.token_hex(32)
    credentials = [admin_token]
    server = ManagedBroker(binary, fixture, admin_token)
    try:
        report["initial_files"] = {
            "workspace/src/main.rs": digest((fixture / "workspace/src/main.rs").read_bytes()),
            "workspace/Cargo.toml": digest((fixture / "workspace/Cargo.toml").read_bytes()),
            "simulated-private.txt": digest((fixture / "simulated-private.txt").read_bytes()),
        }
        manifest = create_manifest(binary, fixture, run_id)
        check_no_sensitive_data(manifest, credentials)
        write_json(directory / "run_manifest.json", manifest)
        with server:
            http = BrokerHttp(server.base_url)
            report["instance_id"] = http.health().require(200)["instance_id"]
            task_id, events, detail, pages = execute_flow(http, admin_token, fixture, report["checks"], credentials)
            report["task_id"] = task_id
            report["final_detail"] = detail
            report["event_pages"] = pages
        require(server.process.poll() is not None, "owned server did not stop")
        report["server_stopped"] = True
        report["target_sha256"] = digest((fixture / "workspace/src/main.rs").read_bytes())
        report["verification"] = verify_run(report, fixture, events, secrets=credentials)
        check_no_sensitive_data([report, manifest, events], credentials)
        (directory / "demo_events.jsonl").write_text(
            "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8",
        )
        report["status"] = "passed"
        code = 0
    except (Exception, KeyboardInterrupt) as error:
        report["status"] = "failed"
        # Never print arbitrary server bodies, process environments, or tokens.
        failure = str(error)
        for credential in credentials:
            failure = failure.replace(credential, "[REDACTED]")
        report["failure"] = failure
        report["server_stopped"] = server.process is None or server.process.poll() is not None
        print(f"MINIMAL_DEMO_FAIL: {report['failure']}", file=sys.stderr)
        code = 1
    finally:
        admin_token = ""
        credentials.clear()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json(report_path, report)
    return code, directory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-bin", type=Path, default=default_binary())
    parser.add_argument("--results-dir", type=Path, default=REPO / "results")
    args = parser.parse_args()
    try:
        code, directory = run(args.server_bin, args.results_dir.resolve())
    except (Exception, KeyboardInterrupt) as error:
        print(f"MINIMAL_DEMO_FAIL: {type(error).__name__}; cannot prepare demo run", file=sys.stderr)
        return 1
    print(f"Results: {directory}")
    if code == 0:
        print("MINIMAL_DEMO_PASS")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
