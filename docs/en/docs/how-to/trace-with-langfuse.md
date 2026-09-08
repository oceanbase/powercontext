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

## Start Langfuse

Langfuse self-hosting runs several services (web, worker, PostgreSQL, ClickHouse, Redis, and MinIO) with Docker
Compose:

```bash
git clone https://github.com/langfuse/langfuse.git
cd langfuse
docker compose up -d
```

Open <http://localhost:3000>, create a user, an organization, and a project, then create an API key pair in the project
settings. Keep the public key (`pk-lf-...`) and the secret key (`sk-lf-...`) at hand; they authenticate the exporter
below. The OTLP endpoint requires Langfuse v3.22.0 or later. This guide was verified with Langfuse 4.10.0.

For a reproducible local setup, [headless initialization](https://langfuse.com/self-hosting/headless-initialization)
creates the organization, project, user, and keys from environment variables instead of the UI. Langfuse Cloud works
the same way as a self-hosted instance: skip the compose step and replace `http://localhost:3000` below with the base
URL of your region, such as `https://cloud.langfuse.com` or `https://us.cloud.langfuse.com`.

## Install the export dependency

Recording and export require the `tracing-otlp` extra:

```bash
uv tool install --force "powercontext[cli,server,tracing-otlp] @ git+https://github.com/oceanbase/powercontext.git@master"
```

Without this extra, enabling tracing fails at startup with an explicit error instead of silently dropping spans.

## Configure and start the Server

Langfuse authenticates OTLP requests with HTTP Basic authentication built from the project keys. Enable tracing, point
the exporter at Langfuse, and configure a generation model so inference spans have something to record:

```bash
export LANGFUSE_PUBLIC_KEY=pk-lf-replace-me
export LANGFUSE_SECRET_KEY=sk-lf-replace-me
LANGFUSE_AUTH=$(printf '%s:%s' "$LANGFUSE_PUBLIC_KEY" "$LANGFUSE_SECRET_KEY" | base64 | tr -d '\n')

export POWERCONTEXT_SERVER_TRACING_ENABLED=true
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:3000/api/public/otel
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic ${LANGFUSE_AUTH},x-langfuse-ingestion-version=4"
export OTEL_SERVICE_NAME=powercontext-server
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
powercontext server run
```

The OpenTelemetry SDK appends `/v1/traces` to `OTEL_EXPORTER_OTLP_ENDPOINT`, so the spans arrive at
`http://localhost:3000/api/public/otel/v1/traces`, the traces endpoint Langfuse expects. Langfuse accepts OTLP over
HTTP only, which is the protocol of the exporter installed by the `tracing-otlp` extra. The
`x-langfuse-ingestion-version=4` header makes Langfuse process the spans immediately; without it, Langfuse documents
that ingestion can lag by up to ten minutes. Set the provider credentials your generation model needs; PowerContext
records neither them nor the exporter headers.

## Trigger one inference request

Set `POWERCONTEXT_SCOPE_ID` to an existing ID returned by `create_scope`, capture a Source, then convert it into
Memory:

```bash
curl -X POST http://localhost:8000/v1/sources/content \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${POWERCONTEXT_SCOPE_ID}\",\"source_id\":\"task-1\",\"content\":\"I always book aisle seats.\"}"
```

```bash
curl -X POST http://localhost:8000/v1/memory/flush \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${POWERCONTEXT_SCOPE_ID}\"}"
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
their own traces, as described in the "Scheduled background spans" section of
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

Add `-v` to delete the stored traces as well.

Span names and attributes follow the Pydantic AI GenAI semantic conventions and can change when that dependency is
upgraded across a major version. Do not treat them as a stable contract.
