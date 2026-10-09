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
| `atomic-memory` | 支持 | `text`、`vector`、`hybrid`；默认文本检索 | 调用者可读取、处于 active 状态的独立 Atomic Memory head |
| `memory` | 不支持 | 已冻结的旧集合；兼容检索返回 Atomic Memory 记录 | 见[使用 Atomic Memory](atomic-memory.md#旧-memory-api-兼容) |
| `profile` | 不支持 | 此入口不提供相关性检索 | 见[使用 Profile](use-profiles.md) |
| `handoff` | 不支持 | 此入口不提供相关性检索 | 见 [Memory 与 Handoff](memory-and-handoff.md) |
| `prompt` | 不支持 | 此入口不提供相关性检索 | 见[管理 Prompt](manage-prompts.md) |

Source 是证据，Tag 是元数据，两者都不是此入口检索的 Artifact Family。部署必须启用所选 Family 的检索索引。
Topic 和 Atomic 向量或混合检索还需要兼容的 Embedding 模型和索引 Profile。部署设置见[配置向量检索](configure-vector-search.md)。

## 通用参数与限制

| 字段 | 类型与默认值 | 支持范围 |
| --- | --- | --- |
| `query` | 必填非空字符串 | 去除首尾空白。Experience、Topic 和 Atomic 最多 8192 个字符，Skill 最多 2000 个字符。 |
| `limit` | 整数，默认 `10` | Experience、Skill 为 `1`–`200`；Topic 为 `1`–`20`；Atomic 为 `1`–`100`。这是返回上限，不保证填满。 |
| `mode` | 可省略的字符串 | 选择该 Family 支持的模式；省略时使用默认策略。 |
| `filters` | 对象，默认 `{}` | Atomic 支持 `kind`、`tags` 和 `tag_match`；Experience、Skill 和 Topic 只支持 `{}`。 |
| `admission` | 可省略的对象 | 决定检索到的候选能否参与评分；字段见下文。 |
| `min_score` | 可省略的有限数值，范围 `[0, 1]` | 只保留归一化检索分数不低于该值的结果；省略时不设评分阈值。 |
| `include_scores` | 布尔值，默认 `false` | 在结果中附加检索分数和实际存在的通道原分。 |
| `fusion` | 可省略的对象 | Topic 和 Atomic 开放 `rrf`，见[融合算法与参数](search-fusion.md)。 |

使用默认值时省略对应字段。显式 `null`、未知字段、数字字符串、数值字段中的布尔值均会被拒绝。
`limit` 必须是整数，`include_scores` 必须是 JSON 布尔值。请求参数 `rerank` 和 `weighted_score` 融合方法
均未开放。Experience 和 Skill 不接受 `fusion`。

准入和评分完成后，先应用 `min_score`，再截取最终 `limit`。阈值过滤后不会用更低分的候选补齐数量。
开启评分只增加响应元数据，不改变候选准入、结果身份、顺序或阈值行为。

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

## Atomic Memory 参数

Atomic 通过 `text`、`vector` 或 `hybrid` 检索 active 状态的当前 head。省略模式时始终使用文本检索，配置了
Embedding 的部署也一样。向量和混合模式要求启用匹配的索引 Profile 并配置 Embedding 模型，不会回退到文本。
空 Scope 返回空结果之前，也会检查部署能力和参数组合。

Atomic 使用 `text` 和 `vector` 两个通道名。文本通道应用上述词法准入规则，向量通道应用与 Topic 相同的
L2 距离语义准入规则。默认词法覆盖率为 `0.25`、最少匹配词数为 `2`、语义相似度为 `0.3`。
文本模式不接受显式语义准入字段，向量模式不接受显式词法准入字段。权重键必须属于启用通道，权重为 `0` 也一样。

Atomic 过滤条件与访问控制、active 状态资格一起应用，先于候选排名：

| 字段 | 合法值 |
| --- | --- |
| `filters.kind` | 应用定义的非空白类别，最多 128 个字符 |
| `filters.tags` | 1–16 个 Tag 标签，每个 1–64 个字符，不能带首尾空白或控制字符；规范化后重复的标签会被拒绝 |
| `filters.tag_match` | `all` 或 `any`；提供 tags 时默认 `all`，显式设置时必须同时提供 `tags` |

Tag 匹配沿用 NFC 和大小写折叠规则，见[管理 Artifact Tag](manage-artifact-tags.md)。

```http
POST /v1/scopes/project-a/artifacts/atomic-memory/search
Content-Type: application/json

{
  "query": "release rollback",
  "mode": "hybrid",
  "limit": 10,
  "filters": {"kind": "decision", "tags": ["release"], "tag_match": "all"},
  "fusion": {"method": "rrf", "params": {"rank_constant": 60, "weights": {"text": 2}}},
  "include_scores": true
}
```

Atomic 在单通道模式下也使用归一化 RRF。`min_score` 根据融合后的检索分数过滤，先于部署配置的重排和最终选择。
已配置的重排器仍会生效，可以改变结果顺序，但不会改写检索分数和通道原分。请求不能启用、关闭或配置重排。
结果返回完整、不可变的 Artifact 正文和 lineage。当前生命周期与 state_version 通过
[Atomic Memory 接口](atomic-memory.md#当前状态与遗忘)读取。

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

`retrieval` 是 `[0, 1]` 范围的归一化检索分数，用于 `min_score` 筛选和 Atomic 配置重排之前的排名。
`channels` 保存该结果通过准入的通道原分：

| 度量 | 含义 | 更好的方向 |
| --- | --- | --- |
| `sqlite_bm25` | SQLite FTS 的带符号 BM25 原分 | 越小越好；匹配分数为负数 |
| `oceanbase_match` | OceanBase MATCH 相关性原分 | 越大越好 |
| `l2_distance` | Topic 或 Atomic 向量 L2 距离 | 越小越好 |

Experience 和 Skill 的通道名为 `text`；Atomic 使用 `text` 和 `vector`；Topic 使用上表列出的四个通道名。
没有命中的通道不会出现，也不会补造 `0`。如果其他通道选中了同一个 Artifact，通过准入但权重为 `0` 的通道
仍可出现在评分元数据中。Topic 详情原分对应各通道实际选出的代表分块。

原分依赖 backend 和查询，不是跨查询、跨 backend 通用的相似度。Topic 和 Atomic 通道原分是 FTS 分数或向量距离，
不是 RRF 贡献值。评分属于本次搜索响应，不修改已保存的 Artifact 正文或 lineage。
`include_scores: false` 时，响应省略 `scores`。

## 故障与原有接口

未知 Family 返回 `404`；已知但不支持统一搜索的 Family、未配置的检索能力或不支持的参数返回 `422`。
非法请求会指出参数路径；缩小 limit 或使用空 Scope 都不会绕过校验。不能回退时，模型超时或不可用返回 `503` 服务错误。
实现和存储故障会保留失败状态，不转换成成功的空结果。

统一检索保留 Atomic、Topic 专用检索行为、Skill Library 搜索，以及已有 SDK/内部 Experience 召回。
旧 Memory 路由的升级契约见 [Atomic Memory 兼容说明](atomic-memory.md#旧-memory-api-兼容)。
其中 `POST /v1/topic-memory/search` 仍按部署默认策略检索，返回摘要命中和 `0`–`100` 的旧评分，
不开放上述高级参数。统一 Topic 入口返回完整 Artifact 正文，归一化检索分数为 `[0, 1]`。
专用摘要搜索和精确详情读取流程见[使用 Topic Memory](topic-memory.md)。
