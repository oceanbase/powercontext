---
title: Pydantic AI
description: 了解当前 Pydantic AI 适配器 API 和安装状态。
status: community
---

# Pydantic AI

`community` · `experimental`

适配器将 Pydantic AI Agent 连接到运行中的 PowerContext Server，提供 Memory 工具、自动上下文准备与可选事件采集。
其 API 与行为仍为试验性。

## 从源码安装

在应用环境中添加相同 ref 的 Client 和适配器。以下示例使用 OpenAI：

```bash
uv add "powercontext[client] @ git+https://github.com/oceanbase/powercontext.git@master"
uv add "powercontext-pydantic-ai @ git+https://github.com/oceanbase/powercontext.git@master#subdirectory=integrations/pydantic-ai"
uv add "pydantic-ai-slim[openai]>=2.29,<3"
```

按[安装与运行](../get-started/install-and-run.md)从同一 ref 启动独立 Server。
适配器要求 `powercontext[client]>=0.0.3`；这些示例使用匹配的当前源码。
使用其他 Provider 时，替换 `openai` extra 和模型字符串。

## 挂载预览 Capability

下面的示例使用 OpenAI。使用其他 Provider 时，请安装匹配的 `pydantic-ai-slim` Provider extra，并修改模型字符串。

把 Capability 加到 Agent：

```python
from pydantic_ai import Agent
from powercontext_pydantic_ai import PowerContext

agent = Agent(
    "openai:gpt-5.2",
    capabilities=[PowerContext()],
)
```

该 Capability 提供 `powercontext_search`、`powercontext_remember` 和 `powercontext_context`。它还会从最新文本
User Prompt 请求 `prepare_context`，并在一个 run 内最多前置一次不可信证据块。即使新 run 复用旧 message
history，也会重新准备 Context。query 是去掉首尾空白的该 prompt，识别到的凭据按
[自动 prompt 隐私策略](index.md#自动-prompt-隐私策略)替换为 `[REDACTED]`。超过 8192 个字符或 8192 个 UTF-8 字节的
prompt 改用最近的文本召回，先取末尾 8192 个字符，再截到末尾 8192 字节。

如果只需要工具，不需要自动准备与采集，可以只挂载 Toolset：

```python
from pydantic_ai import Agent
from powercontext_pydantic_ai import PowerContextToolset

agent = Agent("openai:gpt-5.2", toolsets=[PowerContextToolset()])
```

## 设置环境变量

```bash
export POWERCONTEXT_PYDANTIC_AI_BASE_URL=http://127.0.0.1:8000
export POWERCONTEXT_PYDANTIC_AI_TOKEN=opaque-server-token
```

| 变量 | 默认值 | 校验与行为 |
| --- | --- | --- |
| `POWERCONTEXT_PYDANTIC_AI_BASE_URL` | `http://127.0.0.1:8000` | HTTP(S)，不能含凭证、query 或 fragment |
| `POWERCONTEXT_PYDANTIC_AI_ALLOW_INSECURE_HTTP` | `false` | 显式允许非环回明文 HTTP |
| `POWERCONTEXT_PYDANTIC_AI_TOKEN` | 未设置 | 以 `SecretStr` 保存的裸可打印 Token |
| `POWERCONTEXT_PYDANTIC_AI_SCOPE_ID` | 未设置 | 最多 256 个字符的已有 Server Scope；未设置时选择 Server 默认 Scope |
| `POWERCONTEXT_PYDANTIC_AI_TIMEOUT` | `10` | 正秒数 |
| `POWERCONTEXT_PYDANTIC_AI_MAX_BYTES` | `8000` | `512` 至 `32768` Context 字节 |
| `POWERCONTEXT_PYDANTIC_AI_CAPTURE_EVENTS` | `false` | 显式同意采集可见事件 |
| `POWERCONTEXT_PYDANTIC_AI_CAPTURE_CHECKPOINT_EVERY` | `5` | 每 `1` 至 `100` 个成功事件 Flush |
| `POWERCONTEXT_PYDANTIC_AI_CAPTURE_MAX_BYTES` | `8192` | 每个事件 `512` 至 `32768` UTF-8 字节 |

Codex 与 Claude Code 插件的相关设置接收完整 authorization 值，而本适配器只接收裸 Token。不要带
`Bearer `，也不要传完整 `Authorization` Header；公共 Client 会补上 scheme。

环回 HTTP 默认可用；非环回 HTTP 需要通过上表环境变量或 `PowerContextSettings(allow_insecure_http=True)`
显式同意。HTTPS 证书校验仍然启用。通用环境变量和绑定地址的持久化同意见
[连接远程 Server](../operate/connect-remote-server.md)。框架适配器没有对应的 setup 安装命令。

`PowerContext` 与 `PowerContextToolset` 都接受 `PowerContextSettings`、稳定的 `id`（默认 `powercontext`），
以及固定或回调形式的 `scope_id`：

```python
from pydantic_ai import RunContext
from powercontext_pydantic_ai import PowerContext, PowerContextSettings

settings = PowerContextSettings(timeout=5, max_bytes=4096)


def tenant_scope(ctx: RunContext[dict[str, str]]) -> str:
    return ctx.deps["powercontext_scope_id"]


capability = PowerContext(settings=settings, scope_id=tenant_scope)
```

回调在每个 Agent run 内只执行一次。Scope 优先级是构造器字符串或回调，其次是环境变量 `SCOPE_ID`。适配器在每个
run 内只把该显式 ID（未配置时为 `None`）发送给 `resolve_scope_binding` 一次，并在该 run 的 Recall、Capture、
Flush 和工具调用中复用 Server 返回的 Scope ID。显式 ID 必须对应 Server 中已有的 Scope；未配置时选择 Server
默认 Scope。适配器不会读取 cwd、Git 元数据或路径来创建 Scope ID。

## 决定是否采集事件

Capture 默认关闭。只有在允许把初始用户文本、可见模型文本和工具调用、已完成的工具参数与结果发送到指定 scope
时，才设置 `POWERCONTEXT_PYDANTIC_AI_CAPTURE_EVENTS=true`。Thinking/reasoning 内容不会采集。含可识别凭据的用户
prompt 不会采集，见[自动 prompt 隐私策略](index.md#自动-prompt-隐私策略)。事件会清洗敏感键、
已知环境凭证和 Codex 凭证，按配置的字节上限渲染，并使用
`powercontext.pydantic-ai-capture-event/v1` schema。

每次成功 Capture 都会推进 run-local Source position。达到配置数量时执行 checkpoint Flush，`after_run` 会 Flush
剩余 Source；并发工具结果在 run-local lock 下获得唯一序号。Recall、Capture 和 Flush 遇到 Server 故障时 fail-open；
显式工具失败则转换为 `ModelRetry`。HTTP 401 或 403 首次出现时只记录一条不含凭证的配置告警。

凭证清洗不能保证普通项目内容不敏感，请同时保护 Server、scope、数据库和日志。

## 把提示词时上下文交给 gateway

Agent 的模型请求经过 PowerContext gateway 时，gateway 为每次模型调用准备并注入上下文；设置 `PC_GATEWAY_SEDIMENT=on` 后，
gateway 也会采集这一轮。Core 检查 gateway 转发的每个请求，拒绝不是 gateway 注入的 prepared-context 块，
在每种隐私模式下都是如此：Core 返回 HTTP 422，gateway 返回 HTTP 503 `gate_paused`。本适配器有两处会产生这样的块：

- `PowerContext` Capability 在每次运行中添加一个。请改用 `PowerContextToolset`，由 gateway 采集各轮。
- `powercontext_context` 的工具结果带有一个，Core 会拒绝把它返回给模型的请求。
  构造工具集时请设置 `include_context_tool=False`，这样既不提供这个工具，也会从工具集说明中去掉它的那一行。

`openai:` 模型字符串本身就会选择 Responses 模型；要让它指向 gateway 的 `/gateway/v1`，请显式构造：

```python
import os

from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from powercontext_pydantic_ai import PowerContextToolset

model = OpenAIResponsesModel(
    "approved-model",
    provider=OpenAIProvider(base_url="http://127.0.0.1:8787/gateway/v1", api_key=os.environ["PC_GATEWAY_KEY"]),
)
toolset = PowerContextToolset(include_context_tool=False)
agent = Agent(model, toolsets=[toolset])

result = await agent.run(
    prompt,
    message_history=history,
    model_settings={"extra_headers": {"X-PowerContext-Conversation-ID": conversation_id}},
)
```

`PC_GATEWAY_KEY` 保存 gateway 的 `PC_GATEWAY_API_KEY` 值。这个请求头标识会话：每个会话使用一个稳定的 ID，长度为 1～256
个可见 ASCII 字符；不要让所有会话共用一个 ID。组合部署（`PC_GATEWAY_CORE_UDS`）
在一个会话中每条用户消息准备一次，并把结果重放给本次运行的工具循环步骤。没有这个请求头时，它根据请求的历史开头推导会话：
除非设置 `openai_previous_response_id` 或 `openai_conversation_id`，Pydantic AI 每次请求都发送完整的消息历史；
以 system 消息和一条用户 prompt 开头的历史即可推导。请求头仍能区分开头相同的会话，
并为采集和前缀提取提供在 gateway 重启后保持不变的会话标识；推导出的 ID 仅在当前 gateway 进程内用于重放准备结果。
独立运行的 gateway（`PC_GATEWAY_CORE_BASE`）只有设置 `PC_GATEWAY_INJECTION=on` 才会注入。
见[网关上下文注入](../operate/deploy-server.md#网关上下文注入)。

开始经过 gateway 时请使用新的消息历史。使用 Capability 的运行会把召回内容作为 system prompt part 保留在历史中；
模型调用过 `powercontext_context` 的历史会保留该结果。Core 会拒绝带有其中任何一种的请求。经过 gateway 的运行，
其历史不含注入的上下文，可以传给下一次运行。

请使用 Responses 路由。在这个路由上，gateway 以非流式方式调用模型，采集通过检查的响应，
并以流式重放给发送流式请求的客户端。`openai-chat:` 模型使用 Chat Completions 路由，在那里，enforcing 模式的 Core
会暂缓流式运行（`run_stream`），gateway 返回 HTTP 400。非 enforcing 模式下，gateway 原样放行流式响应；
设置 `PC_GATEWAY_SEDIMENT=on` 后，还会从中采集完成的一轮。

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
- gateway 在 `PC_GATEWAY_DEFAULT_SCOPE` 指定的同一个 Scope 中准备和采集所有会话。请把它设为 Toolset 解析的 Scope，
  使工具搜索和写入的正是 gateway 召回的 Memory。

## 与 MCP 备选方案比较

连接 PowerContext MCP 不需要额外适配器包，但对 Pydantic AI 来说能力较低。MCP 提供显式工具，不会自动调用
`prepare_context`，也不会采集轨迹或在 checkpoint/run 结束时 Flush。

预览版只支持普通 Pydantic AI run；Temporal、DBOS、Prefect 等 durable execution 尚未验证。Handoff、Candidate
Review、Experience 与 Skill operation 不在本适配器范围内。
