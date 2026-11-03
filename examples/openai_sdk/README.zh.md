# 在 OpenAI Python SDK 中使用 PowerContext

[English](README.md)

本示例用官方 [OpenAI Python SDK](https://github.com/openai/openai-python) 回答一个问题。发送模型请求前，先向
PowerContext 获取 PreparedContext，并把返回的历史上下文加到 system 消息中。PowerContext 没有相关内容时，照常发送
请求，不附带历史。

[`chat.py`](chat.py) 使用异步 `PowerContextClient` 和 `AsyncOpenAI`：

1. 用固定的幂等键创建或复用示例 Scope；也可以用 `POWERCONTEXT_SCOPE` 指定已有 Scope。
2. 带 `--seed` 时，先在该 Scope 中保存一条账单决策 Memory。
3. 以问题作为 query 调用 `prepare_context`，字节预算为 4000。
4. 发送一次 `chat.completions` 请求。PreparedContext 为 `ready` 时，把 `content` 原样追加到 system 消息；为 `empty`
   时，请求只包含指令和问题。

## 安装

需要 Python 3.11+、[`uv`](https://docs.astral.sh/uv/getting-started/installation/) 和一个正在运行的 Server。在本仓库的
克隆目录中执行：

```bash
uv sync
uv run powercontext server run
```

保持 Server 运行，在第二个终端中执行后面的命令。要在自己的项目中使用该脚本，安装 `powercontext[client]` 和
`openai`，复制 `chat.py`，再用 `python chat.py` 运行。

## 配置

| 变量 | 必需 | 用途 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 是 | 由 `AsyncOpenAI` 读取。 |
| `OPENAI_BASE_URL` | 否 | 兼容 OpenAI 的端点；由 `AsyncOpenAI` 读取。 |
| `OPENAI_MODEL` | 否 | 对话模型，默认 `gpt-4o-mini`。 |
| `POWERCONTEXT_URL` | 否 | Server 地址，默认 `http://127.0.0.1:8000`。 |
| `POWERCONTEXT_TOKEN` | 否 | 启用 Server 认证时使用的 Bearer token。 |
| `POWERCONTEXT_SCOPE` | 否 | 已有 Scope ID。不设置时，脚本创建或复用 `openai-sdk-example` Scope。 |

这些值由受信任的应用进程持有，只作为传输凭据发送，不会写入提示词、日志或 Memory。在实际应用中，由受信任的代码选择
并授权 Scope，不要让模型输出决定 Scope。

## 运行

针对运行中的 Server 和真实模型做手动冒烟测试：

```bash
export OPENAI_API_KEY=replace-with-a-secret-store-value
uv run python -m examples.openai_sdk.chat
uv run python -m examples.openai_sdk.chat --seed
```

第一条命令使用新 Scope，PreparedContext 为空，模型在没有历史的情况下回答。第二条命令先保存账单决策，
PreparedContext 为 ready。预期输出形如下面这样，Scope ID、字节数和回答内容会不同：

```text
Scope: scp_...
PreparedContext: empty (0 bytes)
Answer:
...
```

```text
Scope: scp_...
PreparedContext: ready (601 bytes)
Answer:
...
```

可以把其他问题作为第一个参数传入。默认 Server 使用全文检索，能否召回取决于问题是否与已保存的 Memory 有共同词语；
需要语义检索时，配置[向量搜索](../../docs/zh/docs/workflows/configure-vector-search.md)。

## 把召回内容当作历史

PreparedContext 是有界、只读的文本，已经带有“不可信历史”的提示和精确引用。原样传给模型，不要再次截断。当前指令、
授权、实时系统状态和最新验证始终优先于其中的内容。

## 无凭据测试

离线测试用 `httpx.MockTransport` 替代两个 HTTP 服务，检查 prepare 请求、在 ready 和 empty 两种情况下经真实 OpenAI
SDK 发出的消息，以及凭据只出现在 `Authorization` 请求头中：

```bash
uv run pytest tests/examples/openai_sdk
```

这些测试使用模拟响应，不能证明某个 Server 或模型服务确实返回这些响应；这一点请用上面的冒烟测试验证。

关于原生 SDK 的通用接入方式，以及何时用它替代 Agent Host 或框架适配器，见
[使用原生模型 SDK](../../docs/zh/docs/develop/native-sdk.md)。
