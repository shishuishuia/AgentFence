"""Small synchronous HTTP clients for the implemented v2.1 broker contract.

Uses only the Python standard library. There are no automatic retries, redirects,
environment proxies, or direct target-file operations. In particular a failed or
timed-out write may already have executed; its caller must inspect the outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from http.client import HTTPException
import ipaddress
import json
import math
import re
import socket
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_RESPONSE_BYTES = 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


class ClientError(RuntimeError):
    """A safe transport/protocol error; never includes credentials or contents."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass(frozen=True)
class ApiResponse:
    """Keep HTTP status and JSON together, including denied/executed outcomes."""

    status: int
    payload: dict[str, Any] = field(repr=False)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def require(self, status: int) -> dict[str, Any]:
        """Assert an expected HTTP status without leaking the response payload."""
        if self.status != status:
            reason = self.payload.get("reason_code", "UNKNOWN")
            # A hostile/nonconforming message must not become console output.
            safe_reason = reason if isinstance(reason, str) and re.fullmatch(r"[A-Z_]+", reason) else "UNKNOWN"
            raise ClientError(f"expected HTTP {status}, received {self.status} ({safe_reason})")
        return self.payload


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError("identifier must contain 1..128 ASCII letters, digits, '_' or '-'")
    return value


def _limit(value: int, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"limit must be an integer in 1..{maximum}")
    return value


class BrokerHttp:
    """Bounded JSON transport to a numeric loopback HTTP address only."""

    def __init__(self, base_url: str = "http://127.0.0.1:8080", *, timeout: float = 5.0):
        parsed = urlsplit(base_url)
        try:
            loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
            port = parsed.port
        except ValueError:
            raise ValueError("broker URL must use a numeric loopback address") from None
        if (
            parsed.scheme != "http"
            or not loopback
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError("broker URL must be a loopback HTTP origin without credentials or a path")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._opener = build_opener(ProxyHandler({}), _NoRedirect())

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        body: dict[str, Any] | None = None,
    ) -> ApiResponse:
        """Return non-2xx JSON normally so callers can check policy and side effects."""
        if not path.startswith("/") or path.startswith("//") or "#" in path or any(ord(c) < 32 or ord(c) == 127 for c in path):
            raise ValueError("request path must be an absolute local path without controls")
        headers = {"Accept": "application/json"}
        if token is not None:
            if not isinstance(token, str) or not token or any(not 33 <= ord(c) <= 126 for c in token):
                raise ValueError("token must contain visible ASCII characters only")
            headers["Authorization"] = f"Bearer {token}"
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            try:
                response = self._opener.open(request, timeout=self.timeout)
            except HTTPError as error:
                response = error
            with response:
                status = response.code
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (TimeoutError, socket.timeout):
            raise ClientError("broker request timed out; write execution may be unknown; do not retry automatically") from None
        except (URLError, OSError, HTTPException):
            raise ClientError("broker connection failed; write execution may be unknown; do not retry automatically") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ClientError("broker response exceeds the client size limit")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ClientError("broker response is not valid UTF-8 JSON") from None
        if not isinstance(payload, dict):
            raise ClientError("broker response must be a JSON object")
        return ApiResponse(status, payload)

    def health(self) -> ApiResponse:
        """Health is intentionally unauthenticated."""
        return self.request("GET", "/healthz")


class AdminClient:
    """Control-plane operations using an externally supplied Admin Token."""

    def __init__(self, http: BrokerHttp, token: str):
        self.http = http
        self._token = token

    def _request(self, method: str, path: str, body=None) -> ApiResponse:
        return self.http.request(method, path, token=self._token, body=body)

    def create_task(self, workspace_id: str) -> ApiResponse:
        return self._request("POST", "/v1/admin/tasks", {"workspace_id": _identifier(workspace_id)})

    def tasks(self, *, limit: int = 50) -> ApiResponse:
        return self._request("GET", "/v1/admin/tasks?" + urlencode({"limit": _limit(limit, 100)}))

    def task(self, task_id: str) -> ApiResponse:
        return self._request("GET", f"/v1/admin/tasks/{_identifier(task_id)}")

    def advance(self, task_id: str, phase: str) -> ApiResponse:
        if phase not in ("inspect", "edit", "verify", "done"):
            raise ValueError("unknown phase")
        return self._request("POST", f"/v1/admin/tasks/{_identifier(task_id)}/phase", {"phase": phase})

    def issue_grant(self, task_id: str, path: str, *, ttl_seconds: int = 60, max_uses: int = 1) -> ApiResponse:
        return self._request("POST", f"/v1/admin/tasks/{_identifier(task_id)}/grants", {
            "tool": "write_file", "path": path,
            "ttl_seconds": _limit(ttl_seconds, 300), "max_uses": _limit(max_uses, 10),
        })

    def revoke_grant(self, task_id: str, grant_id: str) -> ApiResponse:
        # DELETE must have an empty body, not {} or null.
        return self._request("DELETE", f"/v1/admin/tasks/{_identifier(task_id)}/grants/{_identifier(grant_id)}")

    def events(self, *, task_id: str | None = None, after: str | None = None, limit: int = 100) -> ApiResponse:
        query: dict[str, Any] = {"limit": _limit(limit, 500)}
        if task_id is not None:
            query["task_id"] = _identifier(task_id)
        if after is not None:
            query["after"] = after  # Opaque cursor; no client arithmetic or reconstruction.
        return self._request("GET", "/v1/admin/events?" + urlencode(query))


class AgentClient:
    """Task-bound file tools. No admin methods or direct filesystem access."""

    def __init__(self, http: BrokerHttp, task_id: str, token: str):
        self.http = http
        self.task_id = _identifier(task_id)
        self._token = token

    def read_file(self, path: str) -> ApiResponse:
        return self.http.request("POST", "/v1/tool/execute", token=self._token, body={
            "task_id": self.task_id, "tool": "read_file", "path": path,
        })

    def write_file(self, path: str, content: str, *, grant_id: str | None = None) -> ApiResponse:
        body = {"task_id": self.task_id, "tool": "write_file", "path": path, "content": content}
        if grant_id is not None:
            body["grant_id"] = _identifier(grant_id)
        return self.http.request("POST", "/v1/tool/execute", token=self._token, body=body)
