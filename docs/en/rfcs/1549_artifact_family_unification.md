---
title: Unified Artifact Family Reads and Registration
---

# Unified Artifact Family Reads and Registration

- Proposal Name: `artifact_family_unification`
- Start Date: 2026-09-10
- RFC PR: [#1549](https://github.com/oceanbase/powercontext/pull/1549)
- Status: Proposed
- Related RFCs: [1437](1437_source_artifact_rest_api.md), [1417](1417_topic_memory.md), [1485](1485_profile_artifact.md), [1515](1515_artifact_processing_supervisor.md)

## Summary

PowerContext already uses common Source and Artifact abstractions, but the public HTTP contract still declares Artifact Families in several places. Topic Memory is already persisted as an artifact, yet it cannot be listed through the standard `list_artifacts` endpoint. Adding another family also requires updating the OpenAPI contract, generated models, runtime composition, and other family lists separately.

This RFC proposes:

1. Include Topic Memory in the standard Artifact read APIs, at minimum `list_artifacts`, and use the same family contract for standard get, revision list, and revision get operations.
2. Introduce a single family registry that declares each family’s wire name, artifact type, Source capabilities, read/write capabilities, and optional adapters. Generic interfaces depend on the registry instead of adding a branch for every new family.

Topic Memory generation, flush, search, and detail semantics remain on their existing specialized endpoints. Whether standard Artifact writes are exposed for a family is still controlled by the registry’s capability declaration.

## Motivation

The memory showcase page needs stable list and search capabilities. Topic Memory already has an internal browse capability, but the public list route uses a static enum and rejects `topic-memory`. Calling the specialized detail endpoint for every generic list item would create an inconsistent protocol, omit collection fields, and cause N+1 requests.

Source and Artifact Families will continue to grow. Copying family names into OpenAPI parameters, generated code, repository composition, authorization configuration, and tests makes it easy to miss an integration point and makes generic API evolution grow with the number of families.

## Design

### 1. Include Topic Memory in standard Artifact reads

Add `topic-memory` to `BaseArtifactFamily` and make the `family` parameter of the following operations reference that shared schema:

- `GET /v1/scopes/{scope_id}/artifacts/{family}` (`list_artifacts`)
- `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` (`get_artifact`)
- `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions` (`list_artifact_revisions`)
- `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}` (`get_artifact_revision`)

`list_artifacts` continues to return the shared `ArtifactPage`. A Topic Memory collection item must expose the common metadata needed by the showcase page: artifact reference, title, summary, publication time, source count, and relevant revision metadata. Full Topic Memory content remains available through the existing `topic-memory/get` endpoint; if the standard get model cannot represent the complete content, the family-specific detail contract should extend it rather than adding a family branch to the generic route.

Topic Memory listing is ordered by published time descending and keeps cursor pagination semantics. Only currently publishable Topic Memory items are returned; temporary drafts, processing state, and internal work records are excluded from the standard list.

The security boundary remains unchanged: Topic Memory reads are scoped and follow the current scope-ownership rules. Adding the family to the common read enum does not open manual Topic Memory create, replace, or delete operations.

### 2. A single family registry

Introduce `ArtifactFamilyDefinition` and `ArtifactFamilyRegistry`. Each family registers one definition containing at least:

- a stable wire name such as `memory` or `topic-memory`;
- its Python artifact type and persistence identity;
- whether it is a Source, supports standard reads, and supports standard writes;
- whether it supports tags, revisions, search, and other common capabilities;
- optional family-specific collection/detail mappers, query services, and write adapters.

Sources continue to use the existing SourceDefinitionRegistry. The Artifact Family registry describes the relationship between Artifacts and Sources without conflating Source types with the public Artifact Family enum. Runtime repository composition, management writers, access capabilities, and the OpenAPI family schema should be generated from or validated against these registries.

Generic HTTP handlers only parse a registry-supported wire name, apply common authorization/pagination/error mapping, invoke the family capability, and return the shared response. They must not grow branches such as `if family == "topic-memory"`. Family differences are expressed through registered capabilities or adapters.

### 3. OpenAPI and generated code

OpenAPI remains the source of truth for the public HTTP contract. All generic Artifact routes share the `BaseArtifactFamily` schema; generated code is updated with `make api-generate`, and `src/powercontext/http/_generated/` is never edited by hand.

The runtime registry must be checked for consistency with the OpenAPI family schema:

- every publicly readable family in the registry must appear in `BaseArtifactFamily`;
- every family declared by OpenAPI must have a corresponding registry definition;
- only families declaring a capability may appear in tag, write, or other specialized parameters.

The minimum flow for adding a family is therefore one registry entry, implementations for its required declared capabilities, and contract regeneration. Generic list/get routes, pagination, and base authorization do not need family-specific edits.

## Alternatives

### Keep a specialized Topic Memory list endpoint

This is small to implement, but the showcase must maintain two list protocols and generic Artifact pages cannot be reused. More specialized routes would follow as families grow. Rejected.

### Reuse the current `list_artifacts` and call `topic-memory/get` per item

The current contract does not accept Topic Memory, causes N+1 requests, and cannot directly return Topic Memory title, summary, and publication time. Rejected.

### Extend only one global enum

This temporarily fixes route validation but leaves runtime, authorization, and mapping registrations scattered, so a new family can still miss an integration point. Rejected.

### Keep an independent handler for every family

This expresses differences, but causes common pagination, authorization, and response behavior to diverge. Specialized business endpoints remain allowed when semantics cannot be expressed as a capability; standard reads remain registry-driven.

## Compatibility and migration

- `topic-memory` is a new standard read family; existing memory, experience, skill, handoff, profile, and prompt calls remain compatible.
- Existing Topic Memory search/get/flush endpoints remain available; clients do not need to migrate immediately.
- Topic Memory persistence tables, artifact references, and generation flow are unchanged.
- New collection fields must follow existing optional-field and backward-compatibility rules.
- If the registry and OpenAPI disagree, startup or contract tests should fail fast rather than silently omit a family at runtime.

## Testing plan

- OpenAPI contract tests confirm that the four standard read routes share the family schema and accept `topic-memory`.
- Registry contract tests confirm that all public families are registered and that runtime definitions match OpenAPI.
- Runtime tests read Topic Memory through standard list/get/revision paths and verify scope, ordering, cursor, and empty-result behavior.
- Regression tests confirm that existing family list, tag filtering, revision, and authorization behavior is unchanged.
- Extension tests register a minimal test family and verify that generic reads and base composition require no new family branch.

## Open questions

1. Should a Topic Memory collection item carry full content, or only shared summary metadata with detail loaded on demand? This RFC defaults to summary in list and on-demand detail.
2. Should the registry directly generate the OpenAPI enum in this implementation, or initially validate consistency between the two? The first implementation should use the lower-risk consistency check while leaving a path to automate contract generation later.

## Conclusion

The unified read contract solves the current Topic Memory showcase requirement, while the family registry concentrates future extension points into one registration location. Together, a new family only needs to declare its capabilities and required adapters; generic interfaces no longer need to change linearly with every family addition.
