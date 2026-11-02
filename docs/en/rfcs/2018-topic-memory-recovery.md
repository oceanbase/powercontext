---
title: Topic Memory blocked processing recovery
---

- Proposal Name: `topic_memory_blocked_processing_recovery`
- Start Date: 2026-10-15
- RFC PR: [oceanbase/powercontext#2018](https://github.com/oceanbase/powercontext/pull/2018)
- Tracking Issue: [#1885](https://github.com/oceanbase/powercontext/issues/1885)
- Implementation PR: [#1966](https://github.com/oceanbase/powercontext/pull/1966); conformance is verified against this design before merge.
- Amends: [RFC 1417](1417_topic_memory.md), [RFC 1515](1515_artifact_processing_supervisor.md)
- Related: [RFC 1549](1549_artifact_family_unification.md), [RFC 1771](1771-unified-database-migrations.md)

# Summary

Provide Scope-authorized inspection and explicit, bounded retry of blocked Topic Memory processing. Reuse the existing
work budget for the current attempt, processing intents for scheduling, and leases for fencing. Add one Topic-owned
recovery table containing a snapshot only when a frontier becomes blocked; store its retry acceptance in the same row.
Successful and superseded attempts do not become permanent execution history. Retain block snapshots and accepted
receipts without automatic expiry so a duplicate request cannot grant another allowance after restart or cleanup.

# Motivation

A failed Topic window can exhaust its allowance while later Sources remain behind the same Cursor. Stopping repeated
dispatch prevents wasted work, but operators also need to diagnose and recover that frontier without editing the
database. Recovery must preserve evidence and make the extra model cost an explicit administrative decision.

The necessary durable evidence is the blocked state and whether an administrator already authorized another allowance.
That requirement does not by itself justify retaining every attempted window, including ordinary successful work.
This proposal decides the storage lifecycle, ownership, retention, and reuse boundary before implementation.

# Guide-level explanation

An operator reads processing status for a Scope. The response reports the Cursor, Source head, retained target, and a
block when processing cannot continue. An active final attempt remains pending because it may still publish.

The operator corrects the cause, then submits the observed `block_id` to retry. Acceptance means one fresh bounded
allowance was granted and processing was requested; it does not promise generation success. Sources, Cursor, and target
remain intact. Each allowance keeps the existing ceilings of three attempts, 512 requests, and 64,000,000 reserved tokens.

If the response is lost, resending the same ID returns the original acceptance. The same applies after restart,
subsequent processing, another block, or disabling processing in an offline configuration change. An unretried block
that is no longer current conflicts. Ordinary flushes, reads, restarts, and model changes never grant new allowance.

Inspection requires `scope.read`; retry, including replay, requires `scope.admin`. Records contain controlled metadata,
not Source bodies, prompts, model output, credentials, or arbitrary exception messages.

# Reference-level explanation

## Ownership and storage reuse

The source inventory is `master` at `a1ed8553149ced3e15756642905a8ea315a3a4a8`.

| Existing storage | Lifecycle and reuse decision |
| --- | --- |
| `pc_topic_memory_work_budgets` | Authoritative allowance at a Cursor; updated during work and deleted on success or accepted retry. Extend it with current-attempt diagnostics, but do not retain old budgets as history. |
| Processing pending/intents | Mutable scheduling state and generation counters. Reuse them to request work in the acceptance transaction; they cannot identify an arbitrary historical block's acceptance after later requests. |
| Processing leases | Replaced or renewed operational ownership. Reuse them to validate publication rights; they cannot supply immutable historical diagnosis or receipts. |
| Artifact revisions and heads | Can participate in the same transaction. Reuse is feasible, but requires an additional internal Family and lifecycle restrictions described below. |

`ArtifactRepository.create` accepts a caller-owned connection, so atomicity is not a reason to rule out Artifact storage.
An Artifact design could use `block_id` as its identity and revision/head CAS to record acceptance with budget reset and
intent creation. It must also register a typed content model; prevent generic writes, publication, merge, and lifecycle
operations from changing the recovery record; enforce Scope inspection/admin permissions; and retain every exact
revision needed for replay through cleanup and backup. Reusing a publicly writable Family or deleting its head must
not erase authorization evidence.

The concrete lifecycle is create-on-block → exact read → accept retry once → exact read/replay indefinitely. Both
storage options can join the budget-reset/intent transaction; neither gains an atomicity advantage.

| Concern | Private Artifact Family | Topic-owned block table |
| --- | --- | --- |
| Identity and acceptance | Use `block_id` as Artifact ID and revision/head CAS; preserve the accepted revision and current head. | One stable primary key and a one-way nullable acceptance pair. |
| Permissions | Register a private typed Family; prevent generic writes, publication, merge and lifecycle changes; expose only Scope-authorized recovery operations. | The recovery service owns reads and mutations, with `scope.read` and `scope.admin` at its boundary. |
| Atomicity | Caller-owned repository connections commit acceptance, budget reset and intent creation together. | The same connection commits the block update, budget reset and intent creation. |
| Cleanup | Exempt the required head and exact revisions from generic deletion, compaction and Family cleanup. | No expiry or normal delete operation; back up the row with processing state. |
| Maintenance | Maintain a private content schema, capability restrictions and revision retention for a record without publication, merge or retrieval needs. | Maintain one frozen relational format and offline conversion; no additional Artifact capabilities or revision lifecycle. |

Choose the dedicated table: this one-way operational transition needs fewer independent lifecycle rules. This is an
overall complexity decision, not a claim that Artifact transactions or access control cannot support recovery. Current
execution metadata stays on the existing budget; successful and superseded attempts create no retained history.

Topic Memory owns Source ranges, terminality, allowances, and request/token ceilings. The Supervisor owns execution,
fencing, and controlled Worker failure classification; a bounded metadata callback lets the domain retain a stopped
Worker's diagnosis without granting allowance. No shared execution-history table or generic job framework is introduced.
A future shared record needs a second concrete workflow with the same identity, retry, and retention contract.

## Mutable current attempt

Extend the existing budget row with nullable metadata for the latest admitted attempt: selected Source end, start and
finish observations, execution holder/group/generation, and controlled last error. Keep the existing `attempt_id` and
aggregate attempt/request/token counters authoritative. Initialize the current metadata in the same transaction that
admits an attempt; update it only when the attempt identity still matches. A later attempt supersedes that metadata.
Success deletes it with the budget in the atomic publication/Cursor transaction.

A stopped Worker's controlled failure must settle the matching current attempt before its terminal snapshot is frozen,
in the same transaction when both occur together. Late callbacks must not rewrite a replacement attempt or immutable
block evidence. If a crash leaves the category genuinely unknown, retain null instead of inferring a timeout.

The budget's existing `source_through` remains cumulative coverage. It must never substitute for the selected end of
the last attempt. For example, selected ends `2, 1, 1` mean the final attempted range is `(0, 1]`, while cumulative
coverage can remain `2` and the retained target `3`.

## Durable block and receipt

Add `pc_topic_memory_work_blocks` with one row per distinct terminal block, not per attempt:

| Field group | Required evidence |
| --- | --- |
| Identity | Server-generated `block_id`; `scope_id`, `binding_name`, Cursor `source_after`, and final `attempt_id`. |
| Range and target | Nullable exact selected `source_through`; separate nullable `budget_source_through` cumulative coverage; nullable `legacy_source_through` preserving an ambiguous imported value; `target_source_through` captured from the retained target at block observation. |
| Diagnosis | Reserved `attempts`, `requests`, `tokens`; terminal `error_code`; nullable controlled `last_error_code`; first `blocked_at` observation. |
| Acceptance | Initially null `retried_at` and `retry_request_generation`; set together exactly once on acceptance. |

Use `block_id` as the primary key and a unique key on `(scope_id, binding_name, source_after, attempt_id)` to make repeated
observation converge on one identity. Constraints validate ranges and nonnegative usage without requiring a fabricated
selected end for legacy data. Block evidence is immutable after creation; only the acceptance pair transitions once.
The same row represents both the diagnosed block and the durable receipt, so receipt persistence needs no second table.

Observe current Cursor, budget, and target under the publication-compatible lock order. Only the dispatcher that reaped
that exact Worker, or the failed Worker itself, can attest that it stopped. Other callers check current lease/fence
ownership before finalizing an unfinished attempt; use locking current reads on MySQL, not a stale repeatable-read
snapshot. A live final attempt stays pending. An obsolete owner cannot regain publication rights by racing block
observation or retry.

When observation needs both locks, acquire lease before Source head, then reread Cursor/budget under the head lock. If
the attempt changed meanwhile, leave it pending and retry observation later; do not acquire a replacement lease lock
under the head lock. Retry consumes only a finalized block whose attempt still matches, so it does not reverse that
lock order to rediscover a live owner.

The first observation can persist a metadata snapshot, including from a status read; it grants no allowance and requests
no work. Snapshot creation never materializes Source content or calls a model. Its target is an observation-time fact,
not an invented target at an unknown historical failure time.

## Retry transaction and identity

After authorization, serialize on the Scope's existing Source-head write lock and load the exact Scope/binding/block:

1. If acceptance already exists, return it before checking today's budget or processing configuration. Make no writes.
2. Otherwise require the same authoritative Cursor and final attempt, an exhausted budget, available processing, and
   no live attempt that can publish. An obsolete unretried block conflicts; an unknown or cross-Scope ID is not found.
3. In one transaction, remove that exact exhausted budget, request pending work and an intent using existing repositories,
   and set the block's acceptance time and accepted request generation. Preserve Sources, Cursor, and retained target.

Rollback restores all of these changes. A crash after commit but before response is covered by step 1. Concurrent callers
for the same block receive one acceptance; a later block needs its own observed ID. A caller-supplied unknown ID never
creates a block or grants allowance. Retry does not refund ambiguous or failed work in an existing allowance.

## Public contract

Add HTTP operations for processing status, exact block read, and retry, with corresponding Python Client SDK and CLI
operations. Proposed HTTP paths are `/v1/topic-memory/processing/status`, `/v1/topic-memory/processing/blocks/get`, and
`/v1/topic-memory/processing/retry`, all using POST. The MCP tool catalog is unchanged.

Status exposes current Cursor/head/target and `idle`, `pending`, or `blocked`. Block read returns the stable snapshot and
acceptance; retry returns that same accepted record. Use 409 for a stale unretried block and 404 for an unknown or
cross-Scope block after authorization. Replaying an accepted retry must work even if processing is now disabled.

`source_through` is null when the exact selected end is unknown. `budget_source_through` reports recorded cumulative
coverage; it is non-null for new blocks and may be null only for imported historical blocks whose allowance is no longer
available. `legacy_source_through` is null for new blocks and preserves the original ambiguous range of imported
attempt-table blocks. It asserts neither an exact selected end nor cumulative coverage. Clients must distinguish these
fields and support null. Never infer an old allowance from today's budget or attempt history. OpenAPI owns the precise
wire schema; regenerate HTTP models, SDK/host operation metadata, and desktop types together in the implementation.

## Retention, cleanup, and storage growth

Permanent exact block lookup and replay of the original accepted retry are product guarantees within the retained
database, including after progress, restart and disabled processing. Returning 404 for an expired accepted ID prevents
a second allowance but breaks this guarantee; this RFC deliberately chooses durable replay and its storage commitment.
Retain every block snapshot and acceptance indefinitely within the retained database. No TTL, per-attempt history,
successful-attempt history, bulk purge, or automatic receipt compaction is introduced. Unaccepted blocks also remain
readable after becoming stale. A backup/restore must preserve budgets, blocks/receipts, Cursor, targets, and processing
state consistently. Partial restore or record deletion is not a supported recovery operation.

This retention is deliberately unbounded by time: its cost grows with distinct blocks, including those after repeated
administrative retries. It does not grow with every successful window. Future cleanup requires an explicit contract for
exact historical reads and receipt replay; silently deleting an acceptance would violate this RFC even if an unknown ID
is subsequently rejected. No cleanup operation may turn an old ID into authorization for another allowance.

Let `W` be processed windows, `A` mean admitted attempts per window, and `B` distinct blocks, including blocks after an
accepted retry. Full attempt history retains roughly `W × A` rows; this design retains `B` rows plus current budget rows.
Reads and mutations address an exact key; no normal observation, acceptance, or completion scans or rewrites history.

A synthetic SQLite 3.50.4 sizing run used the frozen recovery schemas at implementation commit `358b0b59`, 100,000 rows
per layout, 4 KiB pages, 1,000 32-character Scope IDs, the `topic-memory-source-window` binding and UUIDs generated with
seed 2018. Each layout included its indexes: primary/unique keys plus the historical frontier index for attempt history.
The successful-attempt sample populated both timestamps and execution identity; the accepted-block sample populated
both receipt timestamps, three attempts, nine requests, 1,000 tokens, `window_attempt_limit` and `model_timeout`, with
null legacy provenance. Net database growth excluding empty-schema pages was 52,895,744 bytes for the 20-field
attempt layout and 42,901,504 bytes for the final 17-field block/receipt layout: about 529 and 429 bytes per row.
This measures isolated schema storage, not runtime performance, model execution, write latency, OceanBase or seekDB.
Active budget columns, WAL, backups and other tables are excluded; deployments and backend indexes need their own sizing.

As an illustrative workload, not a production observation, one million windows/day, `A = 1.1`, and 10,000 blocks/day
produce 1.1 million history rows versus 10,000 retained block rows per day. Applying those SQLite sample widths gives
about 582 MB versus 4.3 MB/day of additional retained records. Successful-attempt history is therefore an explicit
storage commitment even though a single allowance admits at most three attempts; the per-allowance ceiling is not a
historical retention bound. No backend performance improvement or acceptable deployment capacity is claimed.

## Migration and compatibility

The implementation must deliver explicit, versioned offline migrations under RFC 1771 for budget columns, the recovery
table, constraints, and indexes on every supported backend. Register affected objects, recognized baseline shapes,
verification, and backup coverage. Normal startup must not silently alter an existing database. Empty database
initialization follows the same supported revision chain. This RFC adds no migration code or runtime behavior itself.

The versioned Topic recovery bundle uses the existing `server db-migrate` entry point, with explicit selection of the
`topic-memory-recovery` bundle. Its plan covers only registered domain objects and reports `server_ready=false`; it does
not turn the partial Artifact migration bundle into complete Server readiness. Reuse maintenance connections, target
locking, plan/backup declarations and durable recovery evidence. Normal startup checks this recovery shape before any
business initializer writes. Empty initialization uses the same frozen target revision, without treating an old partial
database as empty.

Empty initialization creates the block table before the budget. SQLite wraps both in an explicit write transaction.
After nontransactional DDL interruption, only that exact empty block-table prefix, with no other business tables except
the known Access bootstrap and independent Code graph storage, can resume without maintenance evidence. These companion
tables may precede initial recovery storage and remain untouched. A prefix containing a receipt or other business state
is rejected; it cannot silently recreate a missing allowance or discard recovery evidence.

Support these finite inputs, verified before mutation:

| Input | Conversion |
| --- | --- |
| Empty recovery storage in an empty database | Initialize the frozen target schema. |
| Recognized pre-recovery budget layout without an attempts table | Preserve budgets and counters; add null current-attempt metadata and an empty block table. |
| Recognized attempts-table layout used by #1966 | Copy every terminal block and acceptance with its exact IDs, counts, error categories, target, first observation, acceptance time and accepted generation. Copy the matching current attempt's known execution metadata into its budget without changing allowance. |
| Target schema | Verify and return a no-op; never reset budgets or remint identities. |
| Unknown columns/constraints/indexes, partial layouts without matching recovery evidence, invalid acceptance pairs or conflicting block identities | Reject before business or migration writes; never repair by dropping evidence or accepting an unknown format. |

The attempts-table layout did not record range provenance; some accepted historical budgets were already deleted.
For converted blocks set exact `source_through` to null and preserve the original value as `legacy_source_through`.
Set `budget_source_through` only from the same still-present allowance with matching frontier and final `attempt_id`,
otherwise null. Never infer it from a later budget, successful history or the maximum of unrelated attempts. This is an
explicit wire-contract conversion: the old number retains its original ambiguity, while retry identity and original
acceptance stay unchanged. Newly admitted attempts always record an exact selected end.

Stop every writer, including Workers, before migration. A backup consistently covers budgets, the old attempts table,
blocks, Sources, Cursor, targets, pending/intents and leases. Verify copied evidence and unchanged budget costs before
removing the old attempts table; successful/superseded rows need no runtime history and remain in the recovery point.
MySQL-compatible DDL can commit independently: persist source/target fingerprints and step evidence outside the database
before DDL, resume only a recognized prefix, and require the same maintenance target and recovery point after interruption.
SQLite commits schema and data conversion atomically. Verification gates reopening business access. Never reconstruct
an absent error/owner/time fact or reinterpret `processing_failed` as a timeout.

Before reopening, revoke every pre-upgrade processing lease using the existing maintenance fence transition: advance
its generation, expire it in UTC, and set a maintenance-window holder. Updating only holders different from that window
makes recovery idempotent. All workflows sharing those leases must be stopped. Preserve known attempt owner metadata
and leave unknown finish times null; a status read must be able to finalize stopped work without waiting for a new
Supervisor, including while processing remains disabled. No pre-upgrade fence may publish after conversion.

Mixed old/new writers and destructive downgrade after accepting retries are unsupported. Any rollback must preserve
accepted authorization evidence; a backup rollback to before acceptance cannot claim continuous idempotency across
that discarded database history. RFC 1417's exclusions of retry/reset APIs and durable Job history are amended only to
permit these management operations and terminal recovery records. RFC 1515 gains the bounded failure-metadata callback;
the Supervisor never resets a Topic budget.

## Acceptance criteria

- Blocking does not restart exhausted work, move Cursor, discard Sources/target, or prevent another Scope's progress.
- Live final attempts stay pending; stopped/revoked attempts retain diagnosis. Worker and model timeouts remain distinct
  through restart; exact selected ranges remain independent of cumulative coverage and target.
- Duplicate and concurrent retries, lost responses, restart, a later block, and disabled processing replay one accepted
  request without granting another allowance; stale, cross-Scope, unknown, and unauthorized requests cannot reset work.
- Success creates no retained execution history. Repeated observation preserves one block ID and immutable evidence;
  acceptance changes only its receipt fields.
- Supported legacy budgets and attempts-table blocks/receipts migrate without resetting costs, changing identities or inventing historical facts; file-backed restart, backup
  restoration, and actual supported backend transactions preserve identities and recovery semantics.
- OpenAPI, Python SDK, CLI, desktop, and advertised host management surfaces agree; MCP remains unchanged. SQLite-only
  tests or mocked inputs do not establish real OceanBase/seekDB or native host acceptance.

# Drawbacks

The design still adds a durable domain table and nullable budget columns, requires an offline schema upgrade, and
retains block records without a time bound. It cannot answer a full execution-history query or reconstruct every
intermediate failure. Operators must account for persistent blocks caused by repeated administrative retries.

# Rationale and alternatives

| Alternative | Decision |
| --- | --- |
| Keep every attempt, including successes | More forensic detail, but recovery does not require it and no history consumer or retention budget is established. |
| Keep only a block in the existing budget | Cannot replay acceptance after success/reset deletes the budget; retaining old generations changes its current-frontier ownership. |
| Reuse Artifact storage | Transactionally feasible, but requires a private Family and durable restrictions on revisions, capabilities, and cleanup. Prefer the narrower operational lifecycle. |
| Carry transient attempts and permanent blocks in one new table | Avoids widening budgets, but adds separate active-attempt storage beside the authoritative budget. Reuse the existing current-work record instead. |
| Create shared processing history now | Requires agreement on another real workflow's identity and retention semantics. A rename or a hypothetical generic consumer does not establish reuse. |
| Rely on logs or let flush reset budgets | Logs cannot guarantee durable replay; ordinary flush must not authorize new model cost. |

# Prior art

[RFC 1417](1417_topic_memory.md) defines frontier allowances and atomic publication;
[RFC 1515](1515_artifact_processing_supervisor.md) supplies common scheduling and fencing;
[RFC 1771](1771-unified-database-migrations.md) defines explicit schema upgrades.
[Issue #1885](https://github.com/oceanbase/powercontext/issues/1885) records the operational recovery requirement.

# Unresolved questions

Before acceptance, reviewers should decide whether the proposed budget-plus-block model is the smallest sufficient
durable lifecycle, whether the assessed Artifact alternative offers a concrete advantage, and whether indefinite
block/receipt retention is acceptable. Any accepted change to these choices must retain the identity and atomicity
guarantees above. Backend migration/sizing evidence remains an implementation acceptance requirement, not an assumed
result of accepting this design.

# Future possibilities

A separately justified forensic history, shared execution records validated by another workflow, or bounded retention
with explicit replay-expiry semantics can be proposed later. None is required for blocked Topic recovery.
