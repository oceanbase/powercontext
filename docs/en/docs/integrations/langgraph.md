---
title: LangGraph
description: Connect a LangGraph graph to a running PowerContext Server for durable Memory and bounded recall.
status: community
---

# LangGraph

`community`

`powercontext-langgraph` connects a [LangGraph](https://langchain-ai.github.io/langgraph/) graph to a running
PowerContext Server through the public Python Client. It integrates at the node and tool level, using LangGraph
primitives that are stable public API. It never starts or embeds the Server.

## Install

The package is not yet published to PyPI, so install it from source alongside a running Server:

```bash
uv pip install "powercontext-langgraph @ git+https://github.com/oceanbase/powercontext.git@master#subdirectory=integrations/langgraph"
powercontext server run
```

From a checkout you can install the local path instead: `uv pip install ./integrations/langgraph`. The adapter is not
currently published on PyPI, so use one of these source installations.

The package depends on `powercontext[client]`, `langgraph`, `langchain-core`, and `pydantic-settings`. It does not
pull in the Server; point it at a Server you run separately.

## Three components

- `powercontext_tools()` returns `langchain_core.tools.BaseTool` instances — `powercontext_search`,
  `powercontext_remember`, and `powercontext_context` — for model-initiated Memory read and write. Add them to a
  `ToolNode` or any tool list.
- `PowerContextRecall` is a `pre_model_hook`. It reads the latest human message, requests one bounded
  `PreparedContext`, and supplies a complete, ordered model input on the `llm_input_messages` channel — the prepared
  content as a single leading system message, followed by the run's messages. That context reaches the model but
  never enters the persisted `messages` history, so it cannot accumulate across turns under a checkpointer.
- `PowerContextScope` is a dataclass intended for the graph `context_schema`. It carries the durable scope, and
  optional per-run connection overrides, for one run.

Use it as the `pre_model_hook` of `create_react_agent`, which wires the `llm_input_messages` channel for you:

```python
from langgraph.prebuilt import create_react_agent
from powercontext_langgraph import PowerContextRecall, PowerContextScope, powercontext_tools

agent = create_react_agent(
    model,
    tools=powercontext_tools(),
    pre_model_hook=PowerContextRecall(),
    context_schema=PowerContextScope,
    checkpointer=my_checkpointer,
)
await agent.ainvoke(state, context=PowerContextScope())
```

The recall hook and the Memory tools are async, so drive the graph with `ainvoke`/`astream`; a synchronous
`invoke`/`stream` cannot run them.

In a custom graph, add an `llm_input_messages` channel to the state and have the model step read it:

```python
from typing import Annotated
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph import StateGraph, START
from langgraph.graph.message import add_messages
from powercontext_langgraph import PowerContextRecall, PowerContextScope

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    llm_input_messages: list[BaseMessage]

def call_model(state: AgentState):
    model_input = state.get("llm_input_messages") or state["messages"]
    ...

builder = StateGraph(AgentState, context_schema=PowerContextScope)
builder.add_node("recall", PowerContextRecall())
builder.add_node("model", call_model)
builder.add_edge(START, "recall")
builder.add_edge("recall", "model")

graph = builder.compile(checkpointer=my_checkpointer)
await graph.ainvoke(state, context=PowerContextScope())
```

The recall hook and the tools read the active `PowerContextScope` from the LangGraph runtime, so a single value on
`context` configures the whole run. Outside a run — for example when a tool is exercised directly — they fall back to
the environment settings below.

## Configure the connection

Configuration is read through pydantic-settings with the prefix `POWERCONTEXT_LANGGRAPH_`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `POWERCONTEXT_LANGGRAPH_BASE_URL` | `http://127.0.0.1:8000` | PowerContext Server URL |
| `POWERCONTEXT_LANGGRAPH_ALLOW_INSECURE_HTTP` | `false` | Explicitly permit non-loopback plaintext HTTP |
| `POWERCONTEXT_LANGGRAPH_TOKEN` | unset | Bare token forwarded to `PowerContextClient` |
| `POWERCONTEXT_LANGGRAPH_SCOPE_ID` | unset | Existing Server Scope to use instead of the Server default |
| `POWERCONTEXT_LANGGRAPH_TIMEOUT` | `10` | Client timeout in seconds |
| `POWERCONTEXT_LANGGRAPH_MAX_BYTES` | `8000` | Prepared-context size limit |

`PowerContextScope(base_url=..., token=..., timeout=...)` overrides these per run. A field left as `None` on the
scope falls back to the environment value.

Loopback HTTP is allowed by default; non-loopback HTTP requires explicit consent through the environment variable
above or `allow_insecure_http=True` on `PowerContextLangGraphSettings` or `PowerContextScope`. HTTPS certificate
validation stays enabled. Common environment settings and endpoint-bound saved consent are described in
[Connect to a remote Server](../operate/connect-remote-server.md). The framework adapter does not have a setup installer.

`POWERCONTEXT_LANGGRAPH_TOKEN` carries a **bare token**, not a complete `Authorization` header value. This differs from the
`POWERCONTEXT_*_AUTHORIZATION` convention used by the Codex, Claude Code, and DeepSeek Harness plugins.
`PowerContextClient` accepts the bare token and composes `Authorization: Bearer <token>` internally. The token is
used only to authenticate the Client; it never appears in graph state or agent-visible message content.

## Resolve the scope

The adapter asks the Server to resolve the Scope for every operation:

1. an explicit, existing `scope_id` on `PowerContextScope`, or `POWERCONTEXT_LANGGRAPH_SCOPE_ID`;
2. otherwise the Server default Scope.

Scope IDs are Server-owned opaque identifiers. The adapter never derives one from the process working directory, a
Git remote, or a filesystem path. An explicit ID is validated by the Server before the operation continues; obtain it
from the Scope API rather than inventing it locally.

## Recall query

`PowerContextRecall` sends the latest human message, trimmed, with recognized credentials replaced by `[REDACTED]` as
described in [automatic prompt privacy](index.md#automatic-prompt-privacy). A message longer than 8,192 characters or
8,192 UTF-8 bytes recalls from its most recent text, cut to the last 8,192 characters and then to the last 8,192 bytes.

## Treat recalled context as untrusted history

`PowerContextRecall` injects the prepared content as a system message that starts with
`PowerContext host-supplied context`, followed by the Server's prepared content. The trust policy at the top of that
content labels everything after it as untrusted historical evidence.
Memory content originates from prior model output and user input; presenting it as authoritative system instruction
would extend the prompt-injection surface to historical data. The model must still check current code, the current
user request, and system instructions before acting on recalled content.

When the Server returns an empty result the node adds nothing and returns the state unchanged.

## Fail open when the Server is unavailable

Server unavailability must not interrupt graph execution. `PowerContextRecall` handles Client errors internally and
returns the state unmodified, so the graph still reaches its end. A configuration fault — HTTP 401 or 403, usually a
missing or wrong `POWERCONTEXT_LANGGRAPH_TOKEN` — is logged once at error level; other transient faults are logged at
debug level. The Memory tools return a short `(PowerContext unavailable: ...)` string rather than raising, so a model
that called a tool can retry or choose another strategy instead of concluding that no memory exists.

## Leave prompt-time context to the gateway

When the graph sends its model requests through the PowerContext gateway, the gateway prepares and injects context for
each model call and, with `PC_GATEWAY_SEDIMENT=on`, captures the turn. Core checks every request the gateway forwards
and rejects a prepared-context block that the gateway did not inject, in every privacy mode: Core answers HTTP 422 and
the gateway returns HTTP 503 `gate_paused`. Two parts of this package produce such a block:

- `PowerContextRecall` adds one to each model input. Leave the hook out.
- `powercontext_context` returns one as its tool result, which stays in the `messages` history and makes Core reject
  every later request of the thread. Offer only the other tools.

```python
import os

from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from powercontext_langgraph import PowerContextScope, powercontext_remember, powercontext_search


def gateway_agent(thread_id: str):
    model = ChatOpenAI(
        model="approved-model",
        base_url="http://127.0.0.1:8787/gateway/v1",
        api_key=os.environ["PC_GATEWAY_KEY"],
        use_responses_api=True,
        default_headers={"X-PowerContext-Conversation-ID": thread_id},
    )
    return create_react_agent(
        model,
        tools=[powercontext_search, powercontext_remember],
        context_schema=PowerContextScope,
        checkpointer=my_checkpointer,
    )
```

The hook never writes recalled context to the checkpointed history, so an existing thread can continue behind the
gateway. A thread in which the model called `powercontext_context` keeps that result; start a new thread instead. In a
custom graph, remove the recall node and have the model step read `messages`: `llm_input_messages` is graph state, so
the checkpointer keeps the last recalled context there.

`PC_GATEWAY_KEY` holds the gateway's `PC_GATEWAY_API_KEY` value. The header names the conversation: use the thread ID or
another stable ID per conversation, of 1 to 256 visible ASCII characters, and never one ID for every conversation. A
combined deployment (`PC_GATEWAY_CORE_UDS`) prepares once per user message of a conversation and replays that context to
the tool-loop steps of the message. Without the header, it infers the conversation from the opening of the request:
`ChatOpenAI` sends the thread's full history on every call unless `use_previous_response_id=True`, and a history that
opens with system messages and a user message qualifies. The header still keeps apart conversations that open the same
way, and gives capture and prefix extraction a conversation identity that stays the same across gateway restarts; an
inferred ID is local to the gateway process and serves only preparation replay. A standalone gateway
(`PC_GATEWAY_CORE_BASE`) injects only with `PC_GATEWAY_INJECTION=on`. See [Gateway context
injection](../operate/deploy-server.md#gateway-context-injection).

Use the Responses route (`use_responses_api=True`). There the gateway calls the model without streaming, captures the
checked response, and replays it as a stream to a client that streams. On the Chat Completions route, an enforcing Core
withholds a streamed response and the gateway returns HTTP 400. With a non-enforcing Core, the gateway releases the
stream unchanged and, with `PC_GATEWAY_SEDIMENT=on`, captures the completed turn from it.

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
  the Scope the tools resolve, so that they search and write the Memory the gateway recalls.

## Why this package does not implement `BaseStore`

`BaseStore` is LangGraph's cross-thread long-term memory interface and appears to be the intended seam for an
integration of this kind. It is not usable as one. `BaseStore.batch` must service `GetOp`, `PutOp` (upsert and
delete), and `SearchOp`. Only `SearchOp` maps onto the PowerContext Memory model; the others require get, upsert, and
delete by a caller-assigned key, which Memory does not provide — entry identity and versioning are assigned by the
Server. Implementing only search and raising for the rest produces an object that passes assembly-time validation but
fails at runtime inside unrelated nodes or tools, which is worse than providing no store. The adapter therefore
integrates at the node and tool level and does not occupy the `store` parameter of `compile()`.

## Current scope

Included: Memory read and write, and bounded context preparation.

Not included: automatic trajectory capture, checkpointing, Handoff, Artifact Candidate review, and Experience or
Skill generation. Use `powercontext_remember` for explicit writes; automatic capture of a run as Source evidence is
not part of this adapter.
