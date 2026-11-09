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

Configure the subscription available on the Server host, and make its credentials or signed-in CLI available to workers. Credential file paths refer to the Server host and the user running the service. A login on the client machine is not transferred to the Server automatically.

The Codex, Copilot, and Grok examples below use the default service URL and authentication. Leave `POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL` and `POWERCONTEXT_SERVER_INFERENCE_GENERATION_HEADERS` unset. Replace `<model-name>` with a model ID available to that subscription.

The supported execution and credential boundaries are:

| Subscription | Server | Python SDK | Credential lifecycle |
| --- | --- | --- | --- |
| Codex CLI | `openai-codex:<model>` | Native Pydantic AI model with PowerContext's read-only auth | CLI owns refresh; every request reloads `auth.json` |
| GitHub Copilot | `github-copilot:<model>` | Native `GitHubCopilotProvider` / `GitHubCopilotOAuthFlow` | Server uses a startup environment token; login and token replacement remain external |
| ChatGPT Plan Usage | No credential loader | Native `OpenAIChatGPTProvider` / `OpenAIChatGPTOAuthFlow` | Application owns credential storage; native provider refreshes and saves rotated credentials |
| Grok CLI OAuth | `grok:<model>` | `GrokCliAuth` on an async HTTPX client and a Pydantic AI Responses model | Reloads the CLI file; refreshes under `auth.json.lock` and replaces the file atomically |
| Claude Code subscription | `claude-cli:<model>` | `ClaudeCliModel` | Genuine signed-in CLI, fresh text-only print process; CLI owns renewal and internal retries |
| Gemini CLI / Code Assist | `gemini-cli:<model>` | Native `GoogleModel` with Code Assist transport | Read-only CLI OAuth cache, project/tier discovery; CLI owns renewal and onboarding |
| Qwen Code Plus/Pro | `qwen:<model>` | Native OpenAI Chat model and `CodingOAuthAuth` | PowerContext-owned device grant, locked refresh and atomic persistence |
| Kimi Code | `kimi:<model>` | Native OpenAI Chat model and `CodingOAuthAuth` | PowerContext-owned device grant, locked refresh and atomic persistence |

`xai:<model>` is Pydantic AI's native xAI SDK/API-key provider, not Grok CLI OAuth. It requires the separate Pydantic AI `xai` extra. These integrations do not provide an interactive login UI.

### Codex CLI credentials

If Codex CLI is signed in with a subscription account and stores its credentials in `auth.json`, use the `openai-codex:` model prefix:

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="openai-codex:<model-name>"
```

By default, the Server reads `~/.codex/auth.json` in the home directory of the user running the service. If the credentials are in another Codex home directory, specify that directory in the environment:

```bash
export CODEX_HOME="/path/to/existing/codex-home"
```

`CODEX_HOME` names the directory containing `auth.json`. The CLI owns credential renewal. The Server and workers read the complete login file on every request; they never exchange its refresh token or update the file. This avoids competing with the CLI or each other for a single-use refresh token. If the access token expires or is rejected, inference fails until the original CLI renews the login. The next request picks up that update without a service restart. Unattended renewal is not provided by this integration. See [Pydantic AI's Codex credential configuration](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/docs/models/openai-codex.md).

### GitHub Copilot credentials

If GitHub CLI has an OAuth login for an account with Copilot access, export its token before starting the Server:

```bash
export GITHUB_COPILOT_API_KEY="$(gh auth token)"
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="github-copilot:<model-name>"
```

Choose a Copilot model that supports Chat Completions. Models available only through the Responses API cannot use the `github-copilot:` prefix.

You can also reuse an existing `GITHUB_COPILOT_API_TOKEN` or `COPILOT_GITHUB_TOKEN` environment variable; the Server reads them in that order as fallbacks. The general-purpose `GITHUB_TOKEN` and `GH_TOKEN` variables are not used for Copilot authentication. Run the example's `gh auth token` command before starting the service. The Server does not load CLI configuration or renew tokens automatically. When the token changes, update the service's environment and restart the Server. See [Pydantic AI's Copilot configuration](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/docs/models/github-copilot.md).

These subscription configurations support generation for Memory, Experience, and Profile. Topic Memory and Dream are unavailable with these subscription adapters. If you have explicitly enabled Topic Memory processing or its automatic schedule, remove that configuration before switching providers. Enabling built-in inference protection prevents startup with these providers.

### Grok CLI OAuth credentials

After signing in with the official Grok CLI, configure:

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="grok:<model-name>"
export GROK_HOME="/path/to/existing/grok-home"
```

The default file is `~/.grok/auth.json`. `GROK_HOME` selects its directory; `GROK_AUTH_PATH` selects a file and takes precedence. Every request reads the current xAI OAuth entry, including its issuer, client ID, expiry, and optional principal fields. An API-key entry is not accepted as a CLI OAuth login.

The integration follows [republic's Grok OAuth implementation](https://github.com/bubbuild/republic/blob/main/src/republic/providers/grok.py): Responses requests go to `https://api.x.ai/v1/responses`, while Authlib handles the public-client refresh grant at `https://auth.x.ai/oauth2/token`. Tokens expiring within 60 seconds are renewed under the CLI's `auth.json.lock` OS lock. The file is read again after acquiring the lock, and the complete rotated credential set is saved with a same-directory atomic replacement. Other fields and provider entries are preserved; an omitted replacement refresh token retains the previous token. The service user needs write access to the credential directory. On POSIX, the replacement file is private (`0600`).

Failed refreshes, and cancellation while awaiting the refresh response, do not write credentials or send inference. Resource 401 responses do not trigger a refresh or replay. This provider disables OpenAI SDK retries so failures cannot silently repeat a token exchange or potentially billable model request. Any explicit application retry remains the application's responsibility. Custom base URLs, static generation headers, and built-in inference protection are unsupported for this provider.

This is the republic HTTP OAuth path. [Magpie's current Grok implementation](https://github.com/yetone/magpie/blob/main/internal/provider/grok.go) instead delegates to a plugin using `cli-chat-proxy.grok.com` and CLI-driven renewal. The two paths are not asserted to be interchangeable. The Grok adapter does not implement that plugin or the separate Claude Code session/tool bridge.

### Gemini CLI / Code Assist

Sign in and complete onboarding with the official Gemini CLI, then configure:

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="gemini-cli:<model-name>"
# Optional: an existing unencrypted CLI credential file on this host.
export GEMINI_CLI_AUTH_PATH="/private/gemini/oauth_creds.json"
```

The default file is `~/.gemini/oauth_creds.json`. The adapter reads `access_token` and `expiry_date` for every request without spending the CLI refresh token or changing the file. Expired credentials require renewal by the original CLI. It does not read keychains, encrypted caches, generic ADC, or Gemini API keys. Organization accounts can supply `GOOGLE_CLOUD_PROJECT` or `GOOGLE_CLOUD_PROJECT_ID`; account and project eligibility still come from `loadCodeAssist`.

The native Google SDK/Pydantic AI model produces Gemini content. An HTTP transport wraps the request for `https://cloudcode-pa.googleapis.com/v1internal:generateContent`, after resolving an already-onboarded project and tier, and unwraps `response.candidates`. Ineligible or uninitialized accounts fail before inference. Google SDK retries are disabled; this adapter supports non-streaming generation only. It neither performs onboarding nor opts into credit overage. Google AI Pro/Ultra, free individual Code Assist, and organization licenses have different quotas and eligibility; a valid Google OAuth token alone is not proof of paid entitlement. See [official CLI authentication](https://github.com/google-gemini/gemini-cli/blob/main/docs/get-started/authentication.mdx), [its Code Assist converter](https://github.com/google-gemini/gemini-cli/blob/main/packages/core/src/code_assist/converter.ts), and [Goose's OAuth adapter](https://github.com/block/goose/blob/main/crates/goose/src/providers/gemini_oauth.rs).

### Claude Code subscription through the real CLI

Sign the official Claude Code CLI into the intended Claude account before starting the service:

```bash
claude auth login
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="claude-cli:sonnet"
# Optional: the installed executable on this host.
export CLAUDE_CLI_EXECUTABLE="/path/to/claude"
```

Each model request checks `claude auth status`, requires `authMethod=claude.ai`, and runs genuine `claude --print --output-format json`. It sends a text transcript through stdin in a temporary working directory, disables built-in tools and MCP tools, uses safe mode, disables session persistence, and limits the run to one turn. Only text and prompted JSON are supported; caller tools, images, streaming, and model settings other than `max_tokens` are rejected. `max_tokens` is passed as the official `CLAUDE_CODE_MAX_OUTPUT_TOKENS` environment setting. The installed CLI must support these flags. On POSIX, cancellation terminates its process group and reaps the child before returning.

API-key, custom Anthropic endpoint, cloud-backend, and explicit OAuth-token environment overrides are rejected to avoid selecting a different authentication/billing path. CLI renewal, internal HTTP calls/retries, model fallback, and provider-side quota decisions remain CLI-owned. Python requires built-in inference protection to be disabled and rejects use requiring SDK retries to be disabled; it cannot prove inspection of each internal HTTP request. Topic Memory and Dream remain unavailable. This is a fresh single-turn execution contract, not Magpie's persistent session/MCP/rewind bridge. See [official print-mode flags](https://code.claude.com/docs/en/cli-reference), [subscription usage](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan), and [Goose's genuine CLI provider](https://github.com/block/goose/blob/main/crates/goose/src/providers/claude_code.rs).

### Qwen Code and Kimi Code device authorization

Acquire a fresh PowerContext-owned device grant, then configure the corresponding model:

```bash
uv run python -m powercontext.builtin.inference.coding_oauth qwen
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="qwen:<subscription-model>"

uv run python -m powercontext.builtin.inference.coding_oauth kimi
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL="kimi:<coding-model>"
```

The command prints the verification URL and user code, polls the official token endpoint, and saves a private (`0600` on POSIX) credential set. Default files are `~/.config/powercontext/oauth/qwen.json` and `kimi.json`; set `POWERCONTEXT_QWEN_CREDENTIALS_PATH` or `POWERCONTEXT_KIMI_CREDENTIALS_PATH` before login and service startup to select another file. Existing files are not overwritten: use a new path for reauthorization. These files are separate grants owned by PowerContext, not copied CLI refresh tokens.

Qwen uses the current Plus/Pro device flow at `auth.qwen.ai/api/oauth/device/code`, client `qwen-code-cli`, scopes `model:invoke profile:read`, and Chat Completions at `https://api.qwen.ai/compatible-mode/v1/chat/completions`. The retired free OAuth endpoint and API-key Coding/Token Plans are separate products. Kimi uses device authorization at `auth.kimi.com/api/oauth/device_authorization`, client `17e5f671-d194-4dfb-9706-5516cb48c098`, and Chat Completions at `https://api.kimi.com/coding/v1/chat/completions`. This adapter targets that region; it does not infer region or accept arbitrary API hosts. Kimi's `kimi_cli` / `0.1.0` platform/version headers follow Goose's compatibility implementation; the User-Agent identifies PowerContext and the device UUID belongs to its new grant. They are protocol compatibility fields, not proof of subscription acceptance.

Both adapters reload their owned files, refresh within 60 seconds of expiry through Authlib under an OS file lock, reread after locking, and persist the full replacement atomically. Refresh rejection, malformed token responses, and cancellation while awaiting renewal do not write the store or send inference. No automatic model replay or SDK retry occurs. Custom base URLs, static generation headers, built-in inference protection, Topic Memory, and Dream are unsupported. See [Qwen's subscription OAuth client](https://github.com/QwenLM/qwen-code/blob/main/packages/core/src/qwen/subscription-oauth-client.ts), [Kimi's current OAuth package](https://github.com/MoonshotAI/kimi-code/tree/main/packages/oauth), and [Goose's Kimi provider and tests](https://github.com/block/goose/blob/main/crates/goose/src/providers/kimicode.rs).

### Existing ChatGPT Plan Usage credentials

You can reuse an existing ChatGPT Plan Usage authorization through the SDK. The Server does not support loading these credentials through environment variables, so configuring a model name alone does not enable the service.

Use your application's configured Pydantic AI model in the SDK example below. The model needs a ChatGPT Plan Usage authorization; Codex CLI credentials cannot replace it. See [Pydantic AI's existing credential configuration](https://github.com/pydantic/pydantic-ai/blob/v2.64.0/docs/models/openai-chatgpt.md#persisting-credentials).

Construct the native model with an application-owned credential source, then pass it to the generator below:

```python
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai_chatgpt import OpenAIChatGPTProvider

provider = OpenAIChatGPTProvider(credential_source=credential_source)
generation_model = OpenAIResponsesModel("<model-name>", provider=provider)
```

`credential_source` implements async `load()` and `save(credentials)`, storing every `OpenAIChatGPTCredentials` field together. The authorization must grant `chatgpt.tokens.use.direct`. Native refresh and single-flight belong to Pydantic AI; its load-before-refresh behavior does not provide mutual exclusion across processes. The application must choose a storage/ownership design appropriate to its deployment. Unlike the Codex and Grok adapters, the native ChatGPT provider can refresh and replay once after a 401. Supplying only `credentials=` keeps rotated credentials in memory; supplying `openai_client=` opts out of native credential injection and refresh.

### Verification boundary

The shared [OAuth fixtures](https://github.com/oceanbase/powercontext/tree/master/tests/fixtures/oauth) describe deterministic wire and storage behavior. The Python runner uses synthetic credentials, temporary files, and controlled HTTP transports. Additional Grok tests use spawned processes, a real shared file/OS lock, and a local HTTP server to check one refresh across workers and reuse of persisted rotation after restart. These are local protocol and lifecycle checks, not live subscription acceptance. They do not establish that a deployed CLI or subscription accepts this endpoint, model, client identity, or billing path. CLI interoperability and non-POSIX lock behavior require corresponding environment acceptance.

### Gateway admission profiles

The Core checker supports final Code Assist request JSON with `protocol="google-codeassist"` for a native `response.candidates` release, or `protocol="google-codeassist-chat"` for a final Chat Completion release. Select the format the executor actually releases; approval of an intermediate representation does not authorize a later conversion. Requests require an approved exact target/model, a project, inspectable text/function content, and an explicit bounded `request.generationConfig.maxOutputTokens`. Opaque image/file parts and thought signatures are withheld. Prepared evidence uses the exact `/request/contents/<index>/parts/<index>/text` user-part location; system/model text cannot gain evidence authority.

Responses `stream:true` requests require `allow_streaming=true` and retain the output/context budget checks and `store:false` requirement. The executor must buffer and normalize a complete terminal response before JSON release checking; this does not add Responses SSE parsing to Python's protected transport. Codex's endpoint does not accept `max_output_tokens`, so a deployment requiring that explicit enforcing budget still withholds Codex. A profile's configured limit does not make the upstream execute an unsupported limit.

For genuine CLI execution, Core profiles use `protocol: "claude-cli-prompt"` and the unique logical destination `endpoint: "claude-cli://execute"`. Advisory checks inspect the final Chat JSON passed on stdin and the normalized result. Enforcing checks always withhold with `cli_http_unobservable`, including after a runtime policy change: a prompt approval cannot authorize the CLI's hidden HTTP requests or retries. Other CLI destinations and pairing this destination with an HTTP protocol are invalid.

Upstream test vectors live in `tests/fixtures/oauth/upstream/`. Its README records exact commits, test paths, original input/expected assertions, adaptations and attribution for republic, Goose, Qwen Code and Kimi Code; full license notices are consolidated in `LICENSES/oauth.md`. These are migrated source-test vectors, not recorded live subscription traffic. The original 27-case OAuth contract and process/locking/cancellation/Core regressions are separate project-authored tests. Controlled tests use synthetic credentials and executable stubs; they do not establish live account eligibility or quota behavior.

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
