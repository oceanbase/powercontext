# 配置 Pydantic AI 推理

PowerContext 在 provider 边界使用 Pydantic AI，同时保持 Memory service 与具体框架无关。generation 用于提取结构化
Memory candidate，embedding 为支持 vector search 的 index 提供向量。这两项能力可以独立配置。

## 安装集成

推理集成属于内置实现：

```bash
uv add "powercontext[builtin]"
```

Server extra 已经包含 Builtin：

```bash
uv add "powercontext[server]"
```

## 配置 Server

标准 Server 根据 `ServerSettings.inference` 构造推理 adapter。配置 generation model 后会启用
Source-to-Memory extraction：

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="provider:model-name"
```

额外的 Pydantic AI model settings 可以通过 JSON 配置。provider-specific request field 应放在 `extra_body`
下面；例如，兼容的 OpenAI-style endpoint 可以这样关闭 Qwen thinking：

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL_SETTINGS='{"extra_body":{"chat_template_kwargs":{"enable_thinking":false}}}'
```

generation-backed pipeline、可选的 LLM reranker 和 readiness probe 共用这些 settings。PowerContext 仍保留自身的
请求边界：readiness 使用 `max_tokens=1`，rerank 使用 `temperature=0`。credential 与 static header 应放在
provider 配置中，不要放入 model settings。

vector search 需要 embedding model 和完整的 deployment profile：

```bash
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL="provider:embedding-model"
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID="project-embedding-v1"
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION="1536"
```

每类 workload 可以连接不同的模型服务。自定义 base URL 使用 model identifier 指定的 provider 接口：
OpenAI-compatible Chat Completions 服务使用 `openai-chat:<model>`，OpenAI-compatible Responses 或 embedding
服务使用 `openai:<model>`，Anthropic-compatible generation 服务使用 `anthropic:<model>`。内置 reranker 是 LLM
listwise reranker，因此它的独立 endpoint 也是 Pydantic AI generation endpoint，而不是 cross-encoder `/rerank`
API：

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

header 和 model settings 都使用 JSON object。settings model 会将 header value 作为 secret 处理，并将它们作为
workload provider client 的静态 header；这些值不会进入 Pydantic AI request settings。不要在 model settings 中配置
`extra_headers`；使用独立的 headers 变量才能保留配置与日志脱敏语义。其余 model settings 由 Pydantic AI 传递给
所选 provider。reranker 始终将 `temperature` 固定为零。自定义 embedding base URL 目前要求服务实现
OpenAI-compatible embeddings 接口。

base URL 可以包含 gateway path prefix，但具体 operation suffix 仍由所选 Pydantic AI provider 决定，例如
`/chat/completions`、`/responses` 或 `/embeddings`；不支持任意改写 operation path。
自定义 base URL 或静态 header 时必须使用显式的 OpenAI- 或 Anthropic-compatible model identifier，PowerContext
才能创建对应的 provider client。

未设置 `RERANK_MODEL` 时，LLM rerank 复用 generation model 和 base URL；仍可通过 reranker headers 和 model
settings 扩展或覆盖 generation 配置。覆盖 header 时会为 rerank workload 创建独立 provider client，但保留
generation model identifier 和 base URL。独立的 reranker base URL 必须同时配置显式 reranker model。reranker
timeout 和 request limit 未显式设置时继承 generation 的对应配置。

不需要自定义 base URL 或 header 时，provider credential 仍使用所选 Pydantic AI provider 支持的环境变量。

Server 会拒绝不完整的 embedding profile。`embedding_model`、`embedding_profile_id` 和
`embedding_dimension` 必须一起配置。SQLite vector search 使用这组配置，因为 index dimension 必须与持久化向量一致。

## 直接组合 generation

应用已经持有 provider model 生命周期时，可以直接组合：

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

`generation_model` 必须是已经初始化的 Pydantic AI `Model`，不能是 provider name 字符串。打开 model 的应用也负责
关闭它。

generator 使用 Pydantic 序列化 input model，并根据声明的 output type 校验结构化输出。generation 之后仍会执行
Memory validation，因此 schema 有效的 candidate 不会自动获得持久化资格。

## 直接组合 embedding

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

将这个 adapter 与 `SQLiteConfig` 一起传给 `open_builtin_contexts()` 或 `open_builtin_runtime()` 后，会自动启用捆绑的
sqlite-vec index。向量进入持久化之前，adapter 会校验输出数量、顺序、dimension 和数值有效性，并执行 profile 声明的单位归一化。

`EmbeddingProfile` 是 deployment contract，不是描述性 metadata。持久化 projection 和 query embedding 必须使用
同一个 profile。model、dimension 或 normalization 发生变化后，应从权威 Memory revision 重建 projection。

## 失败行为

generation 和 embedding failure 会将 provider error 映射为 PowerContext inference error。timeout 和临时 provider
failure 不会提交部分 Memory revision。

对于 `mode="auto"` 的检索，如果 backend 支持 FTS，临时 query embedding failure 可以回退到 FTS。显式 vector
或 hybrid search 会报告能力缺失，不会静默改变模式。
