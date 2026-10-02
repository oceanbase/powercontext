- 提案名称：`replay_guided_artifact_evolution`
- 开始日期：2026-09-27
- 跟踪 Issue：[#1635](https://github.com/oceanbase/powercontext/issues/1635)
- 相关 RFC：[Experience 与 Skill](0051_experience_skill_artifact_families.md)、[Artifact Dreaming](1510-artifact-dreaming.md)、[Artifact Processing Supervisor](1515_artifact_processing_supervisor.md) 和 [Recurring Failure Repair](1557_recurring_failure_repair.md)

# 摘要

本 RFC 探索将 Dream-RSI 的核心机制迁移到 PowerContext 的 Experience 与 Skill evolution：把真实发生过的探索历史作为 replay environment。PowerContext 只借鉴思想，不采用其实现；不会依赖、集成、fork、vendor 或执行 Dream-RSI 的代码、package、schema、protocol、数据格式或脚本。

RFC 回答两个问题：

1. 自进化首先改善 Experience 与 Skill generation，而不是 `prepare_context` 热路径；
2. 在不削弱 Candidate Review 和 Artifact 权限边界的前提下，以可观测的 proposal attempt、faithful replay、bounded comparison 和下游证据丰富 PowerContext 现有的显式 Dream 机制。

近期范围有意保持克制：

- **P0：** 描述当前机制边界，并用行为等价测试保护它们；
- **P1：** 定义 evaluation-owned recording contract，faithfully replay 已发生的历史；
- **P2 spike：** 只对显式 `refine_experience` Dream 做一个小型、按需开启的 Experience-only 实验。

P3 的 Experience-to-Skill 与 usage-driven Skill replacement 是有条件的后续项。P4 的跨 policy improvement 是 research track。它们均不是短期交付承诺。

# 动机

## 为什么不选择 `prepare_context`

`prepare_context` 位于在线调用路径，根据调用方配置、Scope、检索结果和延迟限制组装 bounded、citable context。对多数产品而言，可用 section 与选择策略是业务决策，并不需要在每次请求时开放式搜索。把 multi-attempt exploration 放在这里会直接增加服务延迟，使行为更难预测。

Approved Experience 仍可提供 recall、task outcome、runtime、token、turn 和 tool call 等下游观测。这些观测可以反馈给 Artifact evolution，而不必把 `prepare_context` 作为首个自进化边界。未来若要优化 context assembly policy，应另写 RFC。

## 为什么选择 Experience 与 Skill generation

Experience 与 Skill 已具备受控改进所需的生命周期：exact evidence、typed generation、pending Candidate、人工 Review、immutable Revision、lineage 和后续 outcome evidence。Generation 可以异步执行，找不到改进也是合法结果。因此，Artifact generation 更适合试验探索与 replay，同时不改变在线服务路径。

PowerContext 也已经定义显式 Artifact Dreaming contract。本提议丰富这一 contract，而不是新建通用的自修改 policy platform。

# 术语与当前边界

必须区分以下三个对象。

| 对象 | 当前或拟议职责 | Candidate 语义 |
| --- | --- | --- |
| Experience incubation | 现有自动处理：消费 bounded `task-outcome` Source window，一次 generation call 产生 `0..N` proposals，并将自身 cursor 与处理结果原子推进 | 每个合法 proposal 可按当前行为成为 pending Candidate |
| Explicit `DreamRun` | 现有异步 run：处理调用方选择的 exact Memory entry version、Experience Revision 和可选 Source；operation 为 `refine_experience` 或 `derive_skill`；一次成功运行最多产生一个 Candidate | 作为拟议 spike 的权威 live business object |
| Artifact Evolution Policy / `ProposalAttempt` | evaluation-owned policy 选择有限 action 与预算；每个实际执行的隔离 action 及其结果记录为 `ProposalAttempt`。二者都不是 Runtime Artifact，也不是第二套 run API | `ProposalAttempt` 不直接进入 Review Inbox；最多一个 nominated result 可以为 `DreamRun` 创建现有 Candidate，但这需要另行的 contract change |

Experience incubation 与 explicit Dream 解决相关的生成问题，但并非同一个实现对象。本 RFC 不合并它们的 cursor、storage、API 或 Candidate 数量规则。

公开 Dream budget 允许 `max_model_calls` 为 `1..2`，主要用于在可恢复 inference failure 后重试。这种 retry 延续同一个逻辑 generation attempt，不能说明 Dream 已在探索两个 proposal branch。P2 的 additional attempt 是单独记录的 proposal action，具有自身 output、validation 和 cost。

同理，Experience incubation 可以由一次 model call 返回多个 proposals。这是 multi-output generation，不是 multi-attempt exploration。

Evaluation 层的 `objective` 是派生 identity，不是新的 Runtime 字段：`(scope_id, operation, target_artifact_ref_or_none, expected_head_or_none, input_manifest_digest, experiment_id)`。只有 tuple 相同的 attempts 才能比较或去重；这不改变 `CreateDreamRunRequest`。

# 设计决策

## 保留 `DreamRun` 作为 live authority boundary

本 RFC 不在 `DreamRun` 之外增加公开的 `EvolutionRun`。现有 `DreamRun` 继续权威记录 live status、exact inputs、operation、result、cost 和可选 Candidate。P1 首先将 `ProposalAttempt` 与 replay 数据放在 evaluation-owned bundle 中，不引入数据库 migration 或公开 API。

这样可以避免把实验性的 search-tree node 过早纳入 Runtime contract，也能保持“一个成功的 explicit Dream run 最多创建一个 Candidate”的现有保证。

## 保留 Review 与 Artifact 权限边界

探索层可以生成、验证、比较、剪枝、停止或提名，但不能批准 Candidate、直接写入 Artifact Revision、发布或安装 Skill、执行 Skill、授予权限、伪造 Source evidence 或跨越 Scope。人工 Review 仍是 pending Candidate 变为 immutable Revision 的唯一途径。

## 渐进丰富 Dream

概念上的 bounded 流程为：

```text
explicit refine_experience Dream objective
  -> baseline proposal attempt
  -> deterministic validation
  -> optional additional attempt under an explicit small budget
  -> deterministic comparison with auditable reasons
  -> nominate zero or one proposal
  -> existing pending ArtifactCandidate
  -> human Review
  -> approved immutable Experience Revision
```

P2 spike 将 additional attempt 实现为 evaluation-owned shadow action：记录 validation 和 comparison 证据，但不提名 shadow result，也不改变 Candidate/Review 路径。若要提名 additional branch，仍需另行修改 contract。Multi-attempt 行为是按需开启、异步且实验性的；它不会对所有 Dream run 默认启用，也不改变 scheduled Experience incubation。

# 近期范围

## P0：baseline boundary 与行为等价

P0 不改变行为。它分别描述并测试三个 baseline：

- 基于 Source window 的自动 Experience incubation；
- 显式 `refine_experience` Dream；
- 显式 `derive_skill` Dream。

等价测试通过 public behavior 断言，必要时使用固定的 deterministic collaborator。对于同一组 resolved input 与 collaborator result，应验证以下结果不变：

- `proposed`、`no_change`、`needs_evidence` 或 execution-error outcome；
- Candidate 数量、family、operation、target 和 pending status；
- exact evidence 与 lineage refs；
- Experience incubation 的 Source cursor 是否推进（explicit Dream 不推进也不清除普通 Source cursor）；
- retry 与 model-call accounting；
- Review 与授权行为。

P0 不增加 migration、新 public API、默认配置、额外 Candidate 或额外 model call。退出条件是把当前行为描述清楚并充分保护，使后续实验可以衡量真实变化。

## P1：记录 contract 与 faithful replay

P1 定义版本化、evaluation-owned replay bundle。它在适用时遵循 RFC 1229 的 workload 与 evaluation 约定，并可引用 typed `powercontext.e2e-evidence/v1` records；不另建一套 workload/replay harness。普通生产 capture 默认关闭；除非受控评估明确提供已经审核或合成的材料，否则不保留完整 prompt、task body 或 evidence body。

P1 分为 MVP 与延后的 P4 扩展。MVP 只包含 Experience-only spike 和只读 faithful replay 所需的记录。跨 policy replay 的完整元数据有价值，但不是 P1-MVP 的实现阻塞项。

### Evaluation ownership 与 contract adapter

P1 和 P2 由 `evaluation/` 下的 Dream Evolution Evaluation Harness 负责。它通过一个 Dream execution adapter 复用现有 evaluation 的 Scope 隔离、workload 选择、保留策略和 report rendering 组件。Adapter 负责 exact Dream input fixture、baseline/shadow 分配、attempt 记录、replay 查询和结果 projection；Dream bundle 不会被当作 Bub workload 或 SWE-Pro run。

本 RFC 唯一需要新增的 versioned artifact 是 `powercontext.artifact-evolution/v1`。它的 `execution.type` 为 `dream`，workload section 记录 `refine_experience` operation、exact Memory/Experience/Source refs、target 与 expected head、fixture digest、treatment 和声明的 budget。如果存在上游 task，可以引用 `powercontext.e2e-task/v1` entry 作为 provenance，但 Dream input 不需要适配成该 catalog。如果底层运行产生了可复用的 typed evidence，bundle 可以通过 adapter 引用 `powercontext.e2e-evidence/v1`；Adapter 只补充 exact artifact refs、action signatures、nomination state 和 validator results 等 Dream 专属字段，不改变共享 evidence contract。

现有 Bub/Harbor catalog 与独立的 SWE-Pro patch/grading path 仍由各自 owner 负责，它们的 workload 或 result artifact 不直接接收 Dream bundle。Evaluation report 是 `powercontext.artifact-evolution/v1` 的 projection，位于 Runtime table 与 public run API 之外。若 bundle 的 schema、operation、fixture digest 或 evidence reference 不匹配，adapter 必须拒绝它。

### Run 与 decision 字段

每个 recorded run 至少包含：

| 字段组 | P1-MVP 必需信息 |
| --- | --- |
| Identity | Bundle schema version、run id、适用时的 exact `DreamRun` ref、timestamp、Scope-safe workload identity |
| Inputs | Exact input manifest、immutable refs、content/snapshot digest、evidence roles、operation、target 和 expected head |
| Generation 与 validation | Policy/generator/model/schema/runtime identity、canonical chosen-action signature、typed output 或 terminal result、validator/scorer identity 与各项结果 |
| Outcome links | exact `DreamRun`、input 和存在时的 Candidate refs；Review、Revision 与 downstream outcome join 作为后续可选 link |
| Cost | Proposal attempts、model calls 与 retries、input/output tokens、wall time、concurrency 和 validation failures |

以下 P4 扩展字段只有在 evaluation harness 已经提供时才记录，不作为 P1-MVP 的交付要求：

- 每个 decision step 的完整 action set；
- logging-policy version 与 selection probability，或 deterministic selection reason 与 tie-break rule；
- 存在时的显式 seed identity；
- RFC 1229 split/holdout identity 与 arm-scoped run identity；
- support 与 coverage 状态；
- 为异步 Review、Revision、Source 或 recurrence join 保存 sealed post-decision outcome record。

P1-MVP 的 decision record 记录：

- decision 前已揭示的 observations；
- chosen canonical action signature；
- decision parent 与排序信息。

每个 `ProposalAttempt` 记录：

- parent decision 与 canonical action signature；
- 该 action 使用的 exact evidence 与 target refs；
- typed proposal、`no_change`、`needs_evidence` 或 error result；
- output digest 与可选的受控 payload；
- validator/scorer identity、version 和每项结果；
- incurred cost 与 retry 信息；execution/retry `attempt_count` 与 `proposal_attempt_id` 分开；
- nomination status 与 reason。

如果 P4 扩展可用，selection probability 可以审计 logging policy，但不能制造 action overlap。Deterministic logger 对其他 policy 通常缺乏足够 support。Post-decision links 通过如 `(bundle_id, event_type, exact_ref)` 的幂等 key 异步写入，不能回写 sealed 的 pre-decision observations。

### Faithful replay 语义

这里的 faithful replay 是 `powercontext.artifact-evolution/v1` 中的 read-only realized-attempt 模式，并使用 adapter 声明的 typed evidence reference。它可以引用 RFC 1229 workload provenance，但不等同于仓库中通过公开 API 重放真实服务请求的脚本。Replay 只读查询已真实发生的 records。输入相同的 revealed observations 和 canonical action signature 时，它返回已保存的 result 与 cost。它绝不调用会产生新 branch 的 generator 或 evaluator。

若不存在 exact action signature，replay 返回带原因的 `out_of_support`。它不得：

- 使用“最相似”的 recorded attempt；
- 推断未观察的 evidence grouping 或 target；
- 重新调用模型补齐缺失 branch；
- 虚构 reward 或零成本结果；
- 让 future observation、future evaluation label、Review decision 或 downstream outcome 影响更早的 decision。

Replay 复用已记录的 validator/scorer identity 与结果。重新运行 deterministic validator 只能作为独立 diagnostic，不能覆盖原始结果或 nomination。P1 必须报告基础 exact-replay coverage：supported requested decision / all requested decision，以及 complete supported trajectory / all requested trajectory。Unsupported decision 与 trajectory 必须留在分母中，并按 workload、policy、operation 以及适用时的 environment 报告。这些比例只衡量已记录历史能否被精确 replay，不表示其他 policy 已经获得足够的 action support。

### P1 验证

固定 fixtures 必须证明：

- record round-trip 与 schema-version rejection 行为；
- 精确复现 baseline action sequence、outcome、nomination 和 cost；
- absent 或 mismatched action 稳定返回 `out_of_support`；
- replay 期间不存在 model call、Candidate creation、Review mutation 或 Runtime write；
- 不泄漏 future information；
- optional controlled payload 的 redaction/retention 行为。

P1-MVP 不声称支持统计上完整的跨 policy replay，也不声称会改善未来 live run。P1 仍然要求上述基础 exact-replay coverage。Split/holdout isolation、跨 policy action support/coverage 和 policy-level replay comparison 属于延后的 P4 扩展。

## P2：小型 Experience-only spike

这里的 spike 指有明确输入、预算和退出条件的限时可行性实验，不是新的生产 API，也不是默认行为变更。

P2 只覆盖异步、显式开启的 `refine_experience` 实验，并通过 Dream Evolution Evaluation Harness 使用 `powercontext.artifact-evolution/v1`。现有 `OFF`/`ON` arm 属于 evaluation 服务的处理开关（当前表示 plugin disabled/enabled），不是 Dream policy variant。Harness 可以通过 adapter 复用它的 Scope isolation、workload selection 和 report 能力，但必须定义 Dream-specific treatment 与 result projection。Dream attempt 不经过 Bub/Harbor catalog 或独立的 SWE-Pro runner。Spike 不新建 arm、holdout 或统计分配子系统。Scheduled Experience incubation 和 `derive_skill` 保持不变。近期 spike 分为两种分析。

Baseline 是当前 Dream 行为：一次 run 处理调用方选定的一个问题并产生一个结果；可恢复的执行失败可以用同一组输入重试。公平比较应使用预先登记、可比且匹配的 workload，并在 outcome 产生前固定 baseline/experimental allocation。如果 workload 规模足够，之后再增加随机分配。只在 baseline 失败后触发 candidate 只能回答补救问题，不能回答 1-vs-2 attempt 的总体比较，因此补救结果必须单独报告。实验 policy 仅在以下情形请求有限的 additional proposal attempt：

- deterministic validation 发现不存在 eligible proposal；
- exact input 暴露了实验 action space 能处理的结构性 duplicate、target 或 conflict condition；
- 预先登记的 experimental decision rule 在预算内要求额外 attempt。

当前公开 `DreamBudget.max_model_calls` 为 `1..2`，第二次 execution attempt 已属于 retry 语义。Spike 不提高这个上限、不挪用 retry 预算，也不把两个独立 branch 塞进一个现有 `DreamRun`。Additional proposal attempt 首先使用 evaluation-owned shadow budget。Shadow attempt 不创建 Candidate，实际 Candidate 仍走当前 Dream 行为。若要让多分支直接产生一个 Candidate，必须另行设计 Dream budget/API contract 和 migration。

Spike 必须区分 proposal attempt 与 recoverable inference retry。二者都计入总 model-call 与 wall-time cost，但只有 proposal attempt 形成独立的 comparison branch。

执行前固定 proposal attempt 上限、包含 retry 的总 model call、input/output token、wall time 和 concurrency。以 `DreamRun.usage` 作为 live generation cost 的权威口径，shadow evaluation cost 单独报告，不能重复相加。RFC 在完成 workload 测量前不规定数值默认值。触达任何限制时停止 run 并记录原因。

### 预先登记的比较规则

对于每个匹配的 workload `W`，Harness 使用同一组 immutable input 运行当前 Dream action `B`（baseline），并在 decision rule 允许时运行一个 additional shadow action `S`。`B` 仍是现有 Candidate/Review 路径中唯一可能进入的结果。P2 的主要 quality comparison 在固定 workload 上分别评估 `B` 和 `S`；不使用 `max(B, S)` 作为 best-of-two 生产结果，也不把 `S` 当作 nominated Candidate。

固定 fixture 的规则如下：

下表中的 score 仅用于说明规则；实际 Review scale 与最小 effect 在运行 workload 前固定。

| Workload | Baseline `B` | Shadow `S` | Quality comparison | Candidate 与 cost 处理 |
| --- | --- | --- | --- | --- |
| `W1` | Eligible，blinded Review score 为 3 | Eligible，score 为 4 | 配对差值 `S - B = +1`，两个 score 都报告 | `B` 仍是唯一 Candidate result；计入 `B`、`S` 的执行、retry 和 validation cost |
| `W2` | Eligible，score 为 3 | 被 deterministic validation 判定为 ineligible | 记录 `B` 的 score；`S` 记为 eligibility failure，不填充虚构 quality score | `B` 仍是唯一 Candidate result；计入两次 attempt 及其 validation/retry cost |
| `W3` | Ineligible | Ineligible | 记录 zero eligible proposal，不分配 synthetic quality score | 不产生 Candidate；计入所有已发生的 execution、retry 和 validation cost |
| `W4` | Eligible，score 为 4 | Eligible，score 为 4 | 记录 tie，quality delta 为 0 | `B` 仍是唯一 Candidate result；计入两次 attempt 及其 validation/retry cost |

如果 `B` execution 失败而 `S` 成功，该 workload 单独进入 rescue-after-failure 分层，不进入主要的 one-versus-two-attempt 比较。Human Review effort 单独报告；每个 workload 的 evolution cost 包含 baseline 和 shadow 的 model call、retry、wall time、token、deterministic validation 以及失败或丢弃的 attempt。共享 setup cost 只计入一次，并记录分摊方式。缺失 score 或失败 attempt 不能从分母中静默删除。

Spike 开始前登记继续推进所需的最小 paired quality effect 或 cost reduction。Tie 表示零改进，zero-eligible workload 表示 eligibility failure 而非 quality gain。只有在预先登记的 quality/cost 条件满足，且 Scope、lineage、target、fabrication 与 Review-bypass guardrail 均保持干净时，exit gate 才能通过。

### 初始 validators

Spike 只使用 deterministic validators：

- output-schema completeness；
- exact evidence 与 citation resolution；
- Scope consistency；
- create/replacement target 与 current-head correctness；
- lineage completeness；
- exact 或可确定检测的 duplication；
- 可确定检测的 conflict；
- budget 与 cost accounting compliance；
- 防止捏造 evidence、outcome、approval、publication、installation 或 execution claim 的 invariants。

这些检查只判断 eligibility，不判断 semantic superiority。语义层 proposal quality 通过固定 workload 上的 blinded human Review 评估，并由 fresh live Dream validation 验证。第一轮 spike 不引入 LLM evaluator。

### Nomination 与退出条件

所有 attempts 留在 evaluation bundle 中。第一轮 shadow spike 不提名 shadow branch；只有现有 live Dream 结果可以进入 Review Inbox。后续若要提名 branch，需要另行的 contract change。被丢弃的 branch 不会成为 Candidate。

Spike 报告 quality/cost Pareto frontier，不将所有指标混成一个分数。只有在可比预算下提高 grounded reusable-proposal quality 或降低成本，同时不增加 Scope、lineage、target、fabrication 或 Review-bypass violation 时才继续。否则停止 multi-attempt 工作；P0 与 P1 仍可独立保留。

# 评估与成本模型

评估是必需项，但与阶段相称：

| 阶段 | 必需证据 |
| --- | --- |
| P0 | 行为等价与 invariant 保持 |
| P1 | Replay fidelity、record completeness、基础 exact-replay coverage、leakage check 与 replay cost |
| P2 | Deterministic eligibility、blinded Review disposition/reason、分开的 proposal-attempt 与 execution/retry/model-call/token/wall-time cost，以及 fresh live validation |
| 有条件的后续工作 | Approved Revision recall/usage、recurrence 或 Skill validation、task outcome、runtime、token、turn 与 tool call |

Candidate 数量或 approval rate 不能作为唯一目标。比较应分别报告：

- proposal validity 与 semantic quality；
- Review load 与 reviewer disagreement；
- evolution cost，包括失败和被丢弃的 attempts；
- 能通过 exact refs 归因时的 delayed downstream effect。

Runtime、token、turn 和 tool-call 改进是合理的下游指标，但只能在 approved Revision 被用于可比任务后观察。安装本机制不保证这些指标自然改善，报告时也必须同时列出新增 evolution cost。

# 与 recurring-failure outcome 分层

Recurring-failure-repair ledger 与 replay bundle 回答不同问题：

```text
ProposalAttempt / replay record
  = Dream generation strategy 探索了什么

recurrence ledger
  = approved Experience Revision 后来在真实任务中发生了什么
```

Recurrence ledger 负责 evidence-derived `selected`、`recurred` 和 `avoided` event。Replay bundle 负责 proposal action、output、validation、nomination 和 exploration cost。二者保持独立的 semantic store，只能通过 exact Candidate、Artifact Revision、DreamRun 和 Source refs 关联。

未获批准的 attempt 不能得到 recurrence outcome。缺少 `selected`、`avoided`、Skill invocation 或 task outcome evidence 表示 unknown，不是负向观测。Replay 派生 delayed metric 时必须保留这一区别。

# 有条件的后续工作

## P3a：Experience-to-Skill

Experience-only spike 通过退出条件后，可以另行提交 scoped change，把同样的 bounded attempt 与 nomination 机制应用到现有显式 `derive_skill` Dream operation。它必须继续保持每个 run 最多一个 Candidate、标准 Skill validation、人工 Review 和显式 publication/installation authority。

## P3b：usage-driven Skill replacement

PowerContext 目前有 `skill-usage` evidence model 与 recording entry point，generation lineage contract 也包含 `SkillGenerationOrigin.USAGE`。这些是前置基础，不代表自动的 usage-driven replacement producer 已存在。

只有 selection、invocation、validation、task outcome、environment fingerprint 和 missing-data rate 都具备覆盖率与质量 gate 后，才应设计该 producer。Non-invocation 和缺失 outcome 绝不能计为成功。P3b 需要独立的详细方案与 validation workload。

## P4：跨 policy replay research

P4 可以研究 realized history 上不同的 attempt-allocation、pruning、stopping 与 nomination policy，但当前不排入工程交付。只有 logging-policy 数据提供足够的跨 policy action support 与 coverage、RFC 1229 workload manifest 与 arm assignment 稳定且 promotion gate 已预先登记时，这项研究才有意义。

Replay result 只筛选 candidate，不授权部署。即使 policy 在 supported replay 上得分更好，仍必须经过 fresh live Dream run、fresh workload evaluation、人工 Review 和 code/configuration review。任意 model-generated executable policy code 不在范围内。

# 隐私、保留与安全

- 普通生产默认不为 replay 保存完整 evidence body 或 prompt；
- Evaluation bundle 优先使用 synthetic、reviewed 或 redacted evidence，并带显式 retention 与 access policy；
- Digest 能证明 identity，但不能使敏感 content 适合公开；
- Exact Scope 与 authorization check 作用于 live input；不得通过 replay 恢复无权访问的 body；
- Log 与 report 不得暴露 evidence content、credential、model secret 或 cross-Scope identifier；
- Replay 只读，不具备 Candidate、Review、Artifact、publication、installation 或 execution 能力。

# 兼容性与 rollout

P0 不改变 API、storage schema、migration、default 或可观察 Candidate 行为。P1 首先作为 public Runtime contract 之外的版本化 evaluation format。P2 仅用于显式 evaluation 或 opt-in experimental control；关闭它即可恢复当前 explicit Dream 行为。

若要在 Runtime storage 中持久化 attempt、通过 HTTP 暴露它、修改 `DreamRun` schema 或启用 production capture，必须另行完成 contract review 与 migration plan。本 RFC 不授权这些变化。

# 缺点与替代方案

该设计在出现可见的生成改进前增加了 instrumentation 与 evaluation 工作。严格的 replay support 也可能使早期数据过于稀疏，无法比较 policy。这是有意接受的限制：用推断的生成结果填补缺口会使 replay 结论失去可信度。

保持当前 Dream 行为不变成本更低，也始终是 baseline。直接默认增加多个 attempt 实现更简单，但会在证明价值前提高成本与 Review 压力。先优化 `prepare_context` 会让在线热路径承担错误的实验风险。LLM evaluator 可能提供更丰富的排序，但会在 record contract 可信前引入另一层非确定 policy、成本与校准问题。

# 尚未解决的问题

1. 对 P2 `refine_experience` spike 而言，最小且有用的 canonical action vocabulary 是什么？
2. Dream adapter 应复用哪些现有 evaluation isolation 与 report 组件，哪些 adapter 字段需要在记录第一个 fixture 前固定？
3. 在 `max_model_calls <= 2` 不变的前提下，哪些条件应触发 additional proposal attempt，怎样的预算对 baseline 公平？
4. 受控 replay bundle 应保存加密 payload，还是只保存 manifest/digest，并单独管理 fixture content？
5. 哪些 exact refs 与 retention window 足以关联 approved result 和 recurrence ledger，同时不耦合两个 store？
6. 怎样的 blinded Review protocol 和最小 workload size 足以支撑 spike 结论？
7. 怎样的 usage coverage、missing-outcome rate 和 environment stratification 可以支持启动 P3b？

# 参考资料

- [Dream-RSI 论文](https://arxiv.org/abs/2609.14858)
- [中文解读](https://mp.weixin.qq.com/s/VRjsoQLqHx80NZpeS5aqkA?scene=1)

论文报告的改进依赖其任务、模型、预算和评估器，不应被当作 PowerContext 的预期收益。
