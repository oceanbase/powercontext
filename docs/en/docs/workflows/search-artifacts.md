---
title: Search Artifacts
description: Search supported Artifact families within a Scope and interpret retrieval scores.
---

# Search Artifacts

Use the unified search route to find relevant current Artifact heads in one Scope. Each result contains the exact
`family`, `artifact_id`, and `revision`, complete family-specific `content`, and `lineage`. Search does not enumerate
every Artifact or search across Scopes. Use [Manage Artifacts](artifacts.md) for lists and exact historical reads;
use [Prepare context text](prepare-context-text.md) when an Agent needs bounded context instead of complete content.

## Send a search request

The caller needs `scope.read` access. Put the Scope and Family in the path, and the search controls in the body.
Identity, access, and audit context come from server authentication and cannot be supplied in the request body:

```http
POST /v1/scopes/project-a/artifacts/experience/search
Content-Type: application/json

{
  "query": "release rollback",
  "limit": 5,
  "include_scores": true
}
```

Use the Server URL and credentials from your trusted environment. Include `Authorization: Bearer <token>` when
Access is enabled. The response is `{"results": [...]}`; an empty `results` array is a successful search with no
admitted result. Results retain their actual content fields and exact revision lineage.

## Family support

The built-in deployment currently exposes these capabilities through this route:

| Family | Unified search | Modes and default | Searchable content |
| --- | --- | --- | --- |
| `experience` | Supported | `text`; omitted mode selects text | Approved, active Experience heads |
| `skill` | Supported | `text`; omitted mode selects text | Approved, active managed Skill heads; external Skill catalogs are excluded |
| `topic-memory` | Supported | `text`, `vector`, `hybrid`; default is hybrid with configured Embedding, otherwise text | Current Topic Memory heads, including title/summary and detail channels |
| `memory` | Unsupported | Existing Memory search remains available | See [Memory and context](memory-and-context.md) |
| `profile` | Unsupported | No relevance search through this route | See [Use Profiles](use-profiles.md) |
| `handoff` | Unsupported | No relevance search through this route | See [Memory and Handoff](memory-and-handoff.md) |
| `prompt` | Unsupported | No relevance search through this route | See [Manage Prompts](manage-prompts.md) |

Sources are evidence and Tags are metadata; neither is an Artifact Family for this route. The deployment must have
the selected Family's retrieval index enabled. Topic vector and hybrid modes also require a compatible configured
Embedding model and index Profile. See [Configure vector search](configure-vector-search.md) for deployment settings.

## Common controls and limits

| Field | Type and default | Supported behavior |
| --- | --- | --- |
| `query` | Required non-empty string | Surrounding whitespace is trimmed. Experience and Topic allow at most 8192 characters; Skill allows 2000. |
| `limit` | Integer, default `10` | Experience and Skill: `1`–`200`; Topic: `1`–`20`. This is a maximum, not a promise to fill the result. |
| `mode` | Optional string | Select one of the Family's supported modes, or omit it to use its default. |
| `filters` | Object, default `{}` | Only an empty object is supported by these three Families. |
| `admission` | Optional object | Controls whether a retrieved candidate may contribute; fields are described below. |
| `min_score` | Optional finite number in `[0, 1]` | Keeps only results whose normalized retrieval score is at least this value. Omitted means no score threshold. |
| `include_scores` | Boolean, default `false` | Adds retrieval and available channel scores to each result. |
| `fusion` | Optional object | Only Topic exposes `rrf`; see [Fusion algorithms and parameters](search-fusion.md). |

Omit optional fields to use their defaults. Explicit `null`, unknown fields, numeric strings, and booleans in numeric
fields are rejected. `limit` must be an integer; `include_scores` must be a JSON boolean. Non-empty `filters`, `rerank`,
and the `weighted_score` fusion method are unsupported. Experience and Skill reject `fusion` entirely.

Thresholds are applied after admission and score calculation, before the final limit. The search does not add lower
scoring candidates to fill a result after `min_score` removes hits. Enabling scores changes only response metadata;
it does not change candidate admission, identities, ordering, or thresholds.

## Experience and Skill text search

Both Families search one `text` channel. Their admission fields are:

| Field | Default | Range |
| --- | --- | --- |
| `admission.lexical_coverage` | `0.25` | Finite number in `[0, 1]` |
| `admission.lexical_min_matched_terms` | `2` | Integer at least `1` |

Let `n` be the number of distinct analyzed query terms. A query with at most two terms requires one matching term.
Longer queries require at least `max(lexical_min_matched_terms, ceil(lexical_coverage × n))` matching terms. This admission
check precedes scoring and truncation. Unapproved or inactive heads cannot enter the result.

Their normalized score is `relevance / (1 + relevance)`, where relevance is the negated SQLite BM25 raw score or the
OceanBase MATCH raw score. Both backends produce scores in `[0, 1]`, but their scales differ. Choose `min_score` from
observed results for your backend and workload; there is no shared recommended threshold across backends.

## Topic Memory controls

Topic has four named channels:

| Public mode | Enabled channels |
| --- | --- |
| `text` | `topic_fts`, `detail_fts` |
| `vector` | `topic_vector`, `detail_vector` |
| `hybrid` | All four channels |

Text searches title/summary and detail separately. Vector searches use the deployment's Embedding Profile. Text and
hybrid queries allow at most 64 distinct analyzed terms; vector-only queries use the 8192-character limit.

Topic supports the two lexical admission fields above and `admission.min_semantic_similarity`, a finite number in
`[-1, 1]` with default `0.3`. Each lexical channel applies the matching-term rule independently. Each vector channel
requires `clip(1 - distance² / 2, -1, 1) >= min_semantic_similarity`, using the normalized vectors' L2 distance. A failure
in one channel removes that channel's contribution, while another admitted channel may still retain the topic.

Topic combines admitted rankings with RRF. Omitted `fusion` uses `rrf`, rank constant `60`, and weight `1` per enabled
channel. The normalized RRF score is described in [Fusion algorithms and parameters](search-fusion.md).

```http
POST /v1/scopes/project-a/artifacts/topic-memory/search
Content-Type: application/json

{
  "query": "release rollback",
  "mode": "hybrid",
  "limit": 5,
  "admission": {"lexical_coverage": 0.5},
  "fusion": {
    "method": "rrf",
    "params": {
      "rank_constant": 60,
      "weights": {"topic_fts": 2, "detail_vector": 0.5}
    }
  },
  "include_scores": true
}
```

Parameter combinations are validated even when the Scope contains no topics:

- `text` rejects an explicitly supplied semantic admission field and any vector weight key, including weight `0`.
- `vector` rejects explicitly supplied lexical admission fields and any FTS weight key, including weight `0`.
- A weight key must name an enabled channel. Unknown channels and an enabled set with total weight `0` are rejected.
- Explicit `vector` or `hybrid`, an explicit semantic threshold, or any vector weight key requires vector capability.
  An explicitly supplied value equal to its default still carries this requirement.

With omitted mode, Topic uses hybrid when Embedding is configured and text otherwise. If the default hybrid request
has no explicit vector requirement, a temporarily unavailable or timed-out Embedding call may fall back to text,
provided the FTS channels have positive total weight. Explicit vector/hybrid requests and explicit vector requirements
never fall back. If both FTS weights are `0`, an Embedding failure remains a service failure. Invalid vectors,
incompatible Profiles, and storage failures do not trigger text fallback.

## Read the scores

With `include_scores: true`, each result includes a `scores` object. This example shows only that field:

```json
{
  "scores": {
    "retrieval": 0.5,
    "channels": {
      "topic_fts": {
        "raw": -0.000004,
        "metric": "sqlite_bm25",
        "higher_is_better": false
      }
    }
  }
}
```

`retrieval` is the normalized score in `[0, 1]` used for ordering and `min_score`. `channels` contains the actual raw
scores of channels that admitted this result:

| Metric | Meaning | Better direction |
| --- | --- | --- |
| `sqlite_bm25` | Signed SQLite FTS BM25 score | Lower; matching scores are negative |
| `oceanbase_match` | OceanBase MATCH relevance | Higher |
| `l2_distance` | Topic vector L2 distance | Lower |

Experience and Skill use the `text` channel name. Topic uses the four names above. Missing channels are omitted,
with no fabricated zero. An admitted zero-weight Topic channel can still appear in metadata when another channel
selects the same topic. Detail raw scores belong to the representative detail chunk selected by that channel.

Raw values are backend and query dependent; they are not a universal similarity scale across queries or backends.
The Topic channel raw values are FTS scores or vector distances, not RRF contributions. Scores belong to this search
response and do not alter persisted Artifact content or lineage. With `include_scores: false`, `scores` is omitted.

## Failures and existing interfaces

An unknown Family returns `404`. A known Family without unified search, an unavailable configured search capability,
or an unsupported parameter returns `422`. Invalid requests report the parameter path; changing the limit or using
an empty Scope does not bypass validation. Model timeout/unavailability returns a service error when fallback is
not permitted (`503`). Implementation and storage failures remain failures, rather than successful empty results.

The existing Memory and Topic HTTP search routes, Skill Library search, and existing SDK/internal Experience recall
keep their request and response contracts. In particular, `POST /v1/topic-memory/search` still chooses the deployment
default and returns summary hits with its legacy score scale of `0`–`100`; it does not expose these advanced controls.
The unified Topic route returns complete Artifact content and uses retrieval scores in `[0, 1]`. See
[Use Topic Memory](topic-memory.md) for the dedicated summary search and exact detail-read workflow.
