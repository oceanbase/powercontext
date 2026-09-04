# Artifact Processing Persistence Design

## Scope

Implement only the persistent primitives required by RFC 0000 before the Artifact Processing Supervisor exists.
Topic Memory is the first binding, named `topic-memory-source-window`.

This milestone reuses the existing scoped Source Journal (`pc_sources` and `pc_source_journal_heads`) and the existing
generation-CAS source cursor (`pc_source_cursors`). A Source Window remains an ephemeral contiguous slice returned by
`SourceRepository.list(after=..., limit=...)`; it gains no table, identifier, archive, token policy, or new domain type.

The only new persistent record is the RFC's Pending dirty set. This milestone does not implement leases, fencing,
workers, automatic waves, retries, scheduling state, Topic generation, projections, HTTP, MCP, or Prepared Context.

## Storage

Add `pc_artifact_processing_pending` exactly as specified by RFC 0000:

- primary key: `(binding_name, scope_id)`;
- `source_through >= 1`;
- `flush_generation >= 0`, default `0`;
- `handled_flush_generation >= 0`, default `0`;
- `handled_flush_generation <= flush_generation`.

No job status, timestamps, owner, attempt counter, error state, lease, or audit columns are added.

## Repository operations

`ArtifactProcessingPendingRepository` exposes the minimum operations used by later orchestration:

1. `load`: return the current Pending snapshot or `None`.
2. `raise_source`: insert or update the row so `source_through = max(existing, new_source_position)`, leaving both
   flush generations unchanged.
3. `request_flush`: read the current Source head. Return `None` when the binding Cursor already covers that head;
   otherwise raise `source_through` to the head and increment `flush_generation` once.
4. `mark_flush_handled`: set `handled_flush_generation = max(current, claimed_flush_generation)` without advancing
   beyond the stored `flush_generation`.
5. `delete_if_covered`: delete only when the supplied/current Cursor covers `source_through` and the handled and current
   flush generations are equal. Automatic-wave cleanup is not implemented in this milestone.

The repository accepts an existing `AsyncConnection`; callers own transaction boundaries. No method opens an internal
top-level transaction.

## Source capture integration

Source capture must raise Pending in the same transaction that commits a new Source. The integration is explicit at the
runtime capture boundary: after `SourceRepository.add` returns a newly created journal position, the runtime raises the
Topic Memory binding's Pending row on the same connection.

An idempotent add of an already stored Source does not represent a new Source write and need not raise the watermark.
No existing Memory, Experience, or Skill processor is migrated to the new Pending mechanism.

## Concurrency semantics

Use backend-supported SQL upsert/update behavior only to implement the RFC's monotonic maximum and generation changes.
Do not add application locks, advisory locks, retry loops, deduplication tokens, ownership records, or defensive state
beyond the RFC constraints. The existing Cursor repository remains the sole Cursor CAS implementation.

## Observable tests

Tests cover only RFC-visible behavior:

- Source raises create one binding/scope Pending row and monotonically increase `source_through`.
- Source capture and Pending update commit or roll back together.
- Multiple flush requests increment one coalesced row while retaining the latest Source head.
- Different bindings/scopes remain independent.
- Flush handling never skips a later flush generation.
- Pending deletion requires both Cursor coverage and equal flush generations.
- Existing Cursor generation CAS remains the publication guard and is not duplicated.

## Boundary escalation rule

During review, any proposed persistence field, lock, retry, security check, validation rule, or failure state absent from
RFC 0000 must be rejected when the RFC explicitly excludes it. If the RFC is silent and the mechanism would change the
contract or operational trade-off, implementation pauses for human confirmation.
