- Proposal Name: `recurring_failure_repair`
- Start Date: 2026-09-10
- Status: Proposed
- RFC PR: [oceanbase/powercontext#1557](https://github.com/oceanbase/powercontext/pull/1557)
- Tracking Issue: [oceanbase/powercontext#1554](https://github.com/oceanbase/powercontext/issues/1554)
- Related RFCs: [产品定义](0001_product_definition_and_vision.md)、[记忆层设计](0014_memory_layer_design.md)、
  [Context Pack](0028_context_pack.md)、[Handoff 制品](0048_handoff_artifact.md)、
  [Artifact Candidate 与 Review Inbox](0050_artifact_candidate_review_inbox.md)、
  [Experience 与 Skill 制品家族](0051_experience_skill_artifact_families.md)、
  [Scope 统计与用量](0072_scoped_statistics_and_usage.md)、
  [记忆检索重排](0080_memory_search_reranking.md)、
  [端到端评测架构](0081_end_to_end_evaluation_architecture.md)、
  [Source 定义与观测模型](1400_source_definition_and_observation_model.md)、
  [Prepared Context 文本装配](1489_prepared_context_text_assembly.md)

# Summary

Experience 回答的是"在什么情境下、什么动作产生了什么结果、我们学到了什么"。PowerContext 里没有任何东西回答它的
后续问题：「那个情境又出现了 —— 我们学到的东西到底起作用了吗？」

本 RFC 给反复出现的失败一个可机器匹配的身份、一个指明"修复必须触碰哪一层"的归因，以及一个记录某条已发布记录是否
被选中过、是否再次复发过、是否**真的起过作用**的结果账本 —— 最后一项只凭正面证据判定，绝不由一次"没出事的任务"得来。
设计可归纳为四条：

1. **复发需要身份，而自由文本不是身份。** Experience 增加一个可选的 `failure` 结构化块，其中 signature 就是匹配键。
   没有它，同一个失败换一种措辞描述就是一条新的 Experience，于是复发无法计数，`lesson` 也无法被证伪。
2. **归因用于路由修复，而不是因果断言。** 必填的 `repair_surface` 指明修复该触碰哪一层，从而让"记录是对的，但召回
   从不触发它"成为一个可表达的诊断，而不是一个不可见的缺陷。
3. **准入以证据为闸门，降级以 Review 为闸门。** 没有引用了失败观测的失败记录，也没有任何自动退休、衰减或重要度评分 ——
   [RFC 0051](0051_experience_skill_artifact_families.md) 已记录的边界保持不变。
4. **账本从写入路径上已有的证据派生，绝不来自 `prepare_context`。** 选中由 Handoff 引用重建；复发在归整 Task Outcome
   Source 时判定。读路径保持只读。`selected` 只覆盖具备完整 Handoff/Task Outcome 链路的观测；链路缺失是证据缺口，
   不能证明召回没有发生。

# Motivation

## 当前无法计数复发

`LLMExperienceCandidatePipeline.incubate` 把 `task-outcome` Source 归整为 Experience 候选，并按内容精确相等加 Source
身份去重 —— `key = (candidate.proposal.model_dump_json(), tuple((source.source_type, source.source_id) for source in
selected))`，见 `src/powercontext/builtin/artifacts/experience/incubation.py`。这个 `seen` 集合只活在单次 `incubate()`
调用内部，而该调用受 `EXPERIENCE_INCUBATION_WINDOW_LIMIT = 32` 约束。它既不与更早窗口产出的候选比较，也不与已发布的
revision 比较。因此同一个失败在两个窗口被观测到就是两条 Experience —— 而用不同措辞描述的失败即便在同一个窗口内也是两条
Experience。由此产生两个后果：

- **一条 lesson 无法被证明是错的。** `ReviewService` 在发布*之前*校验证据、内容与 revision 一致性，但没有任何东西在发布
  *之后*观察这条记录到底起没起作用。
- **反复出现的失败看起来像进展。** 归整流水线忠实读取失败 —— 它的指令已经禁止把 failed、timed-out、cancelled 的 check
  写成成功（`artifacts/experience/prompts.py`）—— 然后从每一次失败里产出一条正向 Experience。同一个缺陷的第四次出现会
  产出第四条格式良好的 lesson，读起来像是知识在累积。

## 缺失的概念已经被命名过

[RFC 0014](0014_memory_layer_design.md) 把 "validated pitfalls" 列入 Memory 应优先保存的内容，要求一条持久条目
"会改变未来 agent 的判断或行动"，并把 `decision` 与 `constraint` 定义为一等 kind。但它没有定义 *validated* 是什么
意思。RFC 0014 同时固定了本 RFC 必须遵守的纪律："只有显式的 revision 证据才能修订条目；停用仍需要显式的 `forget()`"。

与此同时，[RFC 0051](0051_experience_skill_artifact_families.md) 把 "retirement, ranking, and usage attribution for
Experience and Skill" 列为未来工作，并声明当前 Artifact 契约 "has no retirement semantics, so this RFC adds no
automatic retirement or time decay"。用量归因正是本提案缺失的那一半。

## 具体场景

一个编码 agent 在两周内三次修复同一个不稳定的集成测试。每一次都会提出一条格式良好的 Experience，每一次都有人批准。
到周末，这个 scope 里躺着三条近乎相同的 Experience，而没有任何东西能区分"这条 lesson 起作用了"、"这条 lesson 从来没
进过上下文"和"这条 lesson 进过三次上下文，失败照样发生"。

我们想要的三个答案分别是：这条记录是否被选中过；失败是否还是复发了；以及复发时，坏掉的到底是哪一层。今天这三个答案都
无法表达。下面的流程是能把这三点区分开的最小案例。

## 本 RFC 不是什么

它不等同于 [#1508](https://github.com/oceanbase/powercontext/issues/1508)：后者把反复出现的 Task Outcome 关联到已有
Experience，并在配对比较下为 Skill 修订设闸。 #1508 做的事是归整；它没有匹配键，所以无法计数复发，也没有修复分类，
所以"召回策略坏了"只能被写成散文。它也不等同于 [#1510](https://github.com/oceanbase/powercontext/pull/1510) 的 Dream
工作流：Dream 决定*该提出哪个制品*，并假定输入概念已经存在。本 RFC 提供的正是这两个机制可以归整与路由的负面知识类型。

它也刻意**不**记录 `candidate_not_selected`。`prepare` 必须保持完全只读，因此因字节预算或排序而未进入上下文的候选都不是
负向结果，后续 provenance 链路不完整则是证据缺口。尤其不能从缺少 `selected` 证据推断 `recall_policy` 发生了故障。

# Guide-level explanation

## 本 RFC 围绕的流程

这是促成本提案的场景：一个 agent 反复修改 `openapi/powercontext.yaml`，却忘了重新生成生成代码，于是签入的代码与契约
逐渐脱节。

1. 前两次出现是普通的 Experience 提案，彼此毫无关联。有了本 RFC 的 `failure` 块，第二次会被识别为第一次的**复发**：
   signature 匹配，账本记一次 `recurred`，而因为记录已经存在，不会再写出第三条 lesson。
2. 后来的一次任务又动了同一个文件。该记录被召回到 `prepare_context`，agent 被告知要重新生成。只有当后续 Handoff 和
   Task Outcome 保留所需引用时，这次有链路的观测才产生 `selected` 事件 —— 它由 Handoff 引用重建，而不是在读路径上
   插桩采集（见下文《账本写入路径》）。没有后续链路的 prepare 保持未观测状态。
3. 该任务上报一个 Task Outcome。这次 outcome 算不算数，由证据决定，而不由 agent 自己说了算。该记录的 `verification`
   命名了 check「生成代码与契约保持同步」：
   - check **运行且通过** → `avoided`；
   - check **运行且失败** → `recurred`；这是复发证据，不是因果诊断。后续 Review 的路由由该记录已经经 Review 确认的
     `repair_surface` 决定，而不是从这一次结果自动推断；
   - check **没有运行** → `unknown`，且不写事件。一次没有触发该 check 的成功任务，不能作为"这条记录帮上了忙"的证据。
4. 若该 signature 不断累积复发却从未达到 `avoided`，该 revision 会被推到 Review。当其 `repair_surface` 为
   `recall_policy` 时，Review 可以追问"在已有链路的观测里召回为何失败"。仅仅缺少 `selected` 证据不能证明召回从未
   发生，因为 Handoff 或 Task Outcome 可能没有被记录。

上述每一步都用的是已经存在的机制：Experience revision、制品被召回进 `prepare_context`、Handoff 引用、带有内嵌
`TaskCheck` 结果的 Task Outcome Source，以及 Review Inbox。新增的部分只有记录上的匹配键、`repair_surface` 枚举、`verification` 绑定和账本。

## 三个新概念

**Failure signature。** `ExperienceContent` 可以携带一个可选的 `failure` 块，其 `signature` 由两部分组成：
`recall_cue`（这条记录应该在什么情境下被召回）和可选的 `symptom`（失败的可观测形态）。cue 是匹配键：正是它让
"这事以前发生过"成为一个可核查的陈述。Experience 的其余部分保持原状 —— `situation` / `action` / `outcome` /
`lesson` 依然承载人类可读的判断。

**Repair surface。** 一个必填枚举，指明要让失败停止，必须改变哪一层：

| 取值 | 修复必须改变 |
| --- | --- |
| `experience_content` | 当前 Experience revision 的 `situation` / `action` / `outcome` / `lesson` / `failure` 内容 |
| `working_state` | Handoff 的 `objective` / `state[]` / `next_action`，或被记录的 Task Outcome 字段 |
| `recall_policy` | Scope 召回配置、`prepare` 查询的构造方式，或 `assembly.sections` 的选择 |
| `acceptance_check` | Handoff 的 `disposition` / 验收标准，或挂在 Experience 或 Handoff 上的校验指令 |

这个枚举存在的意义是路由修复。一条修复属于 `recall_policy` 的记录不应该再产出另一条 lesson —— 它应该产出一个关于检索的
信号。`repair_surface` 由生成环节提议、在 Review 确认；它不会被自动推断之后当作事实使用。

**Outcome ledger。** 每条已发布的 Experience revision 配三类证据事件，全部从证据派生，而不是从插桩读路径得到：

- `selected` —— 该 revision 被某个 Handoff 引用过，且后来的 Task Outcome 引用了该精确 Handoff 对应的 Receipt；
- `recurred` —— 更晚的 Task Outcome 报告了与该 signature 匹配的失败；
- `avoided` —— 更晚的 Task Outcome 显示风险情境再次出现，**且**绑定在该记录上的 check 通过。

`avoided` 以证据为闸门，必须同时满足下列四条。"被选进 prepared context"不等于"被用了"，而一次只是没有提到该失败的
Task Outcome 也不等于"避免了"：

1. 该 revision 被某个 Handoff 引用过，且 Task Outcome 的 `handoff_receipt_ref` 能解析为该精确 Handoff 的 Handoff Receipt；
2. `condition_ref` 必须解析到同一 Task Outcome 的 `observations[]` item，且该 item 的 `basis="verified"` 并带非空精确
   evidence，从而证明触发条件已经出现，且其归一化后的 `WorkClaim.text` 必须等于记录的 `verification.condition`；
   `check_ref` 必须解析到同一 Task Outcome 的 `checks[]` item，且该 item 的 `basis="verified"` 并带非空精确 evidence，
   归一化后的 `TaskCheck.name` 必须等于 `verification.check_subject`，从而证明绑定 check **运行过**。声明型、缺失、
   不匹配的 item 或未运行的 check 一律使判定停在 `unknown`；
3. 该 check **通过**；
4. 同一次 Task Outcome 下没有为本 signature 记录 `recurred` 事件。

一次已完成的 Task Outcome 本身不写任何事件。无法证明触发条件出现过、或绑定的 check 没有产出结果时，判定保持
**`unknown`** 且不写事件 —— 证据缺失绝不被记为成功，证据空洞也绝不被静默转成一个正计数。

`unknown` 是*派生判定，不是账本事件*。为每个未被观测的情形写一行，会让只追加的账本塞满不携带信息的行，并要求在必须保持
不写库的路径上做采集；它只在有链路的观测范围内计算：某个 revision 的 `selected` 事件数，减去在同一 Task Outcome 下获得了
`recurred` 或 `avoided` 的那些。没有链路的 prepare 不进入分母，也不能解释成未选中。

`avoided` 依然是代理指标，本 RFC 不宣称相反。绑定的 check 通过说明结果是对的，并不说明这条记录造成了它。`condition` 与
精确的 check item 引用可以排除无关任务，但仍不能建立因果关系。

## 贡献者该如何理解它

把失败记录理解为一条**可被证伪、可被路由**的 Experience，而不是第二类知识存储。如果一条记录的证据不足，就不要写它 ——
缺失的记录好过错误的记录。如果一条记录反复被选中而失败仍在发生，答案不是再写一条记录，而是检查 `repair_surface`，因为
失败可能根本不在内容层。

被否决的方案记录与 API 陷阱依然是普通的 Experience 或 Memory 内容。它们是决策知识，没有复发可计数。只有*反复出现*的失败
才需要匹配键和账本。把两者混为一谈 —— 如下文引用的参考实现所做的那样 —— 会迫使每一条被否决的方案都背上永远用不到的计数器。

## 完整示例

agent 在沙箱里让 `pytest` 因为端口已被占用而失败。Outcome status 为 `failed`，check status 为 `failed`。

1. 归整流程把这次失败与 scope 中已有的 signature 做匹配，返回一条既有 Experience 的 cue（其 `repair_surface` 为
   `experience_content`），并以失败的 check 作为证据。
2. 账本为该 revision 记一次 `recurred`，provenance 指向该 Task Outcome。
3. 这是该记录第三次复发且期间没有 `avoided`，因此它被标记为待 Review。
4. 因为 surface 是 `experience_content`，Review 收到一个 revision 候选，其 `reason` 写明复发次数，证据就是同一次失败观测。
   由人决定是打磨这条记录，还是修改它的 `repair_surface`。

假如 surface 是 `recall_policy`，第 4 步根本不会发生。流水线会记录这次复发、在统计里暴露它，并且不提出任何制品变更，因为
坏掉的是检索而不是文本。

这个例子走的是 `recurred` 路径；上面的 OpenAPI 流程走的是 `avoided` 与 `unknown`。两者合起来覆盖了账本能持有的全部事件。

## 可检查的最小证据案例

下面的来源图是把记账边界落到实现和测试夹具所需的最小案例：

1. Experience revision `E7` 带有 failure signature 和 verification 绑定。
2. Handoff `H12` 引用 `E7`。Handoff Receipt Source `R12` 的 `status = "accepted"`、`selection = "exact"`、
   `selected_revision = H12`、`evidence_status = "available"`。三个彼此不同的 Task Outcome Source
   `O12-pass`、`O12-recurred` 和 `O12-unknown` 都有 `handoff_receipt_ref = R12`；每条完整链路都为 `E7` 写入一条
   `selected` 观测。
3. `E7.failure.verification.condition` 与 `O12-pass.observations[0].text` 归一化后严格相等，
   `E7.failure.verification.check_subject` 与 `O12-pass.checks[0].name` 归一化后严格相等。
   `O12-pass.observations[0]` 是带精确 evidence 的 `basis="verified"` condition claim，
   `O12-pass.checks[0]` 是带精确 evidence、status 为 `passed` 的 `basis="verified"` 绑定 TaskCheck。
   `condition_ref` 与 `check_ref` 都携带 `task_outcome_ref = O12-pass`；两者 digest 均可解析时，该有链路的观测写入
   `avoided`。
4. `O12-recurred.checks[0]` 是带精确 evidence、`basis="verified"` 的失败 check，且其归一化后的 `name` 等于
   `E7.failure.signature.recall_cue`。其唯一、不可变的 `RecurrenceMatch` 使用 `candidate_set_mode = "handoff_citations"`，
   把 `E7` 作为唯一有资格的候选，并记录精确 target
   `(E7, normalized signature key)`。该 match 的 `failure_ref` 解析到这个 check；其 digest 就是 event 的
   `recurrence_match_digest`。这个有链路观测写入 `recurred`，而不是 `avoided`；重放时解析该 match，不得再次让生成器选择。
5. `O12-unknown` 中绑定的 TaskCheck 没有运行，或它只是 `basis="declared"`；不写判定事件，其有链路的选中计入 `unknown`。
   没有 verified 且同一 Outcome 的两个 item 引用时同样不能写入 `avoided`。
6. 一次 prepare 若没有 Handoff/Task Outcome 链路，不写 `selected` 观测，也不进入 `unknown` 分母。能看到 Handoff 引用却无法
   关联 Task Outcome 时，只报告为 provenance 覆盖缺口；没有 Handoff 的 prepare 不产生遥测。两种情况都不能被解释成召回失败
   或 `candidate_not_selected` 结果。

7. Review 发布了同一 cue 的 `E7` revision 2 后，一个新的无链路失败窗口在 `candidate_set_mode = "scope_heads"` 下只快照
   revision 2；历史 `E7` revision-1 的 match 保持不变。一条 recurrence streak 不跨越这个 revision 边界。

实现还必须证明：重放其中任一 Source 窗口不会产生重复 match 或 event；而 `E7` 的两条不同 Handoff/Task Outcome 链路仍是两次独立观测。

# Reference-level explanation

## 数据模型

可选块加在既有内容模型上，而不是新增一个 Artifact 家族：

```python
class FailureSignature(_ExperienceValue):
    recall_cue: Annotated[str, Field(min_length=1, max_length=MAX_FAILURE_CUE_LENGTH)]
    symptom: ExperienceText | None = None

class FailureVerification(_ExperienceValue):
    condition: ExperienceText  # 与 verified WorkClaim.text 归一化后严格绑定
    check_subject: Annotated[str, Field(min_length=1, max_length=MAX_FAILURE_CUE_LENGTH)]
    # 与 verified TaskCheck.name 归一化后严格绑定

class FailureRecord(_ExperienceValue):
    signature: FailureSignature
    repair_surface: RepairSurface
    verification: FailureVerification
    @model_validator(mode="after")
    def reject_blank_cue(self) -> FailureRecord: ...

class ExperienceContent(_ExperienceValue):
    situation: ExperienceText
    action: ExperienceText
    outcome: ExperienceText
    lesson: ExperienceText
    failure: FailureRecord | None = None
```

`RepairSurface = Literal["experience_content", "working_state", "recall_policy", "acceptance_check"]`。
`MAX_FAILURE_CUE_LENGTH` 是提议新增的常量（512），因为匹配键不该有 8000 字符；具体数值是实现决策，不是设计决策。

`verification` 在 `FailureRecord` **内部**是必填的，正是它让账本能说出"又失败了"以外的话。`condition` 是与证明风险情境
出现的 `WorkClaim.text` 的归一化严格绑定；`check_subject` 是与提供证据的 `TaskCheck.name` 的归一化严格绑定。这两者刻意
不是语义匹配：生成器可以提出语义匹配，但其结果必须先作为可持久化、可 Review 的断言另行定义，才可以写入账本。一条没有
check 的记录只可能不断累积 `recurred` 事件，因此把该字段设为必填，正是防止 `avoided` 退化成"什么都没被报告"的关键。

**向后兼容。** Artifact 内容以 JSON 持久化，加载时经注册内容类型重新校验，因此可选字段对既有所有 revision 都是加载兼容的。
不引入 `schema_version`：Artifact 家族今天都不带它，为单个可选字段引入会产生第二套版本机制。

**两个必须实现的改动。** `experience_search_text` 目前只返回用户书写的字段（"so renderer labels cannot cause
matches"）—— signature 必须显式加入该投影，否则 cue 不会参与检索。`render_experience` 服务于有界上下文投递，因此 cue 与
symptom 需要一个渲染形态；否则这条记录可能被选中却永远无法被读到它的 agent 认出来。

## 准入规则

只有当下列条件全部成立时，失败记录才被准入。规则 1 与 2 构成置信下限：无法核实的记录被丢弃而不是被存储。

1. **必须引用一个失败观测。** 候选必须至少引用一个内容记录了失败的 Source —— status 为 `failed` 或 `blocked` 的 Task
   Outcome，或该被引用 Task Outcome 内 status 为 `failed`、`timed_out`、`unavailable` 的 `TaskCheck`。复发事件还必须保留
   指向该 signature 所依赖的精确内嵌 observation 或 check 的 `failure_ref`。observation 只有在其父 Task Outcome 为
   `failed` 或 `blocked`、且该 `WorkClaim` 是带非空精确 evidence 的 `basis="verified"` 时才构成失败证据。check 只有在
   `basis="verified"`、带非空精确 evidence，且 status 为 `failed`、`timed_out` 或 `unavailable` 时才构成失败证据；
   `skipped`、`cancelled` 与 `unknown` 永不写入 `recurred`。既有的 Review 不变量（至少一条精确引用）是必要条件但不充分，
   因为被引用内容必须专门为失败提供证据。
2. **单一、自足的 cue。** cue 必须命名一个可识别的情境，而不是对 outcome 字段的复述。
3. **必须有 `repair_surface`。** 记录必须说明修复该触碰哪一层。
4. **必须有一个将来能运行的 check。** 记录必须携带 `verification`，其 `condition` 与 `check_subject` 分别是将来
   `WorkClaim.text` 与 `TaskCheck.name` 的归一化严格绑定。一条没有 check 的记录只能被观测到"又失败了"，而这正是
   本 RFC 要摆脱的状态。
5. **不允许静默的近似孪生。** 若归一化后的 cue 与既有记录的 cue 近似重复，候选会带一条指明既有记录的警告返回，以便作者改为
   修订那条记录。候选不会被自动拒绝。
6. **provenance。** 复用既有 Review 证据模型，不新增第二套证据机制。

Review 拒绝继续由已持久化的 Candidate `rejected` 状态及其 `decision_reason` 审计。近似重复只是候选生成或 Review UI 的提示，
不是复发账本事件。第一版不为两者新增不可变 Source kind；否则必须另行规定写入时机、访问模型与兼容性影响面。

## 匹配策略

匹配是承重机制，因此规定得保守。

- **归一化。** Unicode NFKC、大小写折叠、空白折叠、去掉首尾标点，得到比较键。归一化是比较的辅助手段，不是被存储的身份。
- **精确匹配输入就是失败 item 本身。** 当 `failure_ref.item_kind` 为 `observation` 时，取解析出的
  `WorkClaim.text`；当它为 `check` 时，取解析出的 `TaskCheck.name`。只有该值归一化后与候选 revision 的
  `signature.recall_cue` 归一化值相等，候选才有资格参与匹配。`TaskCheck.details`、可选的 `symptom` 和
  Task Outcome 周边叙述都不是匹配输入。因此候选资格集合必须在调用生成器之前确定性计算：零个候选为
  `unmatched`，一个候选为 `matched`，多个候选为 `ambiguous`。
- **在匹配前冻结候选集。** 完整 Handoff/Task Outcome 链路只使用该 Handoff 所引用的精确 Experience revision。没有该链路时，
  候选集只包含 scope 内每个 Experience Artifact 的当前 head revision，并按 `ArtifactRef` 排序；已被替代的 revision 不参与。
  所用模式和完整、有序的候选 ref 都会持久化，因此后来的 revision 不会重定向历史复发。一条复发连击始终属于精确 revision，
  绝不转移给替代 revision。
- **持久化一条可重放的匹配决策。** 在任何 `recurred` 事件之前，归整必须为精确的 Task Outcome 与 `failure_ref` 写入一条不可变
  `RecurrenceMatch`。它保存 Outcome ref 和 journal position、失败 locator 和 digest、候选集模式与 digest、全部候选 ref，以及
  一个精确 target `(artifact_ref, signature_key)` 或终态结果 `unmatched` / `ambiguous`。target 必须由上面的确定性候选
  资格规则选出；生成器最多提供解释文本，不得选择或覆盖结果。记录校验必须拒绝不在冻结候选集内的 target，或其归一化 key
  不等于 target revision 的 `recall_cue`。重放时先解析该记录；同一 `(task_outcome_ref, failure_ref)` 不得再次调用生成器或重新选择。
- **只有冻结的精确 target 才建立关联；模糊相似度只做提示。** target 的归一化 key 从其已存储的 `recall_cue` 原样复制；token
  bigram 重叠度达到 0.8 时只产生*提示*，该阈值沿用参考实现，且绝不写计数器。模糊匹配绝不能静默增加复发计数，因为一次错误关联会
  静默污染这个特性存在的意义本身。
- **歧义不产生 verdict。** 若冻结候选集有两个可能 target，持久化的决策为 `ambiguous`；不写账本事件，并暴露该冲突。
- **signature 不是全局身份。** 记录的身份仍然是 `(artifact_id, revision)`。参考实现用内容派生的可变 id 作为卡片键，使得重新
  存储等于原地编辑；这与不可变 revision 不兼容，修改一条记录必须保持为显式修订。

## 账本写入路径

账本只由已经在消费 `task-outcome` Source 的归整流水线写入，绝不由 `prepare_context` 或检索写入。

这不是偏好，而是既有契约的要求。[RFC 0028](0028_context_pack.md) 规定 Context Pack "writes no database or file, enters
no Source journal or Memory evidence, starts no scheduler work, and is not persisted as telemetry"，且正常日志
"must not record scope, query, snippets, entry IDs, entry version IDs, or response bodies"。
[RFC 1489](1489_prepared_context_text_assembly.md) 同样把模型调用挡在装配之外。在读路径上插桩"选中"会同时违反这三条。

因此"选中"改由已经存在的 provenance 重建：

```
TaskOutcome.handoff_receipt_ref  ->  HandoffReceipt Source
                                 ->  HandoffReceipt.selected_revision  ->  Handoff Revision
                                                                     ->  HandoffArtifactCitation[]  ->  进入上下文的 Experience revision
                                                                     ->  HandoffMemoryCitation[]
```

`HandoffResolution` 已经携带 `selection`、`selected_revision`、`current_revision` 与 `evidence_checks`，Handoff 激活
证据也已经受 `MAX_HANDOFF_CITATIONS` 约束。因此该重建是对既有数据的读取，而不是新的采集路径。它的信任级别是
`untrusted_history`，账本会记录这一点：一条引用只能证明 agent 的上下文里出现过这条记录，不能证明 agent 读过或遵守了它。

匹配决策先于每次观测的一条账本事件：

```python
class RecurrenceMatch(_ArtifactValue):
    scope_id: str
    task_outcome_ref: SourceRef
    task_outcome_position: int
    failure_ref: TaskOutcomeItemRef
    candidate_set_mode: Literal["handoff_citations", "scope_heads"]
    candidate_refs: tuple[ArtifactRef, ...]          # 已排序的精确快照
    candidate_set_digest: str
    result: Literal["matched", "unmatched", "ambiguous"]
    artifact_ref: ArtifactRef | None = None           # 只在 matched 时必填
    signature_key: str | None = None                  # 只在 matched 时必填


class RecurrenceObservation(_ArtifactValue):
    observation_id: str                              # 单次来源观测的稳定幂等键
    scope_id: str
    artifact_ref: ArtifactRef                      # 精确的 Experience revision
    signature_key: str                             # 匹配成功的归一化 cue
    event: Literal["selected", "recurred", "avoided"]
    match_basis: Literal["exact"]
    task_outcome_ref: SourceRef                     # 每种事件都必填；把 selected 关联到它的 Handoff
    task_outcome_position: int                      # 对应不可变 Source journal position；事件的规范顺序
    handoff_receipt_ref: SourceRef | None = None   # selected/avoided 必填：解析出精确 Handoff 的 receipt
    handoff_ref: ArtifactRef | None = None         # 选中是如何推导出来的
    condition_ref: TaskOutcomeItemRef | None = None  # avoided 必填：证明风险条件出现的 observation
    check_ref: TaskOutcomeItemRef | None = None      # avoided 必填：运行且通过的 check
    failure_ref: TaskOutcomeItemRef | None = None    # recurred 必填：证明失败的 observation 或 check
    recurrence_match_digest: str | None = None       # recurred 必填：精确、冻结的 RecurrenceMatch


class TaskOutcomeItemRef(_ArtifactValue):
    task_outcome_ref: SourceRef
    item_kind: Literal["observation", "check"]
    item_index: Annotated[int, Field(ge=0)]
    item_digest: str  # item 规范序列化内容的 digest
```

任何违反下列矩阵的事件都必须在进入持久化前由记录校验拒绝：

| 事件 | 必要证据 | 必须拒绝的组合 |
| --- | --- | --- |
| `selected` | `task_outcome_ref` 及其精确、正值的 `task_outcome_position`；accepted/exact 的 `handoff_receipt_ref`；以及引用该 revision 的对应 `handoff_ref` | 没有 receipt、receipt 不是 accepted/exact、journal position 错误、Handoff 未引用该 revision，或同一个 `(artifact_ref, signature_key, task_outcome_ref)` 出现第二条 `selected` 事件 |
| `avoided` | 所有 `selected` 证据；指向 observation 的 verified `condition_ref`，其归一化 `text` 等于 `verification.condition`；指向 check 的 verified 且 passed 的 `check_ref`，其归一化 `name` 等于 `verification.check_subject`；两个 locator 都在 `task_outcome_ref` 上 | declared、无引用、不匹配或歧义 item；错误的 item kind 或 Outcome；非 passed check；或同一 signature 和 Outcome 已有任意 terminal verdict |
| `recurred` | `task_outcome_ref` 及其精确、正值的 `task_outcome_position`；一个结果为 `matched` 的 `RecurrenceMatch` 的 `recurrence_match_digest`；以及其唯一的 `failure_ref`。match 的 scope、Outcome、failure locator、target `artifact_ref` 和 `signature_key` 必须与 event 相等。observation ref 要求 verified claim、精确 evidence 和父 Outcome status 为 `failed` 或 `blocked`；check ref 要求 verified check、精确 evidence 和 status 为 `failed`、`timed_out` 或 `unavailable` | 没有匹配决策、决策的 scope、Outcome、failure locator、冻结集合或 target 错误、不满足这些状态规则的失败 item、journal position 错误、`ambiguous`/`unmatched` 决策、locator 的 digest 无法解析，或同一 signature 和 Outcome 已有任意 terminal verdict |

`RecurrenceMatch` 与事件只追加，并在一个事务内提交。匹配键对 `(scope_id, task_outcome_ref, failure_ref)` 保持唯一；其
`failure_ref.task_outcome_ref` 必须等于 `task_outcome_ref`，候选快照在计算 digest 前规范化。`matched` 结果要求两个 target
字段同时存在；`unmatched` 与 `ambiguous` 则要求二者都不存在。`observation_id` 对单次来源观测保持唯一，并由精确事件证据派生：事件类型、引用的 Task Outcome 及其不可变
journal position、Handoff/Receipt、精确的 artifact revision、归一化 signature key、适用时的规范 match digest，以及每个适用 item
locator（`condition_ref`、`check_ref` 或 `failure_ref`，包括其 digest）。重放同一个 Source 窗口因此会复用已记录的匹配决策，
并保持幂等；同一 revision 的不同观测仍然可以追加。一个**有完整链路的** Source 窗口针对同一
`(scope_id, artifact_ref, signature_key, task_outcome_ref)` 只写一条 `selected`，最多写一条 terminal verdict
（`recurred` 或 `avoided`）。没有链路的 Source 窗口只有在唯一 `failure_ref` 支持匹配时才可写一条 `recurred` verdict，
绝不能写 `avoided`。多个候选证据 item 会让 verdict 保持歧义和 `unknown`，而不是让一条 Task Outcome 膨胀
recurrence streak。`(scope_id, artifact_ref, signature_key)` 只是聚合索引，不是唯一约束。任何内容都不原地更新，因此即使
记录后来被修订，它的产出历史仍然可查。

`TaskOutcomeItemRef` 是账本内部 locator，不是虚构的 `TaskCheck` Source 身份：它必须在 `item_index` 处解析不可变的 Task
Outcome 内容，且 `item_digest` 必须匹配该精确 item 的规范序列化内容。上方校验矩阵让
`condition_ref.item_kind == "observation"`、`check_ref.item_kind == "check"` 成为可执行约束，并要求精确 Outcome、verified
evidence、与 `FailureVerification` 绑定严格相等的归一化内容以及 passed check。`recurred` 通过 `failure_ref` 与冻结的
`RecurrenceMatch` 保留唯一失败 item；observation 要求父 Outcome 为 `failed`/`blocked`，check 必须 verified、带精确 evidence，
且为 `failed`、`timed_out` 或 `unavailable`。`selected` 携带 `handoff_ref` 以及使用该 Handoff 的 Task Outcome。
`task_outcome_position` 必须等于 `task_outcome_ref` 解析出的 Source journal entry；它是
verdict 唯一的顺序键，单个 scope 中不存在并列。没有得出判定的观测根本不写行。因此某个 revision 的 `unknown` 计数只在有链路的观测范围内派生：它带有已记录 Task
Outcome 的 `selected` 事件，减去在同一 Task Outcome 下获得了 `recurred` 或 `avoided` 的那些。缺失 Handoff 或 Outcome 的
观测要报告为 provenance 缺口，而不是零使用或 recall-policy 结果。

## 降级与 Review 的交互

当同一个 revision 上累积出复发连击 —— 提议默认值为连续 3 次 terminal `recurred` verdict 且其间没有 terminal `avoided`
verdict —— 该 revision 被标记为 **needing review**。verdict 严格按不可变的 `task_outcome_position` 排序；重放、延迟处理
和墙上时钟都不会改变连击。后果取决于 `repair_surface`：

- `experience_content` —— 流水线通过既有 `CandidateRepository` 提出一个 Experience revision 候选，把复发次数写进 `reason`，把失败观测
  作为证据。批准会产出一个新的不可变 revision。这复用 #1510 的 Dream 模式：候选自动生成，人工决策强制。
- `working_state`、`recall_policy`、`acceptance_check` —— 不提出任何制品候选。复发被记录并在统计中暴露，因为这次修复不是
  内容变更。
- **没有自动退休、衰减、重要度评分或停用。** [RFC 0051](0051_experience_skill_artifact_families.md) 禁止它
  （"this RFC adds no automatic retirement or time decay"），Artifact 根本没有 `state` 字段，Experience 家族也不存在
  `active`/`inactive` 概念 —— 这与 Memory 条目不同，后者有。低产出的记录被变得*可见*，而不是被*停用*。真正的退休语义需要
  它自己的 RFC。
- `avoided` 事件会清除连击但不改变任何制品状态；它把记录送回正常召回，这是本 RFC 引入的唯一自动转换。
- 绑定的 check 从未运行的 revision 既不会累积 `recurred` 也不会累积 `avoided`，因此永远到不了连击阈值。这不是沉默：它会
  表现为不断增长的 `unknown` 计数 —— 该信号说明 `verification` 的绑定错了，而不是说明这条记录没问题。

## 读取面

`ScopeStatistics`（[RFC 0072](0072_scoped_statistics_and_usage.md)）增加一个 `recurrence` 块：按 scope 统计
`selected` / `recurred` / `avoided` 数量、仍处于 `unknown` 的有链路选中次数，以及处于 needing review 的 Experience revision 数量；
同时统计无法关联到 Task Outcome 的 Handoff 引用；这些引用是 provenance 覆盖缺口，不是 `selected` 事件。缺失链路是证据覆盖率
信号，不是 recall-policy 诊断。
`unknown` 计数与各项判定并列上报而不是被折叠掉，因为一个记录全是 `unknown` 的 scope 根本没有证据环路 —— 这和一个记录
正被反复检验的 scope 是两种不同处境。因为既有统计层没有按制品的用量视图，本 RFC 提议一个有界读取：由既有 statistics
操作返回某 scope 内按复发连击排序的前 N 个 revision。不新增 MCP 工具。

```python
class RecurrenceStreak(BaseModel):
    artifact_ref: ArtifactRef
    signature_key: str
    terminal_recurred_streak: int  # 非负；按 task_outcome_position 的顺序派生


class RecurrenceStatistics(BaseModel):
    selected: int
    recurred: int
    avoided: int
    unknown: int                         # 没有 terminal verdict 的有链路选中
    unlinked_handoff_citations: int      # 仅为 provenance 覆盖率
    needing_review: int
    top_revisions: tuple[RecurrenceStreak, ...]  # 部署配置决定上限 N
```

公开 statistics response 中的 `ScopeStats.recurrence` 必填。`top_revisions` 先按 `terminal_recurred_streak` 降序，
再按 `(artifact_ref.family, artifact_ref.artifact_id, artifact_ref.revision, signature_key)` 排序；API 的部署配置上限 N 在
排序后才应用。多 scope 的 `ScopedStats` response 通过每一条 `by_scope` entry 返回各自的 block，不把互相独立的 scope
合并成一条 streak。

## 兼容性与影响面

| 影响面 | 影响 |
| --- | --- |
| `openapi/powercontext.yaml` | `ExperienceProposal` 增加一个可选对象。`ScopeStats` 增加必填的 `RecurrenceStatistics` block，其中包括有界的 `RecurrenceStreak` 行；既有 statistics operation 通过每条 `by_scope` entry 返回它。更新生成的 Python models 与全部生成 client，再运行 `make api-generate` 和 `make contract-test` |
| 持久化 | 不引入 Artifact schema 版本；账本新增只追加的 `RecurrenceMatch` 与 `RecurrenceObservation` 记录，并持久化不可变的 `TaskOutcomeItemRef` locator。匹配/事件唯一约束及事务写入都是持久化迁移的一部分 |
| Task Outcome / Handoff | 既有公开契约不变：账本按 accepted/exact receipt、index、digest 与既有 verified evidence 重放不可变 Source 内容。若实现改为引入每项稳定 ID，则属于 OpenAPI / model / generated contract 改动，必须另行规定 |
| Review | 契约不变。#1508 式归整继续可用；复发候选沿用既有 `propose_experience` 形态 |
| 检索（`prepare`） | 保持只读、不变。cue 之所以能被索引，是因为它成为内容投影的一部分 |
| 标签、授权 profile、artifact 资源发现 | 不受影响，因为没有新增 Artifact 家族 |
| 评测 | 新增一个可用于度量本特性的 outcome 类别；[#1422](https://github.com/oceanbase/powercontext/issues/1422) 尚未落地，因此该指标在此以不依赖它的方式定义 |

# Drawbacks

- **cue 是模型书写的自由文本，一个糟糕的 cue 会让整套机制失效。** 含糊的 cue 什么都匹配不到，于是复发被静默少计。这个失效模式
  是刻意选择的：少计只会产出一条安静的记录，而多计会产出一个自信的错误信号。两者都不免费。
- **账本给一个刻意保持读路径不写库的系统增加了持久化行。** 存储增长与归整事件成正比、与请求量无关，但它是真实存在的。
- **`avoided` 依赖于一个由生成环节提议的绑定。** `verification` 必须绑定到 signature 的触发条件。绑定过松会在风险情境从未
  出现的任务上记 `avoided`，属于多计；绑定从不触发则让该计数永远停在 `unknown`，属于少计。少计是优先接受的，理由与上文 cue
  相同 —— 但这也意味着这个计数只和它命名的那个 check 一样可靠，而那个 check 是模型书写的，且有些记录可能永远无法被判定。
- **Review 负担上升。** 嘈杂的归整流水线现在可以用复发候选淹没 Review Inbox。复发连击阈值是这里唯一的刹车。
- **把负面知识混入 `ExperienceContent` 拓宽了该家族的形态。** RFC 0051 把 Experience 定义为可复用判断；失败记录仍然是判断，
  但一个只期望四个散文字段的读者，现在需要知道何时该多读两个。

# Rationale and alternatives

**落点：本 RFC 落在 Experience 上，独立家族的问题明确延后。** 上面的设计扩展 `ExperienceContent` 而不是新增家族，遵循
tracking issue 上的指引：优先复用既有的 Experience 与 Task Outcome 路径，等一个具体案例跑通之后再决定是否分离家族。新增家族
是更大的改动 —— 仓库元组、candidate 仓库、`BaseArtifactFamily`、标签、授权 profile、artifact 资源发现、Review service 的
家族分支、OpenAPI、JS 集成、文档与测试 —— 而这条记录的形态本身就是 Experience 形态（situation、action、outcome、lesson）
加一个匹配键。

延后不等于已经决定，而重新审视它的理由很具体：如果 `failure` 块最终只被少数 Experience revision 携带，那么 Experience 就是
错误的容器，上述代价才变成合理的价钱。三件东西 —— signature、`repair_surface`、账本 —— 一起迁移，无需设计变更；落点是唯一
的开放部分，而且它是被刻意保持开放的。

**备选：保留一个 Memory `kind`。** 拒绝。Memory 条目已经有 `active`/`inactive` 状态与 `MemoryChangeOp`，且 RFC 0014 给它们
的准入契约是围绕"改变未来判断的陈述"构建的，与本提案不同。失败记录的评审单元应当是 Experience revision，账本也以 revision 为键。

**备选：原样照搬参考实现的卡片。** 拒绝，理由是两点在不可变 revision 模型下不可谈判：连续 5 次未避免后自动停用（违反 RFC 0051
且违反"Artifact 无状态"），以及内容派生的可变卡片 id（违反不可变 revision）。它的置信下限 —— 丢弃而不存储 —— 以及近似重复
只做*提示*的语义被采纳。

**备选：等 #1422 落地后再说。** 这是合理的排序论证，也是先提 issue 的原因。但本特性需要的指标必须向 #1422 提出请求，否则等循环
建好时它不会存在。

**不做这件事的影响。** 复发继续无法计数，召回策略类失败继续无法表达，也没有任何记录能被证明失败过。Experience 只会单调地、
不可证伪地累积。

# Prior art

**Recuris** —— Zhaochen Yu、Yingcheng Wu、Zhenfei Yin、Kaiyuan Chen、Zhe Zhao、Mengdi Wang、Shuicheng Yan、Ling Yang，
*Recursive Experiential-Working Memory Evolution for Long-Horizon Agent Harnesses*，arXiv:2608.24876v1，2026 年 8 月
25 日（[论文](https://arxiv.org/abs/2608.24876)、[代码](https://github.com/Gen-Verse/Recuris)，Apache-2.0）。它把 harness
的记忆控制层建模为 `M_k = (E_k, W_k, rho_k, C_k)`，并把这个元组定义为**补丁空间**：失败被定位到某个组件，而不仅仅是被总结。
有三点被沿用：以定位取代总结；把归因表述为 *"a repair decision rather than a claim of causal identification"*，这与
PowerContext 的证据纪律一致；以及验证门控的准入。

它的局限界定了可借鉴的边界。**它没有逐失败的复发计数器** —— 复用与避免只通过聚合代理指标衡量（`Reach`、留出集成功率增益、
dev 集回退率）。它的准入还假定存在一个留出开发集，而生产环境的召回环路没有这个预算。作者报告的增益（某模型在 tau-bench 上
+17.8 分、长程失败最高减少 80%）是单篇论文的自报数字，此处未做复现。因此本 RFC 是在已发表基线之上往前走 —— 账本是新工作，
而不是复现。

**claw-mem v7.6.0**（`Error Pattern Card`）是一个小型 Apache-2.0 社区插件，把 `E` 附近的诊断落地了。它的卡片格式是有用的起点：
`{trigger, symptom}` signature；`skill-defect | state-defect | invocation-timing | transition-judgment` 根因枚举
（其源码注释称这些语义映射到"the layer the fix must touch"）；最短 resolution 长度；0.8 重叠度的近似重复 trigger 检测且仅
提示编辑；以及逐卡片的 `hitCount` / `avoidedCount` / `lastHitAt`。其中两个组件被采纳；停用规则与可变卡片 id 不被采纳。它公开的
基准数字不作为基线引用：其 README 同时声称在 LoCoMo、ConvoMem、LongMemEval 上达到 100%，并声称存在源码里并不存在的 "subagent
memory merge"。只有其代码中可核验的常量在此被引用。

**PowerContext 自身的既有工作。** [RFC 0028](0028_context_pack.md) 允许把聚合的选择计数作为 telemetry，但禁止按条目记录日志；
[RFC 0072](0072_scoped_statistics_and_usage.md) 已经持久化召回测量（`preparations`、`baseline_tokens`、`recalled_tokens`、
`token_reduction`）与按 kind 的记忆计数，是复发块的天然归宿。
[RFC 0081](0081_end_to_end_evaluation_architecture.md) 定义了这个指标应当汇入的评测架构。

# Unresolved questions

1. **落点。** 本 RFC 把记录落在 `ExperienceContent` 上，并按 tracking issue 的意见，把"是否分离出独立 Artifact 家族"延后到
   上面的流程在具体案例上跑通之后。如果 `failure` 块最终只被少数 Experience revision 携带，就重新审视这件事。RFC 其余内容与
   落点无关。
2. **`repair_surface` 放在哪里** —— 放进制品，还是作为不触碰制品内容的 Review 注记？放进制品则持久、可查询；放进 Review 则
   保持内容不可变、判断可审计。
3. **阈值。** 触发 Review 的复发连击次数、`MAX_FAILURE_CUE_LENGTH`、以及 0.8 的近似重复重叠度都是提议值而非实测值。复发连击
   阈值尤其与每个 scope 的归整频率相互影响。
4. **`recall_policy` 类复发是否应该汇入检索评测环路**，而不是一个统计视图？如果是，`repair_surface` 就成为两个下游环路之间的
   路由键。
5. **账本归属。** 新增只追加的持久化记录，还是扩展统计层？RFC 0072 的仓库已经是可写且按 scope 分组的，这支持扩展；而按制品的
   粒度支持独立记录。
6. **命名。** RFC 0001 与 RFC 0002 把 `Trigger` 保留为产品级概念且其公开契约尚未定义。本 RFC 刻意避开这个词
   （`recall_cue`、`FailureSignature`）；请在生成公开 schema 之前确认该选择。
7. **`avoided` 是否属于 #1422**，作为通用的按制品 outcome 信号，而不是特性专用计数器？

# Future possibilities

- **跨 Scope 的失败模式。** 在多个 scope 中复发的 signature 是从个人资产晋升为团队资产的候选，这正是 RFC 0001 已经描述的流程。
- **signature 驱动的检索。** 今天 `SearchMemoryRequest` 只按 tag 过滤，没有家族或 kind 过滤。如果召回能直接针对 signature 求值，
  `recall_policy` 诊断会更有可操作性。
- **喂给 Skill 校验。** surface 为 `acceptance_check` 的记录，是 `SkillContent` 已携带的 `validation` 条目的天然来源。
- **Handoff 集成。** 最相关的 signature 可以被带进 `HandoffContent.state` 或 `omissions`，让后继 agent 继承"要避免的失败"，
  而不仅是"要继续的工作"。
- **可验证的退休。** 一旦账本存在，未来的 RFC 就能在实测产出的基础上定义退休语义，而不是靠猜 —— 这正是 RFC 0051 暗示的顺序。
