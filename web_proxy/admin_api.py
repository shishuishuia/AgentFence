"""Opt-in, same-origin, session and CSRF protected local management routes."""

import json
import re
from urllib.parse import parse_qsl

from .workspaces import COOKIE, ManagementError


def _body(handler):
    lengths = handler.headers.get_all("Content-Length", [])
    if handler.headers.get("Transfer-Encoding") or len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,5}", lengths[0]) or not 0 < int(lengths[0]) <= 8192:
        raise ManagementError(400, "INVALID_REQUEST", "管理请求长度无效。")
    if handler.headers.get("Content-Type", "").split(";")[0].lower().strip() != "application/json":
        raise ManagementError(415, "INVALID_REQUEST", "管理请求需要 JSON。")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        result = json.loads(handler.rfile.read(int(lengths[0])), object_pairs_hook=unique,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(result, dict):
            raise ValueError
        return result
    except (UnicodeError, ValueError, RecursionError):
        raise ManagementError(400, "INVALID_REQUEST", "管理请求 JSON 无效。") from None


def handle_admin(handler, manager, path, query):
    """Dispatch only explicitly listed routes, never a generic Admin proxy."""
    try:
        selection = re.fullmatch(r"/api/admin/picker/(sel_[a-f0-9]{32})", path)
        exclusions = re.fullmatch(r"/api/admin/picker/(sel_[a-f0-9]{32})/excluded", path)
        review = re.fullmatch(r"/api/admin/tasks/([A-Za-z0-9_-]{1,128})/review(/start)?", path)
        expected = "GET" if selection or exclusions or path == "/api/admin/workspaces" or (review and not review.group(2)) else "POST"
        if path not in {"/api/admin/session", "/api/admin/picker/start", "/api/admin/workspaces/confirm", "/api/admin/workspaces", "/api/admin/tasks"} and selection is None and exclusions is None and review is None:
            raise ManagementError(404, "ROUTE_NOT_FOUND", "未知管理接口。")
        offset = None
        if exclusions:
            pairs = parse_qsl(query, strict_parsing=True, max_num_fields=1)
            if len(pairs) != 1 or pairs[0][0] != "offset" or not re.fullmatch(r"[0-9]{1,5}", pairs[0][1]):
                raise ManagementError(400, "INVALID_REQUEST", "排除项分页参数无效。")
            offset = int(pairs[0][1])
        if (query and not exclusions) or handler.command != expected:
            raise ManagementError(400 if query else 405, "INVALID_REQUEST", "请求方法或查询参数无效。")
        if expected == "POST":
            origins = handler.headers.get_all("Origin", [])
            if len(origins) != 1 or origins[0] not in {f"http://127.0.0.1:{handler.server.server_port}", "http://127.0.0.1:5173"}:
                raise ManagementError(403, "ORIGIN_REQUIRED", "请通过本机管理页面操作。")
            body = _body(handler)
        else:
            body = {}
        if path == "/api/admin/session":
            if body:
                raise ManagementError(400, "INVALID_REQUEST", "建立会话不接受额外字段。")
            sid, csrf = manager.session(handler.headers.get("Cookie", ""))
            return handler.reply(200, {"csrf_token": csrf}, cookie=f"{COOKIE}={sid}; HttpOnly; SameSite=Strict; Path=/api/admin; Max-Age=1800")
        cookies = handler.headers.get_all("Cookie", [])
        csrf = handler.headers.get_all("X-AgentFence-CSRF", [])
        if len(cookies) != 1 or (expected == "POST" and len(csrf) != 1):
            raise ManagementError(403, "CSRF_DENIED", "管理请求缺少会话或校验信息。")
        sid = manager.authenticate(cookies[0], csrf[0] if expected == "POST" else None)
        if review:
            if body:
                raise ManagementError(400, "INVALID_REQUEST", "审查启动不接受额外参数。")
            return handler.reply(202 if review.group(2) else 200, manager.start_review(review.group(1)) if review.group(2) else manager.review_status(review.group(1)))
        if path == "/api/admin/picker/start":
            if body:
                raise ManagementError(400, "INVALID_REQUEST", "目录仅由系统选择器提供。")
            return handler.reply(202, manager.start(sid))
        if selection:
            return handler.reply(200, manager.status(sid, selection.group(1)))
        if exclusions:
            return handler.reply(200, manager.exclusions(sid, exclusions.group(1), offset))
        if path == "/api/admin/workspaces":
            return handler.reply(200, manager.backend.workspaces())
        if path == "/api/admin/tasks":
            if set(body) != {"workspace_id", "goal", "permission_profile"} or body["permission_profile"] != "readonly" or not isinstance(body["workspace_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", body["workspace_id"]) or not isinstance(body["goal"], str) or not 1 <= len(body["goal"].strip()) <= 1000 or len(body["goal"].encode("utf-8")) > 4000 or any(ord(x) < 32 and x not in "\n\t" for x in body["goal"]):
                raise ManagementError(400, "INVALID_REQUEST", "只读任务参数无效。")
            return handler.reply(201, manager.create_task(body["workspace_id"], body["goal"].strip()))
        if set(body) != {"selection_id", "display_name", "profile"} or body["profile"] != "readonly_snapshot" or not isinstance(body["selection_id"], str) or not re.fullmatch(r"sel_[a-f0-9]{32}", body["selection_id"]) or not isinstance(body["display_name"], str) or not 1 <= len(body["display_name"].strip()) <= 80 or len(body["display_name"].encode("utf-8")) > 256 or any(ord(x) < 32 for x in body["display_name"]):
            raise ManagementError(400, "INVALID_REQUEST", "工作区确认参数无效。")
        return handler.reply(201, manager.confirm(sid, body["selection_id"], body["display_name"].strip()))
    except ManagementError as error:
        handler.error(error.status, error.reason, error.message)
