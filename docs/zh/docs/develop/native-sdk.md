---
title: 使用原生模型 SDK
description: 在直接调用 OpenAI 或 Anthropic 的应用中加入 PowerContext PreparedContext。
---

# 使用原生模型 SDK

PowerContext 不会包装或替换你的模型 SDK。应用保留自己的 `openai` 或 `anthropic` 客户端，通过异步 Python Client
单独调用 PowerContext。不需要 Agent Host、提示词 Hook、插件或 MCP 连接。

当应用、服务或脚本自己掌控模型循环，并且希望把供应商相关代码留在应用中时，使用这种方式。如果希望宿主自动采集提示词
并召回上下文，请选择 [Agent 集成](../integrations/index.md)。

## 请求流程

每一轮请求中，应用：

1. 在受信任代码中选择并授权一个已有 Scope；
2. 以当前问题作为 query、以字节预算调用 `prepare_context`；
3. 发送一次模型请求；结果为 `ready` 时把返回的 `content` 加入 system 提示；
4. 结果为 `empty` 时照常继续。

PowerContext 是独立服务。模型供应商请求和 PowerContext 请求使用各自的客户端与凭据。

## 可运行示例

两个示例用不同的供应商 SDK 实现同一流程：

| SDK | 示例 | 命令 |
| --- | --- | --- |
| OpenAI | [`examples/openai_sdk`](https://github.com/oceanbase/powercontext/tree/master/examples/openai_sdk) | `uv run python -m examples.openai_sdk.chat --seed` |
| Anthropic | [`examples/anthropic_sdk`](https://github.com/oceanbase/powercontext/tree/master/examples/anthropic_sdk) | `uv run python -m examples.anthropic_sdk.chat --seed` |

两个示例都通过固定幂等键创建或复用示例 Scope，在带 `--seed` 时保存一条账单决策，以 4000 字节预算调用
`prepare_context`，然后发送一次请求。`ready` 内容原样追加到 system 提示；`empty` 时只发送指令和问题，不附带历史。

各示例的 README 列出了安装、环境变量、预期输出和离线测试。

## 1. 启动 Server

安装 Server 与 CLI，并保持 Server 运行：

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext server run
```

在本仓库的克隆目录中，`uv sync` 后执行 `uv run powercontext server run` 等价。发布版本选择、存储和平台要求见
[安装与运行](../get-started/install-and-run.md)。

## 2. 运行示例

示例模块位于仓库中，不在发布的 wheel 中，因此必须在克隆目录中运行。第 1 步的 `uv tool install` 只安装 Server 和 CLI。
在第二个终端中：

```bash
git clone https://github.com/oceanbase/powercontext.git
cd powercontext
uv sync
```

然后在仓库根目录设置供应商密钥并运行一个示例：

```bash
export OPENAI_API_KEY=replace-with-a-secret-store-value
uv run python -m examples.openai_sdk.chat
uv run python -m examples.openai_sdk.chat --seed
```

```bash
export ANTHROPIC_API_KEY=replace-with-a-secret-store-value
uv run python -m examples.anthropic_sdk.chat
uv run python -m examples.anthropic_sdk.chat --seed
```

每组第一条命令使用没有相关 Memory 的 Scope，因此 PreparedContext 为 `empty`，请求不附带历史。第二条命令先保存
示例决策，因此 PreparedContext 为 `ready`：

```text
Scope: scp_...
PreparedContext: ready (601 bytes)
Answer:
...
```

可以把其他问题作为第一个参数传入。两个示例也读取 `POWERCONTEXT_URL`、`POWERCONTEXT_TOKEN` 和
`POWERCONTEXT_SCOPE`。

## 3. 接入你的应用

供应商代码保持普通 SDK 调用。只有 system 提示会变化，而且仅在 PowerContext 返回内容时变化。

OpenAI：

```python
from openai import AsyncOpenAI
from powercontext.client import PowerContextClient
from powercontext.http import PrepareContextRequest, PreparedContext

prepared = await powercontext.prepare_context(
    PrepareContextRequest(scope_id=scope_id, query=question, max_bytes=4000)
)
if not isinstance(prepared, PreparedContext):
    raise TypeError("ordinary context preparation returned an unexpected envelope")

system = SYSTEM_INSTRUCTIONS
if prepared.status == "ready" and prepared.content:
    system = f"{SYSTEM_INSTRUCTIONS}\n\n{prepared.content}"

completion = await openai.chat.completions.create(
    model=model,
    messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ],
)
answer = completion.choices[0].message.content or ""
```

Anthropic：

```python
from anthropic import AsyncAnthropic
from powercontext.client import PowerContextClient
from powercontext.http import PrepareContextRequest, PreparedContext

prepared = await powercontext.prepare_context(
    PrepareContextRequest(scope_id=scope_id, query=question, max_bytes=4000)
)
if not isinstance(prepared, PreparedContext):
    raise TypeError("ordinary context preparation returned an unexpected envelope")

system = SYSTEM_INSTRUCTIONS
if prepared.status == "ready" and prepared.content:
    system = f"{SYSTEM_INSTRUCTIONS}\n\n{prepared.content}"

message = await anthropic.messages.create(
    model=model,
    max_tokens=1024,
    system=system,
    messages=[{"role": "user", "content": question}],
    stream=False,
)
answer = "".join(block.text for block in message.content if block.type == "text")
```

在应用中只创建一次客户端，并用 `async with PowerContextClient(...)` 和供应商客户端的异步上下文管理器关闭。
`prepare_context` 默认返回普通 `PreparedContext`；设置 `include_reuse=True` 时返回带版本的复用信封。示例使用普通响应。

## PreparedContext 契约

`PreparedContext` 是一次准备请求的有界、只读结果：

| 字段 | 含义 |
| --- | --- |
| `status` | 选中内容时为 `ready`，没有相关内容时为 `empty` |
| `content` | `ready` 时选中文本，`empty` 时为 `None` |
| `content_bytes` | `content` 的 UTF-8 字节数 |

`max_bytes` 接受 512 到 32768 字节，默认 8000。新 Scope 或无关问题返回 `empty` 是正常结果；此时不附带历史继续发送
模型请求，不要伪造上下文。

`content` 应原样传给模型。它已经是有界文本，自带“不可信历史”提示和引用，不要再截断。当前指令、授权、实时系统状态
和最新验证始终优先于召回文本。不要把召回内容当作新的策略；它是数据，不是指令。

PreparedContext 是临时的。后续轮次需要上下文时，用当轮 query 再次调用 `prepare_context`。

## 配置访问与 Scope

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `POWERCONTEXT_URL` | `http://127.0.0.1:8000` | PowerContext Server 地址 |
| `POWERCONTEXT_TOKEN` | 未设置 | 启用 Server 认证时的 Bearer token |
| `POWERCONTEXT_SCOPE` | 未设置 | 示例使用的已有 Scope ID |

这些值由受信任的应用持有。Scope ID 是数据分区键，不是访问控制检查，也不证明用户身份。在受信任代码中选择并授权
Scope；不要让模型输出选择其他用户的 Scope。凭据只放在传输配置中，绝不写入提示词、日志或 Memory。

默认允许回环 HTTP。非回环 HTTP 必须使用 HTTPS，或采用
[连接远程 Server](../operate/connect-remote-server.md)中说明的显式确认。HTTPS 证书校验保持开启。

## 检索与空结果

默认 Server 使用全文检索，因此能否召回取决于 query 是否与已保存 Memory 有共同词语。需要语义检索时，配置
[向量搜索](../workflows/configure-vector-search.md)。检索模式由 Server 策略决定；原生 SDK 调用方只提供 query 和
字节预算。

## 无凭据测试

示例测试用 `httpx.MockTransport` 替代 PowerContext Server 和模型端点，同时使用真实的 PowerContext Client 和
供应商 SDK。测试检查 prepare 请求、`ready` 与 `empty` 两种情况下的 system 提示和消息，以及凭据只出现在请求头中：

```bash
uv run pytest tests/examples/openai_sdk tests/examples/anthropic_sdk
```

这些测试使用模拟响应，不能证明某个 Server 或供应商确实返回这些响应；这一点请用上面的冒烟测试命令验证。

## 下一步

- [API Quick Start](api-quickstart.md)：通过 HTTP API 采集 Source 并写入 Memory。
- [接口](interfaces.md)：比较 Python Client、HTTP API 和 Core SDK。
- [HTTP 行为](http-api.md)：认证、请求 ID、错误与并发。
- [Python Agent 框架](../integrations/index.md#python-agent-框架)：接管模型循环的适配器。
