---
title: "RFC 1437: Source and Artifact REST APIs"
description: A proposal for scoped Source creation and Artifact management through common HTTP operations, family-owned writes, and exact lineage.
---

+ Proposal Name: `source_artifact_rest_api`
+ Start Date: 2026-09-01
+ RFC PR: [oceanbase/powercontext#1437](https://github.com/oceanbase/powercontext/pull/1437)

# Summary

This RFC adds two foundational HTTP API surfaces to PowerContext:

- Source: Create and Get;
- Artifact: Create, Get head, Get Revision, List, and Replace.

The new operations live below `/v1/scopes/{scope_id}/sources` and `/v1/scopes/{scope_id}/artifacts`. This RFC does
not introduce a generic Resource concept and does not define the Scope API.

Source Create exposes only the `content` type. Artifacts expose only the `memory`, `experience`, `skill`, and
`handoff` families. Artifact Create/Replace dispatches through Family-owned management writers that reuse existing
domain validation, authoritative writes, and derived projections instead of duplicating Family logic in the base
API. Each write persists a system Source in the same transaction as the new Revision and places it first in that
Revision's direct Source lineage. Handoff then derives citation lineage through its existing service. The system
Source must never participate in generation of another Artifact.

Source Create does not trigger Memory, Experience, Skill, or Handoff generation. A caller that needs a subsequent
domain operation first creates the Source and then invokes the corresponding existing domain command.

# Motivation

The existing PowerContext APIs primarily express domain actions such as Source capture, Memory flush,
Experience/Skill evolution, and the Handoff workflow. They do not provide stable foundational access to Sources and
Artifacts. Callers need to create and retrieve Sources by complete identity, and create, retrieve, list, and replace
formal Artifacts without introducing a second data or identity space.

The foundational API must reuse the existing Source journal, Artifact Revision/head, lineage, and authorization
capabilities. Every Artifact Revision written through the foundational API must also leave a traceable direct input
while preventing that provenance-only Source from being consumed again by a model or another generation flow.

# Guide-level explanation

## Two foundational resources

A Source is durable evidence without revisions. Its public identity is:

```json
{
  "source_key": ["scope_id", "source_type", "source_id"]
}
```

Source Create operates on the Source parent collection below a Scope. The caller submits `content` and may omit
`source_type`; the only public type and the default in this release are both `content`. `scope_id` comes from the
path, and the server generates `source_id`.

An Artifact is a formal, committed, evolvable product. Its head and exact Revision identities are:

```json
{
  "artifact_head_key": ["scope_id", "family", "artifact_id"],
  "artifact_revision_key": ["scope_id", "family", "artifact_id", "revision"]
}
```

Artifact Create commits Revision 1. Replace creates the next immutable Revision and moves the head. A caller can
read the current head, read an exact historical Revision, or list current heads within one family.

## Public types

Public Source type:

| `source_type` | Create/Get | Content requirement |
| --- | --- | --- |
| `content` | Supported | `content` is any valid JSON value normalized and persisted by the existing Content Source adapter. |

Internal types such as `external-skill-snapshot` are not part of this OpenAPI enum. The server rejects unknown
public values instead of passing arbitrary strings to internal adapters.

Public Artifact families:

| `family` | Create | Get | List | Replace | Validation requirement |
| --- | --- | --- | --- | --- | --- |
| `memory` | Supported | Supported | Supported | Supported | Create/Replace use Memory commands; reads return canonical `MemoryContent`. |
| `experience` | Supported | Supported | Supported | Supported | Use existing `ExperienceContent` and its search projection. |
| `skill` | Supported | Supported | Supported | Supported | Use existing `SkillContent`, standard package validation, and search projection. |
| `handoff` | Supported | Supported | Supported | Supported | Use existing `HandoffContent`, citation validation, and the Scope singleton identity. |

The server first selects the domain model by `family`, then deserializes, validates, and serializes `content` using
that family's canonical rules. An unknown family or content that violates its data standard returns
`422 Unprocessable Entity`. This RFC does not introduce other direct families.

## Artifact lineage

Artifact responses flatten their own identity at the top level and return multi-valued lineage in two arrays:

```json
{
  "scope_id": "scp_01J...",
  "family": "memory",
  "artifact_id": "mem_01J...",
  "revision": 3,
  "sources": [
    {"source_type": "content", "source_id": "src_01J..."}
  ],
  "artifacts": [
    {"family": "experience", "artifact_id": "exp_01J...", "revision": 2}
  ]
}
```

The top-level `scope_id` also applies to the Sources and Artifacts in those arrays; this release expresses only
same-Scope lineage. Arrays follow persisted `ordinal` order and return `[]`, never `null`, when empty.

`sources` and `artifacts` are read-only results. Create and Replace requests accept neither those fields nor
`source_refs` or `artifact_refs`; callers cannot write lineage directly through the foundational HTTP API.

## Provenance for Artifact Create and Replace

Artifact Create and Replace do not accept Source references. For every new Revision, the server persists the
validated and canonicalized write command as a new system Source with `source_type=content`, then places it at
ordinal zero in that Revision's direct Source lineage. For Memory, this Source contains the `entries[].kind/text`
command while Artifact GET returns the canonical `MemoryContent` produced by that command. Handoff additionally
derives Source and Artifact lineage from its direct citations through the existing Handoff service. Replace does not
delete or rewrite Sources attached to older Revisions. Non-Handoff writers preserve the previous Revision's ordered
Artifact lineage; Handoff derives the replacement Revision's citation lineage from the complete content.

The internal role of this Source is `lineage_only`: it is a real, traceable record of the creation input, but not
ordinary evidence for a model to consume again. Reserved payload data binds it uniquely to
`(scope_id, family, artifact_id, revision)`. Public Source responses do not expose those internal-purpose fields.

Artifact Create or Replace writes the system Source, journal position, new Artifact Revision, head, and Source
lineage in one database transaction. Any failure rolls back the entire operation.

## Non-goals

- No Source List or Search;
- no Artifact Search or cross-family List;
- no Artifact Delete, physical purge, or bulk mutation;
- no client-writable lineage;
- no synchronous generation parameters, composite responses, or generation task model on writes;
- no Scope API or sharing permissions;
- no changes to the business semantics of existing domain commands.

# Reference-level explanation

## Scope, URIs, and resource identity

`scope_id` is the owner, authorization boundary, and part of the public identity. Scope creation, retrieval,
listing, organization relationships, and binding are handled by
[RFC 1345](1345_scope_organization_and_agent_integration.md) and
[implementation PR #1401](https://github.com/oceanbase/powercontext/pull/1401). This RFC defines only Source and
Artifact children below an existing Scope.

The allowed Resource Paths are:

```text
/v1/scopes/{scope_id}/sources
/v1/scopes/{scope_id}/sources/{source_type}/{source_id}

/v1/scopes/{scope_id}/artifacts
/v1/scopes/{scope_id}/artifacts/{family}
/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}
/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}
```

The canonical URIs of a Source item, Artifact head, and Artifact Revision are respectively:

```text
/v1/scopes/{scope_id}/sources/{source_type}/{source_id}
/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}
/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}
```

Every named GET carries the complete public key in the path. `source_type` and `family` are encoded as one URL
segment each. Paths use lowercase plural nouns, multi-word static segments use `kebab-case`, JSON and query fields
use `snake_case`, and URIs have no trailing slash.

## New operations

This RFC defines seven operations:

| Object | operationId | HTTP method and URI | Success | Description |
| --- | --- | --- | --- | --- |
| Source | `create_source` | `POST /v1/scopes/{scope_id}/sources` | `201 Created` | Create an immutable Content Source. |
| Source | `get_source` | `GET /v1/scopes/{scope_id}/sources/{source_type}/{source_id}` | `200 OK` | Read a Source by complete identity. |
| Artifact | `create_artifact` | `POST /v1/scopes/{scope_id}/artifacts` | `201 Created` | Create an Artifact, Revision 1, and system Source. |
| Artifact | `get_artifact` | `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` | `200 OK` / `304 Not Modified` | Read the current head. |
| Artifact | `get_artifact_revision` | `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}` | `200 OK` | Read an immutable historical Revision. |
| Artifact | `list_artifacts` | `GET /v1/scopes/{scope_id}/artifacts/{family}` | `200 OK` | List current heads in one family. |
| Artifact | `replace_artifact` | `PUT /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` | `200 OK` | Replace completely and create the next Revision. |

## Wire schemas

`CreateSourceRequest`:

```json
{
  "source_type": "optional; single-value enum content; defaults to content",
  "content": "required JSON value; original content to persist"
}
```

`SourceRecord`:

```json
{
  "scope_id": "scp_01J...",
  "source_type": "content",
  "source_id": "src_01J...",
  "content": "Refunds require manual review.",
  "position": 42,
  "content_digest": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

Source requests and responses expose no `metadata`, server timestamp, or internal-purpose fields.

`CreateArtifactRequest` is a discriminated union keyed by `family`:

```text
CreateArtifactRequest
├── CreateMemoryArtifactRequest
├── CreateExperienceArtifactRequest
├── CreateSkillArtifactRequest
└── CreateHandoffArtifactRequest
```

The outer shape stays `{ "family": "<family>", "content": {} }`, but the `content` shape remains Family-owned:

| family | Create `content` | Write result |
| --- | --- | --- |
| `memory` | Command with `entries[].kind/text` | Generate Entry Versions, manifest, changes, entry heads, and search projection; GET returns canonical `MemoryContent`. |
| `experience` | `ExperienceContent` | Write Revision/head and refresh the Experience search projection. |
| `skill` | `SkillContent` | Validate or build the standard Skill package, write Revision/head, and refresh the Skill search projection. |
| `handoff` | `HandoffContent` | Validate citations and write the Scope's sole `handoff` Artifact. |

Memory Create example:

```json
{
  "family": "memory",
  "content": {
    "entries": [
      {"kind": "preference", "text": "The user prefers responses in Chinese"}
    ]
  }
}
```

`kind` is required and remains an open string between 1 and 128 characters. Recommended values are `fact`,
`preference`, `decision`, `constraint`, and `working_note`. Business-specific values are allowed; the server
validates and preserves the supplied value and never guesses or overwrites it. `text` is required and non-empty.

Experience Create example:

```json
{
  "family": "experience",
  "content": {
    "situation": "A compatibility issue was found before release",
    "action": "Add cross-version tests",
    "outcome": "Avoided a production regression",
    "lesson": "Public API changes require compatibility coverage"
  }
}
```

`ReplaceArtifactRequest`:

```json
{
  "content": "required object; a write command or complete family-specific content selected by the path family"
}
```

OpenAPI models Create with `oneOf` and a `family` discriminator so generated Python and TypeScript clients receive
precise types. Replace already selects the family in the path, so its body remains `content` only while still using
a family-specific union.

`ArtifactCreated`:

```json
{
  "scope_id": "scp_01J...",
  "family": "memory",
  "artifact_id": "mem_01J...",
  "revision": 1,
  "sources": [
    {"source_type": "content", "source_id": "src_01J..."}
  ],
  "artifacts": []
}
```

`ArtifactRevision`:

```json
{
  "scope_id": "scp_01J...",
  "family": "memory",
  "artifact_id": "mem_01J...",
  "revision": 2,
  "content": {"summary": "Refunds require manual review."},
  "sources": [
    {"source_type": "content", "source_id": "src_01J..."}
  ],
  "artifacts": [],
  "content_digest": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

`ArtifactCollectionItem` omits complete `content`:

```json
{
  "scope_id": "scp_01J...",
  "family": "memory",
  "artifact_id": "mem_01J...",
  "revision": 2,
  "sources": [
    {"source_type": "content", "source_id": "src_01J..."}
  ],
  "artifacts": [],
  "content_digest": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

`ArtifactPage`:

```json
{
  "items": [],
  "next_cursor": null
}
```

Artifact responses expose no server timestamp and use neither an `artifact_ref` envelope nor `source_refs` or
`artifact_refs` envelopes.

## Operation behavior

### Create Source

`POST /v1/scopes/{scope_id}/sources` accepts `CreateSourceRequest`. The server generates `source_id`, writes the
Source journal synchronously, and returns `201 SourceRecord` plus a `Location` containing the complete canonical
URI. Every successful call creates a new Source; the operation does not accept `Idempotency-Key`.

### Get Source

`GET /v1/scopes/{scope_id}/sources/{source_type}/{source_id}` reads by all three identity components. A mismatch in
any component returns `404 Not Found`; the implementation must not fall back to querying only by `source_id`.

### Create Artifact

`POST /v1/scopes/{scope_id}/artifacts` accepts the discriminated `family` and `content` union. The server selects the
Family writer, generates the system `source_id`, and creates the system Source, Revision 1, head, Family-derived
state, and ordinal-zero Source lineage in one transaction. The server generates `artifact_id` except for Handoff.
It returns `201 ArtifactCreated`, the Artifact head `Location`, and the new head `ETag`.

Handoff uses fixed `artifact_id=handoff` within a Scope. Create writes it when absent. If it already exists, Create
returns `409 artifact_already_exists` with `use_replace=true` in error details; callers must use Replace and Create
must not silently update the singleton.

Except for the Handoff singleton conflict, every successful call creates a new Artifact and a `lineage_only` Source
uniquely bound to its Revision 1. The operation does not accept `Idempotency-Key`. The response `sources` must
contain that Source, and `artifacts` is always `[]`.

### Get Artifact head

`GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` returns the current `ArtifactRevision` and head ETag.
When an optional `If-None-Match` matches, it returns `304 Not Modified` without a body.

### Get Artifact Revision

`GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}` returns the requested immutable
Revision and must not substitute the current head for the path revision. An exact Revision returns no ETag and
accepts no conditional header.

### List Artifacts

`GET /v1/scopes/{scope_id}/artifacts/{family}` lists only current heads in that family. The query accepts only
optional `limit` and `cursor`. Items omit complete `content`, and the response has no `total`.

The cursor is opaque and bound to the caller, `scope_id`, `family`, stable ordering, and expiration. An invalid or
collection-mismatched cursor returns `400 Bad Request`; an expired cursor returns `410 Gone`. The HTTP cursor is
unrelated to internal `pc_source_cursors`.

### Replace Artifact

`PUT /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` interprets and validates `content` using the family in
the path. Memory uses an entries command (`entry_id` omitted to append, provided to revise a current logical entry);
the other families use complete content. `If-Match` is required. After matching the current head ETag, the server
creates a new `lineage_only` Source bound to the next Revision and places it first in that Revision's Source lineage.
The writer inherits the previous Artifact lineage or derives Handoff citation lineage, creates the next immutable
Revision, and returns the new
`ArtifactRevision` and ETag. The returned `sources` array contains the newly generated `source_type` and
`source_id`. The operation supports neither merge patch nor automatic merging.

## System Source invariants

The system Source generated by Artifact Create or Replace uses public `source_type=content`. Its public `content`
is the validated and canonicalized write command. For Memory, the Source stores the entries command while Artifact
GET returns generated canonical `MemoryContent`. The server-reserved portion of the typed payload contains:

```json
{
  "role": "lineage_only",
  "operation": "artifact_replace",
  "target": {
    "scope_id": "scp_01J...",
    "family": "memory",
    "artifact_id": "mem_01J...",
    "revision": 2
  }
}
```

These are not OpenAPI fields. A caller cannot submit, overwrite, or impersonate them. A historical Source without
`role` is treated as ordinary `evidence`.

A `lineage_only` Source can be written only to the lineage of its exact target Revision. Explicit generation, Propose,
Candidate Revise, Handoff citation, or another flow that attempts to use it for a different target returns
`422 source_not_eligible`.

The implementation provides one shared Source generation admission check after resolving the complete Source from
persistence and before it enters a model or Candidate. Memory, Experience, Skill, Handoff, and future generation
flows must reuse this check. Candidate Approve and Artifact commit validate again before persistence so an illegal
reference cannot enter a formal Artifact through another path.

The system Source still enters the existing Source journal and receives a normal `journal_position`. A
Source-window consumer filters `lineage_only` Sources from model input but advances its cursor over the complete
window after business processing succeeds. If every Source in the window is filtered, it does not call the model,
creates no Revision, advances the cursor normally, and returns a no-op.

The atomic Artifact Create/Replace transaction proceeds as follows:

```text
1. Validate scope_id, family, content, and the Replace precondition when applicable
2. Determine the new Artifact identity and Revision, then generate source_id
3. Build the lineage_only Source with that exact target Revision
4. Insert pc_sources and allocate journal_position
5. Invoke the Family-owned writer to write the Revision/head and maintain Family constraints and derived state
6. Insert pc_artifact_lineage_sources with the new Source at ordinal = 0
7. Memory writes Entry Versions, manifest, changes, entry heads, and search projection; Experience/Skill refresh search projections, Skill synchronizes its package, and Handoff validates citations and singleton identity
8. Commit the transaction
```

Any failure rolls back the whole operation. It must not leave an orphaned Source, an Artifact without Source
lineage, or an advanced journal head without the corresponding record. The implementation must not call standalone
Source Create first and then write the Artifact Revision in another transaction.

## HTTP headers, requests, and responses

Every success and error response includes:

```http
X-PowerContext-Request-ID: <request-id>
```

ETag is an opaque HTTP validator for the representation of the current Artifact head. It is not a business field
and does not replace `revision`. Clients must not parse or construct it and must send it back unchanged, including
quotation marks.

- Artifact Create, Get head, and Replace return ETag;
- missing `If-Match` on Replace returns `428`, and a mismatch with the current head ETag returns `412`;
- matching `If-None-Match` on Get head returns bodyless `304`;
- exact Revisions do not use ETag.

Fields already fixed by the path are not repeated in the request body. Create Source `source_type` and Create
Artifact `family` are exceptions used to select a type from a parent collection. Undeclared request body fields
return `422` instead of being ignored.

`content_digest` covers only canonical `content`, not identity or lineage. Each successful Source or Artifact Create
creates a new resource. After a client timeout, the caller must confirm the result from business context and cannot
assume that retry is idempotent.

Explicit Atomic Memory creation through `remember_memory` is outside these operations and is the only Atomic Memory
creation path with an idempotency key. The "Idempotent explicit creation" section of
[RFC 1809](1809-atomic-memory.md) defines its optional `idempotency_key` request body field, namespace, request
digest, replay, and retention. That field does not apply to Source Create or Artifact Create, and no operation accepts
an `Idempotency-Key` header. Creating an Atomic Memory through Artifact Create remains non-idempotent.

## Status and error model

| Status | Scenario |
| --- | --- |
| `200 OK` | Get, List, or Replace succeeds. |
| `201 Created` | Source Create or Artifact Create succeeds. |
| `304 Not Modified` | `If-None-Match` matches on Get Artifact head. |
| `400 Bad Request` | Malformed path, query, header, or collection-mismatched cursor. |
| `401 Unauthorized` | Credentials are missing or invalid. |
| `403 Forbidden` | The authenticated caller lacks Scope or resource permission. |
| `404 Not Found` | The Scope, Source, Artifact, or Revision does not exist or is hidden. |
| `409 Conflict` | A uniqueness, Revision commit, or internal-state conflict occurs. |
| `410 Gone` | A List cursor has expired. |
| `412 Precondition Failed` | Replace `If-Match` differs from the current head ETag. |
| `413 Content Too Large` | `content` exceeds the configured limit. |
| `422 Unprocessable Entity` | Type, family content, extra-field, or Source-admission validation fails. |
| `428 Precondition Required` | Replace omits `If-Match`. |
| `429 Too Many Requests` | The request is rate-limited. |
| `503 Service Unavailable` | A dependency is temporarily unavailable. |

The common error body is:

```json
{
  "error": {
    "code": "precondition_failed",
    "message": "artifact head changed",
    "details": {}
  }
}
```

Errors must not reveal whether a resource in another Scope exists. Permission and invisibility scenarios return
`403` or `404` according to the common security policy.

For `remember_memory`, an idempotency key already bound to a different request returns `409` with the existing code
`idempotency_conflict` and `details.kind` set to `memory_idempotency_key`. No new error code is introduced for it.

## OpenAPI contract

`openapi/powercontext.yaml` is the sole source of truth for the HTTP contract. The principal schemas are:

```json
{
  "schemas": [
    "CreateSourceRequest",
    "SourceRecord",
    "CreateArtifactRequest",
    "ReplaceArtifactRequest",
    "ArtifactCreated",
    "ArtifactRevision",
    "ArtifactCollectionItem",
    "ArtifactPage"
  ],
  "request_headers": ["If-Match", "If-None-Match"],
  "response_headers": ["Location", "ETag", "X-PowerContext-Request-ID"]
}
```

Every operationId is unique and stable and has success and error examples. The contract adds no Source/Artifact
union, generic selector, `source_ref`, `artifact_ref`, or writable `source_refs`, `artifact_refs`, `sources`, or
`artifacts` request fields.

## API-to-persistence mapping

OpenAPI fields and semantics are public contract. Table and column names describe only the current implementation.
This RFC reuses the existing schema and requires no new columns.

### Source fields

| API field | Persistence field | Mapping | Meaning |
| --- | --- | --- | --- |
| `scope_id` | `pc_sources.scope_id` | `direct` | Source owner Scope, authorization boundary, and identity component. |
| `source_type` | `pc_sources.source_type` | `direct` | The only public value in this release is `content`. |
| `source_id` | `pc_sources.source_id` | `direct` | Server-generated Source ID. |
| `content` | `pc_sources.payload` | `encoded` | Content Source body; a system Source stores the canonical Artifact Create/Replace command. |
| `position` | `pc_sources.journal_position` | `direct` | Position in the owning Scope's Source journal. |
| `content_digest` | no independent column | `derived` | SHA-256 digest of canonical `content`. |

`pc_source_journal_heads.position` is the Scope-level high-water mark and allocation source, not an individual
Source position.

Internal-purpose fields for a system Source are encoded in a server-reserved portion of `pc_sources.payload`, are
absent from OpenAPI, and add no database columns:

| Internal field | Meaning |
| --- | --- |
| `role=lineage_only` | Forbid this Source from other Artifact generation or Candidate evidence. |
| `operation` | `artifact_create` or `artifact_replace`; records which foundational Artifact write supplied the input. |
| `target.scope_id` | Bind the target Artifact Scope. |
| `target.family` | Bind the target family. |
| `target.artifact_id` | Bind the target Artifact ID. |
| `target.revision` | Allow lineage only for the exact Revision created by the write. |

### Artifact fields

| API field | Persistence field | Mapping | Meaning |
| --- | --- | --- | --- |
| `scope_id` | `scope_id` in Artifact, head, and lineage tables | `direct` | Artifact owner Scope, authorization boundary, and identity component. |
| `family` | `pc_artifacts.family`, `pc_artifact_heads.family` | `direct` | Family and adapter route. |
| `artifact_id` | `pc_artifacts.artifact_id`, `pc_artifact_heads.artifact_id` | `direct` | Server-generated Artifact ID. |
| `revision` | `pc_artifacts.revision`, `pc_artifact_heads.revision` | `direct` | Immutable revision number increasing from 1. |
| `content` | `pc_artifacts.content` | `encoded` | Canonical complete content validated or generated by the Family writer; Memory stores the command result. |
| `sources` | `pc_artifact_lineage_sources` | `relation/derived` | Same-Scope Source identities assembled by Revision and ordinal. |
| `artifacts` | `pc_artifact_lineage_artifacts` | `relation/derived` | Upstream Artifact Revisions assembled by Revision and ordinal. |
| `content_digest` | no independent column | `derived` | SHA-256 digest of canonical `content`. |

`sources` relation mapping:

```json
{
  "child_identity": ["scope_id", "family", "artifact_id", "revision"],
  "ordinal": "array order",
  "sources[].source_type": "source_type",
  "sources[].source_id": "source_id"
}
```

`artifacts` relation mapping:

```json
{
  "child_identity": ["scope_id", "family", "artifact_id", "revision"],
  "ordinal": "array order",
  "artifacts[].family": "upstream_family",
  "artifacts[].artifact_id": "upstream_artifact_id",
  "artifacts[].revision": "upstream_revision"
}
```

### HTTP and pagination fields

| Field | Mapping | Meaning |
| --- | --- | --- |
| `limit` | `runtime` | Maximum number of Artifact List items on one page. |
| `cursor` | `runtime` | Opaque pagination token, unrelated to `pc_source_cursors`. |
| `next_cursor` | `derived/runtime` | Generated from page-end position and query context. |
| `Location` | `derived` | Canonical URI assembled from the identity after Create. |
| `ETag` | `derived` | Opaque HTTP validator for the current Artifact head. |
| `If-Match` | `runtime` | Replace precondition. |
| `If-None-Match` | `runtime` | Get-head conditional request. |
| `X-PowerContext-Request-ID` | `runtime` | Per-request trace ID. |

Digest rule:

```json
{
  "algorithm": "sha256",
  "input": "UTF-8 canonical JSON bytes of API content",
  "object_key_order": "lexicographic",
  "insignificant_whitespace": "removed",
  "included_fields": ["content"],
  "output": "sha256:<64 lowercase hexadecimal characters>"
}
```

## Existing API compatibility

This RFC defines only new interfaces. Existing paths, requests/responses, status codes, and domain behavior are out
of scope. The new entry points read and write the same authoritative Source journal, Artifact Revisions, lineage,
and authorization decisions.

## Implementation and acceptance

Implementation steps:

1. Add the seven operations to OpenAPI;
2. restrict public `source_type` to `content` and `family` to the four public values;
3. generate four discriminated Create requests and dispatch Artifact Create/Replace to Family-owned writers;
4. create a target-bound `lineage_only` Source at ordinal zero in every Artifact Create/Replace transaction, then let
   the Handoff writer derive citation lineage;
5. add one shared Source admission check across all model, Candidate, and commit paths;
6. filter `lineage_only` Sources from Source windows while advancing the full cursor and returning no-op when all
   entries are filtered;
7. load Artifact lineage in batches by complete Revision identity and assemble top-level identities and arrays;
8. reuse the current Source journal, Artifact repository, Memory service, Handoff service, Skill package, and
   Family search projections;
9. run generated-code, contract, unit, documentation, SQLite, and OceanBase behavior tests.

Acceptance criteria:

- Only seven operations are added below the two Scope child trees, without redefining Scope API;
- Source exposes only Create/Get, allows only `content`, and exposes no metadata, timestamps, or internal-purpose
  fields;
- Artifact exposes only Create/Get/Get Revision/List/Replace, without Search or Delete;
- all four public families use their owning writer for Create/Get/List/Replace and maintain authoritative constraints
  and derived state;
- Memory Create accepts non-empty `entries[].kind/text`, preserves open `kind`, and produces canonical Memory state
  and search projections;
- Experience/Skill Create and Replace refresh existing search projections, and Skill synchronizes its standard package;
- Handoff Create uses the Scope singleton `handoff`, duplicate Create returns 409 with a Replace hint, and both Create
  and Replace reuse citation validation;
- Artifact Create accepts only `family` and `content`; Replace accepts only `content`;
- Artifact requests accept no lineage field;
- Artifact Create and every successful Replace atomically generate a target-bound `lineage_only` Source at ordinal
  zero; Handoff additionally derives its direct citation lineage through the existing service;
- Replace returns that new Source identity and preserves older Revision Sources; non-Handoff writers inherit prior
  Artifact lineage while Handoff derives it from replacement content;
- every generation, Candidate, and commit path rejects using that Source for another target;
- Source-window consumers advance over filtered records and return no-op without model or Revision when all are
  filtered;
- any failure writing Source, Artifact Revision, head, or lineage rolls back all writes;
- Artifact responses flatten identity and return ordered lineage through `sources` and `artifacts`;
- Source and Artifact responses contain no server timestamp;
- Replace uses opaque ETag/If-Match and Get head supports If-None-Match;
- every response carries `X-PowerContext-Request-ID`;
- no new metadata-table column is required;
- SQLite and OceanBase pass the same contract and behavior tests.

# Drawbacks

- Family-specific Create `content` has precise generated-client types through the discriminator, while responses
  still require family-specific interpretation of canonical content;
- each family needs stable deserialization, validation, and canonical serialization adapters;
- lineage arrays on List increase relationship-query cost, so implementations must avoid per-item queries;
- every Artifact Create and successful Replace writes an additional Source and lineage row, growing the Source journal;
- Source admission becomes a cross-family security invariant, and a new path that bypasses it could leak a
  `lineage_only` Source;
- old and new read paths need shared application services and parity tests to prevent behavior drift;
- compound identities produce longer URIs, and changing owner Scope, Source type, or Artifact family changes the
  canonical URI;
- Source Create and a subsequent domain command are not transactional, so callers must handle retry after a later
  failure.

# Rationale and alternatives

## No generic Resource API

Source and Artifact have different lifecycles, identities, and write constraints. Generic CRUD would blur Source's
no-Revision semantics, immutable Artifact Revisions, and family-specific validation, so this RFC keeps two trees.

## Publish only known types and families

Arbitrary strings would expose unknown values directly to internal adapters and prevent OpenAPI from expressing
capabilities. This release publishes only the existing stable models: Source `content` and four Artifact families.
A new value requires a domain model, canonical serializer, and behavior tests.

## No Search or Source List

This release stabilizes identity access and the Artifact lifecycle without introducing cross-adapter retrieval
semantics. Artifact List returns current heads only within one family. Source List/Search, Artifact Search, and
cross-family List need separate contracts.

## Generate a system Source for every foundational Artifact write

Caller-supplied lineage references cannot ensure that every Revision has a real direct input and expand the surface
for invalid references. A new target-bound `lineage_only` Source created in each Create or Replace transaction
provides per-Revision provenance, atomicity, and generation isolation together.

## Flatten identity and expose read-only lineage arrays

Top-level identity avoids an `artifact_ref` envelope. Arrays preserve one-to-many lineage and ordinal order without
introducing a new public domain object. The server derives lineage from authoritative relationship tables so the
foundational API cannot bypass domain flows by writing arbitrary relationships.

## Keep ETag opaque

`revision` is a domain version, while ETag is an HTTP representation validator. Even if the server derives an ETag
from the head Revision, clients cannot depend on its encoding. This allows the representation strategy to change
without breaking the business contract.

# Prior art

- HTTP conditional requests provide `ETag`, `If-None-Match`, `If-Match`, `304`, `412`, and `428` for cache reads and
  optimistic concurrency;
- the existing Source journal provides stable positions and consumer cursors;
- the existing Artifact repository uses immutable Revisions, a head pointer, and ordered lineage tables;
- the Memory, Experience, Skill, and Handoff domain models provide the family-specific validation basis.

# Unresolved questions

This RFC has no unresolved question that blocks acceptance. The following are implementation details that do not
change the public contract:

- the server-generated ID algorithm, provided values are opaque, path-safe, and within length limits;
- ETag and cursor encoding or signing, provided values remain opaque and preserve conditional and expiry semantics;
- the versioned representation of reserved `lineage_only` fields in the Content Source payload;
- the query and caching strategy used to load Artifact List lineage in batches.

# Future possibilities

- Add Source List/Search, Artifact Search, or cross-family List through a separate RFC;
- publish another Source type or Artifact family after adding its domain model, canonical serializer, and tests;
- design cross-Scope lineage, sharing, and authorization;
- design Artifact restore, retention, administrative purge, and bulk mutation;
- add explicit version migration and observability for internal Source roles.
