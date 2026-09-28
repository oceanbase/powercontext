- Proposal Name: `recall_sufficiency_gate`
- Start Date: 2026-09-10
- Status: Proposed
- RFC PR: [oceanbase/powercontext#1560](https://github.com/oceanbase/powercontext/pull/1560)
- Tracking Issue: [oceanbase/powercontext#1556](https://github.com/oceanbase/powercontext/issues/1556)
- Related RFCs: [RFC 0028](0028_context_pack.md)、[RFC 0080](0080_memory_search_reranking.md)、
  [RFC 0081](0081_end_to_end_evaluation_architecture.md)、[RFC 1229](1229_unified_workloads_and_long_horizon_memory_evaluation.md)、
  [RFC 1489](1489_prepared_context_text_assembly.md)

# Summary

本 RFC 为 Runtime 的 `prepare_context` 增加内部的**召回充分性闸门**与**有界扩展**。

当前 `prepare_context` 只对参与类别执行一次搜索，把命中结果拟合进调用方的 `max_bytes` 预算后渲染。当这一次搜索
召回不足时，操作仍然正常返回，只是交付更少的条目，或一个正常的空结果。

本 RFC 让 Runtime 在渲染之前，用低成本方式判断候选集对当前 query 是否**充分**；不充分时，最多执行两轮受控扩展。
两轮都降低施加于各类别搜索**已经取回**的候选上的**准入下限**，然后在**同一个**调用方给定的预算内完成选择。

闸门不调用模型，不改变调用方预算，不改变公开 `PreparedContext` 契约，并在任何异常时退化为当前行为。

有三条性质使该提案可以被安全评估：

- **扩展不可能破坏 Builder 不变量。** 跨轮合并后的候选会沿用它已经用于截断第 0 轮的那两个分配器，重新选择到各类别
  既有的候选上限之内，因此跨轮并集永远不会超过 `build_scopes_result()` 所强制的那道上限
  （`prepared_context.py:164-169`）。
- **不扩展的运行与今天逐字节相同。** 无扩展路径完全未变，这就是回归保证。
- **交付体积永不越过 `max_bytes`。** 扩展可以使用第 0 轮候选集未用满的预算，因此 `content_bytes` 可以在这个上限
  之内**变大**，但永远不能越过它。

# Motivation

RFC 0028 有意把内部原因（没有 Memory、没有命中、异常）对调用方合并为同一个正常空结果。该决定继续有效，本 RFC
不重新讨论。**合并"对外报告的原因"与"完全没有恢复路径"是两件事。**

具体地，当相关证据恰好落在第一轮召回之外——措辞不同的条目，或搜索**已经取回**、却被准入下限丢弃的条目——当前会
得到一份静默变薄的上下文，每个集成都观察到同样的变薄。RFC 1489 让调用方可以控制参与类别、类别顺序和每类条数
上限，但没有回答：当参与的类别合起来返回的内容太少时，Runtime 应该做什么。

当前流水线在结构上就是单趟的：

```text
每个参与类别各搜索一次
  -> 按 Builder 的候选上限截断
  -> 选择并拟合到 max_bytes
  -> 渲染
```

现有实现中有两个事实，决定了"有界地再看一次"究竟能在哪里改变候选集：

- **第 0 轮已经把上限允许的量全部取满：余量在搜索结果内部，而不在它旁边。** 每个类别的搜索上限是
  `PreparedContextBuilder` 上的固定常量（`prepared_context.py:103-110`：`memory_candidate_limit = 16`、
  `topic_memory_candidate_limit = 8`、`experience_candidate_limit = 8`），超过即抛出 `PreparedContextInvariantError`
  （`prepared_context.py:164-169`），而 Runtime 请求的就是这些值本身（Memory 与 Experience 见
  `application.py:742-743`，Topic Memory 见 `application.py:761`）。因此 `SearchMemoryRequest.limit` **完全没有**余量：
  提高它在结构上就是空操作。未被动用的余量在下一层——每个类别向其后端请求的候选数已经是自身 limit 的四倍左右：
  Memory 为 `candidate_limit = max(coarse_limit * 4, 32)`（`service.py:457`，在 prepare 路径的 limit 16 下即 64），
  Experience 为 `limit * 4`（`sqlite/experience_index.py:156`、`oceanbase/experience_index.py:114`），Topic Memory 为
  `min(MAX_TOPIC_MEMORY_SEARCH_LIMIT, limit * 4)`（`persistence/topic_memory.py:441`）；而这个候选池在融合之前先被
  准入下限过滤——词项证据门槛要求覆盖足够多的不同 query 词项（`search.py:104`，各类别共用），向量通道还有固定的
  余弦基线 `0.3`（`memory/fusion.py:29`、`topic_memory/fusion.py:33`）。偏薄是**这道下限**造成的，不是 limit 造成的，
  而第 0 轮从不重新审视它。
- **切换 `mode` 同样不是扩展杠杆。** `_recall_scope` 硬编码了 `mode="auto"`（`application.py:849`），但 `auto` 在
  hybrid 通道可用时已经是 `hybrid`（`service.py:602-607`），所以能同时服务两个通道的 Scope 本来就在这么做。而在
  没有向量部署的 Scope 上，显式传 `hybrid` 不会优雅降级：它会抛出 `CapabilityNotSupportedError`
  （`service.py:598-601`）。

仅凭候选池的**容量**无法让闸门判断是否是下限造成了偏薄，因为后端本身稀疏与候选池被大量过滤在交付结果上完全一样：
`MemorySearchResult` 只暴露 `mode`、`hits` 与可选的 `rerank` trace（`memory/models.py:165-169`），而 Experience 与
Topic Memory 的召回入口返回的是裸的命中元组。**没有任何地方统计每个类别"取回了多少"与"通过准入了多少"。** 因此本
RFC 把这些计数作为仅进程内的值引入（见**所需内部传递机制**）；没有它们，下表中的第一个信号不可计算，也就不能被
声称。

第三个观察催生了本 RFC 的报告部分：当前**没有任何地方统计输给预算的条目**。非 assembly 路径下 `_fit_entry` 按
`max_entry_content_bytes` 截断，放得下就返回（`prepared_context.py:461`）；它会整条丢弃，且有两条**彼此不同**的路径
——原文短于 `_MIN_TRUNCATED_CONTENT_BYTES` 时（`prepared_context.py:462-463`，常量值 `64` 见 `:39`），以及在此之外、
随后的截断搜索找不到任何放得下的渲染结果、只能返回仍为 `None` 的 `best` 时（`prepared_context.py:465-485`，`:485`
处 `return best`）。assembly 路径下 `fit_context_text_item` 同理，在截断搜索找不到可用内容、或最佳结果低于 64 字节的
正文下限时整条丢弃（`prepared_text.py:103-104`，常量见 `:35`）。`truncated` 会逐条渲染，但截断条数与整条丢弃数都没有
被计数。让它们可计数是一个很小的改动，也是诚实评估本功能的前提。

本 RFC 并不是主张"召回越多越好"。它主张的是：**有条件的**额外召回——只在第一轮看起来偏薄时才付出代价——
值得在现有 workload 基础设施下被评估。

# Guide-level explanation

## Mental model

```text
Stage A  召回
           对参与类别搜索（第 0 轮）
           在第 0 轮候选集上做一次预算探测          （纯逻辑，无 I/O）
           RecallSufficiencyGate.assess(families, query, budget, policy)
             充分                        -> Stage B
             不充分且已提交轮次 < 2       -> 有界扩展后再次搜索
             不充分且已提交轮次 == 2      -> 带着现有结果进入 Stage B
Stage B  选择 + 分节组装 + 预算拟合 + 渲染        （不变）
Stage C  报告召回代价与省略情况                  （进程内）
```

扩展只能改变**哪些候选参与竞争**。它不改变输出预算、信任包装、引用形式，也不改变调用方选定的类别集合。

## 闸门看什么

闸门刻意保持低成本且无模型。下表中的每个信号，要么第 0 轮结果已经携带，要么来自本 RFC 新增的计数器；没有任何一个
需要模型调用。

| 信号 | 从哪来 | 检测什么 |
| --- | --- | --- |
| 各类别、各通道的取回数与准入数 | 新增的准入计数器 | 某个类别的准入下限把取回的内容几乎全部丢弃了。 |
| 准入候选数与该类别 Builder 上限之比 | 第 0 轮命中与 `prepared_context.py:103-105` | 该类别还有空间让后续轮次贡献；已饱和的类别没有。 |
| Top-1 分数及其与准入候选均分的差距 | 仅限带分数的类别：Memory（`MemoryHit.score`）与 Topic Memory（`TopicMemorySearchHit.score`） | 一条看似可用的命中被噪声包围，或根本没有明显胜出者。 |
| 头部候选在 analyzer 词元空间中的词项覆盖度 | `analyze_text` / `fts_query_requirements`（`search.py:78`） | 命中只是靠停用词或某一个共现 token 匹配上的。 |
| 至少返回一条准入候选的参与类别数量 | 第 0 轮结果 | 选了三个类别，只有一个有结果。 |
| 类别内的不同证据身份数 | 类别专属身份（见下表） | 多条候选其实是同一份证据。 |
| 第 0 轮的拟合是否被预算卡住 | 预算探测结果 | 偏薄输出是由 `max_bytes` 造成的，而不是召回。 |

**Experience 没有分数。** `ExperienceSearchHit` 只有 `artifact_ref` 与 `content`
（`artifacts/experience/search.py:26-30`），因此基于分数的信号只对带分数的类别适用。Experience 只贡献它的准入计数、
它相对自身上限的候选数，以及它的"有结果"位。

**证据身份是类别专属的。** 一次 Memory 搜索返回的多个 `MemoryHit` 共享同一个 `memory_ref` Artifact revision，因为一个
Memory Revision 装有多个条目；真正独立的证据单位是条目，由 `entry_id` 与 `entry_version_id` 标识
（`memory/models.py:142-150`）。因此统计"不同 Artifact revision 数"会把任意 Memory-only 结果都压成一个来源。闸门
必须使用：

| 类别 | 证据身份 |
| --- | --- |
| Memory | `(memory_ref, entry_id, entry_version_id)` |
| Experience | `artifact_ref`（`ArtifactRef` revision） |
| Topic Memory | `artifact_ref`（`ArtifactRef` revision） |

阈值是部署配置，不是请求参数，并以版本形式记录在 trace 中，使一次运行可以被复现。

## 扩展做什么

只有三个类别是可搜索的：`memory`、`experience` 与 `topic-memory`。`profile` 是可选的 section 类别
（`runtime/models.py:195`），但它通过 `profiles.latest` 读取，从不执行搜索、也不经过准入过滤
（`application.py:752-759`），因此没有下限可降。**Profile 永不扩展**，只选中 `profile` 的请求完全不会触发扩展。

| 轮次 | 动作 | 前置条件 |
| --- | --- | --- |
| 1 | 在配置的扩展下限范围内，降低施加于各可搜索类别搜索**已经取回**的候选上的准入下限：词项证据要求（`search.py:104`）与余弦基线（`memory/fusion.py:29`、`topic_memory/fusion.py:33`）。 | 第 0 轮判定不充分。 |
| 2 | 把准入降到 policy 下限，接受当前最好的证据。 | 第 1 轮已提交且第 1 轮判定不充分。 |

第 2 轮没有更多可选动作。特别地，**提高 `memory_rerank_candidate_limit` 不是扩展动作**：`MemoryService` 用这个界
来决定后端请求的规模，而不只是对已有候选池做重排（`coarse_limit` 见 `service.py:452`，随后
`candidate_limit=max(coarse_limit * 4, 32)` 见 `service.py:457`）。把它从 30 提到 100 会把后端候选池从 120 扩到 400，
从而违背"候选池不变"这一使代价论证成立的前提。这里予以否决；如果将来确实需要，它属于一个独立的召回扩展提案。

**代价是累计有界的，不是逐轮单调递增。** 轮数上限为二，因此一次 prepare 每个参与的可搜索类别最多执行三次搜索。
本 RFC **不**声称第 2 轮严格比第 1 轮更贵：在关闭 rerank、候选池大小相同的情况下，第 2 轮只是用更低的下限重复同样的
工作，其增量代价与第 1 轮相同。真正有意义的界是累计界，完整表述见下文的代价模型。

**扩展绝不提高 `limit`，也绝不切换 `mode`**，理由见 Motivation：第 0 轮已经按 Builder 上限请求了每个类别，而
`mode="auto"` 在 hybrid 可用时本来就是 `hybrid`。

**扩展绝不增加类别。** RFC 1489 规定 `assembly.sections` 决定哪些类别参与，未被调用方选择的类别既不执行召回、
也不分配输出预算。静默搜索一个未选择的类别会违反该契约，因此类别成员不属于扩展范围。调用方完全省略
`assembly` 时，Runtime 沿用现有的默认类别选择，同样不扩展。

**对于第 0 轮已经填满自身上限的类别，扩展是空操作。** 因为合并后的集合以第 0 轮为前缀（见**轮次之间**），一个在
第 0 轮就把候选上限填满的类别无法再接纳后续轮次的候选。闸门的"准入数 vs 上限"信号会报出这一点，trace 会把该轮记成
"什么都没改变的一轮"。这种情形下的约束在下游——是预算，而不是召回——预算探测会给出结论。

## 可以观测到什么

沿用 RFC 0080 `rerank` trace 的先例，闸门结果留在进程内，**不**进入 HTTP v1 响应。

`RecallEffort` 由扩展循环产出，而循环位于 `ScopedContextApplication._prepare`（`application.py:727`），不在 Builder 内。
因此它**不**挂在 `PreparedContextBuild` 上：`_prepare` 返回的是 `build.context`（`application.py:812`），构建结果的其余
部分被丢弃，所以在那里新增字段除了可选召回 token 估算器（`application.py:794-796`）之外，对所有消费者都不可见。
替代方案是让 Runtime 增加一个可选 sink，完全对齐已经存在的召回 token 估算器：

```python
RecallEffortSink: TypeAlias = Callable[[RecallEffort], Awaitable[None]]

class Runtime:
    ...
    # 与 recall_token_estimator 同样的配置方式（composition.py:511）；默认为 None。
    self._recall_effort_sink: RecallEffortSink | None = recall_effort_sink
```

`_prepare` 在构建之后 await 该 sink，异常按与估算器失败完全相同的方式吞掉并记录
（`application.py:794-808`）。未配置 sink 的调用方不付出任何代价。

```python
@dataclass(frozen=True)
class AdmissionCounts:
    family: str                       # "memory" | "experience" | "topic-memory"
    scope_id: str
    retrieved: int                    # 后端返回、尚未准入的数量
    admitted: int                     # 通过准入下限的数量

@dataclass(frozen=True)
class RecallEffort:
    policy: str                       # 带版本的 policy id，如 "powercontext.recall-gate.v1"
    assessment: str                   # 最终闸门原因，如 "sufficient" | "thin-candidates" | "weak-top-1"
    rounds: int                       # 实际执行的搜索轮次：1 + len(expansion_actions)，即 1..3
    expansion_actions: tuple[str, ...]  # 仅已提交的轮次；最多 ("admission", "policy-floor")
    candidates_by_round: tuple[int, ...]  # 每一轮交给 Builder 的候选数；len == rounds
    admission_by_family: tuple[AdmissionCounts, ...]  # 在最后执行的轮次中测量
    added_embeddings: int             # 扩展轮次额外付出的 query embedding 次数
    added_generation_calls: int       # 扩展轮次额外付出的 RFC 0080 rerank 调用次数
    truncated_items: int              # 已交付但被截断；当前未计数
    dropped_items: int                # 两条拟合路径上整条省略的总数；当前未计数
    dropped_below_min_bytes: int      # dropped_items 的子集：低于 _MIN_TRUNCATED_CONTENT_BYTES / _BODY_BYTES
    dropped_no_fitting_truncation: int  # dropped_items 的子集：没有任何渲染能放下
```

`rounds` 统计的是**实际执行的搜索轮次**：跑了第 0 轮加两轮扩展的 prepare 报 `rounds: 3`，只扩展一次报 `rounds: 2`。
`dropped_items` 等于 `dropped_below_min_bytes + dropped_no_fitting_truncation`，因此读者可以区分"预算放不下这条"
与"这条太短，无法截断进剩余空间"。若把两者合成一个标注为"整条输给预算"的计数器，就会误报第二种原因，所以两种原因
分别报告，`_fit_entry` 的两条 `None` 路径（`prepared_context.py:462-463`、`:465-485`）与 assembly 路径
（`prepared_text.py:103-104`）也被区分而不是合并。

后四个字段是新增的可观测性，不是新行为；当前截断与丢弃都没有被计数，而没有它们就无法把"我们找到了证据"与
"找到了但预算吃掉了它"区分开来——当一次扩展什么都没改变时，正是这个区分决定了你如何解释结果。

`context.build` stage（`application.py:766-793`）还可以额外携带聚合计数器（`recall.rounds`、`recall.assessment`、
`recall.truncated_items`、`recall.dropped_items`）。RFC 0028 允许指标记录"内部 search mode 和聚合选择计数"
（`docs/en/rfcs/0028_context_pack.md:521-522`），而这些正是聚合计数：不含 query 文本、不含条目身份，也不做逐条归因。

## 示例

Codex Hook 用默认预算请求上下文，第一轮只返回一条很弱的 Memory 命中。

```http
POST /v1/context/prepare
Content-Type: application/json

{
  "scope_id": "project:demo",
  "query": "修改 HTTP API 契约后，如何验证？",
  "max_bytes": 8000
}
```

第 0 轮从 64 条后端候选池中返回三条 Memory 候选，其中一条分数可用；准入从 64 条里只放进三条，远低于 Memory 的
上限 16，因此该类别还有增长空间。预算探测发现仍有未用字节、且没有整条丢弃，所以这是**召回**偏薄而不是**预算**偏薄。
闸门判定为 `weak-top-1`，扩展一次（降低准入下限），第 1 轮又放进四条候选——它们本就在第 0 轮搜索已取回的候选池里，
只是被当时的下限丢弃了。随后选择与渲染完全按现有逻辑进行，仍在同样的 8000 字节内。

如果第 1 轮没有贡献任何新候选，prepare 会交付第 0 轮的结果——也就是今天的行为——`RecallEffort` 会报
`rounds: 2`、`expansion_actions: ("admission",)`，而 `candidates_by_round` 的两个数值持平，这正是 `truncated_items` 与
`dropped_items` 让它可以被解释的那种情形。

## 本 RFC 做什么、不做什么

| 做什么 | 不做什么 |
| --- | --- |
| 用无模型信号判断充分性 | 在组装阶段引入模型调用（RFC 1489 要求组装无模型调用） |
| 最多扩展两轮，累计代价有界 | 无限重试或循环 |
| 在既有候选上限内加大搜索力度 | 突破 `memory_candidate_limit` / `experience_candidate_limit` |
| 保持调用方 `max_bytes` 为唯一输出预算 | 改动 `PreparedContext(schema, status, content, content_bytes)` |
| 把合并后的集合重新选择到各类别上限内 | 让跨轮并集破坏 Builder 不变量 |
| 在 `max_bytes` 之内使用未用预算接纳可用证据 | 交付超过 `max_bytes` 的内容 |
| 通过进程内 sink 报告代价 | 改动 HTTP v1 响应 |
| 任何错误都退化为当前行为 | 让扩展成为新的失败模式 |

# Reference-level explanation

本文档中所有文件与行号引用都对齐到 `5ecfad187b4b303504bfd3a36251531b0f4fe1d0` 的 `origin/master`，并沿 prepare 路径
从 `_prepare` 一直追溯到每个准入点。

## 当前行为

`PreparedContextBuilder`（`src/powercontext/builtin/runtime/prepared_context.py`）声明了各项上限：

```python
memory_candidate_limit = 16
topic_memory_candidate_limit = 8
experience_candidate_limit = 8
candidate_limit = memory_candidate_limit
entry_limit = 8
topic_memory_entry_limit = 8
experience_entry_limit = 2
max_entry_content_bytes = 2000
```

`build_scopes_result()` 在某个类别超过候选上限时抛出 `PreparedContextInvariantError`（`prepared_context.py:164-169`），
随后在 `request.assembly` 存在时走 `_build_text()`，否则交错、拟合、渲染。没有条目能放入时返回
`PreparedContext(status="empty", content=None, content_bytes=0)`；否则返回 `status="ready"` 及渲染内容与 UTF-8
长度。渲染长度超过 `request.max_bytes` 时抛出 `PreparedContextInvariantError("output-budget")`
（`prepared_context.py:207-208`）。`PreparedContextStatus` 只有 `"ready"` 与 `"empty"` 两个取值
（`runtime/models.py:58`）。

`PrepareContextRequest` 含 `query`、`max_bytes`（512–32768，默认 8000）和可选 `assembly`。
`SearchMemoryRequest` 含 `query`、`limit`（默认 10）、`mode`（`fts` / `vector` / `hybrid` / `auto`，默认 `auto`）与
`tag_filter`。两个请求都**没有**时间窗或 as-of 参数。prepare 路径从不使用 `limit` 的默认值：`_prepare` 传的就是
Builder 的上限本身（`application.py:742-743`、`:761`），与上文一致。

## 候选集在哪里被削薄

每个参与类别的搜索分两阶段。阶段一是取回：向后端请求的候选数约为该类别 limit 的四倍（`service.py:457`、
`sqlite/experience_index.py:156` 与 `oceanbase/experience_index.py:114`、`persistence/topic_memory.py:441`）。
阶段二是准入：取回的候选在融合之前先被过滤——词项证据要求，即候选必须覆盖足够多的不同 query 词项
（`search.py:104`，所需数量在 `fts_query_requirements` 中推导，`search.py:78`）——Memory 在 `memory/fusion.py:34-40`
应用，Topic Memory 在 `topic_memory/fusion.py:102-107` 应用，Experience 在其索引内部应用
（`persistence/experience_index.py:309`）；向量通道另有余弦基线 `0.3`（`memory/fusion.py:29`、
`topic_memory/fusion.py:33`，分别在 `memory/fusion.py:43-52` 与 `topic_memory/fusion.py:110-117` 应用）。

一个已经是交付上限四倍、却被过滤到几乎无剩余的候选池，正是闸门要检测的情形。因此扩展动作被定义为**准入下限**：
在这条路径上，它是唯一的、既能放宽、又不会突破 Builder 不变量、不会触发能力错误、也不必改动公开请求的边界。

**Runtime 当前并不拥有这道边界，因此本 RFC 必须明确传递机制，而不能默认它已经存在。** `_prepare` 调用
`MemoryService.search`（`service.py:398`）时只传 `query`、`memories`、`limit` 与 `mode`（`application.py:845-850`）；
词项与向量下限是在这次调用**内部**应用的（`service.py:464-465`），另外两处在 Experience 索引与 Topic Memory 的融合
代码里。因此降低下限需要在这三个入口上新增内部参数，下一节展开。

## 新增组件

1. **`RecallSufficiencyPolicy`** —— 冻结的、带版本的值对象，持有阈值、最大轮数、每轮扩展的准入下限，以及预算探测的
   提示规模上限。由 Runtime 配置构造；功能关闭时默认值保持当前行为。
2. **`RecallAdmissionPolicy`** —— 传给每个可搜索类别搜索、用于覆盖其下限的值：可选的 `required_matches`
   （默认取 `fts_query_requirements` 推导值，`search.py:78`）与可选的 `min_semantic_similarity`
   （默认 `0.3`，`memory/fusion.py:29`、`topic_memory/fusion.py:33`）。传 `RecallAdmissionPolicy()`——两个覆盖都为
   `None`——精确复现今天的行为，第 0 轮就是这么做的。
3. **`AdmissionCounts`** —— `(family, scope_id, retrieved, admitted)`，每个可搜索类别、每个 Scope、每一轮一个。
4. **`RecallSufficiencyGate`** —— 纯函数。输入是该轮各类别的视图、query、policy 与一个预算视图：
   `assess(*, query, families, budget, policy) -> GateAssessment`。无 I/O、无模型调用、除候选自身已携带的信息外不访问
   时钟。返回 `sufficient`，以及原因与产生该判断的信号取值。
5. **`RecallBudgetView`** —— `max_bytes` 连同一次**预算探测**得到的计数：用 Builder 已有的纯拟合代码在该轮候选集上
   跑一遍，丢弃渲染结果，只保留 `delivered_items`、`truncated_items`、`dropped_items` 与 `unused_bytes`。闸门需要它
   来区分"预算造成的偏薄"与"召回造成的偏薄"；没有它，下面 512 字节的边界条件无法判定。
6. **`RecallExpander`** —— 纯函数 `(round, policy) -> SearchPlan`，`SearchPlan` 只携带下一轮的两项准入覆盖。它不涉及
   类别，因此不可能违反 assembly 契约，也绝不设置 `limit`、`mode` 或 rerank 候选界。
7. **`RecallEffort`** —— 上文描述的 trace 值，交付给 Runtime 的可选 sink。

以上都位于 `src/powercontext/builtin/runtime/` 下。闸门、扩展器与预算探测复用纯逻辑，可以直接测试，不需要数据库。

## 所需内部传递机制

计数与下限覆盖必须到达三个准入点。以下没有任何一项是 HTTP 变更。

**Memory。** `MemoryService.search` 本来就已经把边界两侧的东西都物化了——后端的 `channels`，以及随后的
`admitted_fts` 与 `admitted_vector`（`service.py:463-465`）。它新增一个可选关键字参数
`admission: RecallAdmissionPolicy | None = None`，用于替换推导出的词项匹配数与 `0.3` 余弦基线，并报出
`retrieved = len(channels.fts) + len(channels.vector)` 与
`admitted = len(admitted_fts) + len(admitted_vector)`。计数通过 `MemorySearchResult`（`memory/models.py:165-169`）上的
仅进程内字段暴露。HTTP 契约不受影响，因为响应是由 `search_response`（`server/mapping.py:788`）从
`MemorySearchPage`（`runtime/models.py:183-189`，构造于 `application.py:1738` 与 `:1761`）构建的，而它显式枚举自己的
字段；这些计数器必须留在 `MemorySearchPage` 之外、也不进入 `openapi/powercontext.yaml`，因此不需要执行
`make api-generate`。

**Experience。** Runtime 通过 `experience_recall` 回调召回 Experience（`application.py:862-878`），它当前返回裸的
`tuple[ExperienceSearchHit, ...]`。它需要改为返回一个同时携带命中与其 `AdmissionCounts` 的结果值。下限应用在行解码器
`experience_search_hits`（`persistence/experience_index.py:298-323`，准入在 `:309`）中，该函数在准入满 `limit` 条时
就会停止（`:321-322`）；要报出 `retrieved`，它必须统计检查过的行数，而不只是保留的行数。该回调签名属于 Runtime 的
构造面（`composition.py:486`、`application.py:2257`）。Skill 路径（`persistence/experience_index.py:338`）不会从
`prepare_context` 抵达，因为 `ContextAssemblySection.family` 只接受 `memory`、`experience`、`profile` 与
`topic-memory`（`runtime/models.py:195`），所以它不需要同样处理。

**Topic Memory。** `_topic_memory_hits`（`application.py:890`）同样返回裸元组；它需要改为返回一个同时携带命中与其
`AdmissionCounts` 的结果值，计数在 `_admit_fts` 与 `_admit_vector`（`topic_memory/fusion.py:102-117`）两侧测量。

**第 0 轮行为不变。** 第 0 轮传 `RecallAdmissionPolicy()`，忽略下限覆盖；唯一区别是它现在会观测并返回计数。功能
关闭时，计数器根本不会被采集。

## 循环放在哪里

循环属于当前执行各类别搜索、随后调用 `PreparedContextBuilder.build_scopes_result()` 的 Runtime 层，即
`ScopedContextApplication._prepare`（`application.py:727`）与 `_recall_scope`（`application.py:814`）。正如 Builder
的 docstring 所述，Builder 本身保持无 I/O、无持久化、无 rerank；它接收胜出轮次的候选，与今天完全一致。任何一轮都
需要 `_recall_scope` 已经获取的那套 per-scope context 与锁（`application.py:827-830`），因此后续轮次要重新进入同一道
守卫，而不是跨轮持有它。

## 轮次之间

Builder 只接收一份候选集，所以必须说清它来自哪些轮次，而且必须在**不破坏** Builder 所强制的那道不变量的前提下说清。

**合并规则。** 在每个类别、每个 Scope 分组内，合并后的集合是：第 0 轮的候选，之后按轮次顺序追加后续各轮贡献的、
此前不存在的身份，并按该类别的证据身份去重（见**闸门看什么**）。身份冲突时保留较早轮次的那一条。以 `memory` 为例：
`merged = round0_hits + (第 1 轮的新身份，按融合顺序) + (第 2 轮的新身份，按融合顺序)`。

**重新选择规则。** 合并后的集合随后通过**已经用于截断第 0 轮**的那两个分配器——`_limit_memory_candidates`
（`application.py:747`）与 `_limit_experience_candidates`（`application.py:748-751`），它们把类别上限按 scope 分组做
round-robin 分配——Topic Memory 则保留它自己的单 Scope 上限检查。因为第 0 轮是合并序列的前缀，截断只可能移除后续轮次
新增的候选；第 0 轮产生的候选永远不会被扩展挤掉。

由此得到两个必须成立的性质，而不是偶然的副作用：

- **Builder 不变量按构造成立。** 重新选择之后，各类别总量处于或低于 `memory_candidate_limit`、
  `topic_memory_candidate_limit` 与 `experience_candidate_limit`，因此 `prepared_context.py:164-169` 的检查不可能被
  扩展触发。这正是把合并定义在同一批分配器上、而不是定义成无界并集的原因：两轮各自合法、各含 16 条 Memory 命中的
  结果可能只差一条候选，而它们去重后的 17 条并集会让 `build_scopes_result()` 抛出
  `PreparedContextInvariantError("memory-candidate-limit")`。
- **已饱和的类别不可能增长。** 如果第 0 轮已经填满某类别的上限，合并集合会被截断回第 0 轮的候选，该类别的后续轮次不会
  改变任何东西。扩展只能给那些第 0 轮准入数低于自身上限的类别增加候选——这正是闸门在做决定之前读取的信号，也是"空
  操作扩展"属于正常且会被报告的结果的原因。

因此，某一轮返回的候选**少于**上一轮是正常且预期的，不构成降级触发条件。

## 持久化边界

RFC 0028 规定 Context Pack **不写数据库或文件，不进入 Source journal 或 Memory evidence，不启动 scheduler 工作，也不作为
telemetry 持久化**（`docs/en/rfcs/0028_context_pack.md:518-519`，中文对应处
`docs/zh/rfcs/0028_context_pack.md:464`）。本 RFC 保持在该边界之内。

`RecallEffort`——包括 `truncated_items`、`dropped_items` 与各项准入计数——是在单次 `prepare_context` 调用内计算的值。
它不写数据库行，不是 Source observation 或 Memory evidence，不启动 scheduler 工作，也不作为 telemetry 持久化。它交给
部署方自愿配置的进程内 sink；未配置 sink 的调用方不付出任何代价，也不产生任何副作用。可能出现在 `context.build`
span 属性上的聚合计数器，也限于 RFC 0028 已经允许指标记录的那几类（`docs/en/rfcs/0028_context_pack.md:521-522`）。

这正是本提案**刻意不**包含跨会话存活的逐条召回结果台账的原因——尽管那才是更有用的信号。从 prepare 路径持久化
"这条被选中"或"这条输给了预算"，需要修改 RFC 0028 的 write-free 条款，那是一次基础契约变更，且正在别处决定：#1554
提出的正是这个选择，而 maintainer 在那里的意见是首版保持 prepare 只读。因此本 trace 的任何跨会话版本都需要它自己的
RFC。

`truncated_items` 与 `dropped_items` 是单次调用内的聚合计数——绝不是逐条归因，也绝不是对某个条目的评价。这一区分是
刻意的，因为 #1554 已经裁定：条目输给 byte budget **不**构成关于该条目的负向结果。本 RFC 统计省略情况，只是为了解释
自己的扩展，不记录任何 `candidate_not_selected` 式信号，也不向 HTTP 契约增加任何字段。

有一点纠正值得记录，因为它关系到未来那个 RFC 该如何论证：`RelationalRecallTokenEstimator` 在 prepare 内解析召回
血缘（`recall.py:106`）**并不能**作为允许写入的先例。`resolve()` 与 `estimate()` 都是读操作，而 RFC 0028 约束的是
**写**，不是工作量。

## 预算与字节不变性

扩展只能增加参与竞争固定预算的候选数量，并且发生在选择与渲染之前，而选择与渲染本身不变。`content_bytes` 仍然与
`request.max_bytes` 校验（`prepared_context.py:207-208`），所以本 RFC 保证的不变量是：

```text
content_bytes <= request.max_bytes
```

**只有在候选集与选择都不变时，才保证逐字节相同。** 闸门判定为 `sufficient` 的运行——也就是整条无扩展路径——与今天
逐字节相同，这就是回归保证。**确实**发生扩展的运行则本来就应当不同：第 1 轮的候选可能占据第 0 轮未用满的预算余量
（一次探测中，在 `max_bytes = 8000` 下增加一条候选使交付内容从 551 字节变为 785 字节），而把一条候选换成另一条即使
条目数不变也会改变字节（assembly 每节上限为一条时，从 546 字节变为 593 字节）。在这里要求逐字节相同，恰好会否掉本
RFC 想要允许的那些有用扩展。被保证的是：永不越过上限，且扩展会被字节预算拦住，而不是反过来覆盖它。

这也是验收部分测量注入字节数、而不是断言其相等的原因。

## 代价模型

以每个参与的可搜索类别为单位，rerank 关闭与开启分别列出：

| 情形 | 搜索轮次 | query embedding | RFC 0080 rerank 生成调用 |
| --- | --- | --- | --- |
| 第 0 轮充分 | 1 | 若解析出的 mode 含向量通道则 1，否则 0 | 部署自身既有的、每次非空 Memory 搜索 1 次 |
| 提交一次扩展 | 2 | 复用第 0 轮 query vector 时 +0，否则 +1 | +0，或每次被 rerank 的 Memory 搜索 +1 |
| 提交两次扩展 | 3 | 复用则 +0，否则 +2 | +0，或每次被 rerank 的 Memory 搜索 +2 |

累计有界：最多 3 次搜索轮次、最多 2 次额外 query embedding、最多 2 次额外的 rerank 生成调用（仅限 Memory 搜索）。
**不**声称逐轮代价单调递增，因为在关闭 rerank 时两轮都是针对同一候选池的同一搜索，区别只在准入下限。

**query embedding 是真实开销，必须计入。** `mode="auto"` 在 hybrid 通道可用时解析为 `hybrid`
（`service.py:602-607`），而 `MemoryService.search` 每次调用都会嵌入 query（`service.py:442`）；Topic Memory 同样每次
搜索嵌入一次。在 SQLite 后端上用计数版 embedding stub 连跑三次 `auto` 搜索，在关闭 rerank 的情况下产生了三次嵌入
调用。因此本 RFC 把**复用纳入设计**：第 0 轮的 query vector 与 embedding profile 会沿同一条内部准入参数路径传给后续
轮次，使重复轮次不再重新嵌入。当复用不可行时，该轮付出这次调用，并由 `RecallEffort.added_embeddings` 记录，使代价
模型不会静默漂移。

**没有任何扩展轮会改变后端候选池的大小。** `limit` 与 `memory_rerank_candidate_limit` 都不被触碰，因此 `coarse_limit`
（`service.py:452`）与 `candidate_limit = max(coarse_limit * 4, 32)`（`service.py:457`）在每一轮都完全相同。

**rerank 仅限 Memory。** RFC 0080 的 reranker 是为 `MemoryService` 构造的（`service.py:161`、`:174`、`:489`）；
Experience 与 Topic Memory 的搜索不做 rerank。因此上表的生成调用列只适用于被 rerank 的 **Memory** 搜索，混合类别的
扩展不会为其他类别增加生成调用。同时启用两个特性的部署需要显式接受该代价；除非配置允许额外的调用，否则启用 rerank
时会跳过扩展轮，该决定记录在 `RecallEffort` 中。

## 失败与降级

扩展是**按轮次**提交的，且一轮是全有或全无：

- 第 0 轮无条件提交。如果第 0 轮失败，prepare 与今天一样失败。
- 轮次 `r >= 1` 先把该轮所有参与类别、所有 Scope 的结果收集到一个**暂存**集合。只有当其中每一次搜索都成功时，暂存
  集合才会合并进已提交集合。
- 如果该轮中任何一次搜索抛错，整个暂存集合被丢弃，已提交集合回退到上一次提交的状态。`RecallEffort.rounds` 统计实际
  执行的轮次，`expansion_actions` 只记录已提交的轮次。

没有这道边界，"fail-open"就不成立：扩展按类别和 Scope 执行，后面的搜索可能在前面几次搜索已经把候选贡献进来之后才
失败，而把这个错误吞掉就会返回一个**部分扩展**的结果，而不是今天的第 0 轮结果。有了这道边界，一个失败的轮次得到的
正是上一次已提交的结果。

闸门本身是失败即不扩展的：如果 `assess` 抛错，或配置缺失，则不执行扩展，prepare 带着第 0 轮继续。闸门永远不会把一次
成功的 prepare 变成失败，也不会改变 `status`。因为闸门在选择之前运行，一次失败最多多花一次搜索，永远不会丢掉已有
结果。

## 边界条件

- **空 Scope。** 没有 Memory、没有 Experience。闸门不得扩展：内容缺失不等于召回偏薄。闸门返回 `sufficient`，
  原因为 `no-content`，结果是今天的正常空结果。
- **调用方传入 `assembly: {"sections": []}`。** RFC 1489 将其定义为完成校验后直接返回的正常空结果。不执行扩展。
- **`max_bytes` 处于 512 字节下限。** 可能只放得下一条。预算探测会报出"拟合被预算卡住"，因此闸门只报告而不扩展；
  此处结果偏薄是预算属性而非召回属性。
- **重复证据。** 引用同一证据身份的多条候选在"不同来源数"信号中只计一次，该信号按上文的身份表逐类别计算。因此该
  信号偏低本身不构成扩展理由；而 Memory-only 的结果也不会被误判为单一来源，因为 Memory 的身份是条目而不是 Memory
  revision。
- **只选 Profile 的请求。** `profile` 是 section 类别（`runtime/models.py:195`），但通过 `profiles.latest` 读取
  （`application.py:752-759`），既不搜索也不经过准入下限，因此它不是扩展目标，只选 profile 的请求永不扩展。
- **已饱和的类别。** 如果某类别第 0 轮的准入数已经等于其 Builder 上限，扩展无法再给它增加候选；该轮被记录为
  "什么都没改变"，而不是被重试。
- **指定了 `assembly` 的请求。** 扩展不得引入未选择的类别。若调用方只选择了 Memory，则"补 Experience"不是一个
  合法动作，无论第 0 轮多薄。
- **没有向量部署的 Scope。** hybrid 不可用，`mode` 本来就是 `fts`，余弦基线也不适用；此时只有词项证据要求可以放宽，
  且任何一轮都不得尝试切换模式（`service.py:598-601`）。

## 兼容性与 API 影响

`PreparedContext` 保持四个字段，`openapi/powercontext.yaml` 不变，因此不需要执行 `make api-generate`：准入计数器挂在
`MemorySearchResult` 的进程内取值上，永远不会到达由 `search_response`（`server/mapping.py:788`）构建的 HTTP 投影。
`PreparedContextBuild` 不变。改动都是内部、进程内的：

- Memory 搜索入口新增可选的 `admission` 参数与进程内计数器；
- `experience_recall` 与 Topic Memory 召回回调由裸元组改为返回携带计数的结果值，其签名是 Runtime 的构造参数
  （`composition.py:486`、`application.py:2257`、`:2268`）；
- Runtime 新增配置项：policy、各项扩展下限，以及可选的 `RecallEffort` sink。

功能默认关闭，由 Runtime 配置启用。

## 测试

闸门、扩展器与预算探测作为纯函数测试，覆盖：无分数类别（Experience）的信号计算、准入数为零的类别，以及已经处于上限的
类别。

Runtime 层测试在固定候选集与固定预算下断言：

- 未扩展路径产生与今天逐字节相同的输出；
- 扩展路径上 `content_bytes <= max_bytes`，且扩展是被预算拦住的，而不是越过预算；
- 一轮不充分导致恰好一次已提交扩展；第二轮仍不充分则停止；
- **合并集合永不破坏 Builder 不变量**：两轮各含 16 条 Memory 命中、只差一条候选时，必须成功构建，而不是抛出
  `PreparedContextInvariantError("memory-candidate-limit")`；任何轮次都不得超出类别上限；
- 一轮没有贡献新身份 → 沿用先前的候选集，且 `candidates_by_round` 持平；
- 第 0 轮已饱和的类别不再接收后续轮次的候选；
- 第 0 轮产生的候选永远不会被扩展挤掉；
- 一轮中任何一次搜索失败都会丢弃该轮整个暂存集合，并返回上一次已提交的结果；
- 空 Scope 永不扩展；只选 profile 的请求永不扩展；显式选定的类别集合永不被扩大；
- 准入计数被报出，且未配置 sink 时不产生任何副作用。

现有测试位置：`tests/builtin/runtime/test_prepared_context.py`（Builder 与渲染的纯逻辑）与
`tests/e2e/test_builtin_runtime.py`（Runtime 层 prepare 行为）；assembly 路径的对应测试是
`tests/e2e/test_context_text_assembly.py`。

端到端验收沿用 RFC 0028 的规则——任务收益必须与上下文体积分开评估：固定 task、model 与 `max_bytes`，使用 RFC
1229 workload contract 与 RFC 0081 harness，control（单趟 prepare）对照 treatment（带闸门 prepare），在 SQLite 与
OceanBase 上各跑一遍，报告任务成功率、注入字节数、`truncated_items`、`dropped_items`、扩展轮次、`added_embeddings`、
`added_generation_calls` 与新增延迟。在该对照显示出收益之前，默认不启用该特性。

# Drawbacks

- **偏薄查询上增加延迟。** 每次 prepare 多出一到两次搜索，而这恰好发生在召回本就偏弱的场景，因此新增延迟可能
  换不来任何东西。复用第 0 轮 query vector 可以消掉其中的嵌入成本，但消不掉搜索本身。
- **被降低的准入下限会放进它本来要挡住的候选。** 词项证据要求与余弦基线的作用，就是不让弱匹配进入有界预算；放宽
  它们可能挤掉默认下限本可交付的证据。这是该特性默认关闭、且必须先标定而不能直接打开的主要原因。
- **扩展后的输出可能更大。** 在 `max_bytes` 之内，一次成功的扩展可以比第 0 轮交付更多字节，这正是它的意图，但也
  意味着字节数不再是某个 query 的稳定属性。
- **对已饱和类别与被预算卡住的查询，扩展是无效的。** 候选上限封顶了任何轮次能增加的量，而预算探测会在预算是约束时
  停止扩展。该特性只针对一种失败模式——候选偏薄而预算未满——不应被当成通用的召回修复方案来宣传。
- **新增可调参数面。** 决定"是否充分"的阈值，现在又加上了决定"放宽到哪"的一组阈值。两者都容易设错，也很难用经验
  证据证明。以带版本 policy 的形式发布可以缓解但不能消除这一点。
- **可能掩盖检索缺陷。** 如果第一轮偏薄的原因是索引或 embedding 有问题，用同样机制再跑一轮往往同样偏薄，
  闸门只是增加了工作量而没有给出诊断。
- **与 rerank 的交互。** 启用 RFC 0080 时，扩展可能使每次 prepare 的生成调用最多增加两次。
- **prepare 路径上多了代码**，外加三个搜索入口的新内部参数与两个召回回调的契约变更，而收益尚未被本 RFC 证明。

# Rationale and alternatives

- **不做。** 最省，也可辩护：调用方总可以提高 `max_bytes` 或重新查询。但这会把 application policy 推进每一个
  provider adapter，正是 RFC 0028 motivation 反对的方向，并且每个集成会各自发明不同的重试规则。
- **让调用方用改写后的 query 重试。** 同样的反对理由，而且它额外要求调用方去做 RFC 0028 刻意放进 Runtime 的
  检索工作。
- **无条件放宽准入下限**，或无条件抬高 Builder 候选上限。这会让所有查询——包括已经服务得很好的那些——都付出代价，
  并让更弱的候选进入有界预算。闸门只在召回确实偏薄时付出代价。需要说明的是，提高 `SearchMemoryRequest.limit` 不在
  可选项之内：Runtime 已经按上限请求了每个类别（`application.py:742-743`、`:761`）。
- **在第 2 轮提高 `memory_rerank_candidate_limit`。** 否决：它决定的是后端请求的规模（`service.py:452`、`:457`），
  会把 prepare 路径下的候选池从 120 扩到 400，破坏代价模型所依赖的"候选池不变"保证。独立的召回扩展是另一个提案。
- **跨轮累积而不重新选择。** 否决：两轮各自的合法结果去重后的并集可能超出类别上限，并抛出
  `PreparedContextInvariantError("memory-candidate-limit")`。合并后的集合改为通过已经用于截断第 0 轮的分配器重新选择。
- **对扩展后的运行断言逐字节相同。** 否决：`max_bytes` 是上限而不是目标，因此一次有用的扩展在不超过上限的前提下
  本来就会让交付字节变多；而条目数不变时换一条候选也会改变字节。不变量是 `content_bytes <= max_bytes`，逐字节相同
  只在无扩展路径上要求。
- **交给 RFC 0080 的 listwise reranker。** Rerank 在候选池内部选择，无法捞出从未进入候选池的证据。它也只作用于
  Memory、默认关闭，且每次搜索都有生成调用成本。
- **在宿主插件里实现。** 宿主侧上下文插件已经在做这件事。对 PowerContext 而言，那会把选择权重新移出 Runtime 的
  application boundary。
- **跨类别扩展。** 否决：RFC 1489 让类别参与成为调用方的决定，搜索未选择的类别会花掉调用方没有授予的召回与预算。
- **跨时间窗扩展。** v1 不可行：`PrepareContextRequest` 与 `SearchMemoryRequest` 都没有时间或 as-of 参数。新增
  该参数是一次独立的契约变更，属于它自己的 RFC。

# Prior art

- **RFC 0080 的 rerank trace** 是直接先例：把诊断细节挂在进程内结果上，同时不改动 HTTP 响应。本 RFC 沿用同一
  规则，并沿用了召回 token 估算器已经在用的进程内回调模式（`application.py:794-808`）。
- **OpenClaw 生态中的宿主侧上下文插件**实现了完备性闸门，输出 `use` / `expand` / `max_expand`，并配以自适应扩展
  循环：放大 top-K（×2 后 ×3）、放宽时间窗，上限两轮。这个**形状**值得借鉴，实现不值得照抄——它们的闸门与压缩
  是正则与关键词打分，没有真正的语义；跨项目契约是 duck-typed，没有 schema；度量模块被明确声明与它本应影响的
  决策解耦。本 RFC 中有界合并与预算探测这两部分在它们那里没有对应物。
- **检索评测实践**（多阶段召回后 rerank）是标准做法。较不常见、也是本 RFC 从宿主侧实现中借来的一点，是把
  **召回不足**当作一等状态：它触发一次有界的再次尝试，而不是一次静默截断。

# Unresolved questions

1. **阈值标定。** 什么取值能让闸门只在真正偏薄时触发？本 RFC 建议先以关闭状态发布该 policy，用现有 workload
   suite 标定后再在任何地方启用。
2. **闸门应该按类别还是全局？** 全局更简单；按类别可以发现"某个被选中的类别没返回内容而另一个表现良好"，而
   "准入数 vs 上限"信号本身已经暗示了这一点。按类别可能严格更优，但它与 RFC 1489 的每节 limit 交互方式需要先做出
   决定。
3. **准入下限可以放宽到什么程度，按通道还是按类别？** 下限是真正的可调项，也有真正的失败模式，而且它并非处处
   相同：词项要求是共用的（`search.py:104`），但 Experience 在自己的索引内部应用它
   （`persistence/experience_index.py:309`），余弦基线则是每个类别各自一个常量（`memory/fusion.py:29`、
   `topic_memory/fusion.py:33`）。传递机制到位后，无论推导出的 `required_matches` 还是 `0.3` 基线都很好覆盖，但
   单一的全局扩展下限比按类别的下限更容易推理。闸门与扩展下限应当一起，针对现有 workload suite 做标定。
4. **query vector 复用应当是强制还是尽力而为？** 本 RFC 把复用作为设计的一部分，并把残余代价记进
   `RecallEffort.added_embeddings`。把复用变成强制项可以去掉一个记账分支，代价是 Runtime 与各搜索入口之间的内部
   契约更严格。
5. **代价在评测报告中放在哪里？** RFC 1229 workload 应在收益指标之外，报告新增延迟、新增 query embedding，以及
   启用 rerank 时新增的生成调用次数。
6. **调用方是否需要按请求退出？** 当前该特性是部署级的。为延迟敏感的集成提供按请求的逃生舱，可能是不必要的
   复杂度，也可能是必需的。

# Future possibilities

- **给搜索增加时间窗参数**，让后续 RFC 可以把"放宽时间窗"作为真正的扩展动作，而不仅限于本 RFC 的准入下限动作。
- **一次可以改变候选池大小的召回扩展**，如果评测显示在放宽准入无效的场景下扩大后端候选池有帮助。那需要对代价、以及
  本 RFC 所依赖的"候选池不变"不变量给出它自己的保证。
- **把闸门结果回灌到检索排序**——记录哪些候选被选中、哪些落选，使检索质量可以演化。刻意排除在本 RFC 之外，
  且必须遵守 RFC 0051 与 #1425 的边界：不自动衰减，不自动淘汰。
- **在公开契约中报告扩展**，如果未来的 profile 要求 Agent 或运维人员看到召回代价。那将是一次独立的 API 变更。
- **把闸门复用到其他有界选择操作**，如果 PowerContext 今后出现第二个在预算下选择证据的操作。
