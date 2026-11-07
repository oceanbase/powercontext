---
title: LangChain
description: Add bounded PowerContext recall and completed-turn Source capture to a LangChain agent.
status: community
---

# LangChain

`community`

`PowerContextMiddleware` connects a LangChain `create_agent` agent to a separately running PowerContext Server. Before
each model call it requests one bounded `PreparedContext` for the latest user message. With automatic capture enabled,
it captures the latest user message and final assistant response as one Content Source after a successful agent run.

The middleware uses LangChain's public `AgentMiddleware` API. Recalled content modifies only the current
`ModelRequest`; it never enters agent state or a checkpointer.

## Install

The middleware source is packaged separately as `powercontext-langchain` and requires LangChain 1.3 or later. It is
not currently published on PyPI:

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext server run
```

Keep the Server running, then install the middleware in the LangChain application's environment:

```bash
uv pip install "powercontext-langchain @ git+https://github.com/oceanbase/powercontext.git@master#subdirectory=integrations/langchain"
```

Skip the Server installation when the application already connects to a separately managed Server. From a repository
checkout, install the middleware with `uv pip install ./integrations/langchain`.

The package owns its Scope, Settings, Client wiring, and Middleware implementation. It does not import or depend on
the separate `powercontext-langgraph` adapter. LangChain itself uses LangGraph internally, so LangGraph can still
appear as LangChain's transitive dependency.

## Add the middleware

```python
from langchain.agents import create_agent
from powercontext_langchain import PowerContextMiddleware, PowerContextScope

agent = create_agent(
    model,
    tools=application_tools,
    middleware=[PowerContextMiddleware()],
    context_schema=PowerContextScope,
)

result = await agent.ainvoke(
    {"messages": [{"role": "user", "content": "How should we deploy this service?"}]},
    context=PowerContextScope(),
)
```

The middleware supports synchronous `invoke` and `stream`; an async application must use the async agent methods
instead of calling a synchronous method inside its event loop.

## Recall lifecycle

For every model step, the middleware:

1. reads the latest human message without changing state;
2. calls `/v1/context/prepare` with the resolved scope and configured byte limit;
3. appends a separate text block to the current system message;
4. labels the entire block as untrusted historical evidence.

Tool loops can therefore receive fresh context on later model steps, including Memory explicitly written by a tool
during the same run. The override is local to each model request and cannot accumulate in checkpointed message history.

## Completed-turn capture

`PowerContextMiddleware()` disables `auto_capture` by default because user and model content can contain credentials or
other sensitive data. Enable it only when the application's transcript policy permits durable storage:

```python
middleware = PowerContextMiddleware(auto_capture=True)
```

Once enabled, a successful agent run captures the latest user message and final non-empty assistant response through
`/v1/sources/content`. A successful structured result is serialized from LangChain's `structured_response`. Capture does
not store the recalled system block, tool outputs, or intermediate tool-calling model messages.

Capture creates Source evidence, not an inferred Memory entry. The configured scheduler or an explicit
`flush_memory` operation performs the normal Source-to-Memory extraction later. This keeps lineage intact and avoids
treating raw model output as an already reviewed durable fact. Captured content is bounded but is not guaranteed to be
free of secrets, so applications must apply their own input and output policy before opting in.

LangChain does not run `after_agent` when a model or tool aborts the run, so a failed run without a final response is
not captured.

## Configure connection and scope

The middleware owns `PowerContextScope` and uses its own `POWERCONTEXT_LANGCHAIN_*` settings; it does not reuse the
LangGraph adapter's scope or environment prefix:

| Variable | Default | Purpose |
| --- | --- | --- |
| `POWERCONTEXT_LANGCHAIN_BASE_URL` | `http://127.0.0.1:8000` | PowerContext Server URL |
| `POWERCONTEXT_LANGCHAIN_ALLOW_INSECURE_HTTP` | `false` | Explicitly permit non-loopback plaintext HTTP |
| `POWERCONTEXT_LANGCHAIN_TOKEN` | unset | Bare bearer token passed to the Client |
| `POWERCONTEXT_LANGCHAIN_SCOPE_ID` | unset | Existing Server Scope to use instead of the Server default |
| `POWERCONTEXT_LANGCHAIN_TIMEOUT` | `10` | Client timeout in seconds |
| `POWERCONTEXT_LANGCHAIN_MAX_BYTES` | `8000` | Prepared-context size limit |

Loopback HTTP is allowed by default; non-loopback HTTP requires explicit consent through the environment variable
above or `allow_insecure_http=True` on `PowerContextLangChainSettings` or `PowerContextScope`. HTTPS certificate
validation stays enabled. Common environment settings and endpoint-bound saved consent are described in
[Connect to a remote Server](../operate/connect-remote-server.md). The framework adapter does not have a setup installer.

An explicit `PowerContextScope` value wins over environment configuration and must identify an existing Server Scope.
If neither is set, the Server default Scope is used. The adapter never derives a Scope ID from Git, a path, the Agent,
or the prompt. The token remains in Client configuration and never enters agent state or message content.

## Failure behavior

Recall and capture are best-effort. Server unavailability, an invalid response, or request validation failure does not
replace the model response or stop the agent. HTTP 401 and 403 are logged once at error level without content or token
values; transient and unexpected failures are available at debug level.


### Adapter-owned GenAI tracing

The LangChain middleware marks its own scope-resolution, recall and optional completed-turn capture calls as GenAI context-service dependencies. It reuses the application-owned OpenTelemetry provider and current parent context. It does not create an exporter, configure logging or import Langfuse. Applications keep their normal middleware configuration; no span allowlist or private Langfuse export predicate is required for these client spans.

Within that adapter-owned context, client spans carry `gen_ai.provider.name=powercontext`. Recall uses `gen_ai.operation.name=retrieval` and the logical `gen_ai.data_source.id=powercontext`. Supporting calls use bounded custom operation names such as `powercontext.resolve_scope_binding` and `powercontext.capture_content_source`; they are not labeled as LLM inference or model-selected tool calls. These attributes contain no query, scope identifier, source body, result, token count or credentials. Standalone SDK calls outside the scoped adapter retain their existing operational-only semantics.

A configured provider/exporter and server-side telemetry are still application/deployment responsibilities. Semantic visibility does not configure a destination or guarantee export by an arbitrary backend. Success, errors, cancellation and fail-open recall behavior are unchanged.

### Optional recall input/output

Use `PowerContextMiddleware(trace_content=True)` to record the latest recall query and the returned context on the
`PowerContextMiddleware recall` observation. This is separate from `auto_capture`, which writes completed turns to
PowerContext storage. The default is `False`: recall text may contain private user and memory content.
The library owns the observation and parent propagation; applications only configure their existing exporter.
Input and output use OpenInference `input.value` / `output.value` JSON strings, each bounded to 16 KiB of UTF-8
content with explicit truncation flags. Empty results record the returned null or empty output and the actual context status.
Request failures do not fabricate an output. This observation is not a model inference or model-selected tool call.
Server operational spans remain content-free, and historical spans cannot be backfilled from this setting.

The adapter owns its tracing helpers and imports with the released PowerContext 1.5.0 client alone;
installing a Server in a separate tool environment does not update the application's core client.
Adapter-owned optional recall I/O works with that core. SDK dependency-span enrichment requires a core
implementing the local `powercontext.genai_client` OTel context marker (included with this change).
A 1.5.0 core ignores the marker and retains its existing operational spans and consumer filtering behavior;
install matching core/adapter artifacts to obtain the complete default-filter tracing fix.
The marker is local execution context, not network baggage, credentials, or a provider/exporter configuration.
