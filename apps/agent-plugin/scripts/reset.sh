#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
. "$SCRIPT_DIR/common.sh"

"$SCRIPT_DIR/stop.sh"

if [ "${POWERMEM_RESET_CONFIRM:-}" != "delete" ]; then
  echo "Refusing to delete plugin data without confirmation."
  echo "Run with POWERMEM_RESET_CONFIRM=delete to remove: $DATA_DIR"
  exit 2
fi

echo "Deleting plugin data dir: $DATA_DIR"
rm -rf "$DATA_DIR"

