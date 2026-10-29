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


class NativePicker:
    """Own one native-dialog process, including cleanup on helper shutdown."""
    def __init__(self):
        self.lock = threading.Lock()
        self.process = None
        self.closed = False

    def __call__(self):
        env = {k: v for k, v in os.environ.items() if k.upper() in {
            "SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "HOME", "USERPROFILE",
            "SYSTEMDRIVE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
            "PROGRAMFILES", "PROGRAMFILES(X86)", "COMMONPROGRAMFILES", "COMMONPROGRAMFILES(X86)",
            "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "TCL_LIBRARY", "TK_LIBRARY"}}
        try:
            with self.lock:
                if self.closed:
                    raise ValueError
                process = subprocess.Popen([sys.executable, "-m", "web_proxy.picker_dialog"],
                    cwd=Path(__file__).resolve().parents[1], env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.process = process
            raw, _ = process.communicate(timeout=180)
            if process.returncode or len(raw) > 32768:
                raise ValueError
            value = json.loads(raw)
            path = value["path"]
            if path is not None and (not isinstance(path, str) or not Path(path).is_absolute()):
                raise ValueError
            return path
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            self._stop_process()
            raise ManagementError(503, "PICKER_UNAVAILABLE", "本机目录选择器不可用或已超时，请检查图形环境。") from None
        finally:
            with self.lock:
                self.process = None

    def close(self):
        with self.lock:
            self.closed = True
        self._stop_process()

    def _stop_process(self):
        with self.lock:
            process = self.process
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=2)


def native_picker():
    return NativePicker()()


def preview_shape(value):
    """Allow-list responses, so credentials and internal errors cannot leak."""
    if not isinstance(value, dict) or not all(isinstance(value.get(k), str) for k in
            ("preview_id", "manifest_sha256", "expires_at")):
        raise invalid_response()
    for key in ("allowed_file_count", "excluded_entry_count", "total_bytes"):
        if type(value.get(key)) is not int or value[key] < 0:
            raise invalid_response()
    if not 1 <= value["allowed_file_count"] <= 1000 or value["total_bytes"] > 20 * 1024 * 1024 or value["excluded_entry_count"] > 20000:
        raise invalid_response()
    excluded = value.get("excluded", [])
    if not isinstance(excluded, list) or len(excluded) > 200 or any(
            not isinstance(item, dict) or not all(isinstance(item.get(k), str) for k in ("path", "reason"))
            for item in excluded):
        raise invalid_response()
    files = value.get("files")
    if not isinstance(files, list) or len(files) != value["allowed_file_count"] or any(
            not isinstance(f, dict) or not isinstance(f.get("path"), str) or not isinstance(f.get("sha256"), str)
            or type(f.get("bytes")) is not int or not 0 <= f["bytes"] <= 65536 for f in files):
        raise invalid_response()
    return {**{k: value[k] for k in ("preview_id", "manifest_sha256", "expires_at",
             "allowed_file_count", "total_bytes")}, "excluded_file_count": value["excluded_entry_count"],
            "files": [{k: f[k] for k in ("path", "bytes", "sha256")} for f in files],
            "excluded": [{"path": x["path"], "reason_code": x["reason"]} for x in excluded]}


def workspace_shape(value):
    if not isinstance(value, dict) or not all(isinstance(value.get(k), str) for k in
            ("workspace_id", "snapshot_id", "display_name", "imported_at", "manifest_sha256")):
        raise invalid_response()
    if value.get("import_mode") != "readonly_snapshot":
        raise invalid_response()
    if any(type(value.get(k)) is not int or value[k] < 0 for k in
           ("allowed_file_count", "excluded_entry_count", "total_bytes")):
        raise invalid_response()
    return {**{k: value[k] for k in ("workspace_id", "snapshot_id", "display_name", "imported_at",
            "manifest_sha256", "import_mode", "allowed_file_count", "total_bytes")},
            "excluded_file_count": value["excluded_entry_count"]}


class WorkspaceBackend:
    """Admin-only Rust contract. This adapter never scans or reads source files."""
    def __init__(self, config):
        self.config = config

    def request(self, method, route, body=None):
        try:
            limit = 2 * 1024 * 1024 if (route in ("/v1/admin/workspace-previews", "/v1/admin/workspaces") and method == "POST") or (method == "GET" and route.endswith("/changes")) else 1024 * 1024
            response = BrokerHttp(self.config.upstream, timeout=max(self.config.timeout, 10), max_response_bytes=limit).request(
                method, route, token=self.config.token, body=body)
        except ClientError:
            raise ManagementError(502, "BROKER_UNAVAILABLE", "Rust v2 管理接口不可用；操作未自动重试。") from None
        if not response.ok:
            if response.status in (404, 405):
                raise ManagementError(503, "V2_BACKEND_REQUIRED", "当前 Rust 服务尚未提供 v2 工作区接口。")
            reason = response.payload.get("reason_code")
            if reason not in {"SNAPSHOT_CHANGED", "PREVIEW_EXPIRED", "PAYLOAD_TOO_LARGE", "PATH_DENIED",
                              "READONLY_TASK", "INVALID_REQUEST", "AUDIT_UNAVAILABLE", "IMPORT_LIMIT_EXCEEDED"}:
                reason = "BROKER_REJECTED"
            message = "原文件或隔离副本已变化，未应用修改；请重新检查。" if reason == "SNAPSHOT_CHANGED" else "Rust 拒绝了管理操作，请检查预览、限制或服务状态。"
            raise ManagementError(response.status, reason, message)
        return response.payload

    def preview(self, root):
        return preview_shape(self.request("POST", "/v1/admin/workspace-previews", {"source_path": root}))

    def exclusions(self, preview_id, offset):
        value = self.request("GET", f"/v1/admin/workspace-previews/{preview_id}/excluded?offset={offset}&limit=200")
        items, count, next_offset = value.get("excluded"), value.get("excluded_entry_count"), value.get("next_offset")
        if not isinstance(items, list) or len(items) > 200 or type(count) is not int or not 0 <= count <= 20000 or (next_offset is not None and (type(next_offset) is not int or next_offset <= offset or next_offset > count)) or any(not isinstance(x, dict) or not isinstance(x.get("path"), str) or not isinstance(x.get("reason"), str) for x in items):
            raise invalid_response()
        return {"items": [{"path": x["path"], "reason_code": x["reason"]} for x in items], "next_offset": next_offset}

    def confirm(self, preview_id, display_name):
        return workspace_shape(self.request("POST", "/v1/admin/workspaces", {
            "preview_id": preview_id, "display_name": display_name, "profile": "readonly_snapshot"}))

    def workspaces(self):
        value = self.request("GET", "/v1/admin/workspaces")
        if not isinstance(value.get("items"), list) or len(value["items"]) > 32 or any(not isinstance(x, dict) for x in value["items"]):
            raise invalid_response()
        return {"items": [workspace_shape(item) for item in value["items"] if item.get("import_mode") == "readonly_snapshot"]}

    def create_edit_task(self, workspace_id, goal):
        copy = self.request("POST", f"/v1/admin/workspaces/{workspace_id}/edit-copy")
        copy_id = copy.get("workspace_id")
        if copy.get("import_mode") != "editable_copy" or not isinstance(copy_id, str) or copy_id == workspace_id or not re.fullmatch(r"ws_[a-f0-9]{32}", copy_id):
            raise invalid_response()
        value = self.request("POST", "/v1/admin/tasks", {"workspace_id": copy_id, "goal": goal, "permission_profile": "controlled_edit"})
        if not all(isinstance(value.get(k), str) for k in ("task_id", "workspace_id", "snapshot_id", "goal", "agent_token", "created_at", "updated_at", "instance_id")) or value.get("permission_profile") != "controlled_edit" or value.get("workspace_id") != copy_id or value.get("phase") != "inspect" or value.get("goal") != goal or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value["task_id"]) or not 32 <= len(value["agent_token"]) <= 256 or any(not 33 <= ord(c) <= 126 for c in value["agent_token"]):
            raise invalid_response()
        return value

    def edit_changes(self, task_id):
        return self.request("GET", f"/v1/admin/tasks/{task_id}/changes")

    def edit_change(self, task_id, path):
        from urllib.parse import urlencode
        return self.request("GET", f"/v1/admin/tasks/{task_id}/change?" + urlencode({"path": path}))

    def apply_edit(self, task_id, path, after_sha256):
        return self.request("POST", f"/v1/admin/tasks/{task_id}/apply", {"path": path, "after_sha256": after_sha256})

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
    def __init__(self, backend, *, picker=None, clock=time.monotonic, review_factory=None, external_review_factory=None):
        self.backend, self.picker, self.clock = backend, picker if picker is not None else NativePicker(), clock
        self.lock = threading.Lock()
        self.sessions = {}
        self.jobs = {}
        self.tasks = {}  # Task credentials remain in this trusted process only.
        self.dialog_busy = False
        self.review_factory = review_factory
        self.external_review_factory = external_review_factory
        self.review_thread = None
        self.review_loop = self.review_task = None
        self.closed = False

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
            if self.closed:
                raise ManagementError(503, "MANAGER_CLOSED", "管理助手已经关闭。")
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

    def exclusions(self, sid, selection, offset):
        value = self.status(sid, selection)
        if value["state"] != "ready":
            raise ManagementError(409, "SELECTION_CONSUMED", "预览尚未就绪或已经消费。")
        return self.backend.exclusions(value["preview"]["preview_id"], offset)

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

    def create_task(self, workspace_id, goal, profile="readonly"):
        with self.lock:
            if len(self.tasks) >= 128:
                raise ManagementError(429, "TASK_LIMIT", "本次服务的任务数量已达上限，请重启后再创建。")
            # Reserve capacity while the HTTP worker is in flight.
            reservation = "pending_" + secrets.token_hex(16)
            self.tasks[reservation] = None
        try:
            value = (self.backend.create_edit_task(workspace_id, goal) if profile == "controlled_edit" else self.backend.create_readonly_task(workspace_id, goal))
            clean = {k: value[k] for k in ("task_id", "workspace_id", "snapshot_id", "goal",
                    "permission_profile", "phase", "created_at", "updated_at", "instance_id")}
            with self.lock:
                self.tasks[value["task_id"]] = {"token": value["agent_token"], "summary": clean, "review": {"state": "idle", "call_count": 0, "read_file_count": 0}}
            return clean
        finally:
            with self.lock:
                self.tasks.pop(reservation, None)

    def edit_task(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if task is None or task["summary"]["permission_profile"] != "controlled_edit" or task["review"]["state"] != "completed":
                raise ManagementError(409, "EDIT_NOT_READY", "修改任务尚未完成或不可查看。")
        return task

    def changes(self, task_id):
        self.edit_task(task_id)
        return self.backend.edit_changes(task_id)

    def change(self, task_id, path):
        self.edit_task(task_id)
        return self.backend.edit_change(task_id, path)

    def apply(self, task_id, path, after_sha256):
        self.edit_task(task_id)
        return self.backend.apply_edit(task_id, path, after_sha256)

    def review_status(self, task_id):
        with self.lock:
            value = self.tasks.get(task_id)
            if value is None:
                raise ManagementError(404, "TASK_NOT_MANAGED", "本机助手未持有该任务身份；重启后需要重新创建。")
            return dict(value["review"])

    def start_review(self, task_id, agent_type="builtin"):
        if agent_type not in ("builtin", "deepseek_harness"):
            raise ManagementError(400, "INVALID_REQUEST", "不支持的 Agent 类型。")
        with self.lock:
            if self.closed:
                raise ManagementError(503, "MANAGER_CLOSED", "管理助手已经关闭。")
            task = self.tasks.get(task_id)
            if task is None:
                raise ManagementError(404, "TASK_NOT_MANAGED", "请通过当前本机助手创建任务。")
            if task["review"]["state"] != "idle":
                raise ManagementError(409, "REVIEW_CONSUMED", "该任务的运行已经启动或结束，请查看结果。")
            if any(t and t["review"]["state"] == "running" for t in self.tasks.values()):
                raise ManagementError(409, "REVIEW_BUSY", "已有任务正在执行，请等待完成。")
            if agent_type == "deepseek_harness":
                if task["summary"]["permission_profile"] != "readonly":
                    raise ManagementError(403, "READONLY_TASK", "外部 Agent 暂仅支持只读任务。")
                factory = self.external_review_factory
                if factory is None:
                    raise ManagementError(503, "HARNESS_NOT_CONFIGURED", "外部 Agent 未完成安全配置，无法启动。")
            else:
                factory = self.review_factory
                if factory is None:
                    raise ManagementError(503, "MCP_NOT_CONFIGURED", "启动助手时需配置 Rust MCP 可执行文件。")
            task["review"].update({"state": "running", "agent_type": agent_type})
            self.review_thread = threading.Thread(target=self._review, args=(task_id, factory), daemon=True)
            self.review_thread.start()
        return {"state": "running", "agent_type": agent_type}

    def _review(self, task_id, factory):
        import asyncio
        from integrations.mcp_review import ReviewError
        from integrations.deepseek import ModelError

        task = self.tasks[task_id]
        external = task["review"].get("agent_type") == "deepseek_harness"
        def progress(value):
            if external:
                if not isinstance(value, dict):
                    return
                safe = {k: value[k] for k in ("call_count", "read_file_count") if type(value.get(k)) is int and 0 <= value[k] <= 10000}
                if value.get("progress_text") in ("正在连接外部 Agent", "正在执行任务", "正在核对审计"):
                    safe["progress_text"] = value["progress_text"]
                value = safe
            with self.lock:
                self.tasks[task_id]["review"].update(value)
        async def run(client):
            with self.lock:
                self.review_loop, self.review_task = asyncio.get_running_loop(), asyncio.current_task()
                closed = self.closed
            if closed:
                raise ReviewError("管理助手已经关闭。")
            return await client.run()
        try:
            client = factory(task["summary"], task["token"], progress)
            result = asyncio.run(run(client))
            if external:
                if not isinstance(result, dict) or result.get("mode") != "external_agent" or not isinstance(result.get("report"), str) or not 1 <= len(result["report"]) <= 20000 or not isinstance(result.get("calls"), list) or len(result["calls"]) > 100:
                    raise ReviewError("外部 Agent 返回的任务结果无效。")
                # Do not let untrusted process fields override lifecycle or expose credentials.
                safe_calls = []
                for call in result["calls"]:
                    if not isinstance(call, dict) or not isinstance(call.get("tool"), str) or len(call["tool"]) > 128 or type(call.get("ok")) is not bool:
                        raise ReviewError("外部 Agent 返回的调用记录无效。")
                    safe = {k: call[k] for k in ("tool", "ok", "request_id", "path") if k in call}
                    if any(not isinstance(v, str) or len(v) > 1024 for k, v in safe.items() if k != "ok"):
                        raise ReviewError("外部 Agent 返回的调用记录无效。")
                    safe_calls.append(safe)
                result = {"mode": "external_agent", "report": result["report"], "calls": safe_calls}
                secrets_to_hide = [task["token"], getattr(getattr(self.backend, "config", None), "token", ""), os.environ.get("DEEPSEEK_API_KEY", "")]
                if any(secret and secret in json.dumps(result, ensure_ascii=False) for secret in secrets_to_hide):
                    raise ReviewError("外部 Agent 返回了不安全的内容，任务已停止。")
            with self.lock:
                task["review"].update({"state": "completed", **result})
        except (ReviewError, ModelError) as error:
            message = str(error)
            if external:
                secrets_to_hide = [task["token"], getattr(getattr(self.backend, "config", None), "token", ""), os.environ.get("DEEPSEEK_API_KEY", "")]
                if any(secret and secret in message for secret in secrets_to_hide):
                    message = "外部 Agent 运行失败，请检查安全配置。"
            with self.lock:
                task["review"].update({"state": "failed", "message": message})
        except asyncio.CancelledError:
            with self.lock:
                task["review"].update({"state": "failed", "message": "管理助手关闭，运行已停止。"})
        except Exception:
            with self.lock:
                task["review"].update({"state": "failed", "message": "MCP 审查失败，请检查依赖、后端和启动配置。"})
        finally:
            with self.lock:
                self.review_loop = self.review_task = None

    def close(self):
        with self.lock:
            self.closed = True
            loop, task, thread = self.review_loop, self.review_task, self.review_thread
        close_picker = getattr(self.picker, "close", None)
        if close_picker:
            close_picker()
        if loop is not None and not loop.is_closed() and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass
        if thread is not None:
            thread.join(timeout=5)
