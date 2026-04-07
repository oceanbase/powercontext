#!/bin/sh
# Select the correct native binary for macOS / Linux. Pass-through args (e.g. "poll" for file watcher).
ROOT=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
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
