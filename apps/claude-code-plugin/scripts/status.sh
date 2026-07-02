#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
. "$SCRIPT_DIR/common.sh"

# --- Discover connection configuration ---
# Hook mode (REST):   runtime.env holds POWERMEM_BASE_URL (+ optional POWERMEM_API_KEY)
# MCP mode (http):    user-scope ~/.claude.json holds mcpServers.powermem.url
#                     (written via `claude mcp add --scope user`, survives plugin
#                     reinstalls; the plugin cache .mcp.json is volatile and unused)
# Both mode:          both sources populated
hook_url=""
if [ -f "$RUNTIME_FILE" ]; then
  # shellcheck disable=SC1090
  . "$RUNTIME_FILE"
  hook_url="${POWERMEM_BASE_URL:-}"
fi

mcp_url=""
mcp_file="${HOME:-}/.claude.json"
if [ -f "$mcp_file" ]; then
  if BOOTSTRAP_PYTHON=$(choose_python 2>/dev/null); then
    mcp_url=$("$BOOTSTRAP_PYTHON" - "$mcp_file" <<'PY' 2>/dev/null || true
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    srv = data.get("mcpServers", {}).get("powermem", {})
    print(srv.get("url", ""))
except Exception:
    pass
PY
)
  fi
fi

if [ -n "$hook_url" ] && [ -n "$mcp_url" ]; then
  mode="both"
elif [ -n "$hook_url" ]; then
  mode="hook"
elif [ -n "$mcp_url" ]; then
  mode="mcp"
else
  mode="none"
fi

# Derive a base URL for health checks. Prefer hook_url; fall back to MCP url
# with the trailing /mcp stripped.
health_url="$hook_url"
if [ -z "$health_url" ] && [ -n "$mcp_url" ]; then
  health_url=$(printf '%s' "$mcp_url" | sed -E 's#/mcp$##')
fi

echo "PowerMem Claude Code plugin status"
echo "Data dir: $DATA_DIR"
echo "Runtime file: $RUNTIME_FILE"
echo "Env file: $ENV_FILE"
echo "PID file: $(managed_pid_file)"
echo "Connection mode: $mode"
[ -n "$hook_url" ] && echo "Hook base URL: $hook_url"
[ -n "$mcp_url" ]  && echo "MCP URL: $mcp_url"

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
  case "$mode" in
    mcp)   echo "Managed server PID: not running (MCP-only mode, no local server expected)" ;;
    hook)  echo "Managed server PID: not running (hook mode against remote, no local server expected)" ;;
    both)  echo "Managed server PID: not running (remote mode, no local server expected)" ;;
    *)     echo "Managed server PID: not running" ;;
  esac
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

if [ -z "$health_url" ]; then
  echo "Health: no base URL configured (run /memory-powermem:init)"
else
  if is_healthy "$health_url"; then
    echo "Health: healthy ($health_url)"
  else
    echo "Health: unavailable ($health_url)"
    case "$health_url" in
      http://localhost:*|http://127.0.0.1:*)
        port=$(printf '%s\n' "$health_url" | sed -E 's#^http://(localhost|127\.0\.0\.1):([0-9]+).*#\2#')
        case "$port" in
          *[!0-9]*|"") ;;
          *) describe_port "$port" ;;
        esac
        ;;
    esac
  fi
fi

if [ -f "$LOG_FILE" ]; then
  echo "Log: $LOG_FILE"
fi
