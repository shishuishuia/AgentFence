"""Record the actual tested tree and binary, including uncommitted source hashes."""

import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

from .runtime import REPO


def command(*args) -> str:
    try:
        result = subprocess.run(args, cwd=REPO, capture_output=True, text=True, timeout=5, check=True)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unavailable"


def create_manifest(binary: Path, fixture: Path, run_id: str) -> dict:
    files = [REPO / name for name in ("Cargo.toml", "Cargo.lock", "rust-toolchain.toml")]
    for folder in ("crates", "integrations", "demo"):
        files.extend(path for path in (REPO / folder).rglob("*") if path.suffix in (".rs", ".toml", ".py", ".sh") and path.is_file())
    sources = {path.relative_to(REPO).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(files)}
    source_hash = hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()
    binary_hash = hashlib.sha256(binary.read_bytes()).hexdigest() if binary.is_file() else None
    return {
        "run_id": run_id, "git_commit": command("git", "rev-parse", "HEAD"),
        "working_tree_dirty": command("git", "status", "--porcelain") != "",
        "source_tree_sha256": source_hash, "source_files": sources,
        "server_binary": binary.name, "server_binary_sha256": binary_hash,
        "build_profile": "release binary supplied by caller",
        "python": sys.version.split()[0], "rustc": command("rustc", "--version"),
        "cargo": command("cargo", "--version"), "platform": platform.platform(),
        "machine": platform.machine(),
        "policy_sha256": hashlib.sha256((fixture / "policy.json").read_bytes()).hexdigest(),
        "config": {"workspace_id": "demo", "allowed_paths": ["src/main.rs", "Cargo.toml"],
                   "max_content_bytes": 65536, "lease_ttl_seconds": 60, "lease_max_uses": 1,
                   "bind": "127.0.0.1:0", "event_page_limit": 2},
    }
