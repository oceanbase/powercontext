# Compose Pydantic AI inference for Memory

This how-to is for application developers who already have a Memory backend, evidence resolvers, and a
`MemoryEvidenceCodec`. It shows how to inject application-constructed Pydantic AI objects. Provider selection,
credentials, HTTP clients, and their lifecycle remain application concerns.

## Install the optional integration

```console
uv add 'powercontext[pydantic-ai]'
```

Importing `powercontext` does not import Pydantic AI. Only import the adapter module in the composition code that uses
the optional integration.

## Compose the capabilities

Construct the provider-specific Pydantic AI `Model` and embedding model in the application. Do not pass a model
name string to a PowerContext adapter.

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

Configure the backend with the exact same `EmbeddingProfile`. A mismatched profile disables vector and hybrid search;
it never reuses vectors from another profile.

The default evidence projector exposes only stable Source and Artifact metadata. Pass an application-owned
`MemoryEvidenceProjector` when extraction needs captured task bodies, summaries, or other domain fields. The projector
must return JSON-compatible values; unsupported values fail before the model call.

Persist the evidence first, then request extraction explicitly:

```python
updated_memory = await memory_service.remember(
    memory=repo_memory,
    sources=(task_outcome,),
    mode="extract",
)
```

The generator sees only the selected evidence and current active entries. Its add/revise suggestions remain untrusted:
`MemoryService` resolves evidence, validates exact entry versions, removes exact duplicates, and commits the new
Revision with head CAS. An empty candidate list returns the current Memory unchanged. Generation failure commits no
Revision.

If embedding is temporarily unavailable, authoritative Memory and full-text projections can still commit without a
vector. `search(mode="auto")` falls back to full-text search; explicit vector or hybrid search reports an unavailable
capability.
