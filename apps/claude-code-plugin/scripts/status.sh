#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
. "$SCRIPT_DIR/common.sh"

base_url=$(runtime_base_url)

echo "PowerMem Claude Code plugin status"
echo "Data dir: $DATA_DIR"
echo "Runtime file: $RUNTIME_FILE"
echo "Env file: $ENV_FILE"
echo "PID file: $(managed_pid_file)"
echo "Base URL: $base_url"

if BOOTSTRAP_PYTHON=$(choose_python 2>/dev/null); then
  echo "Bootstrap Python: $BOOTSTRAP_PYTHON ($(python_version "$BOOTSTRAP_PYTHON"))"
else
  echo "Bootstrap Python: missing Python >= 3.11"
fi

if [ -f "$ENV_FILE" ]; then
  echo "Config: present"
else
  echo "Config: missing"
fi

if pid_alive; then
  echo "Managed server PID: $(managed_pid)"
else
  echo "Managed server PID: not running"
fi

if [ -n "${POWERMEM_UV_BIN:-}" ] && command -v "$POWERMEM_UV_BIN" >/dev/null 2>&1; then
  uv_bin=$(command -v "$POWERMEM_UV_BIN")
elif command -v uv >/dev/null 2>&1; then
  uv_bin=$(command -v uv)
else
  uv_bin=""
fi

if [ -n "$uv_bin" ]; then
  echo "uv: $uv_bin ($("$uv_bin" --version 2>/dev/null || echo unknown))"
  echo "Backend launcher: uvx --from '${POWERMEM_INIT_PACKAGE:-powermem[server,seekdb]}' powermem-server"
else
  echo "uv: missing"
fi

if [ -d "$DATA_DIR/venv" ]; then
  echo "Legacy venv: $DATA_DIR/venv (unused by uvx init)"
fi

if is_healthy "$base_url"; then
  echo "Health: healthy"
else
  echo "Health: unavailable"
  case "$base_url" in
    http://localhost:*|http://127.0.0.1:*)
      port=$(printf '%s\n' "$base_url" | sed -E 's#^http://(localhost|127\.0\.0\.1):([0-9]+).*#\2#')
      case "$port" in
        *[!0-9]*|"") ;;
        *) describe_port "$port" ;;
      esac
      ;;
  esac
fi

if [ -f "$LOG_FILE" ]; then
  echo "Log: $LOG_FILE"
fi
