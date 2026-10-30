"""Task-scoped external Agent review over ACP, MCP and Broker audit.

Configuration is explicit: a trusted launcher supplies the pinned Harness entry,
the Node executable and the Rust MCP binary before the service starts. Nothing
is auto-detected from PATH at request time, and an absent configuration keeps
the external Agent unavailable instead of silently enabled.

The Agent never receives the task token or the administrator token. It launches
a pipe client whose name only reaches a current-user relay that owns the
task-bound Rust MCP child. The review is accepted only when Broker audit shows
at least three successful snapshot reads for this task and no successful write.
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .client import AdminClient, BrokerHttp, ClientError
from .harness_acp import AcpError, AcpProcess
from .harness_pipe import PipeRelayError, TaskPipeRelay
from .mcp_review import ReviewError

PINNED_NAME = "@deepseek-ai/dsh"
PINNED_VERSION = "0.2.1-rc.2"
MAX_REPORT = 20_000
MAX_CALLS = 100
REQUIRED_READS = 3
PIPE_CLIENT = Path(__file__).with_name("harness_pipe_client.py").resolve()
ALLOWED_TOOLS = (
    "mcp__agentfence__agentfence_list_files",
    "mcp__agentfence__agentfence_read_file",
    "mcp__agentfence__agentfence_task_status",
)
WRITE_TOOL = "mcp__agentfence__agentfence_write_file"
EDIT_CONSTRAINT = "\n\n约束：只修改完成目标所需的最小片段；保持原文件的编码、换行符和其余格式不变。"
GUARD_NAME = "agentfence-readonly-guard"
GUARD_SOURCE = """export const name = "agentfence-readonly-guard";
export const inject = ["tools"];
export function apply(ctx) {
  const allow = new Set(ALLOW);
  ctx.tools.guard(exec => allow.has(exec.name) ? undefined : "AgentFence blocked this tool");
}
"""


def compose_goal(goal: str, editable: bool) -> str:
    """Add a launcher constraint for edits without exceeding the ACP goal limit."""
    if not editable or len(goal) + len(EDIT_CONSTRAINT) > 1000:
        return goal
    return goal + EDIT_CONSTRAINT


def _resolved_home(value) -> Path | None:
    return Path(value).resolve() if value else None


@dataclass(frozen=True)
class HarnessConfig:
    """Validated launcher configuration for one pinned external Harness."""

    entry: Path
    node: Path
    mcp_binary: Path
    home: Path | None = None
    model: str = "deepseek-flash"
    timeout: float = 600.0

    def __post_init__(self):
        entry, node, binary = Path(self.entry), Path(self.node), Path(self.mcp_binary)
        for path, label in ((entry, "DSH entry"), (node, "Node"), (binary, "MCP binary")):
            if not path.is_absolute() or not path.is_file():
                raise ValueError(f"{label} must be an existing absolute file")
        if self.home is not None:
            home = Path(self.home)
            if not home.is_absolute() or not home.is_dir():
                raise ValueError("Harness home must be an existing absolute directory")
            object.__setattr__(self, "home", home)
        if not 1 <= self.timeout <= 600:
            raise ValueError("Harness timeout must be within 1..600 seconds")
        if not isinstance(self.model, str) or not self.model or len(self.model) > 64 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in self.model):
            raise ValueError("Harness model name is invalid")
        self._verify_version(entry)
        object.__setattr__(self, "entry", entry)
        object.__setattr__(self, "node", node)
        object.__setattr__(self, "mcp_binary", binary)

    @staticmethod
    def _verify_version(entry: Path) -> None:
        manifest_path = entry.parent.parent / "package.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ValueError("Harness package manifest is unreadable") from None
        if (manifest.get("name"), manifest.get("version")) != (PINNED_NAME, PINNED_VERSION):
            raise ValueError(f"Harness must be {PINNED_NAME}@{PINNED_VERSION}")

    @classmethod
    def from_environment(
        cls,
        *,
        entry: Path | str | None = None,
        node: Path | str | None = None,
        mcp_binary: Path | str,
        home: Path | str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> "HarnessConfig":
        """Resolve explicit values first, then documented environment variables."""
        resolved_entry = entry or os.environ.get("AGENTFENCE_DSH_ENTRY")
        resolved_node = node or os.environ.get("AGENTFENCE_DSH_NODE") or shutil.which("node")
        if not resolved_entry:
            raise ValueError("set AGENTFENCE_DSH_ENTRY to the pinned Harness lib/bin.js")
        if not resolved_node:
            raise ValueError("set AGENTFENCE_DSH_NODE to an absolute node executable")
        return cls(
            entry=Path(resolved_entry).resolve(),
            node=Path(resolved_node).resolve(),
            mcp_binary=Path(mcp_binary).resolve(),
            home=_resolved_home(home or os.environ.get("AGENTFENCE_DSH_HOME")),
            model=model or os.environ.get("AGENTFENCE_DSH_MODEL") or "deepseek-flash",
            timeout=float(timeout or os.environ.get("AGENTFENCE_DSH_TIMEOUT") or 600),
        )

    def describe(self) -> dict:
        """Safe diagnostics for a launcher log; never includes credentials."""
        return {
            "harness": f"{PINNED_NAME}@{PINNED_VERSION}",
            "entry": str(self.entry),
            "node": str(self.node),
            "mcp_binary": str(self.mcp_binary),
            "home": str(self.home) if self.home else None,
            "model": self.model,
            "timeout": self.timeout,
        }


def summarize_events(events: list[dict], snapshot_id: str, *, editable: bool = False) -> tuple[list[dict], set[str]]:
    """Accept audit-visible snapshot reads; writes only for isolated edit tasks."""
    calls: list[dict] = []
    reads: set[str] = set()
    for event in events:
        if event.get("event_type") != "result":
            continue
        tool = event.get("tool")
        ok = bool(event.get("allowed")) and bool(event.get("executed"))
        request_id = event.get("request_id")
        if tool == "write_file":
            if not isinstance(request_id, str) or not request_id:
                raise ReviewError("审计记录缺少请求标识。")
            if ok:
                if not editable:
                    raise ReviewError("外部 Agent 在只读任务中完成了写入。")
                if event.get("snapshot_id") != snapshot_id:
                    raise ReviewError("写入记录与任务快照不一致。")
            path = event.get("path")
            calls.append({"tool": "write_file", "ok": ok, "request_id": request_id, "path": path if isinstance(path, str) else None})
            if len(calls) > MAX_CALLS:
                raise ReviewError("外部 Agent 的调用次数超出上限。")
            continue
        if tool != "read_file":
            continue
        if not isinstance(request_id, str) or not request_id:
            raise ReviewError("审计记录缺少请求标识。")
        path = event.get("path")
        if ok:
            if event.get("snapshot_id") != snapshot_id:
                raise ReviewError("读取记录与任务快照不一致。")
            if not isinstance(path, str) or not path:
                raise ReviewError("读取记录缺少相对路径。")
            reads.add(path)
        calls.append({"tool": "read_file", "ok": ok, "request_id": request_id, "path": path if isinstance(path, str) else None})
        if len(calls) > MAX_CALLS:
            raise ReviewError("外部 Agent 的调用次数超出上限。")
    return calls, reads


def guard_patch(model: str | None = None) -> str:
    """Loader patch that inserts the guard and optionally pins another model."""
    lines = [
        "- insert:",
        f"    - id: {GUARD_NAME}",
        "      name: '@agentfence/readonly-guard'",
    ]
    if model and model != "deepseek-flash":
        lines += [
            "- id: agent-default-model",
            "  config:",
            "    provider: deepseek-official",
            f"    model: {json.dumps(model)}",
        ]
    return "\n".join(lines) + "\n"


class ExternalReview:
    """One configured Harness run bound to one readonly task."""

    def __init__(self, config: HarnessConfig, url: str, summary: dict, token: str, admin_token: str, *, progress=None, api_key: str | None = None):
        profile = summary.get("permission_profile")
        if profile not in ("readonly", "controlled_edit"):
            raise ReviewError("外部 Agent 不支持该任务类型。")
        if not isinstance(summary.get("snapshot_id"), str) or not summary["snapshot_id"]:
            raise ReviewError("任务缺少快照标识。")
        self.editable = profile == "controlled_edit"
        self.config, self.url, self.summary = config, url, summary
        self._token, self._admin_token = token, admin_token
        self.progress = progress or (lambda _: None)
        self._api_key = api_key if api_key is not None else os.environ.get("DEEPSEEK_API_KEY", "")
        self._last_counts = 0.0

    def has_credentials(self) -> bool:
        """An inherited key or a managed Harness credential store is enough."""
        if self._api_key:
            return True
        return self.config.home is not None and (self.config.home / ".credentials.yaml").is_file()

    def _report_progress(self, update: dict) -> None:
        """Include live audit counts so the console does not stay at zero."""
        now = time.monotonic()
        if now - self._last_counts >= 3:
            try:
                admin = AdminClient(BrokerHttp(self.url), self._admin_token)
                page = admin.events(task_id=self.summary["task_id"], limit=500).require(200)
                calls, reads = summarize_events(
                    page.get("events") or [], self.summary["snapshot_id"], editable=self.editable
                )
                update = {**update, "call_count": len(calls), "read_file_count": len(reads)}
                self._last_counts = now
            except Exception:
                pass
        self.progress(update)

    async def run(self) -> dict:
        if not self.has_credentials():
            raise ReviewError("外部 Agent 需要模型凭证：设置 DEEPSEEK_API_KEY，或在 DSH_HOME 中完成登录。")
        goal = self.summary.get("goal")
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 1000:
            raise ReviewError("任务目标无法交给外部 Agent。")
        goal = compose_goal(goal, self.editable)

        relay = None
        acp = None
        root = None
        result = None
        try:
            self._report_progress({"progress_text": "正在连接外部 Agent"})
            try:
                relay = TaskPipeRelay(
                    self.config.mcp_binary, self.url, self.summary["task_id"], self._token,
                    grant_hook=self._issue_grant if self.editable else None,
                ).start()
            except PipeRelayError:
                raise ReviewError("外部 Agent 的任务中继无法启动。") from None

            root = Path(tempfile.mkdtemp(prefix="agentfence-harness-"))
            home = prepare_profile(self.config, root, self.editable)
            servers = [{
                "name": "agentfence",
                "command": str(Path(sys.executable).resolve()),
                "args": [str(PIPE_CLIENT), relay.name],
                "env": [],
            }]
            acp = AcpProcess(
                self.config.node,
                [str(self.config.entry), "--profile", "acp"],
                root,
                self._child_environment(home),
                servers,
                secrets=(self._token, self._admin_token, self._api_key),
                timeout=self.config.timeout,
                progress=self._report_progress,
            )
            stop_counts = threading.Event()

            def poll_counts() -> None:
                while not stop_counts.wait(2):
                    try:
                        admin = AdminClient(BrokerHttp(self.url), self._admin_token)
                        page = admin.events(task_id=self.summary["task_id"], limit=500).require(200)
                        calls, reads = summarize_events(
                            page.get("events") or [], self.summary["snapshot_id"], editable=self.editable
                        )
                        self.progress({"call_count": len(calls), "read_file_count": len(reads)})
                    except Exception:
                        pass

            counts_thread = threading.Thread(target=poll_counts, daemon=True)
            counts_thread.start()
            if self.editable:
                await asyncio.to_thread(self._advance_to_edit)
            try:
                outcome = await acp.run(goal)
            except AcpError as error:
                message = _safe(str(error))
                if self.editable:
                    message += " 隔离副本可能已被修改，请在差异页核对。"
                raise ReviewError(message) from None

            report = (outcome.final_text or outcome.text or "").strip()
            if not report:
                raise ReviewError("外部 Agent 未返回审查报告。")
            if len(report) > MAX_REPORT:
                raise ReviewError("外部 Agent 报告超出长度上限。")
            self._report_progress({"progress_text": "正在核对审计"})
            calls, reads = await asyncio.to_thread(self._verify_and_finish)
            self.progress({"call_count": len(calls), "read_file_count": len(reads)})
            result = {"mode": "external_agent", "report": report, "calls": calls}
        except ClientError:
            raise ReviewError("审计查询失败，无法确认外部 Agent 的读取。") from None
        finally:
            if 'stop_counts' in locals():
                stop_counts.set()
            if 'counts_thread' in locals():
                counts_thread.join(2)
            if acp is not None:
                try:
                    await acp.close()
                except Exception:
                    pass
            if relay is not None:
                try:
                    relay.close()
                except PipeRelayError:
                    if sys.exc_info()[0] is None:
                        raise ReviewError("外部 Agent 中继未能停止。") from None
            if root is not None:
                shutil.rmtree(root, ignore_errors=True)
        return result

    def _prepare_home(self, root: Path) -> Path:
        return prepare_profile(self.config, root, self.editable)

    def _advance_to_edit(self) -> None:
        """Move an isolated edit task into its write phase before the prompt."""
        admin = AdminClient(BrokerHttp(self.url), self._admin_token)
        task_id = self.summary["task_id"]
        response = admin.advance(task_id, "edit")
        if response.status == 200:
            return
        detail = admin.task(task_id).require(200)
        if detail.get("phase") == "edit":
            return
        raise ReviewError("外部 Agent 无法进入修改阶段。")

    def _issue_grant(self, path: str) -> str | None:
        """Trusted launcher issues one short, single-use lease for a requested path."""
        admin = AdminClient(BrokerHttp(self.url), self._admin_token)
        task_id = self.summary["task_id"]
        try:
            response = admin.issue_grant(task_id, path, ttl_seconds=120, max_uses=1)
            if response.status == 201:
                value = response.payload.get("grant_id")
                return value if isinstance(value, str) else None
            if response.status == 409:
                detail = admin.task(task_id).require(200)
                for grant in detail.get("grants") or []:
                    if (
                        isinstance(grant, dict)
                        and grant.get("path") == path
                        and grant.get("revoked") is False
                        and isinstance(grant.get("remaining_uses"), int)
                        and grant["remaining_uses"] > 0
                        and isinstance(grant.get("grant_id"), str)
                    ):
                        return grant["grant_id"]
        except (ClientError, ValueError):
            return None
        return None

    def _child_environment(self, home: Path) -> dict:
        return child_environment(home, self._api_key)

    def _verify_and_finish(self) -> tuple[list[dict], set[str]]:
        """Advance to verify, reconcile audit, then close the task."""
        admin = AdminClient(BrokerHttp(self.url), self._admin_token)
        task_id = self.summary["task_id"]
        admin.advance(task_id, "verify").require(200)
        events: list[dict] = []
        cursor = None
        for _ in range(20):
            page = admin.events(task_id=task_id, after=cursor, limit=500).require(200)
            if page.get("instance_id") != self.summary["instance_id"]:
                raise ReviewError("审计实例已变化，无法确认这次外部 Agent 运行。")
            events.extend(page.get("events") or [])
            if not page.get("has_more"):
                break
            if page.get("next_cursor") == cursor:
                raise ReviewError("审计游标未前进。")
            cursor = page.get("next_cursor")
        else:
            raise ReviewError("审计记录超出本次核对上限。")
        calls, reads = summarize_events(events, self.summary["snapshot_id"], editable=self.editable)
        required = 1 if self.editable else REQUIRED_READS
        if len(reads) < required:
            raise ReviewError(f"外部 Agent 仅通过 Broker 读取了 {len(reads)} 个文件，少于 {required} 个。")
        admin.advance(task_id, "done").require(200)
        return calls, reads


def child_environment(home: Path, api_key: str) -> dict:
    """Minimal child environment: no Admin token, no ambient credentials."""
    env = {name: os.environ[name] for name in ("SYSTEMROOT", "PATH", "TEMP", "TMP") if os.environ.get(name)}
    env["DSH_HOME"] = str(home)
    if api_key:
        env["DEEPSEEK_API_KEY"] = api_key
    return env


def prepare_profile(config: HarnessConfig, root: Path, editable: bool = False) -> Path:
    """Locate a bootable profile, then install the guard and model patch.

    A deployment may supply one prepared DSH_HOME whose profile already has its
    bundle dependencies installed; otherwise the pinned launcher seeds a fresh
    profile, which only works when those dependencies are resolvable offline.
    """
    if config.home is not None:
        home = config.home
        profile = home / "profiles" / "acp"
        if not (profile / "package.json").is_file():
            raise ReviewError("外部 Agent 的 profile 未准备完成。")
    else:
        home = root / "home"
        home.mkdir(parents=True, exist_ok=True)
        env = child_environment(home, "")
        env["TEMP"] = str(root); env["TMP"] = str(root)
        try:
            completed = subprocess.run(
                [str(config.node), str(config.entry), "--profile", "acp", "--help"],
                cwd=str(root), env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ReviewError("外部 Agent 配置初始化失败。") from None
        profile = home / "profiles" / "acp"
        if completed.returncode != 0 or not profile.is_dir():
            raise ReviewError("外部 Agent 配置初始化失败。")

    plugin = profile / "node_modules" / "@agentfence" / "readonly-guard"
    plugin.mkdir(parents=True, exist_ok=True)
    (plugin / "package.json").write_text(
        json.dumps({"name": "@agentfence/readonly-guard", "type": "module", "main": "index.js"}),
        encoding="utf-8",
    )
    allowed = list(ALLOWED_TOOLS) + ([WRITE_TOOL] if editable else [])
    (plugin / "index.js").write_text(
        GUARD_SOURCE.replace("ALLOW", json.dumps(allowed)),
        encoding="utf-8",
    )
    (profile / "cordis.patch.yml").write_text(guard_patch(config.model), encoding="utf-8")
    return home


def preflight(config: HarnessConfig) -> dict:
    """Run one ACP handshake against the configured Harness without a model call."""
    root = Path(tempfile.mkdtemp(prefix="agentfence-harness-check-"))
    process = None
    try:
        home = prepare_profile(config, root)
        process = subprocess.Popen(
            [str(config.node), str(config.entry), "--profile", "acp"],
            cwd=str(root), env=child_environment(home, "offline-preflight"),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        request = {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": 1, "clientCapabilities": {}},
        }
        process.stdin.write((json.dumps(request) + "\n").encode("utf-8"))
        process.stdin.flush()
        lines: queue.Queue[bytes] = queue.Queue()

        def read_line() -> None:
            try:
                lines.put(process.stdout.readline())
            except (OSError, ValueError):
                lines.put(b"")

        threading.Thread(target=read_line, daemon=True).start()
        try:
            raw = lines.get(timeout=30)
        except queue.Empty:
            raise ReviewError("外部 Agent 未在 30 秒内响应 ACP 初始化。") from None
        if not raw:
            raise ReviewError("外部 Agent 启动后立即退出；请检查已准备的 profile。")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise ReviewError("外部 Agent 返回了无效的 ACP 响应。") from None
        if not isinstance(value, dict) or value.get("jsonrpc") != "2.0" or "result" not in value:
            raise ReviewError("外部 Agent 未完成 ACP 初始化。")
    finally:
        if process is not None:
            try:
                if process.stdin:
                    process.stdin.close()
            except OSError:
                pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
        shutil.rmtree(root, ignore_errors=True)
    return config.describe()


def _safe(message: str) -> str:
    """Keep ACP diagnostics bounded and free of child output."""
    text = " ".join(str(message).split())
    return text[:200] or "外部 Agent 运行失败。"