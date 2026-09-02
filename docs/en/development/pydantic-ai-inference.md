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

Additional Pydantic AI model settings can be supplied as JSON. Provider-specific request fields belong under
`extra_body`; for example, a compatible OpenAI-style endpoint can disable Qwen thinking with:

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL_SETTINGS='{"extra_body":{"chat_template_kwargs":{"enable_thinking":false}}}'
```

These settings are shared by the generation-backed pipelines, the optional LLM reranker, and the readiness probe.
PowerContext retains its own request bounds: readiness uses `max_tokens=1`, and reranking uses `temperature=0`.
Keep credentials and static headers in provider configuration rather than model settings.

Vector search needs the embedding model and its complete deployment profile:

```bash
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL="provider:embedding-model"
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID="project-embedding-v1"
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION="1536"
```

Each workload can target a different model service. Custom base URLs use the provider interface named by the model
identifier; use `openai-chat:<model>` for an OpenAI-compatible Chat Completions service, `openai:<model>` for an
OpenAI-compatible Responses or embeddings service, and `anthropic:<model>` for an Anthropic-compatible generation
service. The built-in reranker is an LLM listwise reranker, so its independent endpoint is also a Pydantic AI generation
endpoint rather than a cross-encoder `/rerank` API:

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

The header and model-settings values are JSON objects. Header values are treated as secrets by settings models and are
installed as static headers on the workload's provider client. They are not included in Pydantic AI request settings.
Do not put `extra_headers` inside a model-settings object; use the dedicated headers variable so configuration and log
redaction remain effective. Pydantic AI passes the remaining model settings through to the selected provider. The
reranker always fixes `temperature` to zero. A custom embedding base URL currently requires the OpenAI-compatible
embeddings interface.

A base URL may contain a gateway path prefix. The selected Pydantic AI provider still owns the operation suffix, such
as `/chat/completions`, `/responses`, or `/embeddings`; arbitrary operation-path rewriting is not supported.
Custom base URLs and static headers require an explicit OpenAI- or Anthropic-compatible model identifier so
PowerContext can construct the corresponding provider client.

When `RERANK_MODEL` is unset, LLM reranking reuses the generation model and base URL. Reranker headers and model
settings can still extend or override the generation configuration. A header override creates a separate provider
client for the rerank workload while retaining the generation model identifier and base URL. A separate reranker base
URL requires an explicit reranker model. The reranker timeout and request limit inherit their generation counterparts
unless they are set explicitly.

When no custom base URL or headers are needed, provider credentials remain in the environment variables understood by
the selected Pydantic AI provider.

The Server rejects a partial embedding profile. `embedding_model`, `embedding_profile_id`, and `embedding_dimension`
must be configured together. SQLite vector search uses that embedding configuration because the index dimension and
stored vectors must agree.

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
        normalization="unit",
    ),
    limits=InferenceLimits(timeout_seconds=30),
)
```

Pass this adapter to `open_builtin_contexts()` or `open_builtin_runtime()` with a `SQLiteConfig`. The bundled
sqlite-vec index is enabled automatically. The adapter verifies output count, order, dimension, and finite numeric
values, then applies the declared unit normalization before vectors reach persistence.

An `EmbeddingProfile` is a deployment contract, not descriptive metadata. Stored projections and query embeddings
must use the same profile. When the model, dimension, or normalization changes, rebuild Memory projections from the
authoritative revisions.

## Failure behavior

Generation and embedding failures map provider errors into PowerContext inference errors. Timeouts and temporary
provider failures do not commit partial Memory revisions.

For `mode="auto"` search, a temporary query embedding failure can fall back to FTS when the backend supports it.
Explicit vector or hybrid search reports the missing capability instead of silently changing modes.
