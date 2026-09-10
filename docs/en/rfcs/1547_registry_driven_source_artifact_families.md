+ Proposal Name: `registry_driven_source_artifact_families`
+ Start Date: 2026-09-10
+ Status: Proposed
+ RFC PR: [oceanbase/powercontext#1547](https://github.com/oceanbase/powercontext/pull/1547)
+ Related RFCs: [Source and Artifact REST API](1437_source_artifact_rest_api.md), [Topic Memory](1417_topic_memory.md), [Profile Artifact](1485_profile_artifact.md), [Artifact Processing Supervisor](1515_artifact_processing_supervisor.md)

# Summary

This RFC introduces centralized registration for Source types and Artifact Families. Adding a built-in Source type or Artifact
Family requires one declaration of its identity, model, capabilities, and any required Family-owned adapters. Standard Source,
Artifact, authorization, capability-discovery, and client contracts are derived from registration rather than adding new
`if family == ...` branches to shared interfaces.

Registration describes what a type is, which common capabilities it supports, and which component owns its special semantics. It
does not merge different Family content models or force domain-specific generation, search, or processing into the base API.

The target shape is:

```python
register_artifact_family(
    ArtifactFamilySpec(
        name="topic-memory",
        artifact_type=TopicMemory,
        catalog=topic_memory_catalog,
        reader=topic_memory_reader,
        writer=None,
        search=topic_memory_search,
        processing=topic_memory_processing,
    )
)
```

An ordinary Family that has no special `catalog` or `reader` automatically reuses the shared Artifact Revision, Head, lineage,
digest, and pagination implementations.

# Motivation

Source and Artifact already share Scope, identity, lineage, Revision, and authorization foundations, but the way a new Family
enters the public interface remains scattered across multiple places:

- OpenAPI Family or Source type enums;
- generated HTTP models, operations, and schema;
- Artifact Repository type registration;
- Family management writers and content validation;
- Access Profiles, capabilities, and readiness;
- processing bindings, specialized search/get operations, and Dashboard adapters.

This split creates two failure modes: a registered type is missing from the public interface, or a public interface accepts a type
that lacks complete runtime capability. `Topic Memory` exposed the first problem: it was registered in the Artifact Repository and
runtime but was not included in the standard Artifact List.

The base interfaces should stably handle common resource behavior. Family-specific differences should be declared by registration
and implemented by optional adapters. Adding an ordinary Family should therefore not require another change to shared routing,
pagination, cursors, Source lineage, Artifact digests, or authorization scaffolding.

# Goals and non-goals

## Goals

- Establish one runtime registry for public Source types and Artifact Families.
- Make standard Source/Artifact read, list, revision, lineage, and capability discovery work from the registry.
- Provide optional Family adapters for catalog, reader, writer, search, and processing behavior.
- Make ordinary Families inherit shared implementations and register only exceptional semantics.
- Reject duplicate names, inconsistent models, incomplete capability declarations, and unsafe registrations at startup.
- Keep OpenAPI, generated clients, the runtime registry, and capabilities consistent.

## Non-goals

- Do not allow callers to dynamically register arbitrary Source types or Artifact Families through requests.
- Do not turn Family-specific content schemas into a permissive public JSON black box.
- Do not require every Family to support Create, Replace, Search, Generation, or background Processing.
- Do not remove domain validation or lifecycle differences among Topic Memory, Memory, Profile, Experience, Skill, and Handoff.
- Do not invent data migrations, indexes, model prompts, or review workflows for a new Family.

# Guide-level explanation

## A Family is a capability set

Each public Artifact Family registers an immutable `ArtifactFamilySpec`. For example:

```python
ArtifactFamilySpec(
    name="experience",
    artifact_type=Experience,
    catalog=shared_artifact_catalog,
    reader=shared_artifact_reader,
    writer=experience_writer,
    search=experience_search,
    processing=experience_processing,
    access=experience_access_profile,
)
```

Field semantics:

| Field | Purpose |
| --- | --- |
| `name` | Stable public Family name subject to identity constraints |
| `artifact_type` | Runtime model for the Artifact and its `content` |
| `catalog` | Optional current-head catalog projection; defaults to the shared Artifact catalog |
| `reader` | Optional current-head/exact-revision reader; defaults to the shared Artifact Repository |
| `writer` | Optional Create/Replace writer; absent means the Family is read-only |
| `search` | Optional Family-specific search capability |
| `processing` | Optional Source-driven or other background processing capability |
| `access` | Access, sharing, and selector constraints for the Family |
| `tags` | Whether the common Artifact/entry tag target is supported |

`Topic Memory` may register a specialized `catalog` and `reader` to enforce active-topic, publication, and retrieval-projection
completeness. An ordinary Artifact only needs its `artifact_type` and any required writer.

## Source type registration

Source types use the same pattern:

```python
SourceTypeSpec(
    name="content",
    adapter=content_source_adapter,
    public=True,
    readable=True,
    capture=True,
    generation_eligible=True,
)
```

The Source adapter owns canonicalization, wire content, materialization, and generation eligibility. Source journal, Scope
identity, pagination, and authorization remain responsibilities of the shared Source service.

An internal Source type may be registered with `public=False`. It can be used by internal processing but is not automatically
exposed through public OpenAPI or accepted as arbitrary caller input.

## Deriving standard interface capabilities

Standard interfaces select implementations from Family capabilities instead of branching on names:

| Capability | Default behavior | Specialized Family behavior |
| --- | --- | --- |
| Artifact List | Shared current-head catalog, pagination, and cursor | Use `catalog` for a Family-specific stable summary |
| Artifact Get head | Shared Artifact Repository | Use `reader` for a domain response or extra integrity checks |
| Artifact List revisions | Shared immutable Revision query | Override only for non-standard history rules |
| Artifact Get revision | Shared exact Revision query | Use `reader` for Family-specific decoding |
| Artifact Create/Replace | Not exposed when undeclared | Use `writer` for validation, commit, and derived state |
| Source Create/Get/List | Shared Source service | Use the Source adapter for normalization and reading |
| Tags | Exposed according to `tags` capability | Disable or customize the Family target |
| Search | Not synthesized | Register a Family-specific search operation |
| Processing | Not started automatically | Register a processing binding and scheduling policy |

The shared interface handles request boundaries, Scope authorization, cursors, errors, transactions, and response envelopes. A
registered adapter cannot bypass those boundaries.

## Public contract and generation

OpenAPI remains the source of truth for the complete wire contract. To prevent the same Family name from being maintained in many
paths:

1. `BaseArtifactFamily` and the public Source type each define the complete enum once;
2. Path parameters reference those schemas instead of copying inline enums;
3. `make api-generate` generates HTTP models, operations, and schema from the contract;
4. Startup checks compare public OpenAPI Families, the runtime registry, and capabilities and fail on drift.

If full automation is later needed, a Family manifest can become a build input that generates the OpenAPI enum and runtime
registry. Clients should still use stable build-time enums instead of arbitrary runtime strings.

The minimum process for adding an ordinary built-in Family is therefore:

```text
1. Register the Family, Artifact model, and capabilities
2. Add one value to the centralized Family schema and declare its content schema in OpenAPI
3. Run make api-generate
4. Add content-validation and behavior tests for the Family
```

The shared HTTP routes, pagination, Source lineage, Artifact digest, and standard read adapters do not change. Only a
Family-specific writer, search, or processing adapter requires additional registration.

# Reference-level explanation

## Registry lifecycle

The registry is built while assembling the Server Application and frozen before application startup:

```text
Load built-in Source types and Artifact Families
  -> validate name uniqueness and visibility
  -> validate Artifact models, content models, and adapter capabilities
  -> validate Access Profile compatibility with public operations
  -> build Repositories, Source service, capabilities, and processing bindings
  -> freeze the registry
```

Requests, plugins, and database content cannot change the public Family set after startup. This ensures that OpenAPI,
authorization, persistence decoding, and capability discovery in one process observe the same Family collection.

## Validation rules

A registration must satisfy:

- `name` is unique and matches `[a-z][a-z0-9-]*`;
- `artifact_type.family == spec.name`;
- `artifact_type.content` is a verifiable Pydantic model;
- when `writer` is declared, Create/Replace validation and transaction boundaries are explicit;
- when `catalog` is declared, every returned identity belongs to the requested Scope and Family;
- when `reader` is declared, exact reads preserve the requested ArtifactRef revision;
- when `processing` is declared, its binding, configuration, authorization, and failure-recovery policy are complete;
- a `public=False` type is excluded from public enums, capability lists, and standard external operations;
- every adapter uses shared Scope authorization and error normalization.

Duplicate names or invalid registrations must prevent successful startup; the Server must not continue with partial registration.

## Family-specific adapter boundary

Adapters own only behavior that cannot be derived from shared Artifact/Source facts:

- content-specific validation and canonicalization;
- Family-owned derived tables or retrieval projections;
- Family-specific current-head completeness;
- domain search, generation, processing, and publication;
- Family-specific response projections.

Adapters do not reimplement Scope, signed cursors, common Artifact identity, lineage storage, access checks, or request IDs. The
shared service passes validated Scope and exact identity to the adapter, which returns a typed result.

## Capabilities and errors

Capabilities are generated from the frozen registry rather than from a separate list of Family strings. Clients can read
capabilities to determine whether a Family or operation is enabled in the deployment.

When a Family is declared in the public contract but its runtime capability is missing, the Server returns the existing
unavailable/configuration error and explains the reason through readiness. It must not misrepresent missing capability as an empty
list or a 404. Unknown or non-public Families continue to return invalid request.

## Authorization and security

Each registration must define Scope read/write, Artifact ownership, sharing unit, selector, and transitivity behavior in its
`access`. A new Family must not gain write, sharing, or cross-Scope capability merely by reusing the shared Artifact handler.

An automatically generated Family such as Topic Memory may declare Scope-owned reads without creating a caller-specific owner
relation. A manually writable Family must declare write authority through both its writer and Access Profile.

## Persistence and processing

The shared Artifact Repository continues to provide common Revision, Head, lineage, and digest tables. Family-specific derived
tables, indexes, and processing state are declared by the Family adapter and assembled during composition.

Adding a Family does not backfill historical Sources or create historical Revisions automatically. If a projection or migration is
needed, that Family owns its explicit migration and readiness checks.

# Migration plan

Migrate existing code in compatible phases:

1. Wrap the current Artifact Repository type collection and Source registry with the new registry.
2. Map Access Profiles, capabilities, management writers, and processing bindings to registration entries.
3. Make standard Source/Artifact handlers query registry capabilities and remove Family-name branches from shared routing.
4. Replace duplicated OpenAPI Path-parameter enums with references to centralized Family schemas.
5. Add registry-consistency tests for Topic Memory, Profile, and existing writable Families.
6. Remove duplicated static Family lists after all existing Families have migrated.

During migration, existing operations and response schemas remain compatible. The registry changes the source of capabilities, not
existing Artifact identity, Source journal, Revision, lineage, or authorization semantics.

# Alternatives

## Continue adding Family branches to shared interfaces

Rejected. Every new Family would require repeated changes to routes, clients, permissions, tests, and documentation, with a high
risk of missing one surface.

## Make family/source_type arbitrary strings

Rejected. Fully open strings weaken OpenAPI client typing, authorization, and content-decoding boundaries and make deployment
capabilities impossible to discover statically.

## Provide a complete CRUD route for every Family

Rejected. Family-specific operations may remain, but Scope, identity, cursors, lineage, and standard read/list behavior would be
duplicated across many implementations.

## Generate the runtime registry only from OpenAPI

Not sufficient by itself. OpenAPI describes wire schemas but cannot fully express processing, derived projections, authorization,
or adapter lifecycles. OpenAPI and the runtime registry should be kept consistent through startup checks and build-time generation,
with each source owning the contract it can express.

# Compatibility and rollout

Adding a public enum value is an additive, backward-compatible contract change; clients that do not recognize the new value can
continue using existing Families. Generated clients must regenerate their types and operation schemas.

After the registry is frozen, a Family's name and capabilities remain constant for the lifetime of a Server process. Enabling a
Family capability is a deployment configuration change and does not change persisted identity.

The security defaults are:

- unregistered, non-public, or disabled Families are not exposed;
- a Family without a writer does not support Create/Replace;
- a Family without search does not receive a synthesized generic search;
- a Family without processing does not enter background scheduling;
- adapter errors cannot degrade into successful empty responses.

# Acceptance criteria

- Every public Source type and Artifact Family can be enumerated from the frozen registry.
- Capabilities, Access Profiles, Repository Family sets, and public OpenAPI Families agree.
- A test Family that supports only standard read/list works without changing the shared HTTP handler.
- A test Family with a custom catalog or writer requires only its registration entry and Family adapter, not a shared route branch.
- Unknown Families, duplicate registrations, incomplete capabilities, and model/name mismatches fail during startup or contract tests.
- Standard Source/Artifact Scope authorization, cursors, lineage, digests, and error semantics remain unchanged.
- Topic Memory can enter the unified Artifact read/list surface through a registered catalog/reader while retaining specialized
  search, processing, and read-only write boundaries.
- No historical data migration occurs, and registering a new Family does not rewrite existing Sources or Artifacts.
