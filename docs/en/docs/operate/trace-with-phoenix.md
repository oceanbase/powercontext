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

- Docker Engine and Docker Compose, or Docker Desktop;
- `uv`, Bash, `curl`, and `python3`;
- ports `6006` and `8000` available locally.

Check the tool versions before starting:

```bash
docker info
docker compose version
uv --version
```

## Start Phoenix

The simplest command uses a temporary container:

```bash
docker run -d --name powercontext-phoenix -p 6006:6006 arizephoenix/phoenix:20.1.0
```

For a local setup that retains SQLite trace data across container recreation, set a working directory and mount a
named volume:

```bash
export PHOENIX_WORKING_DIR=/mnt/data
docker volume create powercontext-phoenix-data
docker run -d --name powercontext-phoenix \
  -p 6006:6006 \
  -e PHOENIX_WORKING_DIR="$PHOENIX_WORKING_DIR" \
  -v powercontext-phoenix-data:"$PHOENIX_WORKING_DIR" \
  arizephoenix/phoenix:20.1.0
```

Phoenix serves both its UI and its OTLP HTTP receiver on port `6006`. Open <http://localhost:6006> to confirm it is
running. Pin an explicit tag so the endpoint and UI layout match this guide. The OTLP HTTP route is
`http://localhost:6006/v1/traces`; port `4317` is only relevant when using an OTLP gRPC receiver.

The first command uses the container's writable layer. Without the named volume, removing the container also removes
the SQLite trace data. Use the volume variant when you need to stop and recreate Phoenix without losing traces.

## Install the export dependency

Recording and export require the `tracing-otlp` extra. The following command installs the latest code from the
repository's `master` branch:

```bash
uv tool install --force "powercontext[cli,server,tracing-otlp] @ git+https://github.com/oceanbase/powercontext.git@master"
```

Without this extra, enabling tracing fails at startup with an explicit error instead of silently dropping spans. The
command below is intended for a new local deployment. Because `--force` replaces the existing `uv` tool environment,
do not use it blindly for a Server that is already serving traffic.

If an existing Server should gain tracing, install the extra into the Python environment that actually runs that
Server, then restart the Server. For example, with a `uv`-managed environment:

```bash
command -v powercontext
uv tool install --force "powercontext[cli,server,tracing-otlp] @ git+https://github.com/oceanbase/powercontext.git@master"
command -v powercontext
powercontext server run --env-file /path/to/powercontext.env
```

For a systemd, Supervisor, container, or other managed deployment, update that deployment's image or environment
instead and restart the existing Server through its process manager. The important requirement is that the restarted
process imports the environment containing `tracing-otlp`; installing a separate CLI copy does not change a running
process.

## Configure and start the Server

Enable tracing, point the exporter at Phoenix, and configure a supported generation model so inference spans have
something to record. `provider:model-name` is only a placeholder and will not work as-is. For example, an OpenAI
deployment can use:

```bash
export POWERCONTEXT_SERVER_TRACING_ENABLED=true
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006
export OTEL_SERVICE_NAME=powercontext-server
export OPENAI_API_KEY=replace-with-your-key
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai:gpt-4.1-mini
powercontext server run
```

Run the Server command in Terminal A and keep it running. If the Server is already running, apply the same
environment variables to its actual service definition and restart it; setting them in another terminal does not
change the existing process.

The OpenTelemetry SDK appends `/v1/traces` to `OTEL_EXPORTER_OTLP_ENDPOINT`, so the spans arrive at
`http://localhost:6006/v1/traces`. Use `OTEL_EXPORTER_OTLP_HEADERS` for a Phoenix deployment that requires
authentication. Set the provider credentials your generation model needs; PowerContext never records them.

Create a Scope and run the client-side checks in Terminal B:

```bash
export POWERCONTEXT_BASE_URL=http://localhost:8000
export POWERCONTEXT_AUTH_TOKEN=replace-with-server-token
export POWERCONTEXT_SCOPE_NAME=tracing-example
export POWERCONTEXT_AUTH_HEADER="Authorization: Bearer $POWERCONTEXT_AUTH_TOKEN"

POWERCONTEXT_SCOPE_ID=$(curl --fail-with-body -sS -X POST "$POWERCONTEXT_BASE_URL/v1/scopes" \
  -H "$POWERCONTEXT_AUTH_HEADER" \
  -H 'content-type: application/json' \
  -d "$POWERCONTEXT_SCOPE_NAME" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
export POWERCONTEXT_SCOPE_ID
```

The exact authentication setup depends on the Server configuration. Keep `POWERCONTEXT_AUTH_TOKEN` empty for an
unauthenticated local Server and remove the `Authorization` header in the examples, or set a token with the
permission required by the endpoint. For an authenticated Server, the token must be allowed to create Scopes and
call the traced endpoints.

Check readiness, capabilities, and a real trace-producing request in order:

```bash
curl --fail-with-body -sS "$POWERCONTEXT_BASE_URL/health/ready" \
  -H "$POWERCONTEXT_AUTH_HEADER" \
  | python3 -c 'import json, sys; data=json.load(sys.stdin); assert data.get("status") == "ready", data; print(data["status"])'

curl --fail-with-body -sS "$POWERCONTEXT_BASE_URL/v1/capabilities" \
  -H "$POWERCONTEXT_AUTH_HEADER" \
  | python3 -m json.tool

curl --fail-with-body -sS -X POST "$POWERCONTEXT_BASE_URL/v1/memory/flush" \
  -H "$POWERCONTEXT_AUTH_HEADER" \
  -H 'content-type: application/json' \
  -d '{"scope_id":"'"$POWERCONTEXT_SCOPE_ID"'"}' \
  | python3 -m json.tool
```

`curl --fail-with-body` only checks the HTTP status code. The readiness example also parses the JSON body and fails
when the response is HTTP 200 with a `degraded` status. The capabilities response should report tracing as enabled.
The flush request is the final end-to-end check; it needs the configured model credentials and may take a few seconds.

## Trigger one inference request

The following full API flow creates a fresh Scope, captures a Source, and converts it into Memory. It also avoids
silently continuing after a failed request:

```bash
set -euo pipefail

POWERCONTEXT_BASE_URL=${POWERCONTEXT_BASE_URL:-http://localhost:8000}
POWERCONTEXT_IDEMPOTENCY_KEY="tracing-example-$(date +%s)-$"
POWERCONTEXT_SCOPE_NAME=tracing-example
POWERCONTEXT_SOURCE_ID="tracing-source-$(date +%s)"
POWERCONTEXT_AUTH_HEADER="Authorization: Bearer $POWERCONTEXT_AUTH_TOKEN"

POWERCONTEXT_SCOPE_ID=$(curl --fail-with-body -sS -X POST "$POWERCONTEXT_BASE_URL/v1/scopes" \
  -H "$POWERCONTEXT_AUTH_HEADER" \
  -H 'content-type: application/json' \
  -H "Idempotency-Key: $POWERCONTEXT_IDEMPOTENCY_KEY" \
  -d "$POWERCONTEXT_SCOPE_NAME" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')

curl --fail-with-body -sS -X POST "$POWERCONTEXT_BASE_URL/v1/sources/content" \
  -H "$POWERCONTEXT_AUTH_HEADER" \
  -H 'content-type: application/json' \
  -H "Idempotency-Key: $POWERCONTEXT_IDEMPOTENCY_KEY" \
  -d '{"scope_id":"'"$POWERCONTEXT_SCOPE_ID"'","source_id":"'"$POWERCONTEXT_SOURCE_ID"'","content":"I always book aisle seats."}' \
  | python3 -m json.tool

curl --fail-with-body -sS -X POST "$POWERCONTEXT_BASE_URL/v1/memory/flush" \
  -H "$POWERCONTEXT_AUTH_HEADER" \
  -H 'content-type: application/json' \
  -H "Idempotency-Key: $POWERCONTEXT_IDEMPOTENCY_KEY" \
  -d '{"scope_id":"'"$POWERCONTEXT_SCOPE_ID"'"}' \
  | python3 -m json.tool
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

## Stop Phoenix

Stop the temporary container without removing it:

```bash
docker stop powercontext-phoenix
```

Start it again later with:

```bash
docker start powercontext-phoenix
```

If the container is no longer needed, remove it:

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
