"""B's independent two-project HTTP management + real MCP acceptance replay.

The OS chooser is replaced by a deterministic selection of generated fixtures;
native dialog appearance and online model quality are separate checks.
"""

import argparse
import asyncio
from datetime import datetime, timezone
from http.client import HTTPConnection
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import threading
import time
import uuid

from integrations.client import AdminClient, AgentClient, BrokerHttp
from integrations.mcp_review import ReviewClient
from web_proxy.config import Config
from web_proxy.server import create_server
from web_proxy.workspaces import WorkspaceBackend, WorkspaceManager
from .fixture import prepare_fixture
from .manifest import create_manifest
from .runtime import ManagedBroker, REPO


def digest_tree(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


def run(server_bin, mcp_bin, output, *, model=None):
    output.mkdir(parents=True, exist_ok=False)
    prepare_fixture(output / "fixture")
    projects = []
    for name in ("v2-project-a", "v2-project-b"):
        path = output / name
        shutil.copytree(REPO / "demo/fixtures" / name, path)
        (path / ".env").write_text("SIMULATED_TEST_DATA_ONLY=1\n", encoding="utf-8")
        projects.append(path)
    admin_token = secrets.token_hex(32)
    evidence = {"mode": "model_review" if model else "offline_mcp_replay", "native_dialog": "deterministic selection double; native GUI must be checked separately", "projects": [], "negative_cases": []}
    with ManagedBroker(server_bin, output / "fixture", admin_token) as broker:
        config = Config(broker.base_url, admin_token, port=0, dist=None, timeout=10)
        backend = WorkspaceBackend(config)
        selected = iter(projects)
        manager = WorkspaceManager(backend, picker=lambda: str(next(selected).resolve()))
        server = create_server(config, manager=manager)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        cookie = csrf = ""

        def request(path, body=None, headers=None):
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=15)
            options = {"Origin": origin, "Content-Type": "application/json", "Cookie": cookie, "X-AgentFence-CSRF": csrf}
            options.update(headers or {})
            connection.request("GET" if body is None else "POST", "/api/admin" + path,
                               None if body is None else json.dumps(body).encode(), options)
            response = connection.getresponse()
            result = response.status, dict(response.getheaders()), json.loads(response.read())
            connection.close()
            return result

        def denied(name, status, value, expected):
            if status != expected:
                raise RuntimeError("negative case failed: " + name)
            evidence["negative_cases"].append({"case": name, "status": status, "reason_code": value.get("reason_code"), "request_id": value.get("request_id")})

        try:
            status, headers, value = request("/session", {})
            if status != 200:
                raise RuntimeError("session failed")
            cookie, csrf = headers["Set-Cookie"].split(";")[0], value["csrf_token"]
            for label, route, body, override, expected in (
                ("foreign-origin", "/picker/start", {}, {"Origin": "https://evil.example"}, 403),
                ("bad-csrf", "/picker/start", {}, {"X-AgentFence-CSRF": "wrong"}, 403),
                ("arbitrary-root", "/picker/start", {"root": "/"}, {}, 400),
                ("unbound-session", "/workspaces", None, {"Cookie": "agentfence_admin=unknown"}, 401),
                ("editable-profile", "/tasks", {"workspace_id": "ws_test", "goal": "review", "permission_profile": "controlled_edit"}, {}, 400),
                ("browser-token-injection", "/tasks", {"workspace_id": "ws_test", "goal": "review", "permission_profile": "readonly", "agent_token": "test-only"}, {}, 400),
            ):
                status, _, value = request(route, body, override)
                denied(label, status, value, expected)
            for project in projects:
                before = digest_tree(project)
                status, _, value = request("/picker/start", {})
                if status != 202:
                    raise RuntimeError("picker failed")
                selection = value["selection_id"]
                for _ in range(150):
                    status, _, preview = request("/picker/" + selection)
                    if preview["state"] != "pending":
                        break
                    time.sleep(.02)
                if preview["state"] != "ready" or preview["preview"]["allowed_file_count"] < 3:
                    raise RuntimeError("preview failed")
                status, _, value = request("/picker/" + selection, headers={"Cookie": "agentfence_admin=unknown"})
                denied(project.name + ":selection-session", status, value, 401)
                status, _, workspace = request("/workspaces/confirm", {"selection_id": selection, "display_name": project.name, "profile": "readonly_snapshot"})
                if status != 201:
                    raise RuntimeError("import failed")
                status, _, value = request("/workspaces/confirm", {"selection_id": selection, "display_name": project.name, "profile": "readonly_snapshot"})
                denied(project.name + ":selection-replay", status, value, 409)
                status, _, public_task = request("/tasks", {"workspace_id": workspace["workspace_id"], "goal": "检查代码问题，引用文件路径和行号，提出修改建议。", "permission_profile": "readonly"})
                if status != 201 or "agent_token" in public_task:
                    raise RuntimeError("task creation or credential boundary failed")
                private_task = manager.tasks[public_task["task_id"]]
                agent = AgentClient(BrokerHttp(broker.base_url), public_task["task_id"], private_task["token"])
                admin = AdminClient(BrokerHttp(broker.base_url), admin_token)
                for label, response in (
                    ("readonly-write", agent.write_file("src/main.rs", "blocked")),
                    ("readonly-edit", admin.advance(public_task["task_id"], "edit")),
                    ("readonly-grant", admin.issue_grant(public_task["task_id"], "src/main.rs")),
                    ("path-traversal", agent.read_file("../private.txt")),
                    ("absolute-path", agent.read_file(str(project / "README.md"))),
                    ("excluded-env", agent.read_file(".env")),
                ):
                    denied(project.name + ":" + label, response.status, response.payload, 403)
                result = asyncio.run(ReviewClient(mcp_bin, broker.base_url, public_task, private_task["token"], admin_token, model=model).run())
                if digest_tree(project) != before:
                    raise RuntimeError("source project changed")
                if len(result["files"]) < 3:
                    raise RuntimeError("three files not read")
                events = admin.events(task_id=public_task["task_id"], limit=500).require(200)["events"]
                for negative in evidence["negative_cases"][-6:]:
                    if not any(e["event_type"] == "result" and e["request_id"] == negative["request_id"] and not e["allowed"] and not e["executed"] for e in events):
                        raise RuntimeError("denied call missing audit")
                evidence["projects"].append({"workspace": workspace, "task": public_task, "review": result, "source_hashes": before, "events": events})
            manifest = create_manifest(server_bin, output / "fixture", output.name)
            manifest["mcp_binary_sha256"] = hashlib.sha256(mcp_bin.read_bytes()).hexdigest()
            (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            (output / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            server.shutdown(); server.server_close(); thread.join(5)
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    suffix = ".exe" if os.name == "nt" else ""
    parser.add_argument("--bin-dir", type=Path, default=REPO / "target/v2/release")
    parser.add_argument("--model", action="store_true", help="use configured DeepSeek; paid request, no key on CLI")
    args = parser.parse_args()
    model = None
    if args.model:
        from integrations.deepseek import DeepSeekClient, DeepSeekConfig
        model = DeepSeekClient(DeepSeekConfig.from_env())
    output = REPO / "results" / ("local-v2-b-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    try:
        run(args.bin_dir / ("agentfence-server" + suffix), args.bin_dir / ("agentfence-mcp-server" + suffix), output, model=model)
        print("V2_B_ACCEPTANCE_PASS " + str(output))
        return 0
    except Exception as error:
        print("V2_B_ACCEPTANCE_FAIL " + type(error).__name__ + "; inspect configuration and tests; no automatic retry")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
