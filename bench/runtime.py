"""Own one fixed-fixture experiment process and parse its stdout readiness JSON."""

import json
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

from demo.runtime import REPO
from integrations import BrokerHttp
from bench.stats import check, BenchError


def default_binary():
    target = Path(os.environ.get("CARGO_TARGET_DIR", REPO / "target"))
    if not target.is_absolute():
        target = REPO / target
    return target / "release/examples" / ("http_experiment.exe" if os.name == "nt" else "http_experiment")


class Experiment:
    def __init__(self, binary, directory, mode, token):
        self.binary, self.directory, self.mode = binary.resolve(), directory.resolve(), mode
        self._token = token
        self.process = None
        self.readers = []
        self.base_url = None

    def __enter__(self):
        try:
            self.start()
            return self
        except BaseException:
            self.stop()
            raise

    def start(self):
        check(self.mode in {"enforced", "baseline"}, "invalid experiment mode")
        check(self.binary.is_file(), "build the release http_experiment example first")
        check(not self.directory.exists() and self.directory.parent.is_dir(), "experiment requires a new directory and an existing parent")
        env = os.environ.copy()
        env["AGENTFENCE_ADMIN_TOKEN"] = self._token
        self.process = subprocess.Popen([str(self.binary), self.mode, str(self.directory), "127.0.0.1:0"], cwd=REPO, env=env,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        ready = queue.Queue(maxsize=4)

        def drain(stream, capture):
            try:
                while line := stream.readline(8193):
                    if capture:
                        try:
                            ready.put_nowait(line)
                        except queue.Full:
                            pass
            except (ValueError, OSError):
                pass

        for stream, capture in ((self.process.stdout, True), (self.process.stderr, False)):
            reader = threading.Thread(target=drain, args=(stream, capture), daemon=True)
            reader.start()
            self.readers.append(reader)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise BenchError("experiment exited before readiness")
            try:
                raw = ready.get(timeout=0.1)
            except queue.Empty:
                continue
            check(len(raw) <= 8192, "invalid experiment readiness size")
            value = json.loads(raw.decode("utf-8"))
            check(value.get("event") == "experiment_ready" and value.get("mode") == self.mode, "unexpected experiment readiness")
            check(isinstance(value.get("base_url"), str) and re.fullmatch(r"http://127\.0\.0\.1:[0-9]+", value["base_url"]), "unexpected experiment address")
            check(os.path.samefile(value["directory"], self.directory), "experiment advertised a different fixture")
            self.base_url = BrokerHttp(value["base_url"]).base_url
            health = BrokerHttp(self.base_url).health().require(200)
            check(health.get("status") == "ok" and isinstance(health.get("instance_id"), str), "experiment is unhealthy")
            self.instance_id = health["instance_id"]
            return
        raise BenchError("experiment readiness timed out")

    def stop(self):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            for stream in (self.process.stdout, self.process.stderr):
                stream.close()
        for reader in self.readers:
            reader.join(timeout=2)
        self._token = ""

    def __exit__(self, *_args):
        self.stop()
