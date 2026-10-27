"""Start the opt-in interactive demo console on a disposable Rust workspace."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import signal
import threading
import uuid

from .fixture import prepare_fixture
from .live import LiveDemo
from .runtime import ManagedBroker, REPO, default_binary
from .workflow import write_json
from web_proxy.config import Config
from web_proxy.server import create_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("dev", "dist"), default="dev")
    parser.add_argument("--server-bin", type=Path, default=default_binary())
    parser.add_argument("--port", type=int, default=4173)
    parser.add_argument("--step-delay", type=float, default=2.5)
    args = parser.parse_args()
    if not 0 <= args.step_delay <= 10:
        parser.error("step-delay must be finite and between 0 and 10")
    root = REPO / "results" / ("local-live-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    root.mkdir()
    prepare_fixture(root / "fixture")
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        token = secrets.token_hex(32)
        with ManagedBroker(args.server_bin, root / "fixture", token) as broker:
            runner = LiveDemo(broker.base_url, token, root / "fixture", root, step_delay=args.step_delay)
            try:
                config = Config(broker.base_url, token, port=args.port, dist=None if args.mode == "dev" else REPO / "front/dist")
                with create_server(config, demo=runner) as server:
                    thread = threading.Thread(target=server.serve_forever, daemon=True)
                    thread.start()
                    state = {"backend_pid": broker.process.pid, "backend_url": broker.base_url,
                             "instance_id": runner.instance, "proxy_url": f"http://127.0.0.1:{server.server_port}",
                             "stop_file": str(root / "stop"), "mode": args.mode}
                    write_json(root / "runtime.json", state)
                    print(json.dumps({"event": "live_dashboard_ready", **state}), flush=True)
                    try:
                        while not stop.wait(0.2) and not (root / "stop").exists():
                            if broker.process.poll() is not None:
                                raise RuntimeError("owned broker exited")
                    finally:
                        server.shutdown()
                        thread.join(timeout=5)
            finally:
                runner.close()
        return 0
    except Exception as error:
        print(f"LIVE_DASHBOARD_FAIL: {type(error).__name__}; check build, port and fixture", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
