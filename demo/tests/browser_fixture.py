"""Controlled real-Rust fixture for manual browser acceptance, not a product API.

Run python -m demo.tests.browser_fixture --mode dist. Send a single command by
writing seed/play/restart/stop to the printed control file. No HTTP write route
or browser credential is introduced. The default proxy entry remains unchanged.
"""

import argparse
from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlsplit
import uuid

from demo.fixture import prepare_fixture
from demo.runtime import ManagedBroker, REPO, default_binary
from demo.workflow import execute_flow, write_json
from integrations import AdminClient, BrokerHttp
from web_proxy.config import Config
from web_proxy.server import create_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("dev", "dist"), default="dist")
    args = parser.parse_args()
    root = REPO / "results" / ("local-browser-" + uuid.uuid4().hex[:8])
    root.mkdir()
    control = root / "control.txt"
    evidence = deque(maxlen=5000)
    generation = 0
    command = "start"
    try:
        while command != "stop":
            generation += 1
            fixture = root / f"fixture-{generation}"
            prepare_fixture(fixture)
            credentials = [secrets.token_hex(32)]
            with ManagedBroker(default_binary(), fixture, credentials[0]) as broker:
                http = BrokerHttp(broker.base_url)
                instance = http.health().require(200)["instance_id"]
                with create_server(Config(broker.base_url, credentials[0], dist=None if args.mode == "dev" else REPO / "front/dist")) as proxy:
                    original = proxy.RequestHandlerClass.handle_local

                    def instrument(handler):
                        path = urlsplit(handler.path).path
                        evidence.append({"method": handler.command, "api": path.startswith('/api/'), "browser_authorization_present": handler.headers.get('Authorization') is not None, "browser_cookie_present": handler.headers.get('Cookie') is not None})
                        original(handler)

                    proxy.RequestHandlerClass.handle_local = instrument
                    worker = threading.Thread(target=proxy.serve_forever, daemon=True)
                    worker.start()
                    state = {"instance_id": instance, "generation": generation, "tasks": [], "control": str(control), "root": str(root)}
                    write_json(root / "state.json", state)
                    print(json.dumps(state), flush=True)
                    played = False
                    try:
                        while True:
                            if broker.process.poll() is not None:
                                raise RuntimeError("owned broker exited")
                            if not control.exists():
                                time.sleep(0.1)
                                continue
                            command = control.read_text(encoding="utf-8-sig").strip()
                            if command not in {"seed", "play", "restart", "stop"}:
                                time.sleep(0.1)
                                continue
                            control.unlink()
                            if command in {"restart", "stop"}:
                                break
                            if command == "seed":
                                created = AdminClient(http, credentials[0]).create_task('demo').require(201)
                                credentials.append(created['agent_token'])
                                state['tasks'].append(created['task_id'])
                            elif not played:
                                checks = []
                                task, events, detail, pages = execute_flow(http, credentials[0], fixture, checks, credentials)
                                state['tasks'].append(task)
                                state['flow'] = {"checks": len(checks), "events": len(events), "phase": detail['phase'], "pages": pages}
                                played = True
                            write_json(root / "state.json", state)
                    finally:
                        proxy.shutdown()
                        worker.join(timeout=3)
                        leaks = []
                        for path in (REPO / 'front/dist').rglob('*'):
                            if path.is_file() and any(token.encode() in path.read_bytes() for token in credentials):
                                leaks.append(path.name)
                        summary = {"checked_at": datetime.now(timezone.utc).isoformat(), "source": "real browser requests observed at proxy boundary; headers recorded as presence booleans only", "observed_requests": len(evidence), "api_methods": sorted({e['method'] for e in evidence if e['api']}), "browser_authorization_present": any(e['browser_authorization_present'] for e in evidence), "browser_cookie_present": any(e['browser_cookie_present'] for e in evidence), "credential_in_build": bool(leaks)}
                        write_json(root / "credential_check.json", summary)
                        credentials.clear()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
