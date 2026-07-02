#!/bin/sh
# Select the correct native binary for macOS / Linux. Pass-through args (e.g. "poll" for file watcher).
ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
PLUGIN_ROOT=$(CDPATH= cd -- "$ROOT/.." && pwd)
DATA_DIR="${POWERMEM_DATA_DIR:-$HOME/.powermem}"
load_env_file() {
  [ -f "$1" ] || return 0
  set -a
  # shellcheck disable=SC1090
  . "$1"
  set +a
}
load_env_file "$DATA_DIR/runtime.env"
load_env_file "$PLUGIN_ROOT/config/runtime.env"
# MCP-only mode: runtime.env sets POWERMEM_HOOK_DISABLED=1 so the native
# binary never runs (and never falls back to a stale POWERMEM_BASE_URL).
# Must be checked AFTER loading runtime.env so the marker takes effect.
if [ "${POWERMEM_HOOK_DISABLED:-0}" = "1" ]; then
  exit 0
fi
case "$(uname -s 2>/dev/null)" in
  Darwin) GOOS=darwin ;;
  Linux) GOOS=linux ;;
  *) exit 0 ;;
esac
case "$(uname -m 2>/dev/null)" in
  arm64|aarch64) GOARCH=arm64 ;;
  x86_64|amd64) GOARCH=amd64 ;;
  *) GOARCH=amd64 ;;
esac
BIN="$ROOT/bin/powermem-hook-${GOOS}-${GOARCH}"
if [ ! -x "$BIN" ] && [ -f "$BIN" ]; then
  chmod +x "$BIN" 2>/dev/null || true
fi
if [ ! -f "$BIN" ]; then
  exit 0
fi
exec "$BIN" "$@"
