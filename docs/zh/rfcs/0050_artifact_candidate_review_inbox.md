- Proposal Name: `artifact_candidate_review_inbox`
- Start Date: 2026-07-29
- RFC PR: [oceanbase/powercontext#50](https://github.com/oceanbase/powercontext/pull/50)
- Related RFCs: [RFC 0001](0001_product_definition_and_vision.md)、[RFC 0014](0014_memory_layer_design.md)、
  [RFC 0019](0019_local_source_memory_runtime.md)、[RFC 0028](0028_context_pack.md)

# Summary

本 RFC 为需要显式 Review 确认的 Artifact Family 定义 Artifact Candidate 与 Review Inbox。

是否进入 Review 不由用户选择，也不取决于是否使用 LLM，而由 Artifact Family 固定决定：Memory 继续直接写入 Artifact；Experience 和 Skill 必须先成为 pending Candidate，批准后才能形成 Artifact Revision。Task Outcome 尚未定义，不在本 RFC 中预设 Review policy。Handoff 是组合这些来源得到的下游交接结果，也不作为并列 Family 纳入本 RFC 的 Review policy。

Candidate 是持久化的不可信提案，不是 Artifact，不进入搜索或 PreparedContext。Review Inbox 是当前 scope 中 pending Candidate 的查询视图。首个实现随 Experience Artifact Family 一起交付，Skill 后续复用；不单独实现一个没有使用方的通用工作流框架。

本次评审需要确认四项设计：

1. Review policy 固定在 Artifact Family 上，用户不填写 mode；
2. Memory 直接写入，Experience、Skill 必须 Review；
3. pending/rejected Candidate 与 Artifact 检索和 PreparedContext 完全隔离；
4. Review operation 通过 HTTP、Python Client、CLI 和 MCP 一致暴露；MCP 不是单独的 approval policy 边界。

# Motivation

不同 Artifact 的风险不同。

Memory 保存事实、约定和偏好，写入频繁，并且可以保留 exact evidence。如果每条 Memory 都要求单独 Review 确认，Review Inbox
会变成日常操作负担。

Experience 和 Skill 则是更高阶的制品：Experience 从多个结果中归纳 situation、action、outcome 和 lesson；Skill 再把 Experience
蒸馏成可复用步骤。错误的归纳、语义合并或操作步骤会影响后续多个任务，因此需要显式 Review。

PowerMem 已经验证了 Experience/Skill distillation、dedup 和 merge 的价值，也说明直接
`distill -> merge -> store` 存在治理风险。PowerContext 保留自动生成能力，但把高阶制品的“生成”和“确认”分开：

```text
Memory -> Artifact Revision

task result evidence -> Experience Candidate -> Review Inbox -> Experience Revision
Experience           -> Skill Candidate      -> Review Inbox -> Skill Revision

Memory + optional Task Outcome + approved Experience/Skill -> handoff context
```

现有 Source-to-Memory 链路保持不变：

```text
Source window -> CandidatePipeline.extract() -> Memory Revision
```

# Guide-level explanation

## 哪些 Artifact 需要 Review

首版使用固定 policy，不提供用户配置或通用规则引擎：

| Artifact Family | Review policy | 结果 |
| --- | --- | --- |
| Memory | `direct` | 通过现有 Memory 写入路径直接形成 Revision |
| Experience | `review` | 先进入 Review Inbox，批准后形成 Revision |
| Skill | `review` | 先进入 Review Inbox，批准后形成 Revision |

`direct` 不表示创建一个自动 approved 的 Candidate，而是完全不创建 Candidate。`review` 表示该 Family 的创建和修改必须先进入 Review Inbox，批准后才能提交 Artifact Revision。新增 Artifact Family 必须在自己的 RFC 中声明 policy；Runtime 不对未知 Family 猜测默认值。

## 例子一：普通 Memory 直接写入

用户要求 Codex 记住一条项目约定：

```text
“修改 OpenAPI 后运行 contract tests。”
```

Codex 调用 `remember_memory`，Runtime 按现有流程直接创建 Memory Revision。用户不填写 mode，也不需要再到 Review Inbox 批准一次。`POST /v1/memory/flush` 处理 Source window 时同样保持现有直接写 Memory 的行为。

## 例子二：Experience 必须 Review

任务系统提供一组有边界的结果 evidence，包括目标、变更和测试结果。Runtime 从它归纳出：

```text
Candidate: cand_exp_123@1
Family: experience
Proposal: “修改 OpenAPI 后，重新生成 Client 并运行 contract tests。”
Evidence: source:task-result/run_42
Status: pending
```

这段内容是跨任务经验，在 pending 状态下不会进入 Artifact search 或 PreparedContext。审核者批准后，Runtime 才提交
Experience Revision。

## 例子三：先修改 Experience，再批准

模型提出的 Experience 过度概括：

```text
cand_exp_124@1: “修改任何 YAML 文件后都运行 contract tests。”
```

审核者执行 `revise`，提交完整的新 proposal：

```text
cand_exp_124@2: “修改 openapi/powercontext.yaml 后运行 contract tests。”
```

version 1 保持不可变，version 2 仍为 pending。审核者确认 version 2 后再批准，保证最终写入内容与实际审核内容一致。

## 例子四：Skill 被拒绝

Runtime 从已批准 Experience 蒸馏出 Skill，但缺少失败处理：

```text
Candidate: cand_skill_7@1
Family: skill
Steps: modify spec -> generate client -> run tests
Failure handling: missing
Status: pending
```

审核者拒绝该 Candidate。它不会形成 Skill Artifact，也不能被发布、挂载或进入 Context Pack。即使 Skill 被批准，是否允许
执行或挂载仍由后续 Skill governance 决定，Artifact approval 不等于执行授权。

# Reference-level explanation

## Family review policy

Review policy 是 Runtime 内置的 Family contract，不是每次请求的参数：

```text
direct families: memory
review families: experience, skill
```

- `direct` Family 继续使用自己的 validation、identity、Revision 和 CAS 语义；
- `review` Family 的公开创建和修改入口只生成 Candidate，不直接提交 Artifact；
- 只有 Review approval 可以调用 `review` Family 的内部 Artifact commit；
- policy 不根据调用者、MCP、HTTP、CLI、LLM 或规则动态变化；
- 首版不增加 policy DSL、动态 registry、租户级开关或 per-request override。

首个实现与 Experience Family 一起交付。Skill 出现后复用同一 Candidate envelope 和 lifecycle。Experience 和 Skill 的
content schema 分别由各自 RFC 定义，本 RFC 不提前定义任意 JSON payload。Task Outcome 的 schema、生命周期和 Review
policy，以及 Handoff 的组合和持久化语义，均由后续 RFC 定义。

## Candidate model

Candidate 由 family-neutral envelope 和 family-owned typed proposal 组成：

| 字段 | 含义 |
| --- | --- |
| `scope_id + candidate_id + version` | Candidate 的精确乐观并发引用 |
| `family` | `experience` 或 `skill` |
| `status` | `pending`、`approved` 或 `rejected` |
| `proposal` | 对应 Family 定义的完整强类型提案 |
| `sources/artifacts` | 支持提案的 exact evidence，至少存在一项 |
| `target` | 修改现有 Artifact 时指向 exact active ArtifactRef |
| `reason` | 不可信说明，不参与授权或排序 |
| `result_artifact` | 批准后指向 exact Artifact Revision |
| `decision_reason` | 拒绝时由审核者填写的原因；其他状态为空 |

Candidate reference 不是 `ArtifactRef`。Candidate 没有 Artifact identity，也不能被 Artifact catalog、Family search index 或
Context builder 读取。

## Lifecycle and concurrency

```text
create version 1 -> pending
pending --revise--> pending version N+1
pending --approve-> approved
pending --reject--> rejected
```

`approve` 只确认当前 Candidate version，不能同时修改 proposal。需要修改时先执行 `revise`，创建完整的新 version，再批准
新 version。`approved` 和 `rejected` 是终态，首版不支持 reopen。

修改现有 Artifact 的 Candidate 必须同时把 exact `target` 放入 `artifacts` evidence。这样批准后的新 Revision 会在 lineage
中保留直接前序，而不是只依赖 Candidate envelope 中的临时目标字段。

所有 Review 写操作都要求 `expected_version`。Runtime 同时执行两层并发校验：

1. Candidate version CAS：防止审核者操作过期 proposal；
2. Family target CAS：防止 proposal 基于已经变化的 Artifact head 提交。

stale Candidate 返回 `candidate_conflict`；stale target 返回 `artifact_conflict`。Runtime 不自动三路合并。

## Persistence and transactions

Builtin Runtime 使用两个逻辑表：

- `artifact_candidate_heads` 保存当前 version、status 和审批结果；
- `artifact_candidate_versions` 保存每个不可变 proposal version。

Review Inbox 是 pending head 与当前 version 的查询，不增加 queue、assignment 或 search index 表。模型生成在数据库事务外
运行；Candidate 写入完成后才出现在 Inbox。

批准时，Family Artifact commit 与 Candidate `approved` 状态必须在同一数据库事务提交。任一步失败都整体回滚，Candidate
保持 pending。SQLite 与 OceanBase 必须通过相同的 lifecycle、CAS、rollback 和 isolation contract tests。

## API and compatibility

OpenAPI 继续是 HTTP contract 的 source of truth。Review Inbox 增加：

| operationId | 作用 |
| --- | --- |
| `list_artifact_candidates` | 按 scope、status 和可选 family 分页；默认只列 pending |
| `get_artifact_candidate` | 读取当前 Candidate head |
| `approve_artifact_candidate` | 按 expected version 批准，不接受内容修改 |
| `reject_artifact_candidate` | 按 expected version 和原因拒绝 |
| `revise_artifact_candidate` | 按 expected version 提交完整 replacement proposal |

首个 Experience vertical slice 还提供 `propose_experience` 和 `get_experience`：前者只创建 pending Candidate，后者只按
exact `ArtifactRef` 读取已经批准的 Experience Revision。对应 HTTP 路径分别是 `/v1/experience/propose` 和
`/v1/experience/get`；五个 Review 路径位于 `/v1/artifact-candidates/` 下。

生成的 Python Client 暴露相同操作，CLI 提供 `candidate list/show/approve/reject/revise`，MCP 则把五个 Review
operation 全部投影为 tool。transport 不改变 Candidate validation、`expected_version` 校验或原子 approval transaction。
PowerContext 不把 MCP 可见性作为授权边界；需要 reviewer separation 的部署必须控制 MCP endpoint 的访问权限。

本 RFC 不修改现有 Memory contract：

- `POST /v1/memory/flush` 继续把 Source window 处理成 Memory；
- `remember_memory`、`revise_memory_entry`、`retire_memory_entry` 保持不变；
- `MemoryRememberMode` 及其现有行为保持不变；它只控制 Memory 内容的生成方式，与 Review 无关；
- Codex Hook、现有 Memory MCP tools 和 `prepare_context` contract 保持不变；Review 新增五个 MCP tools。

Candidate/Review 的代码与第一个 Experience vertical slice 一起实现，不先交付只有表和空 API 的基础设施。

## Retrieval and trust boundary

以下不变量必须成立：

- pending/rejected Experience 或 Skill Candidate 不进入 Artifact/Family search；
- pending/rejected Candidate 不进入 `prepare_context`；
- 只有 approval 成功后的 Experience/Skill Artifact 才可能成为未来 Context contributor；
- Candidate status 不是 retrieval ranking signal；
- Candidate 内容始终按不可信数据展示，body、reason 和 evidence preview 不写日志；
- `scope_id` 仍是业务分区，不是认证或 ACL。

PreparedContext v1 当前读取 active Memory 与相关的 approved Experience 当前 head。Experience 获批后会进入
scope-local 可重建 FTS projection 的候选范围，但是否在 query 和共享 byte 预算下被选中并不保证。Skill approval
仍不会扩展 Context Pack。

## Acceptance

| 场景 | 通过条件 |
| --- | --- |
| Family routing | Memory 直接写 Artifact；Experience/Skill 只生成 Candidate |
| Inspect | Client/CLI 能查看 family、proposal、version 和 exact evidence |
| Gate | pending/rejected Candidate 不进入 Artifact search 或 PreparedContext |
| Revise | 创建不可变的下一 Candidate version，状态保持 pending |
| Approve | expected version 只成功一次，Artifact commit 与 approved status 原子提交 |
| Conflict | stale Candidate 或 stale Artifact target 返回 typed conflict，不自动 merge |
| Reject | 不写 Artifact，终态 Candidate 不能再次 approve/revise |
| Compatibility | 现有 Memory flush、HTTP、MCP、Hook 和 PreparedContext v1 public envelope 不变 |
| MCP parity | MCP 可按相同 lifecycle 与 CAS 规则列出、读取、修订、批准和拒绝 Candidate |

# Drawbacks

- Memory 不经 Review，错误内容需要通过现有修订语义纠正。
- Experience 和 Skill 不会在生成后立即可用，必须等待显式 Review。
- 没有 assignment、通知或批量操作时，Candidate 可能积压。
- 固定 policy 很简单，但每个新增 Family 都必须明确选择 `direct` 或 `review`。
- Candidate head/version 增加了存储和迁移成本。

# Rationale and alternatives

| 方案 | 结论 |
| --- | --- |
| 所有 Artifact 都 Review | 不采用；会让高频 Memory 和任务记录产生过多审核负担 |
| 用户每次选择 mode | 不采用；容易选错，也允许调用方绕过 Family 治理规则 |
| 根据是否使用 LLM 决定 | 不采用；Runtime 无法可靠判断调用链背后是否存在模型 |
| 按 Artifact Family 固定 policy | **采用**；规则稳定、可解释，也不增加用户参数 |
| 现在建设通用 policy engine | 不采用；当前只有两个 `review` Family，没有动态规则需求 |
| 把 Candidate 做成 Artifact Family | 不采用；会让未审核内容提前获得 Artifact identity |

# Prior art

- RFC 0014 将 Memory pipeline 输出视为不可信候选，但仍由 Memory validation 直接提交；本 RFC 不改变该流程。
- RFC 0019 定义 Source window、cursor 和 Memory flush；本 RFC 保持其公开行为。
- RFC 0028 负责 PreparedContext selection 与最终 byte 预算；pending Candidate 继续与其隔离。
- PowerMem 的 Experience/Skill distillation、dedup 和 merge 提供生成能力参考，但自动 content review 不等于 Artifact approval。

# Limits and deferred work

首个实现采用以下确定上限：

- 一个 Candidate 最多包含 32 个 exact evidence reference，`sources` 与 `artifacts` 合计计算；
- `reason` 和拒绝时的 `decision_reason` 最多 2,000 个字符；
- Inbox 默认返回 50 项，调用方可以在 1 到 100 之间选择 `limit`，并使用 `next_cursor` 翻页；
- proposal payload 由 Family 的 typed schema 限制；首个 Experience 的四个字段分别最多 8,000 个字符。

Experience 与 Skill 的生成规则和 Family write semantics 由各自 RFC 定义。Candidate retention、reviewer identity、RBAC、
通知、批量审核和多 IDE UI 留给后续工作。

# Future possibilities

后续依赖关系是：

```text
task result evidence -> Experience Candidate -> approved Experience
approved Experience  -> Skill Candidate      -> approved Skill

Memory + optional Task Outcome + approved Experience/Skill
  -> multi-Artifact Context Profile
  -> PreparedContext (临时) / Handoff Artifact (需要重放或审计时持久化)
  -> retrieval quality evaluation
```

- Experience 的语义 merge 只能生成新的 Candidate，不能直接覆盖 approved Artifact；
- Skill approval 不自动授予执行、发布或挂载权限；
- Context Pack 只有在 Experience/Skill 成为真实来源后才扩展 contributor、预算和 provenance contract；
- 检索质量以现有 FTS/vector/RRF 为基线测量 freshness、conflict 和 diversity；graph、sparse、reranker 只有证明增益后才实现。
