#!/usr/bin/env bash
# Launch wrapper for powermem-server. macOS launchd cannot bootstrap a LaunchAgent
# whose ProgramArguments point directly at a venv Python entry script; install this
# script to ~/bin via powermem-server-service.sh and set POWERMEM_REPO_ROOT.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${POWERMEM_REPO_ROOT:-$(cd "$SCRIPT_DIR/.." && pwd)}"
SERVER_BIN="$REPO_ROOT/.venv/bin/powermem-server"
HOST="${POWERMEM_HOST:-0.0.0.0}"
PORT="${POWERMEM_PORT:-8848}"

if [ ! -x "$SERVER_BIN" ]; then
  echo "[powermem-server-launch] Missing $SERVER_BIN — run SETUP.md section 0.2 first (POWERMEM_REPO_ROOT=${REPO_ROOT})" >&2
  exit 1
fi

cd "$REPO_ROOT"
exec "$SERVER_BIN" --host "$HOST" --port "$PORT"
