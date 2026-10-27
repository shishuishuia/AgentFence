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
    args = parser.parse_args()
    try:
        options = {"dist": None} if args.mode == "dev" else {}
        config = Config(os.environ.get("AGENTFENCE_BROKER_URL", "http://127.0.0.1:8080"), os.environ.get("AGENTFENCE_ADMIN_TOKEN", ""), port=args.port, **options)
        with create_server(config) as server:
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
