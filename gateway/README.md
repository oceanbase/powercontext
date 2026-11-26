# PowerContext Rust Gateway

The optional Rust Gateway adds PowerContext recall and conversation capture to OpenAI-compatible model traffic. PowerContext Core decides whether a request may be sent and whether its response may be released. See [RFC 2199](../docs/en/rfcs/2199-powercontext-gateway.md) for the design.

The Core-bound egress path requires privileged target resolution in `/v1/context/check` and `include_reuse`. It has been exercised with real Core and local upstream TCP listeners, not an external provider. See the [1.6.0 release notes](../release/notes/powercontext-v1.6.0.md) for compatibility.

## Model traffic

The Gateway serves `/gateway/v1/chat/completions` and `/gateway/v1/responses`. It checks the final request with Core before sending it, then buffers the response and checks it before releasing content. Clients requesting a stream receive buffered replay after approval, rather than live token delivery. Upstream error statuses are preserved.

- **Two-phase gate executor**: `check(before_send)` → send admitted bytes → `check(before_release)` → release. Opaque bindings thread the two phases (`request_binding` must be the send-phase binding). HTTP success alone is never treated as approval; unknown never becomes allow.
- **Privacy enforcement by Core, executed by the gateway**: forbidden content is blocked before any byte leaves the gateway (zero-send on block/withhold, zero-release on refuse, upstream cancel on refusal).
- **Deterministic-failure-only bounded retry**: connection-refused style failures retry a bounded number of times with a single owner; indeterminate outcomes (timeout after hand-off) are surfaced as `send_indeterminate` and never auto-retried (no paid re-sends).
- **Core-owned send destination**: privileged `before_send` returns the exact endpoint and renderer protocol from the Core profile. Only that admission can reach transport; missing/old responses, an unavailable Core before admission, or a mismatched protocol pause with zero sends. Offline passthrough is disabled; `PC_GATEWAY_DEGRADE=passthrough` fails at startup. Issued admissions remain usable through their deadline, and each confirmed-not-sent retry requires a fresh Core decision.
- **Streaming ingestion** (`stream: true`): upstream SSE is received incrementally with a byte cap; first HTTP body byte latency is measured from before the response-header wait, while model TTFT remains unknown. Strict semantics hold — the complete raw response is buffered and must pass `before_release` before anything is released to the client. (The current public carrier in #2264 withholds streaming payloads; the pipeline is ready for when the contract allows it.)
- **Status-code fidelity**: upstream errors pass through with their original status code (a provider 402/400 is never masked as 200), and `Content-Type` is preserved.

The default upstream uses the same API format as the client. [Subscription adapters](SUBSCRIPTIONS.md) provide channel-specific conversions. The Gateway does not serve the Anthropic Messages API.

Blocked calls return HTTP 403. Withheld calls return HTTP 400 unless the executor marks them retryable, in which case they return HTTP 409. Currently, only a nonempty reason list containing exclusively `context-refresh-required` is retryable. Admission expiry also returns HTTP 409. A retry after upstream generation may spend provider quota again.

## Memory

- **Two deployment forms**: *combined* (Rust owns the public TCP port; Core listens only on a private Unix socket; all non-`/gateway` routes are reverse-proxied to Core over UDS) or *standalone* (TCP to a remote Core).
- **UDS client**: hand-rolled HTTP/1.1 over the Unix socket with a pooled, keep-alive connection pool (bounded idle set, connect/read timeouts, operation-aware single retry, one complete-operation timeout, content-length truncation and chunked-trailer consumption — covered by a real-socket regression test).
- **OpenAI-compatible ingress** at `/gateway/v1/chat/completions` and `/gateway/v1/responses` (same format in and out, no cross-format translation), authenticated by a gateway key that replaces provider keys.
- **Authentication boundary**: `PC_GATEWAY_API_KEY` protects only the two model routes above. Other paths forwarded to Core retain the client's credentials and follow Core's own authentication and access-control rules. Before exposing a combined deployment, enable Core authentication and enforced access control; neither the Gateway key nor UDS replaces Core authentication. See [deployment authentication](../docs/en/docs/operate/deploy-server.md#gateway-authentication-boundaries) ([中文](../docs/zh/docs/operate/deploy-server.md#网关认证边界)).
- **Responses fidelity**: on `/gateway/v1/responses`, every input item other than a plain user message (developer messages, tool calls and outputs, reasoning, client-specific items such as Codex's `additional_tools`) is forwarded unchanged, and request fields the ingress does not model (for example `reasoning`, `include`, `prompt_cache_key`) are passed through for Core's request allowlist to decide; only the client-local `client_metadata` is dropped. The upstream request is always non-streaming; a streaming client receives the released  200 response replayed as Responses SSE events: `response.created`, `response.output_item.added`/`done` per item with each message content part as `response.content_part.added`/`done` around one buffered `response.output_text.delta`/`done` (or `response.refusal.delta`/`done`), and a terminal event chosen from the response status (`response.completed`, `response.incomplete`, or `response.failed` when the status is `failed`/`cancelled` or an `error` is present).
- **Protection refusals**: blocked requests return HTTP 403; deterministic `withhold` outcomes return HTTP 400 with `decision` and `reason_codes`, including release-time refusals after upstream generation. Codex can retry generic 403 errors as stream failures, so 403 does not make a `withhold` terminal for that client. Repeating the same refused request cannot resolve these outcomes and may consume provider quota again. The HTTP adapter maps the executor's `retryable` classification to HTTP 409 when true and HTTP 400 when false; it does not classify `reason_codes`. The executor currently classifies a `withhold` as retryable only when its nonempty reason list contains exclusively `context-refresh-required`, in either phase; mixed, unknown, or empty reasons are non-retryable. A release-phase retry can repeat upstream generation and consume provider quota again. Stale prepared context is still refreshed internally at most once before send. Admission expiry retains HTTP 409.
- **Byte capacity**: startup-confirmed model input is the minimum of deployment, Core input capacity, and the current 1 MiB parser ceiling; final rendered bytes are checked before send. Provider response buffering is capped independently at 1 MiB; overflow withholds content without automatic resend and may already be billed. Model and fallback concurrency budgets are separate. See [deployment capacity and migration behavior](../docs/en/docs/operate/deploy-server.md#gateway-byte-capacity) ([中文](../docs/zh/docs/operate/deploy-server.md#gateway-字节容量)). These limits do not establish a process RSS ceiling. G5-A readable billing evidence remains pending integration with #2506; see [acceptance status](CAPACITY_VALIDATION.md#remaining-issue-acceptance).
- **Middleware chain**: composable `GatewayMiddleware` trait; ships with resource limits (request-body precheck + concurrency semaphore, health probe exempt) and request-id propagation (`x-powercontext-request-id`, client-supplied or generated, echoed on responses).
- **Body-free evidence**: bounded JSONL on stdout (1024 queued records plus one in flight), correlated call/attempt terminals, per-field usage completeness and cache reads, explicit first-byte/release/framework timing and separate first model-content frame observation; model TTFT stays unknown. Operational tracing uses stderr and omits configured Core URLs/socket paths. Common operation/unit/outcome fields align with the Server vocabulary; the managed Server OTLP pipeline does not ingest Gateway JSONL by default. See the [output and deployment contract](../docs/en/docs/operate/gateway-evidence.md) / [中文](../docs/zh/docs/operate/gateway-evidence.md). `PC_GATEWAY_EVIDENCE=off` explicitly disables coverage.

Use `X-PowerContext-Conversation-ID` to identify a conversation. The Gateway also recognizes client session headers from OpenCode, Codex and translated Claude Code traffic. Without a valid header, eligible full-history requests get a process-local identity for preparation replay from their opening messages. See [context injection settings](../docs/en/docs/operate/deploy-server.md#gateway-context-injection) for header precedence and fallback behavior. Standalone deployments require `PC_GATEWAY_INJECTION=on` to enable injection.

Set `PC_GATEWAY_SEDIMENT=on` to capture completed user turns as Sources for later extraction. Capture excludes injected context and provider reasoning ciphertext. Tool continuations remain open until the final response. Capture requires Core batch support; capture failure does not prevent response delivery.

When Core allows a Chat Completions stream, capture assembles the primary choice's text from its deltas and requires a terminal finish reason. The client receives the original SSE body.

For a supported Responses upstream, prefix extraction is enabled by default; `PC_GATEWAY_PREFIX_EXTRACTION=off` disables it. With a subscription this applies to `codex` only, on a Core confirmed as non-enforcing; the extraction requests use the subscription account, and the Core-approved destination must match the subscription endpoint. Traffic of the other subscriptions uses normal extraction. Capture remains opt-in. Core also needs its gateway callback URL and generation model configured. With that URL configured, the Server defaults to `one_pass` unless a processing mode was explicitly supplied; `POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_PROCESSING_MODE=per_candidate` retains the previous mode. Both processing modes can reuse an eligible agent prefix when estimated cheaper, with normal extraction as the fallback. See [prefix extraction settings](../docs/en/docs/operate/deploy-server.md#memory-extraction-over-the-cached-prefix).

## Steering

Responses requests include the [built-in cost guidance](crates/pc-gateway-app/src/default_steering.txt) by default. Set `PC_GATEWAY_STEERING_FILE` to replace it, or `PC_GATEWAY_STEERING=off` to disable steering even when a file is configured. Chat requests are unchanged. Both `PC_GATEWAY_STEERING` and `PC_GATEWAY_PREFIX_EXTRACTION` accept only `on` or `off` when set. See [instruction settings](../docs/en/docs/operate/deploy-server.md#gateway-instruction).

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

For a combined deployment, start Core on a private Unix socket and configure an approved upstream profile and an accessible Scope:

```bash
PC_GATEWAY_CORE_UDS=/path/to/core.sock \
PC_GATEWAY_CORE_TOKEN="$POWERCONTEXT_DEPLOYMENT_TOKEN" \
PC_GATEWAY_UPSTREAM_PROFILE=approved-provider \
PC_GATEWAY_DEFAULT_SCOPE=your-scope \
PC_GATEWAY_API_KEY="$GATEWAY_CLIENT_TOKEN" \
PC_GATEWAY_UPSTREAM_API_KEY_CHAT="$PROVIDER_API_KEY" \
./gateway/target/release/pc-gateway
```

The Rust process owns the public listener and forwards other routes to Core over UDS. For a standalone Gateway, set `PC_GATEWAY_CORE_BASE` to the Core HTTP URL instead; standalone mode does not reverse-proxy Core routes.

`PC_GATEWAY_API_KEY` authenticates the two model routes and is required for every listener. Forwarded Core API, MCP and Dashboard requests retain the client's credentials. `PC_GATEWAY_CORE_TOKEN` authenticates Gateway-initiated Core calls. See [deployment authentication](../docs/en/docs/operate/deploy-server.md#gateway-authentication-boundaries) before exposing the service.

Request size, response buffering and concurrency have separate limits. The effective input limit includes Core's advertised capacity and the parser ceiling; increasing a deployment setting cannot bypass either. See [byte capacity](../docs/en/docs/operate/deploy-server.md#gateway-byte-capacity) for limits and failure statuses. Operational JSONL omits request and response bodies; its configuration is in [Gateway evidence](../docs/en/docs/operate/gateway-evidence.md).

## OAuth subscriptions

`PC_GATEWAY_SUBSCRIPTION` selects `codex`, `copilot`, `grok`, `qwen`, `kimi` or `gemini`; Claude CLI is not supported by Core-bound egress because its internal sends cannot be observed. HTTP channels require an explicit credential path in `PC_GATEWAY_SUBSCRIPTION_CREDENTIALS`. Copilot, Qwen and Kimi also support `pc-gateway login <provider> <new-file>`.

See [OAuth subscriptions](SUBSCRIPTIONS.md) for login steps, credential renewal, supported routes and protection limits. Gemini disables context injection. Subscription credentials are sent only when Core approves their exact endpoint.

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

Core `InferenceConfig.protection.targets[<profile>]` owns the endpoint, model and protocol. Configure
an authenticated service principal with `gateway.invoke` and `scope.read` on the selected Scope,
and pass its bearer token as `PC_GATEWAY_CORE_TOKEN` (UDS alone does not authenticate).
Chat uses `PC_GATEWAY_UPSTREAM_PROFILE`; Responses requires `PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES`.
Requests for other models are rejected, even when privacy enforcement is disabled. Deploy separate
Gateway instances for multiple models. The client-facing `PC_GATEWAY_API_KEY` must be distinct
from the Core token. Neither key authenticates a client to Core's forwarded API routes.
The transport connects directly to the Core-approved endpoint and ignores ambient HTTP proxies;
the removed `PC_GATEWAY_UPSTREAM_URL*` variables cause a startup error. Credentials never enter
Core check payloads.

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

The production Executor is the only model retry owner. Each provably unsent attempt may retry up to the
configured attempt limit, obtains a new before-send admission, and validates its binding and validity.
The original deadline and cancellation apply throughout. `Executor::close_model_admission()` permanently
closes new attempts, including retries; handed-off calls can still finish within their
original lifecycle. `Executor::model_admission()` exposes a one-way atomic closure handle for signal
handlers; `close_model_admission()` also synchronizes with committed transport handoffs before the
service inspects sending facts.
A timeout, partial response, or disconnect after request handoff retains possibly-sent facts and never
triggers an automatic resend or fallback. The transport no longer exports `BoundedRetry` or `RetryOutcome`;
embedding callers that used them must route model retries through the Executor.

Both Core clients allow at most one eligible transport retry. Check and capabilities are read-only. Capture serializes once and replays the same Scope, content Source identity, body, and full metadata; an existing equal capture returns its original journal position, while changed content or metadata remains conflicting. Prepare reconnects only on proof that zero request bytes were handed off. Missing response headers, EOF, and timeouts do not prove non-execution. Authentication failures, explicit rejections, malformed responses, and capture conflicts are not retried.

HTTP Core operations have one 10-second local budget and UDS operations one 30-second local budget, including connection acquisition, writes, both attempts, response bodies and trailers. Managed calls may stop earlier under their shared deadline/cancellation; startup capabilities keeps its independent 10-second bound. The UDS pool bounds idle connections, not active concurrency, and has no capacity-wait queue. Reqwest automatic retries and redirects are disabled. HTTP Core/provider clients use fresh HTTP/1 connections, also preventing hyper's implicit stale-pool replay; this increases connection and TLS setup cost. UDS retains operation-aware keep-alive reuse.

`cargo test --locked --manifest-path gateway/Cargo.toml --workspace` requires an installed project Python at `.venv/bin/python` (override with `PC_TEST_PYTHON`) for mandatory real-Core UDS recovery acceptance. Core startup failure fails that test. The older optional TCP binding tests still use their separate port-8000 availability check.

## Staged process shutdown

On Unix, SIGTERM or Ctrl-C closes model-attempt admission directly in the signal handler. The first handler invocation records a monotonic timestamp; repeated signals never restart the budget. Before listening, the app installs the handlers and starts a native watchdog thread independent of Tokio. The listener then closes and existing HTTP/1 connections enter graceful shutdown. Established or buffered requests cannot bypass closed model admission. Calls still in intake, prepare, admission, or retry admission stop with `gateway_shutdown` when no model request is outstanding.

Already handed-off calls may finish response checks, capture, and local delivery under their original 180-second request deadline and a common drain deadline 25 seconds after the first signal. Normally drained calls retain their business result without a shutdown stop reason. Explicit cancellation and observable disconnect still stop work. A transport attempt subsequently proved unsent cannot retry. Fallback keeps its Core authentication, independent quota, and long connections during normal service; its connections share shutdown deadlines, including long MCP SSE streams.

At drain expiry, the service ends remaining connections, cancels managed tasks, and joins their actual owners. Hyper, inline request/body work, and pending output are destroyed before connection-held permits and controls are released. Owner joins and Tokio runtime destruction must finish before the same signal's 30-second total exit deadline. The watchdog remains armed after the owner set empties. If cleanup cannot finish, it invokes Unix `_exit(1)` without further logging, destructors, atexit hooks, or joins. This covers non-yielding request work and already-running `spawn_blocking` tasks; Tokio cannot preempt them. As with any process deadline, OS scheduling has a finite measurement tolerance.

Abnormal process exit is not proof of per-request cleanup, provider rollback, or client receipt. Known or uncertain remote operations retain their meaning. This executable does not start a Python supervisor, close the Python Core database, or implement restart generations. The RFC 2199 parent must own those responsibilities and confirm the old Rust PID has exited before restart. The Rust embedding API still leaves service signals and process lifetime to its host.
