"""Validate prerequisite tools and prepare a fresh, independent example fixture."""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import uuid

from .fixture import prepare_fixture
from .runtime import REPO


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--fixture-dir", type=Path)
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        print("SETUP_FAIL: Python 3.10+ is required", file=sys.stderr)
        return 1
    try:
        for tool in ("rustc", "cargo"):
            version = subprocess.run([tool, "--version"], check=True, capture_output=True, text=True).stdout.strip()
            if version.split()[1] != "1.98.1":
                raise RuntimeError(f"{tool} 1.98.1 is required by rust-toolchain.toml")
            print(version)
        print(f"Python {sys.version.split()[0]} (standard library only)")
        if not args.check_only:
            if args.fixture_dir is None:
                parent = REPO / ".agentfence"
                parent.mkdir(exist_ok=True)
                directory = parent / ("setup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
            else:
                directory = args.fixture_dir.resolve()
            prepare_fixture(directory)
            print(f"Prepared fixture: {directory}")
    except (OSError, subprocess.SubprocessError, RuntimeError) as error:
        print(f"SETUP_FAIL: {error}", file=sys.stderr)
        return 1
    print("SETUP_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
