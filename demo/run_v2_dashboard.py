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

from integrations.credentials import CredentialStore
from integrations.mcp_review import ReviewClient, ReviewError
from web_proxy.config import Config
from web_proxy.server import create_server
from web_proxy.workspaces import WorkspaceBackend, WorkspaceManager
from .fixture import prepare_fixture
from .runtime import ManagedBroker, REPO


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin-dir", type=Path, default=REPO / "target/v2/release")
    parser.add_argument("--port", type=int, default=4174)
    parser.add_argument("--dist", type=Path, default=REPO / "front/dist", help="built frontend directory")
    parser.add_argument("--agent", choices=("auto", "offline", "deepseek"), default="auto", help="built-in executor used only by isolated edit tasks")
    parser.add_argument("--harness-entry", type=Path, default=None, help="pinned Harness lib/bin.js; also AGENTFENCE_DSH_ENTRY")
    parser.add_argument("--harness-node", type=Path, default=None, help="absolute node executable; also AGENTFENCE_DSH_NODE")
    parser.add_argument("--harness-home", type=Path, default=None, help="prepared DSH_HOME with an installed acp profile; also AGENTFENCE_DSH_HOME")
    parser.add_argument("--harness-model", default=None, help="Harness model name; also AGENTFENCE_DSH_MODEL")
    parser.add_argument("--harness-timeout", type=float, default=None, help="Harness run timeout seconds; also AGENTFENCE_DSH_TIMEOUT")
    parser.add_argument("--harness-check", action="store_true", help="validate the configured Harness and exit")
    parser.add_argument("--credentials-file", type=Path, default=None, help="per-user file for the Harness model key")
    args = parser.parse_args()
    suffix = ".exe" if os.name == "nt" else ""
    if args.agent == "auto":
        args.agent = "deepseek" if CredentialStore(args.credentials_file).load() else "offline"
    model = None
    if args.agent == "deepseek":
        from integrations.deepseek import DeepSeekClient, DeepSeekConfig
        config = DeepSeekConfig.from_env()
        if not config.key:
            stored = CredentialStore(args.credentials_file).load()
            if not stored:
                parser.error("set DEEPSEEK_API_KEY or save a model credential in the workspaces page first")
            config = DeepSeekConfig(stored, config.model)
        model = DeepSeekClient(config)
    mcp = args.bin_dir / ("agentfence-mcp-server" + suffix)
    if not mcp.is_file():
        raise ValueError("missing MCP binary")
    harness = None
    entry = args.harness_entry or os.environ.get("AGENTFENCE_DSH_ENTRY")
    if entry:
        from integrations.external_review import ExternalReview, HarnessConfig, preflight
        try:
            harness = HarnessConfig.from_environment(entry=entry, node=args.harness_node, mcp_binary=mcp, home=args.harness_home, model=args.harness_model, timeout=args.harness_timeout)
        except ValueError as error:
            parser.error(str(error))
        if args.harness_check:
            try:
                state = preflight(harness)
            except ReviewError as error:
                parser.error(str(error))
            print(json.dumps({"event": "harness_ready", **state}, ensure_ascii=False), flush=True)
            return 0
    elif args.harness_check:
        parser.error("--harness-check requires --harness-entry or AGENTFENCE_DSH_ENTRY")
    credentials = CredentialStore(args.credentials_file) if harness is not None or args.agent == "deepseek" else None
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
            config = Config(broker.base_url, token, port=args.port, dist=args.dist)
            factory = lambda summary, task_token, progress: ReviewClient(mcp, broker.base_url, summary, task_token, token, model=model, progress=progress)
            external = None
            if harness is not None:
                external = lambda summary, task_token, progress: ExternalReview(harness, broker.base_url, summary, task_token, token, progress=progress, api_key=credentials.load())
            manager = WorkspaceManager(WorkspaceBackend(config), review_factory=factory, external_review_factory=external, credential_store=credentials, builtin_mode=args.agent)
            with create_server(config, manager=manager) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                state = {"event": "v2_dashboard_ready", "url": f"http://127.0.0.1:{server.server_port}/workspaces", "agent": args.agent, "harness": harness is not None, "stop_file": str(root / "stop")}
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
