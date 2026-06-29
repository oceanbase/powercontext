#!/usr/bin/env bash
# Start/stop/status for powermem-server with durable persistence on macOS.
# Usage: bash scripts/powermem-server-service.sh {start|stop|status|restart|uninstall}
set -euo pipefail

LABEL="ai.powermem.server"
DEPRECATED_LABEL="ai.powermem.server.v2"
BACKEND_URL="${POWERMEM_BACKEND_URL:-http://localhost:8848}"
PORT="${POWERMEM_PORT:-8848}"
LOG_OUT="/tmp/powermem-server.launchd.log"
LOG_ERR="/tmp/powermem-server.launchd.err"
PID_FILE="/tmp/powermem-server.pid"
INSTALLED_LAUNCH_SCRIPT="$HOME/bin/powermem-server-launch.sh"
UV="${UV:-uv}"
UV_PYTHON="${UV_PYTHON:-3.11}"
PYTHON_CMD=("${UV}" run --no-project --python "${UV_PYTHON}" python)

resolve_repo_root() {
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cd "$script_dir/.." && pwd
}

REPO_ROOT="$(resolve_repo_root)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCH_SCRIPT="$SCRIPT_DIR/powermem-server-launch.sh"
SERVER_BIN="$REPO_ROOT/.venv/bin/powermem-server"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
SYSTEMD_UNIT="powermem-server.service"
SYSTEMD_PATH="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/${SYSTEMD_UNIT}"

log() { echo "[powermem-server-service] $*"; }

is_healthy() {
  curl -sf -m 5 "${BACKEND_URL}/api/v1/system/health" >/dev/null 2>&1
}

find_server_pid() {
  pgrep -f "powermem-server.*--port ${PORT}" 2>/dev/null | head -1 || true
}

ensure_launch_script() {
  if [ ! -f "$LAUNCH_SCRIPT" ]; then
    log "Missing launch wrapper $LAUNCH_SCRIPT"
    exit 1
  fi
  chmod +x "$LAUNCH_SCRIPT"
  mkdir -p "$HOME/bin"
  cp "$LAUNCH_SCRIPT" "$INSTALLED_LAUNCH_SCRIPT"
  chmod +x "$INSTALLED_LAUNCH_SCRIPT"
}

install_launchagent() {
  ensure_launch_script
  mkdir -p "$HOME/Library/LaunchAgents"
  "${PYTHON_CMD[@]}" - "$PLIST_PATH" "$REPO_ROOT" "$INSTALLED_LAUNCH_SCRIPT" "$LOG_OUT" "$LOG_ERR" "$LABEL" <<'PY'
import plistlib, sys
path, repo_root, launch_script, log_out, log_err, label = sys.argv[1:7]
data = {
    "Label": label,
    "ProgramArguments": [launch_script],
    "WorkingDirectory": repo_root,
    "EnvironmentVariables": {
        "POWERMEM_REPO_ROOT": repo_root,
    },
    "RunAtLoad": True,
    "KeepAlive": True,
    "ThrottleInterval": 30,
    "ProcessType": "Background",
    "StandardOutPath": log_out,
    "StandardErrorPath": log_err,
}
with open(path, "wb") as f:
    plistlib.dump(data, f)
print(f"Wrote {path}")
PY
}

remove_deprecated_launchagent() {
  launchctl bootout "gui/$(id -u)/${DEPRECATED_LABEL}" 2>/dev/null || true
  launchctl remove "$DEPRECATED_LABEL" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/${DEPRECATED_LABEL}.plist"
}

unload_launchagent() {
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  launchctl remove "$LABEL" 2>/dev/null || true
}

enable_launchagent() {
  # A previously failed bootstrap can leave the label disabled in launchd; bootstrap then
  # returns "Bootstrap failed: 5: Input/output error" until re-enabled.
  launchctl enable "gui/$(id -u)/${LABEL}" 2>/dev/null || true
}

launchagent_loaded() {
  launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1
}

bootstrap_launchagent() {
  remove_deprecated_launchagent
  unload_launchagent
  install_launchagent
  enable_launchagent
  if launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"; then
    log "Registered LaunchAgent (bootstrap) label=${LABEL}"
    return 0
  fi
  log "launchctl bootstrap failed for label=${LABEL} — wrapper installed at $INSTALLED_LAUNCH_SCRIPT"
  return 1
}

start_detached() {
  ensure_launch_script
  if [ ! -x "$SERVER_BIN" ]; then
    log "Missing $SERVER_BIN — run SETUP.md section 0.2 first"
    exit 1
  fi
  POWERMEM_REPO_ROOT="$REPO_ROOT" nohup "$INSTALLED_LAUNCH_SCRIPT" >> "$LOG_OUT" 2>> "$LOG_ERR" &
  echo $! > "$PID_FILE"
  disown 2>/dev/null || true
  log "Started detached PID $(cat "$PID_FILE") (nohup; survives terminal exit)"
}

wait_for_health() {
  local attempts="${1:-45}"
  for _ in $(seq 1 "$attempts"); do
    if is_healthy; then
      return 0
    fi
    sleep 2
  done
  return 1
}

cmd_start() {
  if is_healthy; then
    log "Backend already healthy at $BACKEND_URL"
    find_server_pid | xargs -I{} log "Running PID {}"
    return 0
  fi

  lsof -ti:"$PORT" | xargs kill -9 2>/dev/null || true
  pkill -f "powermem-server.*${PORT}" 2>/dev/null || true
  sleep 1

  if [ "$(uname -s)" = "Darwin" ] && command -v launchctl >/dev/null 2>&1; then
    if bootstrap_launchagent && launchagent_loaded; then
      if wait_for_health 45; then
        log "Backend healthy via LaunchAgent"
        return 0
      fi
      log "LaunchAgent registered but backend not healthy — check $LOG_ERR"
      launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
    else
      log "LaunchAgent bootstrap failed; falling back to detached process"
    fi
  fi

  start_detached
  if wait_for_health 60; then
    log "Backend healthy via detached process"
    log "WARNING: detached mode does not auto-restart on login — prefer LaunchAgent"
    return 0
  fi

  log "Backend failed to become healthy — check $LOG_OUT and $LOG_ERR"
  tail -30 "$LOG_ERR" 2>/dev/null || true
  exit 1
}

cmd_stop() {
  remove_deprecated_launchagent
  unload_launchagent
  if [ -f "$PID_FILE" ]; then
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    rm -f "$PID_FILE"
  fi
  lsof -ti:"$PORT" | xargs kill -9 2>/dev/null || true
  pkill -f "powermem-server.*${PORT}" 2>/dev/null || true
  log "Stopped"
}

cmd_status() {
  local pid mode="none"
  pid="$(find_server_pid)"
  if launchagent_loaded; then
    mode="LaunchAgent"
  elif [ -n "$pid" ]; then
    mode="detached"
  elif [ "$(uname -s)" = "Linux" ] && command -v systemctl >/dev/null 2>&1 \
    && systemctl --user is-active "$SYSTEMD_UNIT" >/dev/null 2>&1; then
    mode="systemd"
  fi
  if is_healthy; then
    log "healthy at $BACKEND_URL (PID ${pid:-unknown}, mode ${mode})"
    return 0
  fi
  log "not running or unhealthy (PID ${pid:-none}, mode ${mode})"
  return 1
}

remove_systemd_service() {
  if ! command -v systemctl >/dev/null 2>&1; then
    return 0
  fi
  systemctl --user stop "$SYSTEMD_UNIT" 2>/dev/null || true
  systemctl --user disable "$SYSTEMD_UNIT" 2>/dev/null || true
  if [ -f "$SYSTEMD_PATH" ]; then
    rm -f "$SYSTEMD_PATH"
    log "Removed $SYSTEMD_PATH"
  fi
  systemctl --user daemon-reload 2>/dev/null || true
}

remove_service_artifacts() {
  if [ "$(uname -s)" = "Darwin" ]; then
    if [ -f "$PLIST_PATH" ]; then
      rm -f "$PLIST_PATH"
      log "Removed $PLIST_PATH"
    else
      log "LaunchAgent plist already absent"
    fi
    rm -f "$HOME/Library/LaunchAgents/${DEPRECATED_LABEL}.plist"
    if [ -f "$INSTALLED_LAUNCH_SCRIPT" ]; then
      rm -f "$INSTALLED_LAUNCH_SCRIPT"
      log "Removed $INSTALLED_LAUNCH_SCRIPT"
    else
      log "Launch wrapper already absent"
    fi
  elif [ "$(uname -s)" = "Linux" ]; then
    remove_systemd_service
  fi
  rm -f "$PID_FILE" "$LOG_OUT" "$LOG_ERR"
  log "Removed service logs and pid file"
}

cmd_uninstall() {
  log "Uninstalling powermem-server persistence service"
  cmd_stop
  remove_service_artifacts
  log "Service uninstalled (process stopped, persistence config removed)"
}

case "${1:-status}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_stop; sleep 1; cmd_start ;;
  status) cmd_status ;;
  uninstall) cmd_uninstall ;;
  *)
    echo "usage: $0 {start|stop|status|restart|uninstall}" >&2
    exit 1
    ;;
esac
