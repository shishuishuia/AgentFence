"""Prepare disposable demo targets, never the repository's actual source."""

import json
from pathlib import Path

BEFORE = 'fn main() { println!("before"); }\n'
AFTER = 'fn main() { println!("fixed 中文"); }\n'
PRIVATE = "SIMULATED_TEST_TEXT_ONLY\n"


def prepare_fixture(directory: Path) -> Path:
    """Create a new directory atomically; refuse existing files or directories."""
    directory.mkdir(parents=False, exist_ok=False)
    workspace = directory / "workspace"
    (workspace / "src").mkdir(parents=True)
    (workspace / "src/main.rs").write_bytes(BEFORE.encode("utf-8"))
    (workspace / "Cargo.toml").write_bytes(
        b'[package]\nname = "demo-target"\nversion = "0.1.0"\nedition = "2024"\n\n[workspace]\n'
    )
    (directory / "simulated-private.txt").write_bytes(PRIVATE.encode("utf-8"))
    policy = directory / "policy.json"
    policy.write_text(json.dumps({"workspaces": {"demo": {
        "root": "workspace", "allowed_paths": ["src/main.rs", "Cargo.toml"],
        "max_content_bytes": 65536,
    }}}, indent=2) + "\n", encoding="utf-8")
    return policy
