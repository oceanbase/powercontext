---
title: Migrate to Atomic Memory
---

Existing Memory collections require an offline conversion to independent `atomic-memory` artifacts.
The versioned task `powercontext.memory.v1-to-atomic-memory.v1` freezes the legacy content format,
identity rules, version chains and import encoding. It connects using deployment settings without starting
a Runtime or Worker. After migration, legacy Memory collections exist only in the offline archive table
`pc_memory_artifact_archive`; they leave the public Artifact tables, search and every online read path.
Normal service startup runs only a light residual check: no legacy collection or lineage to one remains in the
public tables, the legacy entry tables no longer reference them, and the public tables no longer declare the
legacy `memory_citations` columns. It reads neither the archive nor the
legacy entry tables; complete per-entry verification is the job of `verify`.

## Run the maintenance task

Inspect the read-only plan with the new deployment's configuration:

```shell
powercontext server atomic-memory-migrate --action plan --env-file .env
```

The JSON output includes `counts`, `errors`, `ready` and a Source/processing snapshot digest.
`pending_entries` counts identities still requiring conversion. A valid plan with pending entries has
`ready: false`. The scan includes every Scope and legacy container, all collection and entry revisions,
inactive entries and compacted entries.

For SQLite, plan and verify require an existing persistent database. Filesystem URLs and SQLite file URIs
are opened with `mode=ro`; the commands only configure connection settings and do not create directories,
initialize schema or change the database's journal mode. Missing or unreadable paths fail explicitly.
Process-memory and temporary databases, including `:memory:`, file URIs with `mode=memory` and empty
file URIs, are rejected. Inspection ignores `immutable` and `nolock` URI options to retain normal locking
and committed WAL visibility. SQLite's WAL coordination can use or create `-wal` and `-shm` sidecars.

Back up the database. Stop all old APIs, hosts and Workers, disable their automatic restart, and pause
Source input, manual writes and explicit triggers. The confirmation flag attests to these external
conditions; the command cannot stop external processes. Then run:

```shell
powercontext server atomic-memory-migrate --action apply --env-file .env --maintenance-confirmed
powercontext server atomic-memory-migrate --action verify --env-file .env
```

Start the new service and resume writes only after verification returns `ready: true`. A vector deployment
uses its configured embedding service through the normal projection publisher, preparing vectors only for
current active entries. A deployment without vectors still maintains full-text search. Plan and verify do
not call the embedding service.

Complete any required [processing scheduling migration](./artifact-processing-migration.md) first.
Its schema marker establishes scheduling readiness only; Atomic Memory readiness is verified independently.

## Conversion and rejection rules

A fixed UUIDv5 rule derives the new identity from `(scope_id, old_memory_artifact_id, entry_id)`.
Entry IDs shared by different containers remain distinct. Each new revision equals the old entry version;
collection revisions caused by other entries do not create extra content revisions.

Validation requires versions continuous from 1, consistent predecessor IDs, content hashes, identities and
manifest references. The current manifest pointer, or the last pointer before compaction, must identify
that entry's chain tail. Unexplained absence, missing changes, duplicate identities, skipped versions and
lagging heads block conversion. Legacy active, inactive and compacted entries become active, forgotten and
retired respectively. Lifecycle is authoritative in `pc_artifact_heads`: these states use `lifecycle_state`
values `active`, `deprecated` and `retired`, respectively, and newly imported heads have no `merged_into_id`.
Atomic Memory reads the head's `governance_generation` as its `state_version`.

A formal Owner on an exact legacy entry resource becomes the new memory's Owner. Without enforced access,
an entry without an Owner migrates without one. Several Owners on one entry or an invalid Owner block
conversion. Collection ownership is not distributed to entries. Entry tags become artifact
tags; collection tags retain their collection meaning. Exact entry bindings keep their original binding ID,
subject, role, expiry, revocation, grant provenance and idempotency fields while their resource is retargeted.
Legacy Memory grants without an entry selector are unsupported and block conversion.

Grant creation idempotency receipts include the resource identity. Migration validates the original request
digest before converting it to the new identity. Replaying the same request and key still returns the same
binding; changing its subject, role, expiry, reason or resource still conflicts. Revoke and replace receipt
digests do not contain a resource identity and remain unchanged. Missing receipts, mismatched associations
or unverifiable digests block migration.

A current custom `memory.extract` Prompt blocks the task. Explicitly set that legacy Prompt to Auto and
configure `atomic_memory.extract` and `atomic_memory.reconcile` for their new input/output contracts; the
new Prompts may use Auto. Old Prompt history remains available. Injected legacy CandidatePipeline components
must be replaced with AtomicMemoryGenerationPipeline. The task does not silently translate custom guidance
or examples. Unknown cursor/task formats and unresolved legacy Memory candidates also block conversion.


The legacy MemoryWriteGate depends on the collection write contract and cannot be injected into Atomic Runtime.
Enabling `POWERCONTEXT_SERVER_RUNTIME_MEMORY_WRITE_GATE_ENABLED` or injecting an old gate is rejected during
construction. Legacy capacity and compact settings do not constrain Atomic Memory. See
[configuration](configuration.md#atomic-memory) and [API/SDK compatibility](../workflows/atomic-memory.md#legacy-memory-api-compatibility).

## Archive, legacy references and decisions

Apply runs these steps in order; each can be repeated after an interruption:

1. Each collection's revisions, lineage, head, Owners, tags, grants and grant receipts are written to
   `pc_memory_artifact_archive`. An existing identical archive is reused; a collection changed after
   archiving is a conflict and never overwrites the snapshot. Import and verification read that snapshot.
2. Each entry's version chain is imported. Every imported revision records only that entry's exact Source
   and non-collection Artifact evidence; revision two and later also reference their imported predecessor.
3. Exact references to legacy entries become the corresponding Atomic revision. Experience
   `memory_citations` become lineage ArtifactRefs; Candidate `memory_citations` join `artifact_refs`;
   `kind: memory` citations in Handoff content and Work Sources, including Handoff receipts, become
   `kind: artifact`. Handoff lineage and publication digests follow, and recurrence ledger keys are
   recomputed when a Task Outcome item digest changes.
4. A relationship to a whole collection has no single Atomic counterpart. Its original location and value
   are recorded in the referenced collection revision's archived `incoming_references` before it is removed
   from online lineage, Candidate `artifact_refs`, Task Outcome `produced_artifacts`, and `kind: artifact`
   citations of a whole collection in Handoff content and Work claims or checks. A Handoff receipt's
   `unavailable_evidence` records evidence that was unavailable rather than support: its collection references
   move unchanged into the receipt's `historical_data` (format `powercontext.handoff-receipt-history.v1`), which
   is shown as history and never resolved.
5. Finished Dream runs move to a historical format: the original request, input manifest and request digest
   move to `historical_data` and are no longer read as an executable request. Unfinished runs drop the empty
   legacy entry citation fields from their request and input manifest. Request digests are recomputed in the
   current request format, so retrying the same request with the same idempotency key still replays the run.
   A request that cited legacy entries cannot be sent again; its run keeps the accepted digest, so reusing its
   key conflicts.
6. The retained legacy entry tables are detached from public Artifact rows. While the collections are still
   public, the import history, current projection, grant conversion and every reference conversion are
   accepted; any failure stops before a collection is removed.
7. The lineage owned by every legacy collection is deleted first, then the collections, their heads, tags
   and Owners, so collections that cite each other never block removal through identifier order. Legacy entry
   tables stay unused.
8. The emptied `memory_citations` columns are dropped from `pc_artifacts` and `pc_artifact_candidate_versions`.
   A final residual check confirms nothing legacy remains public.

If startup reports that public tables still declare `memory_citations`, reference conversion has not finished
for that database, for example because an interrupted apply had already removed every collection. Keep writers
stopped and rerun apply with the same configuration; it completes the remaining receipt, Dream and column steps
and can be repeated.

These conditions block before any data is rewritten and are listed by plan: an unfinished Dream run whose
pinned inputs this migration changes, including legacy entry citations, legacy collections, and Artifacts or
Work Sources whose content or lineage is rewritten; a legacy Memory Family Candidate or publication; a
reference that does not resolve to an exact imported Atomic revision; a Handoff statement or verified Work
claim or check left without evidence once collection citations are removed; and a Candidate version left
without any Source or Artifact evidence once its collection references are removed.

The last case requires a decision file, passed with `--decisions`, that names replacement evidence for each
blocked Candidate version:

```json
{
  "format": "powercontext.atomic-memory-reference-decisions.v1",
  "decisions": [
    {
      "carrier": "candidate",
      "scope_id": "team-a",
      "candidate_id": "candidate-1",
      "version": 1,
      "field": "artifact_refs",
      "action": "replace",
      "artifact_refs": [{"family": "experience", "artifact_id": "exp-1", "revision": 2}]
    }
  ]
}
```

```shell
powercontext server atomic-memory-migrate --action plan --env-file .env --decisions decisions.json
powercontext server atomic-memory-migrate --action apply --env-file .env --maintenance-confirmed --decisions decisions.json
```

Only `replace` is supported. Replacement evidence must be existing exact Artifact revisions and cannot be a
legacy Memory collection. Decisions must match blocked versions one to one; extra or duplicate decisions are
errors. The original reference is still archived and marked as replaced. After partial or complete success,
rerun the same command with the same decision file: an applied decision is checked against the Candidate
version's current replacement evidence, counts as complete when it matches, and conflicts only otherwise.

Historical lineage_only Sources retain their original targets and remain provenance without entering model
input. The task neither rebinds old Sources nor invents timestamps.

Cursors, CAS generations, high-water marks, pending/flush requests, accepted tasks and scheduling keys remain
unchanged. Family `memory` and binding `memory-source-window` remain scheduling aliases. Old leases are
invalidated and their fences advance before maintenance completes. Consumed Sources are not extracted again.

Each entry's history, head, Owner, tags, grant conversion and current projection commit together.
Embedding preparation occurs outside the transaction. Repeat apply with the same configuration after an
interruption. Existing targets must match the exact imported history; differing data or orphan content
is rejected without overwriting authority. Subsequent revisions or lifecycle changes never cause the task
to reset a target's head, state or tags.

If a converted binding still has a creation receipt with the legacy resource digest, plan and verify return
`ready: false` and report `pending_grant_receipts`. Keep the service stopped and repeat apply to repair those
receipts. `migrated_grant_receipts` reports the number repaired in that run; binding identities, revocation
state and audit records remain unchanged.

Legacy collections are retained only in the archive table, which has no online read surface. This task does
not downgrade the database; rollback requires the complete backup from before maintenance. The command
performs archiving, reference conversion, removal and the column drop itself rather than through the RFC 1771
migration executor, which currently covers only the four Artifact tables' structure and cannot run data tasks.
The column drop is not registered as an RFC 1771 schema revision because it is valid only after the reference
conversion has been accepted. Atomic
Memory content restoration is a separate operation.

## Rebuild the current search projection

After changing the embedding model, profile, dimension or normalization, or repairing current search data,
back up the database and stop every API, host and Worker, their automatic restart and all input writes.
Use the target deployment configuration:

```shell
powercontext server atomic-memory-rebuild-projection --env-file .env --maintenance-confirmed
```

This command requires completed legacy history migration, valid authoritative content revisions and head
lifecycle data. It rebuilds only active current rows and removes nonactive or orphan current rows. Heads, content
revisions, lifecycle states, retained history and Source/processing progress keep their exact identities and
values. It cannot repair or bypass missing legacy history imports.

Each active body's embedding is prepared outside the write transaction. Before publishing, the command
locks and rechecks its exact revision, state version and deployment profile, then loads the latest formal
tags and immutable Owner in that transaction. With vectors disabled, it retains body/full-text
data and clears embedding, profile and input hashes. SQLite refreshes its derived FTS helper using stable
Scope/Artifact identity tokens, independent of rowids changed by `VACUUM`.

For OceanBase and seekDB, a changed vector dimension first clears the derived vectors, replaces the native
vector index and reconfigures the current column offline. This DDL can commit independently; keep maintenance
in force throughout the operation. Matching dimensions still require all active rows to be embedded with
the target profile. No historical vector cache is retained.

Rows commit separately. An interrupted run can leave a partial projection; repeat the same command while
writes remain stopped. It prepares every active row again rather than resuming through a progress table.
`--batch-size` controls identity reads (1–1000, default 100), not a vector-result limit. The JSON report
includes `profile_fingerprint`, `active_rows`, `obsolete_rows`, `rebuilt_rows`, `removed_rows`,
`embedding_calls` and `elapsed_ms`. Resume service only after the complete current check returns
`ready: true` and normal startup verification succeeds. Errors do not silently enable another retrieval mode.

Current bounded vector searches also calculate exact L2 over the eligible current rows. Eligibility is
applied before ranking and limiting. Complete related-memory enumeration uses the same exact calculation
without a result limit and returns every qualifying result within its threshold. Work grows with eligible
row count times vector dimension. The native vector index is provisioned, but these search paths do not
use ANN; this implementation makes no ANN performance promise or claim of production backend validation.

## Record maintenance costs

Counts report containers, collection revisions, entries, entry versions, lifecycle totals, imported/verified
entries, `archived_collections`, `removed_collections` and reference conversion counts prefixed `reference_`. Apply's `elapsed_ms` measures time inside the command, not total external downtime. Payload byte
counts exclude indexes, access records, database pages and replication; record actual coexistence storage
through database monitoring.

Plan, apply and verify read retained history and Source/task snapshots, materializing those records in
memory. Read cost and peak memory grow with history. Record read volume, peak memory, embedding calls and
downtime on a backup copy before scheduling production maintenance. Production scale costs have not been
established by this implementation. The startup residual check only counts legacy objects in public tables
and reads no retained history; its cost depends on those tables and the execution plan.

A current table created during development of this branch (with copied read grants or Owner columns) belongs
to no release and is rejected at initialization. Before migration completes, apply drops and recreates that
derived table and its report asks for a projection rebuild; after migration, stop writers and run the
projection rebuild command above. Either way only that derived table is rebuilt from retained authority;
Artifact history, head lifecycle data and grant records are unchanged.
