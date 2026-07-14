# powermem-langchain

`powermem-langchain` adds PowerMem long-term memory to LangChain v1 agents.
The middleware retrieves relevant memories once per agent invocation, exposes
them to each model call, and optionally writes the completed interaction back
to PowerMem.

## Usage

The public entry point is:

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

`user_id` must be an explicit, non-empty application user identifier. It is
used for both search and write-back; the middleware never derives identity from
LangChain runtime configuration.

The middleware lifecycle is:

- Before the agent runs, search PowerMem once using the latest non-empty user
  message and the configured `search_limit`.
- Before every model call, append the retrieved memories to the system context.
  This context is transient: it is not added to the conversation history or
  saved back to PowerMem.
- After the agent finishes, save the latest user message and its final assistant
  response when `save_interactions=True`.

Retrieved memory is untrusted reference context, not a source of system
instructions. Applications should still apply their normal prompt-injection
and data-handling controls.

Both synchronous and asynchronous agents are supported. Async hooks accept
PowerMem clients whose methods are synchronous or awaitable; synchronous calls
are moved off the event loop. Search and write-back are best-effort: failures
are logged as warnings and do not replace or discard the agent response. A
synchronous agent cannot execute an async-only PowerMem client, so that
configuration is also logged and handled without failing the agent.

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
export EMBEDDING_PROVIDER=openai
export EMBEDDING_API_KEY="$OPENAI_API_KEY"
export EMBEDDING_MODEL=text-embedding-3-small
export EMBEDDING_DIMS=1536
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

The output shows memories found before the agent call, the assistant response,
and memories found after write-back. Use `--no-save` to disable write-back or
`--skip-seed` to run without adding the demo memory first.
