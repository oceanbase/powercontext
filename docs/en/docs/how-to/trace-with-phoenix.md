---
title: Trace with Phoenix
description: Export PowerContext transport, application, and inference spans to a local Phoenix container.
---

# Trace with Phoenix

PowerContext exports OpenTelemetry spans for transport and application operations. When tracing is enabled, the
generation and embedding calls that PowerContext itself constructs are traced too, so one trace shows the request, the
Memory operation, and the model calls underneath it.

This guide sends those spans to [Phoenix](https://github.com/Arize-ai/phoenix) running locally.

## Start Phoenix

```bash
docker run -d --name powercontext-phoenix -p 6006:6006 arizephoenix/phoenix:20.1.0
```

Phoenix serves both its UI and its OTLP HTTP receiver on port `6006`. Open <http://localhost:6006> to confirm it is
running. Pin an explicit tag so the endpoint and UI layout match this guide.

## Install the export dependency

Recording and export require the `tracing-otlp` extra:

```bash
uv tool install "powercontext[cli,server,tracing-otlp] @ git+https://github.com/oceanbase/powercontext.git@master"
```

Without this extra, enabling tracing fails at startup with an explicit error instead of silently dropping spans.

## Configure and start the Server

Enable tracing, point the exporter at Phoenix, and configure a generation model so inference spans have something to
record:

```bash
export POWERCONTEXT_SERVER_TRACING_ENABLED=true
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006
export OTEL_SERVICE_NAME=powercontext-server
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
powercontext server run
```

The OpenTelemetry SDK appends `/v1/traces` to `OTEL_EXPORTER_OTLP_ENDPOINT`, so the spans arrive at
`http://localhost:6006/v1/traces`. Use `OTEL_EXPORTER_OTLP_HEADERS` for a Phoenix deployment that requires
authentication. Set the provider credentials your generation model needs; PowerContext never records them.

## Trigger one inference request

Capture a Source, then convert it into Memory:

```bash
curl -X POST http://localhost:8000/v1/sources/content \
  -H 'content-type: application/json' \
  -d '{"scope_id":"project:demo","source_id":"task-1","content":"I always book aisle seats."}'
```

```bash
curl -X POST http://localhost:8000/v1/memory/flush \
  -H 'content-type: application/json' \
  -d '{"scope_id":"project:demo"}'
```

Memory extraction runs during the flush, not during capture.

## Read the trace

Open <http://localhost:6006>, select the `default` project, and open the most recent trace for
`powercontext-server`. The flush produces four nested spans in one trace:

| Span | Meaning |
| --- | --- |
| `HTTP flush_memory` | The inbound HTTP request. `powercontext.request.id` matches the `X-PowerContext-Request-ID` response header. |
| `powercontext flush_memory` | The application operation, independent of the transport that invoked it. |
| `invoke_agent memory_extraction` | One PowerContext generation task. The name identifies the purpose, not the model. |
| `chat <model>` | One request to the model provider, with token usage and latency. |

The other PowerContext generation tasks appear under the same convention: `experience_incubation`,
`experience_generation`, `skill_generation`, `handoff_generation`, and `memory_rerank`. When an embedding model is
configured, embedding calls appear as `embeddings <model>` spans under the operation that triggered them.

Spans are exported in batches, so allow a few seconds before refreshing. An MCP request produces
`MCP mcp.tools.call` in place of the `HTTP` span. Readiness probes are deliberately not traced, so health checks do not
create single-span traces.

## What is not exported

PowerContext configures inference instrumentation to exclude content. Spans carry model identifiers, token usage,
durations, and error categories. Prompts, model responses, Memory content, and vectors are excluded, and message
attributes record only the shape of each message rather than its text.

One exception applies to generation. When a model returns output that does not satisfy the requested schema,
Pydantic AI retries with feedback that quotes the model's own invalid output, and it records that feedback in the
`gen_ai.input.messages` and `pydantic_ai.all_messages` attributes regardless of the content setting. For Memory
extraction, that quoted output is the proposed Memory content. Treat the tracing backend as a system that may receive
model output on this retry path, and restrict access to it accordingly.

## Stop Phoenix

```bash
docker rm -f powercontext-phoenix
```

Span names and attributes follow the Pydantic AI GenAI semantic conventions and can change when that dependency is
upgraded across a major version. Do not treat them as a stable contract.
