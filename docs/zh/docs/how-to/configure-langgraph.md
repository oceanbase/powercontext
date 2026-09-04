---
title: 配置 LangGraph
description: 把 LangGraph 图连接到运行中的 PowerContext Server，获得持久 Memory 与有界召回。
---

# 配置 LangGraph

`powercontext-langgraph` 通过公开的 Python Client 把 [LangGraph](https://langchain-ai.github.io/langgraph/) 图连接到
运行中的 PowerContext Server。它在节点和工具层集成，只使用 LangGraph 稳定的公开 API，不会启动或内嵌 Server。

## 安装

该包尚未发布到 PyPI，请从源码安装，并配合一个运行中的 Server：

```bash
uv pip install "powercontext-langgraph @ git+https://github.com/oceanbase/powercontext.git@master#subdirectory=integrations/langgraph"
powercontext server run
```

在仓库检出目录下，也可以直接安装本地路径：`uv pip install ./integrations/langgraph`。该适配器目前没有发布到
PyPI，请使用上述任一种源码安装方式。

该包依赖 `powercontext[client]`、`langgraph`、`langchain-core` 和 `pydantic-settings`，不会拉入 Server；请把它指向
一个单独运行的 Server。

## 三个组件

- `powercontext_tools()` 返回 `langchain_core.tools.BaseTool` 实例——`powercontext_search`、`powercontext_remember`
  和 `powercontext_context`——供模型显式读写 Memory。把它们加入 `ToolNode` 或任意工具列表。
- `PowerContextRecall` 是一个 `pre_model_hook`。它读取最新的人类消息，请求一个有界的 `PreparedContext`，并在
  `llm_input_messages` 通道上给出一份完整、有序的模型输入——把准备好的内容作为唯一的前置系统消息，后接本轮的
  消息。该上下文会送达模型，但不会进入持久化的 `messages` 历史，因此在 checkpointer 下也不会跨轮累积。
- `PowerContextScope` 是用于图 `context_schema` 的 dataclass，为单次运行承载持久 scope 和可选的连接覆盖项。

将它用作 `create_react_agent` 的 `pre_model_hook`，后者会自动为你接好 `llm_input_messages` 通道：

```python
from langgraph.prebuilt import create_react_agent
from powercontext_langgraph import PowerContextRecall, PowerContextScope, powercontext_tools

agent = create_react_agent(
    model,
    tools=powercontext_tools(),
    pre_model_hook=PowerContextRecall(),
    context_schema=PowerContextScope,
    checkpointer=my_checkpointer,
)
await agent.ainvoke(state, context=PowerContextScope())
```

召回 hook 和 Memory 工具都是异步的，因此请用 `ainvoke`/`astream` 驱动图；同步的 `invoke`/`stream` 无法运行它们。

在自定义图中，为 state 增加一个 `llm_input_messages` 通道，并让模型步骤读取它：

```python
from typing import Annotated
from typing_extensions import TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph import StateGraph, START
from langgraph.graph.message import add_messages
from powercontext_langgraph import PowerContextRecall, PowerContextScope

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    llm_input_messages: list[BaseMessage]

def call_model(state: AgentState):
    model_input = state.get("llm_input_messages") or state["messages"]
    ...

builder = StateGraph(AgentState, context_schema=PowerContextScope)
builder.add_node("recall", PowerContextRecall())
builder.add_node("model", call_model)
builder.add_edge(START, "recall")
builder.add_edge("recall", "model")

graph = builder.compile(checkpointer=my_checkpointer)
await graph.ainvoke(state, context=PowerContextScope())
```

召回 hook 和工具都从 LangGraph runtime 读取当前 `PowerContextScope`，因此 `context` 上的单个值即可配置整轮运行。在
运行之外——例如直接调用某个工具时——它们回退到下面的环境配置。

## 配置连接

配置通过 pydantic-settings 以前缀 `POWERCONTEXT_LANGGRAPH_` 读取。

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `POWERCONTEXT_LANGGRAPH_BASE_URL` | `http://127.0.0.1:8000` | PowerContext Server 地址 |
| `POWERCONTEXT_LANGGRAPH_TOKEN` | 未设置 | 转发给 `PowerContextClient` 的裸 token |
| `POWERCONTEXT_LANGGRAPH_SCOPE_ID` | 未设置 | 用于替代 Server 默认 Scope 的现有 Server Scope |
| `POWERCONTEXT_LANGGRAPH_TIMEOUT` | `10` | Client 超时（秒） |
| `POWERCONTEXT_LANGGRAPH_MAX_BYTES` | `8000` | 准备上下文的大小上限 |

`PowerContextScope(base_url=..., token=..., timeout=...)` 可按单次运行覆盖这些值。scope 上留为 `None` 的字段会回退到
环境值。

`POWERCONTEXT_LANGGRAPH_TOKEN` 承载的是**裸 token**，不是完整的 `Authorization` header 值。这与 Codex、Claude Code 和 DeepSeek Harness
插件使用的 `POWERCONTEXT_*_AUTHORIZATION` 约定不同。`PowerContextClient` 接收裸 token 并在内部组装成
`Authorization: Bearer <token>`。该 token 只用于对 Client 鉴权，绝不会出现在图状态或对 Agent 可见的消息内容里。

## 解析 scope

适配器会为每个操作请求 Server 解析 Scope：

1. `PowerContextScope` 上显式且已存在的 `scope_id`，或 `POWERCONTEXT_LANGGRAPH_SCOPE_ID`；
2. 否则使用 Server 默认 Scope。

Scope ID 是由 Server 拥有的不透明标识符。适配器绝不会从进程工作目录、Git remote 或文件系统路径推导 Scope ID。操作
继续前，Server 会验证显式 ID；请通过 Scope API 获取它，不要在本地自行构造。

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

## 当前范围

已包含：Memory 读写，以及有界上下文准备。

未包含：自动轨迹采集、checkpointing、Handoff、Artifact Candidate 审核，以及 Experience 或 Skill 生成。显式写入请用
`powercontext_remember`；把一次运行自动采集为 Source 证据不属于本适配器。
