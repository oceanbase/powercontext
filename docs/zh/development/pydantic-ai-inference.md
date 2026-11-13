---
title: "配置 Pydantic AI 推理"
description: "通过 Pydantic AI 配置结构化 Memory 候选生成，并单独配置向量检索所需的 Embedding。"
---

# 配置 Pydantic AI 推理

PowerContext 使用生成模型从 Source 提取 Memory，使用向量模型为支持向量检索的索引生成向量。这两项能力可以分别配置。

## 安装依赖

在 Python 项目中使用内置推理功能，安装 `builtin` extra：

```bash
uv add "powercontext[builtin]"
```

运行 Server 时，安装 `server` extra 即可，它已包含上述依赖：

```bash
uv add "powercontext[server]"
```

这两个 extra 都会安装 Pydantic AI 2.64 或更新的 2.x 版本。

## 配置 Server

设置以下环境变量，为 Server 指定用于从 Source 提取 Memory 的生成模型：

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="provider:model-name"
```

额外的模型参数使用 JSON 配置。模型服务特有的请求字段放在 `extra_body` 中。例如，兼容 OpenAI 接口的服务可以这样关闭 Qwen 的思考模式：

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="openai-chat:<served-model-name>"
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL_SETTINGS='{"extra_body":{"chat_template_kwargs":{"enable_thinking":false}}}'
```

Topic Memory 仅允许 `openai-chat:<model>` 使用示例中的 `extra_body` 结构。值必须是 JSON 布尔值 `false`，两层对象都不能包含额外字段。其他 provider 前缀和向量模型不支持这一配置。模型服务需要实现 `chat_template_kwargs.enable_thinking`；关闭思考模式后，推理仍可能超时。

生成流程、可选的 LLM 重排和就绪检查共用这些参数。就绪检查可能调用模型并消耗用量。可用参数取决于所选模型服务和模型。凭据与静态请求头使用各自的配置项，不要放入模型参数。

启用向量检索时，需要同时指定向量模型、配置标识和向量维度：

```bash
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL="provider:embedding-model"
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID="project-embedding-v1"
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION="1536"
```

生成、向量和重排任务可以分别连接不同的模型服务。按服务接口选择模型前缀：OpenAI Chat Completions 使用 `openai-chat:<model>`，OpenAI Responses 或 embedding 使用 `openai:<model>`，Anthropic 兼容接口使用 `anthropic:<model>`。内置重排使用语言模型对候选列表排序，需要配置生成模型的地址，不支持 cross-encoder 的 `/rerank` 接口：

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="openai-chat:generator"
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL="http://127.0.0.1:8080/v1"
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_HEADERS='{"Authorization":"Bearer generation-secret"}'
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL_SETTINGS='{"max_tokens":4096}'

export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL="openai:embedding"
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_BASE_URL="http://127.0.0.1:8081/v1"
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_HEADERS='{"Authorization":"Bearer embedding-secret"}'
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL_SETTINGS='{"dimensions":1536}'

export POWERCONTEXT_SERVER_INFERENCE_RERANK_MODEL="openai-chat:reranker"
export POWERCONTEXT_SERVER_INFERENCE_RERANK_BASE_URL="http://127.0.0.1:8082/v1"
export POWERCONTEXT_SERVER_INFERENCE_RERANK_HEADERS='{"Authorization":"Bearer rerank-secret"}'
export POWERCONTEXT_SERVER_INFERENCE_RERANK_MODEL_SETTINGS='{"max_tokens":256}'
```

请求头和模型参数都使用 JSON 对象。`*_HEADERS` 中的值按敏感信息处理，并作为相应任务的静态请求头发送。将 `extra_headers` 放入模型参数会被拒绝；请使用独立的请求头配置，以便配置展示和日志脱敏生效。其余模型参数传给所选服务。重排不支持自定义采样温度。自定义向量服务需要提供 OpenAI 兼容的 embeddings 接口，已支持的 MiniMax 原生 embedding 端点除外。

服务地址可以包含网关路径前缀，后面会自动追加 `/chat/completions`、`/responses` 或 `/embeddings` 等接口路径，不支持任意改写这些路径。自定义地址或静态请求头时，模型名称必须带有明确的 OpenAI 或 Anthropic 兼容接口前缀。

Anthropic 兼容网关需要分别认证网关和上游时，配置网关的 `Authorization` 请求头，并通过 `ANTHROPIC_API_KEY` 或显式 `X-Api-Key` 请求头提供上游密钥。PowerContext 会将两个请求头都发给网关；显式 `X-Api-Key` 优先于环境变量中的密钥。网关转发给 Anthropic 前必须移除自己的 `Authorization`，因为官方 API 每次请求只接受一份凭据。网关仅要求 `Authorization` 时，不要设置 `ANTHROPIC_API_KEY` 和 `X-Api-Key`。详见 [Anthropic 的网关认证说明](https://platform.claude.com/docs/en/api/migration-2026-10-21#a-client-holds-one-credential)。

未设置 `RERANK_MODEL` 时，LLM 重排复用生成模型及其服务地址。重排的请求头和模型参数可以扩展或覆盖生成配置；请求头覆盖仅影响重排，不会改变所用模型和地址。单独设置重排地址时，也必须指定重排模型。未设置重排超时和请求次数上限时，它们沿用生成任务的配置。

使用默认地址和请求头时，通过所选模型服务支持的环境变量提供凭据。已有订阅凭据的复用方式见下文。

`embedding_model`、`embedding_profile_id` 和 `embedding_dimension` 必须一起配置，缺少任一项时 Server 会拒绝该配置。SQLite 向量检索使用这组配置，索引维度必须与存储的向量一致。对于 OpenAI 兼容的 embedding 服务，`embedding_dimension` 默认作为请求参数 `dimensions` 发送，并覆盖模型参数中的同名字段。模型输出维度固定且不接受该参数时，将 `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_SEND_DIMENSIONS` 设为 `false`。本地维度仍然必填，Server 仍会按它校验返回的向量。

## 复用已有订阅凭据

在 Server 所在主机配置订阅，并确保 worker 能读取凭据或运行已登录的 CLI。不要设置 `POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL` 和 `POWERCONTEXT_SERVER_INFERENCE_GENERATION_HEADERS`。将下面的模型占位符替换为账号可用的模型。

| 订阅 | 模型配置 | 凭据续期 |
| --- | --- | --- |
| Codex CLI | `openai-codex:<model>` | CLI 负责续期；PowerContext 每次请求重读文件。 |
| GitHub Copilot | `github-copilot:<model>` | 启动前导出 GitHub OAuth token；续期后更新环境并重启。 |
| Grok CLI OAuth | `grok:<model>` | PowerContext 在 CLI 的 OS 锁下更新共享凭据文件。 |
| Gemini CLI / Code Assist | `gemini-cli:<model>` | CLI 负责登录、初始化和续期。 |
| Claude Code 订阅 | `claude-cli:<model>` | 已安装的 CLI 负责登录和续期。 |
| Qwen Code Plus/Pro | `qwen:<model>` | PowerContext 持有独立设备授权并自动续期。 |
| Kimi Code | `kimi:<model>` | PowerContext 持有独立设备授权并自动续期。 |
| ChatGPT Plan Usage | 仅支持 SDK，见下文。 | 应用存储凭据，Pydantic AI 负责续期。 |

Server 订阅适配器支持 Memory、Experience 和 Profile 的生成。切换前应关闭内置推理保护，并移除显式配置的 Topic Memory 处理或调度；Topic Memory 与 Dream 暂不支持。

### Codex CLI 凭据

在 Server 主机使用 Codex CLI 登录后选择模型：

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="openai-codex:<model-name>"
```

PowerContext 默认读取 `~/.codex/auth.json`。使用其他登录目录时配置：

```bash
export CODEX_HOME="/path/to/existing/codex-home"
```

Server 和 worker 不修改该文件，也不兑换其中的刷新令牌。登录过期导致推理失败时，由原 CLI 续期；下一次请求会读取更新后的文件，无需重启 Server。详见 [Pydantic AI 的 Codex 配置](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/docs/models/openai-codex.md)。

### GitHub Copilot 凭据

使用具有 Copilot 权限的账号登录 GitHub CLI，在启动 Server 前导出 token：

```bash
export GITHUB_COPILOT_API_KEY="$(gh auth token)"
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="github-copilot:<model-name>"
```

请选择支持 Chat Completions 的模型。Server 也会依次读取备用变量 `GITHUB_COPILOT_API_TOKEN` 和 `COPILOT_GITHUB_TOKEN`，不使用 `GITHUB_TOKEN` 或 `GH_TOKEN`，也不自动读取 CLI 登录或续期导出的 token。续期后需要更新环境并重启。详见 [Pydantic AI 的 Copilot 配置](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/docs/models/github-copilot.md)。

### Grok CLI OAuth 凭据

在 Server 主机使用 Grok CLI 登录后配置：

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="grok:<model-name>"
export GROK_HOME="/path/to/existing/grok-home"
```

默认文件是 `~/.grok/auth.json`。`GROK_AUTH_PATH` 可指定文件，并优先于 `GROK_HOME`。PowerContext 要求 CLI 的 xAI OAuth 条目，将 Responses 请求发送到 `https://api.x.ai/v1/responses`。独立的 `xai:<model>` 前缀使用 API Key 认证。

服务用户需要凭据目录的写权限。PowerContext 在距到期 60 秒内取得 `auth.json.lock`，续期后原子保存完整凭据并保留无关字段；续期失败不修改文件。资源返回 401 时不刷新或重放请求。这条 API 路径不提供 Magpie 独立的 SuperGrok 代理集成。

### Gemini CLI / Code Assist

在 Server 主机使用 Gemini CLI 完成登录和初始化后配置：

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="gemini-cli:<model-name>"
# Optional: override the default CLI credential file.
export GEMINI_CLI_AUTH_PATH="/private/gemini/oauth_creds.json"
```

PowerContext 默认读取 `~/.gemini/oauth_creds.json`。过期凭据由原 CLI 续期；适配器不读取 keychain、加密缓存、ADC 或 Gemini API Key。组织账号可通过 `GOOGLE_CLOUD_PROJECT` 或 `GOOGLE_CLOUD_PROJECT_ID` 指定已配置的项目。

适配器仅支持非流式 Code Assist 生成，在调用模型前检查账号的项目与 tier，不执行初始化或启用超额 credits。账号资格和额度取决于订阅。详见 [Gemini CLI 认证](https://github.com/google-gemini/gemini-cli/blob/main/docs/get-started/authentication.mdx)。

### 通过真实 Claude Code CLI 使用订阅

启动服务前，将 Claude Code CLI 登录到目标账号：

```bash
claude auth login
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="claude-cli:sonnet"
# Optional: override the executable found on PATH.
export CLAUDE_CLI_EXECUTABLE="/path/to/claude"
```

每次请求启动新的 print 进程，禁用工具、MCP 和会话持久化。支持文本与 prompted JSON；不支持图片、流式输出及 `max_tokens` 以外的模型参数。安装的 CLI 须支持适配器使用的参数。POSIX 上取消会终止其进程组。

使用 CLI 的 `claude.ai` 登录，并移除 API Key、自定义 Anthropic endpoint、云后端和显式 OAuth token 的环境覆盖。CLI 自行控制内部 HTTP 调用及重试，因此该适配器要求关闭内置推理保护。详见 [Claude CLI 参数](https://code.claude.com/docs/en/cli-reference)。

### Qwen Code 和 Kimi Code 设备授权

创建 PowerContext 自有的独立设备授权，再选择模型：

```bash
uv run python -m powercontext.builtin.inference.coding_oauth qwen
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="qwen:<subscription-model>"

uv run python -m powercontext.builtin.inference.coding_oauth kimi
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="kimi:<coding-model>"
```

按命令输出的验证 URL 和 user code 完成授权。凭据默认保存在 `~/.config/powercontext/oauth/` 下的 `qwen.json` 或 `kimi.json`，POSIX 权限为 `0600`。使用其他路径时，在登录和启动服务前设置 `POWERCONTEXT_QWEN_CREDENTIALS_PATH` 或 `POWERCONTEXT_KIMI_CREDENTIALS_PATH`。已有文件不会被覆盖，重新授权需要新路径。

Qwen 对应当前 Code Plus/Pro 订阅，与旧版免费 OAuth 和 API Key 计划分开。Kimi 使用 `https://api.kimi.com/coding/v1`，账号和地区须与其匹配。不要将 CLI 的刷新令牌复制到这些文件后视为独立登录。

两者在距到期 60 秒内持 OS 文件锁续期，并原子保存轮换凭据。续期失败或取消不修改文件，也不发送推理请求。Grok、Qwen 和 Kimi 均关闭 SDK 自动重试及认证层的模型重放。

通过 SDK 接入时，`QwenProvider`、`KimiProvider` 使用 Pydantic AI 公开的 `OpenAICompatibleProvider(openai_client=...)` 接口。Authlib 处理 OAuth grant，PowerContext 维护凭据文件事务。Runtime 关闭自己创建的客户端；SDK 调用者负责关闭自行传入的客户端。

### 已有 ChatGPT Plan Usage 凭据

ChatGPT Plan Usage 可通过 SDK 使用，由应用提供凭据源。Server 不提供该凭据的加载器，Codex CLI 凭据也不能替代这项授权。

```python
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTProvider

provider = OpenAIChatGPTProvider(credential_source=credential_source)
generation_model = OpenAIResponsesModel("<model-name>", provider=provider)
```

凭据源实现异步 `load()` 和 `save(credentials)`，一起存储所有 `OpenAIChatGPTCredentials` 字段。授权须包含 `chatgpt.tokens.use.direct`。Pydantic AI 原生续期可在 401 后重放一次；多个进程共享存储时，应用需要协调续期所有权。详见[持久化 ChatGPT 凭据](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/docs/models/openai-chatgpt.md#persisting-credentials)。

### 验证边界

[OAuth 测试夹具](https://github.com/oceanbase/powercontext/tree/master/tests/fixtures/oauth) 用合成数据和受控服务验证协议、凭据轮换及取消行为。真实账号资格、额度和已安装 CLI 的兼容性需要单独验收；该目录提供向量来源及许可说明。

### Gateway 准入 profile

选择与执行器最终请求和释放结果一致的协议：

| 协议 | 请求与响应 |
| --- | --- |
| `google-codeassist` | 最终 Code Assist 请求与响应封装，候选结果位于 `response.candidates`。 |
| `google-codeassist-chat` | 最终 Code Assist 请求及转换后的 Chat Completion JSON。 |
| `claude-cli-prompt` | 送入 CLI stdin 的 Chat JSON 及规范化结果，逻辑目标固定为 `claude-cli://execute`。 |

Code Assist profile 要求批准的目标与模型、项目，以及显式 `request.generationConfig.maxOutputTokens`。不透明图片、文件及 thought signature 会被暂缓。准备上下文必须定位到 `/request/contents/<index>/parts/<index>/text` 的用户文本。

Responses profile 对流式请求要求 `allow_streaming=true`，执行器须缓冲完整终态后才能进行释放检查。Codex 不接受 `max_output_tokens`，因此要求该显式输出上限的 Core 策略会暂缓请求。Python 的 protected transport 不解析 Responses SSE。

Core 在 enforcing 模式下始终以 `cli_http_unobservable` 暂缓 `claude-cli-prompt`，运行时切换策略后同样如此。prompt 检查不能覆盖 CLI 内部的 HTTP 调用或重试。

## 通过 SDK 使用生成模型

如果应用已经配置好 Pydantic AI 模型，可以将它传给结构化生成器，再创建 Memory 提取流程：

```python
from powercontext.builtin.artifacts.memory import (
    MEMORY_EXTRACTION_INSTRUCTIONS,
    LLMMemoryCandidatePipeline,
    MemoryExtractionInput,
    MemoryExtractionOutput,
)
from powercontext.builtin.inference.pydantic_ai import (
    InferenceLimits,
    PydanticAIStructuredGenerator,
)

generator = PydanticAIStructuredGenerator(
    model=generation_model,
    instructions=MEMORY_EXTRACTION_INSTRUCTIONS,
    input_type=MemoryExtractionInput,
    output_type=MemoryExtractionOutput,
    limits=InferenceLimits(timeout_seconds=30, max_requests=2),
)
candidate_pipeline = LLMMemoryCandidatePipeline(generator)
```

`generation_model` 接收已初始化的 Pydantic AI `Model` 对象，不接受模型名称字符串。应用负责打开和关闭该对象。

生成器按声明的输入类型序列化数据，并按输出类型校验结果。输出通过 schema 校验后，还需要满足 Memory 的写入规则才能保存。

输入和输出之间的业务约束无法写入输出 schema 时，可以传入 `output_check`。该回调接收输入和已通过校验的输出，并在输出违反规则时抛出 `InvalidInferenceOutputError`。异常的 `detail` 会作为重试反馈发给模型，与 schema 校验失败共用 `max_requests` 预算。预算耗尽后，`generate()` 抛出最后一次 `InvalidInferenceOutputError`。`detail` 只应描述规则，不要包含输入内容。

## 通过 SDK 使用向量模型

```python
from pydantic_ai import Embedder

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference.pydantic_ai import (
    InferenceLimits,
    PydanticAIEmbeddingModel,
)

embedding_model = PydanticAIEmbeddingModel(
    embedder=Embedder(provider_embedding_model),
    profile=EmbeddingProfile(
        profile_id="project-embedding-v1",
        model="provider:embedding-model",
        dimension=1536,
        normalization="unit",
    ),
    limits=InferenceLimits(timeout_seconds=30),
)
```

将 `embedding_model` 与 `SQLiteConfig` 一起传给 `open_builtin_contexts()` 或 `open_builtin_runtime()`，即可启用 sqlite-vec 向量索引。保存向量前，PowerContext 会校验数量、顺序、维度和数值是否有效，并按配置执行单位归一化。

通过 SDK 为 MiniMax 向量模型或 Jev 重排提供 HTTP 客户端时，使用 `httpx2.AsyncClient`。

已存储的向量和查询向量必须使用相同的 `EmbeddingProfile`。修改模型、维度或归一化方式后，需要从权威 Memory revision 重建向量投影。

## 失败行为

生成或向量调用失败时，PowerContext 返回相应的推理错误。超时或模型服务暂时不可用时，不会提交部分 Memory revision。

使用 `mode="auto"` 检索时，如果后端支持 FTS，查询向量生成暂时失败时可以回退到 FTS。显式指定 vector 或 hybrid 检索时，系统会报告能力缺失，不会自动切换检索模式。
