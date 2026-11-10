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

按 Server 所在主机可用的订阅配置服务，并确保 worker 能读取相应凭据或运行已登录的 CLI。凭据文件的路径以 Server 所在主机及其运行用户为准，客户端机器上的登录不会自动带到 Server。

下面的 Codex、Copilot 和 Grok 示例使用默认服务地址和认证方式。使用这些示例时，不要设置 `POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL` 和 `POWERCONTEXT_SERVER_INFERENCE_GENERATION_HEADERS`。将 `<model-name>` 替换为对应订阅可用的模型 ID。

支持的执行方式与凭据边界如下：

| 订阅 | Server | Python SDK | 凭据生命周期 |
| --- | --- | --- | --- |
| Codex CLI | `openai-codex:<model>` | Pydantic AI 原生模型与 PowerContext 的只读认证 | CLI 负责续期；每次请求重读 `auth.json` |
| GitHub Copilot | `github-copilot:<model>` | 原生 `GitHubCopilotProvider` / `GitHubCopilotOAuthFlow` | Server 使用启动环境中的 token；登录与 token 替换由外部负责 |
| ChatGPT Plan Usage | 不提供凭据加载器 | 原生 `OpenAIChatGPTProvider` / `OpenAIChatGPTOAuthFlow` | 应用负责凭据存储；原生 provider 续期并保存轮换后的凭据 |
| Grok CLI OAuth | `grok:<model>` | 异步 HTTPX 客户端上的 `GrokCliAuth` 与 Pydantic AI Responses 模型 | 重读 CLI 文件；在 `auth.json.lock` 下续期并原子替换文件 |
| Claude Code 订阅 | `claude-cli:<model>` | `ClaudeCliModel` | 真实已登录 CLI；每次创建只接受文本的 print 进程，CLI 负责续期与内部重试 |
| Gemini CLI / Code Assist | `gemini-cli:<model>` | 原生 `GoogleModel` 与 Code Assist transport | 只读 CLI OAuth 缓存，发现项目与 tier；CLI 负责续期与 onboarding |
| Qwen Code Plus/Pro | `qwen:<model>` | 原生 OpenAI Chat 模型与 `QwenProvider` / `QwenOAuthAuth` | PowerContext 自有设备授权，持锁续期与原子持久化 |
| Kimi Code | `kimi:<model>` | 原生 OpenAI Chat 模型与 `KimiProvider` / `KimiOAuthAuth` | PowerContext 自有设备授权，持锁续期与原子持久化 |

`xai:<model>` 是 Pydantic AI 原生的 xAI SDK/API Key provider，不是 Grok CLI OAuth，需要额外安装 Pydantic AI 的 `xai` extra。这些集成不提供交互式登录界面。

### Codex CLI 凭据

Codex CLI 已使用订阅账号登录，且凭据保存在 `auth.json` 时，使用 `openai-codex:` 模型前缀：

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="openai-codex:<model-name>"
```

Server 默认读取运行用户的 `~/.codex/auth.json`。凭据保存在其他 Codex home 目录时，通过环境变量指定目录：

```bash
export CODEX_HOME="/path/to/existing/codex-home"
```

`CODEX_HOME` 指向包含 `auth.json` 的目录。凭据续期由 CLI 负责。Server 和 worker 每次请求都会读取完整登录文件，不会兑换其中的刷新令牌或修改文件，避免与 CLI 或其他进程争用一次性刷新令牌。访问令牌过期或被拒绝时，推理会失败，直到原有 CLI 更新登录；下一次请求即可读取更新后的凭据，无需重启服务。此集成不提供无人值守的自动续期。详见 [Pydantic AI 的 Codex 凭据配置](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/docs/models/openai-codex.md)。

### GitHub Copilot 凭据

已有 GitHub CLI OAuth 登录，且账号具有 Copilot 访问权限时，在启动 Server 前导出 token：

```bash
export GITHUB_COPILOT_API_KEY="$(gh auth token)"
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="github-copilot:<model-name>"
```

请选择支持 Chat Completions 的 Copilot 模型。仅支持 Responses API 的模型不能使用 `github-copilot:` 前缀。

也可以沿用环境中已有的 `GITHUB_COPILOT_API_TOKEN` 或 `COPILOT_GITHUB_TOKEN`，它们会依次作为备用变量读取。Server 使用这些专用变量，通用的 `GITHUB_TOKEN` 和 `GH_TOKEN` 不用于 Copilot 认证。示例中的 `gh auth token` 需要在启动服务前执行，Server 不会自动读取 CLI 配置或续期 token。token 更新后，更新服务进程的环境并重启 Server。详见 [Pydantic AI 的 Copilot 配置](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/docs/models/github-copilot.md)。

这些配置支持 Memory、Experience 和 Profile 的生成。这些订阅适配器暂不支持 Topic Memory 与 Dream。如果已经显式启用 Topic Memory 处理或自动调度，切换 provider 前需要移除对应配置。已启用内置推理保护时，切换到这些 provider 会导致启动失败。

### Grok CLI OAuth 凭据

使用官方 Grok CLI 登录后，配置：

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="grok:<model-name>"
export GROK_HOME="/path/to/existing/grok-home"
```

默认文件为 `~/.grok/auth.json`。`GROK_HOME` 指定目录；`GROK_AUTH_PATH` 指定文件且优先级更高。每次请求都会读取当前 xAI OAuth 条目，包括 issuer、client ID、到期时间及可选的 principal 字段。API Key 条目不能替代 CLI OAuth 登录。

集成遵循 [republic 的 Grok OAuth 实现](https://github.com/bubbuild/republic/blob/main/src/republic/providers/grok.py)：Responses 请求发送到 `https://api.x.ai/v1/responses`，Authlib 通过 `https://auth.x.ai/oauth2/token` 执行 public-client 刷新授权。距到期时间不超过 60 秒时，在 CLI 同名的 `auth.json.lock` OS 文件锁下续期。取得锁后再次读取文件，将完整轮换凭据通过同目录原子替换保存；保留其他字段及 provider 条目，响应省略刷新令牌时保留旧令牌。服务用户需要凭据目录的写权限。POSIX 上替换文件的权限为 `0600`。

续期失败或等待刷新响应时取消，不修改凭据、不发送推理请求。资源返回 401 时不自动续期或重放。此 provider 关闭 OpenAI SDK 自动重试，防止失败隐式重复兑换 token 或重发可能计费的模型请求。应用显式重试仍由应用负责。此 provider 不支持自定义 base URL、静态 generation headers 或内置推理保护。

这里接入的是 republic 的 HTTP OAuth 路径。[Magpie 当前的 Grok 实现](https://github.com/yetone/magpie/blob/main/internal/provider/grok.go) 已交给插件，通过 `cli-chat-proxy.grok.com` 和 CLI 驱动续期，两者不能直接视为等价。Grok 适配器不提供该插件，也不提供独立的 Claude Code 常驻会话与工具桥接。

### Gemini CLI / Code Assist

使用官方 Gemini CLI 完成登录与 onboarding 后配置：

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="gemini-cli:<model-name>"
# Optional: an existing unencrypted CLI credential file on this host.
export GEMINI_CLI_AUTH_PATH="/private/gemini/oauth_creds.json"
```

默认文件为 `~/.gemini/oauth_creds.json`。适配器每次读取 `access_token` 与 `expiry_date`，不兑换 CLI 刷新令牌或修改文件；过期后需要原 CLI 续期。不读取 keychain、加密缓存、通用 ADC 或 Gemini API Key。组织账号可配置 `GOOGLE_CLOUD_PROJECT` 或 `GOOGLE_CLOUD_PROJECT_ID`，账号与项目资格仍由 `loadCodeAssist` 判定。

原生 Google SDK/Pydantic AI 模型负责 Gemini 内容序列化。HTTP transport 先解析已完成 onboarding 的项目与 tier，再将请求封装到 `https://cloudcode-pa.googleapis.com/v1internal:generateContent`，并解开 `response.candidates`。不符合资格或尚未初始化的账号在推理前失败。Google SDK 自动重试关闭；仅支持非流式生成，不自行 onboarding，也不启用超额 credits。Google AI Pro/Ultra、个人免费 Code Assist 与组织许可证的额度及资格不同，普通 Google OAuth token 本身不能证明付费权益。来源：[官方 CLI 认证](https://github.com/google-gemini/gemini-cli/blob/main/docs/get-started/authentication.mdx)、[Code Assist converter](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/code_assist/converter.ts)、[Goose OAuth 适配器](https://github.com/block/goose/blob/main/crates/goose/src/providers/gemini_oauth.rs)。

### 通过真实 Claude Code CLI 使用订阅

启动服务前，将官方 Claude Code CLI 登录到目标 Claude 账号：

```bash
claude auth login
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="claude-cli:sonnet"
# Optional: the installed executable on this host.
export CLAUDE_CLI_EXECUTABLE="/path/to/claude"
```

每次模型请求先检查 `claude auth status`，要求 `authMethod=claude.ai`，再运行真实的 `claude --print --output-format json`。文本历史通过 stdin 进入临时工作目录中的进程；禁用内置工具及 MCP 工具，启用 safe mode，不持久化会话，限制为一轮。仅支持文本与 prompted JSON；拒绝调用方工具、图片、流式输出及 `max_tokens` 之外的模型参数。`max_tokens` 通过官方 `CLAUDE_CODE_MAX_OUTPUT_TOKENS` 环境变量传给 CLI，安装版本须支持使用的参数。POSIX 上取消会终止进程组并回收子进程，再返回调用方。

API Key、自定义 Anthropic endpoint、云后端及显式 OAuth token 环境覆盖均被拒绝，避免选择另一条认证与计费路径。凭据续期、内部 HTTP/重试、模型回退及配额判定仍由 CLI 负责。Python 要求关闭内置推理保护，并拒绝要求关闭 SDK 自动重试的执行路径；它无法证明每次内部 HTTP 发送都经过检查。Topic Memory 与 Dream 仍不可用。此处是新建单轮执行契约，不是 Magpie 的常驻会话、MCP 工具回传与 rewind 桥接。来源：[官方 print 参数](https://code.claude.com/docs/en/cli-reference)、[订阅用量说明](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)、[Goose 真实 CLI provider](https://github.com/block/goose/blob/main/crates/goose/src/providers/claude_code.rs)。

### Qwen Code 与 Kimi Code 设备授权

取得 PowerContext 自有的新设备授权后，配置对应模型：

```bash
uv run python -m powercontext.builtin.inference.coding_oauth qwen
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="qwen:<subscription-model>"

uv run python -m powercontext.builtin.inference.coding_oauth kimi
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="kimi:<coding-model>"
```

命令显示验证 URL 与用户码，轮询官方 token 端点，再保存私有凭据（POSIX 权限为 `0600`）。默认文件为 `~/.config/powercontext/oauth/qwen.json` 和 `kimi.json`；可在登录与服务启动前通过 `POWERCONTEXT_QWEN_CREDENTIALS_PATH` 或 `POWERCONTEXT_KIMI_CREDENTIALS_PATH` 指定文件。不覆盖已有文件；重新授权使用新路径。这些是 PowerContext 自有的独立授权，不是复制的 CLI 刷新令牌。

Qwen 使用当前 Plus/Pro 的设备端点 `auth.qwen.ai/api/oauth/device/code`、client `qwen-code-cli`、scope `model:invoke profile:read`，模型请求发送至 `https://api.qwen.ai/compatible-mode/v1/chat/completions`；已退役的免费 OAuth 与使用 API Key 的 Coding/Token Plan 是不同产品。Kimi 使用 `auth.kimi.com/api/oauth/device_authorization`、client `17e5f671-d194-4dfb-9706-5516cb48c098`，模型请求发送至 `https://api.kimi.com/coding/v1/chat/completions`。此适配器只使用该地区端点，不推测地区或接受任意 API 主机。Kimi 的 `kimi_cli` / `0.1.0` platform/version 字段沿用 Goose 的兼容实现；User-Agent 标识 PowerContext，设备 UUID 属于新授权。这些是协议兼容字段，不能证明上游已接受订阅调用。

`QwenProvider`、`KimiProvider` 分别位于 `powercontext.builtin.inference` 下的 `qwen`、`kimi` 模块，继承 Pydantic AI 公开的 `OpenAICompatibleProvider`，通过 `openai_client=` 接收已认证的 `AsyncOpenAI`。Runtime factory 关闭自建客户端；SDK 调用者负责所注入客户端的生命周期。各模块维护自身 endpoint、scope、凭据路径和身份字段；`coding_oauth` 分发登录命令。

Authlib 公开的 `AsyncOAuth2Client` 负责 token grant 和响应解析，`OAuth2Auth.prepare` 只提供认证 headers，不重建 HTTPX2 推理请求或修改 body。共享 `oauth` 模块维护凭据文件事务及有界 RFC 8628 轮询，后者没有现成的 Authlib 异步高层 client。Pydantic AI 2.64 的私有通用 OAuth 生命周期会在 401 后刷新和重放，CredentialSource 也不使跨进程 load/refresh/save 互斥；这里保留应用自有 OS 锁和不重放策略。

两者每次重读自有文件，在距到期 60 秒内通过 Authlib、OS 文件锁、持锁重读执行续期，再原子持久化完整凭据。续期被拒、token 响应畸形或等待续期时取消，均不修改凭据、不发送推理。没有自动模型重放或 SDK 重试。不支持自定义 base URL、静态 generation headers、内置推理保护、Topic Memory 与 Dream。来源：[Qwen 订阅 OAuth client](https://github.com/QwenLM/qwen-code/blob/main/packages/core/src/qwen/subscription-oauth-client.ts)、[Kimi 当前 OAuth package](https://github.com/MoonshotAI/kimi-code/tree/main/packages/oauth)、[Goose Kimi provider 与测试](https://github.com/block/goose/blob/main/crates/goose/src/providers/kimicode.rs)。

### 已有 ChatGPT Plan Usage 凭据

已有 ChatGPT Plan Usage 授权可以通过 SDK 复用。Server 环境变量暂不支持加载这类凭据，只配置模型名称不足以启用服务。

将已有应用配置好的 Pydantic AI 模型用于下文的 SDK 示例。该模型需要使用 ChatGPT Plan Usage 授权，Codex CLI 凭据不能替代它。详见 [Pydantic AI 的已有凭据配置](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/docs/models/openai-chatgpt.md#persisting-credentials)。

通过应用自有的凭据源构造原生模型，再传给下文的生成器：

```python
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTProvider

provider = OpenAIChatGPTProvider(credential_source=credential_source)
generation_model = OpenAIResponsesModel("<model-name>", provider=provider)
```

`credential_source` 实现异步 `load()` 与 `save(credentials)`，一起存储 `OpenAIChatGPTCredentials` 的所有字段。授权必须包含 `chatgpt.tokens.use.direct` scope。原生续期和同实例并发合并由 Pydantic AI 负责；刷新前重读存储不提供跨进程互斥，应用需要按部署方式确定存储与凭据所有权。原生 ChatGPT provider 可以在 401 后续期并重放一次，与 Codex 和 Grok 适配器的规则不同。仅传入 `credentials=` 时，轮换凭据只保存在内存；传入 `openai_client=` 会退出原生凭据注入与续期。

### 验证边界

公共 [OAuth 夹具](https://github.com/oceanbase/powercontext/tree/master/tests/fixtures/oauth) 描述确定性的 HTTP 与存储行为。Python runner 使用合成凭据、临时文件和受控 HTTP transport；独立的 Grok 测试通过 spawned 进程、真实共享文件与 OS 锁、受控本地 HTTP 服务，检查多个 worker 合并一次续期，以及重启后继续使用已持久化的轮换令牌。这些是本地协议与生命周期验证，不是真实订阅验收，不能据此断言实际 CLI 或订阅接受对应端点、模型、客户端身份与计费路径。CLI 互操作及非 POSIX 文件锁仍需要相应环境验收。

### Gateway 准入 profile

Core 检查器支持最终 Code Assist 请求 JSON。`protocol="google-codeassist"` 对应原生 `response.candidates` 释放；`protocol="google-codeassist-chat"` 对应最终 Chat Completion 释放。应选择执行器实际释放的格式，检查中间表示不能授权后续转换。请求须有批准的精确目标与模型、项目、可检查的文本/函数内容，以及显式且受限的 `request.generationConfig.maxOutputTokens`。不透明图片、文件和 thought signature 均 withheld。准备证据使用精确的 `/request/contents/<index>/parts/<index>/text` 用户文本位置，system/model 文本不能被提升为证据权威。

Responses 的 `stream:true` 请求要求 `allow_streaming=true`，仍执行输出/上下文预算及 `store:false` 检查。执行器必须缓冲完整终态，并在 JSON 释放检查前完成归一化；这不代表 Python protected transport 新增了 Responses SSE 解析。Codex 端点不接受 `max_output_tokens`，因此要求该显式 enforcing 预算的部署仍会 withheld Codex；profile 配置中的限额不会让上游执行不支持的限制。

真实 CLI 执行的 Core profile 使用 `protocol: "claude-cli-prompt"` 与唯一逻辑目标 `endpoint: "claude-cli://execute"`。advisory 检查最终送入 stdin 的 Chat JSON 和规范化结果；enforcing 始终以 `cli_http_unobservable` 暂缓，包括运行时切换策略后的检查。prompt 获准不能授权不可观察的 CLI 内部 HTTP 或重试。其他 CLI 目标，以及将此目标与 HTTP 协议配对，均为无效配置。

上游测试向量位于 `tests/fixtures/oauth/upstream/`。其中 README 记录 republic、Goose、Qwen Code、Kimi Code 的精确 commit、测试路径、原始 input/expected 断言及适配差异，许可证与署名统一归入 `LICENSES/oauth.md`。这些是迁移的源码测试向量，不是真实订阅流量录制；原 27 项 OAuth 契约和进程、锁、取消、Core 回归仍明确属于本项目自补测试。受控验证只用合成凭据与合成可执行文件，不代表真实账户资格或配额验收。

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
