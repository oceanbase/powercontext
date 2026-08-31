- Proposal Name: `memory_search_reranking`
- Start Date: 2026-08-06
- Related RFCs: [RFC 0014](0014_memory_layer_design.md)、
  [RFC 0016](0016_pydantic_ai_inference_integration.md)、[RFC 0028](0028_context_pack.md)

# Summary

本 RFC 为 PowerContext Memory 搜索增加可选的 listwise rerank 阶段。FTS 与 vector channel 仍负责召回候选，
Reciprocal Rank Fusion（RRF）仍生成粗排顺序。启用 rerank 后，系统通过一次结构化生成请求，从有界候选池中选择稀疏且有序的
子集，再由 `MemoryService.search()` 返回最终 hits。

首个策略是 `powercontext.memory.rerank.listwise.v1`：默认粗召回最多 30 条，由配置的 generation model 选择不超过调用方
`limit` 的结果。Rerank 默认关闭，不修改已存储的 Memory 或索引，并保留每个选中 hit 的准确 Artifact、entry 和 Revision
身份。

# Motivation

Hybrid retrieval 的目标是 recall。较宽的 Top-30 候选池可能已经包含必要证据，但它前面仍可能排着若干词汇相似、却无法回答
query 的条目。把所有候选交给下游模型会增加噪声和 token 成本；直接截断粗排 Top-10 又会丢失相关证据。

LoCoMo 评测验证了一个有效的中间策略：

```text
Hybrid/RRF Top-30
  -> 一次结构化 listwise generation 请求
  -> 面向回答的稀疏选择，最多 Top-10
```

该策略可以作为 benchmark 后处理进行验证，但那不会让 Server、Runtime 或 SDK 用户获得 rerank 能力。产品行为应位于 Memory
search service 的 fusion 之后；benchmark 应通过正常 PowerContext 接口配置并观测该行为。

# Guide-level explanation

## 启用 rerank

Rerank 是 deployment policy。配置 generation model，并在 Runtime 中启用：

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
export POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_ENABLED=true
export POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_CANDIDATE_LIMIT=30
powercontext server run
```

原有搜索请求不变：

```python
page = await runtime.memory.for_scope("project").search(
    SearchMemoryRequest(query="Which deployment decision is current?", limit=10, mode="hybrid")
)
```

关闭 rerank 时，`limit=10` 表示融合并返回粗排前十条。启用 rerank 且 candidate limit 为 30 时，PowerContext 最多融合
30 条粗排结果，让 reranker 选择不超过十条，最后只返回该选择。

当只有少量候选有用时，reranker 可以返回少于十条。如果结构化输出中没有任何合法 rank，PowerContext 会安全回退到粗排前
十条。应用选择前，系统会删除重复、越界和虚构的 rank。

## 理解成本

每次有非空结果的 reranked search 都会增加一次结构化 generation operation。它沿用配置的 generation timeout 和
request bound，并把 temperature 固定为 0。这能提高可重复性，但无法保证所有 provider 都完全确定。

因此，rerank 适用于回答质量比新增模型延迟与 token 成本更重要的场景。低延迟词法查询或没有 generation model 的部署应保持
关闭。

## 观测一次搜索

进程内 Runtime 结果包含 `rerank` trace，记录：

- 带版本的 policy ID；
- 准确的粗排 candidate hits；
- 被选中的原始 ranks；
- 丢弃 rank 与 fallback 诊断；
- rerank latency 和可移植的 request/token usage。

HTTP v1 response 继续只暴露最终 hits，不返回该诊断 trace，以保持现有 OpenAPI contract 兼容。Benchmark 可以使用
进程内 trace，分别评估粗排候选池和最终选择。

# Reference-level explanation

## 配置 contract

`RuntimeConfig` 定义两个字段：

| 字段 | 默认值 | Contract |
| --- | ---: | --- |
| `memory_rerank_enabled` | `false` | 组装并应用 listwise Memory reranker。 |
| `memory_rerank_candidate_limit` | `30` | 粗排融合池，范围为 1 到 100。 |

首版复用 `InferenceConfig.generation_model`、`generation_timeout_seconds` 和 `generation_max_requests`。启用
rerank 却没有 generation model 或显式注入的 `MemoryReranker` 时，启动会返回配置错误。

注入 reranker 是 application composition 选择，即使环境开关为 false 也会应用。这使测试和 provider-specific adapter
部署成为可能，同时保持环境驱动的 composition 清晰。

## 搜索算法

对于 query `q`、最终请求 limit `k` 和配置的 candidate limit `c`：

1. 规范化 `q`，并按现有 capability 规则选择 FTS、vector 或 hybrid mode。
2. 配置 reranker 时令 `coarse_limit = max(k, c)`，否则使用 `k`。
3. 要求每个选中 backend channel 返回 `max(4 * coarse_limit, 32)` 条候选。
4. 应用现有 FTS/vector admission rule 和 RRF，并以 `coarse_limit` 为上限。
5. 未配置 reranker 或没有 hit 时，返回前 `k` 条。
6. 把规范化 query、candidate rank 和 candidate Memory text 交给 reranker。
7. 规范化选中 ranks，保留模型给出的顺序，并映射回原始 `MemoryHit` 对象。
8. 返回最终 hits 及进程内 rerank trace。

模型不会提供 Artifact ID、entry ID、score 或 citation，只选择整数位置。PowerContext 根据已经通过 admission 的候选池解析这些
位置，因此 rerank 无法虚构 evidence identity，也无法修改已存储 Revision。

## Listwise policy

`powercontext.memory.rerank.listwise.v1` 优先选择直接陈述 query 所需 person、event、date、duration、count、list、
reason 或 outcome 的候选；多事实问题会保留互补证据；除非 query 请求当前状态或证据确实冲突，否则不因内容较新而提高优先级。

Candidate Memory 是不可信 evidence，不是 instructions。结构化 schema 只接受整数 rank tuple。规范化规则如下：

- 保留每个范围内 rank 的第一次出现；
- 丢弃重复项和 `1..candidate_count` 之外的 rank；
- 得到 `k` 个合法 rank 后停止；
- 只有完全没有合法 rank 时，才回退到粗排 `1..k`。

合法的稀疏输出不会被补齐，否则会重新引入模型明确排除的候选。

## 一致性与并发

每个 candidate 始终锚定一个准确、不可变的 Memory Revision。Read-only search 在调用模型时不持有 scope mutation lock，
避免慢速 rerank call 串行化同一 scope 中的并发搜索。

并发 Memory mutation 可能使搜索结果引用刚刚成为上一版的 Revision。该结果仍是准确、有效的 citation，不会混合不同 Revision
的 hit identity。Mutation path 继续持有 scope lock，并保持已有 compare-and-swap 行为。

## 失败行为

Provider timeout、provider unavailable 和无效结构化生成输出遵循现有 inference error contract，不会被静默转换成粗排结果。
调用方可以重试幂等 search operation。唯一内置 fallback 是：成功校验的响应中没有可用 rank 时回退粗排。

这种 inference fail-closed 行为使质量降级保持可见。要求搜索不依赖模型可用性的部署应关闭 rerank。可配置 fail-open policy
不在本 RFC 范围内。

## Persistence 与 API 影响

Rerank 不写入 table、projection、cursor 或 Artifact Revision。启用或关闭无需 migration，也不改变 embedding profile
identity。

Transport-neutral Runtime 返回最终 reranked hits 和可选 trace。HTTP v1 mapping 继续只返回最终 hits，因此
`openapi/powercontext.yaml` 和生成 client 不变。请求 `limit` 始终表示最终 hit 最大数量。

## 评测边界

LoCoMo 负责 dataset loading、回答阶段的 Source expansion、answer prompt、确定性指标和 judge policy。它可以把 `none` 或
`llm` 作为运行参数，但不得自行构造 rerank generator、实现 selection 或重新排序 hits。Candidate metrics 来自
PowerContext rerank trace，最终指标来自返回 hits。

准确率声明必须写明 judge policy。Topical-policy LLM Judge 分数不能与 strict judge、Exact Match、Token F1 或 BLEU-1
混为一谈。

# Drawbacks

- 每次非空搜索增加一次 generation request，会显著提高延迟和 token 成本。
- Memory entry 很长时，listwise prompt 可能占用较大 context。
- 复用 generation model 配置简单，但可能比专用 cross-encoder rerank service 更慢。
- 进程内诊断 trace 保留 candidate text，应按普通搜索结果的敏感级别处理。

# Rationale and alternatives

直接返回更小的粗排 Top-K 会在比较 relevance 前损失 recall，因此未采用。Lexical reranker 在评测中没有改善 evidence
selection，因此未采用。把 rerank 放在 benchmark 会导致 PowerContext 本身没有该能力，并可能与 production behavior
漂移，因此未采用。

Provider-neutral `MemoryReranker` port 把 Memory selection 语义保留在 Artifact Family 内，而 Pydantic AI structured
generator 继续作为 inference adapter。只选择 rank、不重新生成 hit，可以保留 identity，并使输出校验保持小而确定。

# Prior art

PowerContext 已在 RFC 0014 中区分 backend channel retrieval、candidate admission 与 RRF fusion，并使用 RFC 0016 的
schema-bound generation adapter。LoCoMo retrieval experiment 为首个 policy 提供了经验 candidate/final limit。本 RFC
组合这些既有机制，不引入新的 index 或 persistence authority。

# Unresolved questions

- HTTP client 是否应能请求或接收脱敏 rerank trace？
- 后续 policy 是否需要在 provider outage 时支持 fail-open？
- 对于不报告 chat token 的专用 rerank endpoint，需要怎样的 provider-neutral usage contract？

# Future possibilities

后续实现可以增加专用 rerank-model adapter、batch search、按 query class 调整 candidate limit 或 latency budget。这些扩展
必须继续保留准确 hit identity、显式 policy version、默认关闭兼容性，以及粗排与最终选择的独立评测。
