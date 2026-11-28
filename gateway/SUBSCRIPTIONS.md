# OAuth subscriptions

Configure one subscription channel per Gateway process. The client model must match the selected Core profile, and the account must have access to that model and API. Subscription credentials authenticate the upstream service, separately from Gateway and Core authentication.

## Choose a channel

| `PC_GATEWAY_SUBSCRIPTION` | Login and renewal | Supported routes and limits |
| --- | --- | --- |
| `codex` | Read-only Codex CLI `auth.json`; the CLI renews it. | Responses only. Requests use `store:false` and `stream:true`; Core checks the complete response before release. |
| `copilot` | Gateway device login or an existing GitHub OAuth token in a private file as `oauth_token`; the token is sent directly, following Pydantic AI. | Chat Completions and Responses, subject to model support. Default github.com service only; GHE and Auto model negotiation are unsupported. |
| `grok` | Grok CLI `auth.json`; the Gateway refreshes it under the CLI's `auth.json.lock`. | Chat Completions and Responses at `api.x.ai/v1`. This does not provide Magpie's separate SuperGrok proxy path. |
| `qwen` | Independent Gateway device login with automatic refresh. | Qwen Code Plus/Pro Chat Completions at `api.qwen.ai/compatible-mode/v1`. Legacy free OAuth and API-key plans are separate products. |
| `kimi` | Independent Gateway device login with automatic refresh. | Coding Chat Completions and Responses at the supported `.com` or `.ai` endpoint. Account, region and model access must match. |
| `gemini` | Read-only Gemini CLI `oauth_creds.json`; the CLI owns renewal and onboarding. | Chat ingress converted to non-streaming Code Assist. Context injection is disabled; images and signed thought continuations are unsupported. |

## Configure credentials

Build and run commands from `gateway/`. For an existing Codex login:

```bash
PC_GATEWAY_SUBSCRIPTION=codex \
PC_GATEWAY_SUBSCRIPTION_CREDENTIALS=/private/codex/auth.json \
PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES=approved-codex \
PC_GATEWAY_API_KEY="$GATEWAY_CLIENT_TOKEN" \
PC_GATEWAY_CORE_TOKEN="$POWERCONTEXT_DEPLOYMENT_TOKEN" \
./target/release/pc-gateway
```

Keep the Core connection, Scope and authentication settings from the [deployment guide](../docs/en/docs/operate/deploy-server.md#gateway-authentication-boundaries).

For a new Qwen login, create a private directory and follow the device authorization prompt:

```bash
install -d -m 700 /private/powercontext/oauth
./target/release/pc-gateway login qwen /private/powercontext/oauth/qwen.json

PC_GATEWAY_SUBSCRIPTION=qwen \
PC_GATEWAY_SUBSCRIPTION_CREDENTIALS=/private/powercontext/oauth/qwen.json \
PC_GATEWAY_UPSTREAM_PROFILE=approved-qwen \
./target/release/pc-gateway
```

The same login command supports `copilot` and `kimi`. It creates a new `0600` file and refuses to overwrite an existing file. For reauthorization, choose a new path and update the service configuration. Keep credentials on a local filesystem that supports OS locks and atomic rename, writable only by the service user.

All HTTP channels require `PC_GATEWAY_SUBSCRIPTION_CREDENTIALS`; the Gateway does not search home directories or keychains. Qwen and Kimi use Gateway-owned grants, so their CLI refresh tokens cannot be copied into these stores as independent logins. Codex and Gemini files must remain usable by their original CLIs for renewal.

For Gemini, set the credential path to the existing `oauth_creds.json`. The Gateway discovers an onboarded project and tier through `loadCodeAssist`; `GOOGLE_CLOUD_PROJECT` or `GOOGLE_CLOUD_PROJECT_ID` can select an already configured project. It does not perform onboarding or enable credit overage. Google OAuth login alone does not establish Code Assist entitlement.

Claude CLI cannot be used with Core-bound egress: its internal HTTP sends and retries are not observable by the Gateway. Selecting `PC_GATEWAY_SUBSCRIPTION=claude` fails at startup.

HTTP subscription credentials are acquired for the provider's default endpoint, and Core must approve that exact endpoint before each send; redirects are disabled. `PC_GATEWAY_UPSTREAM_URL*` variables cause startup failure. Errors and uncertain delivery do not trigger authentication-layer model replay. An interrupted refresh can require a new login if the remote service rotated a token before its replacement was saved.

## Configure Core protection

Core must approve the final target, model and protocol through the profile selected for the route: `PC_GATEWAY_UPSTREAM_PROFILE` for Chat Completions and `PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES` for Responses. See [inference protection](../docs/en/docs/operate/inference-protection.md) for profile configuration.

| Channel | Profile requirement |
| --- | --- |
| Chat Completions channels | Use `openai-chat` with the approved model and exact endpoint. |
| Responses channels | Use `openai-responses`. Codex and Kimi require streaming requests; complete SSE results are converted to JSON before release checking. |
| Gemini | Use `google-codeassist-chat` with the actual Code Assist endpoint and model. Core checks the final Code Assist input and converted Chat output. |

Codex's endpoint rejects `max_output_tokens`. The Codex CLI sends none, but other agents (OpenCode, Pi, DeepSeek Harness) set one on every request, so the Gateway leaves the field out of a request to this endpoint and the reply has no output limit. A Core policy requiring that explicit output cap withholds the request with `output_limit_required`; a configured profile limit cannot make the upstream enforce an unsupported parameter.

The Codex subscription adapter also omits `temperature`, `top_p`, and `prompt_cache_options` before Core checks the final request. The endpoint uses its own sampling and cache defaults. For multi-turn sessions, use `store: false` and send the conversation history, including tool calls and their results, in `input`. The endpoint rejects `previous_response_id`, which refers to stored response state.

The Codex endpoint routes its prompt cache by the `session-id` request header; `prompt_cache_key` in the body does not do this there. The Gateway forwards the request's `session-id` to this endpoint, or the body's `prompt_cache_key` as `session-id` when the request has no such header, and sends the same value on a prefix extraction request, so requests of one session reach the same cache. A request with neither gets few cache hits. The header is not forwarded to other upstreams.

Memory extraction over the cached prefix is enabled by default on `codex` when Core is confirmed as non-enforcing at startup; `PC_GATEWAY_PREFIX_EXTRACTION=off` disables it. Each extraction request is sent with the credentials in the Codex login at that moment, counts against that account's usage limits, and has no output limit; a request that asks for one is refused with `output_limit_unsupported`, and Core uses normal extraction. See [prefix extraction settings](../docs/en/docs/operate/deploy-server.md#memory-extraction-over-the-cached-prefix).

Gemini maps Chat output limits to `request.generationConfig.maxOutputTokens`. Responses containing thought signatures are withheld; signed tool continuations are unsupported. Plain thought text is retained in the checked result.

Do not route Core's own generation calls back through a Gateway with automatic recall or capture enabled without a separate recursion boundary. These global features cannot distinguish internal generation from ordinary clients.

## Compatibility and tests

### Codex subscription clients

Point the client provider at the Gateway's `/gateway/v1` base URL and select the OpenAI Responses protocol. Give the client the Gateway API key and configure the Codex subscription credential file on the Gateway. The client can declare an output limit for its own budgeting, but the subscription endpoint cannot enforce that limit.

The following clients completed a streamed text turn, a read-tool call and result, and a later turn in the same session against a real Codex subscription with `gpt-5.6-sol`. The Gateway used UDS to reach Core, with inference protection disabled and recall injection, capture, steering, and prefix extraction off.

| Client | Provider configuration exercised |
| --- | --- |
| DeepSeek Harness `@deepseek-ai/dsh@0.2.2-rc.5` (pi-ai `1.6.0`) | Headless profile, custom `llm-pi-ai` provider with `api: openai-responses`, an `apiKeyEnv` reference to the Gateway key, and the custom provider and model selected by `agent-default-model`. Resume with `--session-id`. |
| Pi `@mariozechner/pi-coding-agent@0.73.1` (pi-ai `0.73.1`) | Custom provider in `models.json` with `api: openai-responses`, the Gateway key, and the model ID. Resume with `--continue`. |
| OpenCode `opencode-ai@1.19.6` | Custom `@ai-sdk/openai` provider with `options.baseURL` set to the Gateway and model `options.store: false`. Resume with `--continue`. |

All three sent `stream: true`, `store: false`, and `max_output_tokens`, and carried tool continuations in `input`. Their normal requests omitted `temperature`, `top_p`, `prompt_cache_options`, and `previous_response_id`. A Pi extension that set the three sampling and cache options reproduced the upstream HTTP 400 for `temperature` and completed all three turns with the Codex adaptation. Separate single-field probes confirmed HTTP 400 for each of the other two options and for `previous_response_id`. In a separate check, a real enforcing Core with a target profile requiring an explicit output limit returned `output_limit_required` for a request carrying `max_output_tokens` at ingress.

DeepSeek Harness loads `$DSH_HOME/.credentials.yaml` during startup. The `0.1.0-rc.6` credential loader expects a flat mapping of reference names to strings. A file with a numeric `version: 1` and nested `refs` fails at startup with “the value for "version" … must be a string”, before any Gateway request. Use a compatible build for that file format, as in the `0.2.2-rc.5` run, or use a separate `DSH_HOME` if an older build is required. Keep the original credential file and profile, including any managed credential records. Add the Gateway provider alongside existing entries in `llm-pi-ai.config.providers` and point its `apiKeyEnv` at a dedicated environment variable.

### Controlled protocol coverage

Grok Responses follows Magpie's tool adaptation: namespaced functions are flattened for the upstream and restored before Core release checks and client JSON/SSE delivery. Codex, Copilot, Grok and Code Assist reject duplicate JSON keys before response rewriting. When identity restoration changes the response, Core checks both its original bytes and the final response before release. `allowed_tools` restricts the forwarded declarations while preserving `auto` or `required`. Ambiguous flattened names, names longer than 64 bytes, unsupported explicit tool choices and top-level union tool schemas are rejected before sending. A required choice with no supported tools is rejected. The Gateway rejects the Claude CLI subscription at startup because its internal sends cannot be bound to a Core-approved destination.

Run the controlled workspace tests from `gateway/`:

```bash
cargo test --locked --workspace -- --skip live_
```

The [shared OAuth fixtures](../tests/fixtures/oauth/README.md) and [upstream vector notes](../tests/fixtures/oauth/upstream/README.md) describe credential, protocol and process coverage. Tests use synthetic credentials and local peers; live entitlement, quota and installed CLI compatibility require separate verification. Copyright and license terms are in [LICENSES/oauth.md](../LICENSES/oauth.md).
