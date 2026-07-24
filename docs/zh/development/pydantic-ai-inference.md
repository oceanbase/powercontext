# 为 Memory 组合 Pydantic AI 推理能力

本文面向已经准备好 Memory backend、evidence resolver 和 `MemoryEvidenceCodec` 的应用开发者，说明如何显式注入由应用
创建的 Pydantic AI 对象。Provider 选择、凭证、HTTP client 及其生命周期仍由应用负责。

## 安装可选集成

```console
uv add 'powercontext[pydantic-ai]'
```

该可选集成安装 Pydantic AI slim package、用于 Claude 的 Anthropic provider，以及用于 OpenAI-compatible
endpoint 的 OpenAI provider。

导入 `powercontext` 不会导入 Pydantic AI。只有实际使用可选集成的组合代码才需要导入 adapter 模块。

## 组合两种能力

应用先创建 provider-specific Pydantic AI `Model` 和 embedding model。不要向 PowerContext adapter 传入模型名字符串。

```python
from pydantic_ai import Embedder
from pydantic_ai.embeddings import EmbeddingModel as PydanticAIEmbeddingModelBase
from pydantic_ai.models import Model

from powercontext import EmbeddingProfile, MemoryService
from powercontext.inference.pydantic_ai import (
    InferenceLimits,
    PydanticAIEmbeddingModel,
    PydanticAIStructuredGenerator,
)
from powercontext.memory import (
    LLMMemoryCandidatePipeline,
    MEMORY_EXTRACTION_INSTRUCTIONS,
    MemoryEvidenceProjector,
    MemoryExtractionInput,
    MemoryExtractionOutput,
)


def build_memory_service(
    *,
    generation_model: Model,
    pydantic_embedding_model: PydanticAIEmbeddingModelBase,
    evidence_projector: MemoryEvidenceProjector,
    backend,
    evidence_codec,
    source_resolver,
    artifact_resolver,
) -> MemoryService:
    limits = InferenceLimits(timeout_seconds=30, max_requests=2)
    generator = PydanticAIStructuredGenerator(
        model=generation_model,
        instructions=MEMORY_EXTRACTION_INSTRUCTIONS,
        input_type=MemoryExtractionInput,
        output_type=MemoryExtractionOutput,
        limits=limits,
    )
    candidate_pipeline = LLMMemoryCandidatePipeline(generator, evidence_projector=evidence_projector)

    profile = EmbeddingProfile(
        profile_id="project-embedding-v1",
        model="provider:embedding-model",
        dimension=1536,
        distance="l2",
        normalization="none",
    )
    embedding_model = PydanticAIEmbeddingModel(
        embedder=Embedder(pydantic_embedding_model),
        profile=profile,
        limits=limits,
    )

    return MemoryService(
        backend=backend,
        candidate_pipeline=candidate_pipeline,
        embedding_model=embedding_model,
        evidence_codec=evidence_codec,
        source_resolver=source_resolver,
        artifact_resolver=artifact_resolver,
    )
```

Backend 必须配置完全相同的 `EmbeddingProfile`。Profile 不匹配时 vector 和 hybrid search 不可用，也不会复用其他 profile 的
向量。

默认 evidence projector 只暴露稳定的 Source 和 Artifact 元数据。提取需要 captured task body、summary 或其他领域字段时，
应用必须传入自己拥有的 `MemoryEvidenceProjector`。Projector 必须返回 JSON-compatible value；不支持的值会在模型调用前失败。

先持久化 evidence，再显式请求提取：

```python
updated_memory = await memory_service.remember(
    memory=repo_memory,
    sources=(task_outcome,),
    mode="extract",
)
```

生成模型只能看到本次选择的 evidence 和当前 active entries。add/revise 建议仍是不可信输入：`MemoryService` 会解析
evidence、校验精确 entry version、移除精确重复项，并通过 head CAS 提交新 Revision。空候选会原样返回当前 Memory；生成
失败不会产生 Revision。

Embedding 暂时不可用时，权威 Memory 和全文 projection 仍可在没有向量的情况下提交。`search(mode="auto")` 会降级到
全文搜索；显式 vector 或 hybrid search 会报告 capability 不可用。
