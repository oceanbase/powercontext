# PowerContext Rust Gateway

The optional Rust Gateway adds PowerContext recall and conversation capture to OpenAI-compatible model traffic. PowerContext Core decides whether a request may be sent and whether its response may be released. See [RFC 2199](../docs/en/rfcs/2199-powercontext-gateway.md) for the design.

## Model traffic

The Gateway serves `/gateway/v1/chat/completions` and `/gateway/v1/responses`. It checks the final request with Core before sending it, then buffers the response and checks it before releasing content. Clients requesting a stream receive buffered replay after approval, rather than live token delivery. Upstream error statuses are preserved.

Requests with uncertain delivery are not automatically resent. When Core is unreachable, the default is to pause model traffic. `PC_GATEWAY_DEGRADE=passthrough` allows marked passthrough only in deployments where Core protection permits it.

The default upstream uses the same API format as the client. [Subscription adapters](SUBSCRIPTIONS.md) provide channel-specific conversions. The Gateway does not serve the Anthropic Messages API.

Blocked calls return HTTP 403. Withheld calls return HTTP 400 unless the executor marks them retryable, in which case they return HTTP 409. Currently, only a nonempty reason list containing exclusively `context-refresh-required` is retryable. Admission expiry also returns HTTP 409. A retry after upstream generation may spend provider quota again.

## Memory

In a combined UDS deployment, recall injection is enabled by default for requests with a conversation ID. The Gateway prepares context once per new user message and reuses it for tool continuations in that turn. Core verifies the injected text and its binding. Empty or failed preparation leaves the request uninjected.

Use `X-PowerContext-Conversation-ID` to identify a conversation. The Gateway also recognizes client session headers from OpenCode, Codex and translated Claude Code traffic. See [context injection settings](../docs/en/docs/operate/deploy-server.md#gateway-context-injection) for header precedence and subagent handling. Standalone deployments require `PC_GATEWAY_INJECTION=on` to enable injection.

Set `PC_GATEWAY_SEDIMENT=on` to capture completed user turns as Sources for later extraction. Capture excludes injected context and provider reasoning ciphertext. Tool continuations remain open until the final response; retried terminal requests with a trusted conversation ID share one Source. Capture failure does not prevent response delivery. Blocked and degraded turns are not captured.

## Run

From `gateway/`, build the executable:

```bash
cargo build --release
```

For a combined deployment, start Core on a private Unix socket and configure an approved upstream profile and an accessible Scope:

```bash
PC_GATEWAY_CORE_UDS=/path/to/core.sock \
PC_GATEWAY_CORE_TOKEN="$POWERCONTEXT_DEPLOYMENT_TOKEN" \
PC_GATEWAY_UPSTREAM_PROFILE=approved-provider \
PC_GATEWAY_DEFAULT_SCOPE=your-scope \
PC_GATEWAY_UPSTREAM_URL_CHAT=https://provider.example/v1/chat/completions \
PC_GATEWAY_UPSTREAM_API_KEY_CHAT="$PROVIDER_API_KEY" \
./target/release/pc-gateway
```

The Rust process owns the public listener and forwards other routes to Core over UDS. For a standalone Gateway, set `PC_GATEWAY_CORE_BASE` to the Core HTTP URL instead; standalone mode does not reverse-proxy Core routes.

`PC_GATEWAY_API_KEY` authenticates the two model routes and is required on a non-loopback listener. Forwarded Core API, MCP and Dashboard requests retain the client's credentials. `PC_GATEWAY_CORE_TOKEN` authenticates Gateway-initiated Core calls. See [deployment authentication](../docs/en/docs/operate/deploy-server.md#gateway-authentication-boundaries) before exposing the service.

Request size, response buffering and concurrency have separate limits. The effective input limit includes Core's advertised capacity and the parser ceiling; increasing a deployment setting cannot bypass either. See [byte capacity](../docs/en/docs/operate/deploy-server.md#gateway-byte-capacity) for limits and failure statuses. Operational JSONL omits request and response bodies; its configuration is in [Gateway evidence](../docs/en/docs/operate/gateway-evidence.md).

## OAuth subscriptions

`PC_GATEWAY_SUBSCRIPTION` selects `codex`, `copilot`, `grok`, `qwen`, `kimi`, `gemini` or `claude`. HTTP channels require an explicit credential path in `PC_GATEWAY_SUBSCRIPTION_CREDENTIALS`. Copilot, Qwen and Kimi also support `pc-gateway login <provider> <new-file>`.

See [OAuth subscriptions](SUBSCRIPTIONS.md) for login steps, credential renewal, supported routes and protection limits. Gemini disables context injection. Claude CLI requires a confirmed non-enforcing Core deployment. Codex can be withheld when Core requires an output token cap its endpoint cannot enforce.

## Layout

| Crate | Responsibility |
| --- | --- |
| `pc-gateway-app` | Listener, ingress, reverse proxy and middleware |
| `pc-gateway-core` | Execution and Core check, prepare, capture and capability calls |
| `pc-gateway-auth` | Provider authentication and credential storage |
| `pc-gateway-types` | Core contract DTOs |
| `pc-gateway-protocol` | Request rendering, provider conversions and SSE framing |
| `pc-gateway-transport` | HTTP and CLI execution, buffering and bounded retry |
| `pc-gateway-evidence` | Call, attempt, usage and delivery records |
| `pc-gateway-embedded` | Placeholder for an embedded executor |

## Tests

Run controlled tests from `gateway/`:

```bash
cargo test --locked --workspace -- --skip live_
```

These tests use local HTTP peers, synthetic credentials and subprocess fixtures. They cover admission, release, protocol conversion, credential rotation and cancellation. [OAuth fixtures](../tests/fixtures/oauth/README.md) and [protocol fixture notes](crates/pc-gateway-app/tests/fixtures/republic/README.md) record their sources and scope.

For live subscription acceptance, run from the repository root with an existing Codex CLI login and GitHub CLI authentication:

```bash
cargo build --locked --manifest-path gateway/Cargo.toml -p pc-gateway-app
uv run python tests/e2e/oauth_subscription_acceptance.py
```

The harness uses GPT-6 Luna for Codex and Claude Haiku 5.5 for Copilot Chat, spending subscription usage without starting a login flow. It creates disposable authenticated Core and Gateway processes, temporary SQLite storage and a private UDS. Checks cover native generation and artifact readback, recall injection, conversation capture, API authentication and generation through the Gateway with automatic recall and capture disabled. Inference protection is disabled for native subscription adapters. Select one channel with `--channel codex` or `--channel copilot`; results are written to `.powercontext/oauth-live/report.json`. Use `--routes-only` to check JSON and SSE consumption through the OpenAI SDK for supported Chat and Responses routes; results are written to `.powercontext/oauth-live/routes-report.json`.

The four `live_context_check` tests require a separate Core configured with enforcing protection and a `coding` profile. Their current helper targets `127.0.0.1:8000` without a Bearer token; use an isolated test deployment rather than an authenticated personal service.

`make gateway-tck`, run from the repository root, checks pinned upstream SDKs and Open Responses behavior against a direct baseline and the Gateway. Its prerequisites and coverage are in the [TCK README](tck/README.md). Controlled tests do not establish live provider, account or CLI compatibility.
