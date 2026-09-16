---
title: Complete the Topic Memory generic Artifact APIs
---

# Complete the Topic Memory generic Artifact APIs

- Proposal Name: `topic_memory_generic_api_completion`
- Start Date: 2026-09-15
- Status: Proposed
- Inspected code: `9ff03eedffe6e73a54bb3adf40d42badd7cacdf7`
- Related implementation: [Standard Topic Memory reads #1550](https://github.com/oceanbase/powercontext/pull/1550)
- Business requirements: [Memory requirements](https://yuque.antfin.com/obopensrc/knowledge_sharing/tccng2l0qzc6iuw0)

## 1. Summary

Support `topic-memory` through generic Artifact creation, full replacement, tag reads and writes, tag queries, filtered listing, and publication across Scopes. A successful write means the content, publication record, and all search channels enabled in the deployment have committed together.

Topic Memory remains Scope-owned. Manual writes, automatic generation, and cross-Scope publication share the family's atomic publication logic so generic reads and dedicated search observe consistent revisions. This RFC defines the target behavior and acceptance criteria for implementation.

## 2. Current capabilities and gaps

The inspected baseline supports `list_artifacts`, `get_artifact`, `list_artifact_revisions`, and `get_artifact_revision`. Topic lists contain published current heads in descending publication order, including title, summary, publication time, and direct Source count.

| Capability | Current limitation | Target |
| --- | --- | --- |
| `create_artifact` | No request variant or management writer | Create a complete searchable revision 1 |
| `replace_artifact` | No path family or management writer | Commit a complete next revision under If-Match |
| `get_artifact_tags` | Family excluded from TaggableArtifactFamily | Read logical topic tags |
| `replace_artifact_tags` | Same exclusion | Update tags independently of content |
| `query_artifact_tags` | Family excluded from queries | Include topics in exact and mixed-family queries |
| Filtered `list_artifacts` | Reader rejects tag_filter | Apply tag predicates before pagination |
| `publish_artifact` | Generic copying omits family projections; authorization uses Artifact sharing | Atomically create a complete topic in the target Scope |

`ArtifactPublicationApplication` explicitly rejects memory, profile, and prompt. `ArtifactRepository.copy_exact` writes the base revision, head, and cross-Scope provenance. Topic Memory additionally requires revision publication, active topic/chunks, and search indexes. Copying only the base state violates storage invariants; reaching that path also depends on authorization configuration. Accepting a family in the request schema alone does not establish support.

## 3. Scope

Include all seven capabilities above, OpenAPI, HTTP, Python SDK, runtime composition, authorization, error mapping, SQLite/OceanBase, FTS/vector deployment shapes, and concurrency with the Topic Memory Worker. Preserve the four existing generic reads and dedicated search/get/flush behavior.

Exclude generic deletion, archival, bulk import, PATCH, permanent manual locks, Candidate review workflows, Memory Entry APIs, tag parameters on dedicated Topic Memory search, and a new global family registry. Candidates retain their independent experience/skill/profile proposal model; topic chunks have no public entry identity.

## 4. HTTP contract

`S` and `A` below denote Scope and Artifact IDs.

| Operation | HTTP route | Success |
| --- | --- | --- |
| Create | `POST /v1/scopes/{S}/artifacts` | 201, ArtifactCreated, Location, content ETag |
| Replace | `PUT /v1/scopes/{S}/artifacts/topic-memory/{A}` | 200, ArtifactRevision, new content ETag |
| Get tags | `GET /v1/scopes/{S}/artifacts/topic-memory/{A}/tags` | 200 with tag ETag; 304 on conditional match |
| Replace tags | `PUT /v1/scopes/{S}/artifacts/topic-memory/{A}/tags` | 200, full tag set and tag ETag |
| Query tags | `POST /v1/scopes/{S}/artifact-tags/query` | 200, ArtifactTagPage |
| Filtered list | `GET /v1/scopes/{S}/artifacts/topic-memory?tag=operations&tag_match=all` | 200, ArtifactPage in publication order |
| Publish | `POST /v1/artifact-publications` | 201, exact source/target addresses and digest |

### 4.1 Create and replace requests

```json
{
  "family": "topic-memory",
  "content": {
    "title": "Order timeout handling",
    "summary": "Check payment status before cancelling an expired order.",
    "detail": "## Steps\n1. Check payment status.\n2. Escalate paid orders.\n3. Cancel unpaid orders according to policy."
  }
}
```

Replace accepts the same `content`, selects the family from its path, and requires the current content If-Match. All three fields are required, with existing maximum lengths of 512, 8,000, and 125,000 characters. Reject blank text and fields outside the request contract. Replacement does not merge missing fields.

The server chooses identity, revision, publication time, and projections. Clients cannot supply lineage, source_count, vectors, or chunks. Manual content does not invoke a generation model; vector deployments still require Embedding calls.

Valid non-blank content may contain no Analyzer v1 terms, for example when a title and summary contain only emoji or punctuation. Such content remains valid for create, replace, and publication. Internal `topic_searchable_text` may be an empty string (never NULL); full-text search returns no match for that field, while detail chunks or vector channels continue to use the actual content. Truly blank request fields remain invalid.

Create retains the generic API's non-idempotent semantics: identical titles or content may create separate topics. Clients cannot assume that retrying after a lost response returns the same Artifact. Replace uses ETag concurrency control and commits a next revision even when submitted content is unchanged.

### 4.2 Schema changes

Add CreateTopicMemoryArtifactRequest to the create union and family discriminator, and ReplaceTopicMemoryArtifactRequest to the replace union. Reuse the TopicMemoryContent field constraints. Accept the family in writable path schemas and ArtifactCreated; retain ArtifactReadFamily for reads.

Extend TaggableArtifactFamily and default TagQuery families. Raise the OpenAPI families limit from four to five. Audit every BaseArtifactFamily consumer, including authorization and response conversion, rather than relying on enum expansion to make default branches correct. Regenerate from `openapi/powercontext.yaml` with `make api-generate`, and check SDK exports and generated tool descriptions. Never edit generated files manually.

## 5. Write preparation and atomicity

### 5.1 Prepare outside the transaction

Authorize and validate, read required exact revisions, prepare chunks/FTS text/vectors, recheck preconditions and publish in a short transaction, then respond.

Add TopicMemoryManagementWriter to the existing explicit writer registry. Provide a small optional asynchronous preparation step for families that need it; existing writers need no broad redesign. Prepared state belongs to one invocation, never mutable shared writer fields.

Reuse `chunk_topic_memory_detail`, `prepare_topic_memory_projection`, and the Embedding protocol. Network inference runs outside the database write transaction under existing time and concurrency budgets. Validate projection/content equality and recheck retrieval shape at commit in case deployment configuration changed during preparation.

### 5.2 Create transaction

Atomically create the generic system Content Source with `lineage_only`, `artifact_create`, and the Topic Memory target; use it as direct lineage in TopicMemoryRepository.publish_create; commit revision 1, head, revision publication, active topic/chunks, and all enabled indexes. Roll back all state on failure. A successful response must already be searchable. System provenance retains existing admission restrictions and cannot recursively become fresh generation evidence.

### 5.3 Replace and Worker concurrency

Check If-Match before preparation and again in the write transaction. Use head CAS: update only if the current revision still matches. The system Source uses `artifact_replace` and the exact next revision.

The new revision directly references this system Source and links the previous exact Artifact revision through lineage, reusing publish_revision's deduplication. Historical evidence remains reachable through that chain. source_count counts direct Sources of this revision, not accumulated evidence.

Manual replacement and Worker updates use the same head CAS. If the Worker wins, return 412 without writing the prepared manual revision against a newer head. If manual replacement wins, reject the stale Worker commit and let its existing reread/recompute behavior recover. Verify that conflict handling does not consume unprocessed evidence.

Manual topics remain eligible for automatic evolution. Creation does not merge by title or similarity, advance Source Cursors, or consume Worker pending windows. It implies neither a permanent lock nor manual priority.

## 6. Tags and filtered listing

Tags belong to logical `(scope_id, family, artifact_id)`, survive manual and automatic revisions, and do not change content revision, published_at, or content digest.

Retain existing normalization and bounds: up to 32 stored tags, up to 16 query tags, NFC/casefold keys, and rejection of normalized duplicates. Tag ETags are independent of content ETags. Require the tag ETag for replacement; stale preconditions return 412 and missing preconditions return 428.

Extend browse_current with optional TagFilter and reuse tag_predicate before SQL LIMIT. The page and continuation probe must apply identical predicates. Do not paginate first and discard unmatched rows in Python.

Keep `published_at DESC, artifact_id ASC, revision DESC` and published-current-head selection. Filtered cursors bind Scope, family, endpoint, order, normalized keys, and all/any mode. Reject reuse across bindings. Preserve the existing unfiltered binding so valid unfiltered cursors remain usable.

Pagination is a boundary over a live collection, not a cross-request snapshot. Topics moved ahead by revisions or changing tags require refreshing the collection. Query tags retains generic tag ordering and returns references aligned with actual heads, subject to Scope authorization.

## 7. Scope authorization

| Operation | Required Topic Memory authority |
| --- | --- |
| Existing reads, tag reads/queries, filtered lists | `scope.read` |
| Create | `scope.contribute` |
| Replace and replace tags | `scope.admin` |
| Cross-Scope publish | `scope.admin` in both source and target |

Topics are shared Scope knowledge, including automatically generated topics without a single owner. Do not introduce per-topic Artifact ownership or sharing. Skip the generic owner-establishment hooks for Topic Memory creation and publication; otherwise a successful data commit could be followed by an unsupported resource error.

Source administrator permission authorizes exporting the whole topic; read access alone does not. Target administrator permission authorizes introducing external knowledge. Topic-specific resolvers return Scope resources while retaining other families' rules.

Embedded calls retain the trusted-host model; protected HTTP/MCP entrypoints enforce these requirements. Cover enabled/shadow/disabled modes and enabled-mode denial cases. Provenance never grants access to source Scope content.

## 8. Cross-Scope publication

### 8.1 Exact content and target state

Reuse PublishArtifactRequest with a complete source address, including revision. A published historical revision is eligible. Generate a new target ID and revision 1 with identical content; never merge matching titles or overwrite a target topic.

Validate the source's Topic Memory publication record. Prepare projections using the target deployment's retrieval shape rather than copying source index rows. Use target transaction time for published_at and list ordering.

Preserve existing publication_source/publication_digest and ArtifactPublication provenance, including repeated-publication chain semantics. Do not place source-local SourceRef/ArtifactRef values into target-local lineage. A pure published copy has no direct Sources and source_count is zero. Do not copy tags, permissions, or Worker cursor/pending state.

### 8.2 Transaction and idempotency

Add a family-owned repository publication method that performs base copying, Topic Memory activation, and the generic publication record in one transaction. It may internally reuse copy_exact and activation, but callers must not commit the base copy first or invoke private repository methods directly.

Retain target_scope_id/idempotency_key uniqueness. The same full request returns the original target; a different request with the same key returns 409. Concurrent duplicates leave at most one complete target and no orphan Artifact. Look up successful requests before expensive preparation, then recheck in the transaction. Replaying success must not depend on Embedding availability.

Missing revisions return 404. A missing required Topic Memory publication record is a storage invariant failure and must not write a target. Index or publication-record failure rolls back all target state. Validate publication through dedicated get/search, standard list/get, and restart integrity checks.

## 9. Errors and budgets

| Condition | Behavior |
| --- | --- |
| Invalid content/family/tag combination | Existing 422 validation envelope |
| Missing replacement precondition | 428 |
| Stale content/tag ETag | 412; reread before deciding to retry |
| Missing target or source revision | 404 under existing authorization ordering |
| Tampered or mismatched cursor | Existing InvalidCursorError mapping |
| Expired cursor | 410 |
| Publication idempotency conflict | 409 |
| Embedding timeout/unavailability | Existing inference mapping, no durable writes |
| Retrieval shape or storage invariant failure | Existing capability/integrity mapping; never silently commit partial state |

Retain TopicMemoryContent, chunk, and vector bounds. Synchronous 201/200 includes Embedding latency in vector deployments. This RFC introduces no 202 job protocol: exceeding a budget fails explicitly.

## 10. Implementation areas

| Area | Work |
| --- | --- |
| OpenAPI, generated HTTP and SDK | Request variants, enums, query limits, exports |
| family_management.py and records.py | Writer, external preparation, system Source, CAS |
| topic_memory.py | Filtered browse, family-owned publication |
| artifact_readers.py | Filtered cursor bindings and matching continuation probes |
| tags.py and tag persistence | Family support, defaults, tag semantics |
| publication.py | Complete publication and idempotent preparation |
| runtime/relational.py | Explicit dependency injection |
| server/app.py and authorization | Scope authority and owner-hook handling |

Implement contract/authorization, atomic writes, tags/filtering, then cross-Scope publication and acceptance tests. Keep family state maintenance in repositories rather than HTTP handlers.

## 11. Compatibility and rollout

Reuse existing tables, widening the tag table's family CHECK constraint to include `topic-memory`. SQLite rebuilds the tag table transactionally, preserving data, keys, foreign keys, and indexes; OceanBase updates its CHECK constraint. Pause old-instance writes during upgrade. Repeated startup must not repeat migration or lose tags. Assess new-family data compatibility before rolling back the application. Check publication/head/active projection/index integrity before enabling writes. Diagnose historical incomplete generic copies separately; startup must not guess provenance and repair them automatically.

Preserve existing reads and other families. Unspecified tag-query families intentionally include Topic Memory; document this expanded result set. Clients needing a fixed set must specify families.

Release complete support together. If delivery is split, explicitly reject generic Topic Memory publication until atomic support is ready. That temporary rejection does not satisfy this RFC's publication requirement.

## 12. Acceptance criteria

1. Create is visible through generic reads, dedicated get/search, and after restart, with correct system provenance.
2. Replace switches content and all indexes together, retains readable history, and updates ETag.
3. Concurrent Replace and Worker/Replace races obey CAS with no orphan Source or partial revision/index state.
4. Tags survive Replace/Flush and remain independent of content revision, publication time, and source count.
5. all/any, multiple pages, empty results, equal timestamps, and cursor binding mismatches behave correctly without post-pagination filtering loss.
6. Mixed-family queries include topics; explicit old-family queries retain results; unauthorized Scopes remain inaccessible.
7. Publishing an exact historical revision creates a searchable target revision 1 with empty tags, zero direct sources, and correct provenance.
8. Duplicate/concurrent publications return one target; conflicting keys return 409; successful replay works without Embedding.
9. Preparation, index, and publication-record failures leave no partial writes and preserve existing searchable content.
10. Test readers, contributors, administrators, cross-Scope denial, revocation, and owner/share branch handling.
11. Cover SQLite FTS, OceanBase FTS, supported vector shapes, and restart integrity.
12. Run `make api-generate`, `make contract-test`, focused HTTP/access/persistence/E2E tests, and `make check`. Explicitly report unavailable backend checks.

## 13. Tradeoffs

Small writer/repository extensions keep content validation, concurrency, and index invariants independently testable. Manual text is not regenerated. Scope administration governs changes to existing shared knowledge, avoiding dependence on an owner for automatic topics.

Synchronous complete publication preserves the meaning of successful generic writes at the cost of Embedding latency. Tags remain independent discovery metadata, outside generation and authority decisions.

## 14. References

- [Topic Memory models and chunking](https://github.com/oceanbase/powercontext/tree/9ff03eedffe6e73a54bb3adf40d42badd7cacdf7/src/powercontext/builtin/artifacts/topic_memory)
- [Atomic Topic Memory publication](https://github.com/oceanbase/powercontext/blob/9ff03eedffe6e73a54bb3adf40d42badd7cacdf7/src/powercontext/builtin/persistence/topic_memory.py)
- [Generic publication](https://github.com/oceanbase/powercontext/blob/9ff03eedffe6e73a54bb3adf40d42badd7cacdf7/src/powercontext/builtin/publication.py)
- [OpenAPI](https://github.com/oceanbase/powercontext/blob/9ff03eedffe6e73a54bb3adf40d42badd7cacdf7/openapi/powercontext.yaml)
- [Original Topic Memory RFC #1417](https://github.com/oceanbase/powercontext/pull/1417)
