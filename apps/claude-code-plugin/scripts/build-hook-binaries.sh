#!/usr/bin/env bash
# Cross-compile powermem-hook for all supported platforms (requires Go 1.22+).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BIN="${PLUGIN_ROOT}/hooks/bin"
mkdir -p "${BIN}"
cd "${PLUGIN_ROOT}"

if ! command -v go >/dev/null 2>&1; then
  echo "error: Go is not installed. Install Go 1.22+ to build hook binaries." >&2
  exit 1
fi

build_one() {
  local goos=$1 goarch=$2
  local out="${BIN}/powermem-hook-${goos}-${goarch}"
  if [[ "${goos}" == "windows" ]]; then
    out="${out}.exe"
  fi
  echo "Building ${goos}/${goarch} -> ${out}"
  GOOS="${goos}" GOARCH="${goarch}" CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o "${out}" ./cmd/powermem-hook
}

build_one darwin amd64
build_one darwin arm64
build_one linux amd64
build_one linux arm64
build_one windows amd64

echo "Done. Binaries in ${BIN}/"
