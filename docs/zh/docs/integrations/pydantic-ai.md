---
status: community
title: Pydantic AI
description: 了解当前 Pydantic AI 适配器 API 和安装状态。
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
history，也会重新准备 Context。

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
时，才设置 `POWERCONTEXT_PYDANTIC_AI_CAPTURE_EVENTS=true`。Thinking/reasoning 内容不会采集。事件会清洗敏感键、
已知环境凭证和 Codex 凭证，按配置的字节上限渲染，并使用
`powercontext.pydantic-ai-capture-event/v1` schema。

每次成功 Capture 都会推进 run-local Source position。达到配置数量时执行 checkpoint Flush，`after_run` 会 Flush
剩余 Source；并发工具结果在 run-local lock 下获得唯一序号。Recall、Capture 和 Flush 遇到 Server 故障时 fail-open；
显式工具失败则转换为 `ModelRetry`。HTTP 401 或 403 首次出现时只记录一条不含凭证的配置告警。

凭证清洗不能保证普通项目内容不敏感，请同时保护 Server、scope、数据库和日志。

## 与 MCP 备选方案比较

连接 PowerContext MCP 不需要额外适配器包，但对 Pydantic AI 来说能力较低。MCP 提供显式工具，不会自动调用
`prepare_context`，也不会采集轨迹或在 checkpoint/run 结束时 Flush。

预览版只支持普通 Pydantic AI run；Temporal、DBOS、Prefect 等 durable execution 尚未验证。Handoff、Candidate
Review、Experience 与 Skill operation 不在本适配器范围内。
