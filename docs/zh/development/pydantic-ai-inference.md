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

未设置 `RERANK_MODEL` 时，LLM 重排复用生成模型及其服务地址。重排的请求头和模型参数可以扩展或覆盖生成配置；请求头覆盖仅影响重排，不会改变所用模型和地址。单独设置重排地址时，也必须指定重排模型。未设置重排超时和请求次数上限时，它们沿用生成任务的配置。

使用默认地址和请求头时，通过所选模型服务支持的环境变量提供凭据。已有订阅凭据的复用方式见下文。

`embedding_model`、`embedding_profile_id` 和 `embedding_dimension` 必须一起配置，缺少任一项时 Server 会拒绝该配置。SQLite 向量检索使用这组配置，索引维度必须与存储的向量一致。对于 OpenAI 兼容的 embedding 服务，`embedding_dimension` 默认作为请求参数 `dimensions` 发送，并覆盖模型参数中的同名字段。模型输出维度固定且不接受该参数时，将 `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_SEND_DIMENSIONS` 设为 `false`。本地维度仍然必填，Server 仍会按它校验返回的向量。

## 复用已有订阅凭据

在启动 Server 的环境中提供 Codex 或 GitHub Copilot 凭据，并确保 worker 也能读取。凭据文件的路径以 Server 所在主机及其运行用户为准，客户端机器上的登录不会自动带到 Server。

下面的 Codex 和 Copilot 示例使用默认服务地址和认证方式。使用这些示例时，不要设置 `POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL` 和 `POWERCONTEXT_SERVER_INFERENCE_GENERATION_HEADERS`。将 `<model-name>` 替换为对应订阅可用的模型 ID。

### Codex CLI 凭据

Codex CLI 已使用订阅账号登录，且凭据保存在 `auth.json` 时，使用 `openai-codex:` 模型前缀：

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="openai-codex:<model-name>"
```

Server 默认读取运行用户的 `~/.codex/auth.json`。凭据保存在其他 Codex home 目录时，通过环境变量指定目录：

```bash
export CODEX_HOME="/path/to/existing/codex-home"
```

`CODEX_HOME` 指向包含 `auth.json` 的目录。服务不会更新 CLI 登录文件。认证失效时，用原有 CLI 更新登录，再重启服务。详见 [Pydantic AI 的 Codex 凭据配置](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/docs/models/openai-codex.md)。

### GitHub Copilot 凭据

已有 GitHub CLI OAuth 登录，且账号具有 Copilot 访问权限时，在启动 Server 前导出 token：

```bash
export GITHUB_COPILOT_API_KEY="$(gh auth token)"
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="github-copilot:<model-name>"
```

请选择支持 Chat Completions 的 Copilot 模型。仅支持 Responses API 的模型不能使用 `github-copilot:` 前缀。

也可以沿用环境中已有的 `GITHUB_COPILOT_API_TOKEN` 或 `COPILOT_GITHUB_TOKEN`，它们会依次作为备用变量读取。Server 使用这些专用变量，通用的 `GITHUB_TOKEN` 和 `GH_TOKEN` 不用于 Copilot 认证。示例中的 `gh auth token` 需要在启动服务前执行，Server 不会自动读取 CLI 配置或续期 token。token 更新后，更新服务进程的环境并重启 Server。详见 [Pydantic AI 的 Copilot 配置](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/docs/models/github-copilot.md)。

这些配置支持 Memory、Experience 和 Profile 的生成。Codex 和 Copilot 暂不支持 Topic Memory 与 Dream。如果已经显式启用 Topic Memory 处理或自动调度，切换 provider 前需要移除对应配置。已启用内置推理保护时，切换到这两个 provider 会导致启动失败。

### 已有 ChatGPT Plan Usage 凭据

已有 ChatGPT Plan Usage 授权可以通过 SDK 复用。Server 环境变量暂不支持加载这类凭据，只配置模型名称不足以启用服务。

将已有应用配置好的 Pydantic AI 模型用于下文的 SDK 示例。该模型需要使用 ChatGPT Plan Usage 授权，Codex CLI 凭据不能替代它。详见 [Pydantic AI 的已有凭据配置](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/docs/models/openai-chatgpt.md#persisting-credentials)。

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
