#!/usr/bin/env sh
# Install the pinned DeepSeek Harness runtime used by AgentFence.
# Third-party MIT dependency: it stays under target/ and is never committed.
set -eu

HARNESS_DIR="${HARNESS_DIR:-target/harness}"
HOME_DIR="${DSH_HOME_OVERRIDE:-$HARNESS_DIR/home}"
NODE_BIN="${NODE:-$(command -v node)}"
VERSION="0.2.1-rc.2"
CHECK_ONLY="${CHECK_ONLY:-0}"

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
case "$HARNESS_DIR" in /*) ;; *) HARNESS_DIR="$ROOT/$HARNESS_DIR" ;; esac
case "$HOME_DIR" in /*) ;; *) HOME_DIR="$ROOT/$HOME_DIR" ;; esac
ENTRY="${ENTRY:-$HARNESS_DIR/node_modules/@deepseek-ai/dsh/lib/bin.js}"

if [ -z "$NODE_BIN" ] || [ ! -x "$NODE_BIN" ]; then
  echo "node not found; set NODE to an absolute node executable" >&2
  exit 1
fi

if [ "$CHECK_ONLY" != "1" ]; then
  if [ ! -f "$ENTRY" ]; then
    mkdir -p "$HARNESS_DIR"
    (cd "$HARNESS_DIR" && npm install "@deepseek-ai/dsh@$VERSION")
  fi
  mkdir -p "$HOME_DIR"
  DSH_HOME="$HOME_DIR" "$NODE_BIN" "$ENTRY" --profile acp --help >/dev/null
fi

[ -f "$ENTRY" ] || { echo "Harness entry missing: $ENTRY" >&2; exit 1; }
[ -f "$HOME_DIR/profiles/acp/package.json" ] || { echo "Harness profile missing under $HOME_DIR" >&2; exit 1; }

echo "harness: $ENTRY"
echo "home:    $HOME_DIR"

PYTHON="${PYTHON:-python3}"
if [ -x "$ROOT/target/v2-venv/bin/python" ]; then PYTHON="$ROOT/target/v2-venv/bin/python"; fi
if [ -f "$ROOT/target/v2/release/agentfence-mcp-server" ]; then
  "$PYTHON" -m demo.run_v2_dashboard --harness-check \
    --harness-entry "$ENTRY" --harness-node "$NODE_BIN" \
    --harness-home "$HOME_DIR" --bin-dir target/v2/release
else
  echo "next: build target/v2, then run the --harness-check command with these paths"
fi
