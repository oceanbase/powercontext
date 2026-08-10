- Proposal Name: `scoped_statistics_and_usage`
- Start Date: 2026-08-04
- RFC PR: [oceanbase/powercontext#72](https://github.com/oceanbase/powercontext/pull/72)
- Tracking Issue: [oceanbase/powercontext#58](https://github.com/oceanbase/powercontext/issues/58)

# Summary

PowerContext will expose one scoped statistics query for current content inventory, provider-reported model usage, and
estimated recall token reduction. HTTP, the Python Client, and the CLI will return the same result.

# Motivation

Callers currently need several list operations or logs to answer basic product questions: how much content a scope owns,
what is awaiting review, whether Sources have reached Memory, how many model tokens PowerContext used, and whether
prepared context is smaller than its Source evidence. Operational metrics do not provide a stable product contract for
these questions.

A generic `entry` count is insufficient. Memory has entries, while Experience, Handoff, Skill, and future products are
Artifact families. Source, Candidate, Artifact, and Memory entry counts therefore remain distinct.

# Guide-level explanation

```text
Statistics(scope, period) = Inventory(scope, query_time)
                          + ModelUsage(scope, period)
                          + RecallTokens(scope, period, estimator)
```

Inventory is a current snapshot. Model usage and recall tokens cover `today`, `7d`, or `30d` in UTC. A dashboard can
project the response into inventory cards, review status, model usage, recall comparison coverage, and daily trends.

```text
GET /v1/stats?scope_id={scope_id}&period={period}
operationId: get_stats
```

```bash
powercontext stats --scope-id project:powercontext --period 30d
```

The CLI calls the configured Server. The default period is `30d`. `--json` returns the public response. Human-readable
output renders unavailable provider token values as `unknown`.

# Reference-level explanation

## Application boundary

The built-in Runtime owns one concrete scoped application surface:

```python
class ScopedStatisticsApplication:
    async def overview(self, *, period: StatisticsPeriod) -> Statistics: ...

    async def record_model_usage(
        self,
        purpose: ModelUsagePurpose,
        operation: ModelUsageOperation,
        usage: InferenceUsage,
        /,
    ) -> None: ...

    async def record_recall(self, measurement: RecallTokenMeasurement, /) -> None: ...
```

This is not a generic instrumentation SPI. The design does not introduce `UsageRecorder`, `UsageOperation`,
`UsageFact`, `UsageExporter`, or `UsageQuery` protocols.

## HTTP contract

```yaml
GetStatisticsRequest:
  scope_id: string
  period: today | 7d | 30d = 30d

Statistics:
  scope_id: string
  as_of: datetime
  inventory:
    sources: { total, memory_processed, memory_pending }
    artifacts: { total, by_family[] }
    candidates: { total, pending, approved, rejected, by_family[] }
    memory:
      entries: { total, active, inactive, by_kind[] }
  usage:
    period: { preset, start_date, end_date, timezone }
    totals: { generation, embedding }
    by_purpose[]: { purpose, generation, embedding }
    daily[]: { date, generation, embedding, by_purpose[] }
  recall:
    period: { preset, start_date, end_date, timezone }
    estimator: { estimator_id, version }
    totals: { preparations, ready_preparations, comparable_preparations,
              baseline_tokens, recalled_tokens, token_reduction }
    daily[]: { date, preparations, ready_preparations, comparable_preparations,
               baseline_tokens, recalled_tokens, token_reduction }
```

Each family row contains `family` and `total`. Candidate family rows also contain the three status totals. Each Memory
kind row contains `kind`, `total`, `active`, and `inactive`. Memory kind is an open string, so `by_kind` reports observed
values rather than a fixed enumeration. Each generation or embedding usage value contains `requests`, `input_tokens`,
and `output_tokens`. Purpose is one of `memory_extraction`, `memory_indexing`, `memory_recall`,
`experience_generation`, `skill_generation`, or `handoff_generation`.

`as_of` is the RFC 3339 Server time captured when the query starts. The resolved period contains inclusive UTC dates and
`timezone: UTC`. `daily` includes every date in the period, including zero-activity dates, and is limited to thirty rows.

Objects use `additionalProperties: false`. Counts are non-negative. Family and kind rows sort by UTF-8 byte order, and
daily rows sort by date.

The endpoint inherits the Server's existing optional bearer authentication and `401` response. Responses use
`Cache-Control: no-store` and the existing `X-PowerContext-Request-ID` header. Callers and the CLI do not submit or
override the request ID. The Python Client retains it on structured failures.

## Inventory semantics

- `sources.total` is the Source journal high watermark. `memory_processed` is the Memory Source cursor, and
  `memory_pending = total - memory_processed`. Reading statistics never advances the cursor.
- Artifact totals count current `pc_artifact_heads`, grouped by family. Historical revisions do not count.
- Candidate totals count current `pc_artifact_candidate_heads`, grouped by status and family. Candidate history does not
  count. An approved Candidate and its resulting Artifact remain separate objects.
- Memory entry totals read the current Memory manifest. Historical entry versions do not count. At the top level and for
  each kind, `total = active + inactive`.

Missing inventory objects produce zero counts and empty breakdown arrays.

## Model usage semantics

PowerContext-owned generation and embedding paths record `InferenceUsage` once per successful capability result. A
bucket with no matching operation returns zero requests and tokens. If calls occurred but the provider omitted a token
field, that field is `null`. A period total is `null` when any included non-empty daily bucket is incomplete.

Usage recording is best-effort and must not change the generation or embedding result. The implementation must not
retry a recording operation when doing so could double-count.

## Recall token semantics

Recall accounting runs after final selection and truncation in `prepare_context`. The baseline is the primary text of
the exact, transitively referenced Sources for every selected Memory entry or Artifact. Source references are
de-duplicated and sorted before estimation. The recalled value is the complete `PreparedContext.content`, including
the trust envelope and citations.

A preparation is comparable only when it is ready and every selected material resolves to at least one supported
Source. Empty results and material without Source lineage still increase `preparations` and `ready_preparations` as
applicable, but they do not contribute token values. Dashboards must show comparison coverage as
`comparable_preparations / preparations` with token reduction.

The initial estimator is deterministic and has no model or network dependency. It approximates one token per four
ASCII characters and one token per non-ASCII character, rounding up for each complete text. Aggregates are partitioned
by `estimator_id` and `version`. `token_reduction = baseline_tokens - recalled_tokens` is signed; a negative value
reports context overhead instead of hiding it.

Recall estimation and recording are best-effort and must not change the prepared context result.

## Persistence

```text
pc_model_usage_daily
  (scope_id, usage_date, purpose, operation) -> requests, input_tokens, output_tokens,
                                                input_complete, output_complete

pc_recall_token_daily
  (scope_id, usage_date, estimator_id, estimator_version)
    -> preparations, ready_preparations, comparable_preparations,
       baseline_tokens, recalled_tokens
```

SQLite and OceanBase use atomic incremental upserts. Reads calculate totals and zero-filled daily rows. The tables store
no content, object identity, request ID, trace ID, credential, or user-defined label, and are deleted with the scope.

The statistics query reads every section in one database read transaction and does not return partial results.

# Drawbacks

- Best-effort model-usage recording can undercount and is unsuitable for billing.
- Character-based token estimates are directional and cannot reproduce provider billing.
- Token reduction is meaningful only with its comparison coverage.
- Daily aggregates cannot provide per-operation provenance or arbitrary time ranges.
- The combined query costs more than reading one counter.

# Rationale and alternatives

- Generic Entry statistics erase Artifact-family and review-lifecycle boundaries.
- A generic event recorder requires event identity, retention, idempotency, and query semantics that this feature does
  not need.
- Operational metrics are not authoritative inventory or user-queryable usage storage.
- Per-operation rows add privacy, retention, and pagination requirements without helping the initial dashboard.
- Separate inventory and usage endpoints make the dashboard and CLI coordinate two otherwise related reads.
- An unbounded `all` period would make daily output unbounded.
- `tokens_saved` implies verified provider savings. The contract uses signed `token_reduction` and identifies the
  estimator instead.

# Prior art

Mem0 and Cognee present current totals as dashboard cards. Supermemory returns period totals and time buckets. Letta and
Pydantic AI preserve provider-reported usage. BentoML separates product analytics from operational metrics. PowerContext
applies these boundaries to its Source, Candidate, and Artifact lifecycles.

# Unresolved questions

- Whether `tokens_saved` should become a derived dashboard label remains unresolved. It must not imply
  provider-verified or billable savings.
- Dashboard implementation, billing, arbitrary time ranges, per-operation activity, model or family usage breakdowns,
  MCP projection, and historical backfill remain out of scope.

# Future possibilities

- Add lifetime totals, hourly buckets, or previous-period comparisons when a product view requires them.
- Add fixed model, provider, or Artifact-family breakdowns.
- Add deployment-specific tokenizers without combining their aggregates with the character estimator.
- Link aggregates to a separately defined activity model without changing the statistics fields.
