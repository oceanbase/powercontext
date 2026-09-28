- 提案名称：`memory_quality_and_lifecycle`
- 起始日期：2026-09-18
- 跟踪 Issue：[oceanbase/powercontext#1590](https://github.com/oceanbase/powercontext/issues/1590)
- 相关 RFC：[RFC 0014](/zh/rfcs/0014_memory_layer_design)、[RFC 0019](/zh/rfcs/0019_local_source_memory_runtime)、
  [RFC 0028](/zh/rfcs/0028_context_pack)、[RFC 0050](/zh/rfcs/0050_artifact_candidate_review_inbox)、
  [RFC 0080](/zh/rfcs/0080_memory_search_reranking)、[RFC 1229](/zh/rfcs/1229_unified_workloads_and_long_horizon_memory_evaluation)
  和 [RFC 1560](/zh/rfcs/1560_recall_sufficiency_gate)
- 相关工作：[#1425](https://github.com/oceanbase/powercontext/issues/1425)、
  [#1321](https://github.com/oceanbase/powercontext/issues/1321)、
  [#1556](https://github.com/oceanbase/powercontext/issues/1556)、
  [#1586](https://github.com/oceanbase/powercontext/pull/1586) 和
  [#1596](https://github.com/oceanbase/powercontext/pull/1596)

# 摘要

本 RFC 定义一个默认关闭、保留证据的 Memory 质量与生命周期策略。它引入四个相互独立的派生维度：
`provenance/verification`、重要性、新颖性和当前状态，用于维护健康的活跃 Memory 检索面；但不修改权威
entry 正文、Artifact Revision、精确引用或公开 RRF 分数。

第一阶段的质量策略刻意收窄：先过滤 PowerContext 已经确定无效的记录，然后仅在**固定的 RRF 粗候选池内**
做最多 `±2` 名的稳定重排。它既不改变通道准入，也不会把池外条目带入 reranker。近重复检查只产生关系与
评审提案，不进行破坏性合并。后续可由默认关闭、先 dry-run 的 L1 策略可恢复地停用低价值条目；高风险的
合并、取代和晋升则属于必须评审的 L2 扩展。

本 RFC 严格区分“可恢复停用”和“自动操作副作用的回滚”。同时，它将 Memory 的逻辑生命周期与物理删除、
合规保留、跨制品清理分开；后者仍属于 #1425 与后续 RFC 的范围。

# 动机

Memory 已具备不可变 entry version、manifest 状态、精确 citation、FTS/vector 准入、RRF 融合及可选
listwise rerank。但当前活跃检索面仍有三类缺口：

1. 长期、直接证据支持的约束，和一次性、弱证据的工作笔记，没有可显式使用的质量区分。
2. 字节级去重无法控制同一指令或演化事实的活跃同义改写。
3. 对已经确定 `inactive` 或 `superseded` 的记录，读路径没有独立于相关性排序和 reranker 开关的
   时间有效性语义。

它们不是同一个问题。质量不是写入门禁；语义相似不是可合并的证明；生命周期有效性也不是软排序偏好。
把它们混在一起，会导致低质量记录从历史中消失、reranker 看到已撤销证据，或让任意“更新”压过长期约束。

目标是宽松写入、有限且可检查的检索和维护：

```text
直接写入有依据的 Memory
  → 保留不可变 entry 权威与证据
  → 构建有界的质量/生命周期派生投影
  → 在检索排序前强制已知有效性
  → 只重排固定 RRF 池内的条目
  → 仅可恢复地退役明确合格的低价值条目
  → 合并、取代和晋升必须评审
```

# 使用说明

## 普通 Memory 写入保持不变

本 RFC 不会让普通 `remember` 调用进入 Review。例如“文档变更后运行 `make docs-test`”仍是正常的
Memory 写入，保留其精确证据和不可变 entry version。

关闭所有生命周期选项时，现有写入、搜索、rerank、Context Pack 和 citation 行为完全不变。新策略不允许
改写 entry 正文、静默删除历史，或改变公开的 Memory `score` 语义。

## 当前状态检索不同于历史检索

假设项目先记录：

```text
文档变更必须运行 make test。
```

之后，新的直接证据确认：

```text
文档变更必须运行 make docs-test。
```

在经过评审的显式取代关系建立后，普通“当前状态”检索只返回后者。旧记录不是“排得更低”，而是在 RRF 与
可选 reranker 之前被过滤。精确历史读取仍可解析被保留的旧 entry 以及其取代原因。

若两条记录只是看起来矛盾，但 PowerContext 没有可靠关系证据或可比较时间，则两者必须保留为
`unresolved_conflict`。Context Pack 应标注其为未解决证据，不能让 agent 从两段普通文本或检索排名中猜测
谁更新。

## 质量只帮助已召回的证据排序

单次搜索先完成通道检索、准入、生命周期有效性过滤、RRF 与粗候选池截断；之后质量才可以在该精确集合内做小幅稳定调整：

```text
基线 RRF 成员：      A  B  C  D  E  F
质量调整后的顺序：   A  C  B  D  E  F
```

成员集合不会改变。没有通过 RRF 的记录不能被质量策略带入 reranker 输入。由此，质量是保守的选择辅助，而不是
隐含的第二套召回机制。

启用 RFC 1560 recall gate 时，每个已发起的搜索 round 仍保持现有顺序边界：质量重排只在该 round 的固定粗候选池内进行，
随后运行既有的可选 listwise reranker，gate 接收由此产生的 `result.hits`。本 RFC 不会延迟 rerank，也不会把
仅基线顺序的结果替换为 gate 输入。质量关闭或保持中性时，gate 接收与当前行为相同的重排结果、作出相同决策，并保持
相同的成本统计。

公开 hit `score` 保持为基线 RRF 分数。提议中的进程内 `MemoryRankingTrace` 用于诊断与评测；第一阶段 HTTP
搜索输出不携带该 trace。

## 遗忘是移出活跃检索面，不是抹除历史

可选的 L1 维护任务可以识别低重要性、已过期、未保护的条目进行停用。它必须先以 dry-run 运行；启用后通过
普通 Memory deactivate Revision 写入稳定原因，例如 `auto_decay:v1`。

entry 正文、历史 revision、证据和精确 citation 均保留。`reactivate()` 将同一 entry version 恢复到活跃投影
并重新开始生命周期区间。这是**可恢复停用**，不是承诺撤销该 entry 曾引发的所有下游影响。

## 语义维护必须经过评审

两条同义 entry 可能值得对齐，反复出现的 working note 可能值得晋升；但它们都不是自动变更。L2 任务生成
提案，携带受影响的精确 entry version、证据、关系和预期影响。只有批准后才允许新建 Memory Revision 或改变
生命周期关系。

普通 Memory 仍直接写入。L2 是 RFC 0050 review 机制的受限、Memory 专用扩展，而不是把所有 Memory 写入改为
进入 Review。

# 参考级说明

## 设计不变量

1. **不可变权威。** entry 正文、content hash、Artifact Revision、证据 citation 与 Handoff 精确身份保持不可变且权威。
2. **零回归默认值。** 所有生命周期选项默认关闭或中性；旧数据获得中性派生值，缺少派生条件时禁用功能而非猜测。
3. **有效性先于质量。** 对普通当前状态检索，已知 `inactive` 与显式 `superseded` 记录在 RRF 与可选 rerank 前
   过滤，不依赖质量策略、新鲜度配置或模型可用性。
4. **质量不是准入。** 重要性、新颖性、来源与当前状态不决定是否可写入或通过词法/向量准入，只影响有界排序或维护资格。
5. **时间不确定时安全失败。** 缺少可靠关系或时间不可比较的冲突保留为 `unresolved_conflict`；不得以相关性或 RRF
   顺序替代时间证据。
6. **精确定义可逆性。** L1 承诺可恢复停用；L2 必须记录操作并定义补偿行为，`reactivate()` 不等于回滚合并、晋升
   或派生视图。
7. **后端一致。** SQLite 与 OceanBase 必须提供相同的有效性过滤、有界排序、生命周期原因、重建行为与关闭模式结果。

## 权威状态与派生状态

现有 Memory manifest 仍是 active/inactive entry 状态的权威。本 RFC 引入可重建的 **Memory 生命周期投影**，
与 current-head 搜索投影相邻。它以精确的 Memory artifact、entry 与 entry version 身份为键，可以实现为 head
存储扩展或独立投影表。

投影绝不保存替代正文，其逻辑形状为：

```text
entry 身份：    memory_artifact_id、entry_id、entry_version_id
质量：         importance、evidence_strength、provenance class、novelty relation summary
有效性：       current | inactive | superseded | unresolved_conflict
谱系：         validity_reason、已知时的 successor identity
时间：         recorded_at/effective_at，外加明确的 unknown 状态
生命周期：     tier、protected/pinned、lifecycle interval、automatic-action reason
可观测性：     有界的 source/artifact 引用计数与规则版本标识
```

该投影从权威 Memory Revision、精确 evidence reference、声明的 Source 属性及有界生命周期/操作记录推导，必须
可以重建；它不得影响 entry content hash，也不得成为第二套内容权威。

时间值必须有明确的持久化规则。首次观察 entry 的写入或生命周期操作必须同时记录 `recorded_at`；只有 evidence
明确提供时才接受 `effective_at`。操作记录绑定精确的 Memory revision 和 entry version，投影重建不能依赖重建时的墙上时钟
或后端特有的行时间戳。没有这类记录的旧 entry 保持 `unknown`：新鲜度保持中性，且在后续权威事件提供可用时间前不具备按年龄
执行 L1 停用的资格。单独派生的 `created_at` 或 `revised_at` 不能让旧 entry 自动满足年龄条件。

`MemoryHit` 仍只携带现有身份、文本、公开 RRF 分数与匹配通道。读路径可附加内部
`MemoryContextAnnotation`，其中包括有效性、原因/后继、时间是否已知和有界 provenance 摘要。Context 渲染用它
标记当前、历史或未解决的证据。

## Source 权威与验证前置条件

当前 `Source` 只有 `name`、`definition_version`、`materialization` 和 `description`；`SourceDefinition` 只有
版本和 projection。两者都没有 authority 或 verification 声明。`SourceRef` 只是身份，绝不能被用于猜测可信度。

启用第一阶段质量排序前，Source 注册必须新增可重建的 adapter contract，暂称 `MemoryEvidenceDeclaration`：

```text
SourceDefinition.memory_evidence:
  authority: untrusted | user_asserted | repository_attested | system_attested
  verification: unknown | verified | not_verified
  declaration_version: 稳定的 contract 版本
```

具体枚举文字可在实现 Source contract 时调整，但必须满足：

- adapter 或受信任的注册配置显式声明；
- 该声明带版本，且可对历史 Source materialization 重建；
- 正文、`SourceRef` 或模型都不能伪造更高声明；
- 缺少声明时解析为 `unknown`/untrusted，且不能启用第一阶段质量排序。

Memory entry 写入时，必须把声明版本及解析后的 authority/verification 状态快照到该 Source materialization 的有界生命周期
证据中。重建使用当时生效的历史声明，而不是当前 registry 的结果。若快照缺失或无法校验，entry 保持
`unknown`/untrusted，该部署不能启用第一阶段质量排序。

这是 Source/SourceDefinition adapter surface 的变更，应在排序功能**之前**落地，而不是与其并行猜测。本策略没有
`verified_source_bonus`，也没有模型生成的 authority 分数。

若部署允许实质性的不可信 Source，后续可将一个有界的**来源类别占比**策略与中性基线、已校准的 provenance 排序基线
进行对比：限制低 authority 类别在固定候选池或最终 Context Pack 中占用的位置/字节份额，但必须保留显式路径，允许
确实能回答问题的证据进入。它是 opt-in 的评测实验，不是第一阶段默认行为，也不能替代 Source 声明 contract。

## 独立质量维度

四个维度必须独立：

| 维度 | 派生来源 | 第一阶段用途 | 禁止的捷径 |
| --- | --- | --- | --- |
| provenance/verification | 声明的 Source contract 与精确证据 | 保护 authority-sensitive 条目；校准后可提供有限正向排序信号 | 从 `SourceRef`、正文或模型推断 |
| importance | 确定性的 entry-version 特征 | 有界排序与 L1 资格 | 模型自由打分 |
| novelty | 规范化相等与有界关系检查 | 关系/提案与密度观测 | 自动降重要性或语义删除 |
| current-state | 显式条目分类和时效查询意图 | 仅决定是否可使用新鲜度提示 | 泛化为“越新越好” |

第一阶段不产生 `importance_band`、`importance_reason` 或模型排序加分。确定性 importance 种子为：

```text
base(kind):       fact=1, preference=1, decision=2, constraint=2, working_note=0, unknown=1
updates_state:    有证据支持的 revise 改变当前状态时 +1
thin_penalty:     第一阶段为 0；未来版本化的内容密度规则校准后才可加 −1

importance = clamp(base + updates_state - thin_penalty, 0..3)

封顶：weak evidence、session tier、未被佐证的 working note 不超过 normal。
```

这些数值仅为校准种子，不是待启用的默认值。分数只在 add 或有证据支持的 revise 时重算，不进行周期性模型重评。未知 kind 使用
中性的 normal 基础分。第一阶段不从正文推断 thin penalty；未来内容密度规则必须确定性、版本化，并在所需输入不可用时禁用。
分数可通过显式 revise、后续佐证或用户覆盖改变；有效性和新颖性不能由 importance 推断。

## 有效性与时间语义

普通当前状态检索中，在通道准入之后、RRF 之前按如下策略执行：

| 投影有效性 | 普通当前状态检索 | 精确/历史读取 |
| --- | --- | --- |
| `current` | 可参与 | 可参与 |
| `inactive` | 过滤 | 可读取，带停用原因 |
| `superseded` | 过滤 | 可读取，带后继/原因 |
| `unresolved_conflict` | 可参与并标注 | 可参与并标注 |

第一阶段不新增泛化的 HTTP 历史查询参数。现有精确 Artifact/entry version 解析仍是历史读取路径。未来公开的时间查询
接口必须显式表达所请求的视图；不得把普通当前状态搜索静默用于历史问题。

语义推断不足以设置 `superseded`。必须由 L2 批准或直接、权威的生命周期证据建立。未知或不可比较时间仍明确为
`unknown`，不能成为排序键。

## 检索算法与固定成员

对查询 `q`、请求上限 `k` 与当前搜索配置，第一阶段顺序是：

```text
每通道候选
  → 既有 FTS/vector 准入
  → 请求时间视图的有效性过滤
  → 基线 RRF 与粗候选池截断
  → 仅在该 round 固定池内做有界稳定质量重排
  → 对相同成员运行既有的可选 listwise reranker
  → `result.hits`
  → （启用 recall gate 时）既有 sufficiency 评估与有界的下一轮决策
  → 最终 limit 与既有 Context Pack 候选数、字节预算
```

`fuse_rankings()` 目前在函数内按 `limit` 截断；`MemoryService` 传入 `coarse_limit`，无 reranker 时为 `k`，有
reranker 时至少为其候选上限。质量只能在该截断**之后**运行；不得扩展 fusion、降低准入门槛，或改变 reranker 的
成员身份。recall gate 关闭时，该顺序只有一个 post-RRF pool；启用时，每个已发起的 round 都运行同一顺序：gate 接收
由既有可选 reranker 产生的 `result.hits`，与当前行为一致。质量不会创建新的 gate 输入模式，也不会延迟 rerank。

重排由确定性的 `bounded_stable_reorder` 完成：

1. 从固定 RRF 顺序开始；
2. 根据独立维度和查询意图计算带策略版本的候选位移；
3. 产出稳定顺序，每个成员相对基线最多移动两位；并列时保留基线 RRF 顺序；
4. 断言调整前后 identity 集合完全相同。

旧的 `0.5..2.0` 乘法方案被拒绝。RRF 常数为 60 时，单通道 rank-1/rank-10 仅约 1.15x、rank-1/rank-30 约
1.48x、rank-1/rank-64 约 2.03x；4x 系数会成为一等排序项，并非受限的 tiebreaker。

新鲜度是独立的查询时提示：

```text
freshness(age) = alpha + (1 - alpha) * 2^(-age / half_life)
```

`alpha` 与 `half_life` 均是校准输入。只有显式 current-state 条目且查询表达当前/时效意图时才可使用新鲜度；
历史事实和已完成决定保持中性，时间未知也保持中性。新鲜度不与 importance 相乘，且不得降低高 authority 或 critical
条目。第一阶段以 revision age 为代理，不实现 usage/frequency 记忆模型。

## 排序 trace

`MemoryRankingTrace` 是提议中的进程内诊断值，独立于 RFC 0080 的 `MemoryRerankTrace`，在本 RFC 起草时尚未在
`master` 实现。它只保存有界、无正文的决策数据：

```text
策略标识与参数版本
基线固定池 identity 与基线 rank
质量调整顺序与有界位移
每项使用的维度/规则代码
有效性过滤计数与 unresolved-conflict 计数
membership_changed = false
```

无论 reranker 是否启用均可使用。它不改变公开 RRF `score`，不增加 HTTP 字段，也不复用“reranker 关闭时不存在”的
rerank trace。

## 与 recall sufficiency（#1556 / #1596）的兼容

默认关闭的 [RFC 1560](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/1560_recall_sufficiency_gate.md) / PR #1596
中的 recall sufficiency gate 可以用较宽松的 `AdmissionFloor` 发起额外、有界的搜索。
这种 expansion 是新的 recall round，不是由质量策略改变成员。

每个 round 均在该轮通道准入后、基线 RRF 前进行有效性过滤，然后只在该轮固定粗候选池内做有界重排，再运行既有的可选
listwise reranker。gate 接收 reranker 产生的 `result.hits`，保持 RFC 1560 现有的 gate 输入语义。本 RFC 不会延迟
rerank，不会替换为基线顺序候选，也不会改变 gate 算法、配置的最大轮数、admission floor、query embedding 复用、
成本 trace 语义或进程内 `RecallEffort` sink。质量关闭或保持中性时，行为与 RFC 1560/#1596 完全一致；启用质量后，
其有界重排可通过既有的 rerank 后 gate 输入被观察到，评测必须报告由此产生的 expansion 决策、最终候选池和成本差异。
`MemoryRankingTrace` 与 gate 的 aggregate effort trace 相互独立；评测还必须确认每次质量重排都保持传入该 round
reranker 的成员 identity。

## 近重复对齐

近重复对齐是有界的写端候选过程，不是破坏性 deduper：

```text
规范化内容相等
  → 有界 lexical candidates
  → 仅在目标 Memory 内进行可选 top-k semantic neighbors
  → 记录 relation/evidence increment 或创建 review proposal
```

规范化完全相等保留现有 no-op 行为。对 semantic neighbor，`tau_same`、`tau_similar` 等阈值只是校准输入。
语义关系不得自动把 importance 降到 `low`、停用 entry、合并正文或设置 `superseded`。

没有 embedding 的部署可收集确定性词法证据并创建 review proposal，但不得自动合并或丢弃同义改写。显式写入和确定性
adapter 可保留既有 idempotency 路径；批量导入可把有界检查延后到离线健康扫描。

## Recall 反馈

普通 search 保持只读：第一阶段不加锁、不写 recall event、不更新 `last_recalled_at`。第一策略只把 revision age
作为有限的活跃度代理。

后续可选择异步记录有界 recall event，再折叠进投影。ACT-R base-level learning 只为该后续设计提供动机：它是多个
练习时间的幂律和，不是本 RFC 单个 revision-age proxy。引入频率必须同时引入 provenance 保护，防止低 authority
条目仅因被频繁访问就获得影响力。

## L1：可恢复的自动停用

L1 默认关闭，必须支持 dry-run。仅当所有条件满足时，才可以停用：

1. 权威 manifest 与生命周期投影均为 active/current；
2. importance 不高于配置下限，初始仅 `low`；
3. 适用的保留区间已过期；
4. 未 pin、未保护、且未被用户显式 retire；
5. 不是等待明确 session/task 边界的 session tier；
6. 不存在会破坏 evidence lineage 的未解决入向关系或未处理派生视图。

L1 调用 `forget(..., reason="auto_decay:v1")` 并记录有界 action reason；它必须幂等，使用普通 scope lock/head-CAS。
`reactivate()` 恢复原 entry version，不产生新正文版本。用户操作优先：显式 restore 开启新生命周期区间，用户 revise
刷新派生时间/质量。

`session_end` 是独立的确定性生命周期事件，不是按年龄/重要性判断的 L1 决策。调用方关闭 session 或 task 时，`session` tier
可在满足相同保护和并发检查的前提下，以 `reason="session_end"` 停用，无需等待保留期或 importance 阈值；该操作仍可恢复。
L1 年龄路径不处理 session tier，避免 tier 行为与 low-importance 门槛冲突。

| Tier | 用途 | 生命周期行为 |
| --- | --- | --- |
| `session` | 调用方标记的任务/会话笔记 | 在明确边界以 `session_end` 停用；可恢复 |
| `short` | 临时 working note | 更短的配置保留期 |
| `long` | 长期约束、决定或历史事实 | 常规保护与保留路径 |

容量压力只创建有界清理**提案**，不触发自动压缩。“50 条新 low entry”或“60% inactive 且 1,000 条”等均为种子值。
校准必须绑定当前 Context Pack 形状：16 个 Memory candidates、最多 8 个注入项、调用方字节预算，以及实际
manifest 大小和写延迟。

## L2：经过 review 的语义维护

L2 是 RFC 0050 的受限、Memory 专用扩展。普通 Memory 写入仍直接提交；仅以下维护提案进入 review：

- **consolidation：** 保留证据的 revise 加冗余 entry 停用；
- **显式 supersession：** 保留两条 entry，附加 successor/reason，并使旧条目对普通当前状态检索无效；
- **promotion：** 将跨独立窗口反复出现的 working-note evidence 晋升为 durable Memory kind。

每个 proposal 都包含精确 pre-state identity、source/artifact evidence、关系或矛盾证据、拟议变更、派生视图处理
和策略版本。批准是唯一能生成结果 Memory Revision 的路径。推断关系必须 review；consolidation 必须保留证据并集及
专有名词、数字、日期；没有新证据时禁止反复“润色”正文。

L2 需增加 Memory maintenance operation record，记录 operation identity、获批 proposal identity、受影响 entry、
pre-state、产生的 revision/relation、派生视图影响和声明的补偿行为；必须明确列出无法回滚的影响。它不同于 L1 恢复，
也不同于 PR #1586 的 Experience recurrence ledger：Memory L2 不得复用或改写该 ledger、其 event 语义或 Experience
review routing。任何 Memory Candidate/API 扩展都在 #1586 之后单独分阶段进行，并保持 Experience/Skill 合约。

## 压缩与物理保留边界

本 RFC 不压缩或摘要权威 Memory entry 正文。它们是自包含、可精确引用的记录；重写可能丢失姓名、日期、数量和审计性。
本 RFC 区分：

1. 权威 entry body，本 RFC 不压缩；
2. 可丢弃的 derived view，后续设计可重建或退役；
3. Topic Memory summary，属于独立的组织层问题。

物理 tombstone/manifest compaction、法律保留、外部删除与跨 artifact 清理均不在范围内。#1425 管理这一更宽的边界。
inventory 可以报告有界 active/inactive count、manifest 增长和 automatic-action 类别，但不构成删除授权。

## 兼容性、持久化与 API 影响

- 现有 Memory body、revision、manifest、evidence、search identity、Handoff citation 与公开 RRF score 合约不变。
- 新的生命周期/质量数据只能是可重建投影或有界 action evidence，绝不是 entry body 内容。
- 第一阶段 HTTP、MCP、CLI 与 OpenAPI 不变。Source adapter contract 是实现前置条件，不是对 `SourceRef` 的隐式解释。
- 项目指令文件如 `CLAUDE.md` 和 `AGENTS.md` 是由文件自身作为权威的长期上下文，不受 Memory 自动生命周期控制；可参见
  [文件型记忆说明](https://github.com/vitoworleone/claude-code-handbook/blob/main/docs/manual/part-04-context/ch-10-memory-system.md)。
- 第一阶段不向 HTTP 暴露 ranking trace 或 lifecycle annotation；进程内 trace 不得仅为观测而持久化 Memory 正文。
- SQLite 与 OceanBase 的迁移/重建必须产出等价的投影状态和顺序。
- #1321 的追加写放大、存储布局、manifest 拆分和物理压缩不在本 RFC 范围内；本 RFC 只定义 Memory 的逻辑生命周期与检索质量边界。
- lifecycle inventory 不得静默向 #1586 引入的公开 `ScopeStats` 合约添加字段；任何公开统计 API 扩展必须单独进行兼容设计。

## 评测与校准

所有参数均为种子。在有以下证据前，不启用任何非中性默认值：

1. **质量收益：** 在相同 Context Pack 字节预算下，对比基线与固定池质量顺序的 Recall@k、MRR、答案/任务结果、延迟和成本。
2. **时间有效性：** 验证同一 `entry_id` revise 后，普通检索只返回当前 successor，而精确历史解析返回 predecessor 与原因。
3. **冲突安全：** 验证已知 inactive/superseded 不能进入 reranker 或工具 agent 的 Context Pack；时间未知/不可比较冲突保留并标注。
4. **候选密度：** 固定必要证据，逐步注入近重复/低价值记录，测量 top-k 退化，用于校准关系阈值与有界位移。
5. **L1 dry run：** 测量误停、保护、恢复、action 幂等性和入向 lineage 处理。
6. **长期回归：** 运行 Ledger-QA 形状的 revision 序列，同时评估“当前是什么”和“时间/revision T 时是什么”，并以最终任务/世界状态和检索指标评分。
7. **安全性：** 单独加入 provenance poisoning 和低 authority/高频访问案例，不能仅用相关性覆盖。
8. **来源类别占比：** 对接纳实质性不可信 Source 的部署，对比中性基线、已校准的 additive provenance 排序基线，以及
   opt-in 的有界低 authority 占比上限（固定候选池或最终 Context Pack）。必须保留合法 answer-bearing evidence 的路径，
   并报告攻击成功率、可信/不可信证据召回、citation 正确性、abstention 与误排除。没有明确威胁模型和留出集收益前，
   它不是默认策略。
9. **recall-gate 组合：** 在 FTS/vector/hybrid、reranker 开/关、recall gate 开/关的矩阵中，确认质量关闭或中性时，gate
   接收与 RFC 1560/#1596 相同的 rerank 后 `result.hits`、expansion 决策、最终候选池和 effort trace；启用质量后，确认每轮重排
   保持该轮 reranker pool 的成员 identity，并报告 gate 决策、最终成员、reranker 调用与 `added_generation_calls` 的任何变化。
10. **后端一致：** SQLite 与 OceanBase 上运行 conformance/rebuild，用检索、任务、安全、成本分开报告。
11. **Full-context 参考：** 单独报告完整材料注入结果，避免把模型阅读失败误归因于检索或生命周期策略。

评测遵循 RFC 0080/RFC 1229 边界：PowerContext 通过自身接口暴露真实行为和 trace；workload 数据、judge policy、
验收口径必须明确。竞品论文数值只是假设来源，不是产品验收阈值。

## 建议交付顺序

1. **Source 前置与中性投影：** 新增版本化 Source evidence declaration、生命周期投影重建、内部 annotation 形状和
   conformance fixture；排序仍关闭。
2. **第一阶段有效性与固定池排序：** 新增当前状态有效性过滤、`MemoryRankingTrace` 和默认关闭的 `±2` 重排；保持
   #1596 的逐轮“rerank 后再交给 gate”输入语义与 RFC 0080 reranker 行为。
3. **对齐与 L1：** 新增有界 near-duplicate relation/proposal、inventory、tier 和 dry-run L1。
4. **L2 review：** 单独分阶段实现 Memory Candidate/review 集成、operation record、consolidation、显式 supersession
   和 promotion；不得与 #1586 的 Experience recurrence 工作耦合。
5. **物理保留：** 只有 inventory 与评测证明存储问题后，才提出单独的 compaction RFC。

## 未来实现的验收标准

- 关闭所有生命周期选项时，写入语义、排序、公开合约和后端一致性与当前行为完全相同。
- importance、evidence strength、生命周期观察和 Source 声明都能从权威 revision 加精确、有界的操作证据重建，且不改变
  entry content hash。
- low-importance 条目仍可参与普通检索；只有显式或策略停用才会将其移出活跃候选面，`reactivate()` 恢复原版本且不产生新的正文版本。
- L1 只处理符合条件的 low、过期、未保护条目；记录 `auto_decay:v1`，支持 dry-run、审计、幂等和恢复。session tier 停用记录
  `session_end` 且可恢复。
- L2 批准前不能改变权威状态；批准的 consolidation 保留证据谱系，显式 supersession 保留新旧状态，未解决冲突不推断时间顺序。
- 近重复处理不得进行未经验证的语义合并或丢弃，相似关系也不得静默把 importance 降入自动停用门槛。
- 已知 inactive/superseded 条目在普通当前状态检索中必须于 RRF 和 reranker 前过滤；历史读取保留精确原文，未知冲突继续标注。
- 质量排序不得改变通道准入、传入某轮 reranker 的固定 RRF 池成员、gate 算法/配置或公开 RRF score；每轮仍须先 rerank，
  再由 gate 接收 `result.hits`；质量关闭或中性时必须保持 RFC 1560/#1596 行为。`MemoryRankingTrace` 必须能审计有效性和成员不变性。
- 评测计划必须覆盖 SQLite/OceanBase 的检索、任务、安全、成本和后端一致性，并包含 #1556/#1596 的联合 recall-gate 矩阵。

# 缺点

- Source authority/verification declaration 是新的 adapter-contract 职责，需要谨慎迁移。
- 派生投影、有效性过滤和双后端一致性增加实现与 conformance 复杂度。
- 校准不当时，即便有界重排也可能伤害原本相关的 RRF 排名，因此默认关闭且范围很小。
- L1 仍可能误停低频但有价值的 low entry；恢复只能降低、不能消除成本。
- L2 需要 reviewer 注意力和明确补偿设计，提案可能积压。
- 近重复分析消耗检索/索引资源；无 embedding 时不能安全自动做语义决定。

# 设计理由与替代方案

- **importance/decay 乘法加权：** 不采用。实际 RRF 分差很小，4x 权重会主导粗排；将新鲜度与重要性相乘还会压低长期约束。
- **在 coarse 截断前按质量排序：** 第一阶段不采用。它会改变 reranker 成员，需要独立实验、trace 语义和更宽的召回设计。
- **模型 importance 打分：** 不采用。跨 provider/时间漂移，难以复现和测试。
- **自动语义去重或取代：** 不采用。相似度和相关性不足以证明破坏性的生命周期变化。
- **时间知识图谱：** 暂不采用。会引入第二权威和超出 revisioned Memory 的大型 schema。
- **正文压缩：** 不采用。伤害精确 citation，不能解决活跃池干扰。
- **纯 ACT-R 衰减：** 不采用。其 usage-event 幂律机制无法由单一时间戳表达，作为历史事实通用策略也不安全。

# 先例

- [CoALA](https://arxiv.org/abs/2309.02427) 区分 working、episodic、semantic、procedural；本 RFC 借鉴操作分离，而非新建认知架构。
- [MemGPT / Letta](https://www.letta.com/) 展示了有界 in-context memory 与后台 consolidation；本 RFC 将 consolidation 放在关键读路径之外。
- [mem0 memory types](https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/memory-types.mdx) 启发 session/short/long 时间尺度；其
  [recency 讨论](https://mem0.ai/blog/memory-decay-for-long-running-agents-how-recency-aware-ranking-fixes-retrieval-staleness)
  只作为 query-gated freshness 实验的背景，不构成自动删除依据。
- [Generative Agents](https://arxiv.org/abs/2304.03442) 支持将 relevance、recency、importance 分开；不支持照搬 LLM 自由分数或把创建时间当 usage history。
- [ACT-R base-level learning](https://doi.org/10.1037/0033-295X.111.4.1036) 为未来 recall-event 评测提供动机，而不是第一阶段 revision-age proxy。
- [VoiceMem](https://arxiv.org/abs/2608.26005) 只支持候选密度假设，不作为本 RFC 新鲜度公式的依据。
- [Revoked](https://arxiv.org/abs/2609.08258) 支持在检索时确定性强制已知有效性。
- [Utility Under Attack](https://arxiv.org/abs/2608.21230) 支持来源投毒和非默认的来源类别占比评测；不支持从正文推断
  Source authority，也不支持把占比上限直接设为默认。
- [Selective Memory](https://arxiv.org/abs/2603.15994) 与 [ProMem](https://arxiv.org/abs/2601.04463) 支持可逆退役和后续对齐/验证，不支持未评审语义删除。
- [Retain or Consolidate?](https://arxiv.org/abs/2607.17545) 支持在证据已满足预算时保留原始权威信息，而非引入自动正文压缩。
- [Rate–Distortion Theory for Agent Memory Compaction](https://arxiv.org/abs/2607.08032) 提醒不可逆、查询未知的预查询丢弃风险及统一预算评测，不构成此处正文压缩授权。
- 提案方补充背景：[1](https://mp.weixin.qq.com/s/UDkGQvutJn-OQg0KunOqzw)、
  [2](https://mp.weixin.qq.com/s/eijn2Cg3TSQqrqy2UU4fog) 和
  [3](https://mp.weixin.qq.com/s/sQyuqmnl5EHMb-l356bFVQ)。这些链接只用于记忆组织与生命周期讨论，
  不作为数值默认值或验收阈值的依据。

# 未解决问题

- 哪个 `MemoryEvidenceDeclaration` 枚举与注册方式最适合内置和第三方 Source adapter，且可重建？
- 应由哪个显式字段或调用方合约将 Memory entry 分类为 current-state material？
- 第一阶段精确 revision 路径之后，是否需要公开历史读取 API？
- 哪种有界 rank displacement 映射在校准 workload 上稳定且有收益？
- L2 Memory proposal 应扩展通用 Candidate family，还是使用独立的 Memory maintenance proposal？
- 每类 L2 操作及派生视图可实现何种补偿？
- inventory 可用后，哪些物理保留指标足以触发独立 compaction RFC？

# 后续可能性

- 异步、隐私有界的 recall-event 投影，以及 authority-aware、受 ACT-R 启发的特征。
- 面向用户的 pin/protection 控制和显式生命周期检查。
- 公开、脱敏的历史读取与 lifecycle annotation API。
- 经过独立评审、带显式 effective-time interval 的时间关系模型。
- 若测量证明必要，再新增 Topic-level derived summary 与物理 manifest archive/compaction RFC。
