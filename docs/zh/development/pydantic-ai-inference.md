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

provider credential 仍使用所选 Pydantic AI provider 支持的环境变量，不属于 PowerContext model 字段。

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
