# PowerContext Rust Gateway

An optional Rust executor for PowerContext, defined by the gateway design in #2199 and the context-protection contract in #2264. The gateway owns the model traffic boundary; every decision stays in PowerContext Core ("no policy in the gateway").

> Status: working prototype, verified end-to-end against a real PowerContext Server (PR #2264 branch) with real upstream providers (GLM, DeepSeek). Depends on the `/v1/context/check` contract and `include_reuse` from #2264.

## Features

### Model traffic pipeline (per request)

- **Two-phase gate executor**: `check(before_send)` → send admitted bytes → `check(before_release)` → release. Opaque bindings thread the two phases (`request_binding` must be the send-phase binding). HTTP success alone is never treated as approval; unknown never becomes allow.
- **Privacy enforcement by Core, executed by the gateway**: forbidden content is blocked before any byte leaves the gateway (zero-send on block/withhold, zero-release on refuse, upstream cancel on refusal).
- **Deterministic-failure-only bounded retry**: connection-refused style failures retry a bounded number of times with a single owner; indeterminate outcomes (timeout after hand-off) are surfaced as `send_indeterminate` and never auto-retried (no paid re-sends).
- **Degradation policy**: when Core is unreachable, either pause (default) or pass through without memory (`PC_GATEWAY_DEGRADE=passthrough`) — degraded responses are explicitly marked (`x-pc-degraded` header). Blocked verdicts never degrade.
- **Streaming ingestion** (`stream: true`): upstream SSE is received incrementally (TTFT measured, byte-capped); strict semantics hold — the complete raw response is buffered and must pass `before_release` before anything is released to the client. (The current public carrier in #2264 withholds streaming payloads; the pipeline is ready for when the contract allows it.)
- **Status-code fidelity**: upstream errors pass through with their original status code (a provider 402/400 is never masked as 200), and `Content-Type` is preserved.

### Memory features (#2199 capabilities)

- **Auto-recall injection**: for each new user message the gateway calls `prepare` with `include_reuse`, and injects the returned prepared context **verbatim** into the current user message. The exact UTF-8 byte range is declared in `prepared_contexts` (`ModelJsonTextLocation`); Core verifies the digest of the declared range against the signed reuse binding — a single byte changed by the gateway is rejected. Empty or failed prepares inject nothing and never block the main path. Injection is tail-only (current message), so the client-owned history prefix stays byte-stable (KV prefix-cache friendly, verified with DeepSeek `cached_tokens`).
  A request that belongs to a conversation gets the RFC 2199 lifecycle. The gateway takes the conversation from `X-PowerContext-Conversation-ID` when the host sends one; otherwise from the client's native session header: Claude Code `x-claude-code-session-id` combined with `x-claude-code-agent-id` (subagents share the session ID), OpenCode `x-opencode-session-id` (a child session has its own ID; the shared affinity headers are not used), or, on `/gateway/v1/responses` only, Codex `thread-id` (a subagent has its own thread ID; the `session-id` and `prompt_cache_key` it shares with its parent are not used). Claude Code speaks the Anthropic Messages API, which the gateway does not serve, so its headers take effect only behind a proxy that translates its requests to the Chat or Responses route. Within a conversation, a request whose messages up to and including the last user message are unchanged (a tool continuation or retry) replays the exact prepared content and binding without another retrieval, and a new user message prepares once. Replay state is partitioned by Scope and conversation ID, kept in memory, bounded, and expires with the reuse binding (at most 5 minutes). Without a conversation, every request prepares on its own and never inherits an earlier injection. An empty result is replayed as well; a failed `prepare` is retried by the next request and never blocks it. Every send and release still goes through `check`.
- **Sedimentation**: each user turn is captured once, synchronously, via `capture_content_source` when its terminal response is released: one that needs no client-executed tool. On the Responses route, built-in tools that complete inside the response (web search, file search, code interpreter, image generation, MCP calls) count as finished, while client-executed calls (`*_call` items, MCP approval requests) and unrecognized items keep the turn open (millisecond-scale durable write; extraction happens later in PC background workers). Tool-calling responses in between are not captured. The captured `gateway_turn` record is normalized from the pre-injection request and the terminal response: the user text, then each tool call (name and arguments) and tool output, including built-in tool calls and their returned results, then the final assistant text. Provider reasoning ciphertext, IDs, usage, other transport metadata and `data:` URLs (screenshots, images) are left out; each field is capped at 8 KiB and the record at 64 KiB, dropping the oldest steps first (`omitted_steps`). `source_id` identifies the turn: a digest of the Scope, the trusted conversation ID and the pre-injection messages up to the last user message, so a retried, repeated or concurrent terminal request writes the same Source (Core keeps the first content and answers a different one with 409, recorded as `sediment_duplicate`). Without a trusted conversation, identical messages in different conversations are never merged, and each release keeps its own `source_id` from the release binding. Blocked and degraded turns are never captured, and a failed capture is recorded without blocking the release. The full loop — conversation → capture → background extraction into Atomic Memory → `prepare` recall in a *new* session — has been verified end-to-end.

### Deployment & ops

- **Two deployment forms**: *combined* (Rust owns the public TCP port; Core listens only on a private Unix socket; all non-`/gateway` routes are reverse-proxied to Core over UDS) or *standalone* (TCP to a remote Core).
- **UDS client**: hand-rolled HTTP/1.1 over the Unix socket with a pooled, keep-alive connection pool (bounded idle set, connect/read timeouts, stale-connection single retry, content-length truncation and chunked-trailer consumption — covered by a real-socket regression test).
- **OpenAI-compatible ingress** at `/gateway/v1/chat/completions` and `/gateway/v1/responses` (same format in and out, no cross-format translation), authenticated by a gateway key that replaces provider keys.
- **Responses fidelity**: on `/gateway/v1/responses`, every input item other than a plain user message (developer messages, tool calls and outputs, reasoning, client-specific items such as Codex's `additional_tools`) is forwarded unchanged, and request fields the ingress does not model (for example `reasoning`, `include`, `prompt_cache_key`) are passed through for Core's request allowlist to decide; only the client-local `client_metadata` is dropped. The upstream request is always non-streaming; a streaming client receives the released (or permitted degraded-passthrough, still marked `x-pc-degraded`) 200 response replayed as Responses SSE events: `response.created`, `response.output_item.added`/`done` per item with each message content part as `response.content_part.added`/`done` around one buffered `response.output_text.delta`/`done` (or `response.refusal.delta`/`done`), and a terminal event chosen from the response status (`response.completed`, `response.incomplete`, or `response.failed` when the status is `failed`/`cancelled` or an `error` is present).
- **Byte capacity**: startup-confirmed model input is the minimum of deployment, Core input capacity, and the current 1 MiB parser ceiling; final rendered bytes are checked before send. Provider response buffering is capped independently at 1 MiB; overflow withholds content without automatic resend and may already be billed. Model and fallback concurrency budgets are separate. See [deployment capacity and migration behavior](../docs/en/docs/operate/deploy-server.md#gateway-byte-capacity) ([中文](../docs/zh/docs/operate/deploy-server.md#gateway-字节容量)). These limits do not establish a process RSS ceiling. G5-A readable billing evidence remains pending integration with #2506; see [acceptance status](CAPACITY_VALIDATION.md#remaining-issue-acceptance).
- **Middleware chain**: composable `GatewayMiddleware` trait; ships with resource limits (request-body precheck + concurrency semaphore, health probe exempt) and request-id propagation (`x-powercontext-request-id`, client-supplied or generated, echoed on responses).
- **Body-free evidence**: usage completeness (known/estimated/unknown), cache tokens, TTFT vs first-release latency, attempt counters — never request/response bodies.

## PC call sequence over UDS

All gateway→Core calls go through one `GateClient` (UDS in combined form, TCP in standalone form) and share the connection pool. For one chat request with injection and sedimentation enabled:

```
client ──POST /gateway/v1/chat/completions──▶ gateway (middleware: limits → request-id)
                                                │
 1. prepare     gateway ──▶ POST /v1/context/prepare (query = last user message,
              ◀──          include_reuse=true)
                content (trust preamble + marker block) + reuse binding (5 min)
                — injected verbatim; byte range recorded; failure/empty ⇒ no injection
                                                │
 2. check       gateway ──▶ POST /v1/context/check  phase=before_send
  (send gate)   ◀──          allow + send binding (60 s)   | block/withhold ⇒ 403/409, zero send
                                                │
 3. send        gateway ──▶ upstream provider (admitted bytes only; the single egress)
              ◀──          complete raw response (+ usage)
                                                │
 4. check       gateway ──▶ POST /v1/context/check  phase=before_release
  (release                (payload = complete raw response, request_binding = send binding)
   gate)        ◀──          allow + release binding          | refuse ⇒ cancel upstream, 403,
                                                                     nothing released
                                                │
 5. capture     gateway ──▶ POST /v1/sources/content (transcript JSON,
              ◀──          source_id = turn digest)                 — terminal response only;
                                                                     failure is evidence-only
                                                │
client ◀── 200 (upstream status/content-type preserved, request-id echoed) ── gateway
```

Everything else (Core's 127 domain operations, MCP, dashboard) is reverse-proxied to Core over the same UDS socket, untouched by gateway logic.

## Layout

| Crate | Responsibility |
| --- | --- |
| `pc-gateway-app` | thin binary: ingress, port-takeover proxy, middleware chain |
| `pc-gateway-core` | executor state machine, GateClient (check/prepare/capture/capabilities) over TCP or UDS |
| `pc-gateway-types` | contract DTOs aligned field-by-field with #2264 |
| `pc-gateway-protocol` | canonical request, chat/responses wire renderers, SSE framing |
| `pc-gateway-transport` | upstream transport, bounded retry, streaming buffer with caps |
| `pc-gateway-evidence` | body-free evidence (usage completeness, cache tokens, TTFT) |
| `pc-gateway-embedded` | placeholder for the in-process (FFI) executor path |

## Run

```bash
cargo build --release

# Combined deployment (Core on a Unix socket)
PC_GATEWAY_CORE_UDS=/path/to/pc.sock \
PC_GATEWAY_UPSTREAM_PROFILE=<approved profile name> \
PC_GATEWAY_DEFAULT_SCOPE=<scope id> \
PC_GATEWAY_UPSTREAM_URL_CHAT=https://provider/v1/chat/completions \
PC_GATEWAY_UPSTREAM_API_KEY_CHAT=... \
./target/release/pc-gateway

# Injection (prepare + inject on the Chat and Responses routes): in the combined deployment (PC_GATEWAY_CORE_UDS)
# requests that belong to a conversation (X-PowerContext-Conversation-ID or a recognized client session header)
# are injected by default; standalone is off by default.
# PC_GATEWAY_INJECTION=on injects every request, PC_GATEWAY_INJECTION=off disables injection, and
# PC_GATEWAY_INJECTION_RESPONSES=off keeps the Responses route uninjected, and PC_GATEWAY_INJECTION_CODE=on adds code
# evidence from the Scope's code index (off by default).
# Optional: PC_GATEWAY_STEERING_FILE (a fixed operator instruction added to Responses requests after the client's
# base prompt; read and validated at startup), PC_GATEWAY_SEDIMENT=on (capture after release),
# PC_GATEWAY_DEGRADE=passthrough (no-memory passthrough when Core is unreachable; never applies
# to deployments with enforcing protection), PC_GATEWAY_MAX_CONCURRENT / PC_GATEWAY_MAX_BODY_BYTES,
# PC_GATEWAY_CORE_TOKEN (bearer for Core when authentication is enabled).
```

Credentials live only in server-side environment variables; they are never accepted from request fields and never enter check payloads.

## Tests

`cargo test` — 27 tests covering the failure-semantics table (gate unavailable, withhold terminal, bounded retry, indeterminate no-resend, blocked turns never sediment), renderer fidelity (no timestamps/request ids injected), byte-range declaration, UDS connection-pool correctness over a real Unix socket, and live integration tests against a real PowerContext Server (binding validity, cross-scope rejection, deadlines) that skip gracefully when no server is reachable.

## Execution deadline and cancellation

Model ingress establishes one absolute monotonic deadline of 180 seconds before reading the body.
Intake, parsing, preparation, admission, provider sending, release checks, capture, response handoff, and local delivery
consume that budget. Admission refreshes and permitted retries keep the original deadline. HTTP and UDS
Core RPCs run inline under this control, including connection/pool waits and response framing; repeated
reads cannot extend the deadline. Startup capabilities discovery retains its separate 10-second bound.
Provider attempts retain their existing 120-second limit, further bounded by remaining call time.
Core admission validity and correlation windows remain separate authorization constraints.

Rust callers supply `RequestControl::new(deadline, request_id)`, retain its `lifecycle()`, `cancellation()`,
and `observation()` handles, and pass the one-shot control to `Executor::run`. `cancel()` stops pending
local execution; `cancel_with(LifecycleStopReason::GatewayShutdown)` also supports the service owner's
shutdown cause. `ManagedOutcome::Stopped { reason, model_send }` agrees with the retained execution
snapshot. Cancellation intent alone does not rewrite completed terminals. A known interrupted execution
has `ExecutionTerminal::Stopped(reason)`; dropping unknown unfinished execution still records `Abandoned`.
Before Executor startup, lifecycle interruption retains `NotStarted`.

Ingress attaches the same `RequestLifecycle`, `RequestCancellation`, and `ExecutionObservation` to
response extensions for the next local owner. The Executor and ingress own their futures inline and
create no request cleanup tasks. Embedding owners that spawn tasks must cancel and join them themselves.
The app explicitly drives Hyper HTTP/1 connection futures. Model responses declare `Connection: close`;
normal completion of that connection records local completion. The original deadline also bounds pending
response writes; expiration can truncate output, and error JSON is best effort. Framework-observed client
disconnects stop local work with `request_cancelled`. Unobserved network loss remains bounded by the deadline.
Neither observation establishes client receipt, provider billing cancellation, or remote rollback.

The model concurrency permit transfers to the connection owner before ingress starts and is released after
Hyper's connection, inline request work, body, and pending write buffers end. Body EOF/drop and response
extensions do not establish connection completion. `ExecutionSnapshot::local_delivery` distinguishes
`NotStarted`, `InProgress`, `Completed`, and `Interrupted(reason)`; delivery interruption preserves completed
execution terminals and model-send facts. Retaining an observation does not retain the permit or payload.
Fallback retains its independent quota, body lifetime, authentication, and keep-alive behavior, without the
model deadline or forced connection closure. Core/provider connection pools remain enabled. Service-level
bounded drain/process exit is tracked separately in G3 issue 04. .

When an HTTP error can still be returned, deadline expiration maps to 504 and shutdown to 503. An explicit
in-process cancellation uses 503 if a response channel remains available; there is no cancellation endpoint
or nonstandard cancellation status. The lifecycle JSON contains independent `decision` and `model_send`
fields, registered in `openapi/powercontext.yaml`. Stopping preserves possible sending and complete response
facts; it does not establish zero cost, remote rollback, safe resend, release authorization, or delivery.
