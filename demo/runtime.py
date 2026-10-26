"""Own one Rust subprocess, discover its ephemeral port, and always stop it."""

from __future__ import annotations

import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

from integrations import BrokerHttp, ClientError

REPO = Path(__file__).resolve().parents[1]


def default_binary() -> Path:
    target = Path(os.environ.get("CARGO_TARGET_DIR", REPO / "target"))
    if not target.is_absolute():
        target = REPO / target
    name = "agentfence-server.exe" if os.name == "nt" else "agentfence-server"
    return target / "release" / name


class ManagedBroker:
    """No process-name matching, arbitrary ports, or changes to a user's service."""

    def __init__(self, binary: Path, fixture: Path, admin_token: str, *, startup_timeout: float = 15):
        self.binary = binary.resolve()
        self.fixture = fixture.resolve()
        self._token = admin_token
        self.startup_timeout = startup_timeout
        self.process: subprocess.Popen | None = None
        self.base_url: str | None = None
        self._reader: threading.Thread | None = None
        self._lines: queue.Queue[str] = queue.Queue(maxsize=32)

    def __enter__(self):
        try:
            self.start()
            return self
        except BaseException:
            self.stop()
            raise

    def start(self):
        if not self.binary.is_file():
            raise RuntimeError("release server binary missing; run cargo build --release --locked -p agentfence-server first")
        env = os.environ.copy()
        env.update({
            "AGENTFENCE_ADMIN_TOKEN": self._token,
            "AGENTFENCE_BIND": "127.0.0.1:0",
            "AGENTFENCE_POLICY": str(self.fixture / "policy.json"),
            "AGENTFENCE_AUDIT": str(self.fixture / "audit.jsonl"),
        })
        self.process = subprocess.Popen(
            [str(self.binary)], cwd=REPO, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        def read_stderr():
            try:
                while line := self.process.stderr.readline(8192):
                    try:
                        self._lines.put_nowait(line.decode("utf-8", errors="replace").strip())
                    except queue.Full:
                        pass  # Drain stderr without unbounded memory or blocking the server.
            except (ValueError, OSError):
                pass

        self._reader = threading.Thread(target=read_stderr, daemon=True)
        self._reader.start()
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            try:
                line = self._lines.get(timeout=0.1)
            except queue.Empty:
                if self.process.poll() is not None:
                    raise RuntimeError(f"server exited before readiness (exit {self.process.returncode})")
                continue
            match = re.fullmatch(r"AgentFence listening on http://127\.0\.0\.1:([0-9]+)", line)
            if match:
                port = int(match.group(1))
                if not 1 <= port <= 65535:
                    raise RuntimeError("server advertised an invalid port")
                self.base_url = f"http://127.0.0.1:{port}"
                break
        if self.base_url is None:
            raise RuntimeError("server readiness timed out")
        http = BrokerHttp(self.base_url, timeout=1)
        while time.monotonic() < deadline:
            try:
                health = http.health().require(200)
                if health.get("status") == "ok" and isinstance(health.get("instance_id"), str):
                    return
            except ClientError:
                pass
            if self.process.poll() is not None:
                raise RuntimeError("server exited during readiness")
            time.sleep(0.05)
        raise RuntimeError("server health did not become ready")

    def stop(self):
        """Only terminate the process created by this instance, then reap it."""
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            if self.process.stderr is not None:
                self.process.stderr.close()
        if self._reader is not None:
            self._reader.join(timeout=2)
        self._token = ""

    def __exit__(self, *_args):
        self.stop()
