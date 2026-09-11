---
title: Trace with Phoenix
description: Export PowerContext transport, application, and inference spans to a local Phoenix container.
---

# Trace with Phoenix

PowerContext exports OpenTelemetry spans for transport and application operations. When tracing is enabled, the
generation and embedding calls that PowerContext itself constructs are traced too, so one trace shows the request, the
Memory operation, and the model calls underneath it.

This guide sends those spans to [Phoenix](https://github.com/Arize-ai/phoenix) running locally.

## Prerequisites

This guide assumes a Linux or macOS development machine with:

- Docker Engine; use Docker Desktop on macOS;
- `uv`, Bash, `curl`, and `python3`;
- ports `6006` and `8000` available locally.

Check the tool versions before starting:

```bash
docker info
uv --version
```

## Start Phoenix

The simplest command uses a temporary container:

```bash
docker run -d --name powercontext-phoenix -p 6006:6006 arizephoenix/phoenix:20.1.0
```

Choose either the temporary container or the persistent-volume command below; do not run both commands with the same
container name. To retain traces after removing the container, use the persistent-volume command on the first launch.
If a temporary container already exists, back up any data you need and remove the old container before switching.

```bash
docker volume create powercontext-phoenix-data
docker run -d --name powercontext-phoenix \
  -p 6006:6006 \
  -e PHOENIX_WORKING_DIR=/mnt/data \
  -v powercontext-phoenix-data:/mnt/data \
  arizephoenix/phoenix:20.1.0
```

Phoenix serves both its UI and its OTLP HTTP receiver on port `6006`. Open <http://localhost:6006> to confirm it is
running. The OTLP HTTP route is `http://localhost:6006/v1/traces`; port `4317` is only relevant when using an OTLP
gRPC receiver. Opening the UI confirms only that the UI and Phoenix process are running. Even a successful
`GET /v1/traces` response confirms only that the HTTP route is reachable; it does not prove that OTLP spans are received,
stored, and queryable. Pin an explicit image tag so the endpoint and UI layout match this guide.

Phoenix uses SQLite by default; the official recommendation is to point `PHOENIX_WORKING_DIR` at a persistent volume.
See the [Phoenix storage configuration](https://arize.com/docs/phoenix/self-hosting/deployment-options/docker) documentation
for other storage options.

This guide uses an OTLP/HTTP exporter, so port `4317` is not a required check. Verify it only when using an OTLP/gRPC
exporter.

## Install the export dependency

Recording and export require the `tracing-otlp` extra:

```bash
uv tool install --force "powercontext[cli,server,tracing-otlp] @ git+https://github.com/oceanbase/powercontext.git@master"
```

Without this extra, enabling tracing fails at startup with an explicit error instead of silently dropping spans. This
command is intended for a new deployment. It force-rebuilds the existing `uv tool` environment and replaces the
PowerContext installed in it, so confirm the existing Server's installation method and configuration path first. It
installs the current code from `master`, so the result changes as the repository is updated.

If an existing Server should gain tracing, install the extra into the Python environment that actually runs that
Server, then restart the old process. For a foreground Server installed with `uv tool`, press `Ctrl+C` in the old
Server terminal first, then run:

```bash
command -v powercontext
uv tool install --force "powercontext[cli,server,tracing-otlp] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext server run --env-file /path/to/powercontext.env
```

Replace the example path with the existing Server's actual configuration path. If the Server is started by systemd,
Supervisor, or another process manager, confirm that the service points to the updated `powercontext` executable and
restart it through that manager. Installing the exporter in another environment does not give the running Server
tracing capability.

## Configure and start the Server

`provider:model-name` is only a placeholder and will not work as-is. First configure a supported generation model and
provider credentials as described in [Configure models and full memory](../get-started/configure-models.md). Set a custom
base URL only if you use a proxy or custom endpoint. The example below uses `openai:gpt-4.1-mini`; the available
model still depends on the provider account and region.

In Terminal A, enable tracing, point the exporter at Phoenix, and configure a generation model so `flush_memory`
produces a model-call span:

```bash
export POWERCONTEXT_SERVER_TRACING_ENABLED=true
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006
export OTEL_SERVICE_NAME=powercontext-server
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai:gpt-4.1-mini
powercontext server run
```

`powercontext server run` stays in the foreground. Keep Terminal A open and run the checks and requests in Terminal B.

The OpenTelemetry SDK appends `/v1/traces` to `OTEL_EXPORTER_OTLP_ENDPOINT`, so the spans arrive at
`http://localhost:6006/v1/traces`. Use `OTEL_EXPORTER_OTLP_HEADERS` for a Phoenix deployment that requires
authentication. Set the provider credentials your generation model needs; PowerContext never records them.

The acceptance flow has three levels:

| Check | What it proves |
| --- | --- |
| Open <http://localhost:6006> or check that the HTTP route is reachable | Phoenix UI and HTTP service have started; it does not prove spans are received. |
| Send a real operation after starting the Server and find its span in Phoenix | The OTLP/HTTP exporter sent a valid span and Phoenix received, stored, and queried it. |
| A successful `flush_memory` with a `chat <model>` generation span in the trace | The PowerContext inference tracing path works. |

In Terminal B, first set the connection address. Replace the default address if the Server uses another port. If
authentication is enabled, provide a valid `POWERCONTEXT_CLIENT_API_TOKEN`; do not put the token in this document or
the repository. The capabilities check also requires `server.observe`:

```bash
export POWERCONTEXT_BASE_URL="${POWERCONTEXT_BASE_URL:-http://127.0.0.1:8000}"
export POWERCONTEXT_CLIENT_SERVER_URL="$POWERCONTEXT_BASE_URL"
```

Then follow the checks below in order:

1. Use `powercontext --json ready` or read `/health/ready` directly, checking `status` and `checks`. Automation must
   confirm that `status` is `ready` instead of checking only the command exit code.
2. Use `powercontext capabilities` and confirm that the output contains `Memory extraction: enabled`.
3. Run the `flush_memory` request below and confirm in Phoenix that the trace contains a `chat <model>` span.

Only the third step proves that the inference and tracing path works. Starting the Server or receiving a successful
`/health/ready` response does not prove that Memory extraction is available.

## Trigger one inference request

The following API flow creates a fresh Scope, captures a Source, and converts it into Memory.

This Bash API example uses an unauthenticated local instance by default. If the Server uses authentication, provide
the client token first; the requests below add an `Authorization: Bearer` header consistently. Creating a Scope requires
`server.admin`. Writing a Source and flushing Memory require `scope.contribute` for the Scope.

```bash
export POWERCONTEXT_BASE_URL="${POWERCONTEXT_BASE_URL:-http://127.0.0.1:8000}"
export POWERCONTEXT_IDEMPOTENCY_KEY="tracing-example-$(date +%s)-$"

if [[ -n "${POWERCONTEXT_CLIENT_API_TOKEN:-}" ]]; then
  POWERCONTEXT_AUTH_ARGS=(--header "Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}")
else
  POWERCONTEXT_AUTH_ARGS=()
fi

POWERCONTEXT_SCOPE_ID="$(
  set -o pipefail
  curl --fail --silent --show-error \
    "${POWERCONTEXT_AUTH_ARGS[@]}" \
    --header 'content-type: application/json' \
    --data "{\"title\":\"Phoenix tracing example\",\"summary\":\"Scope for tracing verification\",\"idempotency_key\":\"${POWERCONTEXT_IDEMPOTENCY_KEY}\"}" \
    "${POWERCONTEXT_BASE_URL}/v1/scopes" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["scope_id"])'
)"
: "${POWERCONTEXT_SCOPE_ID:?Scope creation failed; check the response and authentication, then retry}"
export POWERCONTEXT_SCOPE_ID
```

The command stores the `scope_id` returned by `create_scope` in `POWERCONTEXT_SCOPE_ID`. To inspect the creation
request's HTTP status and `X-PowerContext-Request-ID`, rerun it separately with `curl -i`. Then capture a Source and
convert it into Memory:

```bash
export POWERCONTEXT_SOURCE_ID="tracing-example-$(date +%s)-$"

curl --fail --show-error -i -X POST "${POWERCONTEXT_BASE_URL}/v1/sources/content" \
  "${POWERCONTEXT_AUTH_ARGS[@]}" \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${POWERCONTEXT_SCOPE_ID}\",\"source_id\":\"${POWERCONTEXT_SOURCE_ID}\",\"content\":\"I always book aisle seats.\"}"
```

```bash
curl --fail --show-error -i -X POST "${POWERCONTEXT_BASE_URL}/v1/memory/flush" \
  "${POWERCONTEXT_AUTH_ARGS[@]}" \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${POWERCONTEXT_SCOPE_ID}\"}"
```

Memory extraction runs during the flush, not during capture.

## Read the trace

Open <http://localhost:6006>, select the `default` project, and open the most recent trace for
`powercontext-server`. The flush produces five nested spans in one trace:

| Span | Meaning |
| --- | --- |
| `HTTP flush_memory` | The inbound HTTP request. `powercontext.request.id` matches the `X-PowerContext-Request-ID` response header. |
| `powercontext flush_memory` | The application operation, independent of the transport that invoked it. |
| `memory.flush` | The Runtime stage that processes the Source window. Inference spans nest beneath it when extraction runs. |
| `invoke_agent memory_extraction` | One PowerContext generation task. The name identifies the purpose, not the model. |
| `chat <model>` | One request to the model provider, with token usage and latency. |

Scoped operations add the following internal stage spans beneath their application operation. Read-only searches never
take the write lock, so they emit no `scope.lock` span:

| Span | Meaning |
| --- | --- |
| `scope.context` | Resolving the scope's context from the configured provider; near zero for the built-in provider, visible when a provider does I/O here. |
| `scope.lock` | Waiting for the scope write lock, ending the moment it is acquired. `powercontext.scope.lock.contended` reports whether another operation already held it. |
| `memory.flush` | One Source-window flush for `flush_memory` or a scheduled activation. |
| `memory.search` | Memory lookup for `search_memory` or `prepare_context`; embedding and reranking spans, when present, are nested beneath it. |
| `memory.rerank` | One actual reranker call; model-backed reranking nests `invoke_agent memory_rerank` beneath it. |
| `experience.search` | Experience recall during `prepare_context`; emitted even when recall is not configured. |
| `experience.incubation` | One in-process Experience incubation operation. |
| `context.build` | The synchronous step that selects and renders the final prepared context from recalled candidates. |

The other PowerContext generation tasks appear under the same convention: `experience_incubation`,
`experience_generation`, `skill_generation`, `handoff_generation`, and `memory_rerank`. When an embedding model is
configured, embedding calls appear as `embeddings <model>` spans under the operation that triggered them.

Spans are exported in batches, so allow a few seconds before refreshing. An MCP request produces
`MCP mcp.tools.call` in place of the `HTTP` span. Readiness probes are deliberately not traced, so health checks do not
create single-span traces.

## Background Worker spans

Each Scope Worker invocation starts an independent trace, whether requested by a Family schedule or an explicit flush.
Memory, Topic Memory, Experience, and Profile use the same lifecycle spans. The root has
`powercontext.operation.unit` set to `background` and never inherits an HTTP or MCP request trace:

| Span | Meaning |
| --- | --- |
| `artifact_processing.worker` | One bounded Scope invocation, including process startup and durable completion acknowledgement. |
| `artifact_processing.worker.start` | Spawn and start the isolated child process. |
| `artifact_processing.worker.wait` | Wait for the child result or the invocation timeout. |
| `artifact_processing.worker.acknowledge` | Verify the current fence and persisted acknowledgement of the assigned request generation. |

The root outcome is `success` only after durable acknowledgement, including a persisted NOOP. A child that exits
successfully without acknowledging the request produces `failure`. Other outcomes include `failure`, `cancelled`,
`cursor_conflict`, and `head_conflict`. Retry attempts create new independent roots.

Lifecycle spans record `powercontext.artifact_processing.family`; failed roots add a bounded
`powercontext.artifact_processing.failure` category such as `timeout`, `worker_failed`,
`missing_durable_acknowledgement`, or `leadership_lost`. They exclude Scope IDs, request IDs, Source data, and model
payloads. These parent-process spans describe Worker lifecycle and acknowledgement. Inference runs in isolated children
and does not attach model spans to the parent lifecycle trace.

## What is not exported

PowerContext configures inference instrumentation to exclude content. Spans carry model identifiers, token usage,
durations, and error categories. Prompts, model responses, Memory content, and vectors are excluded, and message
attributes record only the shape of each message rather than its text.

## Stop and remove Phoenix

Stop the temporary container without removing it:

```bash
docker stop powercontext-phoenix
```

Start it again later with:

```bash
docker start powercontext-phoenix
```

If the container is no longer needed, remove it. Without a persistent volume, this also deletes the SQLite trace data
stored inside the container:

```bash
docker rm -f powercontext-phoenix
```

When the named-volume variant was used, the volume remains after removing the container. Delete it only when the
stored SQLite traces are no longer needed:

```bash
docker volume rm powercontext-phoenix-data
```

Span names and attributes follow the Pydantic AI GenAI semantic conventions and can change when that dependency is
upgraded across a major version. Do not treat them as a stable contract.
