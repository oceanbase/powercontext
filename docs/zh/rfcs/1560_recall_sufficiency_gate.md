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

本 RFC 让 Runtime 在渲染之前，用低成本方式判断候选集对当前 query 是否充分；不充分时，最多执行两轮受控扩展
——提高每个类别的搜索 `limit`，并在搜索模式为 `auto` 时切换到 `hybrid`——然后在**同一个**调用方给定的总预算内
重新选择。

闸门默认不调用模型，不会抬高交付字节数，不改变公开 `PreparedContext` 契约，并在任何异常时退化为当前行为。

# Motivation

RFC 0028 有意把内部原因（没有 Memory、没有命中、异常）对调用方合并为同一个正常空结果。该决定继续有效，本 RFC
不重新讨论。**合并"对外报告的原因"与"完全没有恢复路径"是两件事。**

具体地，当相关证据恰好落在第一轮召回之外——措辞不同的条目，或在 `auto` 模式下排名低于截断线的条目——当前会得到
一份静默变薄的上下文，每个集成都观察到同样的变薄。RFC 1489 让调用方可以控制参与类别、类别顺序和每类条数上限，
但没有回答：当参与的类别合起来返回的内容太少时，Runtime 应该做什么。

当前流水线在结构上就是单趟的：

```text
每个参与类别各搜索一次
  -> 按 Builder 的候选上限截断
  -> 选择并拟合到 max_bytes
  -> 渲染
```

现有实现中有两个事实，让"有界地再看一次"既便宜又安全：

- 每个类别的搜索上限已经是 `PreparedContextBuilder` 上的固定常量（`prepared_context.py:103-110`：
  `memory_candidate_limit = 16`、`topic_memory_candidate_limit = 8`、`experience_candidate_limit = 8`），超过即抛出
  `PreparedContextInvariantError`（`prepared_context.py:165-169`）。因此现有不变量内部存在第一轮没有用满的余量：
  `SearchMemoryRequest.limit` 默认为 `10`，低于 Memory 候选上限 `16`。
- `ScopedContextApplication._prepare`（`application.py:727`）经 `_recall_scope`（`application.py:814`）调用 Memory
  搜索时，硬编码了 `mode="auto"`（`application.py:849`）。把该模式参数化后，第一轮只走单一检索通道的 query，
  第二轮可以同时走两个通道——不需要新增任何请求字段。

第三个观察催生了本 RFC 的报告部分：当前**没有任何地方统计输给预算的条目**。非 assembly 路径下 `_fit_entry` 按
`max_entry_content_bytes` 截断，放得下就返回（`prepared_context.py:461`），只有在原文短于
`_MIN_TRUNCATED_CONTENT_BYTES` 时才返回 `None`、整条丢弃（`prepared_context.py:462-463`）；assembly 路径下
`fit_context_text_item` 同理，在没有可用内容时整条丢弃（`prepared_text.py:103-104`）。`truncated` 会逐条渲染，
但截断条数与整条丢弃数都没有被计数。让它们可计数是一个很小的改动，也是诚实评估本功能的前提。

本 RFC 并不是主张"召回越多越好"。它主张的是：**有条件的**额外召回——只在第一轮看起来偏薄时才付出代价——
值得在现有 workload 基础设施下被评估。

# Guide-level explanation

## Mental model

```text
Stage A  召回
           对参与类别搜索（第 0 轮）
           RecallSufficiencyGate.assess(candidates, query)
             充分                 -> Stage B
             不充分且 r < 2       -> 有界扩展后再次搜索
             不充分且 r == 2      -> 带着现有结果进入 Stage B
Stage B  选择 + 分节组装 + 预算拟合 + 渲染        （不变）
Stage C  报告召回代价与省略情况                  （进程内）
```

扩展只能改变**哪些候选参与竞争**。它不改变输出预算、信任包装、引用形式，也不改变调用方选定的类别集合。

## 闸门看什么

闸门刻意保持低成本且无模型。以下信号都可以从第 0 轮结果直接得到，不需要额外 I/O：

| 信号 | 检测什么 |
| --- | --- |
| 返回的候选数与该类上限之比 | 某个类别几乎没返回内容。 |
| Top-1 分数及其与均分的差距 | 一条看似可用的命中被噪声包围，或根本没有明显胜出者。 |
| query 词项与头部候选的重叠度 | 命中只是靠停用词或某一个共现 token 匹配上的。 |
| 至少返回一条候选的类别数量 | 选了三个类别，只有一个有结果。 |
| 候选中不同 Artifact Revision 的数量 | 多条候选其实是同一份证据。 |

阈值是部署配置，不是请求参数，并以版本形式记录在 trace 中，使一次运行可以被复现。

## 扩展做什么

| 轮次 | 动作 | 前置条件 |
| --- | --- | --- |
| 1 | 把每个类别的搜索 `limit` 提高到 Builder 候选上限；把 `mode` 从 `auto` 切到 `hybrid`。 | 第 0 轮判定不充分。 |
| 2 | 放宽闸门阈值（接受当前最好的证据）；若启用了 RFC 0080 rerank，则提高其候选上限。 | 第 1 轮判定不充分。 |

每一轮的代价都严格高于上一轮，且轮数上限为二，因此一次 prepare 的最坏代价是有界且可预测的。

**扩展绝不增加类别。** RFC 1489 规定 `assembly.sections` 决定哪些类别参与，未被调用方选择的类别既不执行召回、
也不分配输出预算。静默搜索一个未选择的类别会违反该契约，因此类别成员不属于扩展范围。调用方完全省略
`assembly` 时，Runtime 沿用现有的默认类别选择，同样不扩展。

## 可以观测到什么

沿用 RFC 0080 `rerank` trace 的先例，闸门结果挂在进程内构建结果上，**不**进入 HTTP v1 响应。
`PreparedContextBuild` 增加一个可选字段：

```python
@dataclass(frozen=True)
class PreparedContextBuild:
    context: PreparedContext
    origins: tuple[PreparedContextOrigin, ...]
    recall_effort: RecallEffort | None = None
```

```python
@dataclass(frozen=True)
class RecallEffort:
    policy: str                       # 带版本的 policy id，如 "powercontext.recall-gate.v1"
    rounds: int                       # 0、1 或 2
    gate_reason: str                  # "sufficient" | "thin-candidates" | "weak-top-1" | ...
    expansions: tuple[str, ...]       # 例如 ("limit", "hybrid")
    candidates_by_round: tuple[int, ...]
    truncated_items: int              # 已交付但被截断；当前未计数
    dropped_items: int                # 整条输给预算；当前未计数
```

后两个字段是新增的可观测性，不是新行为。当前两者都没有被计数（`prepared_context.py:462-463`、
`prepared_text.py:103-104`），而它们是把"我们找到了证据"与"找到了但预算吃掉了它"区分开来的必要条件——
当一次扩展什么都没改变时，这个区分决定了你如何解释结果。

进程内 trace 留在进程内。Benchmark 和 `powercontext doctor` 类的诊断可以读取它；公开契约不变。

## Example

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

第 0 轮返回三条 Memory 候选，其中一条分数可用。闸门判定为 `weak-top-1`，扩展一次（提高 limit、`mode` 切到
`hybrid`），第 1 轮返回另一条携带实际验证指令的候选。随后选择与渲染完全按现有逻辑进行，仍在同样的 8000 字节内。

如果第 1 轮没有返回更好的结果，prepare 会交付第 0 轮的结果——也就是今天的行为——并附 `rounds: 2` 与说明原因的
`gate_reason`。

## 本 RFC 做什么、不做什么

| 做什么 | 不做什么 |
| --- | --- |
| 用无模型信号判断充分性 | 在组装阶段引入模型调用（RFC 1489 要求组装无模型调用） |
| 最多扩展两轮，代价单调递增 | 无限重试或循环 |
| 在既有候选上限内加大搜索力度 | 突破 `memory_candidate_limit` / `experience_candidate_limit` |
| 保持调用方 `max_bytes` 为唯一输出预算 | 抬高交付字节数 |
| 在进程内 trace 中报告代价 | 改动 `PreparedContext(schema, status, content, content_bytes)` |
| 任何错误都退化为当前行为 | 让扩展成为新的失败模式 |

# Reference-level explanation

## 当前行为

`PreparedContextBuilder`（`src/powercontext/builtin/runtime/prepared_context.py`）声明了各项上限：

```python
memory_candidate_limit = 16
topic_memory_candidate_limit = 8
experience_candidate_limit = 8
entry_limit = 8
topic_memory_entry_limit = 8
experience_entry_limit = 2
max_entry_content_bytes = 2000
```

`build_scopes_result()` 在某个类别超过候选上限时抛出 `PreparedContextInvariantError`，随后在 `request.assembly`
存在时走 `_build_text()`，否则交错、拟合、渲染。没有条目能放入时返回
`PreparedContext(status="empty", content=None, content_bytes=0)`；否则返回 `status="ready"` 及渲染内容与 UTF-8
长度。`PreparedContextStatus` 只有 `"ready"` 与 `"empty"` 两个取值（`runtime/models.py:58`）。渲染长度超过
`request.max_bytes` 时抛出 `PreparedContextInvariantError("output-budget")`。

`PrepareContextRequest` 含 `query`、`max_bytes`（512–32768，默认 8000）和可选 `assembly`。
`SearchMemoryRequest` 含 `query`、`limit`（默认 10）、`mode`（`fts` / `vector` / `hybrid` / `auto`，默认 `auto`）与
`tag_filter`。两个请求都**没有**时间窗或 as-of 参数。

## 新增组件

1. **`RecallSufficiencyPolicy`** —— 冻结的、带版本的值对象，持有阈值、最大轮数和每轮扩展描述符。由 Runtime 配置
   构造；功能关闭时默认值保持当前行为。
2. **`RecallSufficiencyGate`** —— 纯函数 `assess(candidates, query, policy) -> GateAssessment`。无 I/O、无模型调用、
   除候选自身已携带的信息外不访问时钟。
3. **`RecallExpander`** —— 纯函数 `(round, policy) -> SearchPlan`，`SearchPlan` 描述下一轮使用的 `limit` 与 `mode`。
   它不涉及类别，因此不可能违反 assembly 契约。
4. **`RecallEffort`** —— 上文描述的 trace 值，挂在 `PreparedContextBuild` 上。

四者都位于 `src/powercontext/builtin/runtime/` 下。闸门与扩展器是纯函数，可以直接测试，不需要数据库。

## 循环放在哪里

循环属于当前执行各类别搜索、随后调用 `PreparedContextBuilder.build_scopes_result()` 的 Runtime 层，即
`ScopedContextApplication._prepare`（`application.py:727`）与 `_recall_scope`（`application.py:814`）。正如 Builder
的 docstring 所述，Builder 本身保持无 I/O、无持久化、无 rerank；它接收胜出轮次的候选，与今天完全一致。

## 持久化边界

RFC 0028 规定 Context Pack"不写数据库、不写文件、不进入 Source journal、不进入 Memory evidence、不启动 scheduler，也不作为
telemetry 持久化"（`docs/zh/rfcs/0028_context_pack.md:464`）。本 RFC 保持在该边界之内。

`RecallEffort`——包括 `truncated_items` 与 `dropped_items`——是在单次 `prepare_context` 调用内计算、并挂在进程内构建
结果上的值。它不写数据库行，不是 Source observation 或 Memory evidence，不启动 scheduler 工作，也不作为 telemetry
持久化。从不查看进程内结果的调用方不付出任何代价，也不产生任何副作用。

这正是本提案**刻意不**包含跨会话存活的逐条召回结果台账的原因——尽管那才是更有用的信号。从 prepare 路径持久化
"这条被选中"或"这条输给了预算"，需要修改 RFC 0028 的 write-free 条款，那是一次基础契约变更，且正在别处决定：#1554
提出的正是这个选择，而 maintainer 在那里的意见是首版保持 prepare 只读。因此本 trace 的任何跨会话版本都需要它自己的
RFC。

有一点纠正值得记录，因为它关系到未来那个 RFC 该如何论证：`RelationalRecallTokenEstimator` 在 prepare 内解析召回
血缘（`recall.py:106`）**并不能**作为允许写入的先例。`resolve()` 与 `estimate()` 都是读操作，而 RFC 0028 约束的是
**写**，不是工作量。

## 预算不变性

扩展只能增加参与竞争固定预算的候选数量。由于选择与渲染发生在最后一轮之后且逻辑不变，并且 `content_bytes` 仍然
与 `request.max_bytes` 校验，交付体积不可能因扩展而变大。一次"扩展后放入同样条数"的运行，与一次"无需扩展"的
运行产生逐字节相同的输出。

## 代价模型

| 情形 | 搜索次数 | 模型调用 |
| --- | --- | --- |
| 第 0 轮充分 | 每个参与类别 1 次 | 0 |
| 扩展一次 | 每个参与类别 2 次 | 0（若启用 RFC 0080 rerank，则每类别 1 次） |
| 扩展两次 | 每个参与类别 3 次 | 0（若启用 rerank，则每类别 2 次） |

rerank 是扩展唯一可能引入模型代价的地方，因为 RFC 0080 对每次非空 reranked search 执行一次结构化生成请求。同时
启用两个特性的部署需要显式接受该代价：启用 rerank 时，除非配置允许，否则跳过扩展轮。这一点记录在 `RecallEffort`
中。

## 失败与降级

所有错误路径都退化为当前行为：闸门抛错、扩展抛错、某一轮返回的候选少于上一轮，或配置缺失。闸门永远不会把一次
成功的 prepare 变成失败，也不会改变 `status`。因为闸门在选择之前运行，一次失败最多多花一次搜索，永远不会丢掉
已有结果。

## 边界条件

- **空 Scope。** 没有 Memory、没有 Experience。闸门不得扩展：内容缺失不等于召回偏薄。闸门返回 `sufficient`，
  原因为 `no-content`，结果是今天的正常空结果。
- **调用方传入 `assembly: {"sections": []}`。** RFC 1489 将其定义为完成校验后直接返回的正常空结果。不执行扩展。
- **`max_bytes` 处于 512 字节下限。** 可能只放得下一条。扩展仍然不得改变预算；此处结果偏薄是预算属性而非召回
  属性，闸门应报告而不扩展。
- **重复证据。** 引用同一 Artifact Revision 的多条候选在"不同来源数"信号中只计一次，因此该信号偏低本身不构成
  扩展理由。
- **指定了 `assembly` 的请求。** 扩展不得引入未选择的类别。若调用方只选择了 Memory，则"补 Experience"不是一个
  合法动作，无论第 0 轮多薄。

## 兼容性与 API 影响

无。`PreparedContext` 保持四个字段；`openapi/powercontext.yaml` 不变，因此不需要执行 `make api-generate`。
`PreparedContextBuild` 增加一个默认为 `None` 的可选字段，它是进程内对象。功能默认关闭，由 Runtime 配置启用。

## Testing

闸门与扩展器作为纯函数测试。Runtime 层测试在固定候选集与固定预算下断言：未扩展路径产生与今天逐字节相同的输出；
一轮不充分导致恰好一次扩展；第二轮仍不充分则停止；空 Scope 永不扩展；显式选定的类别集合永不被扩大。

现有测试位置：`tests/builtin/runtime/test_prepared_context.py`（Builder 与渲染的纯逻辑）与
`tests/e2e/test_builtin_runtime.py`（Runtime 层 prepare 行为）；assembly 路径的对应测试是
`tests/e2e/test_context_text_assembly.py`。

端到端验收沿用 RFC 0028 的规则——任务收益必须与上下文体积分开评估：固定 task、model 与 `max_bytes`，使用 RFC
1229 workload contract 与 RFC 0081 harness，control（单趟 prepare）对照 treatment（带闸门 prepare），在 SQLite 与
OceanBase 上各跑一遍，报告任务成功率、注入字节数、`truncated_items`、`dropped_items`、扩展轮次与新增延迟。在该
对照显示出收益之前，默认不启用该特性。

# Drawbacks

- **偏薄查询上增加延迟。** 每次 prepare 多出一到两次搜索，而这恰好发生在召回本就偏弱的场景，因此新增延迟可能
  换不来任何东西。
- **新增可调参数面。** 决定"是否充分"的阈值很容易设错，也很难用经验证据证明。以带版本 policy 的形式发布可以
  缓解但不能消除这一点。
- **可能掩盖检索缺陷。** 如果第一轮偏薄的原因是索引或 embedding 有问题，用同样机制再跑一轮往往同样偏薄，
  闸门只是增加了工作量而没有给出诊断。
- **与 rerank 的交互。** 启用 RFC 0080 时，扩展可能使每次 prepare 的生成调用翻倍或三倍。
- **prepare 路径上多了代码**，而收益尚未被本 RFC 证明。

# Rationale and alternatives

- **不做。** 最省，也可辩护：调用方总可以提高 `max_bytes` 或重新查询。但这会把 application policy 推进每一个
  provider adapter，正是 RFC 0028 motivation 反对的方向，并且每个集成会各自发明不同的重试规则。
- **让调用方用改写后的 query 重试。** 同样的反对理由，而且它额外要求调用方去做 RFC 0028 刻意放进 Runtime 的
  检索工作。
- **无条件提高默认搜索 limit**（例如 `limit` 从 10 到 16，或 `mode` 从 `auto` 改为 `hybrid`）。这会让所有查询
  ——包括已经服务得很好的那些——都付出代价，并给有界预算增加噪声。闸门只在召回确实偏薄时付出代价。
- **交给 RFC 0080 的 listwise reranker。** Rerank 在候选池内部选择，无法捞出从未进入候选池的证据。它默认关闭，
  且每次搜索都有生成调用成本。
- **在宿主插件里实现。** 宿主侧上下文插件已经在做这件事。对 PowerContext 而言，那会把选择权重新移出 Runtime 的
  application boundary。
- **跨类别扩展。** 否决：RFC 1489 让类别参与成为调用方的决定，搜索未选择的类别会花掉调用方没有授予的召回与预算。
- **跨时间窗扩展。** v1 不可行：`PrepareContextRequest` 与 `SearchMemoryRequest` 都没有时间或 as-of 参数。新增
  该参数是一次独立的契约变更，属于它自己的 RFC。

# Prior art

- **RFC 0080 的 rerank trace** 是直接先例：把诊断细节挂在进程内结果上，同时不改动 HTTP 响应。本 RFC 沿用同一
  规则。
- **OpenClaw 生态中的宿主侧上下文插件**实现了完备性闸门，输出 `use` / `expand` / `max_expand`，并配以自适应扩展
  循环：放大 top-K（×2 后 ×3）、放宽时间窗，上限两轮。这个**形状**值得借鉴，实现不值得照抄——它们的闸门与压缩
  是正则与关键词打分，没有真正的语义；跨项目契约是 duck-typed，没有 schema；度量模块被明确声明与它本应影响的
  决策解耦。
- **检索评测实践**（多阶段召回后 rerank）是标准做法。较不常见、也是本 RFC 从宿主侧实现中借来的一点，是把
  **召回不足**当作一等状态：它触发一次有界的再次尝试，而不是一次静默截断。

# Unresolved questions

1. **阈值标定。** 什么取值能让闸门只在真正偏薄时触发？本 RFC 建议先以关闭状态发布该 policy，用现有 workload
   suite 标定后再在任何地方启用。
2. **闸门应该按类别还是全局？** 全局更简单；按类别可以发现"某个被选中的类别没返回内容而另一个表现良好"。
   按类别可能严格更优，但它与 RFC 1489 的每节 limit 交互方式需要先做出决定。
3. **`hybrid` 切换是否该放在第 1 轮？** 在没有向量部署的 Scope 上，切换 `mode` 是空操作，这一轮只付出延迟。
   扩展器可能应该在未配置向量通道时跳过模式切换。
4. **代价在评测报告中放在哪里？** RFC 1229 workload 应在收益指标之外，报告新增延迟以及启用 rerank 时新增的
   生成调用次数。
5. **调用方是否需要按请求退出？** 当前该特性是部署级的。为延迟敏感的集成提供按请求的逃生舱，可能是不必要的
   复杂度，也可能是必需的。

# Future possibilities

- **给搜索增加时间窗参数**，让后续 RFC 可以把"放宽时间窗"作为真正的扩展动作，而不仅限于本 RFC 的 limit / mode
  动作。
- **把闸门结果回灌到检索排序**——记录哪些候选被选中、哪些落选，使检索质量可以演化。刻意排除在本 RFC 之外，
  且必须遵守 RFC 0051 与 #1425 的边界：不自动衰减，不自动淘汰。
- **在公开契约中报告扩展**，如果未来的 profile 要求 Agent 或运维人员看到召回代价。那将是一次独立的 API 变更。
- **把闸门复用到其他有界选择操作**，如果 PowerContext 今后出现第二个在预算下选择证据的操作。
