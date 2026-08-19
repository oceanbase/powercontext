#!/bin/sh
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

set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
cd "$root"

command=${1:-acceptance}
if [ "$#" -gt 0 ]; then
    shift
fi
database=${POWERCONTEXT_E2E_DATABASE:-sqlite}

case "$database" in
    sqlite | oceanbase) ;;
    *)
        echo "POWERCONTEXT_E2E_DATABASE must be sqlite or oceanbase" >&2
        exit 2
        ;;
esac

case "$command" in
    acceptance | check | down) ;;
    *)
        echo "command must be acceptance, check, or down" >&2
        exit 2
        ;;
esac

compose_files="-f e2e/bub/compose.yaml"
if [ "$database" = oceanbase ]; then
    compose_files="$compose_files -f e2e/bub/compose.oceanbase.yaml"
fi
export COMPOSE_PROJECT_NAME="powercontext-e2e-$database"
output=${POWERCONTEXT_E2E_OUTPUT:-"$root/.powercontext-e2e/bub/$database/acceptance"}
mkdir -p "$output"
POWERCONTEXT_E2E_OUTPUT=$(CDPATH= cd -- "$output" && pwd)
export POWERCONTEXT_E2E_OUTPUT
export POWERCONTEXT_E2E_DATABASE=$database

auth_path=${CODEX_HOME:-$HOME/.codex}/auth.json
if [ -f "$auth_path" ]; then
    auth_directory=$(CDPATH= cd -- "$(dirname "$auth_path")" && pwd)
    POWERCONTEXT_E2E_CODEX_AUTH_MOUNT="$auth_directory/$(basename "$auth_path")"
else
    POWERCONTEXT_E2E_CODEX_AUTH_MOUNT=/dev/null
fi
export POWERCONTEXT_E2E_CODEX_AUTH_MOUNT

if [ "$command" = check ]; then
    test "$#" -eq 0 || { echo "check does not accept workload arguments" >&2; exit 2; }
    docker compose $compose_files config --quiet
    exit
fi

if [ "$command" = down ]; then
    test "$#" -eq 0 || { echo "down does not accept workload arguments" >&2; exit 2; }
    docker compose $compose_files down --volumes --remove-orphans
    exit
fi

if [ -z "${GITHUB_SHA:-}" ]; then
    GITHUB_SHA=$(git rev-parse HEAD)
    export GITHUB_SHA
fi

show_startup_diagnostics() {
    echo "Compose startup failed; service state:" >&2
    docker compose $compose_files ps --all >&2 || true
    echo "Compose service logs (last 200 lines):" >&2
    docker compose $compose_files logs --no-color --timestamps --tail 200 >&2 || true
}

start_services() {
    attempt=1
    max_attempts=1
    if [ "$database" = oceanbase ]; then
        max_attempts=2
    fi

    while :; do
        if docker compose $compose_files up --detach --wait powercontext; then
            return
        else
            status=$?
        fi

        show_startup_diagnostics
        if [ "$attempt" -ge "$max_attempts" ]; then
            return "$status"
        fi

        attempt=$((attempt + 1))
        echo "Retrying OceanBase Compose startup (attempt $attempt of $max_attempts)." >&2
        if docker compose $compose_files down --volumes --remove-orphans; then
            continue
        fi

        echo "Compose reset failed; startup will not be retried." >&2
        return "$status"
    done
}

cleanup() {
    status=$?
    trap - EXIT INT TERM
    set +e

    docker compose $compose_files down --volumes --remove-orphans
    cleanup_status=$?
    if [ "$cleanup_status" -ne 0 ]; then
        echo "Compose cleanup failed with exit code $cleanup_status" >&2
        if [ "$status" -eq 0 ]; then
            status=$cleanup_status
        fi
    fi
    exit "$status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

docker compose $compose_files build powercontext harness
start_services

set -- acceptance --manifest e2e/bub/tasks --output /evidence "$@"
docker compose $compose_files run --rm harness "$@"
