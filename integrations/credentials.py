"""Write-only storage for the Harness model credential.

The browser may submit a key once, but it never receives one back: status
responses only say whether a credential exists and where it came from. The key
is stored in a per-user file outside the repository, or supplied through
DEEPSEEK_API_KEY for deployment-managed runs.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

ENV_NAME = "DEEPSEEK_API_KEY"
MIN_LENGTH = 20
MAX_LENGTH = 256


def default_path() -> Path:
    """Per-user location that is never inside a registered workspace."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "AgentFence" / "harness.key"


def validate(api_key: str) -> str:
    """Accept only a single bounded DeepSeek-style API key."""
    if not isinstance(api_key, str):
        raise ValueError("credential must be text")
    key = api_key.strip()
    if not MIN_LENGTH <= len(key) <= MAX_LENGTH:
        raise ValueError("credential length is invalid")
    if not key.startswith("sk-") or any(not 33 <= ord(c) <= 126 for c in key):
        raise ValueError("credential format is invalid")
    return key


class CredentialStore:
    """One launcher-owned credential file with an environment override."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path).resolve() if path else default_path()

    def load(self) -> str:
        """Return the active key, preferring the deployment environment."""
        value = os.environ.get(ENV_NAME, "").strip()
        if value:
            return value
        try:
            return self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def status(self) -> dict:
        """Public state only: never includes the key or part of it."""
        if os.environ.get(ENV_NAME, "").strip():
            return {"configured": True, "source": "environment", "editable": False}
        if self.path.is_file():
            return {"configured": True, "source": "file", "editable": True}
        return {"configured": False, "source": None, "editable": True}

    def save(self, api_key: str) -> dict:
        key = validate(api_key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(key, encoding="utf-8")
        os.replace(temporary, self.path)
        try:
            os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        return self.status()

    def clear(self) -> dict:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        return self.status()
