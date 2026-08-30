---
title: Configure LangGraph
description: Connect a LangGraph graph to a running PowerContext Server for durable Memory and bounded recall.
---

# Configure LangGraph

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
await agent.ainvoke(state, context=PowerContextScope(scope_id="git:github.com/acme/api"))
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
await graph.ainvoke(state, context=PowerContextScope(scope_id="git:github.com/acme/api"))
```

The recall hook and the tools read the active `PowerContextScope` from the LangGraph runtime, so a single value on
`context` configures the whole run. Outside a run — for example when a tool is exercised directly — they fall back to
the environment settings below.

## Configure the connection

Configuration is read through pydantic-settings with the prefix `POWERCONTEXT_LANGGRAPH_`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `POWERCONTEXT_LANGGRAPH_BASE_URL` | `http://127.0.0.1:8000` | PowerContext Server URL |
| `POWERCONTEXT_LANGGRAPH_TOKEN` | unset | Bare token forwarded to `PowerContextClient` |
| `POWERCONTEXT_LANGGRAPH_SCOPE_ID` | derived | Durable scope shared across runs |
| `POWERCONTEXT_LANGGRAPH_TIMEOUT` | `10` | Client timeout in seconds |
| `POWERCONTEXT_LANGGRAPH_MAX_BYTES` | `8000` | Prepared-context size limit |

`PowerContextScope(base_url=..., token=..., timeout=...)` overrides these per run. A field left as `None` on the
scope falls back to the environment value.

`POWERCONTEXT_LANGGRAPH_TOKEN` carries a **bare token**, not a complete `Authorization` header value. This differs from the
`POWERCONTEXT_*_AUTHORIZATION` convention used by the Codex, Claude Code, and DeepSeek Harness plugins.
`PowerContextClient` accepts the bare token and composes `Authorization: Bearer <token>` internally. The token is
used only to authenticate the Client; it never appears in graph state or agent-visible message content.

## Resolve the scope

The scope for a run is resolved in this order:

1. an explicit `scope_id` on `PowerContextScope`, or `POWERCONTEXT_LANGGRAPH_SCOPE_ID`;
2. a scope derived from the current Git remote;
3. otherwise the adapter raises `MissingScopeError`.

This priority is **inverted** relative to the Codex plugin, which prefers the Git- or path-derived local scope. A
LangGraph deployment is typically a long-running service whose working directory has no relationship to the project,
so explicit configuration is the primary path and Git derivation is the fallback. When neither is available the
adapter raises rather than defaulting to a shared local scope, which would place unrelated tenants together.

## Treat recalled context as untrusted history

`PowerContextRecall` injects the prepared content as a system message labelled as untrusted historical evidence.
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
