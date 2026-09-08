- Proposal Name: `unified_artifact_tags`
- Start Date: 2026-09-05
- RFC PR: [oceanbase/powercontext#1467](https://github.com/oceanbase/powercontext/pull/1467)
- Related Discussion: [oceanbase/powercontext#1466](https://github.com/oceanbase/powercontext/issues/1466)
- Related RFCs: [RFC 0014](0014_memory_layer_design.md), [RFC 0019](0019_local_source_memory_runtime.md),
  [RFC 0048](0048_handoff_artifact.md), [RFC 0051](0051_experience_skill_artifact_families.md),
  [RFC 1396](1396_handoff_access_control.md), and [RFC 1437](1437_source_artifact_rest_api.md)

# Summary

This RFC adds scope-local custom tags to PowerContext-managed content. A user can attach tags such as `customer-a`,
`cockpit`, or `verified` to a managed Experience, Skill, Handoff, whole Memory Artifact, or individual Memory entry,
then retrieve current resources by exact tag membership.

The feature uses one `pc_artifact_tags` assignment table for every supported target. Each row retains the owning
Artifact identity and identifies either that Artifact or one logical Memory entry inside it. This preserves a uniform
product and API model without pretending that a Memory entry is an independent Artifact.

Tags are mutable catalog attributes of a logical target. They are not Artifact content, Source metadata, lineage,
authorization policy, lifecycle state, or prompt instructions. Tag changes do not create Artifact Revisions, alter
content digests, rebuild embeddings, or change exact citations. Tags remain local to their owner Scope and are not
copied by Artifact publication unless a later contract explicitly requests that behavior.

The first delivery provides tag read and compare-and-swap replacement, exact `all` and `any` tag queries, optional tag
filters on current-resource listing and Memory search, and a minimal Dashboard editor and filter. It deliberately does
not add tag hierarchies, colors, aliases, automatic tagging, tag-based authorization, or historical tag snapshots.

# Motivation

Teams accumulate many Memory entries, Experiences, and Skills in the same Scope. Text and semantic search answer
"which content resembles this query?" but do not reliably answer organizational questions such as:

- Which entries belong to customer A?
- Which Skills have been validated for the automotive cockpit environment?
- Which Experiences describe release operations rather than inference behavior?
- Which retired or inactive resources still belong to a compliance review set?

Users can encode some of this information in content, but doing so mixes classification with the knowledge itself.
Changing a classification would then create a new immutable Revision, change the content digest, and potentially
rebuild a search projection even though the reusable content did not change.

Existing fields named `metadata` do not provide a shared solution. `ContentSource.metadata` contains provenance and
behavior-bearing fields such as `kind`. Managed Skill metadata belongs to exact Skill content and participates in its
search projection. Treating either field as a generic mutable tag bag would blur ownership, version, indexing, and
security semantics.

The persistence model also has two observable target granularities:

- Experience, Skill, Handoff, and a whole Memory are logical Artifact lifecycles selected by `artifact_id`;
- the Memory shown to a user as one remembered fact or preference is a logical entry selected by `entry_id` inside the
  Scope's Memory Artifact.

Putting one JSON tag column only on `pc_artifact_heads` would give every entry in the standard one-Memory-per-Scope
profile the same tag set. Adding separate family-specific tag stores would preserve granularity but fragment the API
and cross-family query path. PowerContext needs one explicit assignment model that handles both target shapes.

# Guide-level explanation

## User model

A tag is a user-authored string attached to one current logical target in one Scope. It is useful for exact grouping
and filtering. It does not make a claim true, approve an Artifact, grant access, or instruct an Agent.

The same visible behavior applies to every supported resource:

```text
Scope: vehicle-assistant

Memory entry: "The driver prefers 24 C in winter"
Tags: [customer-a, cockpit, preference]

Experience: "Regenerate the Client after editing OpenAPI"
Tags: [release, verified]

Skill: "vehicle-log-triage"
Tags: [customer-a, diagnostics]

Handoff: "Complete the cockpit latency investigation"
Tags: [customer-a, in-progress]
```

Users may also tag the whole Memory Artifact when the classification applies to the collection rather than one entry.
The UI must distinguish `Memory` from `Memory entry` so that a collection tag is not mistaken for an entry tag.

## Add and remove tags

The Dashboard displays tags as inert, escaped chips. A user with write authority opens a resource, edits the complete
tag set, and saves it. Saving is conditional on the tag set observed by the editor so that two editors cannot silently
overwrite each other.

For example, reading the current set returns an opaque `ETag` response header and this body:

```json
{
  "tags": ["cockpit", "customer-a"],
  "tag_digest": "sha256:92f..."
}
```

The user replaces it with:

```json
{
  "tags": ["cockpit", "customer-a", "verified"]
}
```

The client sends the observed ETag in `If-Match`. It does not derive a precondition from `tag_digest`.

Replacing a set with the same normalized tags is idempotent. Replacing it with an empty array removes every tag but
does not delete, retire, or revise the target.

## Retrieve by tags

Tag matching is exact after normalization. A filter for `customer-a` does not match `customer-a-archive`, and a tag
named `release/security` has no implicit parent named `release`.

Multiple tags have an explicit match mode:

- `all` selects targets that have every requested tag;
- `any` selects targets that have at least one requested tag.

For example:

```json
{
  "families": ["memory", "experience", "skill"],
  "target_types": ["artifact", "memory_entry"],
  "tags": ["customer-a", "verified"],
  "match": "all",
  "limit": 50
}
```

The response returns logical targets together with current exact references. An Artifact result includes its current
`ArtifactReference`; a Memory entry result includes its current `MemoryCitation`. The exact references let a caller
read content without resolving `latest` a second time.

Text retrieval can combine a query with a tag filter. PowerContext applies the tag filter to the eligible candidate
set before FTS, vector top-k selection, fusion, or reranking. Filtering an already truncated top-k result is incorrect
because a relevant tagged item may have been excluded before the filter ran.

## Revision behavior

Tags follow logical identity:

```text
Experience exp-1 Revision 1 --\
Experience exp-1 Revision 2 ----> tags for logical exp-1

Memory entry entry-1 Version 1 --\
Memory entry entry-1 Version 2 ----> tags for logical entry-1
```

Revising `exp-1` or `entry-1` therefore preserves its tags. Tagging either target does not create a content Revision.
An exact historical Artifact or Memory citation remains a statement about immutable content and evidence; it does not
implicitly acquire a historical tag snapshot.

## Scope and publication behavior

Tags belong to the Scope in which they were assigned. Publishing or copying an Artifact to another Scope creates the
destination logical Artifact with an empty tag set. This default avoids leaking customer names, internal workflow
categories, or other local classifications. A caller may assign destination tags explicitly after publication.

# Reference-level explanation

## Goals

The first implementation must:

- provide one tag model for managed Artifacts and logical Memory entries;
- persist all assignments in one table;
- keep tag mutation independent from immutable Artifact and Memory-entry content;
- support exact `all` and `any` filtering with deterministic normalization;
- combine tags correctly with current Artifact listing and Memory retrieval;
- preserve Scope isolation, target visibility, and existing lifecycle filters;
- provide concurrency-safe, idempotent complete-set replacement; and
- expose enough current exact identity for a query result to be resolved safely.

## Non-goals

The first implementation does not define:

- tag colors, descriptions, aliases, hierarchy, inheritance, or a tag-definition catalog;
- automatic tag generation by a model, Source metadata, or content keywords;
- tags as authorization, approval, trust, lifecycle, routing, or retention policy;
- historical tag snapshots or tag assignment audit history;
- tag propagation through lineage, publication, fork, import, or Handoff evidence;
- revision-specific tags;
- MCP tools that let an Agent mutate tags automatically; or
- arbitrary key/value Artifact metadata.

## Terminology and target identity

`ArtifactTagTarget` is a discriminated union:

```text
ArtifactTagTarget =
  ArtifactTarget {
    type: "artifact",
    family: string,
    artifact_id: string
  }
  | MemoryEntryTarget {
    type: "memory_entry",
    family: "memory",
    artifact_id: string,
    entry_id: string
  }
```

An `ArtifactTarget` identifies a logical Artifact lifecycle and intentionally omits `revision`. A
`MemoryEntryTarget` identifies one logical Memory entry and intentionally omits both Memory Revision and
`entry_version_id`. The containing `artifact_id` remains explicit because `entry_id` is scoped by its Memory Artifact.

The canonical persistence form uses `target_id`:

| Target | `family` | `artifact_id` | `target_type` | `target_id` |
| --- | --- | --- | --- | --- |
| Experience | `experience` | Experience ID | `artifact` | same Experience ID |
| Skill | `skill` | Skill ID | `artifact` | same Skill ID |
| Handoff | `handoff` | Handoff ID | `artifact` | same Handoff ID |
| Whole Memory | `memory` | Memory ID | `artifact` | same Memory ID |
| Memory entry | `memory` | containing Memory ID | `memory_entry` | entry ID |

New nested resource kinds must not reuse `memory_entry` or overload `target_id`. They require an explicit target type
and validation rule in a follow-up contract.

## Tag value and normalization

A submitted tag must satisfy all of the following:

- it is a Unicode string between 1 and 64 Unicode code points;
- it has no leading or trailing whitespace;
- it contains no Unicode control, surrogate, or unassigned code point;
- its normalized key is at most 128 Unicode code points; and
- the complete submitted set contains at most 32 tags.

PowerContext preserves the submitted value as `tag` for display. It computes `tag_key` by applying Unicode NFC and
then Unicode default case folding. It does not collapse internal whitespace, split punctuation, parse `/`, translate,
stem, or infer hierarchy. Both `tag` and `tag_key` are validated after normalization.

Two submitted tags with the same `tag_key` are duplicates and the complete request is rejected. The Server does not
silently choose a display spelling. A later successful replacement may change only the preserved display spelling
while retaining the same `tag_key`.

Tag sorting is ascending by UTF-8 bytes of `tag_key`. This order is used in responses and digest calculation so that
database collation does not change public behavior.

## Persistence

The shared relational schema adds exactly one business table:

```text
pc_artifact_tags
  scope_id       identity string, not null
  family         identity string, not null
  artifact_id    identity string, not null
  target_type    identity string, not null
  target_id      identity string, not null
  tag_key        identity string, not null
  tag             display string, not null
  assigned_at    UTC timestamp, not null

  primary key (
    scope_id,
    family,
    artifact_id,
    target_type,
    target_id,
    tag_key
  )

  foreign key (scope_id, family, artifact_id)
    references pc_artifact_heads (scope_id, family, artifact_id)
    on delete cascade

  check target_type in ('artifact', 'memory_entry')
  check target_type != 'artifact' or target_id = artifact_id
  check target_type != 'memory_entry' or family = 'memory'
```

The table has these secondary indexes:

```text
(scope_id, family, tag_key, target_type, artifact_id, target_id)
(scope_id, tag_key, family, target_type, artifact_id, target_id)
```

The primary key supports loading one target's tags. The first secondary index supports family-specific filtering; the
second supports a cross-family Scope query. Implementations must use binary identity comparison or application-built
`tag_key` values rather than depending on database-default case or locale collation.

The owning Artifact foreign key is enforced for every row. For `memory_entry`, the repository must lock the owning
`(scope_id, family="memory", artifact_id)` Artifact head, load the exact Revision it points to, and validate `entry_id`
against that Revision's authoritative `MemoryContent.manifest.entries` in the same transaction before changing
assignments. Both `active` and `inactive` manifest entries are valid targets. An entry absent from that manifest is
rejected even if an older Revision or immutable entry-version row contains it.

`pc_memory_entry_heads` contains only active search projections. Deactivation removes an entry's projection while
retaining its logical identity and content in the authoritative manifest. Tag reads and mutations must not require a
row in that table, and tag assignments must not reference it through a foreign key. Projection cleanup and rebuilding
must leave tag assignments unchanged.

Inactive Memory entries and deprecated or retired Artifacts retain their assignments. Tag queries apply the existing
visibility and lifecycle selection before returning results. A caller with the target's write authority may reorganize
tags without reactivating or revising content.

## Tag set and digest

`ArtifactTagSet` contains:

```json
{
  "scope_id": "vehicle-assistant",
  "target": {
    "type": "memory_entry",
    "family": "memory",
    "artifact_id": "memory",
    "entry_id": "mem_ent_123"
  },
  "tags": ["cockpit", "customer-a"],
  "tag_digest": "sha256:..."
}
```

`tag_digest` is the SHA-256 digest of RFC 8785 canonical JSON for the object `{"tags": [...]}`, where tags are in
canonical `tag_key` order and each array item is the preserved display string. The empty set has a stable digest. It
is a content checksum for the tag set, not a client-visible compare-and-swap token, Artifact content digest, Memory
entry content hash, or authorization generation.

HTTP operations use an opaque ETag that binds the complete logical target identity and `tag_digest`. Clients must not
assume that the ETag equals, embeds, or can be reconstructed from `tag_digest`.

## Repository contract and transactions

The tag repository exposes three operations:

```text
get(scope_id, target) -> ArtifactTagSet

replace(
  scope_id,
  target,
  expected_tag_digest,
  tags
) -> ArtifactTagSet

query(
  scope_id,
  tags,
  match,
  families,
  target_types,
  lifecycle_selection,
  limit,
  cursor
) -> ArtifactTagPage
```

`replace` performs the following steps in one transaction:

1. Resolve and authorize the target without returning hidden existence details.
2. Lock the owning Artifact head and load the exact Revision it points to. For a Memory entry, validate `entry_id`
   against that Revision's manifest, accepting either `active` or `inactive` state.
3. Load the complete current tag set and calculate its digest.
4. Reject a mismatched expected digest as a failed precondition.
5. Validate and normalize the complete replacement set.
6. Delete assignments absent from the replacement and insert or update the remaining display values.
7. Return the canonical set and new digest.

Locking the existing owning head serializes replacement even when the current tag set is empty. Implementations must
not rely on locking zero assignment rows or on a process-local lock, because neither provides the required distributed
compare-and-swap behavior.

If the current canonical set equals the requested set, `replace` is an idempotent success and changes no rows or
`assigned_at` values.

`get` uses the same authoritative manifest membership rule for Memory entries. An authorized caller can read,
replace, or clear an inactive entry's tags without reactivating it or requiring a search projection.

## HTTP contract

The first contract adds five operations below the Scope resource tree established by RFC 1437:

| Method | Path | operationId | Purpose |
| --- | --- | --- | --- |
| `GET` | `/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/tags` | `get_artifact_tags` | Read an Artifact's current tags |
| `PUT` | `/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/tags` | `replace_artifact_tags` | Replace an Artifact's complete tag set |
| `GET` | `/v1/scopes/{scope_id}/artifacts/memory/{artifact_id}/entries/{entry_id}/tags` | `get_memory_entry_tags` | Read a Memory entry's current tags |
| `PUT` | `/v1/scopes/{scope_id}/artifacts/memory/{artifact_id}/entries/{entry_id}/tags` | `replace_memory_entry_tags` | Replace a Memory entry's complete tag set |
| `POST` | `/v1/scopes/{scope_id}/artifact-tags/query` | `query_artifact_tags` | Retrieve visible current targets by exact tags |

The two target shapes expose the same `ArtifactTagSet` schema, validation, authorization rules, and repository. The
extra `entries/{entry_id}` path segment expresses containment; it does not create a second tag model or table. Paths
name current logical targets only. Exact Artifact Revision and Memory entry-version paths have no tag subresource.

### Get

Both GET operations return `200 ArtifactTagSet` and an opaque `ETag`. They support `If-None-Match` and return
`304 Not Modified` without a body when it matches. A visible target with no assignments returns `200`, an empty set,
and an ETag. A missing or non-visible target follows the same `404` behavior as reading that target.

### Replace

Both PUT operations accept the complete replacement set:

```json
{
  "tags": ["Customer-A", "cockpit", "verified"]
}
```

`If-Match` is required. The server resolves the opaque validator to the expected target-bound tag state, performs the
repository replacement, and returns `200 ArtifactTagSet` plus the new ETag. Missing `If-Match` returns
`428 Precondition Required`; a mismatch returns `412 Precondition Failed`. The response does not disclose the current
ETag or tag values to a caller that cannot read the target. PUT does not create an Artifact Revision.

### Query

```json
{
  "tags": ["customer-a", "verified"],
  "match": "all",
  "families": ["memory", "experience", "skill"],
  "target_types": ["artifact", "memory_entry"],
  "include_inactive": false,
  "limit": 50,
  "cursor": null
}
```

`tags` has between 1 and 16 unique normalized values. `match` defaults to `all`. Omitted `families` and
`target_types` select every supported value. `include_inactive` defaults to false and never bypasses authorization;
it only expands lifecycle selection for callers already allowed to inspect inactive content.

Each page item contains the logical `target`, all current tags, and one current exact content reference:

```json
{
  "target": {
    "type": "memory_entry",
    "family": "memory",
    "artifact_id": "memory",
    "entry_id": "mem_ent_123"
  },
  "current": {
    "memory_ref": {
      "family": "memory",
      "artifact_id": "memory",
      "revision": 12
    },
    "entry_id": "mem_ent_123",
    "entry_version_id": "mem_ver_456"
  },
  "tags": ["Customer-A", "cockpit", "verified"]
}
```

Artifact targets use an `ArtifactReference` in `current`; Memory entry targets use `MemoryCitation`. Items are ordered
by `(family, target_type, artifact_id, target_id)` using UTF-8 byte order. The opaque cursor binds the Scope, normalized
tag keys, match mode, selected families, target types, lifecycle selection, caller, expiration, and last ordering key.
An invalid or filter-mismatched cursor returns `400 Bad Request`; an expired cursor returns `410 Gone`.

For each Memory entry target matched by the tag assignments, resolve the owning Artifact head once and load that
exact Revision's manifest. Use the manifest entry's state for lifecycle selection and its `entry_version_id`, together
with that same Memory Revision, to build the citation. With `include_inactive=true`, eligible inactive entries remain
discoverable without a `pc_memory_entry_heads` row. Do not use an inner join to active projections to determine their
existence or citation. Apply manifest membership, lifecycle selection, and authorization before the page limit.

Tag mutations between pages can change membership. Pagination guarantees deterministic keyset traversal for each
query but does not claim a database snapshot across requests.

## Existing list and search integration

The following existing request surfaces gain optional filtering with the same normalized `tags` and `match`
semantics:

- RFC 1437's `GET /v1/scopes/{scope_id}/artifacts/{family}` adds repeatable `tag` and optional
  `tag_match=all|any` query parameters;
- Memory entry listing adds an optional `tag_filter` request field; and
- Memory search adds an optional `tag_filter` request field.

The parameters or field are absent by default and therefore preserve existing behavior. A present filter must contain
at least one tag. `tag_match` without `tag` is invalid. Artifact-head listing only matches `artifact` targets.
Memory-entry list and search only match `memory_entry` targets. Artifact-list cursors additionally bind the normalized
tags and match mode; the RFC 1437 invalid, mismatched, and expired cursor statuses remain unchanged.

Memory-entry listing with `include_inactive=true` uses the same manifest-based membership, state, and citation rules
as the dedicated tag query. Memory search retains its existing active-entry eligibility; inactive catalog discovery
does not make an entry searchable.

Existing Artifact-list, Memory-entry-list, and Memory-search item schemas do not gain a `tags` field; their tag filter
changes eligibility only. The dedicated tag query includes current tags, and callers can GET the logical target's tag
subresource when current catalog metadata is required. Exact historical Artifact Revision and Memory entry-version
responses do not acquire a `tags` field.

Memory search applies tag eligibility inside both FTS and vector candidate queries before channel limits. Hybrid
search applies the same eligible target set to both channels before fusion and reranking. A backend that cannot apply
the filter before top-k must report the combined mode unavailable rather than silently over-fetching and returning an
incomplete result.

This RFC does not add tag constraints to automatic `PreparedContext` assembly. A later use case may add a typed
selection profile, but tags never enter a model prompt merely because they exist.

## Authorization and trust boundary

`scope_id` is a business partition, not proof of authority. Tag operations use the same Server authentication and
authorization boundary as the target resource:

- reading tags requires permission to read the target;
- replacing tags requires permission to mutate the target's catalog metadata;
- query results include only targets the principal may discover and read; and
- `include_inactive` does not broaden resource access.

The initial implementation may map metadata mutation to the existing target write authority. A deployment must not
infer access from tag values, create grants from tags, or use tags as a substitute for the access-control Resource
Profile. If a later product needs delegated taxonomy management without content write authority, that requires a
separate action and audit design.

Tags are untrusted display strings. Dashboard rendering must escape them and must not interpret them as HTML,
Markdown, URLs, commands, or CSS classes. Search and application code must use bound parameters. Tags are never
executed and are never injected into Agent instructions by this RFC.

## Publication, import, and lineage

Tag assignments are Scope-local catalog state and do not participate in:

- `ArtifactLineage`;
- content or package digests;
- publication digests;
- Source evidence;
- Candidate approval; or
- managed Skill package metadata.

Publishing, copying, importing, or forking an Artifact does not copy assignments. Destination assignment is a separate
authorized write. Source tags may be displayed to an authorized publisher before that write, but they are not treated
as content provenance.

## Compatibility and migration

The relational initializer adds `pc_artifact_tags` for every supported database profile. Existing Artifacts and Memory
entries require no backfill and behave as if they have an empty tag set.

No migration copies values from `ContentSource.metadata`, `SkillContent.metadata`, Memory `kind`, lifecycle state,
review status, or integration provenance. Those fields have different authorities and semantics.

All additions to existing list and search requests are optional. Older clients that send neither the Artifact-list
parameters (`tag` and `tag_match`) nor the Memory request field (`tag_filter`) retain their current result semantics.
The new operations and schemas are added to `openapi/powercontext.yaml`; generated Python and integration contracts
must be regenerated through the repository's normal contract workflow.

## Observability

The Server may record operation outcome, target type, family, submitted tag count, filter tag count, match mode, result
count, and latency. Logs, traces, metrics, and error messages must not record raw tag values. A normalized tag digest
may be used for correlation only when deployment policy permits it.

Tag mutation must use the ordinary authenticated audit boundary when available. This RFC does not add a historical
assignment table; a deployment that requires a complete tag-change ledger must keep the feature disabled or add the
ledger through a follow-up design before claiming that guarantee.

## Delivery plan

The implementation is split into two reviewable vertical slices:

1. Add target models, normalization, the shared table and repository, ETag-guarded read/replace plus query,
   OpenAPI-generated contracts, and deterministic SQLite and OceanBase/seekDB behavior tests.
2. Add current Artifact and Memory-entry list filters, pre-top-k Memory search filtering, and the minimal Dashboard tag
   editor and exact filter.

The feature is not complete after only adding the table. The first customer-visible release requires both slices so a
user can assign a tag, observe it, retrieve the target with it, and remove it without editing Artifact content.

## Acceptance criteria

The RFC is implemented only when all of the following observable scenarios pass:

1. A caller assigns and reads tags on Experience, Skill, Handoff, and whole-Memory Artifact targets through the same
   tag-set semantics.
2. A caller assigns a different set to one Memory entry without changing tags on another entry in the same Memory.
3. Revising an Artifact or Memory entry preserves its logical target tags and changes no tag because of Revision alone.
4. Replacing tags changes no Artifact Revision, content digest, lineage, Memory entry version, FTS text, or embedding.
5. Empty-set replacement removes all assignments and remains retrievable as an empty tag set.
6. `all` and `any` matching return correct targets across families, with duplicate-normalized input rejected.
7. Artifact listing and Memory entry listing preserve existing lifecycle defaults while applying tag filters.
8. FTS, vector, and hybrid Memory search apply tag eligibility before their candidate limits and return no untagged
   result.
9. A stale tag-set ETag cannot overwrite a concurrent replacement, including when the initial tag set was empty.
10. Pagination rejects a cursor reused with different normalized filters and returns deterministic keyset order.
11. A principal cannot discover, read, or mutate another target's tags without the corresponding target authority.
12. Publication to another Scope creates no destination assignments and cannot expose raw source tags implicitly.
13. Raw tag values do not appear in telemetry, and Dashboard rendering treats adversarial values as inert text.
14. Existing unfiltered API behavior and exact historical content responses remain unchanged.
15. Schema, repository, HTTP contract, generated-client, and supported backend tests pass through repository-standard
    commands.
16. After a tagged Memory entry is deactivated and its search projection disappears, an authorized caller can still
    read and replace its tags. A matching tag query and Memory-entry list with `include_inactive=true` return it with
    a citation resolved from the current manifest; their default requests and Memory search exclude it. Rebuilding
    projections preserves these behaviors and assignments. Clearing the tags then returns an empty tag set, removes
    it from matching tag queries, and leaves its inactive state, Memory Revision, and entry version unchanged.
17. A Memory-entry tag target absent from the current manifest is rejected even when its immutable entry version
    remains stored. An authorized GET of such a target returns `404`, replacement creates no assignments, and tag
    queries omit it.

# Drawbacks

A relational foreign key validates the owning Artifact, but it cannot enforce logical Memory-entry membership in
that Artifact's current manifest. The repository must enforce that membership transactionally. A universal
catalog-item registry would provide a single foreign key, but it would add another persistent identity layer and
another table before any other feature needs one.

Current logical tags are not reconstructible at an earlier time. Exact Artifact content remains reproducible, but the
tag set is only current catalog state. Deployments requiring historical taxonomy audit need an additional event or
history design.

The two query indexes increase write and storage cost. The bounded per-target tag count keeps this cost predictable,
and tag writes are expected to be much less frequent than reads.

Tag pre-filtering must be implemented in every supported Memory search backend. This is more work than filtering final
hits, but it is required for correct top-k behavior.

# Rationale and alternatives

## Store tags in Artifact content

This gives immutable historical tags but turns a classification edit into a content Revision. It changes content
digests, lineage expectations, CAS behavior, and derived indexes without changing reusable knowledge. It is rejected
for user-managed catalog tags.

## Add a JSON tag column to `pc_artifact_heads`

This is a small schema change for whole Artifacts, but it cannot distinguish individual entries in the standard
one-Memory-per-Scope model. Portable indexed `all`/`any` queries also differ across SQLite and MySQL-compatible
backends. It is rejected.

## Add one table for Artifacts and another for Memory entries

Separate assignment tables simplify family-local joins but fragment cross-family queries and duplicate tag
normalization, mutation, pagination, and API behavior. A separate Memory-entry tag table would still need current
manifest validation unless a durable logical-entry registry were also added. The single polymorphic table keeps one
assignment contract and the same explicit application-level Memory-entry invariant.

## Add a universal catalog-item registry

A registry could give every nested and top-level resource a uniform ID and let tags reference one parent table. It
would require new lifecycle, migration, ownership, deletion, and synchronization semantics for all current resources.
Tags alone do not justify that abstraction in the first delivery.

## Rebuild Memory so every entry is an Artifact

This would make physical tag targets uniform, but it would replace the existing Memory Manifest, atomic collection
Revision, entry-version, citation, flush, and index model. It is disproportionate to the customer requirement and is
rejected.

## Use tags as arbitrary metadata keys and values

The requested behavior is set membership and exact filtering. A generic nested metadata object requires type,
operator, indexing, conflict, and authorization semantics that are not needed here. A later key/value metadata feature
must not silently reinterpret tags.

## Do nothing

Users would continue encoding classifications in content, keeping external spreadsheets, or relying on ambiguous text
queries. None provides a consistent, Scope-aware, cross-family retrieval contract.

# Prior art

PowerContext already separates immutable Artifact Revisions from mutable current heads and rebuildable search
projections. RFC 0014 defines Memory entry identity and exact citation; RFC 0019 defines one current Memory Artifact per
Scope in the standard profile; RFC 0048 defines Handoff as a self-contained Artifact lifecycle; and RFC 0051 defines
Experience and managed Skill as independent Artifact families. This RFC applies the same separation of logical identity
and immutable content to user-managed classification.

RFC 1396 separates resource authorization from Artifact content and warns that Scope identity is not authorization.
Tags follow that boundary: they may help a user find a resource but never decide whether the user may access it.

RFC 1437 establishes the Scope-owned Artifact URI tree, opaque HTTP validators for mutable current representations,
and caller- and query-bound expiring cursors. This RFC extends that tree with logical-target tag subresources and
extends Artifact listing without changing exact Revision responses or treating `tag_digest` as an Artifact ETag.

No external system is normative for this RFC. Common repository and issue trackers demonstrate that mutable labels
can organize immutable or versioned content, but PowerContext's Memory-entry containment and exact citation model
require the target contract defined here.

# Unresolved questions

No unresolved question blocks acceptance of the first-delivery contract.

The implementation PR must still confirm backend-specific query plans for the bounded `all` filter and select exact
index names that fit repository naming limits. These are implementation validation details and must not change public
normalization, matching, ordering, or pre-top-k filtering semantics.

The following questions are intentionally outside this RFC:

- whether organizations need managed tag definitions, colors, descriptions, aliases, or rename operations;
- whether tag mutation needs a separately delegable authorization action;
- whether some publication workflow should explicitly offer to copy selected tags;
- whether automated classification can safely propose, but not silently assign, tags; and
- whether a complete historical tag-assignment ledger is required.

# Future possibilities

A later RFC may add a Scope-local tag catalog with descriptions, display color, aliases, usage counts, controlled
rename, or delegated taxonomy management. Such a catalog would describe tags; `pc_artifact_tags` would remain the
assignment relation.

Another extension may let users save named search views that combine tags, families, lifecycle state, and text query.
Saved views must remain queries rather than authorization policy.

Model-assisted classification may propose tags through a reviewed Candidate-like flow. Models must not assign tags
silently, and proposed tags must remain untrusted until an authorized user accepts them.

If multiple nested resource types need tags, access control, favorites, comments, and other catalog metadata, the
project may then justify a universal logical-resource registry. That decision should be based on several proven uses,
not introduced speculatively by this RFC.
