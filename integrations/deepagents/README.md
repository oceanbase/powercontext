# powercontext-deepagents

Thin adapter that adds bounded PowerContext recall to a [Deep Agents](https://github.com/langchain-ai/deepagents)
agent and its declarative subagents.

`create_deep_agent` compiles every declarative subagent as a separate graph, so a middleware passed to it wraps only
the main agent. This package installs `PowerContextMiddleware` from `powercontext-langchain` on the main agent and a
recall-only copy on each declarative subagent, and keeps capture on the main agent so a delegated turn is stored once.

## Install

```bash
uv pip install ./integrations/deepagents
```

## Use

```python
from powercontext_langchain import PowerContextScope
from powercontext_deepagents import create_powercontext_deep_agent

agent = create_powercontext_deep_agent(
    model,
    tools=[],
    system_prompt="You are a deployment assistant.",
    subagents=[{"name": "researcher", "description": "Researches deployment steps.", "system_prompt": "You research."}],
    auto_capture=True,
)

result = await agent.ainvoke(
    {"messages": [{"role": "user", "content": "How should we deploy this service?"}]},
    context=PowerContextScope(),
)
```

See `docs/en/docs/integrations/deepagents.md` and `docs/zh/docs/integrations/deepagents.md` for the subagent,
scope, capture, and failure contracts.
