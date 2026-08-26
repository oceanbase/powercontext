# PowerContext for LangChain

`powercontext-langchain` connects a LangChain `create_agent` application to a separately running PowerContext Server.
It provides `PowerContextMiddleware`, which recalls bounded context before each model call and can capture the completed
user/assistant turn as Source evidence after a successful agent run.

## Install

```bash
uv tool install "powercontext[cli,server]==0.0.2"
powercontext server run
```

Keep the Server running, then install the middleware in the LangChain application's environment:

```bash
uv pip install "powercontext-langchain @ git+https://github.com/oceanbase/powercontext.git#subdirectory=integrations/langchain"
```

Skip the Server installation when the application already connects to a separately managed Server. From a checkout,
install the middleware with `uv pip install ./integrations/langchain`.

This package owns its Scope, Settings, Client wiring, and Middleware implementation. It neither imports nor depends on
the separate `powercontext-langgraph` adapter. LangChain itself uses LangGraph internally, so installing LangChain may
still install LangGraph as a transitive dependency.

## Use

```python
from langchain.agents import create_agent
from powercontext_langchain import PowerContextMiddleware, PowerContextScope

agent = create_agent(
    model,
    tools=application_tools,
    middleware=[PowerContextMiddleware()],
    context_schema=PowerContextScope,
)

result = await agent.ainvoke(
    {"messages": [{"role": "user", "content": "How should we deploy this service?"}]},
    context=PowerContextScope(scope_id="git:github.com/acme/api"),
)
```

The middleware changes only the current model request, so recalled context never enters agent state or a checkpointer.
Automatic capture is disabled by default. Enable it only when the application's transcript policy permits durable
storage of user and model content: `PowerContextMiddleware(auto_capture=True)`.

Configuration uses `POWERCONTEXT_LANGCHAIN_BASE_URL`, `POWERCONTEXT_LANGCHAIN_TOKEN`,
`POWERCONTEXT_LANGCHAIN_SCOPE_ID`, `POWERCONTEXT_LANGCHAIN_TIMEOUT`, and `POWERCONTEXT_LANGCHAIN_MAX_BYTES`.
