"""Keep an isolated Rust broker and read-only proxy alive for the Vue console."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import queue
import secrets
import signal
import subprocess
import sys
import threading
import time
import uuid

from integrations import BrokerHttp, ClientError
from .fixture import prepare_fixture
from .runtime import ManagedBroker, REPO, default_binary
from .verify import check_no_sensitive_data
from .workflow import execute_flow, write_json


class Dashboard:
    """Own exactly two child processes; retain no credential in saved output."""

    def __init__(self, binary: Path, results: Path, mode="dist", port=4173):
        self.binary, self.results, self.mode, self.port = binary, results, mode, port
        self.proxy = None
        self.broker = None
        self.url = None
        self.directory = None
        self._reader = None
        self._credentials = []

    def __enter__(self):
        try:
            self.start()
            return self
        except BaseException:
            self.stop()
            raise

    def start(self):
        if self.mode == "dist" and not (REPO / "front/dist/index.html").is_file():
            raise RuntimeError("Build front/dist first, or select --mode dev")
        self.results.mkdir(parents=True, exist_ok=True)
        self.directory = self.results / ("local-dashboard-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
        self.directory.mkdir()
        prepare_fixture(self.directory / "fixture")
        self._credentials = [secrets.token_hex(32)]
        self.broker = ManagedBroker(self.binary, self.directory / "fixture", self._credentials[0])
        self.broker.start()
        self.instance = BrokerHttp(self.broker.base_url).health().require(200)["instance_id"]
        env = os.environ.copy()
        env.update({"AGENTFENCE_BROKER_URL": self.broker.base_url, "AGENTFENCE_ADMIN_TOKEN": self._credentials[0]})
        self.proxy = subprocess.Popen(
            [sys.executable, "-m", "web_proxy", "--mode", self.mode, "--port", str(self.port)],
            cwd=REPO, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        ready = queue.Queue(maxsize=4)

        def drain():
            try:
                while line := self.proxy.stdout.readline(8192):
                    try:
                        ready.put_nowait(line)
                    except queue.Full:
                        pass
            except (ValueError, OSError):
                pass

        self._reader = threading.Thread(target=drain, daemon=True)
        self._reader.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            self.check()
            try:
                item = json.loads(ready.get(timeout=0.1))
            except queue.Empty:
                continue
            if item.get("event") == "proxy_ready":
                self.url = BrokerHttp(item["url"]).base_url
                break
        if not self.url:
            raise RuntimeError("Proxy startup timed out")
        http = BrokerHttp(self.url)
        if http.request("GET", "/api/health").require(200).get("instance_id") != self.instance:
            raise RuntimeError("Proxy is connected to a different broker instance")
        http.request("GET", "/api/tasks").require(200)

    def check(self):
        if self.broker and self.broker.process and self.broker.process.poll() is not None:
            raise RuntimeError("Owned Rust service exited")
        if self.proxy and self.proxy.poll() is not None:
            raise RuntimeError("Owned proxy exited; check whether port 4173 is occupied")

    def wait(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.check()
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))

    def play(self, checkpoint=None):
        report = {"instance_id": self.instance, "checks": [], "status": "running"}
        task_id, events, detail, pages = execute_flow(
            BrokerHttp(self.broker.base_url), self._credentials[0], self.directory / "fixture",
            report["checks"], self._credentials, checkpoint=checkpoint,
        )
        report.update(task_id=task_id, final_detail=detail, event_pages=pages, status="passed")
        check_no_sensitive_data([report, events], self._credentials)
        write_json(self.directory / "dashboard_demo.json", report)
        (self.directory / "dashboard_events.jsonl").write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        return report

    def stop(self):
        if self.proxy:
            if self.proxy.poll() is None:
                self.proxy.terminate()
                try:
                    self.proxy.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proxy.kill()
                    self.proxy.wait(timeout=5)
            if self.proxy.stdout:
                self.proxy.stdout.close()
        if self._reader:
            self._reader.join(timeout=2)
        if self.broker:
            self.broker.stop()
        self._credentials.clear()

    def __exit__(self, *_args):
        self.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("dev", "dist"), default="dist")
    parser.add_argument("--server-bin", type=Path, default=default_binary())
    parser.add_argument("--results-dir", type=Path, default=REPO / "results")
    parser.add_argument("--auto", action="store_true", help="play without waiting for terminal input")
    parser.add_argument("--step-delay", type=float, default=3, help="seconds per checkpoint, 0..10")
    parser.add_argument("--hold-seconds", type=float, help="optional finite hold for automated checks")
    args = parser.parse_args()
    if not math.isfinite(args.step_delay) or not 0 <= args.step_delay <= 10 or (args.hold_seconds is not None and (not math.isfinite(args.hold_seconds) or args.hold_seconds < 0)):
        parser.error("Invalid delay/hold duration")
    def interrupt(*_args):
        raise KeyboardInterrupt

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, interrupt)
    try:
        with Dashboard(args.server_bin, args.results_dir.resolve(), args.mode) as dashboard:
            input_queue = queue.Queue(maxsize=1)

            def checkpoint(name, task_id):
                if name == "task_ready":
                    print(json.dumps({"event": "dashboard_ready", "url": dashboard.url, "task_id": task_id, "instance_id": dashboard.instance}), flush=True)
                    print("Open http://127.0.0.1:5173" if args.mode == "dev" else "Open " + dashboard.url, flush=True)
                    if not args.auto:
                        print("Press Enter to play; Ctrl+C stops owned services.", flush=True)
                        def read_input():
                            input_queue.put(sys.stdin.readline())
                        threading.Thread(target=read_input, daemon=True).start()
                        while True:
                            dashboard.check()
                            try:
                                if not input_queue.get(timeout=0.1):
                                    raise RuntimeError("Terminal input closed; use --auto for noninteractive runs")
                                break
                            except queue.Empty:
                                continue
                else:
                    print("Checkpoint: " + name, flush=True)
                    dashboard.wait(args.step_delay)

            dashboard.play(checkpoint)
            print("DASHBOARD_FLOW_PASS; services remain available until stopped", flush=True)
            if args.hold_seconds is None:
                while True:
                    dashboard.wait(1)
            else:
                dashboard.wait(args.hold_seconds)
        return 0
    except KeyboardInterrupt:
        print("Dashboard stopped; owned services cleaned up", flush=True)
        return 0
    except Exception as error:
        print(f"DASHBOARD_FAIL: {type(error).__name__}; check build, port and local configuration", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
