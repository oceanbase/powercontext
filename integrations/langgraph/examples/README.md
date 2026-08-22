# Examples

Runnable examples for `powercontext-langgraph`. Each one starts a throwaway local PowerContext Server on an
ephemeral port so it runs with no external setup; `_local_server.py` holds that helper. A production graph
would instead point at a separately managed Server through `POWERCONTEXT_LANGGRAPH_BASE_URL` and run
`powercontext server run`.

## `inspect_recall.py`

Prints the exact system message `PowerContextRecall` injects, including the untrusted-history framing and the
citations the Server attaches. No model or API key required.

```bash
uv run python integrations/langgraph/examples/inspect_recall.py
```

## `agent_memory_roundtrip.py`

Drives a real `create_react_agent` through the write-then-recall loop: turn 1 persists a decision through the
`powercontext_remember` tool, turn 2 recalls it before the model step and answers from it. The connection uses
bearer auth, and the example checks the token never reaches the agent-visible messages.

The model is any OpenAI-compatible endpoint; the defaults target DeepSeek. It needs `langchain-openai` and an
API key read from the environment — no secret is stored in the file.

```bash
DEEPSEEK_API_KEY=sk-... uv run --with langchain-openai python \
    integrations/langgraph/examples/agent_memory_roundtrip.py
```

Override `DEEPSEEK_BASE_URL` and `DEEPSEEK_MODEL` to target a different OpenAI-compatible endpoint.
