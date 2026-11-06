---
title: Gateway call evidence
description: Read bounded, body-free Rust Gateway call, attempt, usage and HTTP-framework observations.
---

# Gateway call evidence

The Rust Gateway exports `powercontext.gateway-evidence.v1` JSON lines on **stdout** by default.
Operational tracing goes to **stderr**. No external telemetry platform is required. Configure your existing
log collector's rotation and retention; redirecting stdout to a file does not bound that file's size.

```bash
# Keep evidence separate from operational logs; use a collector with bounded retention.
./gateway/target/release/pc-gateway | your-existing-log-collector

# Explicitly disable evidence. The startup diagnostic reports missing evidence coverage.
PC_GATEWAY_EVIDENCE=off ./gateway/target/release/pc-gateway
```

The application composes `JsonEvidenceSink`, not the unbounded `BufferedEvidenceSink` retained for tests/demos.
It keeps at most **1024 queued records plus one worker-owned record**. Each accepted record has bounded identifiers,
typed counts/enums and at most 4096 serialized bytes including the newline. Submission uses `try_send`:
when full, the new record is discarded. There is no request-side I/O, writer join or drain wait.
A stalled stdout can occupy the one exporter thread but cannot accumulate more threads or queue entries.
The rest of the Gateway still has its own request/response buffers and concurrency limits.

Every readable line has `schema`, `operation=gateway.model_call`, `unit` (`call`, `attempt`, `delivery`),
`event` (the same three values), `evidence`, and `loss`. The process-local cumulative `loss.dropped`,
`loss.write_failed` and `loss.written` count discarded submissions, failed/unconfirmed record writes, and completed
write-and-flush operations respectively, sampled before that line's write. Recovered output reports earlier losses;
an embedding host can also read `JsonEvidenceSink.stats()` during congestion. No execution IDs are metric labels.
These are best-effort records: overflow can remove a call terminal or an attempt, a failed/partial write can corrupt
one line, and process exit can lose queued records. Readers should skip malformed/blank lines; after a write failure,
the exporter restores a newline boundary before the next record. Counters reset on restart. There is no replay or
billing/audit durability guarantee, and no financial reconciliation, token savings or task-success claim.
If the worker cannot start, the application reports a fixed diagnostic and uses Noop without changing business results.

## Correlation and coverage

`call_id` identifies one execution and joins its `call`, numbered `attempt`, and `delivery` records.
It is generated once in request control and reused through completion and response-body lifetime.
`request_id_fingerprint` is a keyed process-local fingerprint of the **bound** request ID, or null when none exists;
`target_fingerprint` fingerprints the actual transport URL, never the requested model or client-claimed target.
Redirects remain disabled. Fingerprints use a random process-local key and are not portable identities or a
cryptographic anonymization promise. They never export a raw URL, request ID, cache key, conversation ID,
Scope, provider error, model, credential, prompt or response body. Records do not establish task identity or
Source projection coverage. No task association is available unless the embedding host independently supplies it.

Coverage starts when `Executor::run` is polled. Authentication, invalid-body, middleware rejection and other ingress
failures before execution are outside this stream. A complete model response does not imply successful release.
`attempts` counts actual transport handoffs, including a degraded send; an expired admission has zero attempts.
Retries keep one `call_id` and increment attempt numbers only after fresh admission. An uncertain attempt is never
retried. The legacy demo-only `record_not_sent(0, ...)` diagnostics for preparation/capture/degradation are not
exported and are not model-send facts.

## Terminal and send facts

The `call` record contains `terminal`, `sending`, `attempts`, `upstream_status`, normalized `usage`, optional
`interruption`, and timing fields. Terminal values are `release_authorized`, `blocked`, `withheld`,
`send_indeterminate`, `not_sent`, `gate_paused`, `degraded`, `admission_expired`, and `abandoned`.
`sending` independently reports `no_send`, `definitely_not_sent`, `possibly_sent`, or `response_received`.
A release refusal or expiry after a response preserves `response_received` and any reported usage.

Pending execution future destruction emits `abandoned` and preserves sending facts. `interruption` reports
`cancellation_requested` if the shared cancellation signal was set, otherwise `deadline_elapsed` if the host deadline
has elapsed, otherwise `future_dropped`. These are observations at destruction, **not a claim about its cause**.
As in the shared G3-0 lifecycle, signaling cancellation or passing a deadline does not by itself interrupt execution;
the owner must stop/drop the future. Evidence adds no new cancellation authority or timer.
Cancellation during transport remains `possibly_sent`, with unknown usage, never `not_sent`.

Each `attempt` contains `attempt`, `outcome`, `started_ms`, `duration_ms`, `upstream_status`, normalized `usage`,
`response_first_byte_ms`, and `response_complete_ms`. Outcomes are `possibly_sent` (interrupted pending send),
`delivered`, `upstream_error` (complete HTTP response with status >= 400), `connect_refused`, `rejected_locally`,
`lost_after_handoff`, `timeout_awaiting_response`, and `buffer_limit_exceeded`. A partial-response failure preserves
its observed status/first-byte timing but keeps completion and usage unknown. Once the transport reports delivery,
usage survives decoding/release failures. A dropped pending transport cannot provide its private partial observations.

## Usage mapping

`usage_mapping` is `open_ai_chat` or `open_ai_responses`, selected from the actual wire renderer, not the client model. An unrecognized renderer has null mapping and unknown usage.
Each of `usage.input`, `usage.output`, `usage.cache_read`, and `usage.cache_write` has `tokens` (nonnegative integer
or null) and `completeness` (`known`, `estimated`, `unknown`, `reconciled`). This implementation emits only
provider-reported `known` values or `unknown`; it performs no estimates or reconciliation.

| Quantity | OpenAI Chat | OpenAI Responses |
| --- | --- | --- |
| Input | `usage.prompt_tokens` | `usage.input_tokens` |
| Output | `usage.completion_tokens` | `usage.output_tokens` |
| Cache read | `usage.prompt_tokens_details.cached_tokens` | `usage.input_tokens_details.cached_tokens` |
| Cache write | Unknown | Unknown |

Input **includes** cached input: do not add cache reads to input or subtract them from the reported input field.
No Anthropic cache-creation/write fields are guessed into the OpenAI mapping. Missing, null, malformed or negative
counts remain unknown; a reported zero is known zero. `total_tokens` alone cannot establish input/output counts.
SSE uses an explicitly present usage object from a Chat usage chunk or Responses event's `response.usage`.
It retains the last such object, without summing cumulative chunks. Missing/malformed SSE usage remains unknown.
This does not expand the Core's accepted streaming protocols.

## Timing and HTTP delivery

All durations are monotonic milliseconds, truncated to integers. Null means unavailable.

| Field/boundary | Meaning |
| --- | --- |
| Call `duration_ms` | Request-control creation to executor terminal/drop; includes ingress time after control creation |
| Attempt `started_ms` | Request-control creation to transport handoff |
| Attempt `duration_ms` | Handoff to transport result or future destruction |
| `response_first_byte_ms` | Transport send start, **before** awaiting headers, to first nonempty HTTP body chunk |
| `response_complete_ms` | Same transport origin to complete buffered response |
| `upstream_ttft_ms` | Always null: HTTP JSON/SSE bytes do not establish a model's first token |
| `release_decision_ms` | Request-control creation to before-release check result, including error/refusal |
| `release_authorized_ms` | Request-control creation to validated, unexpired release after optional capture |
| Delivery `elapsed_ms` | Request-control creation to the named framework observation |

A `delivery` record reports `framework_handoff` when the released/degraded body is placed in the HTTP response,
`first_body_frame` when the framework first polls a nonempty data frame, `body_completed` when the wrapped body
reports completion, or `body_dropped` if dropped before observed completion. Strict release authorization precedes
framework handoff and content-frame observation. A degraded response has no release authorization timestamp.
No boundary proves a socket write, client receipt, rendering or adoption. The current host cannot observe actual
socket writes. Non-streaming Responses JSON's first byte and replayed SSE output are not provider TTFT.

## Deployment and compatibility

Collectors that previously consumed human tracing from stdout must move that input to stderr and parse evidence
stdout separately. No database migration, new HTTP endpoint, Python operational field or OpenAPI regeneration is
required. The JSON schema is separate from the Server's allowlisted Python log fields and existing exporters.
Custom embedding sinks must keep synchronous callbacks bounded, nonblocking and free of I/O; route blocking writers
through `JsonEvidenceSink` and keep one sink per host. Custom writers must return I/O errors without panicking.
Rust integrations consume the nested per-field `CallEvidence.usage` instead of the demo record's former flat token/completeness fields. There is no production record-store migration. They must replace `RawResponse.ttft_ms` with the explicit first-byte/completion fields and handle
`SendOutcome::IncompleteResponse` as indeterminate, preserving its progress and never retrying it.
