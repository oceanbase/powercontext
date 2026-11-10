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

For an Anthropic-compatible gateway that requires separate gateway and upstream credentials, configure its `Authorization` header and supply the upstream key through `ANTHROPIC_API_KEY` or an explicit `X-Api-Key` header. PowerContext sends both headers to the gateway; an explicit `X-Api-Key` takes precedence over the environment key. The gateway must remove its own `Authorization` before forwarding to Anthropic, which expects one credential per request. For a gateway that requires only `Authorization`, leave `ANTHROPIC_API_KEY` and `X-Api-Key` unset. See [Anthropic's gateway authentication guidance](https://platform.claude.com/docs/en/api/migration-2026-10-21#a-client-holds-one-credential).

When `RERANK_MODEL` is unset, LLM reranking uses the generation model and URL. Reranker headers and model settings can extend or override the generation configuration. Header overrides apply only to reranking and leave the selected model and URL unchanged. If you set a separate reranker URL, also specify a reranker model. The reranker uses the generation timeout and request limit unless you override them.

When using the default URL and headers, supply credentials through the environment variables supported by the selected service. See below for reusing subscription credentials.

Set `embedding_model`, `embedding_profile_id`, and `embedding_dimension` together; the Server rejects a configuration with any of them missing. SQLite vector search uses these settings because the index dimension must match the stored vectors. For OpenAI-compatible embeddings, `embedding_dimension` is sent as the `dimensions` request field by default and overrides that field in model settings. If a model has a fixed output dimension and rejects the field, set `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_SEND_DIMENSIONS` to `false`. The local dimension remains required, and the Server still validates returned vectors against it.

## Reuse existing subscription credentials

Configure the subscription on the Server host and make its credentials or signed-in CLI available to workers. Leave `POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL` and `POWERCONTEXT_SERVER_INFERENCE_GENERATION_HEADERS` unset. Replace model placeholders below with a model available to your account.

| Subscription | Model setting | Credential renewal |
| --- | --- | --- |
| Codex CLI | `openai-codex:<model>` | The CLI renews its login; PowerContext reads the file on each request. |
| GitHub Copilot | `github-copilot:<model>` | Export the GitHub OAuth token before startup; update the environment and restart after renewal. |
| Grok CLI OAuth | `grok:<model>` | PowerContext refreshes the shared CLI file under its OS lock. |
| Gemini CLI / Code Assist | `gemini-cli:<model>` | The CLI owns login, onboarding and renewal. |
| Claude Code subscription | `claude-cli:<model>` | The installed CLI owns login and renewal. |
| Qwen Code Plus/Pro | `qwen:<model>` | PowerContext owns a separate device grant and refreshes it automatically. |
| Kimi Code | `kimi:<model>` | PowerContext owns a separate device grant and refreshes it automatically. |
| ChatGPT Plan Usage | SDK only; see below. | The application stores credentials; Pydantic AI refreshes them. |

Server subscription adapters support Memory, Experience and Profile generation. Disable built-in inference protection and remove explicit Topic Memory processing or scheduling before switching to these adapters; Topic Memory and Dream are unsupported.

### Codex CLI credentials

Sign in with Codex CLI on the Server host, then select the model:

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="openai-codex:<model-name>"
```

PowerContext reads `~/.codex/auth.json` by default. To use another login directory:

```bash
export CODEX_HOME="/path/to/existing/codex-home"
```

The Server and workers never modify this file or exchange its refresh token. If inference fails because the login expired, renew it with the original CLI. The next request reads the updated file without a Server restart. See [Pydantic AI's Codex configuration](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/docs/models/openai-codex.md).

### GitHub Copilot credentials

Sign in with GitHub CLI using an account with Copilot access, then export its token before starting the Server:

```bash
export GITHUB_COPILOT_API_KEY="$(gh auth token)"
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="github-copilot:<model-name>"
```

Choose a model that supports Chat Completions. The Server also accepts `GITHUB_COPILOT_API_TOKEN`, then `COPILOT_GITHUB_TOKEN`, as fallbacks. It does not use `GITHUB_TOKEN` or `GH_TOKEN`, load the CLI login automatically, or renew the exported token. Update the environment and restart after token renewal. See [Pydantic AI's Copilot configuration](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/docs/models/github-copilot.md).

### Grok CLI OAuth credentials

After signing in with Grok CLI on the Server host:

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="grok:<model-name>"
export GROK_HOME="/path/to/existing/grok-home"
```

The default file is `~/.grok/auth.json`. `GROK_AUTH_PATH` selects a file and takes precedence over `GROK_HOME`. PowerContext requires the CLI's xAI OAuth entry and sends Responses requests to `https://api.x.ai/v1/responses`. The separate `xai:<model>` prefix uses API-key authentication.

The service user needs write access to the credential directory. PowerContext refreshes tokens within 60 seconds of expiry under `auth.json.lock` and atomically saves the rotated credentials while preserving unrelated fields. Failed renewal leaves the file unchanged. A resource 401 does not trigger refresh or request replay. This API path does not provide Magpie's separate SuperGrok proxy integration.

### Gemini CLI / Code Assist

Complete login and onboarding with Gemini CLI on the Server host, then configure:

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="gemini-cli:<model-name>"
# Optional: override the default CLI credential file.
export GEMINI_CLI_AUTH_PATH="/private/gemini/oauth_creds.json"
```

PowerContext reads `~/.gemini/oauth_creds.json` by default. The original CLI must renew expired credentials; the adapter does not read keychains, encrypted caches, ADC or Gemini API keys. Organization accounts can set `GOOGLE_CLOUD_PROJECT` or `GOOGLE_CLOUD_PROJECT_ID` to an already configured project.

The adapter supports non-streaming Code Assist generation and verifies the account's project and tier before calling the model. It does not perform onboarding or enable credit overage. Account eligibility and quota depend on the subscription. See [Gemini CLI authentication](https://github.com/google-gemini/gemini-cli/blob/main/docs/get-started/authentication.mdx).

### Claude Code subscription through the real CLI

Sign Claude Code CLI into the intended account before starting the service:

```bash
claude auth login
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="claude-cli:sonnet"
# Optional: override the executable found on PATH.
export CLAUDE_CLI_EXECUTABLE="/path/to/claude"
```

Each request starts a fresh print-mode process with tools, MCP and session persistence disabled. Text and prompted JSON are supported; images, streaming and model settings other than `max_tokens` are rejected. The installed CLI must support the adapter's flags. On POSIX, cancellation terminates its process group.

Use the CLI's `claude.ai` login and unset API-key, custom Anthropic endpoint, cloud-backend and explicit OAuth-token overrides. The CLI controls its internal HTTP calls and retries, so this adapter requires built-in inference protection to be disabled. See [Claude CLI flags](https://code.claude.com/docs/en/cli-reference).

### Qwen Code and Kimi Code device authorization

Create a separate PowerContext-owned device grant, then select the model:

```bash
uv run python -m powercontext.builtin.inference.coding_oauth qwen
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="qwen:<subscription-model>"

uv run python -m powercontext.builtin.inference.coding_oauth kimi
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="kimi:<coding-model>"
```

Follow the verification URL and user code printed by the command. Credentials are saved under `~/.config/powercontext/oauth/` as `qwen.json` or `kimi.json`, with mode `0600` on POSIX. To select another path, set `POWERCONTEXT_QWEN_CREDENTIALS_PATH` or `POWERCONTEXT_KIMI_CREDENTIALS_PATH` before both login and service startup. Existing files are not overwritten; reauthorization needs a new path.

Qwen targets the current Code Plus/Pro subscription, separately from legacy free OAuth and API-key plans. Kimi targets `https://api.kimi.com/coding/v1`; the account and region must match. Do not copy a CLI refresh token into either store as an independent login.

Both adapters refresh under an OS file lock within 60 seconds of expiry and save rotated credentials atomically. Failed or cancelled renewal leaves the store unchanged and sends no inference request. Grok, Qwen and Kimi disable automatic SDK retries and authentication-layer model replay.

For SDK integration, `QwenProvider` and `KimiProvider` use Pydantic AI's public `OpenAICompatibleProvider(openai_client=...)` interface. Authlib handles OAuth grants; PowerContext owns the credential-file transaction. The runtime closes clients it creates; SDK callers close clients they supply.

### Existing ChatGPT Plan Usage credentials

ChatGPT Plan Usage is available through the SDK, with an application-owned credential source. The Server has no credential loader for it, and Codex CLI credentials cannot substitute for this authorization.

```python
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTProvider

provider = OpenAIChatGPTProvider(credential_source=credential_source)
generation_model = OpenAIResponsesModel("<model-name>", provider=provider)
```

The source implements async `load()` and `save(credentials)` and stores every `OpenAIChatGPTCredentials` field together. Authorization must include `chatgpt.tokens.use.direct`. Native Pydantic AI refresh can replay once after a 401; applications sharing a store across processes must coordinate refresh ownership. See [persisting ChatGPT credentials](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/docs/models/openai-chatgpt.md#persisting-credentials).

### Verification boundary

[OAuth test fixtures](https://github.com/oceanbase/powercontext/tree/master/tests/fixtures/oauth) cover protocol behavior, credential rotation and cancellation with synthetic data and controlled peers. Live account eligibility, quota and installed CLI compatibility require separate verification. Fixture provenance and license notices are linked from that directory.

### Gateway admission profiles

Use the protocol that matches the executor's final request and released response:

| Protocol | Request and response |
| --- | --- |
| `google-codeassist` | Final Code Assist request and response envelopes, with candidates under `response.candidates`. |
| `google-codeassist-chat` | Final Code Assist request and converted Chat Completion JSON. |
| `claude-cli-prompt` | Chat JSON sent to CLI stdin and its normalized result, with the exact logical target `claude-cli://execute`. |

Code Assist profiles require an approved target/model, a project and an explicit `request.generationConfig.maxOutputTokens`. Opaque image/file parts and thought signatures are withheld. Prepared context must point to user text at `/request/contents/<index>/parts/<index>/text`.

Responses profiles require `allow_streaming=true` for streamed requests. The executor must buffer a complete terminal response before release checking. Codex does not accept `max_output_tokens`, so Core policies requiring that explicit output cap withhold it. Python's protected transport does not parse Responses SSE.

Core always withholds `claude-cli-prompt` in enforcing mode with `cli_http_unobservable`, including after a runtime policy change. Prompt inspection cannot cover the CLI's internal HTTP calls or retries.

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
