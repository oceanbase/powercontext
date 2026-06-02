# LangChain and LangGraph

Use PowerMem as a persistent memory layer for LangChain and LangGraph applications.

## Prerequisites

- Python 3.11+.
- PowerMem configured with your LLM provider, API key, and model.
- LangChain or LangGraph dependencies for your app.

## Install

For LangChain:

```bash
pip install powermem langchain langchain-core langchain-openai
```

For LangGraph:

```bash
pip install powermem langgraph langchain-core langchain-openai
```

For the runnable healthcare support example:

```bash
cd examples/langchain
pip install -r requirements.txt
```

## LangChain pattern

PowerMem is used beside LangChain as a durable retrieval and write-back layer:

1. Create a PowerMem `Memory` instance.
2. Search PowerMem before each response to load relevant context.
3. Inject the retrieved context into a LangChain prompt or LCEL chain.
4. Save the user/assistant exchange back into PowerMem.

Minimal shape:

```python
from powermem import Memory, auto_config

memory = Memory(config=auto_config())

context = memory.search(
    query="what does the user prefer",
    user_id="user123",
    limit=5,
)

memory.add(
    "User prefers concise answers with examples.",
    user_id="user123",
)
```

For a full LCEL implementation, see [`examples/langchain/README.md`](https://github.com/oceanbase/powermem/blob/main/examples/langchain/README.md) and [`../examples/scenario_5_custom_integration.md`](../examples/scenario_5_custom_integration.md).

## LangGraph pattern

PowerMem works well as a graph node or helper in LangGraph workflows:

1. Add a load-context node that searches PowerMem.
2. Add the retrieved memories to graph state.
3. Generate the response with state-aware context.
4. Add a save-memory node after the response.

See the LangGraph section in [`../guides/0009-integrations.md`](../guides/0009-integrations.md) for a complete example.

## Verify

1. Add a probe memory for a test `user_id`.
2. Search for the probe before invoking the chain or graph.
3. Confirm the retrieved memory is included in the generated prompt/context.
4. Save a new exchange and search it back.

## Troubleshooting

- If search returns no results, confirm the same `user_id` is used for writes and searches.
- If extraction creates no memory, check LLM provider configuration and logs.
- If LangChain imports fail, install the packages listed in `examples/langchain/requirements.txt`.
- If embeddings fail, confirm your embedding provider and dimensions match the configured storage backend.

## See also

- [`examples/langchain/README.md`](https://github.com/oceanbase/powermem/blob/main/examples/langchain/README.md)
- [`../guides/0009-integrations.md`](../guides/0009-integrations.md)
- [`../examples/scenario_5_custom_integration.md`](../examples/scenario_5_custom_integration.md)
