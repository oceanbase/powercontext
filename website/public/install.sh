#!/usr/bin/env bash
# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -euo pipefail

VERSION=1.0.0
UV_VERSION=0.12.12
REGION=${POWERCONTEXT_INSTALL_REGION:-auto}
PYTHON_BIN=""
INDEX_URL=""
HOSTS=()
NO_HOSTS=false
SETUP_INPUT=/dev/stdin
TEMP_DIR=""
UV_BIN=""
GUIDE=https://powercontext.oceanbase.io/en/docs/get-started/configure-models/

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

cleanup() {
    if [[ -n "$TEMP_DIR" ]]; then
        rm -rf -- "$TEMP_DIR"
    fi
}

usage() {
    cat <<'EOF'
Install PowerContext on macOS or Linux. Python and uv need not be installed.

Usage: bash install.sh [--version VERSION] [--index-url URL] [--host HOST]... [--no-hosts]

  --version VERSION  Exact package version (default: 1.0.0).
                     Integrations use the matching powercontext-vVERSION tag.
  --region REGION   auto, cn, or global (default: POWERCONTEXT_INSTALL_REGION or auto).
  --index-url URL    HTTPS default package index for this installation.
                     Existing additional uv indexes still take precedence.
  --host HOST        Install this Agent integration; repeat for multiple hosts.
  --no-hosts         Install only the CLI and local Server.
  -h, --help         Show this help.

Without host options, an interactive terminal opens powercontext setup select.
Without a terminal, --host or --no-hosts is required.

Region priority: --region, POWERCONTEXT_INSTALL_REGION, named timezone, locale
territory, then global. No network location service is queried.
CN defaults: Tsinghua for PyPI, USTC for uv, NJU for Python. Global defaults use
PyPI and Astral's download channels. Unavailable automatic mirrors fall back to
official sources; explicit download settings are preserved without fallback.

Download configuration (independent of the package index):
  POWERCONTEXT_UV_INSTALLER_URL  HTTPS uv installer script URL.
  UV_DOWNLOAD_URL              uv release artifact directory.
  UV_INSTALLER_GITHUB_BASE_URL  GitHub mirror base URL for uv binaries.
  UV_PYTHON_INSTALL_MIRROR      Mirror of python-build-standalone downloads.
  UV_ASTRAL_MIRROR_URL          Astral mirror for uv versions supporting it.
These settings are passed to the official installer and uv without modification.
Existing uv and compatible Python installations are reused.
EOF
}

parse_args() {
    while (($#)); do
        case "$1" in
            --version|--region|--index-url|--host)
                (($# >= 2)) && [[ -n "$2" && "$2" != --* ]] || fail "$1 requires a value"
                case "$1" in
                    --version) VERSION=$2 ;;
                    --region) REGION=$2 ;;
                    --index-url) INDEX_URL=$2 ;;
                    --host) HOSTS+=(--host "$2") ;;
                esac
                shift 2
                ;;
            --no-hosts) NO_HOSTS=true; shift ;;
            -h|--help) usage; exit 0 ;;
            *) fail "Unknown option. Run bash install.sh --help." ;;
        esac
    done
    case "$REGION" in auto|cn|global) ;; *) fail "Use --region auto, cn, or global." ;; esac
    [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+)?$ ]] || fail "Use an exact package version."
    [[ "$VERSION" != 0.0.* ]] || fail "This installer supports releases starting at 0.1.0."
    if [[ -n "$INDEX_URL" ]]; then
        [[ "$INDEX_URL" == https://?* && "$INDEX_URL" != *[@\?#[:space:]]* ]] ||
            fail "Use an HTTPS index URL without credentials, query parameters, or fragments."
    fi
    if [[ -n "${POWERCONTEXT_UV_INSTALLER_URL:-}" ]]; then
        [[ "$POWERCONTEXT_UV_INSTALLER_URL" == https://?* && "$POWERCONTEXT_UV_INSTALLER_URL" != *[@\?#[:space:]]* ]] ||
            fail "POWERCONTEXT_UV_INSTALLER_URL must be an HTTPS URL without credentials, query parameters, or fragments."
    fi
    if [[ "$NO_HOSTS" == true && ${#HOSTS[@]} -gt 0 ]]; then
        fail "--host and --no-hosts cannot be combined."
    fi
    if [[ "$NO_HOSTS" == false && ${#HOSTS[@]} -eq 0 && ! -t 0 ]]; then
        if [[ -t 1 ]] && { : </dev/tty; } 2>/dev/null; then
            SETUP_INPUT=/dev/tty
        else
            fail "No interactive input. Pass --host HOST or --no-hosts."
        fi
    fi
}

download() {
    local timeout=${3:-30}
    if command -v curl >/dev/null 2>&1; then
        curl --fail --location --silent --show-error --connect-timeout 5 --max-time "$timeout" "$1" -o "$2"
    else
        timeout=${3:-15}
        wget --quiet --timeout="$timeout" --tries=1 "$1" -O "$2"
    fi
}

detect_region() {
    local timezone=${TZ:-} locale_name source=explicit
    if [[ "$REGION" == auto ]]; then
        source=timezone
        if [[ -z "$timezone" ]]; then
            timezone=$(readlink /etc/localtime 2>/dev/null || true)
            if [[ "$timezone" != */zoneinfo/* && -f /etc/timezone ]]; then
                IFS= read -r timezone </etc/timezone || true
            fi
        fi
        timezone=${timezone#:}
        timezone=${timezone##*/zoneinfo/}
        case "$timezone" in
            Asia/Shanghai|Asia/Chongqing|Asia/Chungking|Asia/Harbin|Asia/Urumqi|PRC) REGION=cn ;;
            Africa/*|America/*|Antarctica/*|Arctic/*|Asia/*|Atlantic/*|Australia/*|Europe/*|Indian/*|Pacific/*) REGION=global ;;
            *)
                source=locale
                locale_name=${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}
                if [[ -z "$locale_name" || "$locale_name" == C || "$locale_name" == C.* || "$locale_name" == POSIX ]]; then
                    if [[ "$(uname -s)" == Darwin ]]; then
                        locale_name=$(defaults read -g AppleLocale 2>/dev/null || true)
                    fi
                fi
                case "$locale_name" in
                    *_CN|*_CN.*|*_CN@*|*-CN|*-CN.*|*-CN@*) REGION=cn ;;
                    *) REGION=global ;;
                esac
                ;;
        esac
    fi
    printf 'Download region: %s (%s).\n' "$REGION" "$source"
}

has_uv_config_file() {
    [[ -n "${UV_CONFIG_FILE:-}" ]] && return 0
    case "${UV_NO_CONFIG:-}" in 1|true) return 1 ;; esac
    [[ -f "${XDG_CONFIG_HOME:-$HOME/.config}/uv/uv.toml" || -f /etc/uv/uv.toml ]] && return 0
    local directory
    local config_dirs=()
    IFS=: read -r -a config_dirs <<< "${XDG_CONFIG_DIRS:-/etc/xdg}"
    for directory in "${config_dirs[@]}"; do
        [[ -f "$directory/uv/uv.toml" ]] && return 0
    done
    return 1
}

has_uv_configuration() {
    # Let uv resolve its configuration and authentication; do not parse TOML in shell.
    [[ -n "${UV_DEFAULT_INDEX:-}${UV_INDEX:-}${UV_INDEX_URL:-}${UV_EXTRA_INDEX_URL:-}${UV_OFFLINE:-}${UV_NO_INDEX:-}${UV_FIND_LINKS:-}" ]] && return 0
    has_uv_config_file
}

url_available() {
    if command -v curl >/dev/null 2>&1; then
        curl --fail --location --silent --head --connect-timeout 5 --max-time 10 "$1" >/dev/null
    else
        wget --quiet --spider --timeout=10 --tries=1 "$1"
    fi
}

index_has_version() {
    if ! download "${1%/}/powercontext/" "$TEMP_DIR/index.html"; then
        printf 'Package index is unreachable: %s\n' "$1" >&2
        return 1
    fi
    if ! grep -Eq "powercontext-${VERSION//./\\.}(-|\.)" "$TEMP_DIR/index.html"; then
        printf 'Package index does not list PowerContext %s: %s\n' "$VERSION" "$1" >&2
        return 1
    fi
}

check_index() {
    if [[ -z "$INDEX_URL" ]] && has_uv_configuration; then
        printf '%s\n' 'Using existing uv configuration; uv will check the configured indexes.'
        return
    fi
    if [[ -n "${PIP_INDEX_URL:-}${PIP_EXTRA_INDEX_URL:-}" ]]; then
        printf '%s\n' 'uv does not read PIP_INDEX_URL or PIP_EXTRA_INDEX_URL. Use --index-url or uv configuration.'
    fi
    if [[ -n "$INDEX_URL" ]]; then
        index_has_version "$INDEX_URL" || fail "Check the selected index, version, or mirror synchronization; no other index or version was selected."
        printf 'Package index: %s\n' "$INDEX_URL"
        return
    fi

    local selected_index
    local indexes=(https://pypi.org/simple https://pypi.tuna.tsinghua.edu.cn/simple)
    if [[ "$REGION" == cn ]]; then
        indexes=(https://pypi.tuna.tsinghua.edu.cn/simple https://pypi.org/simple)
    fi
    for selected_index in "${indexes[@]}"; do
        printf 'Checking package index: %s\n' "$selected_index"
        if index_has_version "$selected_index"; then
            INDEX_URL=$selected_index
            printf 'Package index: %s\n' "$INDEX_URL"
            return
        fi
    done
    fail "No reachable package index lists PowerContext $VERSION. Check connectivity, the version, or use --index-url URL."
}

ensure_uv() {
    if command -v uv >/dev/null 2>&1; then
        UV_BIN=$(command -v uv)
    elif [[ -x "$HOME/.local/bin/uv" ]]; then
        UV_BIN="$HOME/.local/bin/uv"
    else
        case "${UV_OFFLINE:-}" in 1|true) fail "uv is not installed and offline mode disables downloads." ;; esac
        printf 'Installing uv %s in the user executable directory.\n' "$UV_VERSION"
        local installer_url=${POWERCONTEXT_UV_INSTALLER_URL:-https://astral.sh/uv/$UV_VERSION/install.sh}
        local mirror=https://mirrors.ustc.edu.cn/github-release/astral-sh/uv/$UV_VERSION
        local official=https://releases.astral.sh/github/uv/releases/download/$UV_VERSION
        # A custom installer may install another version; never pair it with our pinned mirror.
        if [[ "$REGION" == cn && -z "${POWERCONTEXT_UV_INSTALLER_URL:-}${UV_DOWNLOAD_URL:-}${INSTALLER_DOWNLOAD_URL:-}${UV_INSTALLER_GITHUB_BASE_URL:-}${UV_INSTALLER_GHE_BASE_URL:-}${UV_ASTRAL_MIRROR_URL:-}" ]]; then
            printf 'uv mirror: %s\n' "$mirror"
            if download "$mirror/uv-installer.sh" "$TEMP_DIR/uv-install.sh"; then
                # The official shell installer supports an ordered list of artifact sources.
                UV_DOWNLOAD_URL="$mirror $official https://github.com/astral-sh/uv/releases/download/$UV_VERSION" \
                    UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh "$TEMP_DIR/uv-install.sh" || fail "uv installation failed."
            else
                printf '%s\n' 'uv mirror unavailable; using the official installer.'
                download "$installer_url" "$TEMP_DIR/uv-install.sh" || fail "Could not download the uv installer."
                UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh "$TEMP_DIR/uv-install.sh" || fail "uv installation failed."
            fi
        else
            download "$installer_url" "$TEMP_DIR/uv-install.sh" || fail "Could not download the uv installer. Check POWERCONTEXT_UV_INSTALLER_URL."
            UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh "$TEMP_DIR/uv-install.sh" || fail "uv installation failed. Check the configured download source."
        fi
        UV_BIN="$HOME/.local/bin/uv"
    fi
    [[ -x "$UV_BIN" ]] || fail "uv executable was not found after installation."
    printf 'Using uv: %s\n' "$UV_BIN"
    PATH="$(dirname "$UV_BIN"):$PATH"
    export PATH
}

ensure_python() {
    if PYTHON_BIN=$("$UV_BIN" python find --no-project --no-python-downloads '>=3.11,<4' 2>/dev/null); then
        printf 'Using local Python: %s\n' "$PYTHON_BIN"
        return
    fi
    case "${UV_PYTHON_DOWNLOADS:-}" in never|false|0) fail "No compatible local Python; UV_PYTHON_DOWNLOADS disables downloads." ;; esac
    case "${UV_OFFLINE:-}" in 1|true) fail "No compatible local Python in offline mode." ;; esac
    local mirror=https://mirror.nju.edu.cn/github-release/astral-sh/python-build-standalone
    local key url suffix
    local python_args=(python install 3.12)
    if [[ "$REGION" == cn && -z "${UV_PYTHON_INSTALL_MIRROR:-}${UV_ASTRAL_MIRROR_URL:-}${UV_PYTHON_DOWNLOADS_JSON_URL:-}" ]] && ! has_uv_config_file; then
        # Ask uv for the exact build URL; platform and archive selection stay with uv.
        "$UV_BIN" python list 'cpython@3.12' --only-downloads --show-urls --color never >"$TEMP_DIR/python-downloads.txt" || fail "Could not resolve a Python download."
        read -r key url <"$TEMP_DIR/python-downloads.txt" || true
        case "$url" in
            https://*/python-build-standalone/releases/download/*)
                suffix=${url#*/python-build-standalone/releases/download/}
                if url_available "$mirror/$suffix"; then
                    printf 'Python mirror: %s\n' "$mirror"
                    python_args+=(--mirror "$mirror")
                else
                    printf '%s\n' 'Python mirror does not provide the requested build; using uv default sources.'
                fi
                ;;
        esac
    fi
    printf '%s\n' 'No compatible local Python found. Installing Python 3.12.'
    "$UV_BIN" "${python_args[@]}" || fail "Python installation failed. Check uv output and UV_PYTHON_INSTALL_MIRROR."
    PYTHON_BIN=$("$UV_BIN" python find --no-project --no-python-downloads 3.12) || fail "Installed Python was not found."
}

main() {
    parse_args "$@"
    case "$(uname -s)" in
        Darwin|Linux) ;;
        *) fail "Use the Windows instructions in the installation guide. This script supports macOS and Linux." ;;
    esac
    [[ -n "${HOME:-}" ]] || fail "HOME is not set."
    command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 || fail "Install curl or wget first."
    if [[ "$NO_HOSTS" == false ]]; then
        command -v git >/dev/null 2>&1 || fail "Agent integration setup requires Git. Install Git or use --no-hosts."
    fi
    TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/powercontext-install.XXXXXX")
    trap cleanup EXIT
    detect_region
    ensure_uv
    ensure_python
    check_index
    [[ -z "$INDEX_URL" ]] || export UV_DEFAULT_INDEX="$INDEX_URL"

    local install_args=(tool install "powercontext[cli,server]==$VERSION" --python "$PYTHON_BIN" --no-python-downloads)
    [[ -z "$INDEX_URL" ]] || install_args+=(--default-index "$INDEX_URL")
    printf 'Installing PowerContext %s.\n' "$VERSION"
    "$UV_BIN" "${install_args[@]}" ||
        fail "Installation failed. Check the uv error: Python downloads use UV_PYTHON_INSTALL_MIRROR or UV_ASTRAL_MIRROR_URL; Python packages use uv indexes."

    local tool_bin
    tool_bin=$("$UV_BIN" tool dir --bin)
    export PATH="$tool_bin:$PATH"
    "$tool_bin/powercontext" --help >/dev/null || fail "The installed CLI could not start."
    printf 'Runtime installed: %s\n' "$VERSION"
    printf 'For a new terminal, add these directories to PATH if needed:\n'
    printf '  export PATH=%q:%q:"%s"\n' "$(dirname "$UV_BIN")" "$tool_bin" "\$PATH"

    local setup_status=0
    if [[ "$NO_HOSTS" == false ]]; then
        "$tool_bin/powercontext" setup select --source oceanbase/powercontext \
            --ref "powercontext-v$VERSION" ${HOSTS[@]+"${HOSTS[@]}"} <"$SETUP_INPUT" || setup_status=$?
    fi
    printf '\nConfigure a generation model before starting the Server:\n  %s\n' "$GUIDE"
    printf '%s\n' 'Then run: powercontext server run --env-file .env'
    if ((setup_status != 0)); then
        fail "Runtime installed, but integration setup did not complete. Review its results and retry setup with powercontext-v$VERSION."
    fi
}

main "$@"
