---
title: Pydantic AI
description: Review the current Pydantic AI adapter API and its installation status.
status: community
---

# Pydantic AI

`community` · `experimental`

The adapter connects a Pydantic AI Agent to a running PowerContext Server. It provides Memory tools,
automatic context preparation, and optional event capture. Its API and behavior are experimental.

## Install from source

Add the Client and adapter from the same ref to your application environment. The examples use OpenAI:

```bash
uv add "powercontext[client] @ git+https://github.com/oceanbase/powercontext.git@master"
uv add "powercontext-pydantic-ai @ git+https://github.com/oceanbase/powercontext.git@master#subdirectory=integrations/pydantic-ai"
uv add "pydantic-ai-slim[openai]>=2.29,<3"
```

Start a separate Server from the same ref using [Install and run](../get-started/install-and-run.md).
The adapter requires `powercontext[client]>=0.0.3`; use the matching current source for these examples.
For another provider, replace the `openai` extra and model string.

## Attach the preview capability

The example below uses OpenAI. For another provider, install the matching `pydantic-ai-slim` provider extra and
change the model string.

Attach the capability to an Agent:

```python
from pydantic_ai import Agent
from powercontext_pydantic_ai import PowerContext

agent = Agent(
    "openai:gpt-5.2",
    capabilities=[PowerContext()],
)
```

The capability adds `powercontext_search`, `powercontext_remember`, and `powercontext_context`. It also requests
`prepare_context` from the latest textual user prompt and prepends at most one untrusted evidence block per run. A new
run prepares context again even when it starts from the previous run's message history. The query is the prompt,
trimmed, with recognized credentials replaced by `[REDACTED]` as described in
[automatic prompt privacy](index.md#automatic-prompt-privacy). A prompt longer than 8,192 characters or 8,192 UTF-8
bytes recalls from its most recent text, cut to the last 8,192 characters and then to the last 8,192 bytes.

Use only the toolset when automatic preparation and capture are not wanted:

```python
from pydantic_ai import Agent
from powercontext_pydantic_ai import PowerContextToolset

agent = Agent("openai:gpt-5.2", toolsets=[PowerContextToolset()])
```

## Set environment configuration

```bash
export POWERCONTEXT_PYDANTIC_AI_BASE_URL=http://127.0.0.1:8000
export POWERCONTEXT_PYDANTIC_AI_TOKEN=opaque-server-token
```

| Variable | Default | Validation and behavior |
| --- | --- | --- |
| `POWERCONTEXT_PYDANTIC_AI_BASE_URL` | `http://127.0.0.1:8000` | HTTP(S), without credentials, query, or fragment |
| `POWERCONTEXT_PYDANTIC_AI_ALLOW_INSECURE_HTTP` | `false` | Explicitly permit non-loopback plaintext HTTP |
| `POWERCONTEXT_PYDANTIC_AI_TOKEN` | unset | Bare printable token stored as `SecretStr` |
| `POWERCONTEXT_PYDANTIC_AI_SCOPE_ID` | unset | Existing explicit Server Scope, up to 256 characters; unset selects the Server default |
| `POWERCONTEXT_PYDANTIC_AI_TIMEOUT` | `10` | Positive seconds |
| `POWERCONTEXT_PYDANTIC_AI_MAX_BYTES` | `8000` | `512` to `32768` prepared-context bytes |
| `POWERCONTEXT_PYDANTIC_AI_CAPTURE_EVENTS` | `false` | Opt in to visible event capture |
| `POWERCONTEXT_PYDANTIC_AI_CAPTURE_CHECKPOINT_EVERY` | `5` | `1` to `100` successful events per flush |
| `POWERCONTEXT_PYDANTIC_AI_CAPTURE_MAX_BYTES` | `8192` | `512` to `32768` UTF-8 bytes per event |

Unlike the Codex and Claude Code plugin settings that accept a complete authorization value, this adapter accepts a
bare token. Do not include `Bearer ` or pass a complete `Authorization` header; the public Client adds the scheme.

Loopback HTTP is allowed by default; non-loopback HTTP requires the opt-in above or
`PowerContextSettings(allow_insecure_http=True)`. HTTPS certificate validation stays enabled. See
[Connect to a remote Server](../operate/connect-remote-server.md) for common environment settings and endpoint-bound
saved consent. The framework adapter does not have a setup installer.

Both `PowerContext` and `PowerContextToolset` accept a `PowerContextSettings` instance, a stable `id` (default
`powercontext`), and a fixed or callable `scope_id`:

```python
from pydantic_ai import RunContext
from powercontext_pydantic_ai import PowerContext, PowerContextSettings

settings = PowerContextSettings(timeout=5, max_bytes=4096)


def tenant_scope(ctx: RunContext[dict[str, str]]) -> str:
    return ctx.deps["powercontext_scope_id"]


capability = PowerContext(settings=settings, scope_id=tenant_scope)
```

The callback runs once per Agent run. Scope precedence is constructor string or callback, then environment `SCOPE_ID`.
The adapter sends that explicit ID, or `None`, to `resolve_scope_binding` once per run and reuses the returned Scope ID
for every recall, capture, flush, and tool call in that run. Explicit IDs must identify existing Server Scopes; when
no ID is configured, the Server default is selected. The adapter does not inspect cwd, Git metadata, or paths to
create Scope IDs.

## Decide whether to capture events

Capture is off by default. Set `POWERCONTEXT_PYDANTIC_AI_CAPTURE_EVENTS=true` only when sending the initial user text,
visible model text and tool calls, and completed tool arguments and results to the configured scope is acceptable.
Thinking/reasoning content is excluded. A user prompt that contains a recognized credential is not captured; see
[automatic prompt privacy](index.md#automatic-prompt-privacy). Events are redacted for credential-like keys and known
environment/Codex credentials, rendered within the configured byte limit, and stored under
`powercontext.pydantic-ai-capture-event/v1`.

Every successful Capture advances the run-local Source position. A checkpoint Flush runs after the configured number
of captures, and `after_run` flushes any remaining Source. Parallel tool results receive unique sequence numbers under
a run-local lock. Recall, Capture, and Flush fail open during Server failures; explicit tool failures become
`ModelRetry`. The first HTTP 401 or 403 logs one credential-free configuration warning.

Captured project content can remain sensitive after credential redaction. Protect the Server, scope, database, and
logs accordingly.

## Leave prompt-time context to the gateway

When the Agent sends its model requests through the PowerContext gateway, the gateway prepares and injects context for
each model request and, with `PC_GATEWAY_SEDIMENT=on`, captures the turn. Core checks every request the gateway forwards
and rejects a prepared-context block that the gateway did not inject, in every privacy mode: Core answers HTTP 422 and
the gateway returns HTTP 503 `gate_paused`. Two parts of this adapter produce such a block:

- The `PowerContext` capability adds one to each run. Use `PowerContextToolset` instead; the gateway captures the turns.
- `powercontext_context` returns one as its tool result, which makes Core reject the request that returns it to the
  model. Build the toolset with `include_context_tool=False`, which leaves the tool out and drops its line from the
  toolset instructions.

An `openai:` model string already selects the Responses model; build it explicitly to point it at the gateway's
`/gateway/v1`:

```python
import os

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from powercontext_pydantic_ai import PowerContextToolset

model = OpenAIResponsesModel(
    "approved-model",
    provider=OpenAIProvider(base_url="http://127.0.0.1:8787/gateway/v1", api_key=os.environ["PC_GATEWAY_KEY"]),
)
toolset = PowerContextToolset(include_context_tool=False)
agent = Agent(model, toolsets=[toolset])

result = await agent.run(
    prompt,
    message_history=history,
    model_settings={"extra_headers": {"X-PowerContext-Conversation-ID": conversation_id}},
)
```

`PC_GATEWAY_KEY` holds the gateway's `PC_GATEWAY_API_KEY` value. The header names the conversation: use one stable ID
per conversation, of 1 to 256 visible ASCII characters, and never one ID for every conversation. A combined deployment
(`PC_GATEWAY_CORE_UDS`) prepares once per user message of a conversation and replays that context to the tool-loop steps
of the run. Without the header, it infers the conversation from the opening of the request: Pydantic AI sends the full
message history on every request unless `openai_previous_response_id` or `openai_conversation_id` is set, and a history
that opens with system messages and a user prompt qualifies. The header still keeps apart conversations that open the
same way, and gives capture and prefix extraction a conversation identity that stays the same across gateway restarts;
an inferred ID is local to the gateway process and serves only preparation replay. A standalone gateway
(`PC_GATEWAY_CORE_BASE`) injects only with `PC_GATEWAY_INJECTION=on`. See [Gateway context
injection](../operate/deploy-server.md#gateway-context-injection).

Start behind the gateway with new message history. History from runs with the capability keeps the recalled context as a
system prompt part, and history in which the model called `powercontext_context` keeps that result; Core rejects a
request that carries either. History from runs behind the gateway carries no injected context and can be passed to the
next run.

Use the Responses route. There the gateway calls the model without streaming, captures the checked response, and replays
it as a stream to a client that streams. An `openai-chat:` model uses the Chat Completions route, where an enforcing
Core withholds a streamed run (`run_stream`) and the gateway returns HTTP 400. With a non-enforcing Core, the gateway
releases the stream unchanged and, with `PC_GATEWAY_SEDIMENT=on`, captures the completed turn from it.

Configure Core and the gateway as well:

- Run Core in a non-enforcing privacy mode (`disabled`, `shadow`, or `advisory`) unless every request meets the
  enforcing rules: an enforcing Core withholds a Responses request without `store: false` or without an output limit
  within the target's token budget, and the gateway returns HTTP 400. Restart the gateway after changing the mode,
  because it discovers the mode at startup.
- For this Responses route, set `PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES` to a Core target whose endpoint,
  `openai-responses` protocol and model match the request. Without it, the route returns HTTP 501
  `responses_route_disabled`. Set `PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES` to the provider key and
  `PC_GATEWAY_SEDIMENT=on`. Do not set `PC_GATEWAY_UPSTREAM_URL*`; these legacy variables prevent startup.
- Set a nonempty `PC_GATEWAY_API_KEY` even on loopback, and set `PC_GATEWAY_CORE_TOKEN` to an authenticated service
  identity. Core access control must be `enforced`; grant it `gateway.user` on the selected Scope and `server.observer`
  on the Server resource for `POWERCONTEXT_SERVER_ACCESS_DEPLOYMENT_ID` (default `powercontext`). Because this guide
  enables capture, grant `scope.contributor` (which includes `scope.read`) rather than `scope.viewer`.
  See [Gateway authentication and target requirements](../operate/deploy-server.md#gateway-model-destination).
- The gateway prepares and captures every conversation in the one Scope that `PC_GATEWAY_DEFAULT_SCOPE` names. Set it to
  the Scope the toolset resolves, so that the tools search and write the Memory the gateway recalls.

## Compare the MCP fallback

Connecting PowerContext MCP requires no adapter package, but it is a lower-capability option for Pydantic AI. MCP
provides explicit tools; it does not automatically call `prepare_context`, capture trajectory events, or Flush at
checkpoints and run completion.

The preview supports ordinary Pydantic AI runs. Durable execution through Temporal, DBOS, Prefect, or similar systems
is not yet validated. Handoff, Candidate Review, Experience, and Skill operations are not included.
