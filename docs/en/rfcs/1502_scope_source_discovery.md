+ Proposal Name: `scope_source_discovery`
+ Start Date: 2026-09-08
+ RFC PR: [oceanbase/powercontext#1502](https://github.com/oceanbase/powercontext/pull/1502)

# Summary

Extend `GET /v1/scopes` with an optional `query` parameter that filters Scope descriptors by a literal substring of
`scope_id`. Add `GET /v1/scopes/{scope_id}/sources` to page through the public Sources owned by one Scope.

Both operations reuse the existing Scope catalog, Source journal, public `SourceRecord` representation, cursor
signing, and authorization boundaries. They add no tables and do not run generation or advance a domain consumer
cursor.

# Motivation

Callers often know only part of a repository-derived Scope ID. The current Scope collection returns every descriptor,
so each client has to download and filter the complete collection. After selecting a Scope, a caller can create a
Source or retrieve one by its complete identity, but cannot discover the Sources already owned by that Scope.

Scope ID matching remains a projection of the existing Scope collection, so it belongs on `GET /v1/scopes` instead
of a separate search action. Source discovery is a read of the existing child collection and therefore belongs on
the existing `/v1/scopes/{scope_id}/sources` resource.

# Guide-level explanation

## Find Scopes by ID

Clients may pass a partial Scope ID:

```http
GET /v1/scopes?query=powercontext
```

```json
{
  "items": [
    {
      "scope_id": "git:github.com/oceanbase/powercontext",
      "title": "PowerContext",
      "summary": "PowerContext repository development",
      "parent_scope_id": null,
      "context_references": [],
      "external_references": [],
      "version": 1
    }
  ]
}
```

The response remains `ScopePage`; no abbreviated ID-only response is introduced. Omitting `query`, or supplying an
empty or whitespace-only value, preserves the existing unfiltered behavior. No match is a successful empty page.

## List public Sources in a Scope

```http
GET /v1/scopes/git%3Agithub.com%2Foceanbase%2Fpowercontext/sources?limit=50
```

```json
{
  "items": [
    {
      "scope_id": "git:github.com/oceanbase/powercontext",
      "source_type": "content",
      "source_id": "src_example_0001",
      "content": {"decision": "Keep the public API stable."},
      "position": 1,
      "content_digest": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  ],
  "next_cursor": null
}
```

Each item uses the same `SourceRecord` representation as exact Source Get, including applicable optional fields such
as `receipt_identity`. The collection contains public `content` Sources, including readable `lineage_only` Sources.
It does not expose internal Source types or recursively include Sources from parent, referenced, or subject Scopes.

When `next_cursor` is non-null, the client repeats the request with that opaque value and the same `limit`. A caller
must encode the complete `scope_id` as one path segment.

# Reference-level explanation

## Scope query contract

`GET /v1/scopes` accepts one optional `query` string of at most 256 Unicode characters. The server trims leading and
trailing whitespace and then:

- matches only `scope_id`, never title, summary, references, Source content, or Artifact content;
- performs a case-sensitive contiguous literal substring match;
- treats `%`, `_`, `*`, backslash, and regular-expression characters literally;
- returns descriptors in the existing deterministic `scope_id` order;
- returns all matching descriptors without introducing pagination.

An omitted, empty, or whitespace-only query means no filter. Repeated `query` parameters are invalid. Preserving the
current unpaginated response avoids silently truncating existing clients; a future Scope pagination migration requires
its own compatibility design.

The operation retains `operationId: list_scopes`, `ScopePage`, and the existing `server.observe` authorization
requirement.

## Source collection contract

The new operation is:

| Field | Value |
| --- | --- |
| Method and path | `GET /v1/scopes/{scope_id}/sources` |
| operationId | `list_sources` |
| Authorization | Existing `path_scope_read_access` / `scope.read` |
| Response | `SourcePage` |

It accepts no request body. Query parameters are:

| Parameter | Rule |
| --- | --- |
| `limit` | Optional integer, default 50, range 1–100 |
| `cursor` | Optional opaque signed string, maximum 4096 characters |

`SourcePage` contains required `items: SourceRecord[]` and `next_cursor: string|null`. An existing Scope with no
public Sources returns `200` with an empty array and null cursor. A missing or unavailable Scope follows the existing
403/404 policy and is not represented as an empty collection.

Only Source types with an established public read contract enter the collection. This release therefore includes
only `content`, matching exact Source Get. Listing a `lineage_only` Content Source does not grant generation
eligibility; the existing `source_not_eligible` invariant remains unchanged.

## Ordering and snapshot boundary

Items are ordered by ascending `pc_sources.journal_position`, exposed as `position`. The first page records the
Scope's committed Source journal high watermark. Later pages read only:

```text
last_position < journal_position <= first_page_high_watermark
```

Sources appended after the first page are excluded from that traversal and become visible when the caller starts a
new traversal. This provides append isolation without retaining a database transaction across HTTP requests.

The signed cursor binds the operation, Scope ID, public Source type set, ordering, limit, caller identity, high
watermark, last returned position, and expiration. It cannot be reused across users, Scopes, endpoints, or limits.
Cursor lifetime uses the Server's existing configured record-cursor TTL. Tampered or context-mismatched cursors return
`400 invalid_cursor`; expired cursors return `410 cursor_expired`.

A page returns at most `limit` records and normally stays within a 4 MiB UTF-8 JSON content budget. The server may
end a page early at the byte budget. It never truncates an item; if the first item alone exceeds the budget, that one
complete item is returned subject to the existing Source size limits.

## Persistence and reuse

No schema migration is required:

| Behavior | Existing authority |
| --- | --- |
| Scope descriptors and ordering | `pc_scopes`, `pc_scope_context_references`, `pc_scope_external_references` |
| Source identity and content | `pc_sources` plus registered Source adapters |
| Source ordering and high watermark | `pc_sources.journal_position` and the existing Source repository |
| Cursor integrity | Existing HMAC cursor codec and deployment secret |
| Authorization | Existing Scope list and path Scope read resolvers |

Scope filtering extends `ScopeApplication` and its repository; it does not use the separate internal
`ScopeSummaryPage` assembled from Source and Artifact tables. Source List reuses the same adapter-aware conversion as
Create and exact Get and never returns raw persisted payloads.

The operation performs no writes: it does not copy Sources, advance `pc_source_cursors`, invoke a model, or create an
Artifact or Candidate.

## OpenAPI and clients

`openapi/powercontext.yaml` remains the source of truth. It gains the `query` parameter on `list_scopes`, the
`list_sources` operation, `ListScopesRequest`, `ListSourcesRequest`, and `SourcePage`. Generated Python and TypeScript
operation metadata is regenerated. The handwritten Python client keeps `list_scopes()` source-compatible by making
`query` optional and adds a typed `list_sources()` method.

## Errors

| Status | Meaning |
| --- | --- |
| 200 | Successful result, including an empty collection |
| 400 | Invalid, tampered, or context-mismatched Source cursor |
| 401 | Missing or invalid credentials |
| 403/404 | Existing authorization and resource visibility policy |
| 410 | Source cursor expired |
| 422 | Invalid query, path, limit, repeated parameter, or cursor syntax |
| 503 | Required persistence or runtime capability unavailable |
| 500 | Unexpected server failure using the existing error envelope |

# Drawbacks

Literal substring matching cannot generally use an ordinary B-tree prefix index, and the Scope endpoint remains
unbounded for compatibility. Returning complete Source content is more expensive than a summary collection. Snapshot
pagination and a byte budget add cursor and response assembly complexity.

# Rationale and alternatives

- A separate Scope Search endpoint was rejected because this is a filter over the same authorized collection with
  the same response type.
- `/sources/{source_type}` was rejected because callers discover Sources by owning Scope and the only public type is
  currently `content`.
- An unbounded Source response was rejected because large or numerous contents can exhaust memory and response
  limits.
- Summary-only Source items were rejected because they require N+1 exact Get requests and do not satisfy the need to
  retrieve all Source information.
- Exposing every internal Source type was rejected because those payloads lack a stable public schema and may contain
  implementation-only data.

# Prior art

[RFC 1437](1437_source_artifact_rest_api.md) defines Source identity, public Content Sources, exact Get, and
`lineage_only` behavior. This RFC adds collection discovery without changing those semantics. Existing Artifact and
Artifact Revision list operations provide the signed-cursor conventions reused here.

# Unresolved questions

None for this release. Additional public Source types, Scope pagination, metadata search, and export-oriented APIs
require separate designs.

# Future possibilities

Future RFCs may add compatible Scope pagination, title or summary search, Source type filters, summary projections,
or asynchronous exports. Each extension must preserve exact-Get authorization and avoid exposing internal payloads.
