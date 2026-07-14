# powermem-langchain

This package provides PowerMem long-term memory middleware for LangChain v1
agents. It retrieves relevant memories before an agent run, injects them into
model-visible context, and can save the final user/assistant interaction after
the run.

The goal is to integrate PowerMem as a long-term memory layer for LangChain v1
agents. The provided scaffold is intentionally small; you may adjust it as your
implementation requires, but the memory retrieval, context injection, and
write-back behavior should be implemented through LangChain middleware hooks.
This is useful beyond a single `create_agent` example: LangChain middleware is
the extension point for controlling agent execution, and related projects such
as Deep Agents also compose capabilities through middleware.

## Usage

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

The middleware:

- Search PowerMem with the latest user message before the model call.
- Add relevant memories to the model-visible context.
- Save the user and assistant interaction to PowerMem when enabled.
- Skip write-back when `save_interactions=False`.
- Use the explicit `user_id` constructor argument.
- Keep the agent usable when PowerMem search fails.
- Support both `invoke()` and `ainvoke()` agent execution.

`user_id` is required and must be supplied explicitly. This keeps application
identity separate from LangChain runtime or checkpoint state. Retrieved memory
is added to the model request's system context without modifying the persisted
conversation messages. Interaction write-back uses `infer=False`, preserving
the final user and assistant messages as explicit PowerMem records.

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

The output should show seeded memories before the agent call, an answer informed
by those memories, and updated memories after the call.
