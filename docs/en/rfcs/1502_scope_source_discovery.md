+ Proposal Name: `scope_source_discovery`
+ Start Date: 2026-09-08
+ RFC PR: [oceanbase/powercontext#1502](https://github.com/oceanbase/powercontext/pull/1502)

# Summary

Extend `GET /v1/scopes` with explicit, SQL-backed Scope discovery and compatible pagination. A caller supplies the
substring in `query` and selects exactly one searchable field with `query_field`. Optional exact filters cover the
Scope parent, External Reference kind, and Binding integration and kind. Add
`GET /v1/scopes/{scope_id}/sources` to page through the public Sources owned by one Scope.

Scope discovery searches one of `scope_id`, `title`, `summary`, External Reference `value`, or Binding `external_id`.
The service persists application-normalized search projections and uses dialect-specific literal-contains expressions
so SQLite and OceanBase implement the same Unicode, case, and metacharacter behavior.

# Motivation

`ScopeApplication` now generates opaque `scp_...` identities. A caller usually knows a title, repository URL, or
workspace binding rather than a fragment of that random identity. Searching only `scope_id` therefore cannot discover
new Scopes from repository information.

A single query that silently searches every field is also unsuitable: callers cannot tell why a row matched, adding a
field changes existing results, and relationship fields have different authorization and query costs. Requiring
`query_field` makes the request and query plan stable while retaining one collection endpoint.

After selecting a Scope, callers can create a Source or retrieve one by complete identity, but cannot discover the
Sources already owned by the Scope. Source discovery is a read of the existing child collection and belongs on the
existing `/v1/scopes/{scope_id}/sources` resource.

# Guide-level explanation

## Discover Scopes

Suppose an opaque Scope has title `PowerContext` and a `repository` External Reference whose value is
`https://github.com/oceanbase/powercontext`. Search the title explicitly:

```http
GET /v1/scopes?query=powercontext&query_field=title&limit=50
```

```json
{
  "items": [
    {
      "scope_id": "scp_01hzy8m6yq8j3h7m3v5w2r9k1p",
      "title": "PowerContext",
      "summary": "PowerContext repository development",
      "parent_scope_id": null,
      "context_references": [],
      "external_references": [
        {
          "kind": "repository",
          "value": "https://github.com/oceanbase/powercontext"
        }
      ],
      "version": 1
    }
  ],
  "next_cursor": null
}
```

Search the repository reference instead:

```http
GET /v1/scopes?query=oceanbase%2Fpowercontext&query_field=external_reference_value&external_reference_kind=repository&limit=20
```

All supplied filters combine with AND. `query` examines only the field named by `query_field`. When the target is an
External Reference or Binding, its text and exact type filters must match the same relationship row.

An unparameterized `GET /v1/scopes` preserves the existing complete-list behavior. Supplying discovery filters,
`limit`, or `cursor` enables pagination. A non-null `next_cursor` is passed unchanged with the same query, field,
filters, and limit.

## List public Sources in a Scope

```http
GET /v1/scopes/scp_01hzy8m6yq8j3h7m3v5w2r9k1p/sources?limit=50
```

```json
{
  "items": [
    {
      "scope_id": "scp_01hzy8m6yq8j3h7m3v5w2r9k1p",
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

Each item uses the exact Source Get `SourceRecord`, including applicable optional fields such as `receipt_identity`.
The collection contains public `content` Sources, including readable `lineage_only` Sources. It excludes internal
Source types and Sources owned by parent, referenced, or subject Scopes.

# Reference-level explanation

## Scope discovery contract

| Parameter | Type | Required | Rule | Example |
| --- | --- | --- | --- | --- |
| `query` | string | Conditional | Supply with `query_field`; at most 256 Unicode characters | `powercontext` |
| `query_field` | string enum | Conditional | Supply with non-empty `query`; selects one field | `title` |
| `parent_scope_id` | string | No | Exact direct-parent match; not recursive | `scp_01hzy8m6yq8j3h7m3v5w2r9k1p` |
| `external_reference_kind` | string | No | Scope has a matching External Reference kind | `repository` |
| `binding_integration` | string | No | Scope has a matching Binding integration | `codex` |
| `binding_kind` | string | No | Scope has a matching Binding kind | `workspace` |
| `limit` | integer | No | Paginated default 50; range 1–100 | `50` |
| `cursor` | string | No | Omit initially; then pass the opaque server value | `eyJ2ZXJzaW9uIjoxLC4uLn0` |

`query_field` is a closed enum:

- `scope_id`
- `title`
- `summary`
- `external_reference_value`
- `binding_external_id`

`query` and `query_field` must appear together. A missing partner, unknown enum value, repeated parameter, or
`query_field` paired with an empty or whitespace-only query returns 422. This release does not define `any` or
multi-field OR search.

The server trims the query and performs a case-insensitive contiguous literal substring match after NFKC and Unicode
casefold normalization. It does not tokenize, spell-correct, or perform semantic search. `%`, `_`, `*`, backslash, and
regular-expression characters remain literal.

Text matching and exact filters combine with AND. External Reference and Binding predicates use correlated `EXISTS`
subqueries so one-to-many relationships cannot duplicate Scopes. If `query_field=external_reference_value` and
`external_reference_kind` is present, one External Reference must satisfy both. If
`query_field=binding_external_id`, one Binding must satisfy the text plus any `binding_integration` and `binding_kind`.

## Scope pagination and compatibility

Scopes use ascending `scope_id` keyset order. No-query-parameter `GET /v1/scopes` preserves the current unbounded
result. Pagination mode starts when the request supplies a valid query pair, an exact filter, `limit`, or `cursor`.
Its default limit is 50 and maximum is 100.

`ScopePage` gains optional `next_cursor: string|null`. Paginated responses always include it; the legacy full-list
response may omit it or return null. The signed cursor binds the operation, stable caller identity, normalized query,
query field, exact filters, limit, order, last Scope ID, and expiration. It uses the existing deployment cursor secret
and record-cursor TTL. It cannot cross callers, fields, filters, limits, or endpoints. Invalid or mismatched cursors
return `400 invalid_cursor`; expired cursors return `410 cursor_expired`.

Opaque Scope IDs are random and searchable metadata may change. This release provides keyset consistency rather than
a cross-request snapshot. With stable data and authorization, each matching Scope appears exactly once. Concurrent
creation or metadata changes can alter later pages; callers requiring a fresh directory restart at the first page.

## SQL implementation and storage compatibility

The Scope repository accepts the query pair, exact filters, keyset boundary, and `limit + 1`. Filtering, ordering, and
limiting occur in SQL before descriptors and relationships are loaded. Python must not load an unbounded Scope list
and then filter or page it.

The application defines one normalization function: NFKC followed by Unicode `casefold()`, without tokenization or
punctuation removal. Write paths persist its output and a startup migration backfills existing rows:

| Table | Source field | Search projection |
| --- | --- | --- |
| `pc_scopes` | `scope_id` | `scope_id_search` |
| `pc_scopes` | `title` | `title_search` |
| `pc_scopes` | `summary` | `summary_search` |
| `pc_scope_external_references` | `value` | `value_search` |
| `pc_scope_bindings` | `external_id` | `external_id_search` |

Original fields remain authoritative and the projections are not public. Application-generated projections avoid
database generated-column, `LOWER()`, and default-collation differences.

The SQL dialect helper emits `instr(normalized_column, :query) > 0` for SQLite and
`locate(:query, normalized_column) > 0` for OceanBase/MySQL mode. Both treat SQL wildcard characters literally. The
query remains a bound parameter. If LIKE is used instead, one shared helper must escape metacharacters and prove
equivalent behavior in both storage contract suites.

The repository builds only the predicate selected by `query_field`; it does not build a fixed OR across all
projections. Main-table fields use a direct predicate. Relationship fields use correlated `EXISTS`, with exact kind
or integration constraints in that same subquery. After selecting one page of Scope rows, the existing bulk loader
assembles Context References and External References without N+1 queries.

Literal contains search generally cannot use a normal B-tree prefix index. SQL pushdown still bounds database-to-app
transfer and application memory. If query plans justify it, add a cross-storage Binding index beginning with
`scope_id`; do not add speculative indexes.

## Source collection contract

| Field | Value |
| --- | --- |
| Method and path | `GET /v1/scopes/{scope_id}/sources` |
| operationId | `list_sources` |
| Authorization | Existing `path_scope_read_access` / `scope.read` |
| Response | `SourcePage` |

Query parameters are optional `limit` (default 50, range 1–100) and an opaque signed `cursor`. `SourcePage` contains
required `items: SourceRecord[]` and `next_cursor: string|null`. An existing Scope with no public Sources returns an
empty array and null cursor. Missing or unavailable Scopes follow the existing 403/404 policy.

Only Source types with a public read contract enter the collection. This release includes only `content`, matching
exact Source Get. Listing a `lineage_only` Source does not grant generation eligibility.

Sources are ordered by ascending journal position. The first page records the committed journal high watermark;
later pages read `last_position < journal_position <= high_watermark`. Appends are excluded until a new traversal.
The cursor binds operation, Scope, public types, order, limit, caller, high watermark, last position, and expiration.

A page returns at most `limit` records and normally remains within a 4 MiB UTF-8 JSON content budget. It never
truncates a Source; one oversized first item may be returned whole within existing Source limits.

## Authorization and errors

Scope discovery retains `server.observe`. Authorization completes before metadata or Binding search, and the response
contains ScopeDescriptor rather than Binding contents. Source List retains `scope.read` and exact-Get visibility.
Every page reauthorizes, so a cursor cannot bypass authorization changes.

| Status | Meaning |
| --- | --- |
| 200 | Success, including an empty collection |
| 400 | Invalid, tampered, or context-mismatched cursor |
| 401 | Missing or invalid credentials |
| 403/404 | Existing authorization and visibility policy |
| 410 | Cursor expired |
| 422 | Invalid or unpaired query field, filter, limit, path, or repeated parameter |
| 503 | Required persistence or runtime capability unavailable |
| 500 | Unexpected failure using the existing error envelope |

## OpenAPI and clients

`openapi/powercontext.yaml` remains authoritative. `list_scopes` gains optional `query`, `query_field`,
`parent_scope_id`, `external_reference_kind`, `binding_integration`, `binding_kind`, `limit`, and `cursor` parameters.
`query_field` is a closed enum and the request model enforces its pairing with `query`. `ScopePage` gains optional
`next_cursor`. The contract also defines `list_sources`, `ListSourcesRequest`, and `SourcePage`.

Generated Python and TypeScript bindings are regenerated. The handwritten Python client retains no-argument
`list_scopes()` and exposes the discovery options. No operation writes Sources, advances consumer cursors, invokes a
model, or creates an Artifact or Candidate.

# Drawbacks

Normalized projections and their startup migration add storage and write-path maintenance. Literal substring search
can still scan rows. Legacy unparameterized Scope listing remains unbounded. Scope keyset pagination is not a
cross-request snapshot. Returning complete Source content costs more than a summary collection.

# Rationale and alternatives

- Searching only `scope_id` was rejected because current opaque IDs do not contain repository identity.
- Searching every field with one query was rejected because match provenance and behavior are unstable.
- One query parameter per field was rejected because it expands the surface and needs extra multi-field AND/OR rules;
  one query plus one required field serves the current single-field use cases clearly.
- Python filtering was rejected because it loads the complete collection and cannot correctly push down pagination.
- Database `LOWER()` or default collation was rejected because SQLite and OceanBase Unicode behavior differs.
- Joining relationship tables was rejected because it duplicates Scopes and destabilizes page boundaries.
- A separate Scope Search route was rejected because discovery filters the same authorized collection and response.

# Prior art

[RFC 1437](1437_source_artifact_rest_api.md) defines Source identity, public Content Sources, exact Get, and
`lineage_only` behavior. This RFC adds Scope and Source collection discovery without changing those semantics.

# Unresolved questions

None for this release. Multi-field relevance search, spelling correction, full-text indexing, and exposing Binding
identity require separate designs and finer authorization review.

# Future possibilities

Future RFCs may migrate the legacy unbounded Scope list, add root/subtree or updated-time filters, add exact External
Reference value matching, or introduce a dedicated full-text index. Each extension must preserve cursor context,
authorization, and SQLite/OceanBase semantics.
