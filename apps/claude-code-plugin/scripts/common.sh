#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
PLUGIN_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

powermem_data_dir() {
  printf '%s\n' "$HOME/.powermem"
}

DATA_DIR="${POWERMEM_DATA_DIR:-$(powermem_data_dir)}"
ENV_FILE="${POWERMEM_ENV_FILE:-$DATA_DIR/.env}"
RUNTIME_FILE="${POWERMEM_RUNTIME_FILE:-$DATA_DIR/runtime.env}"
PID_FILE="${POWERMEM_PID_FILE:-$DATA_DIR/powermem.pid}"
LEGACY_PID_FILE="$DATA_DIR/server.pid"
LOG_FILE="${POWERMEM_LOG_FILE:-$DATA_DIR/powermem-server.log}"
VENV_DIR="${POWERMEM_VENV_DIR:-$DATA_DIR/venv}"

mkdir -p "$DATA_DIR"

choose_python() {
  if [ -n "${POWERMEM_INIT_PYTHON:-}" ]; then
    candidates=$POWERMEM_INIT_PYTHON
  else
    candidates="python3.11 python3 python"
  fi
  for candidate in $candidates; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
      then
        command -v "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

python_version() {
  "$1" - <<'PY'
import sys
print(".".join(map(str, sys.version_info[:3])))
PY
}

runtime_base_url() {
  if [ -n "${POWERMEM_BASE_URL:-}" ]; then
    printf '%s\n' "$POWERMEM_BASE_URL"
    return
  fi
  if [ -f "$RUNTIME_FILE" ]; then
    # shellcheck disable=SC1090
    . "$RUNTIME_FILE"
    if [ -n "${POWERMEM_BASE_URL:-}" ]; then
      printf '%s\n' "$POWERMEM_BASE_URL"
      return
    fi
  fi
  printf '%s\n' "http://localhost:8848"
}

write_runtime_base_url() {
  base_url=$1
  tmp="$RUNTIME_FILE.tmp"
  {
    printf 'POWERMEM_BASE_URL=%s\n' "$base_url"
    printf 'POWERMEM_ENV_FILE=%s\n' "$ENV_FILE"
  } > "$tmp"
  mv "$tmp" "$RUNTIME_FILE"
}

health_url() {
  base_url=$(printf '%s' "$1" | sed 's:/*$::')
  printf '%s/api/v1/system/health\n' "$base_url"
}

is_healthy() {
  url=$(health_url "$1")
  curl -fsS -m 5 "$url" 2>/dev/null | grep -q '"healthy"'
}

pid_alive() {
  pid=$(managed_pid) || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  pid_is_powermem_server "$pid" || return 1
  pid_uses_env_file "$pid" || return 1
}

managed_pid_file() {
  if [ -f "$PID_FILE" ]; then
    printf '%s\n' "$PID_FILE"
    return
  fi
  if [ -z "${POWERMEM_PID_FILE:-}" ] && [ -f "$LEGACY_PID_FILE" ]; then
    printf '%s\n' "$LEGACY_PID_FILE"
    return
  fi
  printf '%s\n' "$PID_FILE"
}

managed_pid() {
  file=$(managed_pid_file)
  [ -f "$file" ] || return 1
  pid=$(cat "$file" 2>/dev/null | tr -d '[:space:]' || true)
  case "$pid" in
    ""|*[!0-9]*) return 1 ;;
  esac
  printf '%s\n' "$pid"
}

write_managed_pid() {
  printf '%s\n' "$1" > "$PID_FILE"
  if [ -z "${POWERMEM_PID_FILE:-}" ] && [ "$LEGACY_PID_FILE" != "$PID_FILE" ]; then
    rm -f "$LEGACY_PID_FILE" 2>/dev/null || true
  fi
}

remove_managed_pid_files() {
  file=$(managed_pid_file)
  rm -f "$file" 2>/dev/null || true
  if [ -z "${POWERMEM_PID_FILE:-}" ]; then
    rm -f "$PID_FILE" "$LEGACY_PID_FILE" 2>/dev/null || true
  fi
}

pid_is_powermem_server() {
  pid=$1
  args=$(process_args "$pid")
  [ -n "$args" ] || return 0
  printf '%s\n' "$args" | grep -q 'powermem-server'
}

process_args() {
  ps -p "$1" -o args= 2>/dev/null || ps -p "$1" -o command= 2>/dev/null || true
}

managed_base_url() {
  pid=$(managed_pid) || return 1
  args=$(process_args "$pid")
  [ -n "$args" ] || return 1

  host=127.0.0.1
  port=
  next=
  for arg in $args; do
    if [ "$next" = "host" ]; then
      host=$arg
      next=
      continue
    fi
    if [ "$next" = "port" ]; then
      port=$arg
      next=
      continue
    fi
    case "$arg" in
      --host=*) host=${arg#--host=} ;;
      --host) next=host ;;
      --port=*) port=${arg#--port=} ;;
      --port) next=port ;;
    esac
  done

  case "$port" in
    ""|*[!0-9]*) return 1 ;;
  esac
  case "$host" in
    ""|"0.0.0.0"|"::"|"[::]") host=localhost ;;
  esac
  printf 'http://%s:%s\n' "$host" "$port"
}

pid_uses_env_file() {
  pid=$1
  environ="/proc/$pid/environ"
  [ -r "$environ" ] || return 0
  tr '\000' '\n' < "$environ" | grep -Fx "POWERMEM_ENV_FILE=$ENV_FILE" >/dev/null
}

venv_python() {
  printf '%s/bin/python\n' "$VENV_DIR"
}

venv_powermem_server() {
  printf '%s/bin/powermem-server\n' "$VENV_DIR"
}

port_free() {
  py=${POWERMEM_BOOTSTRAP_PYTHON:-python3}
  "$py" - "$1" <<'PY'
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket()
try:
    sock.bind(("127.0.0.1", port))
except OSError:
    sys.exit(1)
finally:
    sock.close()
PY
}

describe_port() {
  port=$1
  echo "Port $port is occupied."
  if command -v lsof >/dev/null 2>&1; then
    echo "lsof -nP -iTCP:$port -sTCP:LISTEN:"
    lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
  else
    echo "lsof is not installed; run: netstat -anp | grep ':$port'"
  fi
}

find_free_port() {
  start=${1:-8848}
  end=${2:-8899}
  port=$start
  while [ "$port" -le "$end" ]; do
    if port_free "$port"; then
      printf '%s\n' "$port"
      return 0
    fi
    port=$((port + 1))
  done
  return 1
}
