+ Proposal Name: `topic_memory_unified_artifact_read_api`
+ Start Date: 2026-09-10
+ Status: Proposed
+ Related RFCs: [Topic Memory](1417_topic_memory.md), [Source and Artifact REST API](1437_source_artifact_rest_api.md), [Profile Artifact](1485_profile_artifact.md), [Artifact Processing Supervisor](1515_artifact_processing_supervisor.md)

# Summary

This RFC brings `topic-memory` into the unified read-only Artifact API. Topic Memory keeps its domain-specific search,
exact expansion, and background-processing operations, while Artifact catalog discovery, current-head reads, and historical
Revision reads use the standard Artifact paths and identity model.

The unified paths are:

```text
GET /v1/scopes/{scope_id}/artifacts/topic-memory
GET /v1/scopes/{scope_id}/artifacts/topic-memory/{artifact_id}
GET /v1/scopes/{scope_id}/artifacts/topic-memory/{artifact_id}/revisions
GET /v1/scopes/{scope_id}/artifacts/topic-memory/{artifact_id}/revisions/{revision}
```

This RFC does not add manual Topic Memory Create, Replace, Delete, or Retire operations. Topic Memory remains generated and
evolved by Source-driven processing; the unified Artifact API only reads formally published content.

# Motivation

Topic Memory is already a persisted Artifact Family with stable `family`, `artifact_id`, and `revision` identity. However,
the public API currently exposes only Topic Memory-specific `search` and `get` operations and does not include it in the
standard Artifact catalog. As a result:

- a page that needs to show all topics must depend on a legacy private Dashboard endpoint or call the internal browse capability;
- callers cannot discover current `topic-memory` Heads through the unified Artifact catalog;
- whether a new Artifact Family can be read through standard Artifact List/Get depends on extra route adaptation instead of the
  unified resource model;
- the `family` enum can drift from the Artifact types that are actually registered.

The unified Artifact API should provide stable resource discovery and exact-read boundaries without flattening each Family's
content semantics. Topic Memory may continue to expose progressive disclosure, specialized search modes, and retrieval
projections.

# Goals and non-goals

## Goals

- Make `list_artifacts` support `topic-memory` and list current Topic Memory Heads within one Scope.
- Make the standard Artifact current-head, revision-list, and exact-revision read paths accept `topic-memory`.
- Preserve the standard Scope isolation, pagination, cursor, lineage, and content-digest semantics.
- Make the unified API and Topic Memory-specific API share one persisted fact and identity space.
- Make Topic Memory's read-only boundary explicit so the unified API cannot be mistaken for a manual write surface.

## Non-goals

- Do not turn Topic Memory search into a query parameter on `list_artifacts`. Search ordering, scores, snippets,
  `matched_by`, and the actual retrieval mode remain the responsibility of `POST /v1/topic-memory/search`.
- Do not change Topic Memory Source Windows, generation, indexing, atomic publication, or automatic scheduling.
- Do not require every Family to return the same content fields.
- Do not add cross-Scope or cross-Family aggregation or total counts to the unified catalog.

# Guide-level explanation

## Unified catalog

A caller can use the standard Artifact List operation to discover current Topic Memory Heads in a Scope:

```http
GET /v1/scopes/SCOPE_ID/artifacts/topic-memory?limit=50
```

The response continues to use `ArtifactPage`:

```json
{
  "items": [
    {
      "scope_id": "SCOPE_ID",
      "family": "topic-memory",
      "artifact_id": "architecture",
      "revision": 4,
      "sources": [
        {"source_type": "content", "source_id": "source-17"}
      ],
      "artifacts": [],
      "content_digest": "sha256:..."
    }
  ],
  "next_cursor": null
}
```

`ArtifactCollectionItem` remains a catalog summary and does not return the full `title`, `summary`, or `detail`. A caller can:

1. use `POST /v1/topic-memory/search` for relevant topics with titles, summaries, and snippets;
2. use the standard exact-revision path for a generic Artifact Revision; or
3. use `POST /v1/topic-memory/get` for the domain response with progressive disclosure and direct Source references.

The standard catalog guarantees resource identity and readability. It does not force Topic Memory content fields into every
Family's common schema.

## Current heads and historical Revisions

The standard List operation returns current Heads only. Historical Topic Memory Revisions remain discoverable and readable through
the standard revision-list and exact-revision paths, but an old Revision must not be treated as the currently searchable topic.

The specialized `get` operation continues to use an exact `ArtifactRef` and returns:

- `title`, `summary`, and complete `detail`;
- direct Source references;
- the relation to the current Head and publication state when required by the domain response.

After obtaining an `artifact_id` and `revision` from the standard catalog, callers must carry the original Revision into the
read and must not silently replace it with the latest Head.

# Reference-level explanation

## Public contract

`BaseArtifactFamily` adds `topic-memory`. The following standard Artifact read operations add `topic-memory` to their allowed
Family values:

| operationId | URI | Topic Memory behavior |
| --- | --- | --- |
| `list_artifacts` | `GET /v1/scopes/{scope_id}/artifacts/{family}` | List current Topic Memory Heads |
| `get_artifact` | `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` | Read the current Head as a generic Artifact Revision |
| `list_artifact_revisions` | `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions` | List immutable Revisions for the topic |
| `get_artifact_revision` | `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}` | Read one exact Revision |

`create_artifact` and `replace_artifact` do not gain Topic Memory write capability from this RFC. Callers should continue to
use Source capture, `flush_topic_memory`, and background processing to wait for automatic publication.

## List consistency

The standard Topic Memory List operation must preserve these invariants:

- return only current Revisions in `ARTIFACT_HEADS` whose Family is `topic-memory`;
- require the Revision to exist in the active Topic table and have a complete retrieval projection;
- never return a Revision that has already been replaced by a newer Head;
- keep `sources`, `artifacts`, and `content_digest` consistent with the same Revision fact;
- bind the cursor to Scope, Family, pagination parameters, and ordering, so it cannot be reused across Families or Scopes.

The default canonical order for the unified interface remains ascending `artifact_id`. If a Topic Memory page needs descending
publication time, it should use a domain-specific browse projection with its own opaque cursor; the two cursor types must not be
mixed.

## Implementation boundary

The unified HTTP handler owns path validation, Scope authorization, cursor parsing, and standard response mapping. The
Family-specific service owns validation of the Topic Memory active Head, publication completeness, and content decoding.

The implementation may reuse `ArtifactRepository` and `RelationalRecordService.query_artifacts`, but it must not bypass Topic
Memory's active-topic, publication, or retrieval-shape checks. If a standard List query finds a Head that violates a Topic Memory
integrity invariant, the server should return a consistent service error and record diagnostic information rather than returning a
partial topic.

The specialized `POST /v1/topic-memory/get` continues to use `TopicMemoryRepository` because its domain response differs from
the standard `ArtifactRevision`. Both read paths must address the same Revision and must not maintain separate Topic content.

## Authorization

Standard Topic Memory reads use the current Scope's `scope.read` permission. Topic Memory is an automatically generated,
Scope-owned projection and does not create an Artifact-owner relation tied to the caller's identity. Existing Scope isolation,
access auditing, and content-readiness checks continue to apply.

Without Scope read permission, all four standard read operations and both Topic Memory-specific read operations must be denied;
the specialized endpoints must not bypass the standard Scope boundary.

# Alternatives

## Keep a private Dashboard list

Rejected. A private Dashboard endpoint cannot provide a stable API contract to external callers and makes product pages and
integrations maintain separate discovery paths.

## Use Topic Memory search as the list operation

Rejected. Search requires a non-empty query and carries retrieval-specific score, FTS/vector/hybrid, and snippet semantics. It
cannot reliably express “list every current topic.”

## List and then call Topic Memory get for every row

Allowed for a small client, but not chosen as the standard catalog implementation. It creates N+1 requests and can observe
different Revisions for the catalog and detail during concurrent publication. The unified catalog should return stable Artifact
identity first, and detail reads should use the exact reference.

# Compatibility and rollout

Adding one valid Family value is backward compatible for clients that tolerate additive enum values. Generated clients must
regenerate `BaseArtifactFamily` and related operation schemas; older clients can continue using existing Families.

Implementation steps:

1. Add `topic-memory` to OpenAPI `BaseArtifactFamily` and the Family enums for the four standard read operations.
2. Regenerate the checked-in HTTP models, operations, and schema.
3. Make the standard read handler use the Topic Memory Family reader and integrity checks.
4. Add contract, pagination, concurrent-publication, Scope-authorization, and exact-Revision regression tests.
5. Update the HTTP API documentation and Topic Memory examples.

No data migration is required. Existing Topic Memory Revisions, Heads, publications, and retrieval projections remain the sole
source of truth.

# Acceptance criteria

- `GET /v1/scopes/{scope_id}/artifacts/topic-memory` lists current Topic Memory Heads and returns standard `ArtifactPage`.
- Invalid and expired cursor behavior matches other Families.
- After a new Revision is published, List shows only the new Head while the old Revision remains readable by exact identity.
- Standard exact-revision reads and `POST /v1/topic-memory/get` address the same Revision content.
- Without Scope read permission, all four standard read operations and both Topic Memory-specific read operations are denied.
- No manual Topic Memory Create, Replace, Delete, or Retire behavior is introduced.

