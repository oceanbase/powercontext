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
- **Sedimentation**: after a released turn, the gateway synchronously captures the transcript via `capture_content_source` (millisecond-scale durable write; extraction happens later in PC background workers). `source_id` is idempotent (derived from the release binding digest). Blocked and degraded turns are never captured. The full loop — conversation → capture → background extraction into Atomic Memory → `prepare` recall in a *new* session — has been verified end-to-end.

### Deployment & ops

- **Two deployment forms**: *combined* (Rust owns the public TCP port; Core listens only on a private Unix socket; all non-`/gateway` routes are reverse-proxied to Core over UDS) or *standalone* (TCP to a remote Core).
- **UDS client**: hand-rolled HTTP/1.1 over the Unix socket with a pooled, keep-alive connection pool (bounded idle set, connect/read timeouts, stale-connection single retry, content-length truncation and chunked-trailer consumption — covered by a real-socket regression test).
- **OpenAI-compatible ingress** at `/gateway/v1/chat/completions` and `/gateway/v1/responses` (same format in and out, no cross-format translation), authenticated by a gateway key that replaces provider keys.
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
              ◀──          source_id = digest(release binding))   — released turns only;
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

# Optional: PC_GATEWAY_INJECTION=on (prepare + inject), PC_GATEWAY_SEDIMENT=on (capture after release),
# PC_GATEWAY_DEGRADE=passthrough (no-memory passthrough when Core is unreachable; never applies
# to deployments with enforcing protection), PC_GATEWAY_MAX_CONCURRENT / PC_GATEWAY_MAX_BODY_BYTES,
# PC_GATEWAY_CORE_TOKEN (bearer for Core when authentication is enabled).
```

Credentials live only in server-side environment variables; they are never accepted from request fields and never enter check payloads.

## Tests

`cargo test` — 27 tests covering the failure-semantics table (gate unavailable, withhold terminal, bounded retry, indeterminate no-resend, blocked turns never sediment), renderer fidelity (no timestamps/request ids injected), byte-range declaration, UDS connection-pool correctness over a real Unix socket, and live integration tests against a real PowerContext Server (binding validity, cross-scope rejection, deadlines) that skip gracefully when no server is reachable.
