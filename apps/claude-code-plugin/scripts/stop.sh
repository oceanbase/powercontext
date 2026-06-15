#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
. "$SCRIPT_DIR/common.sh"

base_url=$(runtime_base_url)
stopped=0
stopped_pids=" "

stop_process() {
  pid=$1
  kill "$pid" 2>/dev/null || true
  sleep 2
  if kill -0 "$pid" 2>/dev/null; then
    echo "Server still running; sending SIGKILL"
    kill -9 "$pid" 2>/dev/null || true
  fi
  stopped=1
  stopped_pids="${stopped_pids}${pid} "
}

if pid_alive; then
  pid=$(managed_pid)
  echo "Stopping PowerMem server PID $pid"
  stop_process "$pid"
else
  if [ -f "$(managed_pid_file)" ]; then
    echo "Removing stale managed server PID file: $(managed_pid_file)"
    remove_managed_pid_files
  fi
fi

port=$(local_port_from_base_url "$base_url" || true)
if [ -n "$port" ]; then
  for pid in $(powermem_server_pids_for_port "$port"); do
    case "$stopped_pids" in
      *" $pid "*) continue ;;
    esac
    echo "Stopping orphaned PowerMem server PID $pid on port $port"
    stop_process "$pid"
  done
fi

if [ "$stopped" = "1" ]; then
  remove_managed_pid_files
else
  echo "No managed PowerMem server is running."
fi
