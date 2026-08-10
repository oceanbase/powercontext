- Proposal Name: `handoff_artifact`
- RFC Number: 0048
- Start Date: 2026-07-29
- Status: Draft
- RFC PR: [oceanbase/powercontext#48](https://github.com/oceanbase/powercontext/pull/48)
- Tracking Issue: 尚未分配
- Related RFCs: [RFC 0002](0002_core_sdk_product_model.md)、[RFC 0014](0014_memory_layer_design.md)、
  [RFC 0019](0019_local_source_memory_runtime.md)、[RFC 0028](0028_context_pack.md)

# Summary

Handoff 是一个参与者留给下一个参与者的工作说明。Continue 是使用这份说明继续工作的动作。

每份 Handoff 都回答相同的问题：

- 目标是什么；
- 当前到了哪里；
- 工作仍可继续、已经完成，还是被阻塞；
- 接下来做什么，或者已经没有下一步。

本 RFC 定义两种 Handoff。Prepared Handoff 是在会话间直接传递的临时值。提交后的 Handoff 是作为项目里程碑保存的
不可变 Artifact Revision。两者使用相同的内容和 evidence 契约，只有提交后的 Handoff 拥有持久化 identity 和历史。

Continue 可以使用显式传入的 Handoff，也可以读取当前 scope 最新的已提交 Handoff。PowerContext 会先检查其中引用的
evidence，再把内容作为 untrusted history 交给 Agent。当前指令、当前请求、仓库规则和实时状态始终优先。

RFC 0002 已经把 Handoff 定义为 Artifact Family，本 RFC 补齐其产品语义。本文是设计契约，不表示当前 Runtime
已经提供文中描述的全部 action。

# Motivation

多数会话边界只需要把工作交给下一个参与者，不需要再创建一条永久项目记录。如果每次交接都持久化，里程碑历史会
很快失去可读性，Continue 也会不必要地依赖存储。

有些 Handoff 需要长期保留。团队用它们标记进展、恢复工作，并复盘项目如何变化。这些里程碑必须不可变、可以定位，
并且在多个参与者更新同一 scope 时保持一致。

本 RFC 定义两条链路：

```text
Prepare -> Inspect -> Transfer -> Continue

Prepare -> Inspect -> Commit -> Review or Continue
```

第一条链路是临时交接。第二条链路把同一份内容加入持久化历史。

本 RFC 不定义任务、并行工作、自动选择里程碑或传输接口。它只定义 Handoff 内容、临时与持久化形式的区别，以及
用户可以依赖的行为。

# Guide-level explanation

## 准备 Handoff

PowerContext 在工作边界准备一份包含以下内容的草稿：

| Field | Purpose |
| --- | --- |
| Objective | 调用方期望达成的结果 |
| State | 理解当前位置所需的事实 |
| Disposition | 工作是否仍可继续、已经完成或被阻塞 |
| Next action | 一个可以接着执行的动作，或者没有动作 |
| Evidence | 支持 state 和 next action 的精确引用 |
| Omissions | 无法纳入或验证的相关材料 |

objective 由调用方提供。生成过程可以起草其他内容，但不能改写 objective，也不能编造 evidence。

用户或宿主可以检查和修正草稿。定稿过程会验证内容及其引用，随后得到内容不再变化的 Prepared Handoff。用户和
Agent 看到的是同一份内容。

prepare 只读取有界的候选 Source、Artifact 和 Memory。每条 state statement 和 next action 都引用原始 evidence。
如果 PowerContext 知道某项相关材料被排除或无法检查，就把它记录为 omission。omission 只描述已知缺口，不声称
Handoff 已经包含所有相关信息。

state 描述 Handoff 定稿时被认为仍然成立的事实。disposition 说明这份 Handoff 所描述的工作是否仍可继续、已经完成
或被阻塞；它是里程碑陈述，不是宿主的活动目标或执行状态。没有 next action 只表示 Handoff 没有提出后续行动，
disposition 用于区分工作已经完成、被阻塞或只是没有提出后续行动。

## 交接给另一个会话

Prepared Handoff 可以直接交给另一个会话：

```text
Prepared Handoff -> explicit transfer -> Continue
```

它没有持久化 Artifact identity，也不进入里程碑历史。传输方式不改变 Handoff 语义。

显式 transfer 必须在接收方开始规划前交付同一份定稿内容。复制或恢复会话历史、继承 Memory 都不能替代这一步，
因为这些机制不保证保留 Handoff 内容。

接收方必须能够访问 Handoff 所属的 scope 及其 evidence。来自其他 scope 的 Handoff 可以作为数据检查，但不会自动
成为当前 scope 的 Handoff。Continue 要求使用 Handoff 的来源 scope；cross-scope import 是本 RFC 范围之外的
独立操作。

## 提交里程碑

调用方可以把 Prepared Handoff 提交为持久化里程碑：

```text
Prepared Handoff -> Commit -> Handoff Revision
```

commit 保留用户检查过的内容，并向当前 scope 的 Handoff 历史追加一个不可变 Revision。第一版中，每个 scope
只有一条线性 Handoff 历史。

commit 必须显式发生。生成过程可以在调用方请求后帮助准备 Handoff，但不能自行决定它是否属于里程碑。

如果内容与当前里程碑相同，commit 不创建空 Revision。如果另一个写入方已经推进历史，commit 报告冲突，不会覆盖
较新的状态。commit 还会在发布里程碑前检查其中引用的 evidence 是否仍然可读。

## 继续工作

Continue 可以从以下输入开始：

- 显式的 Prepared Handoff；
- 显式选择的精确 Revision；
- 当前 scope 最新的已提交 Revision。

PowerContext 解析 Handoff，检查 evidence 是否仍然可用，并在 Agent 开始规划前呈现用户可以检查的同一份内容。
Agent 随后把其中的陈述与当前请求、仓库、workspace 和工具结果比较。

Continue 使用以下规则：

| Condition | Behavior |
| --- | --- |
| 没有已提交 Handoff | 明确说明没有保存的状态 |
| Handoff 属于其他 scope | 不把它视为当前状态，要求使用来源 scope |
| Handoff 没有 next action | 只呈现 state，不推测新任务 |
| 当前目标与 Handoff objective 不同 | 报告差异，不把历史 objective 设为当前目标 |
| 所需 evidence 不可用 | 不依赖缺少依据的陈述；只有通过当前事实重新确认后才能行动 |
| 实时事实与 next action 冲突 | 报告冲突，不执行受影响的 action |
| evidence 与实时事实一致 | 在当前请求和权限范围内继续 |

Continue 只交付工作上下文。Handoff objective 是准备时的历史快照；当前目标及执行授权仍由调用方或宿主确定。

选择旧 Revision 不会让它自动成为当前状态。只有经过实时验证，其中的 next action 才可能再次适用。

## 查看里程碑历史

每个已提交 Revision 都是一份完整 Handoff。读取最新里程碑时，不需要回放之前的 Revision。旧 Revision 仍然可以
通过精确引用读取。

历史视图可以比较两个 Revision，但比较结果只用于展示。它不会替代原始 Handoff，也不会成为另一份事实来源。

## 端到端流程

以下示例只演示产品契约，不规定 API 或传输方式。

### 临时会话交接

1. 会话 A 为“完成 parser error handling”这个 objective 准备 Handoff。
2. state 说明 error mapping 已经修改，并引用相关 Source；next action 是运行 regression test，并引用发生变更的
   Artifact；最近一次测试结果缺失，记录为 omission。
3. 用户修正草稿并定稿，得到 Prepared Handoff。
4. 会话 A 把这个值交给会话 B，不创建 Artifact Revision。
5. 会话 B 解析引用，把陈述与实时 workspace 对照，只在 next action 仍然适用时运行测试。

### 持久化里程碑与后续恢复

1. 测试通过后，用户准备另一份完整 Handoff，并选择 commit。
2. commit 检查 evidence，向当前 scope 追加一个不可变 Handoff Revision。
3. 后续会话选择该 scope，在没有显式传入 Handoff 的情况下请求 Continue。
4. PowerContext 解析最新的已提交 Revision。Agent 根据当前环境验证内容，再决定是否执行其中的 next action。

### 并发提交里程碑

1. 会话 A 和 B 从同一个已提交 head 准备不同 Handoff。
2. 会话 A 先 commit，推进当前 scope 的历史。
3. 会话 B 观察到的 head 已经过期，因此 commit 冲突。
4. 会话 B 读取新里程碑并准备一份完整替代内容，不能从旧状态直接覆盖或盲目追加。

# Reference-level explanation

## 产品模型

Prepared Handoff 和已提交 Handoff 共享内容，但生命周期保证不同：

| 契约 | Prepared Handoff | 已提交 Handoff |
| --- | --- | --- |
| 主要用途 | 直接会话交接 | 里程碑跟踪与恢复 |
| 定位方式 | 显式值 | 精确 Revision 或当前 scope |
| 保留保证 | 无 | 持久化历史 |
| 历史 | 无 | 每个 scope 一条不可变 Revision 序列 |
| 更新方式 | 用另一份 Prepared Handoff 替换 | commit 新 Revision |

Prepared Handoff 已经可以传递，但它不是 Artifact。已提交 Handoff 是 Artifact Revision。commit 增加持久化
identity，不改写 Handoff 内容。

第一版为每个 scope 绑定一个 Handoff Artifact identity。该 scope 的全部已提交 Revision 描述同一个当前
workstream。后续 Handoff 可以重述由调用方控制的 objective，但这不会创建独立 work identity 或并行历史。
integration 不得把无关 workstream 提交到同一个 Handoff scope。无法确认当前 scope 对应目标 workstream 时，
Continue 必须要求显式选择精确 Revision，不能把 latest 当作当前工作。

临时 envelope 把 Prepared Handoff 与来源 scope 以及 prepare 时观察到的 committed head 关联起来。这些路由和
并发信息不属于两种形式共享的 Handoff content。已提交 Handoff 通过 Artifact identity 和 Revision 获得等价关联。

Prepared Handoff 与 RFC 0028 中的 Prepared Context 不是同一概念。Prepared Handoff 向下一个参与者描述工作，
Prepared Context 则为一次 Agent turn 提供有界、临时且不可信的材料。

## 内容契约

objective、state、disposition、next action、evidence 和 omissions 组成一个带版本的内容契约。每条 state
statement 和 next action 都带有一个或多个 citation，objective 不要求 citation。

state 至少包含一个当前事实。disposition 必须由 state 支持；工作被阻塞时，state 需要说明 blocker。state 和
next action 必须引用可读取的 evidence。next action 是可选的单值，避免让 Handoff 变成 action queue。

omissions 记录已知相关但无法读取、被排除或未经验证的材料。存在原始引用时，omission 应保留该引用。

evidence 可以引用 Source、精确 Artifact Revision 或精确 Memory entry version。Memory entry citation 是对精确
Memory Revision 的细化，不能在已提交 Handoff 的 Artifact lineage 中取代该 Revision。

Continue 按 statement 检查 evidence。某项 evidence 不可用时，只影响依赖它的 statement 和 action，不使其他已经
验证的内容失效。

每份 Handoff 都是自包含的，不依赖之前的 Handoff 才能理解。

## 生命周期

```text
Draft -> Prepared Handoff -> Transfer
                          -> Commit -> Handoff Revision
```

prepare 和定稿临时 Handoff 不会改变持久化历史。只有 commit 会创建 Revision。

后续 Prepared Handoff 可以把之前的 Prepared Handoff 作为上下文，但仍然需要包含完整的当前状态，而不是增量补丁。

第一版要求由调用方发起 prepare、Continue 和显式 commit。复制、恢复或继承会话历史本身不会启动 Continue。
后续 Trigger 可以请求相同的 prepare 或 Continue 链路，但 automatic preparation 和 automatic commit 都需要独立
policy；Trigger 不能自行授权 commit 或执行 next action。

## 职责

| 参与者 | 职责 |
| --- | --- |
| 调用方或宿主 | 提供 objective，选择临时交接或 commit，并授权当前工作 |
| PowerContext | 准备内容、验证引用、保持内容一致，并管理持久化历史 |
| 用户或 Agent | 根据当前事实检查自然语言陈述，并决定是否执行 |

生成过程可以提出文本和 evidence selection，但不能确立事实、授权 action 或 commit milestone。

## 一致性

一个 scope 只有一条已提交 Handoff 序列。Prepared Handoff 与 prepare 时观察到的 committed head 关联；首次
commit 时则与“尚无 head”这一状态关联。只有该观察仍然有效时，commit 才能成功。

成功的 commit 会一次性发布新 Revision 并推进历史。重试同一次 commit 不会产生重复 Revision。内容与当前 Revision
相同时，commit 不产生变化。

临时交接不改变持久化历史。因此，丢失 Prepared Handoff 不会影响已经保存的里程碑。

## Continue 与信任边界

引用验证与事实验证是两个不同步骤：

- PowerContext 检查 evidence 是否指向 Handoff scope 中可读取的材料；
- 用户或 Agent 判断相关陈述在当前环境中是否仍然正确。

evidence 可读只能证明某条陈述以哪份材料为依据，不能证明陈述现在仍然为真。

Handoff 内容是 untrusted history。它不能覆盖当前 system 或 developer instruction、当前用户请求、仓库规则、授权
或实时工具结果。PowerContext 负责准备 Continue 所需的输入，不自行授权工具使用，也不直接执行 next action。

## 历史与兼容性

已提交 Revision 保持不可变，并且可以通过精确 reference 读取。最新版本查询只返回已提交 Handoff。

Handoff 内容及其临时 envelope 都带有版本。consumer 不理解某个版本时，必须在 Continue 或 commit 前拒绝处理。
新版本不会原地改写旧 Revision。

## 范围

本 RFC 包括：

- 共享的 Handoff 内容契约；
- 通过 Prepared Handoff 完成临时交接；
- 通过已提交 Revision 保存持久化里程碑历史；
- 有界 prepare、evidence validation 和 omissions；
- 精确版本和最新版本的 Continue 行为；
- 并发、信任和兼容性保证。

以下内容不属于本 RFC：

- task 或 work identity；
- 并行 Handoff 历史和 merge；
- automatic milestone commit；
- cross-scope import 或 evidence copying；
- 参与者约定及其生命周期；Handoff 后续可以消费其独立表示，但本 RFC 不定义该结构；
- retention policy 和 authenticated actor identity；
- transport 和 provider interface schema。

## 验收

| Scenario | Pass condition |
| --- | --- |
| Temporary handoff | 两个会话交换一份 Prepared Handoff，不创建 Revision |
| Inspection | 接收方在规划前读取发送方检查过的同一份定稿内容 |
| Milestone | 显式 commit 添加一个不可变 Revision，不改变其中的 Handoff 内容 |
| Concurrency | stale commit 不能替换较新的 milestone，重试不会创建重复 Revision |
| Continue | action 开始前逐条检查 evidence；不继承历史目标，缺少依据的陈述必须重新确认 |
| Workstream selection | 无法确认 scope 对应当前 workstream 时，不使用 latest 隐式恢复 |
| History | 当前 scope 可以解析最新 milestone，精确的旧 Revision 仍然可读 |

# Drawbacks

- 用户和实现者需要理解两种 Handoff 形式的生命周期区别；
- Prepared Handoff 的载体丢失后无法恢复；
- 一条线性里程碑历史无法表达同一 scope 内的并行工作；
- 有界 prepare 可能遗漏相关材料；
- Continue 前需要额外检查 evidence。

# Rationale and alternatives

## 持久化每份 Handoff

这个方案会简化最新版本查询，但普通会话交接会填满里程碑历史，而且每次交接都需要持久化写入。因此，临时交接
保留为独立形式。

## 只使用临时 Handoff

这个方案可以直接交接，但没有持久化里程碑和最新版本查询，也无法在临时值丢失后恢复。

## 保存增量 Handoff

这个方案可以减少重复内容，但接收方必须读取之前的序列才能理解当前状态。自包含 Handoff 让 Continue 不依赖
历史回放。

## 立即加入 work identity

work identity 需要创建、选择、并行历史和 merge 规则。第一版在每个 scope 中只支持一个当前 workstream。

# Prior art

RFC 0002 把 Handoff 定义为 Artifact Family，规定 objective ownership，并定义 optimistic Artifact update。
RFC 0014 定义精确 Memory citation 和 lineage。RFC 0019 定义 scoped Runtime。RFC 0028 定义 bounded、untrusted
context preparation。

# Unresolved questions

第一版有意不定义 automatic milestone policy。selection limit、storage layout、transport schema 和 automatic
preparation policy 属于实现或后续 interface RFC。

# Future possibilities

后续 RFC 可以增加 automatic milestone policy、actor attribution、label、retention 和 export、cross-scope import、
derived scope、parallel workstream 以及临时查询。
