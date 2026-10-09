---
title: 融合算法与参数
description: 配置 Topic 和 Atomic Memory 的 RRF 排名常数、通道权重和归一化检索评分。
---

# 融合算法与参数

当前实现的融合方法为 `rrf`（Reciprocal Rank Fusion，倒数排名融合）。统一 Topic 和 Atomic Memory 检索通过 `fusion`
开放这些参数。Experience 和 Skill 只有一个文本通道，不接受融合参数。
Family 支持范围、准入、模式和请求限制见[检索 Artifact](search-artifacts.md)。

## RRF 计算方式

RRF 合并各启用通道中通过准入的候选排名，排名越靠前，贡献越大。算法使用排名，不直接混合单位和方向不同的
BM25 原分与向量距离。

对于候选 `x`，设 `rank_c(x)` 为它在通道 `c` 中从 `1` 开始的正整数排名，`w_c` 为通道权重，`K` 为排名常数：

```text
raw_rrf(x) = Σ [w_c / (K + rank_c(x))]   对包含 x 的通道求和
upper     = Σ [w_c / (K + 1)]           对所有启用通道求和
retrieval(x) = raw_rrf(x) / upper
```

`retrieval` 是有限数值，归一化范围为 `[0, 1]`。候选在所有正权重的启用通道中都排名第一时，分数为 `1`。
即使某个启用通道没有通过准入的候选，也仍计入 `upper`。候选没有在某个通道出现时，该通道不提供分子贡献，
也不会给它补造原分 `0`。

通用算法使用各通道提供的排名。Topic 在每个通道内合并重复的分块/身份命中，并保留排名间隔；例如排名
`1`、`3` 仍使用 `1`、`3`，不会重新编号。Atomic 检索为每个 Artifact 身份返回一个候选。
只在零权重通道出现的候选不进入结果。融合完成后，先应用 `min_score`，再截取最终 limit。
融合分相同的结果按 Artifact ID 的 UTF-8 升序排序，再按 Revision 降序排序。Atomic 部署配置的重排器在阈值过滤
之后运行，可以改变最终顺序，但不改变检索分数。

## 参数

| 字段 | 类型 | 默认值 | 合法范围 |
| --- | --- | --- | --- |
| `fusion.method` | 字符串 | 省略 `fusion` 时使用 `rrf` | 只支持 `rrf` |
| `fusion.params` | JSON 对象 | `{}` | 只接受 `rank_constant` 和 `weights` |
| `fusion.params.rank_constant` | 整数 | `60` | 正整数 |
| `fusion.params.weights` | 通道名到数值的对象 | `{}` | 有限非负数；启用通道总权重必须为正 |

`K` 只通过 `rank_constant` 设置，顶层的 `K` 或 `rank_constant` 都是非法字段。
数字字符串、布尔值、显式 `null`、非有限数值、未知参数键和负权重都会被拒绝。

权重可以只覆盖部分通道，未在 `weights` 中出现的启用通道仍使用权重 `1`。
权重 `0` 会同时移除该通道在分子和分母中的贡献。所有权重同时乘以同一个正数，不改变归一化分数。
`K` 越大，前几名之间的相对差距越小；`K` 越小，排名差异的影响越大。

各 Family 只接受所选模式启用的通道名：

| Family | 模式 | 合法权重键 |
| --- | --- | --- |
| Topic | `text` | `topic_fts`、`detail_fts` |
| Topic | `vector` | `topic_vector`、`detail_vector` |
| Topic | `hybrid` | `topic_fts`、`topic_vector`、`detail_fts`、`detail_vector` |
| Atomic | `text` | `text` |
| Atomic | `vector` | `vector` |
| Atomic | `hybrid` | `text`、`vector` |

未启用或未知的通道键即使权重为 `0` 也会被拒绝。Topic 省略模式时，任何显式向量权重键都会要求向量能力，
并禁止默认文本回退。Atomic 省略模式时选择文本，因此只有显式选择向量或混合模式后才能提供 `vector` 权重键。
准入和部署能力检查仍然有效，修改权重不能启用部署不具备的检索通道。

## 示例：为两个文本通道设置不同权重

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

`topic_fts` 的权重为 `2`；未指定的 `detail_fts` 仍使用权重 `1`。归一化分母为 `3 / 61`，详情检索没有结果时也一样。
候选在 `topic_fts` 排名第一、未在详情通道出现时，分数为 `2 / 3`。

两个等权重通道、`K = 60` 时：

| 候选排名 | RRF 原始和 | 归一化分数 |
| --- | --- | --- |
| 两个通道都排名第一 | `2 / 61` | `1` |
| 一个通道排名第一，另一个通道未命中 | `1 / 61` | `0.5` |
| 一个通道排名第一，另一个通道为空 | `1 / 61` | `0.5` |
| 一个通道排名第三，另一个通道未命中 | `1 / 63` | `61 / 126` |

启用但为空的通道仍影响分数的含义。不同启用通道集合或权重方案下的分数，不应直接视为同一套相关性标尺。

Atomic 混合检索对 `text`、`vector` 使用相同公式。候选在两个等权重通道都排名第一时为 `1`；在文本中排名第一、
向量通道已启用但为空时为 `0.5`。Atomic 纯文本模式只按一个启用通道归一化，因此文本第一名为 `1`。
这个归一化分数与 Atomic 专用检索保留的 RRF 原始和不同。

## 评分字段与当前边界

`scores.retrieval` 返回归一化 RRF 分数。`scores.channels` 返回带有度量和方向的 FTS 原分或 L2 距离，
不返回内部 RRF 原始和。详见[理解评分](search-artifacts.md#理解评分)。

当前只支持 RRF，未开放 `weighted_score` 和请求参数 `rerank`。Atomic 保留部署配置的重排器，支持 kind/Tag 过滤；
Topic 只接受空过滤条件。融合不会放宽通道准入，也不会用低于 `min_score` 的结果补齐数量。
Topic 专用搜索保留默认 RRF 行为和 `0`–`100` 的旧评分，Atomic 专用搜索保留已有评分契约；高级融合参数通过统一入口使用。
