---
title: Atomic Memory as Independent Artifacts
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
  referring to old objects.
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
outside this proposal. Table schemas, API parameters, locking, and transaction implementation belong in the implementation
design. Migration follows RFC #1771.

# Future possibilities

A management interface could visualize memory revisions and merge relationships to help users inspect original
content, compare revisions, and choose restoration targets. Richer history views do not change these state and
restoration rules and are not prerequisites for basic preview and restoration capabilities.
