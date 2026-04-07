#!/usr/bin/env bash
# Build a portable zip of the Claude Code plugin for sharing or offline install.
# Output: apps/claude-code-plugin/dist/powermem-claude-code-plugin-<version>.zip
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST="${PLUGIN_ROOT}/dist"

VERSION="$(python3 -c "import json; print(json.load(open('${PLUGIN_ROOT}/.claude-plugin/plugin.json'))['version'])")"
ZIP_NAME="powermem-claude-code-plugin-${VERSION}.zip"
ZIP_PATH="${DIST}/${ZIP_NAME}"

if ! command -v zip >/dev/null 2>&1; then
  echo "error: 'zip' not found. Install zip (e.g. brew install zip) or zip manually." >&2
  exit 1
fi

bash "${PLUGIN_ROOT}/scripts/build-hook-binaries.sh"

mkdir -p "${DIST}"
TMP="$(mktemp -d)"
cleanup() { rm -rf "${TMP}"; }
trap cleanup EXIT

STAGE="${TMP}/powermem-claude-code-plugin"
mkdir -p "${STAGE}"

cp -R "${PLUGIN_ROOT}/.claude-plugin" "${STAGE}/"
cp "${PLUGIN_ROOT}/.mcp.json" "${STAGE}/"
[[ -d "${PLUGIN_ROOT}/config" ]] && cp -R "${PLUGIN_ROOT}/config" "${STAGE}/"
cp -R "${PLUGIN_ROOT}/hooks" "${STAGE}/"
chmod +x "${STAGE}/hooks/run-hook.sh" 2>/dev/null || true
cp -R "${PLUGIN_ROOT}/skills" "${STAGE}/"
[[ -d "${PLUGIN_ROOT}/watcher" ]] && cp -R "${PLUGIN_ROOT}/watcher" "${STAGE}/"
[[ -f "${PLUGIN_ROOT}/README.md" ]] && cp "${PLUGIN_ROOT}/README.md" "${STAGE}/"
[[ -f "${PLUGIN_ROOT}/CHANGELOG.md" ]] && cp "${PLUGIN_ROOT}/CHANGELOG.md" "${STAGE}/"
[[ -f "${PLUGIN_ROOT}/go.mod" ]] && cp "${PLUGIN_ROOT}/go.mod" "${STAGE}/"
[[ -d "${PLUGIN_ROOT}/cmd" ]] && cp -R "${PLUGIN_ROOT}/cmd" "${STAGE}/"
[[ -d "${PLUGIN_ROOT}/scripts" ]] && cp -R "${PLUGIN_ROOT}/scripts" "${STAGE}/"

( cd "${TMP}" && zip -r "${ZIP_PATH}" "powermem-claude-code-plugin" -x "*.DS_Store" -x "*__pycache__*" -x "*.pyc" )

echo "Packaged: ${ZIP_PATH}"
ls -lh "${ZIP_PATH}"
