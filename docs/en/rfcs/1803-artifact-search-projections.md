---
title: Artifact Search Projections and Join-Free Retrieval
---

- Proposal Name: `artifact_search_projections`
- Start Date: 2026-09-30
- RFC PR: [oceanbase/powercontext#1803](https://github.com/oceanbase/powercontext/pull/1803)
- Amends RFCs: [0014](0014_memory_layer_design.md), [0051](0051_experience_skill_artifact_families.md),
  [0080](0080_memory_search_reranking.md), [1417](1417_topic_memory.md)
- Related RFCs: [1396](1396_handoff_access_control.md), [1467](1467_artifact_tags.md),
  [1549](1549_artifact_family_unification.md), [1652](1652_memory_quality_and_lifecycle.md)

# Summary

This RFC defines a common query boundary for searchable Artifact Families: **full-text and vector retrieval statements
must not use business-table joins**. Matching, eligibility filtering, candidate ordering, and truncation cannot depend
on joins to authoritative history, heads, tags, or other business tables.

Each Family should use a search wide table or separate current search projections. Tables may be separated by full-text
and vector capabilities or by search granularity, such as topics and chunks. All data need not occupy one physical table.
After candidates are selected, content and display fields may be fetched in batches using exact references.

Authoritative revisions and heads retain their identity, history, and current-version responsibilities. Search projections
are rebuildable derived data maintained synchronously with authoritative state. Memory, Topic Memory, Experience, and
Skill follow this contract.

# Motivation

## Joins affect search indexes and retrieval results

Families already maintain partial search projections, but search still involves cross-table associations:

| Family | Current search structure |
| --- | --- |
| Memory | Separate full-text and vector projections; retrieval joins entry versions for content and uses correlated tag filters |
| Topic Memory | Separate Topic/chunk full-text and vector projections; vector candidates join display content after truncation |
| Experience | Full-text matching on common heads, followed by a join to authoritative revision content |
| Skill | The same full-text retrieval path as Experience |

In OceanBase, a distance-ordered vector scan participating in a merge join causes nearest-neighbor loss. Joining full-text
search with other business tables also degrades the full-text index path. Topic Memory already limits ANN candidates
before joining content to work around the vector merge-join issue, showing that candidate selection and content reads
can be organized separately.

Families need a clear retrieval boundary that keeps business associations for content, current state, and tags out of
full-text and vector retrieval plans.

## Search data has different maintenance costs

Full text, vectors, and multivalued tags use different indexes. Topics and chunks also have different search granularities.
Requiring one physical table couples content duplication, vector configuration, tag updates, and index lifecycles.
Colocating fields alone does not ensure that multivalued tags and vector filtering combine efficiently.

Wide tables suit direct return of complete results. Separate projections suit independently maintained capabilities and
granularities. The common contract should allow Families to choose an appropriate layout.

## Eligibility and content completion need different boundaries

Content can be fetched in batches after candidates are selected. Scope, tags, lifecycle, and access eligibility affect
which objects qualify as candidates. Applying them only after taking a global top-k lets ineligible objects consume
candidate slots and can exclude relevant eligible objects.

Allowing separate tables therefore requires guarantees for eligibility, exact references, and read consistency.

# Guide-level explanation

## Use a wide table or separate projections

A Family may store search fields and response content in its own wide table and return complete results directly.
Alternatively, it may maintain separate current full-text and vector projections, retrieve exact references, match
positions, and scores, then fetch content in batches.

Topic Memory can retain separate topic and chunk search granularities. Chunk hits still reference the exact Topic
revision, preserving candidate budgets, result folding, and scoring semantics. Full-text-only Experience and Skill do
not need vector projections.

The corresponding Family owns these layouts. Families do not have to share one search table.

## Return complete, consistent results

Search interfaces continue returning their promised content, metadata, and exact version references. With separate
projections, the service performs batch content reads; callers need not request details per hit. Content reads use the
version selected during retrieval and cannot replace it by resolving latest again.

After publication, deactivation, or tag changes, search selects candidates from consistent current state. Historical
references remain readable under access control. Search projection layout does not affect exact historical reads.

## Preserve tag filtering semantics

Tag management may retain independent authoritative assignments. Search uses synchronously maintained tag projections
or complete, bounded filter inputs established before retrieval to apply existing all/any matching semantics during
candidate selection.

It cannot arbitrarily truncate the tag-matching object set before ranking or join tags after retrieval to filter results.
Deployments without embeddings retain existing full-text search. Backend capability boundaries explicitly define the
supported combinations of filtering and retrieval modes.

# Reference-level explanation

## Core contract

1. **No business joins in retrieval statements.** Full-text and vector candidate selection must not join authoritative
   history, heads, tags, or other business tables. Correlated subqueries cannot bring those associations back into retrieval.
2. **Multiple current projections are allowed.** Family-owned wide tables or separate projections are recommended.
   The Family and backend determine physical table counts, whether full text and vectors share storage, and search granularity.
3. **Eligibility participates in candidate selection.** Scope, tags, lifecycle, and access control retain their semantics.
   Filtering after truncation cannot replace retrieval of eligible candidates.
4. **Content completion runs separately.** Once candidates exist, separate batch reads may complete their content.
   Business tables must not be joined in the same statement that performs full-text or vector retrieval. Content reads
   do not redetermine eligibility, the current version, or relevance ordering.
5. **Current state is consistent.** Authoritative changes and affected projections update atomically. All retrieval
   channels and content reads in one search share a consistent committed state.
6. **History and budgets are preserved.** Rebuilding changes no authoritative identities, historical content, exact
   references, or Source processing positions. Candidates, filter-input transfer, content reads, and fusion respect
   budgets, without per-hit queries or unbounded object lists connecting stages.

## Responsibilities and scope

`pc_artifacts` and `pc_artifact_heads` retain their existing roles. Projections maintained by Family writers establish
currentness; retrieval does not join heads to identify the latest revision. Candidates carry exact identities sufficient
to identify their original content and match positions.

Content completion may read the corresponding current content projection or authoritative exact-version records.
It must not silently substitute another revision, omit missing content, or interpret inconsistencies as empty results.
Returned content must correspond to the candidates used for scoring.

Independent changes to tags and other eligibility information also update every affected projection synchronously.
Metadata changes need not all create content revisions. Existing Scope-level authorization can run before search;
changing search storage does not require changing the authorization model.

Backends may implement search with native or auxiliary indexes. Internal database index access is not a business-table
join prohibited by this RFC. Application queries still respect the boundary between retrieval and content reads.
OceanBase, SQLite, and other backends retain their physical implementations without weakening eligibility or consistency.

The contract covers existing and future searchable Families. Domain contracts own content generation, evidence semantics,
and semantic merging. Public return content, filters, and scoring contracts remain unchanged.

## Upgrade and compatibility

The initial upgrade uses a separate offline migration tool. Migration must be resumable, repeatable, and validated before
service resumes. Databases with incomplete migration cannot serve ordinary search. The online Runtime does not backfill
data, dual-write, or switch between old and new layouts.

Search projections are rebuilt from authoritative data without creating content revisions or re-extracting processed
Sources. Existing exact references and access control are preserved. Historical content reads do not depend on historical
vectors.

# Drawbacks

- **Projection maintenance costs.** Wide tables may duplicate content. Separate projections still duplicate some
  eligibility attributes and require synchronous maintenance of multiple current representations.
- **Read coordination.** Batch content completion adds a read and must use the same versions and consistent state as retrieval.
- **Index combination limits.** Backend capabilities determine how multivalued tags combine with vector retrieval.
  Removing joins does not eliminate filtering or scanning costs.
- **Upgrade downtime.** Projection migration and index rebuilding require extra space and a maintenance window.
  Authoritative history continues to grow independently.

# Rationale and alternatives

## Constrain retrieval while allowing projection choices

Wide tables reduce content lookups and coordination between copies, suiting Families whose response fields align with
search units. Separate projections allow independent maintenance of full text, vectors, topics, and chunks, reducing
large-field duplication and separating index management. This proposal recommends both layouts under the same query
boundary and consistency responsibilities.

## Require exactly one wide table per Family

This simplifies direct result return but restricts independent evolution of search granularities, vector configurations,
and backend indexes. It also cannot replace multivalued-tag index design, so it is not a mandatory physical requirement.

## Keep business associations in retrieval

This reduces some duplication but retains OceanBase nearest-neighbor loss and full-text index degradation. Expressing
associations as correlated subqueries or outer joins in the same retrieval statement still combines business tables
with the retrieval plan. Separate batch content reads establish an explicit boundary between the two stages.

# Prior art

[RFC 1417](1417_topic_memory.md) provides current Topic/chunk projections, separate retrieval channels, and atomic
publication as a foundation for independent projections. [RFC 0051](0051_experience_skill_artifact_families.md) defines
Experience/Skill content and admission. [RFC 1467](1467_artifact_tags.md) and
[RFC 1396](1396_handoff_access_control.md) define tag and authorization semantics that must be preserved.

# Unresolved questions

- Initial backend versions and the supported combinations and cost boundaries of multivalued tags, full text, and vectors.
- Each Family's choice of wide tables or separate projections, and budgets for duplicated content, metadata updates,
  and batch content reads.
- Acceptable migration windows for large deployments and upgrade boundaries for different vector configurations.

# Future possibilities

- Cross-Family search that fuses bounded candidates from each Family.
- Independent evolution of vector configurations, index rebuilding, and content-read strategies.
- A commercial edition may offer online upgrades through a designated barrier release. Older versions must finish
  migration there before upgrading further, allowing later binaries to remove historical online migration logic.
  Barrier releases and upgrade paths remain available throughout the promised support period.
