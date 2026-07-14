# powermem-langchain

This package is the LangChain middleware exercise for the VLDB 2026 summer
school branch. It implements `PowerMemMiddleware`, the contract tests, and a
runnable OpenAI example that exercise memory retrieval, context injection, and
write-back through LangChain middleware hooks.

The goal is to integrate PowerMem as a long-term memory layer for LangChain v1
agents. The memory retrieval, context injection, and write-back behavior are
implemented through LangChain middleware hooks. This is useful beyond a single
`create_agent` example: LangChain middleware is the extension point for
controlling agent execution, and related projects such as Deep Agents also
compose capabilities through middleware.

## Task

Implement:

```python
from powermem_langchain import PowerMemMiddleware
```

The middleware should be usable with `create_agent`:

```python
from langchain.agents import create_agent
from powermem import create_memory
from powermem_langchain import PowerMemMiddleware

memory = create_memory()

agent = create_agent(
    model="openai:gpt-4o-mini",
    tools=[],
    middleware=[
        PowerMemMiddleware(
            memory=memory,
            user_id="user123",
            search_limit=5,
            save_interactions=True,
        )
    ],
)
```

At minimum, the implementation should:

- Search PowerMem with the latest user message before the model call.
- Add relevant memories to the model-visible context.
- Save the user and assistant interaction to PowerMem when enabled.
- Skip write-back when `save_interactions=False`.
- Use the explicit `user_id` constructor argument.
- Keep the agent usable when PowerMem search fails (fail-open by default).
- Optionally re-raise memory-layer errors instead by setting `fail_open=False`.
- Work with both synchronous and asynchronous agent invocation paths.

The constructor validates its arguments up front: `user_id` must be a non-empty
string (or `None` to defer identity to PowerMem), `search_limit` a positive
integer, and unknown keyword arguments are rejected so typos such as `user_idd=`
surface immediately rather than being silently ignored. `fail_open` (default
`True`) keeps the agent running when PowerMem retrieval or write-back fails;
set it to `False` to propagate those errors instead.

## Tests

Run from the repository root:

```bash
uv run --no-project \
  --python 3.11 \
  --with-editable "." \
  --with-editable "packages/powermem-langchain[test]" \
  pytest packages/powermem-langchain/tests -q
```

The tests use a local SQLite PowerMem instance with the noop LLM provider and
mock embedder. They do not require API keys or OceanBase.

The tests are a baseline, not a complete design specification. If your solution
adds state fields or edge-case handling, add focused tests for those choices.

## Example

The OpenAI example is an end-to-end check for the expected flow:

1. Create a PowerMem instance.
2. Seed one memory for the demo user.
3. Create a LangChain agent with `PowerMemMiddleware`.
4. Invoke an OpenAI chat model.
5. Print memory search results before and after the agent run.

Minimal environment:

```bash
export OPENAI_API_KEY="..."
export LLM_PROVIDER=openai
export LLM_API_KEY="$OPENAI_API_KEY"
export LLM_MODEL=gpt-4o-mini
export DATABASE_PROVIDER=sqlite
export SQLITE_PATH="./data/powermem_langchain_demo.db"
```

Optional demo settings use the `POWERMEM_LANGCHAIN_` prefix:

- `POWERMEM_LANGCHAIN_OPENAI_MODEL`
- `POWERMEM_LANGCHAIN_TEMPERATURE`
- `POWERMEM_LANGCHAIN_USER_ID`
- `POWERMEM_LANGCHAIN_SEARCH_LIMIT`
- `POWERMEM_LANGCHAIN_PROMPT`
- `POWERMEM_LANGCHAIN_SEED_MEMORY`

Run:

```bash
uv run --no-project \
  --python 3.11 \
  --with-editable "." \
  --with-editable "packages/powermem-langchain[example]" \
  python packages/powermem-langchain/examples/openai_agent.py \
    --user-id summer-school-demo
```

After the agent runs, the output should show seeded memories before the agent
call and updated memories afterward, reflecting the middleware's retrieval and
write-back behavior.
