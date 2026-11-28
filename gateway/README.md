# PowerContext Rust Gateway

The optional Rust Gateway adds PowerContext recall and conversation capture to OpenAI-compatible model traffic. PowerContext Core decides whether a request may be sent and whether its response may be released. See [RFC 2199](../docs/en/rfcs/2199-powercontext-gateway.md) for the design.

The optional Gateway requires Core-issued target admission for every model send. Core must run with access control enforced, including for combined UDS deployments and non-enforcing inference privacy modes. See [Gateway deployment](../docs/en/docs/operate/deploy-server.md) and [installation](../docs/en/docs/operate/install-gateway.md) for the current authentication and target requirements.

## Model traffic

The Gateway serves `/gateway/v1/chat/completions` and `/gateway/v1/responses`. It checks the final request with Core before sending it, then buffers the response and checks it before releasing content. Clients requesting a stream receive buffered replay after approval, rather than live token delivery. Upstream error statuses are preserved.

Requests with uncertain delivery are not automatically resent. When Core is unreachable, model traffic pauses; model requests are never sent without Core-issued target admission.

The default upstream uses the same API format as the client. [Subscription adapters](SUBSCRIPTIONS.md) provide channel-specific conversions. The Gateway does not serve the Anthropic Messages API.

Blocked calls return HTTP 403. Withheld calls return HTTP 400 unless the executor marks them retryable, in which case they return HTTP 409. Currently, only a nonempty reason list containing exclusively `context-refresh-required` is retryable. Admission expiry also returns HTTP 409. A retry after upstream generation may spend provider quota again.

## Memory

In a combined UDS deployment, recall injection is enabled by default. The Gateway prepares context on a user turn and reuses that turn's prepared block for the requests that follow, without querying again. A tool continuation without a conversation ID has nothing to reuse and is sent without recall. Core verifies the injected text and its binding. Empty or failed preparation leaves the request uninjected.

Use `X-PowerContext-Conversation-ID` to identify a conversation. The Gateway also recognizes client session headers from OpenCode, Codex and translated Claude Code traffic. A host that sends non-conversation model requests, such as a compaction or branch summary, can mark one with `X-PowerContext-Turn: internal`; the gateway then skips preparation, injection, replay, steering and capture for that request while still running the before-send check, and does not forward the header upstream. Only a single, case-insensitive `internal` is accepted; an empty, repeated or other value is rejected with `400 invalid_turn_header`. Without a valid header, eligible full-history requests get a process-local identity for preparation replay from their opening messages. See [context injection settings](../docs/en/docs/operate/deploy-server.md#gateway-context-injection) for header precedence and fallback behavior. Standalone deployments require `PC_GATEWAY_INJECTION=on` to enable injection.

The Gateway classifies each request tail as `user_turn`, `tool_continuation`, `assistant_only` or `empty`, and records it in call evidence and debug logs. Responses items are classified by their own role and type rather than the verbatim carrier. Every kind other than `user_turn` reuses the turn's prepared block when a replay entry exists and otherwise skips preparation and replay.

Send `X-PowerContext-Scope-ID` on Chat or Responses requests to select the Scope for recall, both Core checks, and capture. Requests without the header use `PC_GATEWAY_DEFAULT_SCOPE`. Core applies its access rules using the Gateway's Core credentials. See [request Scope settings](../docs/en/docs/operate/deploy-server.md#gateway-request-scope).

Set `PC_GATEWAY_SEDIMENT=on` to capture completed user turns as Sources for later extraction. Capture excludes injected context and provider reasoning ciphertext. Tool continuations remain open until the final response. Long turns are split into Sources, and Core stores all parts in one batch. With a trusted conversation ID, captures share a turn digest and include a digest of the ordered normalized Source parts in their Source IDs. Distinct terminal records are stored independently; identical normalized parts are idempotent even when provider response IDs or usage change. Existing Sources remain unchanged, and a repeated capture under the former turn-only ID creates a new Source. Capture requires a Core that supports batch capture. Capture failure does not prevent response delivery. Blocked turns are not captured.

Prefix extraction is enabled by default for supported upstreams on both model routes. Chat extraction requires a captured non-streaming turn with a single completed text reply and a conversation ID. `PC_GATEWAY_PREFIX_EXTRACTION=off` disables prefix extraction. With a subscription this applies to `codex` only, on a Core confirmed as non-enforcing; the extraction requests use the subscription account. Traffic of the other subscriptions uses normal extraction. Capture remains opt-in. Core also needs its gateway callback URL and generation model configured. With that URL configured, the Server defaults to `one_pass` unless a processing mode was explicitly supplied; `POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_PROCESSING_MODE=per_candidate` retains the previous mode. Both processing modes can reuse an eligible agent prefix when estimated cheaper, with normal extraction as the fallback. See [prefix extraction settings](../docs/en/docs/operate/deploy-server.md#memory-extraction-over-the-cached-prefix).
When Core allows a Chat Completions stream, capture assembles the primary choice's text from its deltas and requires a terminal finish reason. The client receives the original SSE body.

For a supported Responses upstream, prefix extraction is enabled by default; `PC_GATEWAY_PREFIX_EXTRACTION=off` disables it. With a subscription this applies to `codex` only, on a Core confirmed as non-enforcing; the extraction requests use the subscription account. Traffic of the other subscriptions uses normal extraction. Capture remains opt-in. Core also needs its gateway callback URL and generation model configured. With that URL configured, the Server defaults to `one_pass` unless a processing mode was explicitly supplied; `POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_PROCESSING_MODE=per_candidate` retains the previous mode. Both processing modes can reuse an eligible agent prefix when estimated cheaper, with normal extraction as the fallback. See [prefix extraction settings](../docs/en/docs/operate/deploy-server.md#memory-extraction-over-the-cached-prefix).

## Steering

Chat and Responses requests include the [built-in cost guidance](crates/pc-gateway-app/src/default_steering.txt) by default, except requests marked `X-PowerContext-Turn: internal`. Set `PC_GATEWAY_STEERING_FILE` to replace it, or `PC_GATEWAY_STEERING=off` to disable steering even when a file is configured. On Chat, the gateway appends guidance to the first system message or inserts a system message before the first user message. Both `PC_GATEWAY_STEERING` and `PC_GATEWAY_PREFIX_EXTRACTION` accept only `on` or `off` when set. See [instruction settings](../docs/en/docs/operate/deploy-server.md#gateway-instruction).

### Replay epoch correlation module

`pc_gateway_core::replay_epoch` provides a bounded, memory-only replay epoch correlation store supporting establishment,
continuation, recovery, and closure. It is not wired into production model ingress or the Executor; recovery restores
correlation only, not an answer or permission to regenerate a model response.
Closed or expired records remain charged against entry and byte limits until explicit cleanup. The store owner must
drive `cleanup`; the module starts no background task, and `establish` does not reclaim records implicitly.

Run the no-model host simulation:

```bash
cargo run --locked --manifest-path gateway/Cargo.toml -p pc-gateway-core --example replay_epoch_host
```

## Run

Download the matching native archive from the [PowerContext Release](https://github.com/oceanbase/powercontext/releases) and verify its SHA256 before installation. Linux (glibc 2.28+) and macOS 15+ archives cover x86_64 and arm64; macOS archives are unsigned and not notarized. Core and provider CLI requirements apply separately from the Gateway's platform baseline. See [Install the Gateway](../docs/en/docs/operate/install-gateway.md) / [安装 Gateway](../docs/zh/docs/operate/install-gateway.md) for commands, Core configuration, compatibility checks and upgrade steps. `pc-gateway --version` reports release version, source commit and target without connecting to Core. Python packages do not include this executable.

If the selected Release has no Gateway archive, build from its source tag. From the repository root:

```bash
cargo build --locked --manifest-path gateway/Cargo.toml --release
```

For every deployment, Core must use `POWERCONTEXT_SERVER_ACCESS_MODE=enforced` and authenticate the service principal represented by `PC_GATEWAY_CORE_TOKEN`. A dedicated Gateway identity needs `gateway.invoke` (`gateway.user`) on every Scope selected with `X-PowerContext-Scope-ID`, not only `PC_GATEWAY_DEFAULT_SCOPE`, and `server.observe` (`server.observer`) on the Server resource for the configured deployment ID. When `PC_GATEWAY_SEDIMENT=on`, grant `scope.contributor` instead of `scope.viewer`; it provides `scope.read` and `scope.contribute`. For the bootstrap Server token and complete role-binding commands, see [Gateway deployment](../docs/en/docs/operate/deploy-server.md#gateway-model-destination).

For a combined deployment, start Core on a private Unix socket and configure Core-owned targets and credentials:

```bash
PC_GATEWAY_CORE_UDS=/path/to/core.sock \
PC_GATEWAY_CORE_TOKEN="$POWERCONTEXT_DEPLOYMENT_TOKEN" \
PC_GATEWAY_UPSTREAM_PROFILE=approved-provider \
PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES=approved-provider-responses \
PC_GATEWAY_DEFAULT_SCOPE=your-scope \
PC_GATEWAY_API_KEY="$GATEWAY_CLIENT_TOKEN" \
PC_GATEWAY_UPSTREAM_API_KEY_CHAT="$PROVIDER_API_KEY" \
PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES="$PROVIDER_RESPONSES_API_KEY" \
./gateway/target/release/pc-gateway
```

The Rust process owns the public listener and forwards other routes to Core over UDS. For a standalone Gateway, set `PC_GATEWAY_CORE_BASE` to the Core HTTP URL instead; standalone mode does not reverse-proxy Core routes.

`PC_GATEWAY_API_KEY` is mandatory on every listener, including loopback, and must differ from `PC_GATEWAY_CORE_TOKEN`. Forwarded Core API, MCP and Dashboard requests retain the client's credentials. `PC_GATEWAY_CORE_TOKEN` authenticates Gateway-initiated Core calls. Core target resolution requires enforced access mode and a service identity with the required Scope grants; a Core with authentication/access control disabled cannot serve model traffic through this Gateway. `PC_GATEWAY_UPSTREAM_URL*` and any non-empty `PC_GATEWAY_DEGRADE` setting are no longer accepted; configure Core-owned target profiles instead.

Request size, response buffering and concurrency have separate limits. The effective input limit includes Core's advertised capacity and the parser ceiling; increasing a deployment setting cannot bypass either. See [byte capacity](../docs/en/docs/operate/deploy-server.md#gateway-byte-capacity) for limits and failure statuses. Operational JSONL omits request and response bodies; its configuration is in [Gateway evidence](../docs/en/docs/operate/gateway-evidence.md).

## OAuth subscriptions

`PC_GATEWAY_SUBSCRIPTION` selects `codex`, `copilot`, `grok`, `qwen`, `kimi` or `gemini`; Claude CLI is not supported by Core-bound egress because its internal sends cannot be observed. HTTP channels require an explicit credential path in `PC_GATEWAY_SUBSCRIPTION_CREDENTIALS`. Copilot, Qwen and Kimi also support `pc-gateway login <provider> <new-file>`.

See [OAuth subscriptions](SUBSCRIPTIONS.md) for login steps, credential renewal, supported routes and protection limits. Gemini disables context injection. Codex can be withheld when Core requires an output token cap its endpoint cannot enforce.

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

Run controlled tests from the repository root:

```bash
cargo test --locked --manifest-path gateway/Cargo.toml --workspace -- --skip live_
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

## Execution deadline and cancellation

Model ingress establishes one absolute monotonic deadline of 180 seconds before reading the body. Intake, parsing, preparation, admission, provider sending, release checks, capture, response handoff, and local delivery consume that budget. Admission refreshes and permitted retries keep the original deadline. HTTP and UDS Core RPCs run inline under this control, including connection/pool waits and response framing; repeated reads cannot extend the deadline. Startup capabilities discovery retains its separate 10-second bound. Provider attempts retain their existing 120-second limit, further bounded by remaining call time. Core admission validity and correlation windows remain separate authorization constraints.

Rust callers supply `RequestControl::new(deadline, request_id)`, retain its `lifecycle()`, `cancellation()`, and `observation()` handles, and pass the one-shot control to `Executor::run`. `cancel()` stops pending local execution; `cancel_with(LifecycleStopReason::GatewayShutdown)` also supports the service owner's shutdown cause. `ManagedOutcome::Stopped { reason, model_send }` agrees with the retained execution snapshot. Cancellation intent alone does not rewrite completed terminals. A known interrupted execution has `ExecutionTerminal::Stopped(reason)`; dropping unknown unfinished execution still records `Abandoned`. Before Executor startup, lifecycle interruption retains `NotStarted`.

Ingress attaches the same `RequestLifecycle`, `RequestCancellation`, and `ExecutionObservation` to response extensions for the next local owner. The Executor and ingress own their futures inline and create no request cleanup tasks. Embedding owners that spawn tasks must cancel and join them themselves. The app explicitly drives Hyper HTTP/1 connection futures. Model responses declare `Connection: close`; normal completion of that connection records local completion. The original deadline also bounds pending response writes; expiration can truncate output, and error JSON is best effort. Framework-observed client disconnects stop local work with `request_cancelled`. Unobserved network loss remains bounded by the deadline. Neither observation establishes client receipt, provider billing cancellation, or remote rollback.

The model concurrency permit transfers to the connection owner before ingress starts and is released after Hyper's connection, inline request work, body, and pending write buffers end. Body EOF/drop and response extensions do not establish connection completion. `ExecutionSnapshot::local_delivery` distinguishes `NotStarted`, `InProgress`, `Completed`, and `Interrupted(reason)`; delivery interruption preserves completed execution terminals and model-send facts. Retaining an observation does not retain the permit or payload. Fallback retains its independent quota, body lifetime, authentication, and keep-alive behavior, without the model deadline or forced connection closure. UDS Core connections retain their idle pool. HTTP Core/provider requests use fresh HTTP/1 connections to prevent hidden stale-connection resends. Service-level bounded drain and process exit follow the shutdown policy below.

When an HTTP error can still be returned, deadline expiration maps to 504 and shutdown to 503. An explicit in-process cancellation uses 503 if a response channel remains available; there is no cancellation endpoint or nonstandard cancellation status. The lifecycle JSON contains independent `decision` and `model_send` fields, registered in `openapi/powercontext.yaml`. Stopping preserves possible sending and complete response facts; it does not establish zero cost, remote rollback, safe resend, release authorization, or delivery.

## Retry and Core RPC recovery

The production Executor is the only model retry owner. Each provably unsent attempt may retry up to the configured attempt limit, obtains a new before-send admission, and validates its binding and validity. The original deadline and cancellation apply throughout. `Executor::close_model_admission()` permanently closes new attempts, including retries; handed-off calls can still finish within their original lifecycle. `Executor::model_admission()` exposes a one-way atomic closure handle for signal handlers; `close_model_admission()` also synchronizes with committed transport handoffs before the service inspects sending facts. A timeout, partial response, or disconnect after request handoff retains possibly-sent facts and never triggers an automatic resend or fallback. The transport no longer exports `BoundedRetry` or `RetryOutcome`; embedding callers that used them must route model retries through the Executor.

Both Core clients allow at most one eligible transport retry. Check and capabilities are read-only. Capture serializes once and replays the same Scope, content Source identity, body, and full metadata; an existing equal capture returns its original journal position, while changed content or metadata remains conflicting. Prepare reconnects only on proof that zero request bytes were handed off. Missing response headers, EOF, and timeouts do not prove non-execution. Authentication failures, explicit rejections, malformed responses, and capture conflicts are not retried.

HTTP Core operations have one 10-second local budget and UDS operations one 30-second local budget, including connection acquisition, writes, both attempts, response bodies and trailers. Managed calls may stop earlier under their shared deadline/cancellation; startup capabilities keeps its independent 10-second bound. The UDS pool bounds idle connections, not active concurrency, and has no capacity-wait queue. Reqwest automatic retries and redirects are disabled. HTTP Core/provider clients use fresh HTTP/1 connections, also preventing hyper's implicit stale-pool replay; this increases connection and TLS setup cost. UDS retains operation-aware keep-alive reuse.

`cargo test --locked --manifest-path gateway/Cargo.toml --workspace` requires an installed project Python at `.venv/bin/python` (override with `PC_TEST_PYTHON`) for mandatory real-Core UDS recovery acceptance. Core startup failure fails that test. The older optional TCP binding tests still use their separate port-8000 availability check.

## Staged process shutdown

On Unix, SIGTERM or Ctrl-C closes model-attempt admission directly in the signal handler. The first handler invocation records a monotonic timestamp; repeated signals never restart the budget. Before listening, the app installs the handlers and starts a native watchdog thread independent of Tokio. The listener then closes and existing HTTP/1 connections enter graceful shutdown. Established or buffered requests cannot bypass closed model admission. Calls still in intake, prepare, admission, or retry admission stop with `gateway_shutdown` when no model request is outstanding.

Already handed-off calls may finish response checks, capture, and local delivery under their original 180-second request deadline and a common drain deadline 25 seconds after the first signal. Normally drained calls retain their business result without a shutdown stop reason. Explicit cancellation and observable disconnect still stop work. A transport attempt subsequently proved unsent cannot retry. Fallback keeps its Core authentication, independent quota, and long connections during normal service; its connections share shutdown deadlines, including long MCP SSE streams.

At drain expiry, the service ends remaining connections, cancels managed tasks, and joins their actual owners. Hyper, inline request/body work, and pending output are destroyed before connection-held permits and controls are released. Owner joins and Tokio runtime destruction must finish before the same signal's 30-second total exit deadline. The watchdog remains armed after the owner set empties. If cleanup cannot finish, it invokes Unix `_exit(1)` without further logging, destructors, atexit hooks, or joins. This covers non-yielding request work and already-running `spawn_blocking` tasks; Tokio cannot preempt them. As with any process deadline, OS scheduling has a finite measurement tolerance.

Abnormal process exit is not proof of per-request cleanup, provider rollback, or client receipt. Known or uncertain remote operations retain their meaning. This executable does not start a Python supervisor, close the Python Core database, or implement restart generations. The RFC 2199 parent must own those responsibilities and confirm the old Rust PID has exited before restart. The Rust embedding API still leaves service signals and process lifetime to its host.
