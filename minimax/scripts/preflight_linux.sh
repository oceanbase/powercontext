#!/usr/bin/env bash
# Can this machine build seekdb at all? Print the facts and a verdict.
#
#   bash scripts/preflight_linux.sh
#
# seekdb builds with the Bazel version pinned in .bazelversion (9.3.0) and downloads
# its own LLVM 18 toolchain, so the system compiler does not have to be modern — but
# the host C library, RAM and disk do matter. This script checks those and says which
# requirement fails, instead of letting a build die 40 minutes in.

set -uo pipefail

ok=0
problems=0

say() { printf '%s\n' "$*"; }
pass() { say "  [ok]   $*"; }
warn() { say "  [warn] $*"; }
fail() { say "  [FAIL] $*"; problems=$((problems + 1)); }

say "== host =="
[ -f /etc/os-release ] && . /etc/os-release && say "  ${PRETTY_NAME:-unknown} ($(uname -m))"
say "  kernel $(uname -r)"

say "== C library =="
glibc=""
raw="$(getconf GNU_LIBC_VERSION 2>/dev/null || true)"
case "$raw" in
  glibc\ *) glibc="${raw#glibc }" ;;
esac
if [ -z "$glibc" ]; then
  line="$(ldd --version 2>/dev/null | head -1 || true)"
  case "$line" in
    *GLIBC*|*glibc*|*"GNU C Library"*) glibc="$(printf '%s' "$line" | awk '{print $NF}')" ;;
  esac
fi
case "$glibc" in
  [0-9]*.[0-9]*) : ;;
  *) glibc="" ;;
esac
if [ -z "$glibc" ]; then
  warn "could not determine the glibc version (not a glibc system?)"
else
  major="${glibc%%.*}"; minor="${glibc#*.}"
  if [ "$major" -gt 2 ] || { [ "$major" -eq 2 ] && [ "$minor" -ge 28 ]; }; then
    pass "glibc $glibc (bazel and the LLVM 18 toolchain need >= 2.28)"
  else
    fail "glibc $glibc is too old: bazel 9 and the LLVM 18 toolchain normally need >= 2.28"
    say "         CentOS 7 ships 2.17; use a newer distribution or a container instead."
  fi
fi

say "== CPU and memory =="
cores="$(nproc 2>/dev/null || echo '?')"
if [ "$cores" = "?" ] || [ "$cores" -lt 4 ]; then
  warn "$cores cores: builds will be slow"
else
  pass "$cores cores"
fi
mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)"
mem_gb=$((mem_kb / 1024 / 1024))
if [ "$mem_gb" -lt 12 ]; then
  fail "$mem_gb GB RAM is below the practical minimum (~16 GB; a link step peaks high)"
elif [ "$mem_gb" -lt 16 ]; then
  warn "$mem_gb GB RAM: expect very long link steps or OOM; close everything else"
else
  pass "$mem_gb GB RAM"
fi

say "== disk =="
free_gb="$(df -Pk . 2>/dev/null | awk 'NR==2 {print int($4/1024/1024)}')"
if [ -z "$free_gb" ]; then
  warn "could not read free space"
elif [ "$free_gb" -lt 50 ]; then
  fail "$free_gb GB free: the build tree plus the LLVM toolchain needs ~50 GB"
else
  pass "$free_gb GB free"
fi

say "== tools =="
for tool in python3 tar gzip curl; do
  if command -v "$tool" >/dev/null 2>&1; then
    pass "$tool"
  else
    fail "$tool is missing"
  fi
done
if command -v git >/dev/null 2>&1; then
  pass "git $(git --version | awk '{print $3}')"
else
  warn "git is missing — use scripts/apply_without_git.sh instead of the git steps"
fi
if command -v bazel >/dev/null 2>&1; then
  say "  (a bazel on PATH is ignored: ./build.py downloads the pinned one)"
fi

say "== network =="
if command -v curl >/dev/null 2>&1; then
  if curl -sS -m 8 -o /dev/null https://github.com 2>/dev/null; then
    pass "github.com reachable"
  else
    fail "github.com unreachable: the build downloads bazel, toolchain and dependencies"
    say "         if this host needs a proxy, export HTTPS_PROXY and re-run"
  fi
fi

say
if [ "$problems" -eq 0 ]; then
  say "VERDICT: this machine looks able to build seekdb."
  say "  next: ./build.py init && ./build.py build seekdb"
  say "        then MINIMAX_WORKTREE=\$PWD bash scripts/verify_on_linux.sh"
  exit 0
fi
say "VERDICT: $problems requirement(s) not met — a build here will most likely fail."
say "  options:"
say "    1) a VM/container on a newer distribution (Ubuntu 22.04+, >=16 GB RAM, >=50 GB disk)"
say "    2) build on CI (GitHub Actions ubuntu-22.04) and bring back the binary"
say "    3) keep the submission as is: docs/代码与测试.md already states the patch is uncompiled"
exit 1
