#!/bin/sh
set -eu

root=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
cd "$root"

mode=${1:-acceptance}
database=${POWERCONTEXT_E2E_DATABASE:-sqlite}

case "$database" in
    sqlite | oceanbase) ;;
    *)
        echo "POWERCONTEXT_E2E_DATABASE must be sqlite or oceanbase" >&2
        exit 2
        ;;
esac

case "$mode" in
    acceptance | live | check | down) ;;
    *)
        echo "mode must be acceptance, live, check, or down" >&2
        exit 2
        ;;
esac

compose_files="-f e2e/bub/compose.yaml"
if [ "$database" = oceanbase ]; then
    compose_files="$compose_files -f e2e/bub/compose.oceanbase.yaml"
fi

export COMPOSE_PROJECT_NAME="powercontext-e2e-$database"
output=${POWERCONTEXT_E2E_OUTPUT:-"$root/.powercontext-e2e/bub/$database/$mode"}
mkdir -p "$output"
POWERCONTEXT_E2E_OUTPUT=$(CDPATH= cd -- "$output" && pwd)
export POWERCONTEXT_E2E_OUTPUT
export POWERCONTEXT_E2E_DATABASE=$database

if [ "$mode" = check ]; then
    docker compose $compose_files config --quiet
    exit
fi

if [ "$mode" = down ]; then
    docker compose $compose_files down --volumes --remove-orphans
    exit
fi

if [ "$mode" = live ]; then
    : "${BUB_MODEL:?BUB_MODEL is required for live replay}"
    : "${BUB_API_KEY:?BUB_API_KEY is required for live replay}"
    case "$BUB_MODEL" in
        openai:*)
            POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=${POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL:-$BUB_MODEL}
            OPENAI_API_KEY=${OPENAI_API_KEY:-$BUB_API_KEY}
            OPENAI_BASE_URL=${OPENAI_BASE_URL:-${BUB_API_BASE:-}}
            export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL OPENAI_API_KEY OPENAI_BASE_URL
            ;;
        deepseek:*)
            POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=${POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL:-$BUB_MODEL}
            DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:-$BUB_API_KEY}
            export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL DEEPSEEK_API_KEY
            ;;
    esac
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

if [ "$mode" = acceptance ]; then
    docker compose $compose_files run --rm harness \
        acceptance \
        e2e/bub/scenarios/locomo-support-group.yaml \
        e2e/bub/scenarios/project-database-decision.yaml \
        --output /evidence
else
    scenario=${POWERCONTEXT_E2E_SCENARIO:-e2e/bub/scenarios/project-database-decision.yaml}
    docker compose $compose_files run --rm harness live "$scenario" --output /evidence
fi
