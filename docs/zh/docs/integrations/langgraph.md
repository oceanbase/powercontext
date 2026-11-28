---
title: LangGraph
description: 把 LangGraph 图连接到运行中的 PowerContext Server，获得持久 Memory 与有界召回。
status: community
---

# LangGraph

`community`

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
| `POWERCONTEXT_LANGGRAPH_ALLOW_INSECURE_HTTP` | `false` | 显式允许非环回明文 HTTP |
| `POWERCONTEXT_LANGGRAPH_TOKEN` | 未设置 | 转发给 `PowerContextClient` 的裸 token |
| `POWERCONTEXT_LANGGRAPH_SCOPE_ID` | 未设置 | 用于替代 Server 默认 Scope 的现有 Server Scope |
| `POWERCONTEXT_LANGGRAPH_TIMEOUT` | `10` | Client 超时（秒） |
| `POWERCONTEXT_LANGGRAPH_MAX_BYTES` | `8000` | 准备上下文的大小上限 |

`PowerContextScope(base_url=..., token=..., timeout=...)` 可按单次运行覆盖这些值。scope 上留为 `None` 的字段会回退到
环境值。

环回 HTTP 默认可用；非环回 HTTP 需要通过上表环境变量，或 `PowerContextLangGraphSettings`、`PowerContextScope`
上的 `allow_insecure_http=True` 显式同意。HTTPS 证书校验仍然启用。通用环境变量和绑定地址的持久化同意见
[连接远程 Server](../operate/connect-remote-server.md)。框架适配器没有对应的 setup 安装命令。

`POWERCONTEXT_LANGGRAPH_TOKEN` 承载的是**裸 token**，不是完整的 `Authorization` header 值。这与 Codex、Claude Code 和 DeepSeek Harness
插件使用的 `POWERCONTEXT_*_AUTHORIZATION` 约定不同。`PowerContextClient` 接收裸 token 并在内部组装成
`Authorization: Bearer <token>`。该 token 只用于对 Client 鉴权，绝不会出现在图状态或对 Agent 可见的消息内容里。

## 解析 scope

适配器会为每个操作请求 Server 解析 Scope：

1. `PowerContextScope` 上显式且已存在的 `scope_id`，或 `POWERCONTEXT_LANGGRAPH_SCOPE_ID`；
2. 否则使用 Server 默认 Scope。

Scope ID 是由 Server 拥有的不透明标识符。适配器绝不会从进程工作目录、Git remote 或文件系统路径推导 Scope ID。操作
继续前，Server 会验证显式 ID；请通过 Scope API 获取它，不要在本地自行构造。

## 召回 query

`PowerContextRecall` 发送去掉首尾空白的最新人类消息，识别到的凭据按[自动 prompt 隐私策略](index.md#自动-prompt-隐私策略)
替换为 `[REDACTED]`。超过 8192 个字符或 8192 个 UTF-8 字节的消息改用最近的文本召回，先取末尾 8192 个字符，
再截到末尾 8192 字节。

## 把召回内容当作不可信历史

`PowerContextRecall` 把准备好的内容作为系统消息注入，消息以 `PowerContext host-supplied context` 开头，
后接 Server 准备的内容，内容顶部的信任策略把其后的全部内容标为不可信历史证据。Memory 内容源自过往模型输出和用户输入；
把它当作权威系统指令，会把提示词注入面扩大到历史数据。模型在据此行动前，仍必须核对当前代码、当前用户请求和系统指令。

Server 返回空结果时，该节点不新增任何内容，原样返回状态。

## Server 不可用时失败开放

Server 不可用不得中断图执行。`PowerContextRecall` 在内部处理 Client 错误并原样返回状态，图仍能到达终点。配置类故障
——HTTP 401 或 403，通常是缺失或错误的 `POWERCONTEXT_LANGGRAPH_TOKEN`——会以 error 级别记录一次；其他瞬时故障以
debug 级别记录。Memory 工具返回简短的 `(PowerContext unavailable: ...)` 字符串而非抛错，这样调用了工具的模型可以
重试或改用其他策略，而不会误以为没有任何 Memory。

## 把提示词时上下文交给 gateway

graph 的模型请求经过 PowerContext gateway 时，gateway 为每次模型调用准备并注入上下文；设置 `PC_GATEWAY_SEDIMENT=on` 后，
gateway 也会采集这一轮。Core 检查 gateway 转发的每个请求，拒绝不是 gateway 注入的 prepared-context 块，
在每种隐私模式下都是如此：Core 返回 HTTP 422，gateway 返回 HTTP 503 `gate_paused`。本包有两处会产生这样的块：

- `PowerContextRecall` 向每次模型输入添加一个。不要使用这个 hook。
- `powercontext_context` 的工具结果带有一个。它留在 `messages` 历史中，使 Core 拒绝该 thread 之后的每个请求。
  只提供其他工具。

```python
import os

from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent
from powercontext_langgraph import PowerContextScope, powercontext_remember, powercontext_search


def gateway_agent(thread_id: str):
    model = ChatOpenAI(
        model="approved-model",
        base_url="http://127.0.0.1:8787/gateway/v1",
        api_key=os.environ["PC_GATEWAY_KEY"],
        use_responses_api=True,
        default_headers={"X-PowerContext-Conversation-ID": thread_id},
    )
    return create_react_agent(
        model,
        tools=[powercontext_search, powercontext_remember],
        context_schema=PowerContextScope,
        checkpointer=my_checkpointer,
    )
```

hook 从不把召回内容写入 checkpoint 历史，因此已有的 thread 可以继续经过 gateway。如果模型在某个 thread 中调用过
`powercontext_context`，该 thread 会保留这个结果，请改用新的 thread。在自定义 graph 中，请移除召回节点，并让模型步骤读取
`messages`：`llm_input_messages` 是 graph state，checkpointer 会在其中保留最近一次召回的上下文。

`PC_GATEWAY_KEY` 保存 gateway 的 `PC_GATEWAY_API_KEY` 值。这个请求头标识会话：每个会话使用一个稳定的 ID，例如 thread
ID，长度为 1～256 个可见 ASCII 字符；不要让所有会话共用一个 ID。组合部署（`PC_GATEWAY_CORE_UDS`）
在一个会话中每条用户消息准备一次，并把结果重放给这条消息的工具循环步骤。没有这个请求头时，它根据请求的历史开头推导会话：
除非设置 `use_previous_response_id=True`，`ChatOpenAI` 每次调用都发送 thread 的完整历史；
以 system 消息和一条用户消息开头的历史即可推导。请求头仍能区分开头相同的会话，
并为采集和前缀提取提供在 gateway 重启后保持不变的会话标识；推导出的 ID 仅在当前 gateway 进程内用于重放准备结果。
独立运行的 gateway（`PC_GATEWAY_CORE_BASE`）只有设置 `PC_GATEWAY_INJECTION=on` 才会注入。
见[网关上下文注入](../operate/deploy-server.md#网关上下文注入)。

请使用 Responses 路由（`use_responses_api=True`）。在这个路由上，gateway 以非流式方式调用模型，采集通过检查的响应，
并以流式重放给发送流式请求的客户端。在 Chat Completions 路由上，enforcing 模式的 Core 会暂缓流式响应，gateway
返回 HTTP 400。非 enforcing 模式下，gateway 原样放行流式响应；设置 `PC_GATEWAY_SEDIMENT=on` 后，还会从中采集完成的一轮。

配置 Core 和 gateway 时还需要：

- 除非每个请求都满足 enforcing 规则，否则请让 Core 运行在非 enforcing 隐私模式（`disabled`、`shadow` 或 `advisory`）：
  enforcing 模式的 Core 会暂缓没有 `store: false`、或没有不超过目标 token 预算的输出上限的 Responses 请求，gateway 返回
  HTTP 400。gateway 在启动时发现 Core 的模式，修改模式后请重启 gateway。
- 此处使用 Responses 路由，应将 `PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES` 设为 Core 中 endpoint、`openai-responses`
  协议和模型均匹配的 target profile。未设置时该路由返回 HTTP 501 `responses_route_disabled`。
  将 `PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES` 设为该目标的服务商 Key，并设置 `PC_GATEWAY_SEDIMENT=on`。
  不要设置 `PC_GATEWAY_UPSTREAM_URL*`；这些旧变量会导致启动失败。
- 即使监听回环地址也必须设置非空 `PC_GATEWAY_API_KEY`，并将 `PC_GATEWAY_CORE_TOKEN` 设为已认证的服务身份。
  Core 访问控制必须为 `enforced`；在所选 Scope 上授予 `gateway.user`，并在 `POWERCONTEXT_SERVER_ACCESS_DEPLOYMENT_ID`
  对应的 Server resource 上授予 `server.observer`（默认 deployment ID 为 `powercontext`）。本指南开启了采集，
  因此还要授予 `scope.contributor`（包含 `scope.read`）来替代 `scope.viewer`。
  详见[Gateway 认证与目标要求](../operate/deploy-server.md#网关模型目标)。
- gateway 在 `PC_GATEWAY_DEFAULT_SCOPE` 指定的同一个 Scope 中准备和采集所有会话。请把它设为工具解析的 Scope，
  使工具搜索和写入的正是 gateway 召回的 Memory。

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
