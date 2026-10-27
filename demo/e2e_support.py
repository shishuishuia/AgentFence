"""Evidence and process ownership for independent, real-TCP security cases."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from integrations import AdminClient, BrokerHttp
from demo.fixture import BEFORE, AFTER, PRIVATE, prepare_fixture
from demo.runtime import ManagedBroker
from demo.e2e_spec import CASES, rule

ALLOWED = ["src/main.rs", "Cargo.toml", "text.bin", "link-file", "link-dir/file.rs"]
TOKEN_KEYS = {"authorization", "agent_token", "admin_token", "token"}


class CaseFailure(RuntimeError):
    """Only fixed, non-sensitive messages may be raised by assertions."""


class PlatformSkip(RuntimeError):
    """Required OS capability is missing; never count it as a passed case."""


def check(condition, message):
    if not condition:
        raise CaseFailure(message)


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")


def file_state(fixture: Path) -> dict:
    """Inspect only the owned fixture, never follow links/reparse points."""
    result = {}

    def visit(directory):
        for entry in sorted(os.scandir(directory), key=lambda item: item.name):
            path = Path(entry.path)
            relative = path.relative_to(fixture).as_posix()
            if relative in {"audit.jsonl", "policy.json"}:
                continue
            metadata = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
                result[relative] = {"kind": "link"}
            elif stat.S_ISDIR(metadata.st_mode):
                result[relative] = {"kind": "directory"}
                visit(path)
            elif stat.S_ISREG(metadata.st_mode):
                raw = path.read_bytes()
                result[relative] = {"kind": "file", "sha256": digest(raw), "bytes": len(raw)}
            else:
                result[relative] = {"kind": "other"}

    visit(fixture)
    return result


def prepare(directory: Path, case_id: str):
    prepare_fixture(directory)
    workspace = directory / "workspace"
    (workspace / "text.bin").write_bytes(b"\xff\xfe\x00")
    (workspace / "unlisted.txt").write_text("SIMULATED_UNLISTED\n", encoding="utf-8")
    (workspace / "src/main.rs.bak").write_text("SIMULATED_BACKUP\n", encoding="utf-8")
    (directory / "outside-dir").mkdir()
    (directory / "outside-dir/file.rs").write_text(PRIVATE, encoding="utf-8")
    policy = json.loads((directory / "policy.json").read_text(encoding="utf-8"))
    policy["workspaces"]["demo"]["allowed_paths"] = ALLOWED
    (directory / "policy.json").write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
    try:
        if case_id == "D17":
            (workspace / "link-file").symlink_to(directory / "simulated-private.txt")
        if case_id == "D18":
            (workspace / "link-dir").symlink_to(directory / "outside-dir", target_is_directory=True)
    except (OSError, NotImplementedError):
        raise PlatformSkip("OS does not permit the required symbolic link") from None


def sanitize(value, secrets, fixture: Path, redactions=None, prefix=""):
    """Retain response structure while removing credential/content values."""
    if redactions is None:
        redactions = {}
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            location = prefix + "/" + str(key)
            if str(key).lower() in TOKEN_KEYS:
                redactions[location] = {"kind": "credential", "length": len(item) if isinstance(item, str) else None,
                                        "hex256": isinstance(item, str) and re.fullmatch(r"[a-f0-9]{64}", item) is not None}
                result[key] = "[REDACTED]"
            elif str(key).lower() == "content":
                encoded = item.encode("utf-8") if isinstance(item, str) else canonical(item)
                redactions[location] = {"kind": "file_content", "sha256": digest(encoded), "bytes": len(encoded)}
                result[key] = "[REDACTED]"
            else:
                result[key] = sanitize(item, secrets, fixture, redactions, location)
        return result
    if isinstance(value, list):
        return [sanitize(item, secrets, fixture, redactions, prefix + "/" + str(index)) for index, item in enumerate(value)]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        for spelling in {str(fixture), fixture.as_posix()}:
            value = value.replace(spelling, "[FIXTURE]")
        return value
    return value


def safe_evidence(value, secrets):
    """Check actual secret values, and require sensitive keys to be redacted."""
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in TOKEN_KEYS | {"content"}:
                check(item == "[REDACTED]", "unredacted sensitive field in exported evidence")
            safe_evidence(item, secrets)
    elif isinstance(value, list):
        for item in value:
            safe_evidence(item, secrets)
    elif isinstance(value, str):
        check(not any(secret and secret in value for secret in secrets), "credential in exported evidence")
        check(not any(content.strip() in value for content in (BEFORE, AFTER, PRIVATE)), "file content in exported evidence")


def validate_audit(requests: list[dict], events: list[dict]):
    groups = defaultdict(list)
    instances = defaultdict(list)
    for event in events:
        groups[event["request_id"]].append(event)
        instances[event["instance_id"]].append(event)
    for records in instances.values():
        check([e["event_id"] for e in records] == list(range(1, len(records) + 1)), "audit IDs are missing, duplicated or unordered")
    expected_ids = set()
    for request in requests:
        body = request["body"]
        rid = body.get("request_id")
        method, path, status = request["method"], request["path"], request["status"]
        kind = None
        if path == "/v1/tool/execute":
            kind = "result"
        elif 200 <= status < 300:
            if method == "POST" and path == "/v1/admin/tasks":
                kind = "task_created"
            elif method == "POST" and path.endswith("/phase"):
                kind = "phase_changed"
            elif method == "POST" and path.endswith("/grants"):
                kind = "grant_issued"
            elif method == "DELETE" and "/grants/" in path:
                kind = "grant_revoked"
        if kind is None:
            check(not rid or not groups.get(rid), "query or rejected management call unexpectedly changed audit")
            continue
        check(isinstance(rid, str) and rid, "audited response is missing request_id")
        expected_ids.add(rid)
        group = groups[rid]
        results = [e for e in group if e["event_type"] != "intent"]
        intents = [e for e in group if e["event_type"] == "intent"]
        check(len(results) == 1 and results[0]["event_type"] == kind, "response has no unique matching audit result")
        result = results[0]
        check(result["reason_code"] == body.get("reason_code", "OK"), "response and audit reason disagree")
        check(result["instance_id"] == request["instance_id"], "response and audit instance disagree")
        if kind == "result":
            check(result["allowed"] is body.get("allowed") and result["executed"] is body.get("executed"), "response and audit flags disagree")
            check(len(intents) <= 1, "multiple intents for one tool call")
            if status == 200:
                check(len(intents) == 1, "successful tool call has no intent")
            attempted = request.get("request_body") or {}
            if result["tool"] is not None:
                check(result["tool"] == attempted.get("tool") and result["path"] == attempted.get("path"), "audit resource differs from submitted tool request")
                check(result["task_id"] == attempted.get("task_id"), "audit task differs from submitted tool request")
        else:
            check(not intents and result["allowed"] is True and result["executed"] is True, "invalid management audit flags")
        for intent in intents:
            check(intent["event_id"] < result["event_id"] and intent["reason_code"] == "PENDING", "invalid intent ordering")
            check(intent["allowed"] is False and intent["executed"] is False, "intent implies completed execution")
    check(set(groups) == expected_ids, "unmatched or missing audit request")


def csv_rows(cases, all_requests):
    rows = []
    for case in cases:
        expected = rule(case["case_id"])
        requests = all_requests.get(case["case_id"], [])
        measured = [requests[p["request_index"]] for p in case["probes"]]
        ok = case["verdict"] == "passed"
        preconditions = []
        for probe in case["probes"]:
            preconditions.append({"variant": probe["label"], "tasks": [{"task_id": task["task_id"], "phase": task["phase"], "grants": [{key: grant[key] for key in ("grant_id", "path", "status", "remaining_uses", "revoked")} for grant in task["grants"]]} for task in probe["before"]["tasks"].values()]})
        rows.append({"case_id": case["case_id"], "category": "normal" if case["case_id"].startswith("A") else "abnormal", "scenario": CASES[case["case_id"]],
                     "precondition": json.dumps(preconditions, ensure_ascii=False, separators=(",", ":")), "expected_http_status": expected["status"],
                     "actual_http_status": ";".join(str(r["status"]) for r in measured), "expected_reason_code": expected["reason"] if case["case_id"] != "A10" else "not_returned_by_query",
                     "actual_reason_code": ";".join(r["body"].get("reason_code", "OK (audit)" if case["case_id"] != "A10" else "not_returned_by_query") for r in measured),
                     "file_check": "PASS" if ok else case["verdict"].upper(), "lease_check": "PASS" if ok else case["verdict"].upper(),
                     "audit_check": "PASS (applicable events + API/disk equality)" if ok else case["verdict"].upper(),
                     "request_id": ";".join(r["body"].get("request_id", "") for r in measured),
                     "request_id_note": "query contract has no request_id" if case["case_id"] == "A10" else "",
                     "variant_count": len(measured), "verdict": case["verdict"], "pass": "true" if ok else "false", "failure": case.get("failure", "")})
    return rows


class CaseContext:
    def __init__(self, binary, fixture, token):
        self.binary, self.fixture = binary, fixture
        self.secrets = [token]
        self.server = None
        self.requests = []
        self.instances = {}
        self.probes = []
        self.identities = {}
        self.servers = []

    def start(self):
        self.server = ManagedBroker(self.binary, self.fixture, self.secrets[0])
        self.servers.append(self.server)
        self.server.start()
        self.http = BrokerHttp(self.server.base_url)
        self.admin = AdminClient(self.http, self.secrets[0])
        self.instance = self.http.health().require(200)["instance_id"]

    def state(self):
        tasks = {}
        for task in self.admin.tasks(limit=100).require(200)["items"]:
            detail = self.admin.task(task["task_id"]).require(200)
            detail.pop("server_time")
            detail.pop("instance_id")
            tasks[task["task_id"]] = detail
        return {"files": file_state(self.fixture), "tasks": tasks}

    def call(self, label, method, path, body=None, identity="admin"):
        token = self.secrets[0] if identity == "admin" else None if identity is None else identity
        response = self.http.request(method, path, token=token, body=body)
        credential = response.payload.get("agent_token")
        if isinstance(credential, str):
            self.secrets.append(credential)
            self.identities[credential] = response.payload.get("task_id")
        redactions = {}
        safe = sanitize(response.payload, self.secrets, self.fixture, redactions)
        request_redactions = {}
        request_body = sanitize(body, self.secrets, self.fixture, request_redactions)
        role = "admin" if identity == "admin" else "missing" if identity is None else "agent" if token in self.identities else "invalid"
        self.requests.append({"label": label, "method": method, "path": path, "status": response.status,
                              "instance_id": self.instance, "body": safe, "redactions": redactions,
                              "request_body": request_body, "request_redactions": request_redactions,
                              "identity": {"role": role, "bound_task_id": self.identities.get(token)},
                              "response_json_sha256": digest(canonical(response.payload)),
                              "request_json_bytes": len(json.dumps(body, ensure_ascii=False, allow_nan=False).encode()) if body is not None else 0})
        return response

    def create(self):
        body = self.call("setup:create", "POST", "/v1/admin/tasks", {"workspace_id": "demo"}).require(201)
        return body["task_id"], body["agent_token"]

    def phase(self, task, phase):
        return self.call("setup:phase", "POST", f"/v1/admin/tasks/{task[0]}/phase", {"phase": phase}).require(200)

    def grant(self, task, path="src/main.rs", ttl=300):
        return self.call("setup:grant", "POST", f"/v1/admin/tasks/{task[0]}/grants", {"tool": "write_file", "path": path, "ttl_seconds": ttl, "max_uses": 1}).require(201)

    def edit(self, ttl=300):
        task = self.create()
        self.phase(task, "edit")
        return task, self.grant(task, ttl=ttl)

    def tool(self, task, tool="read_file", path="src/main.rs", content=None, grant=None, label="tool"):
        body = {"task_id": task[0], "tool": tool, "path": path}
        if content is not None:
            body["content"] = content
        if grant is not None:
            body["grant_id"] = grant
        return self.call(label, "POST", "/v1/tool/execute", body, task[1])

    def events(self):
        events, cursor = [], None
        for _ in range(100):
            page = self.admin.events(after=cursor, limit=500).require(200)
            check(page["instance_id"] == self.instance, "instance changed unexpectedly")
            events.extend(page["events"])
            if not page["has_more"]:
                return events
            check(page["next_cursor"] != cursor, "audit cursor did not advance")
            cursor = page["next_cursor"]
        raise CaseFailure("audit pagination limit exceeded")

    def restart(self):
        self.instances[self.instance] = self.events()
        self.server.stop()
        self.start()

    def finish(self):
        try:
            if self.server and self.server.process and self.server.process.poll() is None:
                self.instances[self.instance] = self.events()
        finally:
            for server in self.servers:
                server.stop()
        check(self.servers and all(server.process is not None and server.process.poll() is not None for server in self.servers), "owned server was not stopped")
        events = [e for batch in self.instances.values() for e in batch]
        raw = (self.fixture / "audit.jsonl").read_bytes()
        check(raw.endswith(b"\n"), "audit has an incomplete trailing line")
        disk = [json.loads(line) for line in raw.decode("utf-8").splitlines()]
        check(events == disk, "paginated API audit differs from disk")
        safe_events = sanitize(events, self.secrets, self.fixture)
        validate_audit(self.requests, safe_events)
        safe_evidence([self.requests, self.probes, safe_events], self.secrets)
        return safe_events
