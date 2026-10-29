"""Bounded ACP subprocess protocol for trusted, explicitly configured Agent drivers.

This module only implements the transport; it does not establish an OS sandbox,
prove that a Harness profile excludes native tools, or start a product task. A
caller must verify its fixed profile and reconcile task-bound Rust audit before
reporting an external Agent run as successful.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Callable, Mapping, Sequence

MAX_FRAME = 512 * 1024
MAX_REPORT = 20_000
MAX_EVENTS = 1000


class AcpError(RuntimeError):
    """A bounded diagnostic that never includes child output or credentials."""


@dataclass(frozen=True)
class AcpOutcome:
    """The text and stop reason delivered by one ACP session prompt.

    This is transport evidence only: the caller must independently verify the
    Broker task and audit before surfacing it as an accepted review.
    """
    text: str
    stop_reason: str
    final_text: str = ""


class AcpProcess:
    """Supervise one trusted ACP stdio process with no automatic retry.

    The binary, args, environment and MCP declarations must be supplied by a
    trusted fixed-profile launcher, never by browser input or model output.
    ``secrets`` are used only for leak detection and are never serialized.
    """

    def __init__(self, executable: Path, args: Sequence[str], cwd: Path,
                 env: Mapping[str, str], mcp_servers: Sequence[dict],
                 *, secrets: Sequence[str] = (), timeout: float = 240,
                 progress: Callable[[dict], None] | None = None):
        try:
            self.executable = Path(executable).resolve(strict=True)
            self.cwd = Path(cwd).resolve(strict=True)
        except (OSError, RuntimeError):
            raise AcpError("外部 Agent 启动配置无效。") from None
        if not self.executable.is_file() or not self.cwd.is_dir():
            raise AcpError("外部 Agent 启动配置无效。")
        if not all(isinstance(a, str) and len(a) <= 4096 and "\0" not in a for a in args):
            raise AcpError("外部 Agent 启动参数无效。")
        if not isinstance(env, Mapping) or not all(isinstance(k, str) and isinstance(v, str) and "\0" not in k + v for k, v in env.items()):
            raise AcpError("外部 Agent 环境无效。")
        if len({k.upper() for k in env}) != len(env) or any(k.upper() == "AGENTFENCE_ADMIN_TOKEN" for k in env):
            raise AcpError("外部 Agent 环境包含不允许的管理员凭证。")
        self.secrets = tuple(value for value in secrets if isinstance(value, str) and value)
        self.args = tuple(args)
        self.env = dict(env)
        self._reject_secret(str(self.executable))
        self._reject_secret(str(self.cwd))
        self.servers = list(mcp_servers)
        # Do not send any credentials to ACP's mcpServers configuration. A
        # fixed server profile may reference its own protected environment.
        server_names = set()
        for server in self.servers:
            if not isinstance(server, dict) or set(server) != {"name", "command", "args", "env"} or server["env"] != []:
                raise AcpError("外部 Agent MCP 声明不符合安全约束。")
            if not isinstance(server["name"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", server["name"]):
                raise AcpError("外部 Agent MCP 名称无效。")
            if server["name"] in server_names:
                raise AcpError("外部 Agent MCP 名称重复。")
            server_names.add(server["name"])
            if not isinstance(server["command"], str) or not Path(server["command"]).is_absolute() or not Path(server["command"]).is_file() or not isinstance(server["args"], list) or len(server["args"]) > 32 or not all(isinstance(a, str) and len(a) <= 4096 and "\0" not in a for a in server["args"]):
                raise AcpError("外部 Agent MCP 启动声明无效。")
        self._reject_secret(self.servers)
        self._reject_secret(self.args)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 600:
            raise AcpError("外部 Agent 超时配置无效。")
        self.timeout = float(timeout)
        self.progress = progress or (lambda _: None)
        self.process: asyncio.subprocess.Process | None = None
        self.next_id = 0
        self.session_id: str | None = None
        self.chunks: list[str] = []
        self.final_from = 0
        self.length = 0
        self.events = 0
        self.creating = False
        self.pending_updates: list[dict] = []
        self.pending_bytes = 0

    def _reject_secret(self, value):
        raw = json.dumps(value, ensure_ascii=False)
        if any(secret in raw for secret in self.secrets):
            raise AcpError("外部 Agent 消息包含受保护凭证。")

    async def _send(self, message: dict) -> None:
        self._reject_secret(message)
        raw = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(raw) > MAX_FRAME or not self.process or not self.process.stdin:
            raise AcpError("外部 Agent 请求超出限制。")
        self.process.stdin.write(raw)
        await self.process.stdin.drain()

    async def _update(self, message: dict) -> None:
        self.events += 1
        if self.events > MAX_EVENTS:
            raise AcpError("外部 Agent 更新次数超出限制。")
        params = message.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("sessionId"), str):
            raise AcpError("外部 Agent 会话更新格式无效。")
        if not self.session_id:
            if not self.creating or len(self.pending_updates) >= 32:
                raise AcpError("外部 Agent 返回了未绑定会话的更新。")
            self.pending_bytes += len(json.dumps(message, ensure_ascii=False).encode("utf-8"))
            if self.pending_bytes > MAX_FRAME:
                raise AcpError("外部 Agent 初始更新超出限制。")
            self.pending_updates.append(message)
            return
        if params.get("sessionId") != self.session_id:
            raise AcpError("外部 Agent 返回了其他会话的更新。")
        update = params.get("update")
        if not isinstance(update, dict) or not isinstance(update.get("sessionUpdate"), str):
            raise AcpError("外部 Agent 更新格式无效。")
        kind = update["sessionUpdate"]
        if kind == "agent_message_chunk":
            content = update.get("content")
            if not isinstance(content, dict) or content.get("type") != "text" or not isinstance(content.get("text"), str):
                raise AcpError("外部 Agent 文本更新格式无效。")
            text = content["text"]
            self._reject_secret(text)
            self.length += len(text)
            if self.length > MAX_REPORT:
                raise AcpError("外部 Agent 报告超出长度限制。")
            self.chunks.append(text)
            self._reject_secret("".join(self.chunks))
        elif kind in ("tool_call", "tool_call_update"):
            # This is display progress only. Rust's audit is authoritative.
            # Assistant text before the last tool call is narration, not report.
            if kind == "tool_call":
                self.final_from = len(self.chunks)
            self.progress({"progress_text": "正在执行任务"})
        # Ignore thoughts and other untrusted, nonessential update payloads.

    async def _request(self, method: str, params: dict) -> dict:
        self.next_id += 1
        request_id = self.next_id
        await self._send({"jsonrpc":"2.0", "id":request_id, "method":method, "params":params})
        assert self.process and self.process.stdout
        while True:
            try:
                raw = await self.process.stdout.readline()
            except (ValueError, asyncio.LimitOverrunError):
                raise AcpError("外部 Agent 响应超出限制。") from None
            if not raw or len(raw) > MAX_FRAME or not raw.endswith(b"\n"):
                raise AcpError("外部 Agent 进程已退出或响应不完整。")
            try:
                message = json.loads(raw)
            except (ValueError, UnicodeError, RecursionError):
                raise AcpError("外部 Agent 协议响应无效。") from None
            if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                raise AcpError("外部 Agent 协议响应无效。")
            if "method" in message and "id" in message:
                if message["method"] == "session/request_permission":
                    params = message.get("params")
                    if not isinstance(params, dict) or not self.session_id or params.get("sessionId") != self.session_id:
                        raise AcpError("外部 Agent 权限请求的会话无效。")
                    options = params.get("options")
                    if not isinstance(options, list) or len(options) > 32:
                        raise AcpError("外部 Agent 权限请求格式无效。")
                    ids = [item.get("optionId") for item in options if isinstance(item, dict) and isinstance(item.get("optionId"), str)]
                    unique = len(ids) == len(options) and len(ids) == len(set(ids))
                    reject = next((item.get("optionId") for item in options if unique and isinstance(item, dict) and item.get("kind") in ("reject_once", "reject_always") and isinstance(item.get("optionId"), str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item["optionId"])), None)
                    outcome = {"outcome": "selected", "optionId": reject} if reject else {"outcome": "cancelled"}
                    await self._send({"jsonrpc":"2.0", "id":message["id"], "result":{"outcome":outcome}})
                    continue
                # Never expose host filesystem, terminal or other client capabilities.
                await self._send({"jsonrpc":"2.0", "id":message["id"], "error":{"code":-32601,"message":"Client capability unavailable"}})
                raise AcpError("外部 Agent 请求了未开放的客户端能力。")
            if "method" in message:
                if message["method"] == "session/update":
                    await self._update(message)
                continue
            if type(message.get("id")) is not int or message["id"] != request_id or "error" in message or not isinstance(message.get("result"), dict):
                raise AcpError("外部 Agent 请求未成功完成。")
            return message["result"]

    async def run(self, goal: str) -> AcpOutcome:
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 1000:
            raise AcpError("外部 Agent 任务目标无效。")
        self._reject_secret(goal)
        self.progress({"progress_text": "正在连接外部 Agent"})
        creationflags = getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        try:
            async with asyncio.timeout(self.timeout):
                self.process = await asyncio.create_subprocess_exec(
                    str(self.executable), *self.args, cwd=str(self.cwd), env=self.env,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, limit=MAX_FRAME, creationflags=creationflags,
                )
                initialized = await self._request("initialize", {"protocolVersion":1,"clientCapabilities":{}})
                if initialized.get("protocolVersion") != 1:
                    raise AcpError("外部 Agent ACP 版本不兼容。")
                self.creating = True
                created = await self._request("session/new", {"cwd":str(self.cwd),"mcpServers":self.servers})
                self.creating = False
                session = created.get("sessionId")
                if not isinstance(session, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session):
                    raise AcpError("外部 Agent 会话标识无效。")
                self.session_id = session
                pending, self.pending_updates = self.pending_updates, []
                self.events -= len(pending)  # Previously counted while awaiting the session ID.
                for update in pending:
                    params = update.get("params")
                    if not isinstance(params, dict) or params.get("sessionId") != session:
                        raise AcpError("外部 Agent 返回了其他会话的更新。")
                    # Session-creation messages cannot be the response to this
                    # prompt. Validate their binding, but never use their text.
                    event = params.get("update")
                    if isinstance(event, dict) and event.get("sessionUpdate") == "agent_message_chunk":
                        continue
                    await self._update(update)
                self.progress({"progress_text": "正在执行任务"})
                result = await self._request("session/prompt", {"sessionId":session,"prompt":[{"type":"text","text":goal}]})
                reason = result.get("stopReason")
                full = "".join(self.chunks)
                final = "".join(self.chunks[self.final_from:]).strip() or full
                if reason != "end_turn" or not full.strip():
                    raise AcpError("外部 Agent 没有完成有效报告。")
                return AcpOutcome(full, reason, final)
        except TimeoutError:
            raise AcpError("外部 Agent 运行超时。") from None
        except (OSError, BrokenPipeError, ConnectionError):
            raise AcpError("外部 Agent 无法启动或连接已中断。") from None
        finally:
            await self.close()

    async def close(self) -> None:
        """Close the owned protocol process without touching other services."""
        process, self.process = self.process, None
        if process is None:
            return
        if process.stdin:
            process.stdin.close()
        if process.returncode is None:
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except TimeoutError:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=3)
                except TimeoutError:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()
