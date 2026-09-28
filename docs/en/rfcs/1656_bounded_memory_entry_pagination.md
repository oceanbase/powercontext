- Proposal Name: `bounded_memory_entry_pagination`
- Start Date: 2026-09-28
- Tracking Issue: [oceanbase/powercontext#1656](https://github.com/oceanbase/powercontext/issues/1656)
- Related work: [#1657](https://github.com/oceanbase/powercontext/issues/1657),
  [#1709](https://github.com/oceanbase/powercontext/pull/1709), and
  [#1718](https://github.com/oceanbase/powercontext/issues/1718)
- Status: core design accepted in principle; implementation in progress

# Summary

PowerContext adds an additive, bounded Memory-entry directory query. The existing full-result
`list_memory_entries` operation remains unchanged. The new operation returns exact entry identities and compact
metadata in `entry_id ASC` order, without loading entry bodies, and continues with a signed opaque cursor.

Page one pins an immutable Memory revision. Later pages query a rebuildable revision-valid directory index at that
revision, so concurrent Memory writes cannot cause duplicates or omissions. Tag-filtered pages also bind a separate
tag generation because tag assignments can change without advancing the Memory revision. A tag change explicitly
invalidates continuation and requires a restart.

# Part A: SPEC

## 1. Background and problem

The current Memory list path resolves the latest Memory Artifact, decodes its complete manifest, loads every referenced
entry-version row, and only then applies presentation and tag filtering. Its persistence and response work therefore
grow with the complete Memory even when a caller only needs a small inventory page. HTTP-level slicing would bound the
returned JSON but not database work, manifest decoding, or entry-body expansion.

Remote clients, MCP integrations, dashboards, and audit tools need a stable, bounded inventory before choosing entries
to retrieve through the existing exact-detail operation.

## 2. Target behavior

Add `POST /v1/memory/entries/query` with operation ID `query_memory_entries`.

The request contains `scope_id`, `include_inactive` (default `false`), the existing optional `tag_filter`, `limit`
(default `50`, range `1..100`), and an optional opaque `cursor`.

The response contains the pinned `memory_ref` (or `null` when the Scope has no Memory), `items` ordered by
`entry_id ASC`, and `next_cursor`, where `null` means traversal is complete. Each item contains only the exact citation
(`memory_ref`, `entry_id`, and `entry_version_id`), positive logical `version`, `kind`, and lifecycle `state`. It does
not contain `text`, `source_refs`, or `artifact_refs`; callers use exact detail for the body.

The request, response, ordering, and behavior of `list_memory_entries` do not change.

## 3. Main scenarios

### Start and continue an unfiltered traversal

1. The Server authenticates the caller and authorizes the Scope.
2. With no cursor, it resolves and pins the current Memory revision. If no Memory exists, it returns a null reference,
   empty items, and no cursor.
3. It reads eligible compact directory rows after the page key and returns at most the count and byte budgets.
4. When more rows exist, the signed cursor continues after the last emitted `entry_id`.
5. Every continuation is authorized again and reads the same pinned revision.
6. Concurrent Memory commits remain invisible to this traversal; a new traversal sees the new head.

### Apply lifecycle and tag filters

Revision validity, lifecycle state, and tag predicates are applied before keyset paging and the item limit. Page one of
a tag-filtered query captures the current durable tag generation. A continuation validates it before and after its
bounded read. An effective Memory-entry tag mutation causes `400 invalid_cursor` with reason `tag_state_changed`; the
caller restarts at page one. A no-op tag replacement does not advance generation, and unfiltered cursors do not depend
on tag generation. This is invalidation/restart, not a tag snapshot guarantee.

### Existing database without a verified index

After authentication and authorization, the new operation returns `503 memory_query_index_unavailable`. It never
falls back to the legacy full-manifest path. Legacy list, exact detail, search, and Memory writes remain available
outside a declared maintenance window.

The operator runs an explicit offline `plan`, bounded/resumable `apply`, and `verify` workflow for SQLite or OceanBase.
Only successful verification marks the feature ready. Interrupted apply remains resumable and never rewrites
authoritative Memory data. Outside the maintenance window, new Memory commits maintain their directory deltas even
while historical backfill is incomplete, but the query remains unavailable until full verification succeeds.

### Enforce a page-byte limit

The Server measures encoded directory-item payloads against a fixed 4 MiB budget. It stops before an additional item
would exceed either limit and continues after the last emitted key. Legal v1 fields are already bounded. If corrupt
legacy data or a future incompatible shape creates one item that cannot fit, the operation returns
`413 memory_directory_item_too_large` with no partial page and no advanced cursor.

## 4. Rules and invariants

| ID | Rule or invariant | Result when violated |
|---|---|---|
| MEM-PAGE-01 | The new operation is additive; legacy full listing is unchanged. | Compatibility regression. |
| MEM-PAGE-02 | A traversal is pinned to one immutable Memory revision. | Invalid cursor or implementation defect. |
| MEM-PAGE-03 | Eligible rows use `entry_id ASC`; filtering precedes paging. | Skip, duplicate, or order regression. |
| MEM-PAGE-04 | Every item preserves exact Memory, entry, and entry-version identity. | Citation integrity failure. |
| MEM-PAGE-05 | Directory reads never decode bodies or expand body references. | Bounded-work contract failure. |
| MEM-PAGE-06 | A cursor binds endpoint, Scope, filters, limit, order version, pinned revision, and applicable tag generation. | `400 invalid_cursor`. |
| MEM-PAGE-07 | Authentication and Scope authorization run on every page before private existence or readiness is disclosed. | Access-control defect. |
| MEM-PAGE-08 | Effective Memory-entry tag mutations advance one generation atomically; no-op replacements do not. | Tag transaction rolls back. |
| MEM-PAGE-09 | Tag generation changes invalidate filtered, but not unfiltered, continuation. | `400 invalid_cursor`, `tag_state_changed`. |
| MEM-PAGE-10 | The query index is rebuildable derived state; Artifacts, manifests, entry versions, and tag rows remain authoritative. | Verification fails. |
| MEM-PAGE-11 | Directory deltas commit atomically with Memory and are proportional to changed entries. | Whole transaction rolls back. |
| MEM-PAGE-12 | An unverified index never serves or triggers an unbounded fallback. | `503 memory_query_index_unavailable`. |
| MEM-PAGE-13 | A page contains at most 100 items and 4 MiB of encoded item payload. | Page ends before the next item. |
| MEM-PAGE-14 | One over-budget item cannot produce partial success. | `413 memory_directory_item_too_large`. |

## 5. Failure, retry, and recovery semantics

- Malformed, tampered, cross-endpoint, cross-Scope, or request-mismatched cursors use the existing
  `400 invalid_cursor` contract. Expired signed cursors use `410 cursor_expired`.
- Retrying the same `tag_state_changed` cursor is not useful; restarting page one is safe.
- Existing authentication and authorization errors take precedence over readiness and private Memory existence.
- Retrying `memory_query_index_unavailable` is safe after migration verification.
- An oversized item returns no items and no replacement cursor.
- Memory and tag writes are all-or-nothing: authoritative state and related index/generation deltas commit or roll back
  together.
- Migration apply commits bounded checkpoints and is resumable; verify alone may mark readiness complete.

## 6. Concurrency and resource constraints

- Memory revision pinning provides a stable entry view. Tag rows are not revisioned, so tag-filtered traversal uses
  invalidation instead of claiming a snapshot.
- Tag generation is checked around the bounded tag query, closing changes during page assembly.
- The last emitted `entry_id`, not the lookahead row, is the continuation boundary.
- Persistence materializes at most `limit + 1` eligible compact rows and does not deserialize entry-body JSON.
- Item encoding is measured incrementally and capped at 4 MiB, excluding a separately bounded envelope and cursor.
- Query plans and measured work are recorded for 200, 1,000, and 5,000 entries. Evidence distinguishes returned or
  materialized rows from database rows examined; it must not claim a bound unsupported by SQLite or OceanBase plans.
- A Memory commit closes and inserts validity rows only for changed manifest pointers or states. Existing search
  projection rows for unchanged entries remain untouched, preserving #1709.
- Backfill uses a positive batch size and durable checkpoint; startup never runs an unbounded rebuild.

## 7. Non-goals

This PR does not paginate or alter `list_memory_entries`; return entry bodies in the directory operation; implement
#1657 history pagination; implement capacity, retention, split/routing, compaction, or physical erasure policy from
#1321/#1718; snapshot all tag assignments; change search, exact-detail, or citation semantics; make derived rows
authoritative; or support mixed old/new writers during the offline migration window.

## 8. Acceptance criteria

| Obligation | Observable evidence | Verification layer |
|---|---|---|
| Additive contract | New OpenAPI operation exists; legacy schemas remain compatible. | Contract/generation tests |
| Stable traversal | Concurrent writes neither skip nor duplicate pinned rows. | Runtime and HTTP tests |
| Compact exact identity | Every item resolves through exact detail; body fields are absent. | Runtime/mapping tests |
| Filter before page | Lifecycle and tag filters fill from eligible rows. | Persistence tests |
| Tag restart boundary | Effective changes invalidate; no-op changes and unfiltered cursors do not. | Tag/concurrency tests |
| Authorization per page | Revoked access stops continuation without disclosure. | HTTP access tests |
| Bounded read/response | `limit + 1`, no body decode, count/byte bounds, and atomic 413 are observed. | Instrumented persistence/HTTP tests |
| Delta writes | Only changed entries mutate directory/search projections. | Persistence tests |
| Safe migration | Fresh, incomplete, interrupted, resumed, verified, and corrupt states obey the gate. | SQLite/OceanBase migration tests |
| Scale behavior | 200/1,000/5,000 evidence records plans, rows, bytes, write deltas, and batch work. | Reproducible report |

# Part B: Design report

## 1. Final data flow

```text
OpenAPI query_memory_entries
  -> Server authentication and Scope authorization
  -> ScopedMemoryApplication.query_directory
  -> relational directory authority
       -> readiness and pinned-revision validation
       -> optional tag-generation validation
       -> revision-valid filter + keyset + lookahead query
       -> optional tag-generation revalidation
       -> item-byte budget and signed continuation
  -> Server response mapping
```

Memory commits use the existing transaction. After comparing previous and current manifest maps, the transaction
updates validity rows only for changed entries. Tag replacement remains owned by `RelationalTagService`; an effective
Memory-entry tag delta advances its dedicated generation under the existing owner-Artifact serialization.

## 2. Authority and seams

| Rule or state | Authority | Interface | Evidence |
|---|---|---|---|
| Wire shape | `openapi/powercontext.yaml` | `query_memory_entries` | schema/generation tests |
| Authorization order | Server application | existing Scope authorization | access tests |
| Directory semantics and budget | Memory runtime/persistence | `query_directory` | persistence/runtime tests |
| Cursor signature and TTL | `SignedCursorCodec` | exact-context encode/decode | cursor tests |
| Revision-valid state | relational Memory persistence | directory validity table | database tests |
| Exact bodies and versions | Memory Artifact and entry-version tables | existing exact read | citation tests |
| Mutable tags | `RelationalTagService` and tag tables | predicate + generation | tag tests |
| Readiness/checkpoint | query-index migration module | plan/apply/verify | migration tests |
| Error presentation | Server error mapping | stable status/code/details | HTTP tests |

The Server does not reimplement pagination, the runtime does not infer authorization, and the cursor codec authenticates
context without owning query semantics.

## 3. Persistence mechanism

A derived directory table stores `entry_id`, exact `entry_version_id`, lifecycle `state`, inclusive
`valid_from_revision`, and exclusive nullable `valid_to_revision` per Scope and Memory Artifact. Indexes support
`entry_id` keyset traversal constrained by `valid_from_revision <= pinned_revision` and
`valid_to_revision IS NULL OR valid_to_revision > pinned_revision`. Compact `version` and `kind` come from an indexed
join to the authoritative entry-version row; body JSON is not selected or decoded.

At revision `R`, a changed entry closes its previous row with `valid_to_revision = R` and inserts its new pointer/state
with `valid_from_revision = R`. New entries only insert; unchanged entries do nothing. This preserves historical
membership without copying a complete manifest per revision.

The signed cursor contains exactly the schema/order version, endpoint, Scope, normalized filters, limit, pinned Memory
Artifact ID/revision, optional tag generation, exclusive `after_entry_id`, and expiry.

A small durable tag-generation table stores one monotonic generation per Scope/Memory Artifact. It advances only when
`RelationalTagService.replace` produces an effective normalized delta for a `memory_entry` target.

A feature-scoped marker stores query-index schema version, readiness, and resumable checkpoint. Fresh databases start
complete. Existing databases remain incomplete until offline verification confirms derived coverage and identities.

## 4. Accepted trade-offs

- Bodies require exact-detail round trips; this preserves a truly bounded directory.
- Tag changes restart filtered traversal instead of retaining an unbounded snapshot.
- Existing databases require explicit offline migration for only this new feature.
- Revision-valid rows use more storage than current heads but avoid per-revision manifest copies.
- The initial order is fixed; future orderings need separately versioned index and cursor contracts.

## 5. Obligation-to-test map

| Obligation | Owning layer | Test |
|---|---|---|
| Public compatibility | OpenAPI | schema/generation and SDK tests |
| Revision-valid selection | directory persistence | SQLite/OceanBase keyset tests |
| Atomic delta maintenance | Memory commit | rollback and row-delta tests |
| Tag generation | tag persistence | effective/no-op/concurrent mutation tests |
| Cursor binding | cursor/query seam | mismatch, tamper, and expiry tests |
| Auth before disclosure | Server | HTTP/MCP access tests |
| Byte behavior | query authority/Server | boundary and 413 tests |
| Readiness | migration module | fresh/incomplete/resume/verify/corrupt tests |
| Legacy unchanged | legacy list path | existing suite plus compatibility regression |
| Resource claims | persistence | reproducible scale report |

## 6. Verification plan and current status

Before review, run focused OpenAPI, cursor, Memory persistence, tag, migration, Server, access-control, and client tests;
SQLite scale measurements; OceanBase evidence when its service is available; generated-code, contract, documentation,
format, lint, and type checks; then `make check` and `make unit-test`. Report unavailable services and skipped checks
separately from passes.

Current status: the revision-valid directory, tag-generation invalidation, bounded runtime query, feature-scoped
migration/readiness gate, operator CLI, and public OpenAPI/Server/SDK/MCP surface are implemented with focused
persistence, contract, access, and runtime tests. The scale report, OceanBase migration evidence, and full repository
checks remain.
