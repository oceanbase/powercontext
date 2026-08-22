---
title: Configure LangGraph
description: Connect a LangGraph graph to a running PowerContext Server for durable Memory and bounded recall.
---

# Configure LangGraph

`powercontext-langgraph` connects a [LangGraph](https://langchain-ai.github.io/langgraph/) graph to a running
PowerContext Server through the public Python Client. It integrates at the node and tool level, using LangGraph
primitives that are stable public API. It never starts or embeds the Server.

## Install

```bash
uv pip install powercontext-langgraph
powercontext server run
```

The package depends on `powercontext[client]`, `langgraph`, `langchain-core`, and `pydantic-settings`. It does not
pull in the Server; point it at a Server you run separately.

## Three components

- `powercontext_tools()` returns `langchain_core.tools.BaseTool` instances — `powercontext_search`,
  `powercontext_remember`, and `powercontext_context` — for model-initiated Memory read and write. Add them to a
  `ToolNode` or any tool list.
- `PowerContextRecall` is a callable usable as a graph node or as a `pre_model_hook`. It reads the latest human
  message, requests one bounded `PreparedContext`, and prepends the result as a system message before the model step.
- `PowerContextScope` is a dataclass intended for the graph `context_schema`. It carries the durable scope, and
  optional per-run connection overrides, for one run.

```python
from langgraph.graph import StateGraph, START
from langgraph.prebuilt import ToolNode
from powercontext_langgraph import PowerContextRecall, PowerContextScope, powercontext_tools

builder = StateGraph(AgentState, context_schema=PowerContextScope)
builder.add_node("recall", PowerContextRecall())
builder.add_node("model", call_model)
builder.add_node("tools", ToolNode([*my_tools, *powercontext_tools()]))
builder.add_edge(START, "recall")
builder.add_edge("recall", "model")

graph = builder.compile(checkpointer=my_checkpointer)
graph.invoke(state, context=PowerContextScope(scope_id="git:github.com/acme/api"))
```

The recall node and the tools read the active `PowerContextScope` from the LangGraph runtime, so a single value on
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

`TOKEN` carries a **bare token**, not a complete `Authorization` header value. This differs from the
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

## Scope of this release

In scope: Memory read and write, and bounded context preparation.

Out of scope for this release: automatic trajectory capture, checkpointing, Handoff, Artifact Candidate review, and
Experience or Skill generation. Use `powercontext_remember` for explicit writes; automatic capture of a run as Source
evidence is not part of this adapter.
