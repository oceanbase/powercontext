- Proposal Name: `distributed_server_workers`
- Start Date: 2026-09-03
- Tracking Issue: [oceanbase/powercontext#1430](https://github.com/oceanbase/powercontext/issues/1430)
- Related RFCs: [RFC 0011](0011_remote_access_architecture.md), [RFC 0019](0019_local_source_memory_runtime.md),
  [RFC 0020](0020_runtime_backed_memory_remote_access.md), and [RFC 0046](0046_observability_foundations.md)

# Summary

PowerContext uses one database-backed Work Ledger for both local and distributed background execution. An API replica
can enqueue and inspect work without process affinity. Schedulers elect one fenced leader while retaining durable
keyset scan positions. Workers claim only available capacity, renew database-time leases, and safely recover abandoned
attempts. Model calls remain at-least-once, while one logical Source window can commit at most one database result.

OceanBase is the only coordination backend in distributed mode. The same ledger also replaces the APScheduler SQLite
sidecar in `single_node/all`, so there is one execution protocol to understand and test. Redis, Kafka, a public queue
SPI, arbitrary user code, DAG scheduling, priorities, multi-region consensus, and connector-specific checkpoints are
outside this RFC.

# Decisions

The design deliberately minimizes sources of truth:

- OceanBase owns work state, leases, cursors, and domain commits in distributed mode.
- Execution is at-least-once. Provider work may repeat after a crash, but fencing and the domain cursor prevent an old
  attempt from committing.
- Memory activation and Experience incubation use the ledger first. Future handlers keep their domain checkpoints in
  their own modules.
- The default remains `single_node/all`; distributed processes use exactly one of `api`, `scheduler`, or `worker`.
- Work payloads are versioned references and numeric window boundaries, not Source bodies, prompts, model responses,
  credentials, or exception text.

This avoids entropy from parallel database, broker, and local-sidecar protocols. The compensating complexity is made
explicit as database constraints, a small state machine, fencing tokens, startup validation, and compatibility
advertisements rather than tacit assumptions about one process.

# Architecture

```text
API / Scheduler
      |  short transaction: identify window, deduplicate, enqueue
      v
OceanBase Work Ledger
      |  short transaction: claim lane head and issue lease fence
      v
Worker ---- provider/model call outside a transaction ---->
      |  short transaction: validate fence, commit domain state + cursor + work
      v
one logical database result
```

The ledger consists of:

| Table | Responsibility |
| --- | --- |
| `pc_work_items` | Versioned work, scope, lane sequence, safe payload, state, lease, attempt budget, and safe result/error |
| `pc_work_attempts` | Append-only owner/fence/timing/outcome audit and non-sensitive trace identity |
| `pc_work_lanes` | Strict sequence and one active head for each consistency domain |
| `pc_work_keys` | Unique reservation for the current logical window |
| `pc_scheduler_leases` | Leader and migrator ownership with monotonic fences and database-time expiry |
| `pc_scheduler_scans` | Next run time and keyset continuation for each discoverer |
| `pc_runtime_members` | Live role and schema/payload/behavior compatibility advertisements |
| `pc_rate_limit_windows` | Optional shared fixed-window counters keyed by an opaque principal digest and policy |

The logical key for a Memory window is the SHA-256 digest of its handler kind, scope, cursor name, cursor generation,
and previous cursor. The enqueue-time high watermark and `through` boundary are stored in the work payload but are not
part of that key. Manual and scheduled discovery therefore join the same unadvanced cursor window; Sources arriving
later are handled by the next window.

Memory and Experience use independent lanes (`memory:{scope}` and `experience:{scope}` before hashing). Different lanes
may execute concurrently, while a lane advances strictly in sequence. A terminal failure retains its logical-key and
lane reservation until an operator retries or cancels it, preventing a poison window from being recreated forever.

# State machine

```text
queued ----> running ----> succeeded
  |             |----> retry_wait ----> running
  |             |----> failed
  |             `----> cancelling ----> cancelled
  `-----------------------------------> cancelled

failed -- explicit operator retry --> queued
```

Cancellation linearizes at the work row. If cancellation wins before the final transaction, the Worker discards the
prepared provider result and completes the attempt as cancelled. If success has already committed, cancellation returns
`409`. Cancelling one operation does not pause future discovery for its scope.

# Lock and transaction protocol

OceanBase 4.3.5 with Read Committed isolation is the baseline. Correctness does not depend on an unlocked read, a
single predicate update, `SKIP LOCKED`, or `GET_LOCK()`. Every path follows this order:

```text
scheduler lease (scheduler paths only)
  -> lane
  -> logical key
  -> work item
  -> domain cursor/head
```

Stable lease and lane rows are explicitly locked. A missing row is inserted under a unique key and the operation is
retried after a conflict. Claiming starts with a bounded indexed candidate scan, then locks and claims one lane at a
time in a short transaction. Workers never prefetch more work than their free execution slots.

Scheduler enqueue and scan updates revalidate the exact leader owner, fence, and database expiry. Worker heartbeats,
failures, cancellation convergence, and completion validate the exact `(work_id, owner, fence)`. All expiry decisions
use `CURRENT_TIMESTAMP(6)` from the database; application time is used only for polling and deadlines.

Provider, connector, network, and filesystem calls never run under a database transaction. The final Worker
transaction validates an unexpired, uncancelled claim and then invokes the handler's domain commit. Memory and
Experience reuse their existing cursor/head compare-and-swap. Domain writes, cursor advancement, attempt completion,
and Work success either commit together or roll back together.

# Scheduler and Worker behavior

Each discoverer stores a durable keyset continuation and scans at most 100 scopes per page. Repeating a page after a
crash is safe because logical keys deduplicate it. A former leader cannot enqueue or save a continuation after another
Scheduler acquires a higher fence.

A Worker claims only its current free slots. Its attempt lease defaults to 120 seconds and is renewed every 30 seconds.
Expired attempts are closed in the audit trail, moved through retry state, and reclaimed with a higher fence. Retryable
failures use full-jitter exponential backoff, beginning at 2 seconds and capped at 5 minutes. Five automatic generation
attempts are allowed by default. Unsupported payload versions stay visible at the lane head and make Worker readiness
`misconfigured`; they are never guessed, silently dropped, or moved aside.

# Process roles

Configuration adds `deployment`, `coordination`, `worker`, `operations`, and `rate_limit` groups.

- `single_node/all` runs API, Scheduler, and Worker together using the same ledger. A database ownership lease rejects a
  mistakenly started second instance.
- `distributed/api` exposes HTTP, Dashboard, MCP, authentication, shared rate limiting, enqueue, and operation queries.
  It does not run discovery or background Worker handlers.
- `distributed/scheduler` exposes only health and metrics, owns no provider credentials, and performs bounded discovery.
- `distributed/worker` exposes only health and metrics and owns only the provider credentials required by its handlers.

Distributed mode requires OceanBase and rejects `role=all`, SQLite, seekDB, and explicit host-local External Skill
targets. All roles use one image but should use separate least-privilege database accounts. DDL belongs to a migrator
account and is never performed automatically by a distributed role.

Every process heartbeats a boot-unique member identity with build version, current schema and payload range, and a
non-sensitive `behavior_revision`. Credentials, secret URLs, authorization data, and hashes that permit offline secret
guessing are not member metadata.

# HTTP and Client contract

`openapi/powercontext.yaml` remains authoritative. `POST /v1/memory/flush` behaves as follows:

- no pending Source, or completion within the wait budget: `200 FlushMemoryResponse`;
- still queued, running, or waiting to retry: `202 OperationAccepted`, a relative `Location`, and `Retry-After: 2`;
- a failed operation already blocks that logical key: `409 operation_blocked` with its operation ID.

`Prefer: respond-async` selects an immediate handle. `Prefer: wait=N` selects a bounded wait up to 30 seconds; the
default is 10 seconds. Operation endpoints provide authorized get/list, optimistic cancel, and operator retry. Mutation
bodies carry `expected_version`; illegal or stale transitions return `409`. Public status values are `queued`,
`running`, `retry_wait`, `cancelling`, `succeeded`, `failed`, and `cancelled`. Internal maintenance work is not exposed by
the Operation API.

`PowerContextClient.flush_memory()` preserves its synchronous-looking result by submitting and polling until its total
deadline. If that deadline expires it raises `OperationPendingError` with the operation ID. Callers that want explicit
control use `submit_memory_flush()`, `get_operation()`, `list_operations()`, `cancel_operation()`, and
`retry_operation()`.

Operation endpoints are not projected as MCP tools. In distributed mode FastMCP uses stateless HTTP, so consecutive
requests may reach different API replicas. Server-to-client elicitation and sampling are disabled; the Workstream picker
returns a structured `needs_selection` response. The internal ASGI bridge carries the already authenticated principal,
and tool visibility is never treated as authorization.

# Health, shutdown, retention, and observability

Liveness means the process can respond. API readiness requires database, schema, authentication policy, membership,
and behavior compatibility. Missing Scheduler or Worker members make API readiness degraded rather than preventing
durable enqueue and reads. A healthy Scheduler standby is ready. A Worker that lacks a required provider or handler
version stops claiming and reports the precise failing check.

On SIGTERM, API readiness is removed first. Scheduler stops discovery and conditionally releases its lease. Worker
stops claiming, continues heartbeats for in-flight work, and drains for at most 90 seconds; an ungraceful exit recovers
through lease expiry.

Succeeded and cancelled work and attempts are retained for 30 days. A durable maintenance handler deletes no more than
500 records per batch and also removes expired rate-limit counters. Failed blocking work is retained until operator
action. Future Source retention must treat every non-terminal work window as a retention root.

Metrics use only bounded labels such as kind, status, outcome, role, and error category. They cover queue depth and age,
claim and attempt latency, lease expiry, retries, throughput, leadership changes, and member counts. Scope, principal,
and work IDs are not metric labels. Enqueue, claim, execute, commit, and retry are separate spans; retry attempts use span
links rather than pretending to be one uninterrupted trace.

Logs, spans, work rows, and attempt rows must never contain Source content, prompts, model output, credentials,
authorization headers, or complete secret URLs. Persistent errors use bounded category and code values.

# Schema and rollout

Alembic owns a forward-only schema chain. `powercontext server migrate` acquires a database lease and upgrades through
the packaged head. A new database starts at the baseline; a known complete legacy schema is validated, receives only
recognized expansions, and is stamped before the ledger migration. Unknown or partially installed schemas are rejected.
Single-node startup runs the same migration path automatically; distributed roles only validate the current revision.

Schema evolution follows expand, mixed-version deployment, then contract. Release N+1 must read N and N+1 layouts, and
destructive removal waits until N+2. Workers support the current and previous payload during an actual mixed-version
transition. `emit_payload_version` stays on the old format until old Workers drain.

The deployment sequence is migrate, Workers, Schedulers, then APIs. Rollback reverses that order and drains any newer
payload before an older Worker is restored. The bridge release first puts `single_node/all` on the Work Ledger; only
after the APScheduler execution path is absent may the same database be served by multiple roles. The old sidecar is an
operator backup artifact and is not deleted automatically.

# Validation

Acceptance requires round-robin HTTP, Dashboard API, and stateless MCP tests across two API replicas; overlapping work
on different lanes and serialization on one lane; manual/scheduled deduplication; Worker crash points before claim,
during provider execution, during final commit, and after commit; Scheduler takeover fencing; retry, cancellation,
operator recovery, retention, and privacy tests; and real multi-process OceanBase 4.3.5 tests. Golden schema/payload
fixtures cover mixed versions and upgrade/rollback. SQLite and seekDB remain supported only for single-node regression
tests.

# Alternatives rejected

- Redis or Kafka would add another durable truth and a cross-system commit problem before throughput requires it.
- A public Queue/Coordinator SPI would freeze multiple coordination semantics before any second implementation exists.
- `GET_LOCK()` is session-scoped and unsafe as a pooled-connection fencing primitive.
- Keeping APScheduler for local mode would preserve two subtly different execution and recovery protocols.
- Exactly-once provider execution is not achievable across external calls; the enforceable contract is at-least-once
  execution with at-most-one fenced database commit.
