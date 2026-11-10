# OAuth subscriptions

Configure one subscription channel per Gateway process. Clients still choose a model in each request; the account must have access to that model and API. Subscription credentials authenticate the upstream service, separately from Gateway and Core authentication.

## Choose a channel

| `PC_GATEWAY_SUBSCRIPTION` | Login and renewal | Supported routes and limits |
| --- | --- | --- |
| `codex` | Read-only Codex CLI `auth.json`; the CLI renews it. | Responses only. Requests use `store:false` and `stream:true`; Core checks the complete response before release. |
| `copilot` | Gateway device login or an existing GitHub OAuth token in a private file as `oauth_token`; the token is sent directly, following Pydantic AI. | Chat Completions and Responses, subject to model support. Default github.com service only; GHE and Auto model negotiation are unsupported. |
| `grok` | Grok CLI `auth.json`; the Gateway refreshes it under the CLI's `auth.json.lock`. | Chat Completions and Responses at `api.x.ai/v1`. This does not provide Magpie's separate SuperGrok proxy path. |
| `qwen` | Independent Gateway device login with automatic refresh. | Qwen Code Plus/Pro Chat Completions at `api.qwen.ai/compatible-mode/v1`. Legacy free OAuth and API-key plans are separate products. |
| `kimi` | Independent Gateway device login with automatic refresh. | Coding Chat Completions and Responses at the supported `.com` or `.ai` endpoint. Account, region and model access must match. |
| `gemini` | Read-only Gemini CLI `oauth_creds.json`; the CLI owns renewal and onboarding. | Chat ingress converted to non-streaming Code Assist. Context injection is disabled; images and signed thought continuations are unsupported. |
| `claude` | An installed Claude Code CLI signed in to `claude.ai`; the CLI owns renewal. | Fresh text-only Chat turns. Tools, MCP, streaming and persistent sessions are disabled. Requires a confirmed non-enforcing Core deployment. |

## Configure credentials

Build and run commands from `gateway/`. For an existing Codex login:

```bash
PC_GATEWAY_SUBSCRIPTION=codex \
PC_GATEWAY_SUBSCRIPTION_CREDENTIALS=/private/codex/auth.json \
PC_GATEWAY_UPSTREAM_PROFILE=approved-codex \
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

For Claude, no Gateway credential file is needed. Sign in with `claude auth login`, set `PC_GATEWAY_SUBSCRIPTION=claude`, and optionally set `CLAUDE_CLI_EXECUTABLE` to the installed executable. API-key, custom Anthropic endpoint, cloud-backend and explicit OAuth-token environment overrides are rejected. The installed CLI must support the adapter's print-mode flags.

HTTP destinations are limited to each provider's supported HTTPS endpoints, and redirects are disabled. `PC_GATEWAY_UPSTREAM_URL_CHAT` and `PC_GATEWAY_UPSTREAM_URL_RESPONSES` may select an allowed endpoint or disable a route with an empty value. Errors and uncertain delivery do not trigger authentication-layer model replay. An interrupted refresh can require a new login if the remote service rotated a token before its replacement was saved.

## Configure Core protection

Core must approve the final target, model and protocol through `PC_GATEWAY_UPSTREAM_PROFILE`. See [inference protection](../docs/en/docs/operate/inference-protection.md) for profile configuration.

| Channel | Profile requirement |
| --- | --- |
| Chat Completions channels | Use `openai-chat` with the approved model and exact endpoint. |
| Responses channels | Use `openai-responses`. Codex and Kimi require streaming requests; complete SSE results are converted to JSON before release checking. |
| Gemini | Use `google-codeassist-chat` with the actual Code Assist endpoint and model. Core checks the final Code Assist input and converted Chat output. |
| Claude | Use `claude-cli-prompt` with `endpoint: "claude-cli://execute"`. Checks cover the stdin prompt and normalized result. Enforcing or unknown Core protection rejects execution. |

Codex's endpoint rejects `max_output_tokens`. A Core policy requiring that explicit output cap withholds the request with `output_limit_required`; a configured profile limit cannot make the upstream enforce an unsupported parameter.

Gemini maps Chat output limits to `request.generationConfig.maxOutputTokens`. Responses containing thought signatures are withheld; signed tool continuations are unsupported. Plain thought text is retained in the checked result. Claude checks cannot inspect the CLI's internal HTTP calls, retries or token budget, so enforcing Core always withholds this execution path, including after a runtime policy change.

Do not route Core's own generation calls back through a Gateway with automatic recall or capture enabled without a separate recursion boundary. These global features cannot distinguish internal generation from ordinary clients.

## Compatibility and tests

Grok Responses follows Magpie's tool adaptation: namespaced functions are flattened for the upstream and restored before Core release checks and client JSON/SSE delivery. `allowed_tools` restricts the forwarded declarations while preserving `auto` or `required`. Ambiguous flattened names, names longer than 64 bytes, unsupported explicit tool choices and top-level union tool schemas are rejected before sending. A required choice with no supported tools is rejected. Claude CLI supports single-turn text execution rather than Magpie's persistent session and tool bridge; token-limit stops are reported as truncated responses.

Run the controlled workspace tests from `gateway/`:

```bash
cargo test --locked --workspace -- --skip live_
```

The [shared OAuth fixtures](../tests/fixtures/oauth/README.md) and [upstream vector notes](../tests/fixtures/oauth/upstream/README.md) describe credential, protocol and process coverage. Tests use synthetic credentials and local peers; live entitlement, quota and installed CLI compatibility require separate verification. Copyright and license terms are in [LICENSES/oauth.md](../LICENSES/oauth.md).
