# powermem-langchain

`powermem-langchain` adds PowerMem long-term memory to LangChain v1 agents by
using agent middleware hooks. It retrieves relevant memories before an agent
run, adds them to model-visible system context, and optionally saves the final
user/assistant interaction after the run.

## Installation

From the PowerMem repository:

```bash
uv pip install --editable . --editable packages/powermem-langchain
```

Python 3.11 or newer, PowerMem 1.1.7 or newer, and LangChain v1 are required.

## Usage

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

result = agent.invoke(
    {"messages": [{"role": "user", "content": "How should you answer me?"}]}
)
```

`user_id` is required and is used explicitly for every PowerMem search and
write. The middleware never derives a business user identity from LangChain
runtime state, thread IDs, or environment variables.

## Lifecycle behavior

For each agent invocation, the middleware:

1. Finds the latest user message in agent state.
2. Calls `memory.search(query, user_id=user_id, limit=search_limit)` once.
3. Stores a formatted memory context in middleware state.
4. Adds that context to the existing system message for every model call in
   the agent loop, without modifying conversation history.
5. After the agent finishes, calls `memory.add(...)` with the latest user
   message and the final assistant message.

Retrieved text is labeled as untrusted background information rather than
instructions. The model is told to use only memories relevant to the current
request.

Set `save_interactions=False` to make the integration retrieval-only:

```python
PowerMemMiddleware(
    memory=memory,
    user_id="user123",
    save_interactions=False,
)
```

PowerMem search and write failures are fail-open by default. The middleware
logs a warning and lets the agent return its response. A failed or empty search
also clears the per-run memory context so that stale context cannot be reused.

## Async agents

The middleware implements both synchronous and asynchronous LangChain hooks:

```python
result = await agent.ainvoke(
    {"messages": [{"role": "user", "content": "Use my saved preferences."}]}
)
```

A synchronous PowerMem `Memory` instance works with both `invoke` and
`ainvoke`; synchronous PowerMem work is moved to a worker thread on the async
path. A PowerMem `AsyncMemory`-compatible object can be used with `ainvoke`.

## Tests

Run from the repository root:

```bash
uv run --no-project \
  --python 3.11 \
  --with-editable "." \
  --with-editable "packages/powermem-langchain[test]" \
  pytest packages/powermem-langchain/tests -q
```

The tests use local SQLite PowerMem storage, the noop LLM provider, and a mock
embedder. They do not require API keys or OceanBase.

If the dependencies are already installed in a repository virtual environment:

```bash
.venv/bin/python -m pytest packages/powermem-langchain/tests -q
```

## OpenAI example

The example at `examples/openai_agent.py` performs an end-to-end check by
seeding a memory, invoking a LangChain agent, and printing PowerMem search
results before and after the invocation.

Minimal environment:

```bash
export OPENAI_API_KEY="..."
export LLM_PROVIDER=openai
export LLM_API_KEY="$OPENAI_API_KEY"
export LLM_MODEL=gpt-4o-mini
export DATABASE_PROVIDER=sqlite
export SQLITE_PATH="./data/powermem_langchain_demo.db"
```

Run it from the repository root:

```bash
uv run --no-project \
  --python 3.11 \
  --with-editable "." \
  --with-editable "packages/powermem-langchain[example]" \
  python packages/powermem-langchain/examples/openai_agent.py \
    --user-id summer-school-demo
```

The OpenAI example is a manual integration check and requires a valid API key.
