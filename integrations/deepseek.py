"""Bounded DeepSeek Chat Completions transport; credentials stay server-side."""

from dataclasses import dataclass, field
from http.client import HTTPException
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class ModelError(RuntimeError):
    """A safe, public error that never embeds upstream response bodies."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class DeepSeekConfig:
    key: str = field(repr=False)
    model: str = "deepseek-flash"

    @classmethod
    def from_env(cls):
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", model):
            raise ValueError("invalid model name")
        return cls(os.environ.get("DEEPSEEK_API_KEY", ""), model)


class DeepSeekClient:
    """Use the official HTTPS endpoint, with no retries or redirects.

    The non-thinking tool-call protocol is documented at
    https://api-docs.deepseek.com/guides/tool_calls/ . Only assistant text and
    function calls are returned; hidden reasoning is neither shown nor saved.
    """

    def __init__(self, config: DeepSeekConfig):
        self.config = config
        self._opener = build_opener(ProxyHandler({}), NoRedirect())

    def complete(self, messages: list, tools: list) -> tuple[dict, dict]:
        if not self.config.key:
            raise ModelError("服务端尚未配置 DEEPSEEK_API_KEY。")
        body = json.dumps({"model": self.config.model, "messages": messages,
                           "tools": tools, "thinking": {"type": "disabled"},
                           "max_tokens": 2048, "stream": False}, ensure_ascii=False).encode()
        if len(body) > 256 * 1024:
            raise ModelError("模型上下文超出本次演示上限，请缩短任务后重新运行。")
        request = Request("https://api.deepseek.com/chat/completions", data=body,
                          headers={"Authorization": "Bearer " + self.config.key,
                                   "Content-Type": "application/json"}, method="POST")
        try:
            with self._opener.open(request, timeout=20) as response:
                raw = response.read(1024 * 1024 + 1)
        except HTTPError as error:
            code = error.code
            error.close()
            messages = {401: "DeepSeek 密钥认证失败，请检查服务端配置。",
                        402: "DeepSeek 余额不足，请检查账户余额。",
                        429: "DeepSeek 请求受限，请稍后重新执行任务。"}
            raise ModelError(messages.get(code, f"DeepSeek 接口返回 HTTP {code}，本次任务停止。")) from None
        except (OSError, URLError, HTTPException):
            raise ModelError("DeepSeek 连接失败或超时，本次任务停止，不自动重试工具写入。") from None
        try:
            if len(raw) > 1024 * 1024:
                raise ValueError
            data = json.loads(raw)
            choice = data["choices"][0]
            if choice.get("finish_reason") not in ("stop", "tool_calls"):
                raise ValueError
            message = choice["message"]
            content = message.get("content")
            calls = message.get("tool_calls") or []
            if not (content is None or isinstance(content, str)) or not isinstance(calls, list) or len(calls) > 8:
                raise ValueError
            clean_calls = []
            for call in calls:
                function = call["function"]
                if call["type"] != "function" or not isinstance(call["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", call["id"]) or not isinstance(function["name"], str) or not isinstance(function["arguments"], str):
                    raise ValueError
                clean_calls.append({"id": call["id"], "type": "function", "function": {
                    "name": function["name"], "arguments": function["arguments"],
                }})
            result = {"role": "assistant", "content": content}
            if clean_calls:
                result["tool_calls"] = clean_calls
            usage = {k: data.get("usage", {}).get(k, 0) for k in ("prompt_tokens", "completion_tokens", "total_tokens")}
            if any(type(v) is not int or v < 0 for v in usage.values()):
                raise ValueError
            return result, usage
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ModelError("DeepSeek 返回了不完整或无效的工具调用响应。") from None
