"""Run with python -m web_proxy; the Admin Token comes only from environment."""

import argparse
import json
import os
import sys

from .config import Config
from .server import create_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("dev", "dist"), default="dist")
    parser.add_argument("--port", type=int, default=4173, help="local port; default 4173 for Vite, 0 for isolated tests")
    parser.add_argument("--manage-workspaces", action="store_true", help="enable trusted local v2 workspace management")
    parser.add_argument("--mcp-bin", type=str, help="trusted Rust MCP binary; never supplied by browser")
    parser.add_argument("--review-agent", choices=("offline", "deepseek"), default="offline")
    args = parser.parse_args()
    try:
        options = {"dist": None} if args.mode == "dev" else {}
        config = Config(os.environ.get("AGENTFENCE_BROKER_URL", "http://127.0.0.1:8080"), os.environ.get("AGENTFENCE_ADMIN_TOKEN", ""), port=args.port, **options)
        manager = None
        if args.manage_workspaces:
            from .workspaces import WorkspaceBackend, WorkspaceManager
            factory = None
            if args.mcp_bin:
                from integrations.mcp_review import ReviewClient
                model = None
                if args.review_agent == "deepseek":
                    from integrations.deepseek import DeepSeekClient, DeepSeekConfig
                    model = DeepSeekClient(DeepSeekConfig.from_env())
                factory = lambda summary, token, progress: ReviewClient(args.mcp_bin, config.upstream, summary, token, config.token, model=model, progress=progress)
            manager = WorkspaceManager(WorkspaceBackend(config), review_factory=factory)
        with create_server(config, manager=manager) as server:
            print(json.dumps({"event": "proxy_ready", "url": f"http://127.0.0.1:{server.server_port}"}), flush=True)
            try:
                server.serve_forever(poll_interval=0.1)
            except KeyboardInterrupt:
                pass
    except (ValueError, OSError):
        print("Proxy startup failed: check configuration, dist build and port 4173", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
