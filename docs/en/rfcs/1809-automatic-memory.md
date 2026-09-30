---
title: Automatic Memory as Independent Artifacts
---

- Proposal Name: `automatic_memory`
- Start Date: 2026-09-30
- RFC PR: [oceanbase/powercontext#1809](https://github.com/oceanbase/powercontext/pull/1809)
- Depends on: [Artifact Search Projections and Join-Free Retrieval, #1803](https://github.com/oceanbase/powercontext/pull/1803)
- Amends: [0014](0014_memory_layer_design.md), [0019](0019_local_source_memory_runtime.md),
  [1345](1345_scope_organization_and_agent_integration.md), [1652](1652_memory_quality_and_lifecycle.md),
  [1718](1718_memory_capacity_contract.md)
- Related: [1417](1417_topic_memory.md), [1549](1549_artifact_family_unification.md)

# Summary

This RFC introduces the `automatic-memory` family, representing each independently maintained memory as an Artifact.
Scope owns membership; each memory has its own identity, revisions, evidence, and lifecycle. Extraction generates
candidates, then uses bounded retrieval to find related current memories within the same Scope and authorized read
boundary before coordinating creation, revision, consolidation, and conflicts. Search follows the join-free retrieval
and current projection contract in prerequisite RFC #1803. Existing deployments upgrade offline with an independent tool
that migrates complete entry history and preserves exact old references. The new Runtime does not perform online copying
or dual writes.

# Motivation

## Memory collections duplicate Scope and Artifact responsibilities

In [RFC 0014](0014_memory_layer_design.md), each Memory Artifact contains a complete directory whose members reference
immutable entry versions. Changing one memory also commits a collection revision. After
[RFC 1345](1345_scope_organization_and_agent_integration.md) introduced Scope, Memory retained its single active
collection. A fact therefore has both collection and entry identities, with two version layers.

Scope already owns organization, while Artifact provides identity, history, evidence, authorization, and tags.
Representing facts directly as Artifacts unifies their maintenance with other families and reduces Memory-specific
version and reference rules.

## A single change still creates a complete directory history

The current implementation supports entry revision, historical reads, capacity limits, optional compaction, and
incremental search projection updates. Content and vectors can be updated incrementally, but each effective change still
constructs and persists a full manifest. Continued writes accumulate directory copies of unchanged entries, and writers
changing different entries depend on the same collection head.

Capacity limits bound collection size without changing the versioning unit. Independent memories make historical growth
correspond to the facts that actually change.

## Extraction context grows with active memory

Built-in extraction currently gives the model every active entry of the selected Memory head and can revise memories
from earlier Source windows. As memories accumulate, input costs grow. A separate related-memory search after candidate
generation and complete boundaries for consolidation and conflict handling are also missing.

Extraction should select related memories for each candidate. An entry replacement endpoint alone cannot resolve
context growth or coordination across memories.

# Guide-level explanation

## Maintain each memory independently

A Scope can contain separate memories stating that production releases require owner approval and that the default
deployment region is East China. Adding a release security check advances only the first memory's revision. The second
stays unchanged. An exact old reference still returns the original content; search returns the current searchable revision.

Automatic Memory can be extracted from Source or written manually by an authorized caller. The word `automatic` does not
restrict how the Artifact is produced.

## Coordinate new input with related memories

Processing Source first generates candidates, then retrieves related current memories within the same Scope and
authorized read boundary. Extraction can create a fact, supplement or correct an existing fact, propose consolidation,
or make no write when there is no new content or evidence. Contradictions with uncertain applicability or ordering remain
unresolved conflicts.

Similarity selects related objects; it does not establish that facts should be merged. Consolidation must preserve valid
evidence and applicability conditions and obey authorization and review rules. One bounded search does not promise to
find every duplicate. Incomplete coordination and unresolved conflicts must remain available for further processing.

## Preserve history throughout the lifecycle

Forgetting removes a memory from default search; explicit reactivation can restore the same identity. Restoring old
content creates a new current revision with its restoration source, leaving history immutable. A retired identity cannot
be ordinarily reactivated; adopting its content requires a new identity with preserved provenance.

After consolidation, old references to deactivated memories still return their historical content. Reading historical
text does not depend on the vector service. Search after restoration follows the public search contract.

## Upgrade existing deployments offline

Upgrade requires a maintenance window, a consistent backup, and an independent migration tool. The new release opens
for business only after historical conversion and validation succeed. Previously consumed Source is not extracted again,
and exact historical references in Handoff, Experience, and other existing Artifacts remain usable.

The old `memory` name can resolve to `automatic-memory` for explicitly supported operations. An old collection reference
still denotes a collection and cannot directly designate one new memory. Callers relying on collection semantics without
an explicit compatibility rule must upgrade.

# Reference-level explanation

## Domain boundaries

- Scope defines memory membership and access boundaries. `automatic-memory` is the Family for independent memories,
  each using ordinary Artifact identity and revisions without a versioned manifest for the whole Scope.
- Effective changes to content, direct evidence, or lifecycle add history only for affected memories. Consolidation
  and restoration preserve exact provenance; migration must not reinterpret historical evidence under present rules.
- Source consumption remains owned by processing bindings within the Scope, without a separate position for each
  memory. Advancing input positions must not lose accepted work.
- Current-memory capacity and historical retention are governed separately. At capacity, revisions that do not increase
  usage and operations that reduce usage remain possible. Limits must not be met by discarding history.

These changes replace the collection and entry-specific version models in RFCs 0014 and 0019, amend RFC 1345's single
active Memory collection, and change RFC 1718's capacity unit. They retain RFC 1549's shared Artifact capabilities and
RFC 1652's evidence, review, and lifecycle boundaries.

## Extraction and write constraints

Candidate generation, related retrieval, and coordination have explicit budgets. The default model context does not
contain every active memory in the Scope. Deployments without vectors still use bounded retrieval. Creation, revision,
consolidation, no-write outcomes, and unresolved conflicts have distinct results; exhausting a budget does not mean
there is no new memory.

Ordinary creation and revision retain extraction authorization and write gates. Semantic consolidation requires review
per operation by default and does not run automatically by default. This RFC makes a limited amendment to RFC 1652:
a Scope administrator may preauthorize compatible consolidation about the same subject under the same applicability
conditions, with all evidence preserved. Mandatory review, unresolved conflicts, operations outside that authorization,
and irreversible retirement still require individual handling. Model judgments cannot grant authority, and explicit calls
cannot bypass mandatory review.

Concurrent writes and retries must not overwrite later changes, duplicate a request's result, or publish a partial
consolidation. Changes to the content, evidence, or permissions used for coordination require rechecking write eligibility.
This proposal does not prescribe the concurrency mechanism.

## Public search dependency

[RFC #1803](https://github.com/oceanbase/powercontext/pull/1803) is a prerequisite. Automatic Memory owns its current
search data and may use a wide table or separate full-text and vector projections. Retrieval statements use no
business-table joins; tags, state, and other eligibility conditions participate in candidate selection. After candidates
are selected, separate batch reads may fetch content by exact reference under the same consistent state. Content
completion cannot filter candidates again.

Current projection updates, rebuilding, and visibility follow #1803. This RFC neither redefines the public search
contract nor owns domain-model changes for other families.

## Compatibility and migration commitments

Public APIs and consumers adopt individual Artifact identities and stop assuming that every memory in a Scope shares
one collection reference. Old-name compatibility is centralized and distinguishes current Family selection from exact
historical references according to the operation. Authorization and execution use the same interpretation of identity.
Name compatibility does not promise equivalence with old collection APIs.

Migration belongs to an explicitly invoked independent tool. Service binaries do not retain online copying, dual-write,
or catch-up flows. Supported old releases must still have an available tool and upgrade path. Old writes stop during
migration; failed or incomplete validation prevents the new release from opening for business.

Migration includes every entry's content, evidence, and lifecycle history, together with exact old references, access
controls, tags, and processing positions. It preserves old collection snapshots, existing Artifact references, and the
non-reactivatable semantics of retired entries, without extracting consumed Source again. Migration supports
safe retries. Missing or unexplained history must be resolved before cutover rather than silently discarded.

Once the new release accepts writes, old collections are no longer synchronized. Deployment downgrade requires a
separate reverse-migration arrangement and is distinct from restoring a memory's historical content.

# Drawbacks

- Identities, interfaces, and consumers must upgrade. Complete historical conversion requires downtime and extra storage.
- Preserving old history and exact references leaves a lasting compatibility burden. Stopping full-manifest writes does
  not immediately reclaim their existing storage.
- Bounded retrieval can miss related memories, and consolidation can lose qualifications, requiring evidence, review,
  and subsequent maintenance.
- Independent memories still require consistency across memories, concurrency handling, and capacity governance.
  Renaming entries alone does not complete the change.

# Rationale and alternatives

**Independent Artifacts.** Reusing Scope membership and Artifact identity, history, and governance makes each fact the
maintenance unit, consistent with other families.

**Keep the `memory` family.** This reduces naming changes but leaves collection and individual semantics under one
Family, complicating capability discovery, interfaces, and historical reads. A new Family distinguishes the models;
controlled entry points provide compatibility for the old name.

**Incremental manifests or smaller collections.** These reduce directory costs or limit collection size while retaining
collection versions, entry-specific references, and coordination across collections. This RFC chooses individual facts
as the long-term maintenance unit.

**Migrate current content only.** This shortens upgrade time, but new identities have history only from import onward.
Complete migration preserves continuous content, evidence, and lifecycle history at the cost of more offline work.

**Online migration.** This reduces downtime, but distributed binaries supporting direct upgrades from old releases must
continue carrying or invoking copying and cutover capabilities. An independent offline tool removes that ongoing
maintenance responsibility from normal service execution.

**Keep the current model.** Capacity limits and compaction can continue bounding collections, while two version layers,
full directory histories, and special reference rules remain.

# Prior art

- [RFC 1345](1345_scope_organization_and_agent_integration.md) and [1549](1549_artifact_family_unification.md) provide
  Scope membership and shared Artifact capabilities.
- [RFC 1417](1417_topic_memory.md) provides candidate generation and related Topic selection as coordination precedents.
- [RFC 1652](1652_memory_quality_and_lifecycle.md) distinguishes evidence, validity, similarity, and conflict, and defines
  review and recoverable deactivation.
- [RFC 1718](1718_memory_capacity_contract.md) describes full-manifest costs, capacity rejection, and historical protection.
- [RFC #1803](https://github.com/oceanbase/powercontext/pull/1803) defines the shared current projection and join-free
  retrieval contract.

# Unresolved questions

- Supported versions for old collection interfaces, exact historical reads, and migration tools, and the public protocol
  upgrade boundary.
- Configuration policies for current-memory capacity and historical retention, including capacity excess after combining
  old collections.
- User-facing handling of unresolved conflicts and consolidation review, including downstream effects that cannot be
  compensated automatically.

Physical deletion, automatic consolidation across Scopes, online upgrade, and implicit whole-Scope snapshot rollback
are outside the current scope. Table schemas, interface forms, concurrency mechanisms, and migration steps belong to
subsequent implementation design.

# Future possibilities

- Bounded background maintenance can find near-duplicates and conflicts missed during extraction while preserving the
  same evidence and review boundaries.
- Historical retention, physical cleanup, and explicit Scope export can be defined separately without reintroducing
  complete directories on every individual write.
- A commercial edition may offer online upgrades through a mandatory barrier release. Migration must finish before
  upgrading further, allowing later binaries to remove historical online migration code. The barrier release and its
  tools must remain available throughout their promised support period; merely installing that release does not mean
  migration is complete.
