#!/usr/bin/env bash
# Apply the parser-layer change on a machine that has no git, then check the result.
#
#   bash scripts/apply_without_git.sh /path/to/seekdb [--revert]
#   PATCH=/path/to/other.patch bash scripts/apply_without_git.sh /path/to/seekdb
#
# Needs only patch(1) and grep. Verified with GNU patch 2.7.6 (CentOS 7 ships 2.7.1,
# which also works). The change touches exactly two files.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKDIR="$(cd "$HERE/.." && pwd)"
TARGET="${1:-}"
MODE="${2:-apply}"
PATCH="${PATCH:-$WORKDIR/agent/code/author-fix.patch}"

die() { echo "error: $*" >&2; exit 1; }

[ -n "$TARGET" ] || die "usage: bash scripts/apply_without_git.sh /path/to/seekdb [--revert]"
[ -d "$TARGET" ] || die "$TARGET is not a directory"
[ -f "$PATCH" ] || die "missing patch file: $PATCH"
command -v patch >/dev/null || die "patch(1) is missing: yum install -y patch"
command -v grep >/dev/null || die "grep is missing"

HEADER="src/query/api/query/parser/ob_item_type.h"
GRAMMAR="src/sql/parser/sql_parser_mysql_mode.y"

cd "$TARGET"
[ -f "$HEADER" ] || die "$TARGET does not look like a seekdb tree ($HEADER missing)"

if [ "$MODE" = "--revert" ]; then
  echo "==> reverting"
  patch -p1 -R --dry-run < "$PATCH" >/dev/null 2>&1 || die "the patch is not applied here"
  patch -p1 -R < "$PATCH"
  echo "reverted"
  exit 0
fi

if grep -q "T_OP_ARRAY_INDEX" "$HEADER" 2>/dev/null; then
  echo "==> already applied (T_OP_ARRAY_INDEX already present in $HEADER)"
else
  echo "==> dry run"
  patch -p1 --dry-run < "$PATCH" || die "patch does not apply — is this checkout at 108fe22?"
  echo "==> applying"
  patch -p1 < "$PATCH"
fi

echo
echo "==> verification (static only; nothing is built here)"
grep -n "T_OP_ARRAY_INDEX" "$HEADER" | sed 's/^/    /'
grep -n -A2 "column_ref '\[' expr '\]'" "$GRAMMAR" | sed 's/^/    /' || \
  die "the grammar production is missing — the patch did not land"

cat <<'MSG'

==> scope of this change
    does:      the parser can build an ARRAY element-access node (T_OP_ARRAY_INDEX)
    does not:  resolver path, runtime expression, element-wise INSERT/UPDATE/DELETE
    so `col[i]` still fails after parsing, and the acceptance cases stay red until the
    follow-up in agent/instruction-2.md is implemented and seekdb is rebuilt.

==> if this machine can build seekdb (see scripts/preflight_linux.sh)
    ./build.py init && ./build.py build seekdb
    MINIMAX_WORKTREE=$PWD bash /path/to/minimax/scripts/verify_on_linux.sh
MSG
