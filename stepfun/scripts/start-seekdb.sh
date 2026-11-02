#!/usr/bin/env bash
# Start a local seekdb instance for the knowledge base app.
#
#   bash scripts/start-seekdb.sh [seekdb-binary] [port] [data-dir]
#
# The server must be built from the 2026-competition branch of oceanbase/seekdb:
# seekdb-js stores a collection in an internal table (c$v2$<id>) that the
# official 1.4.0 release package does not provide.
set -euo pipefail

BIN=${1:-}
PORT=${2:-2881}
DATA_DIR=${3:-/tmp/seekdb-stepfun}

if [[ -z "$BIN" ]]; then
  echo "usage: bash scripts/start-seekdb.sh <seekdb-binary> [port] [data-dir]" >&2
  exit 1
fi
if [[ ! -x "$BIN" ]]; then
  echo "seekdb binary not executable: $BIN" >&2
  exit 1
fi

mkdir -p "$DATA_DIR"
echo "starting seekdb on port $PORT with data dir $DATA_DIR"
exec "$BIN" --nodaemon \
  --base-dir="$DATA_DIR" \
  --port="$PORT" \
  --parameter datafile_size=2G \
  --parameter log_disk_size=3G \
  --parameter memory_limit=4G
