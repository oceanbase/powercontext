#!/usr/bin/env bash
# Build standalone command binaries for release packaging.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python}"

detect_os() {
  case "$(uname -s)" in
    Linux*) echo "linux" ;;
    Darwin*) echo "macos" ;;
    MINGW*|MSYS*|CYGWIN*) echo "windows" ;;
    *) echo "unknown" ;;
  esac
}

detect_arch() {
  case "$(uname -m)" in
    x86_64|amd64|AMD64) echo "amd64" ;;
    arm64|aarch64) echo "aarch64" ;;
    *) uname -m ;;
  esac
}

TARGET_OS="${POWERMEM_BINARY_OS:-$(detect_os)}"
TARGET_ARCH="${POWERMEM_BINARY_ARCH:-$(detect_arch)}"
ARCHIVE_FORMAT="${POWERMEM_BINARY_FORMAT:-tar.gz}"
BUILD_NOTE="${POWERMEM_BINARY_BUILD_NOTE:-Built on GitHub Actions ${TARGET_OS} ${TARGET_ARCH}.}"

if [[ "${TARGET_OS}" == "unknown" ]]; then
  echo "error: unable to detect target OS; set POWERMEM_BINARY_OS" >&2
  exit 1
fi

DIST="${ROOT}/dist-binaries"
BUILD="${ROOT}/build/binaries-${TARGET_OS}-${TARGET_ARCH}"
ENTRYPOINTS="${BUILD}/entrypoints"
BIN_DIR="${DIST}/bin"
EXE_SUFFIX=""

if [[ "${TARGET_OS}" == "windows" ]]; then
  EXE_SUFFIX=".exe"
fi

cd "${ROOT}"

VERSION="$("${PYTHON}" - <<'PY'
import tomllib
with open("pyproject.toml", "rb") as f:
    print(tomllib.load(f)["project"]["version"])
PY
)"

rm -rf "${BUILD}" "${DIST}"
mkdir -p "${ENTRYPOINTS}" "${BIN_DIR}"

cat > "${ENTRYPOINTS}/powermem_cli_entry.py" <<'PY'
from powermem.cli.main import cli

if __name__ == "__main__":
    cli()
PY

cat > "${ENTRYPOINTS}/powermem_server_entry.py" <<'PY'
from server.cli.server import server

if __name__ == "__main__":
    server()
PY

cat > "${ENTRYPOINTS}/powermem_mcp_entry.py" <<'PY'
from powermem.mcp.server import main

if __name__ == "__main__":
    main()
PY

COMMON_OPTS=(
  --onefile
  --clean
  --noconfirm
  --distpath "${BIN_DIR}"
  --workpath "${BUILD}/pyinstaller"
  --specpath "${BUILD}/specs"
  --collect-all powermem
  --collect-all server
  --collect-all fastmcp
  --collect-all pyobvector
  --copy-metadata powermem
  --copy-metadata pydantic
  --copy-metadata pydantic-settings
  --copy-metadata fastmcp
  --copy-metadata pyobvector
)

build_binary() {
  local name="$1"
  local entrypoint="$2"
  local output="${BIN_DIR}/${name}${EXE_SUFFIX}"

  echo "Building ${name} for ${TARGET_OS}-${TARGET_ARCH}"
  "${PYTHON}" -m PyInstaller "${COMMON_OPTS[@]}" --name "${name}" "${entrypoint}"
  chmod +x "${output}" 2>/dev/null || true

  if command -v file >/dev/null 2>&1; then
    file "${output}"
  fi
  if [[ "${TARGET_OS}" == "linux" ]] && command -v ldd >/dev/null 2>&1; then
    ldd "${output}"
  fi
}

build_binary powermem "${ENTRYPOINTS}/powermem_cli_entry.py"
build_binary powermem-server "${ENTRYPOINTS}/powermem_server_entry.py"
build_binary powermem-mcp "${ENTRYPOINTS}/powermem_mcp_entry.py"

PACKAGE_BASENAME="powermem-${VERSION}-${TARGET_OS}-${TARGET_ARCH}"
PACKAGE_DIR="${DIST}/${PACKAGE_BASENAME}"
mkdir -p "${PACKAGE_DIR}/bin"

cp -av \
  "${BIN_DIR}/powermem${EXE_SUFFIX}" \
  "${BIN_DIR}/powermem-server${EXE_SUFFIX}" \
  "${BIN_DIR}/powermem-mcp${EXE_SUFFIX}" \
  "${PACKAGE_DIR}/bin/"

cat > "${PACKAGE_DIR}/README.md" <<EOF
# PowerMem ${TARGET_OS} ${TARGET_ARCH} binaries

${BUILD_NOTE}

Included commands:

- powermem
- powermem-server
- powermem-mcp

Version: ${VERSION}
EOF

case "${ARCHIVE_FORMAT}" in
  tar.gz)
    ARCHIVE_FILE="${DIST}/${PACKAGE_BASENAME}-binaries.tar.gz"
    ( cd "${DIST}" && tar -czf "${PACKAGE_BASENAME}-binaries.tar.gz" "${PACKAGE_BASENAME}" )
    ;;
  zip)
    ARCHIVE_FILE="${DIST}/${PACKAGE_BASENAME}-binaries.zip"
    ( cd "${DIST}" && "${PYTHON}" -m zipfile -c "${PACKAGE_BASENAME}-binaries.zip" "${PACKAGE_BASENAME}" )
    ;;
  *)
    echo "error: unsupported POWERMEM_BINARY_FORMAT=${ARCHIVE_FORMAT}" >&2
    exit 1
    ;;
esac

"${PYTHON}" - "${ARCHIVE_FILE}" > "${ARCHIVE_FILE}.sha256" <<'PY'
import hashlib
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
print(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
PY

echo "Built ${TARGET_OS}-${TARGET_ARCH} binaries:"
ls -lh "${BIN_DIR}" "${ARCHIVE_FILE}" "${ARCHIVE_FILE}.sha256"
