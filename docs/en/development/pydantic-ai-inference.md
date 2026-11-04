---
title: "Configure Pydantic AI inference"
description: "Set up Pydantic AI generation for structured Memory candidates and configure embedding separately for vector search."
---

# Configure Pydantic AI inference

PowerContext uses a generation model to extract Memory from Sources and an embedding model to generate vectors for indexes that support vector search. You can configure each capability independently.

## Install dependencies

Install the `builtin` extra to use inference in a Python project:

```bash
uv add "powercontext[builtin]"
```

To run the Server, install the `server` extra, which includes the same dependencies:

```bash
uv add "powercontext[server]"
```

Both extras install Pydantic AI 2.64 or a newer 2.x version.

## Configure the Server

Set the following environment variable to choose the Server's generation model for extracting Memory from Sources:

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="provider:model-name"
```

Supply additional model settings as JSON. Put service-specific request fields under `extra_body`. For example, an OpenAI-compatible service can disable Qwen thinking with:

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="openai-chat:<served-model-name>"
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL_SETTINGS='{"extra_body":{"chat_template_kwargs":{"enable_thinking":false}}}'
```

For Topic Memory, only `openai-chat:<model>` accepts the `extra_body` structure shown above. The value must be the JSON boolean `false`, and neither object may contain additional fields. Other provider prefixes and embedding models do not support this setting. The service must implement `chat_template_kwargs.enable_thinking`; requests can still time out with thinking disabled.

Generation, optional LLM reranking, and readiness checks share these settings. Readiness checks can call the model and consume quota. Available settings depend on the selected service and model. Use the dedicated configuration fields for credentials and static headers instead of putting them in model settings.

To enable vector search, set the embedding model, profile ID, and vector dimension together:

```bash
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL="provider:embedding-model"
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID="project-embedding-v1"
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION="1536"
```

Generation, embedding, and reranking can each use a different model service. Choose the model prefix for the service's interface: `openai-chat:<model>` for OpenAI Chat Completions, `openai:<model>` for OpenAI Responses or embeddings, and `anthropic:<model>` for an Anthropic-compatible interface. Built-in reranking uses a language model to rank a list of candidates, so configure a generation endpoint. Cross-encoder `/rerank` endpoints are unsupported:

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

Header and model settings are JSON objects. Values in `*_HEADERS` are treated as secrets and sent as static headers for the corresponding task. Model settings reject `extra_headers`; use the dedicated header configuration so configuration displays and logs can redact those values. Other model settings pass through to the selected service. Reranking does not support a custom sampling temperature. Custom embedding services must provide the OpenAI-compatible embeddings interface, except for supported MiniMax-native embedding endpoints.

A base URL can include a gateway path prefix. The service's interface path, such as `/chat/completions`, `/responses`, or `/embeddings`, is appended automatically and cannot be replaced with an arbitrary path. When configuring a custom URL or static headers, use an explicit OpenAI- or Anthropic-compatible model prefix.

When `RERANK_MODEL` is unset, LLM reranking uses the generation model and URL. Reranker headers and model settings can extend or override the generation configuration. Header overrides apply only to reranking and leave the selected model and URL unchanged. If you set a separate reranker URL, also specify a reranker model. The reranker uses the generation timeout and request limit unless you override them.

When using the default URL and headers, supply credentials through the environment variables supported by the selected service. See below for reusing subscription credentials.

Set `embedding_model`, `embedding_profile_id`, and `embedding_dimension` together; the Server rejects a configuration with any of them missing. SQLite vector search uses these settings because the index dimension must match the stored vectors. For OpenAI-compatible embeddings, `embedding_dimension` is sent as the `dimensions` request field by default and overrides that field in model settings. If a model has a fixed output dimension and rejects the field, set `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_SEND_DIMENSIONS` to `false`. The local dimension remains required, and the Server still validates returned vectors against it.

## Reuse existing subscription credentials

Make existing Codex or GitHub Copilot credentials available to the Server and its workers. Credential file paths refer to the Server host and the user running the service. A login on the client machine is not transferred to the Server automatically.

The Codex and Copilot examples below use the default service URL and authentication. Leave `POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL` and `POWERCONTEXT_SERVER_INFERENCE_GENERATION_HEADERS` unset. Replace `<model-name>` with a model ID available to that subscription.

### Codex CLI credentials

If Codex CLI is signed in with a subscription account and stores its credentials in `auth.json`, use the `openai-codex:` model prefix:

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="openai-codex:<model-name>"
```

By default, the Server reads `~/.codex/auth.json` in the home directory of the user running the service. If the credentials are in another Codex home directory, specify that directory in the environment:

```bash
export CODEX_HOME="/path/to/existing/codex-home"
```

`CODEX_HOME` names the directory containing `auth.json`. The service does not update the CLI login file. If authentication fails, update the login with the original CLI, then restart the service. See [Pydantic AI's Codex credential configuration](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/docs/models/openai-codex.md).

### GitHub Copilot credentials

If GitHub CLI has an OAuth login for an account with Copilot access, export its token before starting the Server:

```bash
export GITHUB_COPILOT_API_KEY="$(gh auth token)"
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="github-copilot:<model-name>"
```

Choose a Copilot model that supports Chat Completions. Models available only through the Responses API cannot use the `github-copilot:` prefix.

You can also reuse an existing `GITHUB_COPILOT_API_TOKEN` or `COPILOT_GITHUB_TOKEN` environment variable; the Server reads them in that order as fallbacks. The general-purpose `GITHUB_TOKEN` and `GH_TOKEN` variables are not used for Copilot authentication. Run the example's `gh auth token` command before starting the service. The Server does not load CLI configuration or renew tokens automatically. When the token changes, update the service's environment and restart the Server. See [Pydantic AI's Copilot configuration](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/docs/models/github-copilot.md).

These configurations support generation for Memory, Experience, and Profile. Topic Memory and Dream are unavailable with Codex and Copilot. If you have explicitly enabled Topic Memory processing or its automatic schedule, remove that configuration before switching providers. Enabling built-in inference protection prevents startup with these providers.

### Existing ChatGPT Plan Usage credentials

You can reuse an existing ChatGPT Plan Usage authorization through the SDK. The Server does not support loading these credentials through environment variables, so configuring a model name alone does not enable the service.

Use your application's configured Pydantic AI model in the SDK example below. The model needs a ChatGPT Plan Usage authorization; Codex CLI credentials cannot replace it. See [Pydantic AI's existing credential configuration](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/docs/models/openai-chatgpt.md#persisting-credentials).

## Use a generation model through the SDK

If your application already has a configured Pydantic AI model, pass it to a structured generator and create a Memory extraction pipeline:

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

Pass an initialized Pydantic AI `Model` object as `generation_model`; model name strings are not accepted. Your application opens and closes that object.

The generator serializes data using the declared input type and validates the result against the output type. After schema validation, the output must also satisfy Memory's write rules before it can be saved.

Pass `output_check` to enforce a rule between input and output that the output schema cannot express. The callback receives the input and validated output and raises `InvalidInferenceOutputError` when the output breaks the rule. The error's `detail` goes to the model as retry feedback, sharing the `max_requests` budget with schema validation failures. When that budget runs out, `generate()` raises the last `InvalidInferenceOutputError`. Describe only the rule in `detail`; do not include input content.

## Use an embedding model through the SDK

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

Pass `embedding_model` to `open_builtin_contexts()` or `open_builtin_runtime()` with a `SQLiteConfig` to enable the sqlite-vec vector index. Before saving vectors, PowerContext checks their count, order, dimension, and numeric validity, then applies unit normalization as configured.

When supplying an HTTP client for MiniMax embeddings or Jev reranking through the SDK, use an `httpx2.AsyncClient`.

Stored vectors and query vectors must use the same `EmbeddingProfile`. If you change the model, dimension, or normalization, rebuild the vector projections from authoritative Memory revisions.

## Failure behavior

When generation or embedding calls fail, PowerContext returns the corresponding inference error. Timeouts and temporary service failures do not commit partial Memory revisions.

With `mode="auto"` search, a temporary query embedding failure can fall back to FTS if the backend supports it. If you explicitly request vector or hybrid search, the system reports the missing capability without changing the search mode.
