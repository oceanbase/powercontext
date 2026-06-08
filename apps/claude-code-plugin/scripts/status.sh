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
  echo "Managed server PID: $(cat "$PID_FILE")"
else
  echo "Managed server PID: not running"
fi

if [ -x "$(venv_python)" ]; then
  PYTHON=$(venv_python)
  echo "Venv Python: $PYTHON ($(python_version "$PYTHON"))"
else
  echo "Venv Python: missing"
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
