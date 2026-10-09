---
title: Fusion algorithms and parameters
description: Configure Topic Memory RRF ranks, weights, and normalized retrieval scores.
---

# Fusion algorithms and parameters

The implemented fusion method is `rrf` (Reciprocal Rank Fusion). The unified Topic Memory search exposes it through
`fusion`; Experience and Skill expose one text channel and do not accept fusion parameters. See
[Search Artifacts](search-artifacts.md) for Family support, admission, modes, and request limits.

## RRF calculation

RRF combines the ranks of candidates admitted by each enabled channel. A better rank contributes more. The algorithm
uses ranks rather than mixing BM25 scores and vector distances, whose units and directions differ.

For candidate `x`, let `rank_c(x)` be its positive, one-based rank in channel `c`, `w_c` the channel weight, and `K`
the rank constant:

```text
raw_rrf(x) = Σ [w_c / (K + rank_c(x))]   over channels containing x
upper     = Σ [w_c / (K + 1)]           over every enabled channel
retrieval(x) = raw_rrf(x) / upper
```

`retrieval` is finite and normalized to `[0, 1]`. It reaches `1` when a candidate ranks first in every positive-weight
enabled channel. An enabled channel with no admitted candidates still contributes to `upper`. A missing candidate
contributes nothing to that channel's numerator; it is not assigned a zero raw score.

Ranks retain gaps after duplicate identities are removed within a channel. For example, ranks `1` and `3` remain `1`
and `3`; they are not renumbered. Candidates found only in zero-weight channels are excluded. After fusion, Topic
applies `min_score` and the requested limit. Equal fused scores use Artifact ID UTF-8 order, then descending Revision.

## Parameters

| Field | Type | Default | Valid values |
| --- | --- | --- | --- |
| `fusion.method` | String | `rrf` when `fusion` is omitted | Only `rrf` |
| `fusion.params` | JSON object | `{}` | Only `rank_constant` and `weights` |
| `fusion.params.rank_constant` | Integer | `60` | Positive integer |
| `fusion.params.weights` | Object from channel name to number | `{}` | Finite, non-negative numbers; enabled channels must have positive total weight |

`rank_constant` is the only location for `K`; a top-level `K` or `rank_constant` is invalid. Numeric strings, booleans,
explicit `null`, non-finite values, unknown parameter keys, and negative weights are rejected.

Weight overrides are partial: every enabled channel omitted from `weights` keeps weight `1`. Weight `0` removes a
channel's contribution to both the numerator and denominator. Scaling all weights by the same positive factor leaves
the normalized score unchanged. A larger `K` reduces the relative advantage of nearby top ranks; a smaller `K` makes
their rank differences matter more.

Topic permits only the channel names enabled by the chosen mode:

| Mode | Legal weight keys |
| --- | --- |
| `text` | `topic_fts`, `detail_fts` |
| `vector` | `topic_vector`, `detail_vector` |
| `hybrid` | `topic_fts`, `topic_vector`, `detail_fts`, `detail_vector` |

An inactive or unknown key is rejected even if its weight is `0`. With omitted mode, supplying any vector weight key
requires vector capability and prevents the default text fallback. Admission and deployment checks still apply;
changing a weight cannot enable an unavailable retrieval channel.

## Example: text channels with different weights

```http
POST /v1/scopes/project-a/artifacts/topic-memory/search
Content-Type: application/json

{
  "query": "release rollback",
  "mode": "text",
  "fusion": {
    "method": "rrf",
    "params": {
      "rank_constant": 60,
      "weights": {"topic_fts": 2}
    }
  },
  "include_scores": true
}
```

`topic_fts` has weight `2`; omitted `detail_fts` retains weight `1`. The normalization denominator is `3 / 61`, even
when detail search returns nothing. A candidate first in `topic_fts` and absent from detail has score `2 / 3`.

For two equal-weight channels with `K = 60`:

| Candidate ranks | Raw RRF | Normalized score |
| --- | --- | --- |
| First in both channels | `2 / 61` | `1` |
| First in one, absent from the other | `1 / 61` | `0.5` |
| First in one, other channel empty | `1 / 61` | `0.5` |
| Third in one, absent from the other | `1 / 63` | `61 / 126` |

An empty enabled channel therefore remains visible in the score's interpretation. Scores from different enabled
channel sets or weight plans should not be treated as the same relevance calibration.

## Scores and current boundaries

`scores.retrieval` exposes normalized RRF. `scores.channels` exposes original FTS scores or L2 distances with a metric
and direction; it does not expose the internal raw RRF sum. See [Read the scores](search-artifacts.md#read-the-scores).

Only RRF is available. `weighted_score`, reranking, and non-empty Family filters are unsupported. Fusion does not relax
channel admission or fill results below `min_score`. The original Topic-specific search keeps its default RRF behavior
and legacy `0`–`100` score scale; advanced fusion controls are available through the unified route.
