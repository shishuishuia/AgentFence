"""Explicit local configuration; credentials are never command-line arguments."""

from dataclasses import dataclass, field
from pathlib import Path

from integrations.client import BrokerHttp

REPO = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Config:
    upstream: str
    token: str = field(repr=False)
    port: int = 4173
    dist: Path | None = REPO / "front/dist"
    timeout: float = 5.0

    def __post_init__(self):
        BrokerHttp(self.upstream, timeout=self.timeout)
        if not 32 <= len(self.token) <= 256 or any(not 33 <= ord(c) <= 126 for c in self.token):
            raise ValueError("Admin Token must be 32..256 visible ASCII characters")
        if type(self.port) is not int or not 0 <= self.port <= 65535:
            raise ValueError("invalid local port")
        if self.dist is not None and not (self.dist / "index.html").is_file():
            raise ValueError("front/dist/index.html missing; build the frontend or use --mode dev")
