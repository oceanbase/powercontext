---
title: Trace with Langfuse
description: Export PowerContext transport, application, and inference spans to Langfuse through standard OTLP configuration.
---

# Trace with Langfuse

PowerContext exports OpenTelemetry spans for transport and application operations. When tracing is enabled, the
generation and embedding calls that PowerContext itself constructs are traced too, so one trace shows the request, the
Memory operation, and the model calls underneath it.

This guide sends those spans to [Langfuse](https://langfuse.com) through its OTLP endpoint. It needs no PowerContext
code change and no Langfuse SDK: the standard OpenTelemetry variables from [Trace with Phoenix](trace-with-phoenix.md)
point the exporter at Langfuse instead.

## Prerequisites

This guide assumes a Linux or macOS development machine with:

- Git;
- Docker Engine and Docker Compose, or Docker Desktop;
- `uv`, Bash, `curl`, and `python3`;
- ports `3000` and `8000` available locally.

Check the tool versions before starting:

```bash
docker info
docker compose version
uv --version
```

## Start Langfuse

Langfuse self-hosting runs several services (web, worker, PostgreSQL, ClickHouse, Redis, and MinIO) with Docker
Compose:

```bash
git clone https://github.com/langfuse/langfuse.git
cd langfuse
docker compose up -d
```

The default Compose configuration is suitable for local evaluation only. It uses development credentials and local
storage; do not expose it directly to an untrusted network. Run `docker compose ps` and inspect the service logs if
the UI does not open.

Open <http://localhost:3000>, create a user, an organization, and a project, then create an API key pair in the project
settings. Keep the public key (`pk-lf-...`) and the secret key (`sk-lf-...`) at hand; they authenticate the exporter
below. The OTLP endpoint requires Langfuse v3.22.0 or later. This guide was verified with Langfuse 4.10.0.

For a reproducible local setup, [headless initialization](https://langfuse.com/self-hosting/headless-initialization)
creates the organization, project, user, and keys from environment variables instead of the UI. Langfuse Cloud works
the same way as a self-hosted instance: skip the compose step and replace `http://localhost:3000` below with the base
URL of your region, such as `https://cloud.langfuse.com` or `https://us.cloud.langfuse.com`.

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

Langfuse authenticates OTLP requests with HTTP Basic authentication built from the project keys. Enable tracing, point
the exporter at Langfuse, and configure a supported generation model so inference spans have something to record.
`provider:model-name` is only a placeholder and will not work as-is. For example, an OpenAI deployment can use:

```bash
export LANGFUSE_PUBLIC_KEY=pk-lf-replace-me
export LANGFUSE_SECRET_KEY=sk-lf-replace-me
LANGFUSE_AUTH=$(printf '%s:%s' "$LANGFUSE_PUBLIC_KEY" "$LANGFUSE_SECRET_KEY" | base64 | tr -d '\n')

export POWERCONTEXT_SERVER_TRACING_ENABLED=true
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:3000/api/public/otel
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic $LANGFUSE_AUTH,x-langfuse-ingestion-version=4"
export OTEL_SERVICE_NAME=powercontext-server
export OPENAI_API_KEY=replace-with-your-key
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai:gpt-4.1-mini
powercontext server run
```

Run the Server command in Terminal A and keep it running. If the Server is already running, apply the same
environment variables to its actual service definition and restart it; setting them in another terminal does not
change the existing process.

The OpenTelemetry SDK appends `/v1/traces` to `OTEL_EXPORTER_OTLP_ENDPOINT`, so the spans arrive at
`http://localhost:3000/api/public/otel/v1/traces`, the traces endpoint Langfuse expects. Langfuse accepts OTLP over
HTTP only, which is the protocol of the exporter installed by the `tracing-otlp` extra. The
`x-langfuse-ingestion-version=4` header makes Langfuse process the spans immediately; without it, Langfuse documents
that ingestion can lag by up to ten minutes. Set the provider credentials your generation model needs; PowerContext
records neither them nor the exporter headers.

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
call the traced endpoints; the management endpoints also enforce the configured `server.observe` permission where
applicable.

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

Open <http://localhost:3000>, select the project, and open the **Traces** view. Langfuse names a trace after its root
span, so the flush appears as `HTTP flush_memory`. Every PowerContext span becomes an observation, and Langfuse infers
the observation type from the GenAI attributes on the span:

| Observation | Type | Meaning |
| --- | --- | --- |
| `HTTP flush_memory` | SPAN | The inbound HTTP request. Its `attributes.powercontext.request.id` metadata matches the `X-PowerContext-Request-ID` response header. |
| `powercontext flush_memory` | SPAN | The application operation, independent of the transport that invoked it. |
| `memory.flush` | SPAN | The Runtime stage that processes the Source window. The other stage spans, such as `scope.context`, `scope.lock`, `memory.search`, and `context.build`, are SPAN observations as well. |
| `memory_extraction run` | AGENT | One PowerContext generation task. Langfuse names it from the span's `logfire.msg` attribute, so Pydantic AI's `invoke_agent memory_extraction` span appears under this name. |
| `chat <model>` | GENERATION | One request to the model provider, with the model name, latency, and input, output, and total token usage. |

The other generation tasks follow the same pattern with their own names, such as `experience_incubation run` and
`memory_rerank run`.

An MCP request produces `MCP mcp.tools.call` as the root observation. FastMCP adds a `TOOL` observation named after the
tool, and the `powercontext <operation>` span and its stages nest beneath it. Readiness probes are deliberately not
traced.

Span attributes appear in each observation's metadata as `attributes.<name>`, and resource attributes as
`resourceAttributes.<name>`. To find the trace of one request, filter observations on the metadata key
`attributes.powercontext.request.id` with the value of the `X-PowerContext-Request-ID` response header. Failed
operations carry the `ERROR` level and `attributes.error.type`.

Langfuse derives the cost of a generation from its model definitions, which match the model name; models it does not
recognize show usage but no cost until you add a definition under the project's model settings. Token usage and cost
can then be aggregated in the Langfuse dashboards and Metrics API.

Spans are exported in batches, so allow a few seconds before refreshing. Scheduled background activations arrive as
their own traces, as described in the "Background Worker spans" section of
[Trace with Phoenix](trace-with-phoenix.md).

## What is not exported

PowerContext configures inference instrumentation to exclude content. Observations carry model identifiers, token
usage, durations, and error categories. Prompts, model responses, Memory content, search queries, and vectors are
excluded, so the input and output panels of a generation show only the role and part types of each message, never its
text. PowerContext sets no Langfuse user, session, or tag attributes either, so the user and session views stay empty
and traces are located through metadata instead.

## Stop Langfuse

```bash
docker compose down
```

This stops the containers while preserving named volumes. Add `-v` only when you also want to delete the stored
traces and other local Langfuse data.

Span names and attributes follow the Pydantic AI GenAI semantic conventions and can change when that dependency is
upgraded across a major version. Do not treat them as a stable contract.
