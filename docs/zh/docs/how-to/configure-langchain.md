---
title: 配置 LangChain middleware
description: 为 LangChain agent 增加有界 PowerContext 召回和完成轮次 Source 采集。
---

# 配置 LangChain middleware

`PowerContextMiddleware` 把 LangChain `create_agent` agent 连接到单独运行的 PowerContext Server。每次模型调用前，
它会根据最新用户消息请求一份有界 `PreparedContext`；显式开启自动采集后，agent 成功结束时会把最新用户消息和最终回答
采集为一个 Content Source。

该实现只使用 LangChain 公开的 `AgentMiddleware` API。召回内容只修改当前 `ModelRequest`，不会进入 agent state 或
checkpointer。

## 安装

middleware 由独立的 `powercontext-langchain` 包分发，要求 LangChain 1.3 或更高版本：

```bash
uv tool install "powercontext[cli,server]==0.0.2"
powercontext server run
```

保持 Server 运行，然后在 LangChain 应用自己的环境中安装 middleware：

```bash
uv pip install "powercontext-langchain @ git+https://github.com/oceanbase/powercontext.git#subdirectory=integrations/langchain"
```

应用已经连接到单独管理的 Server 时，可以跳过 Server 安装。在仓库 checkout 中可使用
`uv pip install ./integrations/langchain` 安装 middleware。

该包自己持有 Scope、Settings、Client 连接逻辑和 Middleware 实现，不导入、也不依赖独立的
`powercontext-langgraph` 适配器。LangChain 自身内部使用 LangGraph，因此安装 LangChain 时仍可能传递安装 LangGraph。

## 接入 middleware

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
    {"messages": [{"role": "user", "content": "这个服务应该如何部署？"}]},
    context=PowerContextScope(scope_id="git:github.com/acme/api"),
)
```

middleware 本身支持同步 `invoke` 和 `stream`；异步应用应直接使用 agent 的异步方法，不要在 event loop 中调用同步
方法。

## 召回生命周期

每个模型步骤中，middleware 都会：

1. 读取最新的人类消息，不修改 state；
2. 使用解析后的 scope 和字节上限调用 `/v1/context/prepare`；
3. 向当前 system message 追加一个独立文本块；
4. 把整个文本块明确标为不可信历史证据。

因此，tool loop 的后续模型步骤可以拿到新鲜上下文，包括同一轮中通过工具显式写入的 Memory。这个 override 只属于当前
模型请求，不会在 checkpointed message history 中累积。

## 完成轮次采集

用户和模型内容可能包含凭据或其他敏感数据，因此 `PowerContextMiddleware()` 默认关闭 `auto_capture`。只有应用的
transcript 策略允许持久化这些内容时，才应显式开启：

```python
middleware = PowerContextMiddleware(auto_capture=True)
```

开启后，agent 成功结束时会通过 `/v1/sources/content` 采集最新用户消息和最终的非空回答。成功的 structured result 会从
LangChain 的 `structured_response` 序列化。采集不包含召回 system block、工具输出或中间的 tool-calling 模型消息。

采集结果是 Source 证据，不是推断完成的 Memory entry。配置的 scheduler 或显式 `flush_memory` 操作随后执行标准的
Source-to-Memory 抽取。这样既保留 lineage，也不会把未经处理的模型输出直接当成已经审核的持久事实。采集内容有长度
上限，但不保证一定不含 secret；应用必须先执行自己的输入和输出策略，再选择开启。

模型或工具中止运行时，LangChain 不会执行 `after_agent`；因此，没有最终回答的失败运行不会被采集。

## 配置连接和 scope

middleware 自己持有 `PowerContextScope`，并使用独立的 `POWERCONTEXT_LANGCHAIN_*` 配置；它不会复用 LangGraph
适配器的 scope 或环境变量前缀：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `POWERCONTEXT_LANGCHAIN_BASE_URL` | `http://127.0.0.1:8000` | PowerContext Server 地址 |
| `POWERCONTEXT_LANGCHAIN_TOKEN` | 未设置 | 传给 Client 的裸 bearer token |
| `POWERCONTEXT_LANGCHAIN_SCOPE_ID` | 推导 | 跨运行共享的持久 scope |
| `POWERCONTEXT_LANGCHAIN_TIMEOUT` | `10` | Client 超时（秒） |
| `POWERCONTEXT_LANGCHAIN_MAX_BYTES` | `8000` | PreparedContext 字节上限 |

显式传入的 `PowerContextScope` 优先于环境配置。没有显式 scope 时，PowerContext 尝试从当前 Git remote 推导；两者都不
存在时，召回和采集会失败开放，不中断 agent。Token 只保留在 Client 配置中，不会进入 agent state 或消息内容。

## 故障行为

召回和采集都是 best-effort。Server 不可用、响应无效或请求校验失败时，不会替换模型回答或中止 agent。HTTP 401 和 403
只会以 error 级别记录一次，且日志不包含内容或 token；瞬时和意外故障记录在 debug 级别。
