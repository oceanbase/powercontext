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
git --version
docker info
docker compose version
uv --version
```

## Start Langfuse

Langfuse self-hosting runs several services (web, worker, PostgreSQL, ClickHouse, Redis, and MinIO) with Docker
Compose. The Compose file in the Langfuse repository contains default database, Redis, MinIO, and application
secrets. The command below uses those defaults and is suitable only for temporary single-machine evaluation. If the
host may be accessed by others or the deployment will run for a long time, replace the values marked `CHANGEME` in
the Compose file first:

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

Langfuse authenticates OTLP requests with HTTP Basic authentication built from the project keys. In Terminal A, enable
tracing, point the exporter at Langfuse, and configure a generation model so `flush_memory` produces a model-call span:

```bash
export LANGFUSE_PUBLIC_KEY=pk-lf-replace-me
export LANGFUSE_SECRET_KEY=sk-lf-replace-me
LANGFUSE_AUTH=$(printf '%s:%s' "$LANGFUSE_PUBLIC_KEY" "$LANGFUSE_SECRET_KEY" | base64 | tr -d '\n')

export POWERCONTEXT_SERVER_TRACING_ENABLED=true
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:3000/api/public/otel
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic $LANGFUSE_AUTH,x-langfuse-ingestion-version=4"
export OTEL_SERVICE_NAME=powercontext-server
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai:gpt-4.1-mini
powercontext server run
```

`powercontext server run` stays in the foreground. Keep Terminal A open and run the checks and requests in Terminal B.

The OpenTelemetry SDK appends `/v1/traces` to `OTEL_EXPORTER_OTLP_ENDPOINT`, so the spans arrive at
`http://localhost:3000/api/public/otel/v1/traces`, the traces endpoint Langfuse expects. Langfuse accepts OTLP over
HTTP only, which is the protocol of the exporter installed by the `tracing-otlp` extra. The
`x-langfuse-ingestion-version=4` header makes Langfuse process the spans immediately; without it, Langfuse documents
that ingestion can lag by up to ten minutes. Set the provider credentials your generation model needs; PowerContext
records neither them nor the exporter headers.

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
3. Run the `flush_memory` request below and confirm in Langfuse's **Traces** view that the trace contains a
   `chat <model>` observation.

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
    --data "{\"title\":\"Langfuse tracing example\",\"summary\":\"Scope for tracing verification\",\"idempotency_key\":\"${POWERCONTEXT_IDEMPOTENCY_KEY}\"}" \
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

Add `-v` to delete the stored traces as well.

Span names and attributes follow the Pydantic AI GenAI semantic conventions and can change when that dependency is
upgraded across a major version. Do not treat them as a stable contract.
