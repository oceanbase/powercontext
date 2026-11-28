---
title: "RFC 1809: Atomic Memory as independent artifacts"
description: A proposal for independent Atomic Memory identities, revisions, extraction, merge and undo, with offline migration from legacy collections.
---

- Proposal Name: `atomic_memory`
- Start Date: 2026-09-30
- RFC PR: [oceanbase/powercontext#1809](https://github.com/oceanbase/powercontext/pull/1809)
- Depends on: [Artifact Search Projections and Join-Free Retrieval, #1803](https://github.com/oceanbase/powercontext/pull/1803)
- Migration dependency: [Unified Versioned Database Migrations, #1771](https://github.com/oceanbase/powercontext/pull/1771)
- Amends: [0014](0014_memory_layer_design.md), [0019](0019_local_source_memory_runtime.md),
  [1345](1345_scope_organization_and_agent_integration.md), [1652](1652_memory_quality_and_lifecycle.md),
  [1718](1718_memory_capacity_contract.md)
- Related: [1417](1417_topic_memory.md), [1549](1549_artifact_family_unification.md)

# Summary

This RFC introduces the `atomic-memory` Family. Each memory previously held in a Memory collection becomes an
independent Artifact organized by Scope, with its own ID, revisions, evidence, and state.

Extraction first generates candidates from Source, then retrieves related active memories in the same Scope that
the processing identity may both read and write. The model automatically decides whether to create, revise, merge,
or make no write. Merging multiple memories creates a new Artifact; the inputs retain their history and stop evolving.
Users can edit the result or undo the merge. Search follows RFC #1803, and existing deployments upgrade offline under
RFC #1771.

# Motivation

## Memory collections duplicate Scope responsibilities

[RFC 0014](0014_memory_layer_design.md) models Memory as a collection. A Memory Artifact stores a complete manifest,
with each member pointing to an immutable entry version. Changing one memory writes a new entry version and then a
collection revision that references it.

After [RFC 1345](1345_scope_organization_and_agent_integration.md) introduced Scope, each Scope still retained one
active Memory collection. A memory therefore involves both collection and entry IDs and versions. Scope already
organizes memories, and Artifact already provides revisions, evidence, permissions, and tags. The collection layer
retains additional maintenance rules.

## Changing one memory still stores the whole collection directory

The current implementation supports entry revision, historical reads, capacity limits, and optional manifest cleanup
through compaction. Content and search projections can be updated for changed entries, but every effective change
still constructs and stores the complete manifest, even when most entries are unchanged.

Continued writes accumulate repeated directory information. Tasks editing different memories still update the same
collection head. Independent Artifacts make a single-memory update add history only for that memory.

## Extraction context grows with memory count

Built-in extraction gives the model every active entry of the selected Memory head. The model can revise memories
from earlier Source windows, but context size and input costs grow as memories accumulate.

This change needs related-memory retrieval after candidate generation, before the model makes its decisions, together
with rules for merging multiple memories and undoing those merges. An entry replacement endpoint alone does not
resolve these problems.

# Guide-level explanation

## One memory, one Artifact

Atomic Memory represents a fact, preference, or rule that can be understood and updated independently. Atomicity
refers to content granularity, not a requirement that each memory be one sentence. Times, reasons, and applicability
conditions needed to understand a fact remain part of it.

For example, the default deployment region being East China and production releases requiring owner approval are
two memories. Changing the region to North China creates a new revision of only the first memory. Exact historical
references still return their original content.

Memories can be extracted automatically from Source or written manually by an authorized caller.

## Retrieve related memories before deciding what to write

Processing new Source first generates candidates, then retrieves related active memories in the same Scope that the
processing identity may both read and write. Similarity finds related content; the model uses candidates, existing
memories, and evidence to decide whether to create, revise, merge, or make no write. These operations run automatically
by default, without per-operation approval or preauthorization of a category of merges.

Read-only shared content cannot be a target of revision, merging, or deactivation. When statements about the same fact
conflict under the same conditions, the model uses time to select the newer applicable information and automatically
revises or merges the memories. It prioritizes explicit effective, event, or source-recording times and falls back to
Source ingestion order when comparable times are unavailable. Atomic Memory does not retain conflicts for users to
resolve or require confirmation of each decision.

Times and applicability conditions remain part of the evidence: late-arriving historical material does not become a
new fact merely because it was just ingested, and rules for different environments should not overwrite each other.
A revision or merge does not refresh the time of the underlying fact. Historical content and lineage remain available;
later Source, user edits, or restoration can correct an incorrect decision.

Every memory meeting the relevance threshold enters the comparison set. A fixed top-N cutoff must not discard other
qualifying results. Retrieval and model processing may use batches when there are many results. When vector search
is disabled or unavailable, full-text search or another retrieval method still selects related memories; extraction
must not put the whole Scope's memory collection directly into model context.

## A merge creates a new memory

Merging A and B creates a new memory C. C records the exact input revisions. A and B enter the merged state, stop
content evolution, and leave normal search, while remaining readable by exact reference. Later Source or user edits
continue updating C.

The model may discard invalidated content based on new evidence. Prompts must distinguish invalidation from omission
and preserve still-valid facts, applicability conditions, and evidence during rewriting. Prompts cannot eliminate
all mistakes, so input memories and merge history remain available for correction and undo.

## Four states

| State | Normal search | Authorized exact reads | Restoration |
| --- | --- | --- | --- |
| Active | Included | Allowed | — |
| Forgotten | Excluded | Allowed | May be restored independently to active |
| Merged | Excluded | Allowed | Restore by undoing related merges; a standalone state change cannot bypass the merge relationship |
| Retired | Excluded | Allowed | The original identity cannot be restored |

Only active memories participate in new merges. If C is forgotten, A and B remain merged. Restoring C restores only C;
restoring A or B requires undoing the merge that created C. Historical reads do not automatically restore memories.

## Correct content or undo a merge

Users may edit C through an Agent or management interface, or allow later collected Source to drive its evolution.
Restoring an older revision of C creates a new revision from that content. It neither rewrites history nor changes
the states of A and B.

Undoing the merge that created C restores A and B at their frozen revisions and retires C. Content added to C later
also leaves normal search; it is not automatically distributed back to A and B. All of C's history remains readable
by exact reference. A restoration request targeting A or B makes the server perform this group operation.

The server also handles successive merges. If A and B form C, and C later merges with D into E, a request to restore B
undoes E and then C. The final result restores A, B, and D and retires C and E, with one client request. If D was itself
a merge result, it is restored intact rather than split further.

By default, undo restores each input at the exact revision used in its merge. A request may also select a historical
revision of the target: first undo subsequent merges that keep it in the merged state, then create a new target revision
from the selected content. Other restored inputs retain their frozen revisions. For example, restoring an older
revision of C requires undoing E, but not the merge that created C. This still takes one client request. Selecting a
historical revision cannot restore a retired memory.

Only merge relationships still in effect are followed; ordinary evidence references do not cause other Artifacts to
roll back. Undo does not rewind Source consumption.
New Source may lead the model to merge the memories again, creating a new identity.

## Preview a restoration or call it directly

The service provides separate restoration preview and execution operations. A preview does not change data. It returns
the memories and revisions to restore, the merge results to retire, and later content that will leave current search.
A management interface displays this impact before the user confirms execution.

Client-side state is only a hint; the server determines the actual impact. Execution against a preview checks that the
target memory, selected revision, and restoration operation match the preview, and that the current merge endpoint's
content and state versions have not changed. A mismatch rejects execution and requires a new preview rather than
expanding the scope the user confirmed.

A user or Agent may also request restoration directly, without a preview or manually traversing merge results. The
server computes and performs restoration against execution-time state and returns the affected objects. Direct calls
still require permission, state, and concurrency checks. Confirmation after a preview applies only to user-initiated
restoration; routine extraction and merging remain automatic.

# Reference-level explanation

## Existing contracts and changes made here

Scope membership and organization follow RFC 1345, Source processing follows RFC 0019, and identity, revisions, and
evidence follow RFC 1549. Independent memory Artifacts do not each consume Source separately; the Scope's extraction
flow still owns processing progress.

This proposal replaces the collection and entry version layers of RFCs 0014 and 0019 and removes RFC 1345's requirement
for one active Memory collection per Scope.

RFC 1652's evidence-preservation principles remain applicable. Atomic Memory creation, revision, and semantic merging
run automatically, and the model resolves conflicts using time. Its per-operation merge approval and unresolved-conflict
retention requirements do not apply to Atomic Memory. Merging multiple memories creates a new result and freezes the
inputs. This RFC does not change approval or conflict-handling rules for other Artifact families.

RFC 1718's collection capacity limits for complete manifests do not apply to the new Family. They are not converted
into a Scope-wide memory count limit, and this proposal introduces no historical revision expiry policy. Release
compatibility documentation states how old configuration is handled.

## Search and concurrency

Search projections and their updates follow [RFC #1803](https://github.com/oceanbase/powercontext/pull/1803). Atomic
Memory may use wide tables or separate projections, with no business-table joins in full-text or vector retrieval.
When extraction selects memories it may modify, Scope, read/write permissions, and active state take effect during
candidate selection.

Batch sizes bound individual reads and model inputs without truncating the total set meeting the threshold.
Incomplete processing must not be reported as no new memory. Implementation design determines thresholds, batching,
and index choices.

Memory states, relationships, and current search data affected by a merge, restoration, or merge undo become visible
together. Original memories must not reappear in normal search while their merge result remains searchable. One
active memory cannot be concurrently consumed into two merge results that are both in effect.

During restoration, the server checks current content versions, states, and merge relationships inside the write
transaction. A preview identifier adds validation against the preview; requests without one use current state.
The checked conditions must still hold at commit, without overwriting intervening updates.

## Idempotent explicit creation

`remember_memory` is the only Atomic Memory creation entry point that accepts an idempotency key. It lets a caller
that lost a response retry one explicit creation without creating a second memory. Requests without a key keep their
existing behavior: every successful call creates a new memory. Revision, forgetting, restoration, merging, extraction,
Source processing, and RFC 1437's generic Artifact Create accept no key. The server never deduplicates by content and
never retries on a caller's behalf.

### Request field and namespace

`RememberMemoryRequest` adds optional `idempotency_key`: 1 to 256 printable ASCII characters other than space,
matching `^[!-~]{1,256}$`. The server compares keys byte for byte without trimming, case folding, or other
normalization. Omitting the field or passing `null` means no key. A key that does not match the pattern is rejected
with `422` under the common request-validation policy before any lookup or write: `invalid_request`, or
`invalid_access_request` when enforced access control validates the request body first.

A key is unique within one namespace: the operation `remember_memory`, `scope_id`, and the authenticated principal's
type and ID. When access control is disabled, the principal component is empty, so all callers of that deployment share
one namespace per Scope. The same key in another Scope, from another principal, or for another operation names an
independent write. The HTTP API, Python SDK, and MCP tool carry the field unchanged; none of them generates keys or
retries automatically. Callers generate a fresh unguessable key, such as a UUID, for each logical write and reuse it
only to retry that write. The MCP server instructions and the shared Skill reference give models the same guidance,
and tell them to use a new key after a conflict. The Dify `pc_remember` tool does not expose the field.

The server stores only a SHA-256 digest of the namespace and key, never the raw key or the principal ID in clear.

### Request digest

The request digest identifies a logical write by the revision 1 content it produces. Version 1 is the lowercase
hexadecimal SHA-256 of the RFC 8785 serialization of a JSON object with exactly these four members, matching the
`AtomicMemoryContent` the writer would store:

- `schema` is `powercontext.atomic-memory.v1`.
- `kind` is the submitted value, unchanged, as the writer stores it.
- `text` is the result of the writer's `normalize_text`: Unicode NFC, then removal of leading and trailing whitespace.
- `description` is the submitted value when present. When the field is omitted or `null`, it is the default
  description the writer derives from the normalized text.

The object has no `creation` member, because an explicit creation never carries merge metadata. Strings are hashed as
they would be stored: apart from `text`, no Unicode normalization is applied, so a helper that normalizes every string
must not compute this digest. Changing the member set or the behavior of `normalize_text` or of the default
description requires a new digest version.

Omitting `description`, passing `null`, and passing exactly the default description therefore produce one digest,
because they store the same content. `reason` is not part of the written content and is ignored. `expected_revision`
must be omitted or `null`; a non-null value is rejected before lookup. `scope_id` and the principal belong to the
namespace, not the digest.

The Source written for revision 1 records the command of the request that performed the creation. A retry whose text
differs only by normalization replays that creation and does not rewrite its Source. Each key record stores its digest
version. A new digest version applies only to new keys; the server keeps computing every version that can still appear
within the retention period.

### Results

| Lookup in the key's namespace | Result |
| --- | --- |
| No unexpired record | Create the memory as without a key, and bind the key in the same transaction. |
| Record with the same digest | Replay the original result. Prepare no Embedding and write nothing. |
| Record with a different digest | Return `409 idempotency_conflict` with details `{"kind": "memory_idempotency_key"}`. Write nothing. |

A replay returns the same `MemoryMutationResponse` as the original creation: `changed: true` and one record with the
exact `ArtifactRef` of revision 1, its `kind` and `text`, `state: active`, the state version recorded at creation, and
`merged_into_id: null`. Content comes from the immutable revision 1, and the creation-time state comes from the key
record. A creation currently always yields revision 1, state version 0, and no merge target; the key record stores the
revision and state version so that a replay does not depend on that invariant. Later revision, forgetting, merging,
restoration, or retirement does not change the replay, and a replay never substitutes the current head. Callers read
current state through the Atomic Memory read operations.

For a keyed creation, the original response is built through the same exact-revision read, so a revision committed
concurrently between creation and response changes neither result. Requests without a key keep their existing response
construction. The response carries no replay marker; a caller that must distinguish a replay compares the returned
identity with its own records.

### Authorization

The `scope.contribute` check runs before key lookup, as for any creation. A denied caller receives `403` without
learning whether the key exists. Because the principal is part of the namespace, another principal cannot hit or probe
a key.

A replay reads revision 1 with the caller's current context through the same authorized exact-revision read as Get
Artifact Revision for the `atomic-memory` Family. If the caller can no longer read that memory, for example after its
access was revoked, the request returns that read's error under the common security policy: `403 forbidden` for a
denial, or `503 artifact_owner_pending` while ownership is not yet established. `404 artifact_not_found` occurs only if
revision 1 cannot be found, which does not happen in normal operation because revision 1 is immutable and is never
physically deleted. It neither discloses the record nor creates a new memory, and the key stays bound until it expires.

A conflict discloses only that the caller's own key in that Scope was already used for different content. Other
operations also return `idempotency_conflict`, so callers identify this case by `details.kind`.

### Atomicity and failures

Request validation, authorization, writer validation, and digest computation finish before lookup. Lookup is one
primary-key read in its own read transaction and uses the database clock. Only a miss proceeds to the existing
preparation outside the write transaction, including Embedding generation.

The write transaction first deletes the record for the same namespace only if its `expires_at` is not later than the
current database time, so a live key committed concurrently is never removed. It then inserts the key record, writes
the Sources, revision 1, governance state, Owner, and search and evidence projections as before, and commits everything
together. The key record holds the namespace digest, request digest and its version,
`scope_id`, `artifact_id`, revision, creation-time state version, `created_at`, and `expires_at`. It stores no memory
body.

- A validation, authorization, or preparation failure, or cancellation before commit, leaves no key, Source,
  Artifact, Owner, or projection. A retry with the key runs the creation again.
- A failure inside the write transaction rolls back the key with everything else.
- A response lost after commit, including a cancellation whose outcome the caller cannot observe, leaves the key bound.
  A retry replays the committed result.
- Concurrent requests with one key race on the key record's primary key. The loser rolls back, rereads the key in a
  new transaction, and returns the replay or conflict from the table above. If the reread finds no record, the
  integrity error was unrelated and is returned unchanged. The loser's prepared Embedding is discarded.

Uniqueness comes from the database constraint rather than an in-process lock, so it holds across connections,
processes, and Server instances on SQLite and on OceanBase/seekdb.

### Retention and cleanup

A key is retained for 24 hours from the database time at which its creating transaction inserts it. Replays do not
extend retention. A record whose `expires_at` is not later than the current database time is treated as absent: the
same key then starts a new creation and replaces the expired record inside that creation's transaction. A caller must
not expect a replay after 24 hours.

After a keyed creation commits, the server deletes at most 32 expired records in a separate short transaction: one
range read on the `expires_at` index with a limit of 32, then one delete by primary key that rechecks expiry. Cleanup
failure does not change the creation result. Each keyed creation adds one record and removes up to 32 expired ones, so
an expired backlog shrinks while keyed writes continue. Requests without a key do not touch the table. No operation
scans the whole table: lookup and binding use the primary key, and cleanup uses the expiry index.

### Storage and upgrade

Key records live in the new table `pc_atomic_memory_idempotency`, with the namespace digest as the primary key and a
secondary index on `expires_at`. Fixed-width digests keep the key within MySQL-compatible index limits. A new revision
of the server-resource migration chain adds the table with SQLite and MySQL schemas, manifest resources, and SQLite
fingerprint. Published revisions do not change.

At Server startup, an empty persistent SQLite or seekdb database is initialized through the full revision chain,
including the new revision. OceanBase requires an explicit `powercontext server db-migrate` job even for an empty
database. A managed database at an earlier revision makes Server startup fail with a migration-required error until
`powercontext server db-migrate apply` succeeds; startup never upgrades it implicitly. Only unmanaged storage, such as
in-memory SQLite, creates the table from the built-in table registry. Restarting or applying the upgrade again
preserves existing records.

### Acceptance

| Scenario | Expected result |
| --- | --- |
| Same key and digest | One Artifact and Source, plus one Owner when access control is enabled; every response equals the first. |
| Same key, different digest | `409 idempotency_conflict`; the original memory, Source, Owner, and projections are unchanged. |
| No key; one key in another Scope or from another principal; different keys | Independent creations. |
| `description` omitted, `null`, or equal to the default; text differing only by NFC or outer whitespace | One digest; replay. |
| Concurrent requests on separate connections or processes | Exactly one creation; the others replay or conflict by digest. |
| Failure or cancellation before commit; response lost after commit | No orphaned rows; a retry creates once or replays the commit. |
| Restart, then later revision, forgetting, merging, or retirement | The replay still returns revision 1 and the creation-time state. |
| Revoked access; another principal | No disclosure and no write: the exact-read error, or an independent namespace. |
| Retention boundary and expired-key reuse | Expired at `expires_at`; reuse creates a new memory; cleanup removes at most 32 indexed records per keyed write. |
| Fresh database; upgraded managed database | Real Server startup and `db-migrate` succeed; repeated startup or upgrade keeps records. |

## Upgrade and compatibility

Existing deployments upgrade offline under [RFC #1771](https://github.com/oceanbase/powercontext/pull/1771). Its unified
process governs schema changes, data conversion, projection rebuilding, archive retention, and old-table cleanup.

Each legacy entry's complete content history, original direct evidence, and current lifecycle state migrate to Atomic
Memory. Existing entry tags and Owners are retained. Valid entry sharing grants migrate automatically, preserving
relationship identities, permissions, and revoked or expired status. Atomic follows the same Owner requirements as
other Artifacts: migration does not invent Owners when authorization is disabled, and deployments with authorization
enabled use the shared authorization rules.
Source processing progress remains valid without re-extracting processed Source. Entries that were recoverable or
non-reactivatable retain those respective semantics. Current content cannot stand in for missing historical versions.

Exact entry references still used by business operations become Atomic `ArtifactRef` values. They read the specified
content revision without following the current head. Legacy random version IDs have no online aliases. Clients must
use the migrated Artifact identity and revision rather than submit legacy `MemoryCitation` values.

Old collection content, snapshots, and necessary metadata leave the online Artifact system for a new dedicated archive
table. It preserves original data for offline verification and provides no API compatibility. Before removing
whole-collection evidence from online lineage, the archive records where each reference came from and its association
with the original collection. Such evidence is not expanded into members or Source, or reinterpreted as a single
Atomic Memory. This explicitly ends online reading and tracing of old collections; exact entry evidence is still
converted as described above. The archive does not participate in queries,
search, or generation and cannot replace an upgrade backup. Rollback, archive retention, and cleanup follow RFC #1771.

If removing whole-collection evidence makes a Handoff, Work, or Candidate record violate existing nonempty-evidence
constraints, preflight blocks migration unless a maintainer decision file covers every such item. A decision may
replace the reference with explicit exact `ArtifactRef`s, downgrade a verified Work claim or check to declared, or
reject a pending Candidate; the original value is archived. This proposal does not introduce historical variants of
business records to bypass those constraints.

Only five legacy server entry points, `memory.get/list/search/remember/flush`, retain adapters. Memory results use
Atomic identities and revisions:

- `get` may locate the current Atomic head using the legacy collection ID and entry ID. Exact historical reads use
  the new `ArtifactRef`.
- `list` and `search` query Atomic Memory in the Scope using the new model's listing, state, and search semantics.
- `remember` supports simple creation only, without legacy entry updates, collection revision appends, or evidence
  referring to old objects. It accepts an optional idempotency key, as described in Idempotent explicit creation.
- `flush` continues extraction into Atomic Memory using the preserved Source processing progress.

All other legacy entry points explicitly stop accepting the old model. These include generic `family=memory`
operations, old tag targets and their ETags, old access selectors, collection CAS, and old cursors. Requests cannot be
accepted by ignoring concurrency preconditions or filters. Clients use Atomic and shared Artifact APIs for reading,
writing, tags, and authorization, and restart queries and pagination under the new contract. Authorization and execution
target the same Atomic Memory.

The SDK and runtime do not retain the legacy Memory domain model, `MemoryCitation`, or other legacy typed Memory references.
Terminal Dream runs with old structures are converted offline into explicit untyped historical data for display only,
without evidence resolution, execution, or recovery. Historical receipts retain unavailable collection addresses in
`unavailable_evidence` as descriptions of what was unavailable. These addresses are not resolved or accepted as new evidence;
exact entry citations are converted to Atomic references.
Frozen legacy decoding belongs only to offline migration tools. The runtime keeps no legacy decoder and depends on
neither old tables nor online mapping tables.

Changed semantics of retained entry points, unsupported operations, replacement calls, and retirement arrangements are
declared under RFC #1771. Compatibility does not promise unchanged clients. Each release declares compatibility periods
and archive retention conditions; cleaning up old collection storage does not authorize deleting content history
already migrated to Atomic Memory.

# Drawbacks

- Memory identities and APIs require adaptation. Complete historical migration needs downtime and additional storage.
- Each merge adds an Artifact and retains its inputs and relationships. Undo can involve several memories, requiring
  permission and concurrency handling for the group.
- Many memories may meet the threshold and require batches for comparison. Thresholds and model decisions can still
  miss relationships or produce incorrect merges.
- Automatic conflict resolution using time may accept incorrect newer information, especially when missing times
  require relying on Source order. Later evolution, edits, or restoration provide correction.
- Undo removes later revisions of the merge result from normal search. Other Artifacts already referencing those
  revisions do not automatically roll back.
- Old clients must update their references and calls. Five legacy server entry points still need adapters, while
  online reading and tracing of old collections end.
- Idempotent creation adds a key table and a fixed 24-hour retention period. A retry after expiry creates a second
  memory, and a replay reports creation-time state rather than current state.

# Rationale and alternatives

**Independent Artifacts.** Scope organizes memories, while Artifact handles individual revisions, evidence, and state.
A single-memory update no longer creates a collection directory revision. Incremental manifests or smaller collections
can reduce directory copying but retain two identity layers.

**The `atomic-memory` Family.** The name expresses independently maintained memory granularity for both extraction and
manual writes. Keeping `memory` would give old collections and new individual memories the same Family meaning. A new
Family separates them while supporting compatibility for specific old operations.

**Create C and freeze A and B.** Revising A in place to absorb B can later require disentangling B's content from an A
that has continued evolving. A new C preserves explicit pre-merge inputs: edits affect C, and undo restores the inputs.
Irreversibly retiring A and B would prevent restoration of their original identities.

**Automatic merging by default.** Merging is part of Source extraction and runs automatically alongside ordinary
creation and revision. Per-operation approval or category preauthorization adds maintenance steps. This proposal uses
retained history, correction, and undo to handle mistakes without requiring users to approve each model merge decision.

**Resolve conflicts automatically using time.** Atomic memories are produced continuously in large numbers. Leaving
each contradiction for users to resolve would accumulate maintenance work. The model updates current memories using
time, with history and restoration supporting correction, without a separate unresolved-conflict workflow.

**Idempotency key on explicit creation only.** A lost `remember_memory` response is the case in which a caller cannot
tell whether its write happened. Content-based deduplication would merge writes the caller intended to keep separate,
and a cross-Family idempotency framework would have to define replay for revision, merge, and restoration, whose
results depend on current state. A digest over stored content treats requests that write the same memory as the same
request. Replaying revision 1 with its creation-time state keeps a retry's result independent of later changes;
replaying the current head would let a retry return content the caller never wrote.

# Prior art

- [RFC 1345](1345_scope_organization_and_agent_integration.md) and [1549](1549_artifact_family_unification.md) provide
  Scope organization and shared Artifact capabilities.
- [RFC 0019](0019_local_source_memory_runtime.md) defines Source consumption and extraction progress.
- [RFC 1417](1417_topic_memory.md) generates candidates before selecting related Topics, informing related-memory retrieval.
- [RFC 1652](1652_memory_quality_and_lifecycle.md) defines evidence, validity, conflict, and lifecycle rules. This proposal
  changes its merge approval and conflict-retention requirements for Atomic Memory.
- [RFC 1718](1718_memory_capacity_contract.md) describes complete-manifest capacity costs and historical protection.
- [RFC #1803](https://github.com/oceanbase/powercontext/pull/1803) defines the common search contract, and
  [RFC #1771](https://github.com/oceanbase/powercontext/pull/1771) defines the unified migration process.

# Unresolved questions

- When a user corrects a memory through an Agent, should Source retain the corrected memory's reference and surrounding
  context? Can existing Agent integrations and capture paths provide them, and which integrations need changes?
- Should other Artifact families adopt these four states? How should the existing `deprecated` state map to forgotten
  and merged? This RFC defines Atomic Memory behavior; adoption and adaptation by other families remain separate decisions.

Physical deletion of Atomic content history, automatic merging across Scopes, and whole-Scope snapshot rollback are
outside this proposal. Apart from the idempotent creation protocol above, table schemas, API parameters, locking, and
transaction implementation belong in the implementation design. Migration follows RFC #1771.

# Future possibilities

A management interface could visualize memory revisions and merge relationships to help users inspect original
content, compare revisions, and choose restoration targets. Richer history views do not change these state and
restoration rules and are not prerequisites for basic preview and restoration capabilities.
