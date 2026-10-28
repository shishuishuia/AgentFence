"""Trusted local management boundary; ordinary Agent tools never use this module.

Only an OS dialog supplies a root. Rust owns scanning, manifest validation and
snapshot creation. A selection is session-bound, expires, and is consumed before
the mutating request (including uncertain timeouts). No automatic retries.
"""

from http.cookies import SimpleCookie, CookieError
import json
import os
import re
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time

from integrations.client import BrokerHttp, ClientError

COOKIE = "agentfence_admin"
SESSION_TTL = 1800
SELECTION_TTL = 300
MAX_SESSIONS = 32


class ManagementError(Exception):
    def __init__(self, status, reason, message):
        self.status, self.reason, self.message = status, reason, message


def invalid_response():
    return ManagementError(502, "BROKER_INVALID_RESPONSE", "Rust 工作区接口返回格式不符合 v2 契约。")


def native_picker():
    """A separate main-thread GUI with no administrator/model secrets in its env."""
    env = {k: v for k, v in os.environ.items() if not any(
        marker in k.upper() for marker in ("TOKEN", "SECRET", "API_KEY", "PASSWORD"))}
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    try:
        result = subprocess.run([sys.executable, "-m", "web_proxy.picker_dialog"],
                                cwd=Path(__file__).resolve().parents[1], env=env,
                                capture_output=True, timeout=180, check=True, **options)
        if len(result.stdout) > 32768:
            raise ValueError
        value = json.loads(result.stdout)
        path = value["path"]
        if path is not None and (not isinstance(path, str) or not Path(path).is_absolute()):
            raise ValueError
        return path
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        raise ManagementError(503, "PICKER_UNAVAILABLE", "本机目录选择器不可用或已超时，请检查图形环境。") from None


def preview_shape(value):
    """Allow-list responses, so credentials and internal errors cannot leak."""
    if not isinstance(value, dict) or not all(isinstance(value.get(k), str) for k in
            ("preview_id", "manifest_sha256", "expires_at")):
        raise invalid_response()
    for key in ("allowed_file_count", "excluded_file_count", "total_bytes"):
        if type(value.get(key)) is not int or value[key] < 0:
            raise invalid_response()
    if not 1 <= value["allowed_file_count"] <= 1000 or value["total_bytes"] > 20 * 1024 * 1024:
        raise invalid_response()
    excluded = value.get("excluded", [])
    if not isinstance(excluded, list) or len(excluded) > 1000 or any(
            not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ("path", "reason_code"))
            for item in excluded):
        raise invalid_response()
    return {**{k: value[k] for k in ("preview_id", "manifest_sha256", "expires_at",
             "allowed_file_count", "excluded_file_count", "total_bytes")},
            "excluded": [{"path": x["path"], "reason_code": x["reason_code"]} for x in excluded]}


def workspace_shape(value):
    if not isinstance(value, dict) or not all(isinstance(value.get(k), str) for k in
            ("workspace_id", "snapshot_id", "display_name", "imported_at", "manifest_sha256")):
        raise invalid_response()
    if value.get("import_mode") != "readonly_snapshot":
        raise invalid_response()
    if any(type(value.get(k)) is not int or value[k] < 0 for k in
           ("allowed_file_count", "excluded_file_count", "total_bytes")):
        raise invalid_response()
    return {k: value[k] for k in ("workspace_id", "snapshot_id", "display_name", "imported_at",
            "manifest_sha256", "import_mode", "allowed_file_count", "excluded_file_count", "total_bytes")}


class WorkspaceBackend:
    """Admin-only Rust contract. This adapter never scans or reads source files."""
    def __init__(self, config):
        self.config = config

    def request(self, method, route, body=None):
        try:
            response = BrokerHttp(self.config.upstream, timeout=self.config.timeout).request(
                method, route, token=self.config.token, body=body)
        except ClientError:
            raise ManagementError(502, "BROKER_UNAVAILABLE", "Rust v2 管理接口不可用；操作未自动重试。") from None
        if not response.ok:
            if response.status in (404, 405):
                raise ManagementError(503, "V2_BACKEND_REQUIRED", "当前 Rust 服务尚未提供 v2 工作区接口。")
            reason = response.payload.get("reason_code")
            if reason not in {"SNAPSHOT_CHANGED", "PREVIEW_EXPIRED", "PAYLOAD_TOO_LARGE", "PATH_DENIED",
                              "READONLY_TASK", "INVALID_REQUEST", "AUDIT_UNAVAILABLE"}:
                reason = "BROKER_REJECTED"
            raise ManagementError(response.status, reason, "Rust 拒绝了管理操作，请检查预览、限制或服务状态。")
        return response.payload

    def preview(self, root):
        return preview_shape(self.request("POST", "/v1/admin/workspaces/preview", {"source_path": root}))

    def confirm(self, preview_id, display_name):
        return workspace_shape(self.request("POST", "/v1/admin/workspaces", {
            "preview_id": preview_id, "display_name": display_name, "profile": "readonly_snapshot"}))

    def workspaces(self):
        value = self.request("GET", "/v1/admin/workspaces")
        if not isinstance(value.get("items"), list):
            raise invalid_response()
        return {"items": [workspace_shape(item) for item in value["items"]]}

    def create_readonly_task(self, workspace_id, goal):
        value = self.request("POST", "/v1/admin/tasks", {
            "workspace_id": workspace_id, "goal": goal, "permission_profile": "readonly"})
        if not all(isinstance(value.get(k), str) for k in
                   ("task_id", "workspace_id", "snapshot_id", "goal", "agent_token", "created_at", "updated_at", "instance_id")):
            raise invalid_response()
        if value.get("permission_profile") != "readonly" or value.get("phase") != "inspect" or value["workspace_id"] != workspace_id or value["goal"] != goal or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value["task_id"]) or not 32 <= len(value["agent_token"]) <= 256 or any(not 33 <= ord(c) <= 126 for c in value["agent_token"]):
            raise invalid_response()
        return value


class WorkspaceManager:
    """Bounded sessions and one-use dialog results; all shared state is locked."""
    def __init__(self, backend, *, picker=native_picker, clock=time.monotonic):
        self.backend, self.picker, self.clock = backend, picker, clock
        self.lock = threading.Lock()
        self.sessions = {}
        self.jobs = {}
        self.tasks = {}  # Task credentials remain in this trusted process only.
        self.dialog_busy = False

    def _prune(self):
        now = self.clock()
        self.sessions = {k: v for k, v in self.sessions.items() if v["expiry"] > now}
        self.jobs = {k: v for k, v in self.jobs.items()
                     if v["expiry"] > now and v["session"] in self.sessions}

    def session(self, cookie=""):
        with self.lock:
            self._prune()
            try:
                parsed = SimpleCookie()
                parsed.load(cookie)
                sid = parsed[COOKIE].value
                if sid in self.sessions:
                    return sid, self.sessions[sid]["csrf"]
            except (KeyError, CookieError):
                pass
            if len(self.sessions) >= MAX_SESSIONS:
                raise ManagementError(429, "SESSION_LIMIT", "管理会话过多，请稍后重试。")
            sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self.sessions[sid] = {"csrf": csrf, "expiry": self.clock() + SESSION_TTL}
            return sid, csrf

    def authenticate(self, cookie, csrf=None):
        try:
            # Duplicate session cookies are ambiguous and fail closed.
            if sum(x.strip().startswith(COOKIE + "=") for x in cookie.split(";")) != 1:
                raise ValueError
            parsed = SimpleCookie()
            parsed.load(cookie)
            sid = parsed[COOKIE].value
        except (CookieError, KeyError, ValueError):
            raise ManagementError(401, "SESSION_REQUIRED", "请重新打开管理页面建立会话。") from None
        with self.lock:
            self._prune()
            session = self.sessions.get(sid)
            if session is None:
                raise ManagementError(401, "SESSION_EXPIRED", "管理会话已过期，请刷新页面。")
            if csrf is not None and not secrets.compare_digest(session["csrf"], csrf):
                raise ManagementError(403, "CSRF_DENIED", "管理请求校验失败。")
        return sid

    def start(self, sid):
        with self.lock:
            self._prune()
            if self.dialog_busy:
                raise ManagementError(409, "PICKER_BUSY", "目录选择器已打开，请先完成或取消。")
            if len(self.jobs) >= 128:
                raise ManagementError(429, "SELECTION_LIMIT", "目录选择记录过多，请稍后再试。")
            if sid not in self.sessions:
                raise ManagementError(401, "SESSION_EXPIRED", "管理会话已过期。")
            self.dialog_busy = True
            selection = "sel_" + secrets.token_hex(16)
            self.jobs[selection] = {"session": sid, "state": "pending", "expiry": self.clock() + SELECTION_TTL}
        threading.Thread(target=self._choose, args=(selection,), daemon=True).start()
        return {"selection_id": selection, "state": "pending"}

    def _choose(self, selection):
        try:
            root = self.picker()
            if root:
                preview = self.backend.preview(root)
                result = {"state": "ready", "display_path": root, "preview": preview}
            else:
                result = {"state": "cancelled"}
        except ManagementError as error:
            result = {"state": "failed", "reason_code": error.reason, "message": error.message}
        except Exception:
            result = {"state": "failed", "reason_code": "PICKER_FAILED", "message": "目录选择或预览失败。"}
        finally:
            with self.lock:
                self.dialog_busy = False
        with self.lock:
            if selection in self.jobs:
                self.jobs[selection].update(result)

    def status(self, sid, selection):
        with self.lock:
            self._prune()
            job = self.jobs.get(selection)
            if job is None or job["session"] != sid:
                raise ManagementError(404, "SELECTION_EXPIRED", "目录选择不存在或已过期，请重新选择。")
            return {"selection_id": selection, **{k: v for k, v in job.items() if k not in ("session", "expiry")}}

    def confirm(self, sid, selection, display_name):
        with self.lock:
            self._prune()
            job = self.jobs.get(selection)
            if job is None or job["session"] != sid:
                raise ManagementError(404, "SELECTION_EXPIRED", "目录选择不存在或已过期。")
            if job["state"] != "ready":
                raise ManagementError(409, "SELECTION_CONSUMED", "当前选择尚未就绪或已经确认，请重新选择。")
            job["state"] = "consumed"
            preview_id = job.pop("preview")["preview_id"]
        # Consume before I/O: an uncertain outcome must never be retried with this selection.
        return self.backend.confirm(preview_id, display_name)

    def create_task(self, workspace_id, goal):
        with self.lock:
            if len(self.tasks) >= 128:
                raise ManagementError(429, "TASK_LIMIT", "本次服务的任务数量已达上限，请重启后再创建。")
            # Reserve capacity while the HTTP worker is in flight.
            reservation = "pending_" + secrets.token_hex(16)
            self.tasks[reservation] = None
        try:
            value = self.backend.create_readonly_task(workspace_id, goal)
            clean = {k: value[k] for k in ("task_id", "workspace_id", "snapshot_id", "goal",
                    "permission_profile", "phase", "created_at", "updated_at", "instance_id")}
            with self.lock:
                self.tasks[value["task_id"]] = {"token": value["agent_token"], "summary": clean}
            return clean
        finally:
            with self.lock:
                self.tasks.pop(reservation, None)
