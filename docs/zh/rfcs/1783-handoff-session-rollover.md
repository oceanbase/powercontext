- Proposal Name: `handoff_session_rollover`
- Start Date: 2026-09-29
- Status: Draft
- RFC PR: [oceanbase/powercontext#1783](https://github.com/oceanbase/powercontext/pull/1783)
- Tracking Issue: 尚未分配
- Related RFCs: [RFC 0001](0001_product_definition_and_vision.md)、[RFC 0014](0014_memory_layer_design.md)、[RFC 0019](0019_local_source_memory_runtime.md)、[RFC 0028](0028_context_pack.md)、[RFC 0048](0048_handoff_artifact.md)、[RFC 0082](0082_handoff_report.md)、[RFC 1489](1489_prepared_context_text_assembly.md)

# Summary

本 RFC 将 session rollover 定义为 Handoff 生命周期事件。当一个长 Agent 会话接近上下文上限，或开始积累过时假设、噪声和压缩损耗时，宿主或用户可以要求 PowerContext 准备一份 Rollover Handoff：一份用于在新会话中继续同一 scope 的、完整且带 evidence 的工作检查点。新会话随后从这份 Rollover Handoff 和有界 PreparedContext 继续，而不是继承旧 transcript 或有损会话摘要。

PowerContext 仍然是 Agent memory 与工作连续性系统。它不变成 Agent runtime、transcript store 或 provider 特定的上下文窗口管理器。Rollover Handoff 复用 RFC 0048 的 Handoff 内容契约和 commit 语义，在 prepare 阶段增加 advisory rollover reason，并保持长期 Memory 晋级必须显式发生。

# Motivation

长 Agent 会话的失效方式不同于普通的上下文缺失。即使模型仍有足够 token，会话也可能积累过时假设、陈旧计划、重复摘要、噪声工具输出，以及对早期措辞的隐式依赖。反复 compaction 可以维持表面连续性，却会削弱继续工作真正需要的精确事实。

PowerContext 已经拥有解决这个问题的持久边界：Handoff。Handoff 捕获 objective、state、disposition、next action、evidence 和 omissions。这正是新会话在不继承整个旧会话的前提下继续工作所需的最小状态。

如果没有 rollover 概念，宿主通常会落入两种较弱策略：

- 继续延长当前会话，直到模型或宿主不得不压缩它；
- 另开新会话，并依赖 Memory、旧 prompt 或非正式摘要恢复状态。

这两种方式都会模糊当前工作状态、长期项目知识与原始 evidence 的区别。本 RFC 定义一条更窄的路径：把 session rollover 变成显式 Handoff checkpoint。该 checkpoint 可以被检查、提交，并像其他 Handoff 一样被后续 Continue 使用；Source 和 Memory 继续保持既有边界。

## 评审重点

评审者应重点检查以下决策：

- session rollover 是否应属于 Handoff 语义，而不是 Memory、PreparedContext 或新的 Notes family；
- 该提案是否保留 RFC 0048 中临时 Prepared Handoff 与已提交 Handoff Revision 的区别；
- rollover 质量要求是否足以避免“继续之前的工作”这类模糊摘要；
- 是否可以允许自动准备 draft，同时把自动 commit 留给独立 policy；
- 与 PreparedContext 的关系是否足够窄，避免让 Context Pack 替代 Handoff；
- Source capture 是否足以作为可选 transcript evidence 的先例，而不把 PowerContext 变成完整 transcript store。

# Guide-level explanation

## Rollover Handoff

Rollover Handoff 是一种 Handoff，其准备原因是当前 Agent 会话不应再作为工作状态的主要载体。

典型触发包括：

- 用户要求在新会话中继续；
- 宿主观察到会话很长或接近上下文预算；
- Agent 报告当前上下文噪声过多、过时或压缩过重；
- 宿主已经发生或即将发生 compaction，需要给下一个会话留下干净的继续点；
- 人类希望在把工作交给另一个 Agent 前保存检查点。

Rollover 不表示工作已经完成。它表示下一个参与者应从可验证 checkpoint 继续，而不是从旧对话继续。

Handoff 内容仍然回答 RFC 0048 的问题：

| Field | Rollover 要求 |
| --- | --- |
| Objective | 要继续的 workstream objective |
| State | 新会话必须理解的当前工作状态 |
| Disposition | 通常为 `continuable`，也可以是 `blocked` 或 `complete` |
| Next action | 新会话首先应考虑的动作 |
| Evidence | 支撑 state 和 next action 的精确引用 |
| Omissions | 缺失检查、未验证假设、被排除材料，或未捕获的宿主上下文 |

Rollover Handoff 不是 transcript summary。它应排除不会改变下一会话行动方式的细节。

## 示例：把编码任务移到新会话

会话 A 已经为一个功能实现了数小时。它读过许多文件，尝试过两个设计，修复了测试，现在模型开始重复旧计划。用户说：

```text
准备一份 rollover handoff，我要在新会话继续。
```

PowerContext 准备一份 Handoff draft：

```text
Objective:
  Add session rollover support to Handoff without changing Memory semantics.

State:
  - The RFC draft exists in English and Chinese. [evidence: exact Artifact revision]
  - The design deliberately treats rollover as a Handoff lifecycle event. [evidence: user prompt Source]
  - No runtime implementation has been started. [evidence: Git diff Source]

Disposition:
  continuable

Next action:
  Review the RFC for consistency with RFC 0048 and RFC 0028, then open the RFC PR.

Omissions:
  - No full repository test suite has been run because this is a documentation-only change.
  - Host-specific token-budget signals are not specified beyond an advisory input.
```

用户可以检查并修正草稿。如果只是要复制到另一个会话，Prepared Handoff 可以直接 transfer。如果它要成为最新 workstream checkpoint，调用方必须显式 commit，把它提交为 Handoff Revision。

## 从 rollover 继续

新会话默认不应收到完整旧 transcript。宿主应要求 PowerContext 从 Rollover Handoff Continue，或要求 `prepare_context` 生成 continuation-oriented context pack。

新会话收到：

1. 作为 untrusted historical work state 的 Handoff 内容；
2. 精确 evidence 引用或 evidence-check 结果；
3. 在 context assembly 请求时选择的相关 Memory、Experience、Profile 或 Topic Memory；
4. 说明哪些内容没有带过来的 omissions。

新会话仍然必须把当前指令、当前请求、仓库规则、实时 workspace 状态和工具结果置于 Handoff 之上。

## 与 Memory 的关系

Rollover Handoff 不会自动创建或更新 Memory。

Rollover 期间的信息进入不同位置：

| 信息 | 目标位置 |
| --- | --- |
| 当前 objective、进度、next action、blocker | Handoff |
| 原始 prompt、工具输出、文件快照、测试输出 | Source，如果宿主捕获了它们 |
| 长期项目决策或约束 | Memory，只能通过显式 Memory 流程 |
| 可复用的排障经验 | Experience，只能通过既有 review lifecycle |
| 总结后的领域知识 | Topic Memory，通过自己的处理生命周期 |

这可以防止临时工作 checkpoint 污染长期项目 Memory。Rollover Handoff 可以引用 Memory，后续 review 也可以把 Handoff 中的持久决策晋级为 Memory，但晋级必须显式且可审查。

# Reference-level explanation

## 范围

本 RFC 定义：

- Rollover Handoff 作为现有 Handoff 生命周期的命名用法；
- rollover reason 和宿主信号；
- rollover 内容的额外质量要求；
- 新会话如何接收 continuation context；
- Handoff、Source、Memory、Experience、Topic Memory 和 PreparedContext 的边界。

本 RFC 不定义：

- provider 特定的 API，用于打开新模型会话或上下文窗口；
- PowerContext 中的完整 transcript 存储；
- 每次 rollover 自动创建 Memory；
- 新的持久 Notes family；
- 自动执行 Handoff next action；
- 实现优先级或上线策略。

## 产品模型

Rollover Handoff 不是新的 Artifact family。它是由宿主或用户在 prepare 时提供 advisory rollover reason 的 Handoff。共享内容契约仍然是 RFC 0048 的 Handoff 契约。

Prepared 与 committed 形式保持现有生命周期：

```text
Draft -> Prepared Rollover Handoff -> Transfer
                                  -> Commit -> Handoff Revision
```

rollover reason 属于 prepare 上下文。它们指导 draft generation 和 inspect，但初始实现不把它们纳入 Handoff content identity。已提交的 Rollover Handoff 仍是该 scope 线性 Handoff history 中的一个 Handoff Revision。读取 latest Handoff 不需要特殊处理。

## Rollover reasons

prepare 请求可以包含一个或多个 advisory reason：

| Reason | 含义 |
| --- | --- |
| `user_requested` | 用户明确要求新会话或 checkpoint |
| `host_context_budget` | 宿主估计会话接近上下文限制 |
| `host_compaction` | 宿主报告 compaction 已发生或即将发生 |
| `context_quality` | Agent 或宿主报告上下文陈旧、噪声大或过度压缩 |
| `delegation` | 工作将交给另一个 Agent 或人类 |
| `manual_checkpoint` | 调用方需要 checkpoint，但不声称会话不健康 |

reason 是 advisory。它们帮助 generation 聚焦于 fresh-session checkpoint，但不授权 commit 或执行，也不会创建新的持久 Artifact 类型。

## 质量要求

Rollover Handoff 必须足够自包含，使新会话能够安全开始。除 RFC 0048 validation 外，finalization 应拒绝或标记缺少以下内容的结果：

- 非空 objective；
- 至少一个当前 state statement；
- disposition；
- next action，或解释为什么没有 next action 的 disposition；
- state statement 与 next action 的 evidence，或说明 evidence 不可用的 omission；
- 对已知未验证检查、缺失宿主状态或相关被排除材料的 explicit omissions。

以下内容无效，或应要求修正：

- “continue the previous work”；
- “see the conversation above”；
- 没有 evidence 或 omission 的 next action；
- 没有测试结果引用或当前验证，却声称测试通过；
- generation 改写 objective，而不是使用调用方提供的 objective。

这些检查可以先作为 Handoff content model 的确定性 validation。未来生成流水线可以用模型起草内容，但模型输出本身不能满足 evidence 要求。

## 宿主集成

宿主集成可以分三档支持 rollover：

| Level | 行为 |
| --- | --- |
| Manual | 用户显式要求 prepare 或 commit Rollover Handoff |
| Advisory | 宿主检测到长会话或预算压力，并建议准备 Rollover Handoff |
| Automatic draft | 宿主在安全边界自动准备 draft，但 commit 仍遵循配置 policy |

Automatic commit 不在本 RFC 范围内。它需要独立 policy 决策，因为 commit 会推进 scope 的 Handoff history。

宿主可以提供 context-budget observation，例如模型名、估算剩余 token、compaction 次数或会话时长。除非它们有 Source evidence 支撑，PowerContext 将其视为不可信宿主观察。它们可以影响 draft 和展示，但不能证明 provider 确实会打开或保留某个会话。

## Continue 与 PreparedContext

Continue 仍然是基于 Handoff 行动的主要操作。PreparedContext 仍然是一次 Agent turn 的有界 context package。两者可以协作，但不合并契约。

continuation-oriented `prepare_context` profile 可以选择：

1. 当前 scope 的 latest Handoff，如果调用方请求 Handoff-backed continuation；
2. 该 Handoff 的精确 evidence 引用或 evidence check 摘要；
3. 现有 assembly 预算内的相关 Memory 和 Experience；
4. 显式请求时的 Profile 或 Topic Memory section。

本 RFC 不要求 RFC 1489 立即把 `handoff` 加为 assembly family。它只定义语义边界，使后续实现 PR 或 RFC 可以加入该 family，而不改变 Handoff 含义。

PreparedContext 必须继续把历史材料标记为 untrusted。它不能把历史 objective 变成当前目标，不能执行 next action，也不能隐藏 evidence omissions。

## Source 与 transcript 边界

Rollover 可以引用已捕获的 Source records，包括宿主 prompt、被选择的工具输出、测试结果或人类编写的 note。这不要求 PowerContext 摄取或保留完整 session transcript。

宿主决定自己被允许捕获哪些内容。如果相关 transcript 材料没有被捕获，Handoff 记录 omission，而不是假装材料可用。只有当 transcript 位置或 digest 可通过授权 Source adapter 读取时，宿主才能把它作为 evidence。

## 并发与幂等

已提交的 Rollover Handoff 使用 RFC 0048 相同的 CAS 行为。如果 draft 准备后 scope 的 Handoff head 已经推进，commit 报告 conflict。调用方必须读取新 head 并准备一份完整替代内容，或只 transfer 这份 prepared value 而不提交。

同一 finalized content 的重复 commit 必须遵循现有 Handoff commit 规则保持幂等。只有 rollover reason 变化，而内容与当前 head 相同，不得创建新 Revision。

## Access 与信任边界

Rollover 不削弱访问控制。接收方必须能读取 Handoff scope 和被引用的 evidence。缺失 evidence 只降级依赖它的 statement。

交给 Agent 的所有 Rollover Handoff 内容都是 untrusted history。当前用户请求、developer 和 system instructions、仓库指令、实时 workspace、当前工具结果拥有更高优先级。

# Drawbacks

该设计给 Handoff 增加了一种职责。评审者和实现者必须保持“工作 checkpoint”和“项目里程碑”的区别，避免 Handoff history 变得嘈杂。

如果内容质量检查太弱，rollover preparation 可能制造虚假的安全感。糟糕的 Handoff 可能比没有 Handoff 更危险，因为它看起来像经过审查的状态。

不暴露 context-budget 或 compaction 信号的宿主只能支持 manual rollover。这可以接受，但会限制自动化程度。

把 Handoff 加入 continuation-oriented context assembly 可能增加注入字节数，除非宿主选择紧预算。

# Rationale and alternatives

## 为什么选择 Handoff

Handoff 已经用 evidence 和 omissions 描述可继续工作状态。Session rollover 是工作连续性事件，不是知识摄取事件。复用 Handoff 可以保留现有 trust、evidence 和 CAS 语义。

## 替代方案：把 rollover notes 存进 Memory

这会用临时进度、过时 next action 和不完整验证污染长期 Memory。Memory 适合持久决策与约束，但不适合每个 session checkpoint。

## 替代方案：新增 Notes family

Notes family 可以表达 session-local scratch state，但它会在本用例中与 Handoff 重叠，并引入第二个工作连续性对象。本 RFC 为未来不同用例保留 Notes 空间，但 rollover 不需要它。

## 替代方案：存储完整 transcript

完整 transcript 在某些宿主中是有用 evidence，但成本高、敏感且 provider-specific。PowerContext 可以引用已捕获 Source records，而不成为 transcript archive。

## 替代方案：provider-specific `new_context`

某些宿主可能提供模型可调用的工具来打开 fresh context window。PowerContext 不应标准化该 provider action。它应标准化能让新会话安全继续的 checkpoint。

# Prior art

RFC 0048 将 Handoff 和 Continue 定义为现有工作交接边界。RFC 0028 与 RFC 1489 定义了带 trust wrapper、预算和精确引用的有界 PreparedContext 交付。RFC 0014 将 Memory 定义为持久项目知识，而不是 session state。

OpenAI Codex 围绕 token budgeting、模型可请求 fresh context window、history 与 notes tools，以及轻量 history-note hints 引入了上下文管理工作。可移植的思想不是 provider-specific tool call，而是当前工作上下文、显式工作状态和按需检索 evidence 之间的分离。

开发工作流中已经存在在新会话前手写 handoff 文档的实践。本 RFC 将该模式纳入 PowerContext 的 Handoff 语义，并加上 evidence 与 scope 边界。

# Unresolved questions

- 后续实现是否应把 rollover reason 作为独立 observation 持久化用于诊断，同时不改变 Handoff content identity？
- 已提交的 rollover Handoff 是否应通过独立 observation 在 Handoff Report 中视觉区分，还是 reason 保持为 preparation-only？
- Prepared Rollover Handoff commit 前必须通过哪些最小确定性 validation？
- continuation-oriented PreparedContext 应在 RFC 1489 assembly 中包含 Handoff，还是 Continue 应保持独立宿主步骤？
- 哪些宿主观察可以默认捕获为 Source，哪些需要显式用户或 workspace policy？

# Future possibilities

后续工作可以增加宿主特定 adapter，自动检测预算压力并建议 rollover。这些 adapter 可以保持可选且 fail open。

PowerContext 以后可以支持一个 continuation profile for `prepare_context`，用一个输出预算按固定顺序包含 latest Handoff、相关 Memory、Experience、Profile 和 Topic Memory。

Handoff Report 可以展示 rollover 密度、过时 workstream，或已经 checkpoint 但没有后续 continuation 的长会话。

评测可以比较通过 Rollover Handoff 继续的长编码任务，以及通过 transcript compaction 或非正式摘要继续的任务，分别衡量任务成功率与注入字节数。
