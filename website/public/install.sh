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

VERSION=1.0.0rc2
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

  --version VERSION  Exact package version (default: 1.0.0rc2).
                     Integrations use the matching powercontext-vVERSION tag.
  --index-url URL    HTTPS default package index for this installation.
                     Existing additional uv indexes still take precedence.
  --host HOST        Install this Agent integration; repeat for multiple hosts.
  --no-hosts         Install only the CLI and local Server.
  -h, --help         Show this help.

Without host options, an interactive terminal opens powercontext setup select.
Without a terminal, --host or --no-hosts is required.

Without explicit index settings, detect the network country through Cloudflare:
prefer the Tsinghua mirror for CN, otherwise PyPI. Try the other index if the
preferred one is unreachable or does not list the requested version.
Use --index-url to select one index and skip country detection and fallback.

Download configuration (independent of the package index):
  POWERCONTEXT_UV_INSTALLER_URL    HTTPS uv installer script URL; defaults to
                                 https://astral.sh/uv/install.sh.
  UV_INSTALLER_GITHUB_BASE_URL   GitHub mirror base URL for uv binaries.
  UV_PYTHON_INSTALL_MIRROR       Mirror of python-build-standalone downloads.
  UV_ASTRAL_MIRROR_URL           Astral metadata/artifact mirror for uv versions
                                 that support it (0.11.14+).
These settings are passed to the official installer and uv without modification.
Existing uv and compatible Python installations are reused.
EOF
}

parse_args() {
    while (($#)); do
        case "$1" in
            --version|--index-url|--host)
                (($# >= 2)) && [[ -n "$2" && "$2" != --* ]] || fail "$1 requires a value"
                case "$1" in
                    --version) VERSION=$2 ;;
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

detect_country() {
    # This lookup is optional and must also work before Python or uv is installed.
    download https://www.cloudflare.com/cdn-cgi/trace "$TEMP_DIR/location.txt" 3 2>/dev/null || return 0
    local key value
    while IFS='=' read -r key value; do
        [[ "$key" == loc ]] || continue
        value=${value%$'\r'}
        if [[ "$value" =~ ^[A-Z]{2}$ ]]; then
            printf '%s\n' "$value"
        fi
        return 0
    done < "$TEMP_DIR/location.txt"
}

has_uv_configuration() {
    # Let uv resolve its configuration and authentication; do not parse TOML in shell.
    [[ -n "${UV_DEFAULT_INDEX:-}${UV_INDEX:-}${UV_INDEX_URL:-}${UV_EXTRA_INDEX_URL:-}${UV_CONFIG_FILE:-}${UV_OFFLINE:-}${UV_NO_INDEX:-}${UV_FIND_LINKS:-}" ]] && return 0
    [[ -f "${XDG_CONFIG_HOME:-$HOME/.config}/uv/uv.toml" || -f /etc/uv/uv.toml ]] && return 0
    local directory
    local config_dirs=()
    IFS=: read -r -a config_dirs <<< "${XDG_CONFIG_DIRS:-/etc/xdg}"
    for directory in "${config_dirs[@]}"; do
        [[ -f "$directory/uv/uv.toml" ]] && return 0
    done
    return 1
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

    local country selected_index
    local indexes=(https://pypi.org/simple https://pypi.tuna.tsinghua.edu.cn/simple)
    country=$(detect_country)
    if [[ "$country" == CN ]]; then
        indexes=(https://pypi.tuna.tsinghua.edu.cn/simple https://pypi.org/simple)
        printf '%s\n' 'Detected network country CN; trying the Tsinghua mirror first.'
    elif [[ -n "$country" ]]; then
        printf 'Detected network country %s; trying PyPI first.\n' "$country"
    else
        printf '%s\n' 'Network country unavailable; trying PyPI first.'
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
        printf '%s\n' 'Installing uv in the user executable directory.'
        download "${POWERCONTEXT_UV_INSTALLER_URL:-https://astral.sh/uv/install.sh}" "$TEMP_DIR/uv-install.sh" ||
            fail "Could not download the uv installer. Check connectivity or set POWERCONTEXT_UV_INSTALLER_URL; --index-url only changes Python packages."
        UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh "$TEMP_DIR/uv-install.sh" ||
            fail "uv installation failed. Check the installer output and UV_INSTALLER_GITHUB_BASE_URL for uv binary downloads."
        UV_BIN="$HOME/.local/bin/uv"
    fi
    [[ -x "$UV_BIN" ]] || fail "uv executable was not found after installation."
    printf 'Using uv: %s\n' "$UV_BIN"
    PATH="$(dirname "$UV_BIN"):$PATH"
    export PATH
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
    check_index
    [[ -z "$INDEX_URL" ]] || export UV_DEFAULT_INDEX="$INDEX_URL"
    ensure_uv

    local python_bin
    local install_args=(tool install "powercontext[cli,server]==$VERSION")
    if python_bin=$("$UV_BIN" python find --no-project --no-python-downloads '>=3.11,<4' 2>/dev/null); then
        printf 'Using local Python: %s\n' "$python_bin"
        install_args+=(--python "$python_bin" --no-python-downloads)
    else
        printf '%s\n' 'No compatible local Python found. uv will obtain Python 3.12.'
        install_args+=(--python 3.12)
    fi
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
