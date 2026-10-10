---
title: 检索 Artifact
description: 在一个 Scope 内检索受支持的 Artifact 家族，并理解检索评分。
---

# 检索 Artifact

统一检索入口用于查找一个 Scope 内相关的 Artifact 当前 head。每条结果包含精确的 `family`、`artifact_id`、
`revision`、该家族的完整 `content` 和 `lineage`。搜索不会完整枚举制品，也不跨 Scope 检索。
列举制品和读取历史版本见[管理 Artifact](artifacts.md)；Agent 需要有界上下文时，使用[准备上下文文本](prepare-context-text.md)，
避免直接注入完整正文。

## 发起检索

调用方需要 `scope.read` 权限。Scope 和 Family 放在路径中，检索参数放在请求体中。
调用者身份、访问控制和审计上下文来自服务端认证，不能通过请求体提供：

```http
POST /v1/scopes/project-a/artifacts/experience/search
Content-Type: application/json

{
  "query": "release rollback",
  "limit": 5,
  "include_scores": true
}
```

使用受信任环境中的 Server 地址和凭据；启用 Access 时，添加 `Authorization: Bearer <token>` Header。
响应为 `{"results": [...]}`。`results` 为空表示搜索成功，但没有通过准入的结果。返回内容保留实际的家族字段和精确版本 lineage。

## 各 Family 的支持范围

内置部署通过此入口提供以下能力：

| Family | 统一检索 | 模式与默认策略 | 检索内容 |
| --- | --- | --- | --- |
| `experience` | 支持 | `text`；省略模式时使用文本检索 | 已批准、处于 active 状态的 Experience head |
| `skill` | 支持 | `text`；省略模式时使用文本检索 | 已批准、处于 active 状态的托管 Skill head；不检索外部 Skill 目录 |
| `topic-memory` | 支持 | `text`、`vector`、`hybrid`；配置 Embedding 时默认混合检索，否则默认文本检索 | Topic Memory 当前 head，分别检索标题/摘要和详情通道 |
| `atomic-memory` | 支持 | `text`、`vector`、`hybrid`、`auto`；默认 `auto` | 当前 active 的 Atomic Memory 正文 |
| `memory` | 不支持 | 保留原有 Memory 检索入口 | 见 [Memory 与上下文](memory-and-context.md) |
| `profile` | 不支持 | 此入口不提供相关性检索 | 见[使用 Profile](use-profiles.md) |
| `handoff` | 不支持 | 此入口不提供相关性检索 | 见 [Memory 与 Handoff](memory-and-handoff.md) |
| `prompt` | 不支持 | 此入口不提供相关性检索 | 见[管理 Prompt](manage-prompts.md) |

Source 是证据，Tag 是元数据，两者都不是此入口检索的 Artifact Family。部署必须启用所选 Family 的检索索引。
Topic 向量或混合检索还需要兼容的 Embedding 模型和索引 Profile。部署设置见[配置向量检索](configure-vector-search.md)。

## 通用参数与限制

| 字段 | 类型与默认值 | 支持范围 |
| --- | --- | --- |
| `query` | 必填非空字符串 | 去除首尾空白。Experience、Topic、Atomic 最多 8192 个字符，Skill 最多 2000 个字符。 |
| `limit` | 整数，默认 `10` | Experience、Skill 为 `1`–`200`；Topic 为 `1`–`20`；Atomic 为 `1`–`100`。这是返回上限，不保证填满。 |
| `mode` | 可省略的字符串 | 选择该 Family 支持的模式；省略时使用默认策略。 |
| `filters` | 对象，默认 `{}` | Experience、Skill、Topic 只支持空对象；Atomic 的参数见下文。 |
| `admission` | 可省略的对象 | 决定检索到的候选能否参与评分；字段见下文。 |
| `min_score` | 可省略的有限数值，范围 `[0, 1]` | 只保留归一化检索分数不低于该值的结果；省略时不设评分阈值。 |
| `include_scores` | 布尔值，默认 `false` | 在结果中附加检索分数和实际存在的通道原分。 |
| `fusion` | 可省略的对象 | 只有 Topic 开放 `rrf`，见[融合算法与参数](search-fusion.md)。 |

使用默认值时省略对应字段。显式 `null`、未知字段、数字字符串、数值字段中的布尔值均会被拒绝。
`limit` 必须是整数，`include_scores` 必须是 JSON 布尔值。Experience、Skill、Topic 不接受非空 `filters`；`rerank` 和 `weighted_score` 融合方法
均未开放。Experience 和 Skill 不接受 `fusion`。

准入和评分完成后，先应用 `min_score`，再截取最终 `limit`。阈值过滤后不会用更低分的候选补齐数量。
开启评分只增加响应元数据，不改变候选准入、结果身份、顺序或阈值行为。

## Atomic Memory 检索

`atomic-memory` 使用独立 Atomic Memory 入口已有的当前正文检索，要求 `scope.read`。
单条 Artifact 分享仍可通过精确读取和历史读取访问。结果保留现有 RRF 顺序，并返回完整的精确版本 Artifact。
已退役的 `memory` Family 不作为检索回退。

Atomic 接受 `query`、`limit`、`mode`、`filters` 和 `include_scores: false`。
`filters.kind` 筛选类型；`filters.tag_filter` 接受 `{"tags": ["release"], "match": "all"}`，也可使用 `"any"`。
配置兼容的 Embedding 模型时，`auto` 使用混合检索，否则使用文本检索。
现有检索结果未保留所有通道的原始分数，因此 `include_scores: true` 返回 HTTP `422`，错误码为
`artifact_search_not_supported`，字段为 `include_scores`。此适配层不接受 `admission`、`fusion`、
`min_score`、`rerank` 等其他 Family 参数。

## Experience 和 Skill 文本检索

这两个 Family 都使用一个 `text` 通道，支持以下准入字段：

| 字段 | 默认值 | 范围 |
| --- | --- | --- |
| `admission.lexical_coverage` | `0.25` | 有限数值，范围 `[0, 1]` |
| `admission.lexical_min_matched_terms` | `2` | 不小于 `1` 的整数 |

设 `n` 为分析后去重的查询词数量。查询最多含两个词时，至少匹配一个词；更长的查询至少需要匹配
`max(lexical_min_matched_terms, ceil(lexical_coverage × n))` 个词。准入检查先于评分和截断。
未批准或非 active 的 head 不会进入结果。

归一化评分为 `relevance / (1 + relevance)`。SQLite 的 relevance 为 BM25 原分取负，OceanBase 的 relevance
为 MATCH 原分。两个 backend 都返回 `[0, 1]` 范围的分数，但标尺不同。请根据实际 backend 和查询样本选择
`min_score`，没有可跨 backend 直接套用的统一推荐阈值。

## Topic Memory 参数

Topic 使用四个命名通道：

| 公开模式 | 启用通道 |
| --- | --- |
| `text` | `topic_fts`、`detail_fts` |
| `vector` | `topic_vector`、`detail_vector` |
| `hybrid` | 全部四个通道 |

文本模式分别检索标题/摘要和详情；向量模式使用部署配置的 Embedding Profile。文本和混合查询最多支持
64 个分析后去重的查询词；纯向量查询使用 8192 字符限制。

Topic 支持上述两个词法准入字段，以及 `admission.min_semantic_similarity`：有限数值，范围 `[-1, 1]`，默认 `0.3`。
每个词法通道独立应用匹配词数量规则。每个向量通道要求归一化向量的 L2 距离满足
`clip(1 - distance² / 2, -1, 1) >= min_semantic_similarity`。某个通道未通过准入时，只移除该通道的贡献；
其他通道通过准入后，仍可能保留同一个主题。

Topic 使用 RRF 合并通过准入的排名。省略 `fusion` 时，默认使用 `rrf`、排名常数 `60`，每个启用通道的权重为 `1`。
归一化公式见[融合算法与参数](search-fusion.md)。

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

即使 Scope 内没有主题，也会校验参数组合：

- `text` 不接受显式语义准入字段和任何向量权重键，包括权重为 `0` 的键。
- `vector` 不接受显式词法准入字段和任何 FTS 权重键，包括权重为 `0` 的键。
- 权重键必须属于当前启用通道。未知通道或启用通道总权重为 `0` 均会被拒绝。
- 显式 `vector`、`hybrid`、语义阈值或任意向量权重键都要求向量能力。显式提供与默认值相同的值也带有这一要求。

省略模式时，已配置 Embedding 的 Topic 默认使用混合检索，否则使用文本检索。默认混合请求没有显式向量要求、
且 FTS 通道总权重为正时，Embedding 暂时不可用或超时可以回退到文本。显式向量/混合请求，以及带有显式向量要求
的请求都不会回退。两个 FTS 权重都为 `0` 时，Embedding 失败仍是服务错误。非法向量、不兼容的 Profile 和存储错误
不会触发文本回退。

## 理解评分

`include_scores: true` 会在每条结果中增加 `scores`。下面只展示该字段：

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

`retrieval` 是 `[0, 1]` 范围的归一化检索分数，用于排序和 `min_score` 筛选。
`channels` 保存该结果通过准入的通道原分：

| 度量 | 含义 | 更好的方向 |
| --- | --- | --- |
| `sqlite_bm25` | SQLite FTS 的带符号 BM25 原分 | 越小越好；匹配分数为负数 |
| `oceanbase_match` | OceanBase MATCH 相关性原分 | 越大越好 |
| `l2_distance` | Topic 向量 L2 距离 | 越小越好 |

Experience 和 Skill 的通道名为 `text`；Topic 使用上表列出的四个通道名。没有命中的通道不会出现，也不会补造 `0`。
如果其他通道选中了同一个主题，通过准入但权重为 `0` 的 Topic 通道仍可出现在评分元数据中。
详情原分对应各通道实际选出的代表分块。

原分依赖 backend 和查询，不是跨查询、跨 backend 通用的相似度。Topic 通道原分是 FTS 分数或向量距离，
不是 RRF 贡献值。评分属于本次搜索响应，不修改已保存的 Artifact 正文或 lineage。
`include_scores: false` 时，响应省略 `scores`。

## 故障与原有接口

未知 Family 返回 `404`；已知但不支持统一搜索的 Family、未配置的检索能力或不支持的参数返回 `422`。
非法请求会指出参数路径；缩小 limit 或使用空 Scope 都不会绕过校验。不能回退时，模型超时或不可用返回 `503` 服务错误。
实现和存储故障会保留失败状态，不转换成成功的空结果。

原有 Memory、Topic HTTP 搜索路由、Skill Library 搜索，以及已有 SDK/内部 Experience 召回保留原来的请求与响应契约。
其中 `POST /v1/topic-memory/search` 仍按部署默认策略检索，返回摘要命中和 `0`–`100` 的旧评分，
不开放上述高级参数。统一 Topic 入口返回完整 Artifact 正文，归一化检索分数为 `[0, 1]`。
专用摘要搜索和精确详情读取流程见[使用 Topic Memory](topic-memory.md)。
