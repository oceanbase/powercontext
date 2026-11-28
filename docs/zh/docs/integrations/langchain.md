---
title: LangChain
description: 为 LangChain agent 增加有界 PowerContext 召回和完成轮次 Source 采集。
status: community
---

# LangChain

`community`

`PowerContextMiddleware` 把 LangChain `create_agent` agent 连接到单独运行的 PowerContext Server。每次模型调用前，
它会根据最新用户消息请求一份有界 `PreparedContext`；显式开启自动采集后，agent 成功结束时会把最新用户消息和最终回答
采集为一个 Content Source。

该实现只使用 LangChain 公开的 `AgentMiddleware` API。召回内容只修改当前 `ModelRequest`，不会进入 agent state 或
checkpointer。

## 安装

middleware 源码独立打包为 `powercontext-langchain`，要求 LangChain 1.3 或更高版本。目前该包没有发布到 PyPI：

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext server run
```

保持 Server 运行，然后在 LangChain 应用自己的环境中安装 middleware：

```bash
uv pip install "powercontext-langchain @ git+https://github.com/oceanbase/powercontext.git@master#subdirectory=integrations/langchain"
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
    context=PowerContextScope(),
)
```

middleware 本身支持同步 `invoke` 和 `stream`；异步应用应直接使用 agent 的异步方法，不要在 event loop 中调用同步
方法。

## 召回生命周期

每个模型步骤中，middleware 都会：

1. 读取最新的人类消息，不修改 state；
2. 使用解析后的 scope 和字节上限调用 `/v1/context/prepare`。query 是去掉首尾空白的该消息，识别到的凭据按
   [自动 prompt 隐私策略](index.md#自动-prompt-隐私策略)替换为 `[REDACTED]`；超过 8192 个字符或 8192 个 UTF-8
   字节的消息改用最近的文本召回，先取末尾 8192 个字符，再截到末尾 8192 字节；
3. 向当前 system message 追加一个独立文本块；
4. 文本块以 `PowerContext host-supplied context` 开头，后接 Server 准备的内容；
   内容顶部的信任策略把其后的全部内容标为不可信的历史数据。

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
Source-to-Memory 抽取。这样既保留 lineage，也不会把未经处理的模型输出直接当成已经审核的持久事实。用户消息或助手回答含可识别
凭据的轮次整轮跳过。识别是尽力而为，因此采集内容不保证一定不含 secret；应用必须先执行自己的输入和输出策略，
再选择开启。

模型或工具中止运行时，LangChain 不会执行 `after_agent`；因此，没有最终回答的失败运行不会被采集。

## 配置连接和 scope

middleware 自己持有 `PowerContextScope`，并使用独立的 `POWERCONTEXT_LANGCHAIN_*` 配置；它不会复用 LangGraph
适配器的 scope 或环境变量前缀：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `POWERCONTEXT_LANGCHAIN_BASE_URL` | `http://127.0.0.1:8000` | PowerContext Server 地址 |
| `POWERCONTEXT_LANGCHAIN_ALLOW_INSECURE_HTTP` | `false` | 显式允许非环回明文 HTTP |
| `POWERCONTEXT_LANGCHAIN_TOKEN` | 未设置 | 传给 Client 的裸 bearer token |
| `POWERCONTEXT_LANGCHAIN_SCOPE_ID` | 未设置 | 用于替代 Server 默认 Scope 的现有 Server Scope |
| `POWERCONTEXT_LANGCHAIN_TIMEOUT` | `10` | Client 超时（秒） |
| `POWERCONTEXT_LANGCHAIN_MAX_BYTES` | `8000` | PreparedContext 字节上限 |

环回 HTTP 默认可用；非环回 HTTP 需要通过上表环境变量，或 `PowerContextLangChainSettings`、`PowerContextScope`
上的 `allow_insecure_http=True` 显式同意。HTTPS 证书校验仍然启用。通用环境变量和绑定地址的持久化同意见
[连接远程 Server](../operate/connect-remote-server.md)。框架适配器没有对应的 setup 安装命令。

显式传入的 `PowerContextScope` 优先于环境配置，并且必须指向 Server 中已有的 Scope。两者都没有设置时，使用 Server
默认 Scope。适配器不会根据 Git、路径、Agent 或 prompt 推导 Scope ID。Token 只保留在 Client 配置中，不会进入
agent state 或消息内容。

## 把提示词时上下文交给 gateway

应用的模型请求经过 PowerContext gateway 时，gateway 为每次模型调用准备并注入上下文；设置 `PC_GATEWAY_SEDIMENT=on` 后，
gateway 也会采集这一轮。Core 检查 gateway 转发的每个请求，拒绝不是 gateway 注入的 prepared-context 块，
在每种隐私模式下都是如此：Core 返回 HTTP 422，gateway 返回 HTTP 503 `gate_paused`。middleware
会向每次模型调用添加这样的块，因此模型经过 gateway 的 agent 不要加入 `PowerContextMiddleware`，改由 gateway 采集各轮。
middleware 从不把召回内容保存到 agent state，因此已有的 thread 可以继续经过 gateway。

模型客户端由应用持有。让它指向 gateway 的 Responses 路由，并在 `X-PowerContext-Conversation-ID` 请求头中标识会话：

```python
import os

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI


def gateway_agent(thread_id: str):
    model = ChatOpenAI(
        model="approved-model",
        base_url="http://127.0.0.1:8787/gateway/v1",
        api_key=os.environ["PC_GATEWAY_KEY"],
        use_responses_api=True,
        default_headers={"X-PowerContext-Conversation-ID": thread_id},
    )
    return create_agent(model, tools=application_tools)
```

`PC_GATEWAY_KEY` 保存 gateway 的 `PC_GATEWAY_API_KEY` 值。这个请求头标识会话：每个会话使用一个稳定的 ID，例如
checkpointer 的 thread ID，长度为 1～256 个可见 ASCII 字符；不要让所有会话共用一个 ID。组合部署（`PC_GATEWAY_CORE_UDS`）
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
- gateway 在 `PC_GATEWAY_DEFAULT_SCOPE` 指定的同一个 Scope 中准备和采集所有会话。请把它设为 middleware 使用过的 Scope，
  使 gateway 能召回之前采集的 Memory。

## 故障行为

召回和采集都是 best-effort。Server 不可用、响应无效或请求校验失败时，不会替换模型回答或中止 agent。HTTP 401 和 403
只会以 error 级别记录一次，且日志不包含内容或 token；瞬时和意外故障记录在 debug 级别。


### 由适配层负责的 GenAI 追踪

LangChain 中间件自动为其 Scope 解析、上下文召回和可选的完整轮次采集调用添加 GenAI 上下文服务语义，复用应用已有的 OpenTelemetry provider 和父上下文。它不初始化 exporter、不配置日志，也不导入 Langfuse。业务代码保持普通的 middleware 配置，无需编写 span 白名单或使用 Langfuse 私有过滤 API。

在适配层的调用范围内，SDK span 携带 `gen_ai.provider.name=powercontext`。召回采用 `gen_ai.operation.name=retrieval` 和逻辑来源 `gen_ai.data_source.id=powercontext`；辅助调用采用 `powercontext.resolve_scope_binding`、`powercontext.capture_content_source` 等有界的自定义操作名，不伪装为模型推理或模型选择的工具调用。这些属性不记录查询、Scope 标识、正文、结果、Token 数或凭据。范围之外的独立 SDK 调用保持原有运维语义。

Provider/exporter 的初始化及服务端遥测仍由应用和部署方负责。语义可见性不会自动配置上报地址，也不保证任意后端都会导出。成功、错误、取消和召回失败时放行模型的行为保持不变。

### 异步回调的父上下文

中间件沿用当前 OpenTelemetry 上下文。导出的父链闭合，并不能说明该上下文就是当前执行的 LangChain 节点。
在 LangChain 1.4.4、`langchain-core` 1.7.6 与 Langfuse 5.0.2 的组合中，默认同步 `CallbackHandler` 会在执行器上下文中处理异步 Agent
的回调。LangGraph 节点可以正常导出，但中间件仍看到外层应用 span，召回和采集因此与整个 LangGraph 子树并列。

使用该组合的应用可以通过公开 API 配置自己持有的回调：

```python
from langfuse.langchain import CallbackHandler
from opentelemetry import trace

callback = CallbackHandler()
callback.run_inline = True

with trace.use_span(
    trace.get_current_span(),
    end_on_exit=False,
    record_exception=False,
    set_status_on_exception=False,
):
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "这个服务应当怎样部署？"}]},
        config={"callbacks": [callback]},
        context=PowerContextScope(),
    )
```

该作用域应覆盖完整的 `await` 调用；使用流式接口时，应保持到流被消费完毕或关闭。内联回调让召回归属于 model
节点、采集归属于 `after_agent` 节点；调用作用域则在成功、失败或取消后恢复调用方的当前 span。仅设置内联回调仍不够，
因为 LangChain 可能在另一任务中执行受保护的结束回调。中间件不会修改应用的回调对象，也不初始化 provider 或依赖
Langfuse；`auto_capture` 与 `trace_content` 仍由各自的配置控制。

本地验证使用实际 PowerContext ASGI/SQLite runtime 与确定性模型，覆盖工具循环、并发运行和取消。该外部回调组合中，
被取消模型的 generation span 仍可能缺失；调用方上下文恢复和 PowerContext 召回、采集行为，不能证明所有回调均已导出。

### 可选的召回输入、输出采集

使用 `PowerContextMiddleware(trace_content=True)`，可以在 `PowerContextMiddleware recall` 观测中记录本次查询
和实际返回的上下文。它与把完整对话写入 PowerContext 存储的 `auto_capture` 不同。默认值为 `False`，因为
查询和召回文本可能包含用户私有内容。适配层负责创建此观测，父节点沿用当前 OpenTelemetry 上下文，
包括上文说明的异步回调边界。应用负责配置 exporter，以及自己持有的回调。
输入、输出使用 OpenInference 的 `input.value`、`output.value` JSON 字符串；每项原文最多 16 KiB UTF-8，
并明确标记是否截断。空结果也记录实际返回的 null 或空输出及真实上下文状态，请求失败不伪造输出。这个节点不是模型推理或模型选择的工具调用。
服务端操作 span 仍然不记录内容，开启此选项也无法补回历史 span 的输入、输出。

追踪辅助逻辑由 adapter 自己持有，仅安装已发布的 PowerContext 1.5.0 client 也能导入和使用中间件，
包括可选的召回输入、输出采集。在独立工具环境中安装 Server 不会更新应用环境里的 core client。
SDK 依赖节点的 GenAI 属性增强需要 core 支持本次新增的本地 OTel 上下文标记 `powercontext.genai_client`。
1.5.0 core 会忽略该标记，保留原有操作 span 和采集器过滤行为；要获得完整的默认过滤器链路修复，
需要安装配套的 core 与 adapter。该标记不是网络 baggage，不包含凭据，也不配置 provider 或 exporter。

`tracing-tests` 依赖组为 CI 提供固定版本的可选 Langfuse 消费端和 OTLP exporter。
`worker-observability` 作业同时运行 LangChain 消费端回归，并拒绝跳过的用例。
仅修改 LangChain 适配层也会触发该作业和独立安装验证；内容截断和取消清理测试仍由默认测试集执行，不依赖消费端扩展。
