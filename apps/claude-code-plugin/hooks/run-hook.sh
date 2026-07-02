#!/bin/sh
# Select the correct native binary for macOS / Linux. Pass-through args (e.g. "poll" for file watcher).
ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
PLUGIN_ROOT=$(CDPATH= cd -- "$ROOT/.." && pwd)
DATA_DIR="${POWERMEM_DATA_DIR:-$HOME/.powermem}"
# Load an env file at lower priority than the current process environment.
# Priority: process env (user exports + Claude Code settings.json env injection)
#           > runtime.env > plugin config/runtime.env > Go binary defaults.
# runtime.env is a convenience default; explicit user config always wins.
load_env_file_lower_priority() {
  [ -f "$1" ] || return 0
  # Capture POWERMEM_* before sourcing, restore after — process env wins.
  # Heredoc (not pipe | while) keeps the loop in the current shell so exports stick.
  _pm_saved=$(env | grep '^POWERMEM_' 2>/dev/null || true)
  set -a
  # shellcheck disable=SC1090
  . "$1"
  set +a
  while IFS= read -r _pm_entry; do
    [ -n "$_pm_entry" ] || continue
    export "$_pm_entry" 2>/dev/null || true
  done << _PM_RESTORE_
$_pm_saved
_PM_RESTORE_
}
load_env_file_lower_priority "$DATA_DIR/runtime.env"
load_env_file_lower_priority "$PLUGIN_ROOT/config/runtime.env"
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
