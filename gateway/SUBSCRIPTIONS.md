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

Core must approve the final target, model and protocol through `PC_GATEWAY_UPSTREAM_PROFILE`. See [inference protection](../docs/en/docs/operate/inference-protection.md) for profile configuration.

| Channel | Profile requirement |
| --- | --- |
| Chat Completions channels | Use `openai-chat` with the approved model and exact endpoint. |
| Responses channels | Use `openai-responses`. Codex and Kimi require streaming requests; complete SSE results are converted to JSON before release checking. |
| Gemini | Use `google-codeassist-chat` with the actual Code Assist endpoint and model. Core checks the final Code Assist input and converted Chat output. |

Codex's endpoint rejects `max_output_tokens`. The Codex CLI sends none, but other agents (OpenCode, Pi, DeepSeek Harness) set one on every request, so the Gateway leaves the field out of a request to this endpoint and the reply has no output limit. A Core policy requiring that explicit output cap withholds the request with `output_limit_required`; a configured profile limit cannot make the upstream enforce an unsupported parameter.

The Codex endpoint routes its prompt cache by the `session-id` request header; `prompt_cache_key` in the body does not do this there. The Gateway forwards the request's `session-id` to this endpoint, or the body's `prompt_cache_key` as `session-id` when the request has no such header, and sends the same value on a prefix extraction request, so requests of one session reach the same cache. A request with neither gets few cache hits. The header is not forwarded to other upstreams.

Memory extraction over the cached prefix is enabled by default on `codex` when Core is confirmed as non-enforcing at startup; `PC_GATEWAY_PREFIX_EXTRACTION=off` disables it. Each extraction request is sent with the credentials in the Codex login at that moment, counts against that account's usage limits, and has no output limit; a request that asks for one is refused with `output_limit_unsupported`, and Core uses normal extraction. See [prefix extraction settings](../docs/en/docs/operate/deploy-server.md#memory-extraction-over-the-cached-prefix).

Gemini maps Chat output limits to `request.generationConfig.maxOutputTokens`. Responses containing thought signatures are withheld; signed tool continuations are unsupported. Plain thought text is retained in the checked result.

Do not route Core's own generation calls back through a Gateway with automatic recall or capture enabled without a separate recursion boundary. These global features cannot distinguish internal generation from ordinary clients.

## Compatibility and tests

Grok Responses follows Magpie's tool adaptation: namespaced functions are flattened for the upstream and restored before Core release checks and client JSON/SSE delivery. Codex, Copilot, Grok and Code Assist reject duplicate JSON keys before response rewriting. When identity restoration changes the response, Core checks both its original bytes and the final response before release. `allowed_tools` restricts the forwarded declarations while preserving `auto` or `required`. Ambiguous flattened names, names longer than 64 bytes, unsupported explicit tool choices and top-level union tool schemas are rejected before sending. A required choice with no supported tools is rejected.

Run the controlled workspace tests from `gateway/`:

```bash
cargo test --locked --workspace -- --skip live_
```

The [shared OAuth fixtures](../tests/fixtures/oauth/README.md) and [upstream vector notes](../tests/fixtures/oauth/upstream/README.md) describe credential, protocol and process coverage. Tests use synthetic credentials and local peers; live entitlement, quota and installed CLI compatibility require separate verification. Copyright and license terms are in [LICENSES/oauth.md](../LICENSES/oauth.md).
