# Configure Pydantic AI inference

PowerContext uses Pydantic AI at the provider boundary while keeping Memory services framework-neutral. Generation
extracts structured Memory candidates. Embedding supplies vectors for indexes that support vector search. Either
capability can be configured independently.

## Install the integration

The inference integration is part of the built-in implementation:

```bash
uv add "powercontext[builtin]"
```

The Server extra includes Builtin:

```bash
uv add "powercontext[server]"
```

## Configure the Server

The standard Server builds inference adapters from `ServerSettings.inference`. A generation model enables
Source-to-Memory extraction:

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="provider:model-name"
```

Vector search needs the embedding model and its complete deployment profile:

```bash
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL="provider:embedding-model"
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID="project-embedding-v1"
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION="1536"
export POWERCONTEXT_SERVER_DATABASE_VEC1_EXTENSION="/opt/sqlite-extensions/vec1"
```

Provider credentials remain in the environment variables understood by the selected Pydantic AI provider. They are
not fields on PowerContext models.

The Server rejects a partial embedding profile. `embedding_model`, `embedding_profile_id`, and `embedding_dimension`
must be configured together. Vec1 also requires that embedding configuration because the index dimension and stored
vectors must agree.

## Compose generation directly

Use direct composition when the application already owns provider model lifecycles:

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

`generation_model` must be an initialized Pydantic AI `Model`, not a provider name string. The application that opens
the model also closes it.

The generator serializes Pydantic input models and validates structured output against the declared output type.
Memory validation still runs after generation, so a schema-valid candidate is not automatically accepted for
persistence.

## Compose embeddings directly

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
        normalization="none",
    ),
    limits=InferenceLimits(timeout_seconds=30),
)
```

Pass this adapter to `open_builtin_contexts()` or `open_builtin_runtime()` with a `SQLiteConfig` that selects the Vec1
extension. The adapter verifies output count, order, dimension, and finite numeric values before vectors reach
persistence.

An `EmbeddingProfile` is a deployment contract, not descriptive metadata. Stored projections and query embeddings
must use the same profile. When the model, dimension, or normalization changes, rebuild Memory projections from the
authoritative revisions.

## Failure behavior

Generation and embedding failures map provider errors into PowerContext inference errors. Timeouts and temporary
provider failures do not commit partial Memory revisions.

For `mode="auto"` search, a temporary query embedding failure can fall back to FTS when the backend supports it.
Explicit vector or hybrid search reports the missing capability instead of silently changing modes.
