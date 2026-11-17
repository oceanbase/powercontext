---
title: Query across Scopes
description: Resolve a bounded structural Scope range and search several authorized origins for one Artifact Family.
---

# Query across Scopes

Ordinary [Artifact search](search-artifacts.md) reads one Scope. A project often needs material that lives in several
Scopes it owns, or in a team library another owner maintains. This page covers the two supported pieces: resolving a
**bounded structural range**, and **querying several explicitly declared origins** for one Family while reporting how
much of the range was actually searched.

Neither piece grants access. Every origin is authorized separately against its current `scope.read`, and membership in a
range never lets a caller read that Scope's content.

## Resolve a structural range

`parent_scope_id` defines one single-parent Scope tree. `POST /v1/scopes/selection/resolve` already returns the
descriptors a selection names. Structural membership is those parent edges, and nothing else:

- A **Context Reference** from one Scope to another never adds the referenced Scope, so a project that references a
  team runbook does not thereby include the runbook in its range.
- Publication copies, Artifact lineage, and evidence links never expand a range. Use an
  `ArtifactAddress` when you mean one exact revision.
- `mode: "exact"` is the named Scopes only. `mode: "subtree"` is a root plus its structural descendants. `mode: "all"`
  is a whole-collection observation, not a structural range.

The runtime resolver additionally enforces explicit `max_nodes` and `max_depth` budgets. When a range does not fit its
budget the service **refuses** rather than returning a truncated set, so a caller never mistakes a partial membership
for a complete one. Resolution reads structural edges of the branch rather than every Scope, so the work and the
transferred rows follow the branch size; `parent_scope_id` has no dedicated index, so residual latency still grows
with the total number of stored Scopes. Narrow the root instead of raising the budget. A resolved range reports each member's metadata
`version` and one membership digest, which changes whenever a member is attached, moved, or updated — the basis a
lifecycle operation uses to detect a changed set before confirming an action.

## Search declared origins

Use `POST /v1/scopes/{scope_id}/artifacts/search:cross-scope`. The path Scope is the one the caller works in; each entry
in `scopes` is an origin.

```http
POST /v1/scopes/orders/artifacts/search:cross-scope
Content-Type: application/json

{
  "family": "experience",
  "payload": {"query": "release rollback", "limit": 10},
  "scopes": ["scp_interface", "scp_operations"],
  "coverage": "complete"
}
```

Each origin is authorized separately. Results are merged by **each origin's own rank**, interleaved with the declared
origin order as the stable tie break. A `retrieval_score` belongs to its own origin and is never comparable across
origins. Every hit carries a complete `ArtifactAddress`, so two Scopes that hold an Artifact with the same local ID
remain two distinct inputs.

`payload` is the selected Family's own search request, forwarded unchanged. This operation does not interpret it: the
Family validates it, owns its defaults, and reports its own errors.

## Budgets and coverage

| Field | Type and default | Meaning |
| --- | --- | --- |
| `limit` | Integer, default `10` | Maximum merged results across all origins. |
| `max_scopes` | Integer, default `8` | Largest origin list this request accepts. |
| `max_candidates` | Integer, default `64` | The per-origin window this operation injects into the Family, divided across the declared origins. It is a ceiling on the Family's effective window and never widens a declared or default `limit`. |
| `coverage` | `complete` or `partial`, default `complete` | Whether a failing required origin fails the whole query. |

`max_candidates` is only ever a ceiling, so it cannot turn a declared top-1 request into a wider one, and it never
replaces an invalid original value with a valid one. If the budget cannot cover the Family's effective window, the
query is **refused** instead of being silently under-searched.

It bounds the window handed to each origin, not the Family's internal work. A Family that reranks may examine more
candidates than that window, and coverage still reports the narrowing, so a narrowed search is never presented as
complete.

Because the budget is divided across origins, the usable origin count depends on the window you ask for. With the
defaults (`limit` 10, `max_candidates` 64) at most **six** origins can be searched completely; a 10-item window over two
origins needs `max_candidates` of at least 20, and seven or more origins are refused until you raise `max_candidates`,
lower the per-origin window, or accept `coverage: partial`.

The response states which origins answered:

| Result | Meaning |
| --- | --- |
| `coverage.complete: true`, `reason: complete` | Every declared origin was searched in full. |
| `reason: origin_failure` | At least one required origin was refused, unavailable, or missing; `failed_origins` names them. With `coverage: complete` the whole query fails instead. |
| `reason: candidate_budget` | An origin was searched under a narrowed window; `bounded_origins` names them, so the result is never presented as complete coverage. |

## Open one hit exactly

Opening a hit reads the exact revision in one of two ways.

`GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}` is the ordinary HTTP exact-revision
read. For a body owned by its Artifact it requires `artifact.read` on that logical Artifact and does not require
`scope.read` on the Scope holding it. Only the Scope-owned Topic Memory family is read from its Scope instead, so
`scope.read` is its whole requirement.

`open_hit` is the Runtime query object method, not a `PowerContextClient` HTTP method. It requires more, not less: it
keeps `scope.read` on the origin as a precondition and then requires `artifact.read` on the same exact Artifact. A
caller with a per-Artifact grant but no Scope binding can therefore read the revision over HTTP and will be refused by
`open_hit`. In both paths the families whose bodies are owned by their Artifact are `experience`, `skill`, `profile`,
`handoff`, `prompt` and `atomic-memory`; Topic Memory is Scope-owned.

Neither path falls back to the current head, so an earlier hit still names the revision that was found even after the
origin publishes a newer one. Reading the conclusion and reading the original evidence stay separate permissions:
being allowed to use a Scope does not let you read the Artifact bodies inside it, and holding one Artifact grant does
not let you search the Scope.

## Limits

- A restriction to one Artifact or revision (`restrict_to`) is refused, because the searchable projection indexes
  current heads only; candidates cannot be restricted before scoring.
- This surface reads; it does not generate, review, or publish anything, and it does not maintain a conclusion across
  Scopes.
- Cross-Scope search is available through HTTP and the Python SDK. It is not an MCP tool; MCP retains the exact-read
  tools for following up a hit.
