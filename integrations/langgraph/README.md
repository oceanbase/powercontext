# PowerContext for LangGraph

This package connects a [LangGraph](https://langchain-ai.github.io/langgraph/) graph to a running PowerContext
Server through the public Python client. It integrates at the node and tool level, using LangGraph primitives that
are stable public API, and provides three components:

- `powercontext_tools()` returns `langchain_core.tools.BaseTool` instances for model-initiated Memory read and write.
- `PowerContextRecall` is a `pre_model_hook` that prepares bounded context before a model step. It supplies a
  complete, ordered model input on the `llm_input_messages` channel, so the recalled context reaches the model
  without ever entering the persisted `messages` history.
- `PowerContextScope` is a dataclass intended for the graph `context_schema`, carrying the durable scope for a run.

## Minimal usage

Use `PowerContextRecall` as the `pre_model_hook` of `create_react_agent`, which wires the `llm_input_messages`
channel for you:

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

In a custom graph, add an `llm_input_messages` channel to the state and have the model step read it, exactly as
`create_react_agent` does:

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

## Installation

This package is not yet published to PyPI, so install it from source alongside a running Server:

```bash
uv pip install "powercontext-langgraph @ git+https://github.com/oceanbase/powercontext.git#subdirectory=integrations/langgraph"
powercontext server run
```

From a checkout of this repository you can instead install the local path, e.g.
`uv pip install ./integrations/langgraph`. Publishing to PyPI is pending a standalone build and release step for the
package (its version must be advanced independently of the root `powercontext` distribution); until that lands, use
the source install above.

## Configuration

Configuration is read through pydantic-settings with the prefix `POWERCONTEXT_LANGGRAPH_`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `POWERCONTEXT_LANGGRAPH_BASE_URL` | `http://127.0.0.1:8000` | PowerContext Server URL |
| `POWERCONTEXT_LANGGRAPH_TOKEN` | unset | Bearer token forwarded to `PowerContextClient` |
| `POWERCONTEXT_LANGGRAPH_SCOPE_ID` | derived | Durable scope shared across runs |
| `POWERCONTEXT_LANGGRAPH_TIMEOUT` | `10` | Client timeout in seconds |
| `POWERCONTEXT_LANGGRAPH_MAX_BYTES` | `8000` | Prepared-context size limit |

`TOKEN` carries a bare token rather than a complete `Authorization` header value, differing from the
`POWERCONTEXT_*_AUTHORIZATION` convention used by the Codex, Claude Code, and DeepSeek Harness plugins.
`PowerContextClient` accepts the bare token and composes the header internally.

## Scope resolution

The scope for a run is resolved in this order:

1. an explicit `scope_id` on `PowerContextScope`, or `POWERCONTEXT_LANGGRAPH_SCOPE_ID`;
2. a scope derived from the current Git remote;
3. otherwise the adapter raises.

This priority is inverted relative to the Codex plugin. A LangGraph deployment is typically a long-running service in
which the working directory has no relationship to the project, so explicit configuration is the primary path and Git
derivation is the fallback. When neither is available the adapter raises rather than defaulting to a shared local
scope, which would place unrelated tenants together.

## Why this package does not implement `BaseStore`

`BaseStore` is LangGraph's cross-thread long-term memory interface, and appears to be the intended seam for an
integration of this kind. It is not usable as one. `BaseStore.batch` must service `GetOp`, `PutOp` (upsert and
delete), and `SearchOp`. Only `SearchOp` maps onto the PowerContext Memory model; the others require upsert, get, and
delete by a caller-assigned key, which Memory does not provide — entry identity and versioning are assigned by the
Server. Implementing only search and raising for the rest produces an object that passes assembly-time validation but
fails at runtime inside unrelated nodes or tools, which is worse than providing no store. The adapter therefore
integrates at the node and tool level and does not occupy the `store` parameter of `compile()`.

## Scope

In scope: Memory read and write, and bounded context preparation.

Out of scope for this release: trajectory capture, checkpointing, Handoff, Artifact Candidate review, and Experience
or Skill generation.
