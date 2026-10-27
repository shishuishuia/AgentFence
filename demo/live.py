"""Opt-in local runner for a fixed Agent task; never an arbitrary tool gateway."""

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
import uuid

from integrations import BrokerHttp
from .fixture import BEFORE, AFTER
from .verify import check_no_sensitive_data
from .workflow import execute_flow, require, write_json


def now():
    return datetime.now(timezone.utc).isoformat()


class LiveDemo:
    """Serialize fixed tasks on the launcher's disposable workspace.

    The caller owns the fixture and broker. Tokens stay in memory. Request IDs
    deduplicate starts; pause/resume must target the current run ID.
    """

    def __init__(self, upstream: str, token: str, fixture: Path, results: Path, *, step_delay=2.5):
        self.upstream, self._token = upstream, token
        self.fixture, self.results = fixture, results
        self.delay = step_delay
        self.instance = BrokerHttp(upstream).health().require(200)["instance_id"]
        self._condition = threading.Condition()
        self._seen = OrderedDict()
        self._closed = False
        self._thread = None
        self._state = {
            "enabled": True, "instance_id": self.instance, "run_id": None,
            "status": "idle", "task_id": None, "phase": None, "completed_steps": 0,
            "total_steps": 13, "steps": [], "started_at": None, "finished_at": None,
            "failure": None, "verified": False,
        }

    def snapshot(self):
        """Return a detached, credential-free snapshot for browser polling."""
        with self._condition:
            return deepcopy(self._state)

    def control(self, action, request_id):
        """Return HTTP status/state; do not retry or queue tool writes."""
        with self._condition:
            if self._closed:
                return 409, self.snapshot()
            if action == "start":
                if request_id in self._seen:
                    return (200 if self._seen[request_id] == self._state["run_id"] else 409), self.snapshot()
                if self._state["status"] in ("running", "paused"):
                    return 409, self.snapshot()
                run_id = "run_" + uuid.uuid4().hex
                self._seen[request_id] = run_id
                # Bound memory; retained keys cannot accidentally replay recent starts.
                if len(self._seen) > 128:
                    self._seen.popitem(last=False)
                self._state.update(run_id=run_id, status="running", task_id=None, phase=None,
                                   completed_steps=0, steps=[], started_at=now(), finished_at=None,
                                   failure=None, verified=False)
                self._thread = threading.Thread(target=self._run, args=(run_id,), daemon=True)
                self._thread.start()
                return 202, self.snapshot()
            if request_id != self._state["run_id"]:
                return 409, self.snapshot()
            if action == "pause" and self._state["status"] in ("running", "paused"):
                self._state["status"] = "paused"
            elif action == "resume" and self._state["status"] in ("running", "paused"):
                self._state["status"] = "running"
                self._condition.notify_all()
            else:
                return 409, self.snapshot()
            return 200, self.snapshot()

    def _wait(self):
        deadline = time.monotonic() + self.delay
        with self._condition:
            while True:
                if self._closed:
                    raise RuntimeError("demo stopped")
                if self._state["status"] == "paused":
                    self._condition.wait(0.1)
                    deadline = time.monotonic() + self.delay
                elif time.monotonic() < deadline:
                    self._condition.wait(min(0.1, deadline - time.monotonic()))
                else:
                    return

    def _run(self, run_id):
        credentials = [self._token]
        checks = []
        try:
            # Fixture reset is harness preparation between completed tasks. Agent
            # reads/writes below still exclusively use the Rust HTTP tools.
            target = self.fixture / "workspace/src/main.rs"
            require(not target.is_symlink() and target.is_file(), "invalid demo fixture")
            target.write_bytes(BEFORE.encode("utf-8"))

            def step(entry):
                public = {key: entry[key] for key in (
                    "case", "http_status", "reason_code", "request_id", "allowed", "executed", "pass",
                )}
                # Copy only fixed-schema summaries, not tokens or file contents.
                with self._condition:
                    self._state["steps"].append(public)
                    self._state["completed_steps"] = len(self._state["steps"])
                    self._state["phase"] = (
                        "done" if entry["case"] in ("advance_done", "done_read_denied") else
                        "verify" if entry["case"] in ("advance_verify", "verify_read", "verify_write_denied") else
                        "edit" if entry["case"] in ("advance_edit", "issue_grant", "edit_write", "reuse_denied", "outside_read_denied") else "inspect"
                    )
                if entry["pass"]:
                    self._wait()

            def checkpoint(_name, task_id):
                with self._condition:
                    self._state["task_id"] = task_id

            task, events, detail, pages = execute_flow(
                BrokerHttp(self.upstream), self._token, self.fixture, checks, credentials,
                checkpoint=checkpoint, on_step=step,
            )
            require(len(checks) == 13 and all(c["pass"] for c in checks), "incomplete demo workflow")
            require(target.read_bytes() == AFTER.encode("utf-8"), "unexpected final file")
            records = [json.loads(line) for line in (self.fixture / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
            require([e for e in records if e.get("task_id") == task] == events, "API and disk audit differ")
            require(len(events) == 17, "unexpected audit count")
            report = {"run_id": run_id, "task_id": task, "instance_id": self.instance,
                      "status": "passed", "checks": checks, "final_detail": detail, "event_pages": pages}
            check_no_sensitive_data([report, events], credentials)
            directory = self.results / run_id
            directory.mkdir()
            write_json(directory / "dashboard_demo.json", report)
            write_json(directory / "events.json", {"events": events})
            with self._condition:
                self._state.update(status="succeeded", verified=True, finished_at=now())
        except Exception as error:
            # Exception text can contain server data or paths. Publish a fixed message.
            failure = "执行或核验失败。请检查本地演示记录；暂停过久可能导致租约过期。"
            if checks and not checks[-1]["pass"]:
                entry = checks[-1]
                if len(self.snapshot()["steps"]) < len(checks):
                    step(entry)
                failure = f"步骤 {entry['case']} 返回 HTTP {entry['http_status']}（{entry['reason_code'] or 'UNKNOWN'}），与预期不符。请重新运行任务。"
            with self._condition:
                self._state.update(status="failed", finished_at=now(), failure=failure)
                failure_state = deepcopy(self._state)
            directory = self.results / run_id
            directory.mkdir(exist_ok=True)
            write_json(directory / "failure.json", {"error_type": type(error).__name__, "state": failure_state})
        finally:
            credentials.clear()

    def close(self):
        """Unblock pauses and join the workflow before its broker is stopped."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        if self._thread:
            self._thread.join(timeout=30)
            if self._thread.is_alive():
                raise RuntimeError("demo worker did not stop")
