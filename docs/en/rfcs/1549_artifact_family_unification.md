---
title: Topic Memory in Standard Artifact Reads
---

# Topic Memory in Standard Artifact Reads

- Proposal Name: `topic_memory_artifact_reads`
- Start Date: 2026-09-10
- RFC PR: [#1549](https://github.com/oceanbase/powercontext/pull/1549)
- Status: Proposed
- Related RFC: [1417](1417_topic_memory.md)

## Summary

Topic Memory is already persisted as an Artifact, but the public standard Artifact read contract does not accept `topic-memory`. This RFC adds Topic Memory to the standard Artifact read interfaces needed by the memory showcase page:

1. list current Artifact heads;
2. get the current Artifact head;
3. list Artifact revisions; and
4. get one exact Artifact revision.

The existing Artifact repository composition and family-specific management wiring remain unchanged. Only the read contract and the Topic Memory list behavior are extended. Topic Memory generation, flush, search, and its existing detail semantics remain on their specialized endpoints.

## Motivation

The memory showcase page needs a stable list API. Topic Memory already has a persistence-level browse operation, but the standard `list_artifacts` route rejects `topic-memory`. Using a separate dashboard list route would force clients to maintain two list protocols.

Topic Memory also has family-specific list semantics: only published topics should be shown, ordered by publication time, with the display metadata available to the page. Those differences are limited to the Topic Memory list implementation; they do not require a new general-purpose family registry or a new generic capability framework.

## Design

### 1. Add Topic Memory to standard Artifact reads

The following read operations accept `topic-memory` as a family:

- `GET /v1/scopes/{scope_id}/artifacts/{family}` (`list_artifacts`)
- `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` (`get_artifact`)
- `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions` (`list_artifact_revisions`)
- `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}` (`get_artifact_revision`)

Read-family validation is kept separate from the existing writable Artifact family validation. Adding `topic-memory` to the read contract must not make it valid for create, replace, or tag operations.

The standard Artifact response remains the source of truth for detail and revision reads. No new Topic Memory detail response is introduced. Existing `ArtifactRecord.content` is sufficient for the standard response; the existing Topic Memory detail endpoint remains available for Topic Memory-specific projections.

For `list_artifacts`, the Topic Memory implementation:

- returns currently published topics only;
- orders results by publication time descending;
- preserves the standard signed cursor behavior;
- returns the common collection item and its available display metadata, including title, summary, publication time, and source count; and
- keeps the existing scope-read authorization boundary.

The Topic Memory list behavior is wired explicitly alongside the existing Artifact family composition. It does not introduce a family registry, generated registration mechanism, or a new generic adapter framework.

### 2. Keep existing writes and family wiring unchanged

The current Artifact repository family list, management writers, tag behavior, and write authorization remain as they are on `master`. Topic Memory is read-only through the standard Artifact routes in this change. The existing Topic Memory-specific APIs are not replaced or broadened.

### 3. OpenAPI and generated code

OpenAPI remains the source of truth for the public HTTP contract. The four standard read routes use a read-family schema that includes `topic-memory`; writable and tag-related routes continue to use their existing family schemas. Generated HTTP models are updated with `make api-generate`, and checked-in generated files are not edited manually.

## Alternatives

### Keep a specialized Topic Memory list endpoint

The showcase page would need a second list protocol and could not reuse the standard Artifact page model. Rejected for this use case.

### Reuse `list_artifacts` and call Topic Memory detail for every item

The current contract rejects Topic Memory, and fetching detail once per item creates N+1 requests. Rejected.

### Introduce a general family registry

This would broaden the change beyond the current Topic Memory read requirement and would require redesigning existing Artifact composition and capability handling. Deferred until a concrete second family requires it.

## Compatibility

- Existing Artifact read, write, tag, and revision calls remain compatible.
- `topic-memory` is added only to the standard read-family contract.
- Existing Topic Memory generation, search, get, and flush APIs remain available.
- No persistence schema or Artifact family composition changes are required.

## Testing plan

- Contract tests verify that the four standard read routes accept `topic-memory` while writable and tag routes retain their existing family sets.
- Runtime tests verify published-only filtering, publication ordering, signed cursor pagination, collection metadata, scope authorization, and empty results.
- Regression tests verify existing Artifact family list, revision, tag, and write behavior.

## Conclusion

This RFC makes the smallest change needed for the memory showcase page: expose Topic Memory through the standard Artifact read interfaces while preserving the existing Artifact architecture and family wiring.
