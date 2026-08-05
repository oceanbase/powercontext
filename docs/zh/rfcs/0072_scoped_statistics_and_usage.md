- Proposal Name: `scoped_statistics_and_usage`
- Start Date: 2026-08-04
- RFC PR: [oceanbase/powercontext#72](https://github.com/oceanbase/powercontext/pull/72)
- Tracking Issue: [oceanbase/powercontext#58](https://github.com/oceanbase/powercontext/issues/58)

# Summary

PowerContext 将提供一个 scoped statistics query，用于查询当前 content inventory、provider-reported model usage 和
estimated recall token reduction。HTTP、Python Client 和 CLI 返回同一结果。

# Motivation

调用方目前需要执行多个 list operation 或检查日志，才能回答几个基本产品问题：scope 中有多少内容，哪些内容等待审查，
Source 是否已经进入 Memory、PowerContext 使用了多少 model token，以及 prepared context 是否小于对应的 Source
evidence。Operational metrics 无法为这些问题提供稳定的产品 contract。

通用 `entry` count 也不准确。Memory 包含 entry，而 Experience、Handoff、Skill 和未来产物属于 Artifact family。因此，
Source、Candidate、Artifact 和 Memory entry 必须分别统计。

# Guide-level explanation

```text
Statistics(scope, period) = Inventory(scope, query_time)
                          + ModelUsage(scope, period)
                          + RecallTokens(scope, period, estimator)
```

Inventory 是当前状态的 snapshot。Model usage 和 recall token 按 UTC 统计 `today`、`7d` 或 `30d`。Dashboard 可以将
response 映射为 inventory card、review status、model usage、recall comparison coverage 和 daily trend。

```text
GET /v1/stats?scope_id={scope_id}&period={period}
operationId: get_stats
```

```bash
powercontext stats --scope-id project:powercontext --period 30d
```

CLI 调用已配置的 Server。默认 period 是 `30d`。`--json` 返回 public response。Human-readable output 将
provider 未提供的 token value 显示为 `unknown`。

# Reference-level explanation

## Application boundary

Builtin Runtime 提供一个具体的 scoped application interface：

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

该 interface 不是通用 instrumentation SPI。本设计不引入 `UsageRecorder`、`UsageOperation`、`UsageFact`、
`UsageExporter` 或 `UsageQuery` protocol。

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

每个 family row 包含 `family` 和 `total`。Candidate family row 还包含三个 status total。每个 Memory kind row 包含
`kind`、`total`、`active` 和 `inactive`。Memory kind 是开放字符串，因此 `by_kind` 返回实际存在的值，而不是固定枚举。
Generation 和 embedding usage 都包含 `requests`、`input_tokens` 和 `output_tokens`。Purpose 为
`memory_extraction`、`memory_indexing`、`memory_recall`、`experience_generation`、`skill_generation` 或
`handoff_generation`。

`as_of` 是 query 开始时捕获的 RFC 3339 Server 时间。Resolved period 包含首尾日期均计入的 UTC date 和
`timezone: UTC`。`daily` 包含 period 内的每一天，包括没有 activity 的日期，最多返回三十行。

Object 使用 `additionalProperties: false`。Count 都是非负数。Family 和 kind row 按 UTF-8 byte order 排序，daily row
按日期排序。

Endpoint 复用 Server 现有的可选 bearer authentication 和 `401` response。Response 使用
`Cache-Control: no-store` 和现有的 `X-PowerContext-Request-ID` header。Caller 和 CLI 不提交或覆盖
request ID。Python Client 在 structured failure 中保留该值。

## Inventory semantics

- `sources.total` 是 Source journal high watermark。`memory_processed` 是 Memory Source cursor，且
  `memory_pending = total - memory_processed`。读取 statistics 不会推进 cursor。
- Artifact total 统计当前 `pc_artifact_heads`，并按 family 分组。历史 revision 不计入。
- Candidate total 统计当前 `pc_artifact_candidate_heads`，并按 status 和 family 分组。Candidate history 不计入。
  Approved Candidate 与它产生的 Artifact 是两个独立对象。
- Memory entry total 读取当前 Memory manifest。历史 entry version 不计入。顶层和每个 kind 都满足
  `total = active + inactive`。

缺少 inventory object 时返回 zero count 和 empty breakdown array。

## Model usage semantics

PowerContext-owned generation 和 embedding path 对每个成功的 capability result 记录一次 `InferenceUsage`。Bucket 没有
匹配 operation 时，request 和 token 都返回零。已经发生 call 但 provider 缺少某个 token field 时，该字段返回 `null`。
任一 non-empty daily bucket 不完整时，对应的 period total 也返回 `null`。

Usage recording 是 best-effort，失败不能改变 generation 或 embedding result。如果 retry 可能造成重复计数，实现不得重试。

## Recall token semantics

Recall accounting 在 `prepare_context` 完成最终 selection 和 truncation 后执行。Baseline 是所有最终 Memory entry 或
Artifact 通过 exact lineage 直接或间接引用的 Source primary text。Source reference 在估算前去重并排序。
Recalled value 是完整的 `PreparedContext.content`，包含 trust envelope 和 citation。

只有 ready 且每个最终 material 都能解析到至少一个受支持 Source 时，preparation 才可比较。Empty result 和缺少 Source
lineage 的 material 仍按实际情况增加 `preparations` 和 `ready_preparations`，但不计入 token value。Dashboard 必须同时
展示 `comparable_preparations / preparations` comparison coverage 和 token reduction。

初始 estimator 是无模型、无网络依赖的确定性字符估算：ASCII text 约四个字符计一个 token，non-ASCII character 计一个
token，每段完整 text 向上取整。Aggregate 按 `estimator_id` 和 `version` 分桶。
`token_reduction = baseline_tokens - recalled_tokens` 保留符号；负值表示 context overhead。

Recall estimation 和 recording 是 best-effort，失败不能改变 prepared context result。

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

SQLite 和 OceanBase 使用原子增量 upsert。读取时计算 totals 和 zero-filled daily row。这些表不保存正文、object
identity、request ID、trace ID、credential 或 user-defined label，并随 scope 删除。

Statistics query 在一个 database read transaction 中读取所有 section，不返回 partial result。

# Drawbacks

- Best-effort model usage 可能少计，不能用于 billing。
- Character-based token estimate 只能表达趋势，不能复现 provider billing。
- Token reduction 只有与 comparison coverage 一起展示时才有意义。
- Daily aggregate 无法提供逐 operation provenance 或任意时间范围。
- 组合查询的成本高于读取单个 counter。

# Rationale and alternatives

- Generic Entry statistics 会抹去 Artifact family 和 review lifecycle 的边界。
- Generic event recorder 需要额外定义 event identity、retention、idempotency 和 query semantics，本功能不需要这些能力。
- Operational metrics 不是 inventory 或 user-queryable usage storage 的权威来源。
- Per-operation row 会引入初始 dashboard 不需要的 privacy、retention 和 pagination 要求。
- Separate inventory and usage endpoint 会让 dashboard 和 CLI 协调两个相关查询。
- Unbounded `all` period 会使 daily output 无限增长。
- `tokens_saved` 暗示经过验证的 provider savings。本 contract 使用 signed `token_reduction` 并返回 estimator identity。

# Prior art

Mem0 和 Cognee 将 current total 展示为 dashboard card。Supermemory 返回 period total 和 time bucket。Letta 与
Pydantic AI 保留 provider-reported usage。BentoML 区分 product analytics 和 operational metrics。PowerContext 将这些
边界应用于 Source、Candidate 和 Artifact lifecycle。

# Unresolved questions

- `tokens_saved` 是否应作为 dashboard derived label 仍未确定。该名称不能暗示经 provider 验证或可计费的
  savings。
- Dashboard implementation、billing、任意时间范围、逐 operation activity、按 model 或 family 的 usage breakdown、
  MCP projection 和 historical backfill 不在本 RFC 范围内。

# Future possibilities

- 在产品视图确有需要时增加 lifetime total、hourly bucket 或 previous-period comparison。
- 增加固定的 model、provider 或 Artifact family breakdown。
- 增加 deployment-specific tokenizer，但不与 character estimator 的 aggregate 混合。
- 关联独立定义的 activity model，而不改变 statistics field。
