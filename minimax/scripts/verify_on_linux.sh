#!/usr/bin/env bash
# On a Linux build machine: apply the parser-layer change, build seekdb, start a private
# instance, provision it and judge it with the same guard the submission documents.
#
#   MINIMAX_WORKTREE=/path/to/seekdb bash scripts/verify_on_linux.sh
#   PORT=2882 KEEP_PATCH=0 bash scripts/verify_on_linux.sh
#
# Requires: bazel toolchain via ./build.py, python3 with pymysql, ~40 GB free disk,
# ~16 GB RAM. Only $BASE_DIR (default ~/seekdb-patched) and the chosen port are used.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKDIR="$(cd "$HERE/.." && pwd)"
WORKTREE="${MINIMAX_WORKTREE:-}"
PORT="${PORT:-2882}"
BASE_DIR="${BASE_DIR:-$HOME/seekdb-patched}"
PATCH="${PATCH:-$WORKDIR/agent/code/author-fix.patch}"
KEEP_PATCH="${KEEP_PATCH:-1}"

die() { echo "error: $*" >&2; exit 1; }

[ -n "$WORKTREE" ] || die "set MINIMAX_WORKTREE to the seekdb checkout"
[ -d "$WORKTREE/.git" ] || die "$WORKTREE is not a git checkout"
[ -f "$PATCH" ] || die "missing patch $PATCH"

cd "$WORKTREE"

echo "==> 1/5 apply the parser-layer change"
if git apply --check "$PATCH" 2>/dev/null; then
  git apply "$PATCH"
  APPLIED=1
  echo "    applied"
else
  APPLIED=0
  echo "    patch does not apply (already present, or the checkout moved); continuing"
fi
git --no-pager diff --stat || true

echo "==> 2/5 build seekdb (first build is slow)"
BIN="$WORKTREE/build/bin/src/observer/seekdb"
if [ ! -x "$BIN" ]; then
  ./build.py init
  ./build.py build seekdb
else
  echo "    existing build found, skipping"
fi
[ -x "$BIN" ] || die "build did not produce $BIN"

echo "==> 3/5 start a private instance on port $PORT"
mkdir -p "$BASE_DIR"
if (command -v ss >/dev/null && ss -ltn 2>/dev/null | grep -q ":$PORT ") \
   || (command -v netstat >/dev/null && netstat -ltn 2>/dev/null | grep -q ":$PORT "); then
  echo "    port $PORT already in use, judging the running instance"
else
  "$BIN" --nodaemon --base-dir "$BASE_DIR" --port "$PORT" \
    --parameter memory_limit=4G --parameter datafile_size=2G \
    > "$BASE_DIR/seekdb.out" 2>&1 &
  echo "    pid $! (log: $BASE_DIR/seekdb.out)"
  sleep 20
fi

echo "==> 4/5 provision the scenario database and accounts"
cd "$WORKDIR"
python3 scripts/init_local_instance.py --port "$PORT"

echo "==> 5/5 judge: acceptance 5/5, compatibility 2/2, grammar PASS are the targets"
set +e
SEEKDB_REPO="$WORKTREE" MINIMAX_DB_PORT="$PORT" python3 run.py guard --stage after
status=$?
set -e

echo
if [ $status -eq 0 ]; then
  echo "RESULT: every check passed on the patched build"
else
  cat <<'MSG'
RESULT: not all checks passed. What each failure means:
  grammar-check fail  -> the patch is not in this checkout (git -C "$WORKTREE" diff)
  acceptance < 5      -> the resolver/runtime layer is missing; continue with
                         agent/instruction-2.md, rebuild, run this script again
  compatibility fail  -> a must-keep behaviour changed; do not merge as-is
MSG
fi

if [ "$KEEP_PATCH" = "0" ] && [ "${APPLIED:-0}" = "1" ]; then
  (cd "$WORKTREE" && git checkout -- .)
  echo "patch reverted (KEEP_PATCH=0)"
fi
echo "instance kept at $BASE_DIR; stop it with: pkill -f 'base-dir $BASE_DIR'"
exit $status
