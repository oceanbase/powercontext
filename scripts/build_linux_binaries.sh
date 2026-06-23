#!/usr/bin/env bash
# Backwards-compatible wrapper for the CentOS 7 Linux amd64 release package.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export POWERMEM_BINARY_OS="${POWERMEM_BINARY_OS:-linux}"
export POWERMEM_BINARY_ARCH="${POWERMEM_BINARY_ARCH:-amd64}"
export POWERMEM_BINARY_FORMAT="${POWERMEM_BINARY_FORMAT:-tar.gz}"
export POWERMEM_BINARY_BUILD_NOTE="${POWERMEM_BINARY_BUILD_NOTE:-Built on CentOS 7 for broader glibc compatibility.}"

exec bash "${SCRIPT_DIR}/build_binary_package.sh"
