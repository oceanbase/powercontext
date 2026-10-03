- Proposal Name: `decision_policy_governance`
- Start Date: 2026-09-28
- RFC PR: [oceanbase/powercontext#1770](https://github.com/oceanbase/powercontext/pull/1770)
- Tracking Issue: [oceanbase/powercontext#1649](https://github.com/oceanbase/powercontext/issues/1649)
- Related RFCs: [RFC 0046](0046_observability_foundations.md), [RFC 0050](0050_artifact_candidate_review_inbox.md),
  [RFC 0080](0080_memory_search_reranking.md), [RFC 1223](1223_human_agent_work_continuity.md),
  [RFC 1560](1560_recall_sufficiency_gate.md), [RFC 1652](1652_memory_quality_and_lifecycle.md),
  [RFC 1745](1745-decision-model-rerank-seam.md)
- Related work: [#1643](https://github.com/oceanbase/powercontext/issues/1643),
  [#1644](https://github.com/oceanbase/powercontext/issues/1644),
  [#1645](https://github.com/oceanbase/powercontext/issues/1645),
  [#1647](https://github.com/oceanbase/powercontext/issues/1647),
  [#1648](https://github.com/oceanbase/powercontext/issues/1648),
  [#1739](https://github.com/oceanbase/powercontext/pull/1739),
  [#1740](https://github.com/oceanbase/powercontext/pull/1740),
  [#1742](https://github.com/oceanbase/powercontext/pull/1742),
  以及 [#1745](https://github.com/oceanbase/powercontext/pull/1745)

# Summary

本 RFC 为 PowerContext 中的窄域决策门定义一层共享治理契约。决策门是由确定性 Runtime 代码调用的
`DecisionModel` 消费者：它只问一个有边界的问题，把模型回答与本地规则合并，然后返回一个类型化评估，供领域服务选择观察、标注、送审、暂缓或忽略。治理层提供 policy manifest、shadow/advisory/enforcing 模式、审计 observation、replay/rescore 规则、隐私边界和 promotion 条件。它不新增模型 provider，不把决策模型暴露成 tool，不改变公开 HTTP/OpenAPI 契约，也不宣称 Jev、Laya 或任何后端一定能提升 PowerContext 质量。它定义的是后续 Memory、Handoff、Experience、Skill 和安全门消费者从实验进入运行时行为之前必须满足的契约。

# Motivation

#1739 和 #1740 已经给 PowerContext 增加了 provider-neutral 的 decision role。随后几个 issue 都在问一个便宜的决策后端是否能帮助完成窄域判断：

- #1643：Memory reranking；
- #1644：语义相关性与证据覆盖；
- #1645：Handoff 和 Task Outcome 的证据检查；
- #1647：Experience 与 Skill 适用性；
- #1648：重复和冲突 Memory 候选。

这些消费者的领域影响不同，但风险高度相似：

1. 模型概率可能被误读为授权。
2. provider 故障可能被记录成干净通过。
3. 托管后端可能收到部署方并不想外发的材料。
4. 阈值可能从其他系统照搬，尽管损失函数完全不同。
5. shadow 实验可能在没有 replay 证据时被 promoted。
6. 失败或 abstain 的决策可能被记录成已经实质判断过内容。

项目已经在这个边界上有具体压力。#1742 提出了 Memory 写入时的 evidence gate。#1745 提出了 decision-model reranker，并明确把 rerank 的 fail-closed 行为与 advisory gate 的 fail-open 行为分开。RFC 1652 将 Memory consolidation、supersession 和 lifecycle effects 留给 review-gated 流程。如果没有共享 policy 与 observation 契约，每个消费者都会重新发明自己的阈值、fallback、隐私和审计规则。

预期结果是一层很小的共享机制，使实现 PR 能按一致顺序推进：

1. 先用 shadow mode 定义 policy；
2. 在不改变领域行为的前提下收集 observation；
3. 用历史 observation replay 和 rescore 候选阈值；
4. 只 promote 该 policy 已经证明可以承担的窄域动作；
5. 领域权威、review、持久化和访问控制仍由所属服务负责。

这是一个 enablement RFC，不是对某个具体后端的采用建议。

# Guide-level explanation

## 什么是 decision policy

decision policy 是一个有版本的窄域判断描述。它说明：

- 哪个确定性 Runtime consumer 可以调用它；
- 要问的有界问题是什么；
- 哪些 subject 和 evidence 字段可以发送；
- 在任何模型请求之前，哪些本地规则应先裁决；
- 适用哪种后端失败策略；
- policy 是 disabled、shadow-only、advisory，还是允许影响领域动作；
- 审计和 replay 需要记录什么；
- 改变行为前必须满足哪些 promotion 条件。

policy 不是给 agent 用的 prompt registry。它由 PowerContext 代码调用，不由 host model 调用。host model 永远不会看到 `decision_evaluate` tool，也不会决定何时调用决策后端。

## 什么是 narrow gate

narrow gate 是 decision policy 的消费者。它接收一个领域事件，构造符合 policy 的请求，然后返回类型化 assessment。

示例：

- Memory 写入门判断候选 Memory claim 是否被引用证据覆盖；
- Handoff consult 判断某个 claim 是否需要人工核验；
- Experience/Skill 适用性门判断某个已批准 revision 是否适用于当前任务；
- duplicate/conflict 门判断候选 Memory entry 与精确 existing entry 之间是等价、冲突、互补还是未知。

gate 可以在模型之前运行本地规则。例如，Memory 写入门可以接受短到无法判断的内容，可以拒绝把敏感内容发给托管后端，也可以在 cooldown 期间避免重复调用。真正判断内容的本地规则算 adjudication。只决定“不外发给托管后端”的本地规则不算 adjudication；它是 fallback observation。

## Runtime modes

每个 decision policy 都从 disabled 或 shadow-only 开始。

`disabled`：
consumer 不存在，不发起 decision call，也不产生 observation。

`shadow`：
consumer 记录“如果启用会怎么判”，但领域动作必须与完全没有 gate 时一致。还没有在 PowerContext 数据上测过的新 policy 默认应先进入 shadow。

`advisory`：
consumer 可以标注领域结果、增加 warning，或创建 review suggestion。它仍不能自行 block、approve、merge、retire 或 execute。

`enforcing`：
consumer 可以执行一个明确允许的领域动作，例如 hold 一个 Memory write，或让已配置的 rerank stage fail closed。只有当所属领域契约定义了该动作、policy 声明了精确影响，并且 promotion 条件已经满足时，才能进入 enforcing。enforcing 不授予 approval 权限。

默认推进路径是：

```text
disabled -> shadow -> advisory -> enforcing
```

policy 可以永久停在任何模式。有些 consumer 永远不应该进入 enforcing。

## 用户和 operator 应该期待什么

Decision assistance 是 opt-in。普通 PowerContext 安装不需要 Jev、Laya 或任何其他 decision provider。

当使用托管 provider 时，部署必须显式选择哪些内容可以离开进程。policy 会记录这个内容边界。如果因为隐私规则无法发送足够材料，observation 应标成 fallback 或 unknown，不能记成“已检查且干净”。

policy 处于 shadow mode 时，用户看不到行为变化。operator 可以查看聚合 observation，并运行 replay job。replay 可以回答：

- 如果阈值是 X，写入门会 hold 哪些内容？
- advisory mode 会多产生多少人工 review？
- 哪些 reason 贡献了最多 fallback？
- 某个候选阈值有没有已知 false hold？

promotion 基于 replay 和带标签证据，而不是 provider 宣称。

## 用户不应该期待的边界

本 RFC 不新增通用决策工作流引擎。它不允许模型 approve Review candidate、accept Handoff、验证任务完成、deactivate Memory、install Skill，或替 agent 选 tool。这些仍然是现有 PowerContext 服务及其 review/authorization 契约拥有的领域动作。

本 RFC 也不改变 RFC 1745。Decision reranking 是一个特殊的非 advisory consumer，其失败策略在 RFC 0080 下是 fail-closed。共享 policy 层可以描述该角色，但不能把 reranking 变成 fail-open advisory gate。

# Reference-level explanation

## 命名概念

### `DecisionPolicy`

`DecisionPolicy` 是有版本、低基数的 runtime policy。它的稳定身份独立于后端模型版本。

必需字段：

| 字段 | 含义 |
| --- | --- |
| `policy_id` | 稳定标识，例如 `memory.write.evidence_sufficiency.v1`。 |
| `decision_kind` | 传给 `DecisionRequest.decision_kind` 的低基数 selector。 |
| `version` | policy schema/question 版本，独立于后端模型版本。 |
| `consumer` | 所属 consumer，例如 `memory_write_gate`、`handoff_evidence_consult` 或 `experience_applicability`。 |
| `mode` | `disabled`、`shadow`、`advisory` 或 `enforcing`。 |
| `failure_policy` | `fail_open` 或 `fail_closed`，按 consumer role 解析。 |
| `privacy_boundary` | subject/evidence content 可发送到 local-only、hosted-redacted，还是不能发送到外部后端。 |
| `local_rules` | 确定性前置规则及其 reason code。 |
| `question` | 有界问题模板。 |
| `subject_selector` | 被问题判断的领域值。 |
| `evidence_selector` | 允许作为 evidence 的精确引用或摘录。 |
| `outcome_mapping` | 如何把 `yes`、`no`、`abstain`、fallback 和 local-rule 结果映射为 gate assessment。 |
| `promotion_criteria` | 推进模式前所需的最低证据。 |

policy 文件或代码常量必须可评审。policy 不能在保持同一个 `policy_id` 和 version 的同时静默改变问题文本或阈值。

### `DecisionAssessment`

gate 在所属服务把它映射成领域动作之前，先返回一个领域中立 assessment。

```text
DecisionAssessment:
  policy_id
  policy_version
  mode
  coverage          # adjudicated | unadjudicated
  verdict           # allow | deny | review | unknown
  source            # local_rule | decision_model | none
  reason
  confidence
  used_fallback
  usage
  latency_ms
```

`coverage` 是第一真值轴：

- `adjudicated`：本地规则或后端对内容做出了实质判断。
- `unadjudicated`：gate 没有判断内容；任何领域 pass-through 都是 fallback 行为。

`used_fallback=true` 意味着 `coverage=unadjudicated`。当唯一发生的事情是“内容太敏感不能发送”“没有后端配置”“cooldown 生效”或“后端失败”时，policy 不能记录 `adjudicated+allow`。这些情况可以放行，但不是候选内容干净的证据。

### `DecisionObservation`

`DecisionObservation` 是一次 gate evaluation 的审计/replay 记录。它刻意与领域对象分开。它可以被发送到日志、evaluation artifact 或未来的内部 ledger；第一个实现不需要公开 HTTP 或 OpenAPI surface。

必需 observation 字段：

- operation identity 和 scope；
- consumer 与 policy identity；
- mode；
- sanitized subject/evidence references；
- redaction 与 privacy-boundary 结果；
- 如果调用了后端，则记录 model provider id 和 backend model id；
- model/policy versions；
- assessment；
- 实际采取的最终领域动作；
- usage 与 latency；
- fallback reason（如果有）。

默认不保存 raw subject/evidence text。replay 应优先使用精确 PowerContext reference，并在相同 authorization context 下重新解析。如果部署选择为离线 evaluation 保留 raw snippet，该保留行为必须显式启用并单独记录。

## Consumer classes

### Advisory consumers

大多数 gate 默认是 advisory。Advisory consumer 使用 fail-open 运行时语义：后端失败会变成 `unknown` 或 pass-through，所属领域行为照常继续，除非该 policy 已经被明确 promoted 到更强模式。

示例：

- Handoff evidence consult；
- Experience/Skill applicability recommendation；
- 在 Memory lifecycle review 之前的 duplicate/conflict observation；
- 托管 provider safety screen，其结果可以建议 review，但不能授权。

Advisory consumer 可以标注、告警，或在领域已有 pending review 概念时创建 review suggestion。它不能 approve、accept、execute、publish、deactivate、supersede 或 merge。

### Enforcing consumers

Enforcing consumer 只能在明确的领域契约内改变行为。

允许的例子：

- 如果 Memory service 定义了可见、结构化的 refusal，并且 policy 已被 promoted，Memory write evidence gate 可以 hold 一个写入。
- reranker 可以 fail closed，因为 RFC 0080 和 RFC 1745 已将 reranking 定义为非 advisory。

禁止的例子：

- duplicate/conflict gate 不能自行 deactivate 或 supersede Memory；RFC 1652 将这些影响保留给 review proposal 和已批准的 lifecycle operation。
- Handoff consult 不能把工作标成 complete，也不能 accept Handoff。
- Skill applicability gate 不能 install、publish 或 execute Skill。

## Local rules and code-first decisions

policy 可以在模型调用前运行本地规则。当代码能回答一个更便宜、更可靠的问题时，本地规则很有价值：

- candidate 为空或超出支持的长度边界；
- action 不在 gate 范围内；
- 检测到敏感 key，且 hosted privacy boundary 禁止发送；
- 相同内容已经在同一 policy version 下被 adjudicated；
- 确定性 citation 或 revision check 已经失败。

本地规则必须返回 reason code。它们也必须区分内容判断与传输/隐私决策。

示例：

| 本地结果 | Coverage | Reason |
| --- | --- | --- |
| candidate 低于最小有用长度，且 policy 定义为 pass-through | `adjudicated` | `too_short` |
| candidate 含敏感值，且禁用 hosted sending | `unadjudicated` | `sensitive_not_sent` |
| 没有后端凭据 | `unadjudicated` | `no_backend` |
| provider failure 后 cooldown 生效 | `unadjudicated` | `cooldown` |
| citation reference 无法解析 | gate 之前的领域错误 | `invalid_evidence_reference` |

## Shadow、audit 与 replay

policy 改变生产行为前必须先经过 shadow mode，除非维护者在实现 PR 中明确接受更窄的快速路径。

Shadow mode 要求：

1. 最终领域输出与 no-gate 路径 byte-for-byte 或语义等价。
2. 每次 gate attempt 都产生 `DecisionObservation`。
3. observation 区分 no-call、local-rule、successful backend、backend fallback、privacy fallback 和 parse failure。
4. 只要仍能解析足够精确引用，policy 就可以在不重跑整个 workload 的情况下 replay 或 rescore。

Replay 有两种形式：

- **Rescore**：复用已保存的后端输出，应用新阈值或 outcome mapping。
- **Re-evaluate**：重新解析精确引用，并在新 policy 或模型版本下调用后端。

两者都必须报告输入覆盖率。无法解析足够原始材料的 replay 结果不是 policy 的负面结果；它是不完整 replay。

## Promotion criteria

policy 在超越 shadow 前必须先声明 promotion criteria。

最低条件：

- 与 held-out evaluation 分开的 labeled calibration set；
- 当 consumer 可能看到中英文内容时，held-out 结果必须覆盖中英文案例；
- 对要 promote 的 enforcing action，不存在已知高严重度 false action；
- 按 reason 统计 fallback rate；
- 测量新增 latency 与 request cost；
- 为 human-review 或 hold outcome 设定 friction budget；
- 可回滚到前一模式。

具体数字阈值属于 policy 和领域损失函数。试图避免丢失真实观察的 Memory write gate，与试图避免错误合并历史的 duplicate/conflict gate，有不同的可接受错误。Hermes、OpenClaw、Jev cookbook、Laya 报告或其他 prior art 中的阈值都只能作为假设，不是默认值。

## Privacy boundary

每个 policy 声明一个内容边界：

| Boundary | 含义 |
| --- | --- |
| `local_only` | subject/evidence content 只能发送到进程内或 loopback 后端。 |
| `hosted_redacted` | content 只有经过 PowerContext redaction/sanitization 后才能发送到托管后端。 |
| `references_only` | hosted call 可以接收标识符、metadata 或有界非敏感 label，但不能接收 raw content。 |
| `no_external_call` | policy 只能使用本地规则。 |

实现必须尽量使用 PowerContext 既有 sanitization primitives，而不是发明并行 redaction scheme。如果 sanitization 移除了回答问题所需的材料，observation 是 `unadjudicated`，不是 `allow`。

## 与既有 RFC 的交互

### RFC 0080 与 RFC 1745

Decision reranking 由 RFC 1745 覆盖。本 RFC 不改变其 fail-closed 立场。共享 policy 层可以为 reranker 记录 observation 和 policy metadata，但必须保留 RFC 0080 的启动校验和运行时失败语义。

### RFC 1560

recall sufficiency gate 是 model-free，并继续作为默认 recall expansion policy。Decision policy 可以在 retrieval quality 周围增加语义 observation，但不会替换确定性 sufficiency gate，除非未来 RFC 显式改变该契约。

### RFC 1652

Memory quality 与 lifecycle effects 仍然是 review-oriented 且保留证据的。duplicate/conflict gate 可以产生 observation 或 proposal，供 RFC 1652 evaluation 使用，但不能直接 merge、deactivate、supersede 或 rewrite Memory。

### RFC 0050

Review Inbox 仍然服务于 review-owned families 和明确的 review proposal。只有所属 family 已支持这种流程时，gate 才可以创建 pending Candidate，而且模型不能 approve 它自己创建的 Candidate。

## 兼容性、持久化和 API 影响

本 RFC 本身不新增公开 HTTP endpoint，不新增 OpenAPI 字段，不要求新增数据库表，也不要求 migration。

实现 PR 可以增加内部数据结构、结构化日志、evaluation artifact 或可选私有 ledger。任何公开 diagnostic API、持久化 audit table 或外部可见 error contract，都必须在该实现 PR 或后续 RFC 中提出。

现有安装在没有 decision backend 时继续工作。接受本 RFC 不改变现有 Memory、Handoff、Experience、Skill、Review 和 PreparedContext 契约。

# Drawbacks

- 在所有 consumer 出现之前先引入 policy 层，会让第一个 gate 显得偏重。
- Shadow 和 replay 会推迟可见产品行为。
- 如果不与 RFC 0046 对齐，observation 设计可能变成第二套 telemetry 系统。
- 隐私安全的 replay 比保存 raw snippet 更难，而且有些 replay 会不完整。
- 如果评审没有持续强调 provider neutrality，共享层可能过拟合 Jev 风格的 yes/no 决策。

# Rationale and alternatives

## 为什么需要共享 policy 层

consumer 很窄，但风险控制相同。共享层让每个实现 PR 专注自己的领域问题，同时复用 fallback、audit、shadow、replay、privacy 和 promotion 的词表。

## 替代方案：每个 consumer 自己定义 gate

这对第一个 PR 更快，但会让 “fallback”“unknown”“checked”“shadow”“promoted” 的含义不兼容，也会让跨 consumer evaluation 变得不可比。

## 替代方案：所有 decision model 调用都 fail-open

这对 advisory consumer 安全，但对 RFC 1745 reranking 这种非 advisory 角色是错的。失败策略属于 consumer role，不属于 backend。

## 替代方案：把每个不确定结果都送 Review Inbox

这会把不确定性变成用户摩擦。有些不确定性应该被计数和观察，而不是交给人。policy 必须说明什么时候 `review` 才是正确 verdict。

## 替代方案：保存完整 raw prompt 以便 replay

这会让 replay 更容易，但违背显式 hosted-provider opt-in 的隐私边界。精确 PowerContext reference 加可选的 deployment-controlled retention 是更安全的默认值。

# Prior art

PowerContext 已经有相关基础：

- #1739 和 #1740 增加了 provider-neutral 的 DecisionModel role。
- RFC 1745 定义了 decision-model rerank seam，以及 reranking 的 fail-closed 区分。
- RFC 1560 定义了 model-free recall sufficiency gate 与 bounded expansion。
- RFC 1652 定义了 evidence-preserving 的 Memory quality 与 lifecycle 边界。
- RFC 0050 定义了 Review Inbox，以及 generated candidate 不能 approve 自己的规则。
- RFC 0046 定义了 observability foundations，observation stream 应复用它，而不是绕开它。

相邻系统提供了反例和经验。Hermes 风格的 Jev 插件展示了 cheap code-invoked gates、shadow mode 和 local rules 的价值，但它们的阈值与宿主特定 hook 不是 PowerContext 默认值。OpenClaw 风格的 decision role 说明，decision model 更适合作为 runtime collaborator，而不是 model-callable tool。Jev 与 Laya 是候选后端或参考点，不拥有 policy。

# Unresolved questions

- `DecisionObservation` 的第一个具体存储目标是什么：结构化日志、evaluation artifact，还是私有内部 ledger？
- 默认应保留哪些 observation，保留多久？
- policy definition 应以 Python constant、JSON/YAML resource，还是两者并存？
- 如果 #1742 调整为符合本 RFC，它应使用哪些数字 promotion criteria？
- 第一个托管后端启用前，是否需要先有面向用户的 hosted-provider privacy configuration 文档？
- replay job 如何报告 authorization 变化：shadow 收集时可见的精确 reference，在 replay 时不再可见怎么办？

# Future possibilities

- 在 dashboard 中增加 policy registry 页面，展示 active policy、mode、fallback rate 和最近 replay 结果。
- 增加 CLI 命令，对最近 observation replay 一个 policy，并输出候选阈值。
- 为 decision policy 提供标准 evaluation bundle，供 Jev、Laya、本地规则和未来后端共用。
- 增加带 retention controls 的私有 decision-observation ledger，并支持导出做离线 evaluation。
- Review Inbox 支持按 policy 分组，让 review item 反链到触发它的 policy observation。
- 面向气隙部署的 local-only decision backend，复用相同 policy 与 observation 契约。
