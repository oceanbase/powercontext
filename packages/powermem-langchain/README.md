# PowerMem LangChain Integration

Long-term memory middleware for LangChain agents, backed by [PowerMem](https://github.com/oceanbase/powermem).

## Quick Start

```python
from powermem import create_memory
from powermem_langchain import PowerMemMiddleware
from langchain.agents import create_react_agent, AgentExecutor
from langchain_openai import ChatOpenAI

# 1. Create a PowerMem instance
memory = create_memory()

# 2. Seed some memories
memory.add("User prefers Python over Java", user_id="alice")
memory.add("User works as a software engineer", user_id="alice")

# 3. Build a standard LangChain agent
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
agent = create_react_agent(llm, tools, prompt)
executor = AgentExecutor(agent=agent, tools=tools)

# 4. Wrap with PowerMem middleware
middleware = PowerMemMiddleware(memory=memory, user_id="alice")
wrapped = middleware(executor)

# 5. Invoke — memories retrieved and injected automatically
result = wrapped.invoke({"input": "What language do I prefer?"})
# → "Based on your past conversations, you prefer Python!"

# 6. Interactions are saved back to PowerMem
memory.search("", user_id="alice")
```

## How It Works

`PowerMemMiddleware` wraps any LangChain runnable (agent, chain, or LLM) and
hooks into three lifecycle points:

| Phase | What happens |
|-------|-------------|
| **Before invoke** | The user's latest message is extracted and used to search PowerMem for relevant memories. |
| **Context injection** | Retrieved memories are prepended as a `SystemMessage`, giving the model relevant context from past conversations. |
| **After invoke** | The user message and assistant response are saved back to PowerMem as a new memory (unless `save_interactions=False`). |

Failures in PowerMem operations are silently logged — the agent continues
running without memory augmentation.

## Constructor

```python
PowerMemMiddleware(
    memory,              # powermem.Memory or AsyncMemory instance
    user_id,             # str — user identity for memory scoping
    save_interactions=True,  # bool — persist conversations
    memory_limit=5,      # int — max memories retrieved per query
)
```

## Async Support

The wrapped agent fully supports `ainvoke`:

```python
result = await wrapped.ainvoke({"messages": [...]})
```

## Testing

```bash
uv run --with-editable "packages/powermem-langchain" \
  pytest packages/powermem-langchain/tests/ -v
```

Tests use a lightweight `MockPowerMem` — no API keys or external services
required.
