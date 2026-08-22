---
title: 配置 LangGraph
description: 把 LangGraph 图连接到运行中的 PowerContext Server，获得持久 Memory 与有界召回。
---

# 配置 LangGraph

`powercontext-langgraph` 通过公开的 Python Client 把 [LangGraph](https://langchain-ai.github.io/langgraph/) 图连接到
运行中的 PowerContext Server。它在节点和工具层集成，只使用 LangGraph 稳定的公开 API，不会启动或内嵌 Server。

## 安装

```bash
uv pip install powercontext-langgraph
powercontext server run
```

该包依赖 `powercontext[client]`、`langgraph`、`langchain-core` 和 `pydantic-settings`，不会拉入 Server；请把它指向
一个单独运行的 Server。

## 三个组件

- `powercontext_tools()` 返回 `langchain_core.tools.BaseTool` 实例——`powercontext_search`、`powercontext_remember`
  和 `powercontext_context`——供模型显式读写 Memory。把它们加入 `ToolNode` 或任意工具列表。
- `PowerContextRecall` 是可用作图节点或 `pre_model_hook` 的可调用对象。它读取最新的人类消息，请求一个有界的
  `PreparedContext`，并在模型步骤前把结果作为系统消息前置。
- `PowerContextScope` 是用于图 `context_schema` 的 dataclass，为单次运行承载持久 scope 和可选的连接覆盖项。

```python
from langgraph.graph import StateGraph, START
from langgraph.prebuilt import ToolNode
from powercontext_langgraph import PowerContextRecall, PowerContextScope, powercontext_tools

builder = StateGraph(AgentState, context_schema=PowerContextScope)
builder.add_node("recall", PowerContextRecall())
builder.add_node("model", call_model)
builder.add_node("tools", ToolNode([*my_tools, *powercontext_tools()]))
builder.add_edge(START, "recall")
builder.add_edge("recall", "model")

graph = builder.compile(checkpointer=my_checkpointer)
graph.invoke(state, context=PowerContextScope(scope_id="git:github.com/acme/api"))
```

召回节点和工具都从 LangGraph runtime 读取当前 `PowerContextScope`，因此 `context` 上的单个值即可配置整轮运行。在
运行之外——例如直接调用某个工具时——它们回退到下面的环境配置。

## 配置连接

配置通过 pydantic-settings 以前缀 `POWERCONTEXT_LANGGRAPH_` 读取。

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `POWERCONTEXT_LANGGRAPH_BASE_URL` | `http://127.0.0.1:8000` | PowerContext Server 地址 |
| `POWERCONTEXT_LANGGRAPH_TOKEN` | 未设置 | 转发给 `PowerContextClient` 的裸 token |
| `POWERCONTEXT_LANGGRAPH_SCOPE_ID` | 推导 | 跨运行共享的持久 scope |
| `POWERCONTEXT_LANGGRAPH_TIMEOUT` | `10` | Client 超时（秒） |
| `POWERCONTEXT_LANGGRAPH_MAX_BYTES` | `8000` | 准备上下文的大小上限 |

`PowerContextScope(base_url=..., token=..., timeout=...)` 可按单次运行覆盖这些值。scope 上留为 `None` 的字段会回退到
环境值。

`TOKEN` 承载的是**裸 token**，不是完整的 `Authorization` header 值。这与 Codex、Claude Code 和 DeepSeek Harness
插件使用的 `POWERCONTEXT_*_AUTHORIZATION` 约定不同。`PowerContextClient` 接收裸 token 并在内部组装成
`Authorization: Bearer <token>`。该 token 只用于对 Client 鉴权，绝不会出现在图状态或对 Agent 可见的消息内容里。

## 解析 scope

单次运行的 scope 按以下顺序解析：

1. `PowerContextScope` 上显式的 `scope_id`，或 `POWERCONTEXT_LANGGRAPH_SCOPE_ID`；
2. 由当前 Git remote 推导的 scope；
3. 否则适配器抛出 `MissingScopeError`。

该优先级与 Codex 插件**相反**——Codex 优先使用由 Git 或路径推导的本地 scope。LangGraph 部署通常是长期运行的服务，
其工作目录与项目无关，因此显式配置是主路径，Git 推导只是回退。两者都不可用时，适配器直接报错，而不是退回到共享的
本地 scope——那样会把不相关的租户放到一起。

## 把召回内容当作不可信历史

`PowerContextRecall` 把准备好的内容作为标记为不可信历史证据的系统消息注入。Memory 内容源自过往模型输出和用户输入；
把它当作权威系统指令，会把提示词注入面扩大到历史数据。模型在据此行动前，仍必须核对当前代码、当前用户请求和系统指令。

Server 返回空结果时，该节点不新增任何内容，原样返回状态。

## Server 不可用时失败开放

Server 不可用不得中断图执行。`PowerContextRecall` 在内部处理 Client 错误并原样返回状态，图仍能到达终点。配置类故障
——HTTP 401 或 403，通常是缺失或错误的 `POWERCONTEXT_LANGGRAPH_TOKEN`——会以 error 级别记录一次；其他瞬时故障以
debug 级别记录。Memory 工具返回简短的 `(PowerContext unavailable: ...)` 字符串而非抛错，这样调用了工具的模型可以
重试或改用其他策略，而不会误以为没有任何 Memory。

## 为什么本包不实现 `BaseStore`

`BaseStore` 是 LangGraph 的跨线程长期记忆接口，看起来像是这类集成的理想接入点，但它并不适用。`BaseStore.batch`
必须服务 `GetOp`、`PutOp`（upsert 和删除）和 `SearchOp`。只有 `SearchOp` 能映射到 PowerContext 的 Memory 模型；
其余操作要求按调用方指定的 key 进行读取、upsert 和删除，而 Memory 不提供这些——条目身份和版本由 Server 分配。只实现
search、其余抛错，会产生一个能通过装配期校验、却在无关节点或工具内运行时失败的对象，比不提供 store 更糟。因此适配器
在节点和工具层集成，不占用 `compile()` 的 `store` 参数。

## 本次发布范围

范围内：Memory 读写，以及有界上下文准备。

本次发布范围外：自动轨迹采集、checkpointing、Handoff、Artifact Candidate 审核，以及 Experience 或 Skill 生成。显式写入
请用 `powercontext_remember`；把一次运行自动采集为 Source 证据不属于本适配器。
