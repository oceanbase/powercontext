#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
. "$SCRIPT_DIR/common.sh"

if pid_alive; then
  pid=$(managed_pid)
  echo "Stopping PowerMem server PID $pid"
  kill "$pid" 2>/dev/null || true
  sleep 2
  if kill -0 "$pid" 2>/dev/null; then
    echo "Server still running; sending SIGKILL"
    kill -9 "$pid" 2>/dev/null || true
  fi
  remove_managed_pid_files
else
  if [ -f "$(managed_pid_file)" ]; then
    echo "Removing stale managed server PID file: $(managed_pid_file)"
    remove_managed_pid_files
  fi
  echo "No managed PowerMem server is running."
fi
