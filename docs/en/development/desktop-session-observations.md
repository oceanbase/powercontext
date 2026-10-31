---
title: Desktop session observations
description: Native request and user-operation measurements, bounded retention, and safe diagnostic projection.
---

# Desktop session observations

Desktop owns an in-memory observation window under [#2107](https://github.com/oceanbase/powercontext/issues/2107).
Collection starts with the native host and does not depend on opening Observation. The typed native commands
`session_summary`, `reset_session_observations`, and `preview_session_diagnostics` require the trusted main window
and its capability. They remain available without an active connection or usable profile storage. They do not issue
HTTP requests, probe readiness, launch diagnostics, write the clipboard, or upload anything. The This session page
and explicit preview/copy interaction belong to the separate UI delivery; the page remains unavailable until integrated.

## Measurement units and coverage

Every record declares `unit` and the owning operation ID in `operation`.

| Unit | Counted work | Duration in milliseconds |
| --- | --- | --- |
| `http_request` | One request handed to the native HTTP client | Monotonic time from dispatch through bounded response reading and JSON decoding, or failure/cancellation |
| `user_operation` | One native Memory, Scope or statistics action invocation, including rejected invocations | Monotonic time from entering the native action through context/identity checks, response validation and result construction, or failure/cancellation |

HTTP coverage includes `get_server_info`, `get_liveness`, `get_readiness`, `get_capabilities`,
`get_access_principal`, `list_scopes`, `get_scope`, `get_default_scope`, `get_stats`, `remember_memory`, `search_memory`, and `get_artifact_revision`. Native connection
checks contribute their individual HTTP requests, not an additional user operation. Identity revalidation and its
existing anonymous-access readiness fallback also contribute HTTP requests. A user action can therefore issue multiple
HTTP requests. The summary's `coverage.httpOperations` and `coverage.identityChecksIncluded` disclose that coverage.

User-operation coverage is the seven Memory/Scope/statistics operations listed in `coverage.userOperations`: `list_scopes`,
`get_scope`, `get_default_scope`, `get_stats`, `remember_memory`, `search_memory`, and `get_artifact_revision`. It does not include
profile editing, connection checks, renderer debounce, UI confirmation time, or clipboard actions. Native pre-dispatch
validation/concurrency refusals can produce a failed user operation without an HTTP record. HTTP latency is client
request time, not Server processing time; user-operation latency is native action time, not the entire UI journey.

`coverage.desktopSessionOnly` is true and `coverage.persistenceEvidence` is false. This is this Desktop process's
observation, not global Server/Agent tracing or authoritative evidence of persistence. Installer/Ops and local CLI
diagnostics are not collected (`coverage.installerOps` is false). A future local-setup section must project the owning
Installer/Ops phase/result and supported opaque operation reference. It must not import stdout, paths, configuration,
or journal contents, create another journal, or include local lifecycle work in HTTP latency.

## Outcomes

`outcome` is `success`, `failure`, `cancelled`, or `unknown`. HTTP success means a 200 response was read and decoded;
operation-specific validation can still fail afterward. Readiness 503 remains an HTTP failure with
`errorCode: runtime_not_ready`, even when the adapter successfully returns a structured not-ready fact. A cancelled
HTTP future records `cancelled`; it does not imply that a remote write was cancelled.

Read user operations use their native return result. `stale_context` is classified as cancellation, including an
invocation made with an obsolete context. Saving uses the existing `WriteRecord.status`, not whether the outer
`Result` is `Ok`. A successful save with zero records remains successful. Explicit write rejections remain failures;
dispatch followed by a network failure, 500, invalid receipt, or interruption can leave the user operation `unknown`.
A decoded HTTP 200 can coexist with an unknown save if its receipt fails operation-specific validation. Dropping a
save after entering the existing write dispatch boundary preserves unknown semantics. Observation never retargets,
replays, retries, cancels, or confirms a write.

## Retention, reset and cost

The shared window retains at most **512 terminal records**, across both units and all connections/Scopes, in native
completion order. Pending work is absent until completion or cancellation. Each insertion has constant bounded work;
an overflow evicts the oldest record. Summaries copy and aggregate at most 512 records, with groups bounded by the
declared unit/operation vocabulary. There is no history scan, persistence, background task, external exporter, or
per-context collection. Closing Desktop discards all observations; reopening creates an empty session.

Reset clears retained records, groups and the eviction count and moves the window start. It does not reset the
Desktop session start, affect business state, or cancel work. Guards created before reset cannot repopulate the new
window. When they finish, each excluded HTTP request or user operation increments `droppedSinceStart`.
Requests started after reset can appear even if their enclosing user operation began earlier.

Connection, Principal and Scope transitions keep anonymous process-wide observations. No record stores target
identity, and no historical or late result is assigned to a newly selected target. Existing private-context invalidation,
read cancellation and immutable dispatched-write targeting remain owned by the connection manager.

Recording uses a nonblocking lock and no I/O. Contention or a poisoned collector drops the observation without
changing business results or suppressing cancellation. `droppedSinceStart` counts such losses over the process
lifetime, including pre-reset observations excluded on completion, and intentionally survives reset, so resets cannot
conceal incomplete coverage. Snapshot/reset contention returns `busy`; a poisoned collector returns `storage`, rather than fabricating an empty successful summary.

## Safe projection contract

`SessionSummary` is an explicit Rust projection; it never serializes `DesktopState`, `WriteRecord`, raw requests,
responses or exceptions. The native preview serializes only this projection as pretty JSON. Structured and text
outputs use the same allowlist; no renderer redaction is required for safety.

| Fields | Meaning and units |
| --- | --- |
| `schemaVersion` | Safe-summary schema version, currently 1 |
| `sessionStartedAtMs`, `windowStartedAtMs`, `generatedAtMs` | UTC Unix epoch milliseconds: process session start, last reset/start, snapshot generation |
| `capacity`, `evicted`, `droppedSinceStart` | Maximum retained terminal records, evictions since reset, recording losses since process start |
| `coverage` | Declared native coverage and limits described above |
| `recent` | Retained records in completion order; not a complete session history after eviction or loss |
| `unit`, `operation`, `outcome` | Fixed measurement unit, allowlisted operation ID and result enum |
| `completedAtMs`, `durationMs` | UTC completion timestamp in epoch milliseconds and nonnegative monotonic elapsed milliseconds |
| `httpStatus`, `errorCode` | Observed HTTP status if received, and existing `SafeError` enum if applicable; otherwise null |
| `operations` | Groups by unit and operation, computed only from `recent` |
| `outcomes`, `sampleCount` | Retained terminal counts by outcome and total sample count |
| `minDurationMs`, `meanDurationMs`, `maxDurationMs` | Elapsed milliseconds across all retained outcomes in that group, including cancelled and unknown work |

The collector accepts no free-form diagnostic fields. Memory/Source content, queries, prompts/responses, credentials,
raw endpoints, profile names/IDs, Principal/Scope/Artifact IDs, private paths, request headers, raw errors and installer
output never enter records. Server request IDs remain available through existing operation failures but are omitted
from this export. It creates no correlation IDs or execution-identity metric labels and configures no operational logs,
metrics, spans or telemetry backend.

Native regression checks use synthetic loopback HTTP fixtures and mocked Tauri IPC. They protect bounded retention,
reset with in-flight writes, request/action counting, readiness and write outcomes, cancellation, sensitive-value
exclusion, permissions and collector failure isolation. They do not qualify a real Server or installed clipboard.
