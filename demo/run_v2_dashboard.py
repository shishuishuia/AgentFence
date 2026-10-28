"""Own an isolated v2 Broker, native picker helper and built Vue console."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import signal
import threading
import uuid

from integrations.mcp_review import ReviewClient
from web_proxy.config import Config
from web_proxy.server import create_server
from web_proxy.workspaces import WorkspaceBackend, WorkspaceManager
from .fixture import prepare_fixture
from .runtime import ManagedBroker, REPO


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", type=Path, default=REPO / "target/v2/release")
    parser.add_argument("--port", type=int, default=4174)
    parser.add_argument("--agent", choices=("offline", "deepseek"), default="offline")
    args = parser.parse_args()
    suffix = ".exe" if os.name == "nt" else ""
    model = None
    if args.agent == "deepseek":
        from integrations.deepseek import DeepSeekClient, DeepSeekConfig
        config = DeepSeekConfig.from_env()
        if not config.key:
            parser.error("set DEEPSEEK_API_KEY in the trusted server environment first")
        model = DeepSeekClient(config)
    root = REPO / "results" / ("local-v2-dashboard-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    root.mkdir()
    prepare_fixture(root / "fixture")
    token = secrets.token_hex(32)
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        with ManagedBroker(args.bin_dir / ("agentfence-server" + suffix), root / "fixture", token) as broker:
            config = Config(broker.base_url, token, port=args.port)
            mcp = args.bin_dir / ("agentfence-mcp-server" + suffix)
            if not mcp.is_file():
                raise ValueError("missing MCP binary")
            factory = lambda summary, task_token, progress: ReviewClient(mcp, broker.base_url, summary, task_token, token, model=model, progress=progress)
            manager = WorkspaceManager(WorkspaceBackend(config), review_factory=factory)
            with create_server(config, manager=manager) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                state = {"event": "v2_dashboard_ready", "url": f"http://127.0.0.1:{server.server_port}/workspaces", "agent": args.agent, "stop_file": str(root / "stop")}
                (root / "runtime.json").write_text(json.dumps(state), encoding="utf-8")
                print(json.dumps(state), flush=True)
                try:
                    while not stop.wait(.2) and not (root / "stop").exists():
                        if broker.process.poll() is not None:
                            raise RuntimeError("owned Broker exited")
                finally:
                    server.shutdown(); thread.join(5)
        return 0
    except Exception as error:
        print("V2_DASHBOARD_FAIL " + type(error).__name__ + "; check dependencies, binaries, built front/dist and port", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
