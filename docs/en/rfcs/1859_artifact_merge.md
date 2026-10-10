- Proposal Name: `artifact_merge`
- Start Date: 2026-10-07
- RFC PR: [oceanbase/powercontext#1859](https://github.com/oceanbase/powercontext/pull/1859)

# Summary

Extract Atomic Memory's merge and undo into shared operations backed by one common set of database tables for merge state,
input-to-result relationships, and undo records. Every Artifact Family can integrate with and reuse these operations and tables
without creating its own merge, relationship, or undo tables. All Artifact creation and revision use the common write layer
(`ArtifactRepository` and its shared persistence implementation), with content and revisions remaining in existing Artifact storage.
Each Artifact's business logic decides whether to initiate a merge.

# Motivation

[RFC #1809](https://github.com/oceanbase/powercontext/pull/1809) defines new merge results, frozen inputs, preserved history,
and undo. The implementation branch in [PR #1857](https://github.com/oceanbase/powercontext/pull/1857) stores related state in an
Atomic Memory-specific table and determines input revisions from merge information in the result's first revision and lineage.

Other Artifacts need the same information when merging: which inputs were used, which result was created, when inputs became
frozen, and which objects undo should restore. Common Artifact identities and revisions can express this information without
separate tables and repeated relationship-management and undo implementations for each Family.

This proposal moves those capabilities into the shared layer. Atomic Memory also uses the same operations and tables for merge
and undo.

# Guide-level explanation

## Merge

A caller supplies exact revisions of at least two distinct Artifacts in the same Scope and Family, plus the merged content.
The shared operation creates a new Artifact C, records the relationships from A and B to C, and freezes A and B.
A and B leave normal use while their content and history remain readable through exact revisions. Subsequent revisions affect C.

The caller decides how to produce the result content. Whether initiated by a user or an automatic task, merging uses the same
shared operation and writes to the same common tables.

## Undo

To undo the merge that created C, the shared operation reads its inputs from the merge record and restores A and B to their
pre-merge states. Each restored input appends a revision under its existing Artifact identity, using its frozen content.
An explicit `restore` may select a historical revision only for its target; `undo_merge` does not accept a historical revision.
C also appends a revision under its existing identity to record retirement. Each new
revision is the Artifact's current revision plus one; existing revisions and the original merge relationships remain intact.
Later revisions of C are not automatically distributed back to A and B. C's history remains readable, and its retired identity
cannot be reactivated. Default effective reads follow the new revisions.

Successive merges follow currently effective relationships. If A and B form C, and C and D form E, restoring B undoes E and then C,
restores A, B, and D, and retires C and E. Undoing only E restores C and D while A and B remain frozen.
Ordinary evidence references do not trigger cascading undo, and external operations that used a result are not rolled back.

A caller can preview the impact before undo or request undo directly, letting the server calculate and return the actual impact.
Execution based on a preview must reject it if relevant revisions, states, or relationships have changed rather than expanding
the confirmed scope.

If no state changes are needed and no historical revision is selected, the operation is a no-op and does not append revisions.

# Reference-level explanation

## One common set of tables

Common tables store merge information for Artifacts, identifying objects by Scope, Family, Artifact identity, and exact revision.
They are not tied to any Family's content format. Shared storage must represent:

- The inputs, result, and exact revisions used by each merge.
- Input freezing, pre-merge states, and each input's current merge destination.
- Whether merge relationships remain effective and how successive merges are connected.
- Undo records with exact references to the revisions actually restored or retired and the frozen or selected historical
  revisions used for restoration. Existing revision and provenance records can retain these references without a separate
  operation ledger. The actual group result remains queryable by the operation's primary exact revision, subject to read
  permission checks on the affected Artifacts.

The shared layer maintains these authoritative records for merge and undo. Content stays in existing Artifact revisions and is
not duplicated in the common tables. Ordinary lineage continues to record evidence; it cannot replace merge records or establish
that an Artifact has been merged.

Integrating a new Family requires neither Family-specific merge-state, relationship, or undo tables nor changes to the common
table structure. Families must not maintain another independent authoritative set of merge records. Derived state used for
retrieval must remain consistent with the common records. The number of tables, fields, indexes, and physical layout belong to
subsequent implementation design.

## Shared operations and integration

All Artifact creation and revision ultimately pass through the common Artifact write layer, which maintains revisions and
checks shared merge state. Families can retain their business entry points but cannot bypass the common write layer to modify
Artifact content, revisions, or merge state.

The shared layer also provides merge, relationship queries, undo preview, and undo execution. It owns freezing, restoration,
relationship traversal, and concurrency checks. Each Artifact's business logic decides whether and when to merge, which inputs
to select, and how to generate the result. When a merge is needed, the caller explicitly submits input references and result
content. The shared merge operation reuses the same write foundation and updates the associated states and relationships.
A new Family does not copy the merge algorithm, implement dedicated merge storage, or add name-based branches to the shared entry
point.

The common write layer does not search for similar Artifacts, decide to merge, or create merge relationships on an ordinary
create or revise operation. Ordinary revision centrally rejects changes to frozen inputs; only explicit merge or undo operations
perform the corresponding relationship and state transitions. Default reads and search also honor merge state: frozen inputs and
retired results cannot be used as current effective Artifacts. Authorized exact historical reads always return the original revision
without redirecting to the merge result. Integration does not require enabling automatic retrieval, content generation, or merging.

## Consistency

Merge and undo check permissions, revisions, states, and relationships for every affected Artifact. Reads during content generation
do not replace publication-time checks. Result publication, input freezing, common relationships, and associated current state
become effective together. Undo is also effective as a group; failure must not leave partially frozen or restored inputs.
The revisions appended for restoration and retirement and their current state become effective in the same transaction.
An input can belong to only one currently effective merge result. Concurrent operations cannot merge the same input into two
simultaneously effective results.

## Integration and acceptance

Atomic Memory's merge capability uses shared operations and common tables from its initial delivery, implementing the merge and
undo behavior of RFC #1809. Common records are authoritative for merge and undo. Undo does not rewind Source consumption progress.

Acceptance should cover Atomic Memory and another Family, confirming that both use the same operations and common tables without
adding dedicated merge tables. It should also verify that ordinary writes do not trigger merges, that business entry points
cannot bypass freeze checks, and that successive merges, cascading undo, stale previews, concurrent competition, group failure,
and exact historical reads behave as specified. Restoration and retirement must append the next revision under each existing
identity, preserve exact undo references, and leave unchanged requests without a selected historical revision as no-ops.

# Drawbacks

Extracting shared operations from Atomic Memory requires separating the parts tied to that Artifact type and verifying that
multiple callers can reuse the same behavior. Once operations and tables become shared dependencies, subsequent changes require
validation across all integrated callers.

# Rationale and alternatives

- **Shared operations and common tables.** Maintain merge and undo together and let subsequent Families reuse storage without
  repeating table definitions.
- **A common interface with separate Family tables.** Standardizes calls but still duplicates relationship storage and undo logic,
  failing this proposal's reuse objective.
- **Lineage alone.** Records evidence but cannot distinguish references from merges or determine effective relationships and undo scope.

# Prior art

RFC #1809 and PR #1857 provide the behavioral basis for merge and undo, which this proposal makes shared.
[RFC #1549](1549_artifact_family_unification.md) provides a direction for Family capability registration that can support
integration with the shared operations.

# Unresolved questions

Implementation design will determine the common table structure, reuse of existing common storage, and operation parameters.
These choices must satisfy the requirement that a new Family does not need its own merge tables.

This proposal excludes cross-Scope or cross-Family merging, physical deletion, and rollback of external effects.

# Future possibilities

Common merge records can support shared relationship graphs, operation history, and undo impact views without separate implementations
for each Artifact type.
