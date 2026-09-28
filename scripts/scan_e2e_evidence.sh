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

# Scan replay evidence and publish only bounded, sanitized diagnostics.

set -euo pipefail

: "${DATABASE:?DATABASE is required}"
: "${DIAGNOSTICS_PATH:?DIAGNOSTICS_PATH is required}"
: "${EVIDENCE_PATH:?EVIDENCE_PATH is required}"
: "${SCENARIO_OUTCOME:?SCENARIO_OUTCOME is required}"
: "${TRUFFLEHOG_IMAGE:?TRUFFLEHOG_IMAGE is required}"

mkdir -p "${DIAGNOSTICS_PATH}"
summary="${DIAGNOSTICS_PATH}/summary.txt"
scanner_json="${DIAGNOSTICS_PATH}/trufflehog.jsonl"
scanner_stderr="${DIAGNOSTICS_PATH}/trufflehog.stderr.log"
sanitize_database() {
    case "$1" in
        sqlite|oceanbase) printf '%s' "$1" ;;
        *) printf '[REDACTED]' ;;
    esac
}

sanitize_scenario_outcome() {
    case "$1" in
        success|failure|cancelled|skipped) printf '%s' "$1" ;;
        *) printf '[REDACTED]' ;;
    esac
}

sanitize_scanner_image() {
    if test "${#1}" -le 240 \
        && printf '%s' "$1" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._/-]{0,200}@sha256:[0-9A-Fa-f]{64}$'; then
        printf '%s' "$1"
    else
        printf '[REDACTED]'
    fi
}

scanner_image_summary="$(sanitize_scanner_image "${TRUFFLEHOG_IMAGE}")"
{
    echo "database=$(sanitize_database "${DATABASE}")"
    echo "diagnostic_format=powercontext-e2e-v1"
    echo "scenario_outcome=$(sanitize_scenario_outcome "${SCENARIO_OUTCOME}")"
    echo "scanner_image=${scanner_image_summary}"
} > "${summary}"

if test "${scanner_image_summary}" = "[REDACTED]"; then
    {
        echo "scan_status=infra_error"
        echo "scanner_attempts=0"
        echo "scanner_exit_code=2"
        echo "scanner_error_category=configuration"
    } >> "${summary}"
    echo "Replay evidence scanning configuration is invalid; raw evidence will not be uploaded." >&2
    exit 1
fi

if ! test -d "${EVIDENCE_PATH}"; then
    echo "evidence_status=missing" >> "${summary}"
    echo "Replay evidence directory was not produced." >&2
    exit 1
fi

file_count="$(find "${EVIDENCE_PATH}" -type f | wc -l | tr -d '[:space:]')"
if test "${file_count}" = 0; then
    echo "evidence_status=empty" >> "${summary}"
    echo "Replay evidence directory was empty." >&2
    exit 1
fi
{
    echo "evidence_status=present"
    echo "evidence_file_count=${file_count}"
} >> "${summary}"

run_scan() {
    local json_output="$1"
    local stderr_output="$2"
    docker run --rm \
        --network none \
        --volume "${EVIDENCE_PATH}:/evidence:ro" \
        "${TRUFFLEHOG_IMAGE}" \
        filesystem /evidence \
        --no-verification \
        --results=verified,unknown,unverified \
        --fail \
        --fail-on-scan-errors \
        --no-update \
        --json > "${json_output}" 2> "${stderr_output}" || return $?
}

sanitize_detector() {
    local value="$1"
    local lower_value
    # macOS ships Bash 3.2, which does not support ${value,,}.
    lower_value="$(printf '%s' "${value}" | LC_ALL=C tr '[:upper:]' '[:lower:]')"
    if test "${#value}" -le 120; then
        case "${value}" in
            ""|[!A-Za-z0-9]*) ;;
            *)
                if test -z "$(printf '%s' "${value}" | tr -d 'A-Za-z0-9 _./-')"; then
                    case "${lower_value}" in
                        *password*|*secret*|*authorization*|*bearer*|*basic*|*api_key*|*api-key*|*api\ key*|*://*|*@*|*=*)
                            ;;
                        *)
                            printf '%s' "${value}"
                            return
                            ;;
                    esac
                fi
                ;;
        esac
    fi
    printf '[REDACTED]'
}

sanitize_path() {
    local value="$1"
    if [[ -z "${value}" ]]; then
        printf 'unknown'
    else
        printf '[REDACTED]'
    fi
}

file_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        printf 'unavailable'
    fi
}

scanner_error_category() {
    local stderr_file="$1"
    if grep -Eiq 'docker daemon|cannot connect to the docker daemon|is the docker daemon running' "${stderr_file}"; then
        printf 'docker_daemon'
    elif grep -Eiq 'pull|manifest|image|ghcr\.io|not found' "${stderr_file}"; then
        printf 'image_pull'
    elif grep -Eiq 'network|timeout|connection|dns|resolve' "${stderr_file}"; then
        printf 'network'
    else
        printf 'unknown'
    fi
}

if run_scan "${scanner_json}" "${scanner_stderr}"; then
    scanner_status=0
else
    scanner_status=$?
fi
scanner_attempts=1
if test "${scanner_status}" -ne 0 && test "${scanner_status}" -ne 183; then
    scanner_attempts=2
    scanner_json_retry="${DIAGNOSTICS_PATH}/trufflehog-retry.jsonl"
    scanner_stderr_retry="${DIAGNOSTICS_PATH}/trufflehog-retry.stderr.log"
    if run_scan "${scanner_json_retry}" "${scanner_stderr_retry}"; then
        scanner_status=0
    else
        scanner_status=$?
    fi
    scanner_json="${scanner_json_retry}"
    scanner_stderr="${scanner_stderr_retry}"
fi

echo "scanner_attempts=${scanner_attempts}" >> "${summary}"
if test "${scanner_status}" -eq 183; then
    echo "scan_status=findings" >> "${summary}"
    echo "scanner_exit_code=183" >> "${summary}"
    if command -v jq >/dev/null 2>&1; then
        while IFS=$'\t' read -r detector path; do
            detector="${detector%$'\r'}"
            path="${path%$'\r'}"
            printf 'finding=%s path=%s\n' \
                "$(sanitize_detector "${detector}")" \
                "$(sanitize_path "${path}")" >> "${summary}"
        done < <(
            jq -nr '
                limit(20;
                    inputs
                    | select(.DetectorName? != null)
                    | [
                        (.DetectorName | tostring),
                        ((.SourceMetadata.Data.Filesystem.file // .SourceMetadata.Data.Filesystem.path // "unknown")
                            | tostring
                            | sub("^/evidence/?"; ""))
                      ]
                    | @tsv
                )
            ' "${scanner_json}" 2>/dev/null || true
        )
    else
        echo "finding_diagnostics=jq_unavailable" >> "${summary}"
    fi
    echo "Replay evidence contained scanner findings; raw evidence will not be uploaded." >&2
    exit 1
fi

if test "${scanner_status}" -ne 0; then
    echo "scan_status=infra_error" >> "${summary}"
    echo "scanner_exit_code=${scanner_status}" >> "${summary}"
    stderr_lines="$(wc -l < "${scanner_stderr}" | tr -d '[:space:]')"
    if test "${stderr_lines}" -gt 50; then
        stderr_lines=50
    fi
    echo "scanner_error_category=$(scanner_error_category "${scanner_stderr}")" >> "${summary}"
    echo "scanner_stderr_tail_lines=${stderr_lines}" >> "${summary}"
    echo "scanner_stderr_sha256=$(file_sha256 "${scanner_stderr}")" >> "${summary}"
    echo "Replay evidence scanning failed due to infrastructure error; raw evidence will not be uploaded." >&2
    exit 1
fi

echo "scan_status=clean" >> "${summary}"
