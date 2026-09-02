- Proposal Name: `scope_organization_and_agent_integration`
- Start Date: 2026-08-21
- Related Discussion: [oceanbase/powercontext#1238](https://github.com/oceanbase/powercontext/pull/1238)、
  [oceanbase/powercontext#1219](https://github.com/oceanbase/powercontext/issues/1219)
- Related RFCs: [RFC 0019](0019_local_source_memory_runtime.md)、[RFC 0028](0028_context_pack.md)、
  [RFC 0048](0048_handoff_artifact.md)、[RFC 0082](0082_handoff_report.md)

# Summary

本文定义 PowerContext 如何表征持久状态的分层、隔离和共享。

Scope 是唯一的持久归属边界。Source、Artifact revisions 和统计记录都属于产生它们的 Scope。Memory、Handoff、Experience 和
Skill 是当前定义的 Artifact families。一次请求绑定一个 current Scope，写入只进入该 Scope。

Scope 之间使用两种独立关系：Organization Parent 组织可独立继续和交接的结果，Context Reference 扩展 Prepare Context 的
读取范围。Parent 不授予读取能力，Context Reference 不改变归属，也不传递。跨 Scope 交付使用 exact Artifact publication。

Dashboard、Statistics 和 Handoff Report 不再依赖额外的 Project identity。它们对调用方选择的 Scope 集合进行汇总。`All`
表示全部可观察 Scope，Report Root 表示一个顶层 Organization Scope 及其 descendants；二者都是查询选择，不是持久对象。

Scope application layer 生成不透明的 `scope_id`。Agent integration 按显式 binding、持久外部 binding 和配置的
`default_scope_id` 解析 current Scope，并在每次请求前固定。`default_scope_id` 指向普通 Scope，只是 host 的
binding fallback，不定义 Scope 类型、层级、共享或观察范围。repo、branch、目录、session 和 Agent identity 可以辅助查找
binding，但不用于派生 `scope_id` 或建立层级。

本文适用于单用户或一个既有授权域。多租户身份和跨租户共享不在范围内。

# Motivation

现有实现用 `scope_id` 隔离 Runtime 状态，但其外围形成了三套不同表达：Agent 插件根据 Git remote 或目录派生 Scope，Dashboard
从静态配置列出 Scope，Handoff Report 通过独立的 `project_id` 和 Workstream Catalog 组织报告。这些做法不能稳定回答：

1. 当前请求的状态属于哪里；
2. 当前请求可以读取哪些共享材料；
3. 哪些结果具有组织上的包含关系；
4. 哪些材料应留在原处，哪些材料可以交付；
5. Dashboard 和报告应按什么范围汇总。

一个 ID 或一种父子关系不能同时承担这些职责。session 可以依次处理多个工作；多个 Agent 可以共享一个结果，也可以隔离推进；
repo、branch、目录只表示外部资源位置；报告中的包含关系也不应改变执行时的读取范围。

本文保留一种持久边界，并分别定义组织、读取、交付和观察行为。

# Guide-level explanation

## 领域模型

理解本模型时，先确定状态归属，再确定读取、组织、交付和观察范围。后四种行为都以 Scope 为输入，不产生新的归属边界。

Scope 对应 memory 和 storage 系统中的 namespace 或 partition。Scope 内的对象承担不同职责：

| 概念 | PowerContext 表达 | 职责 |
| --- | --- | --- |
| Source | Source | 保存原始输入及其 provenance |
| Artifact | Artifact revision | 保存可引用、审核和交付的版本化状态 |
| Artifact family | Memory、Handoff、Experience、Skill | 区分 Artifact 的内容、生成方式和生命周期 |
| Statistics | Scope-local records | 保存可按 Scope 回溯的运行统计 |
| Index | Runtime retrieval projection | 加速检索，可从 Scope 内数据重建 |

Memory family 保存可召回状态；Handoff family 保存可继续工作的确定快照；Experience 和 Skill families 保存经过生成和审核的沉淀
结果。它们共享 Artifact identity、revision 和 provenance 基础语义，各自定义生成和状态转换流程。

Source、所有 families 的 Artifact revisions 和统计记录都属于一个 Scope。Storage 的物理布局和 Index 的实现可以变化，不改变
逻辑归属。

```text
Scope
|
+-- Sources
+-- Artifacts
|   +-- memory
|   +-- handoff
|   +-- experience
|   `-- skill
+-- Statistics
`-- rebuildable Index
```

Scope 之外有五种行为：

| 行为 | 表达 | 回答的问题 |
| --- | --- | --- |
| Write binding | current Scope | 本次请求写到哪里 |
| Shared read | current Scope + Context References | Prepare Context 可以读取哪里 |
| Organization | Parent + descendants | 哪些 Scope 构成一个结果及其子结果 |
| Delivery | exact publication | 哪些 revision 进入另一个 Scope |
| Observation | `all`、`exact` 或 `subtree` selection | Dashboard 和报告汇总哪些 Scope |

```text
Agent request
     |
     | bind
     v
current Scope -------- Context References
     |                         |
     | write                   | read
     v                         v
Sources / evidence -> Artifact revisions
                      [memory | handoff | experience | skill]
                       |
                       | exact publication
                       v
                 another Scope

Scope selection -> aggregate -> Dashboard / Statistics / Handoff Report
```

这五种行为可以作用于相同的 Scope，但不能互相推导。Agent、session、workspace、repo 和 branch 是外部身份、来源或 binding
信号，不是 Scope，也不改变上述规则。

## Binding 和 Scope 创建

一次 Agent 请求必须先绑定 current Scope。integration 可以暴露两种操作面，它们不是持久对象：

| 操作面 | Scope 选择 | 行为 |
| --- | --- | --- |
| scope-bound | binding 固定 current Scope | Prepare Context、Capture 和各 Artifact family 的操作 |
| multi-scope | 显式处理已授权 Scope | discover、create、Parent、Context References、binding、publication、report |

```text
resolve current Scope
          |
          +-- explicit binding
          +-- durable external binding
          `-- configured default_scope_id
                         |
                         v
                scope-bound requests

independent boundary -> create ordinary Scope -> persist binding
```

每个部署提供一个普通 Scope 作为默认 binding target。系统只在请求没有显式 binding 或持久外部 binding 时
使用它。defaultness 属于 host 配置，不写入 Scope metadata，也不赋予额外的读取、写入、publication 或观察能力。
修改 `default_scope_id` 只影响后续无 binding 的请求，不迁移数据，不改变 Parent，也不覆盖已保存的 binding。

新 `scope_id` 由 Scope application layer 分配。integration 创建 Scope 时提交 title、summary、可选 Parent、Context References、
external references 和稳定 idempotency key。integration 不得根据 repo remote、路径、branch、Agent 或 session 拼接或哈希生成
`scope_id`。

只有至少满足一个条件时才创建 Scope：

- 状态需要与当前工作隔离；
- 工作需要独立继续或授权；
- 结果需要独立交接、交付或观察。

否则复用已解析的 Scope，不为每个 turn、Agent、session、目录或生命周期阶段创建一层。workspace、repo 和
orchestrator lifecycle 可以用于查找既有 binding，或作为 external references 和 idempotency 输入。binding 必须在
Prepare Context 前确定，并在一次请求内保持不变。scope-bound 操作不接受覆盖 binding 的任意 `scope_id`。

Scope 与 Agent、sub-agent、session 是多对多关系。新 session 不自动创建 Scope；resume 复用已保存的 binding，模型或顺序
切换 Agent 也不改变 current Scope。同一 session 可以在请求边界切换 binding。并行 Agent 只有在中间状态或结果需要
独立时才创建 Scope。缺少稳定外部身份的 integration 必须允许 host 显式选择 Scope 并保存 binding。

## Organization 和 Context sharing

每个 Scope 最多有一个 Organization Parent。设置 Parent 表示 child 是 parent 的独立子结果，并参与 parent subtree 的导航和汇总。
Parent 必须无环；共享 repo、session、Agent 或 Context 不构成 Parent。

一个 Scope 可以通过 Context References 读取多个 Scope，多个 Scope 也可以引用同一个 Scope：

```text
personal conventions ----+
team knowledge ----------+----> current Scope
repository notes --------+
```

Context Reference 只扩展后续 Prepare Context 的读取范围。它不开放被引用 Scope 的 Handoff history 或全部局部状态，不产生写入、
反向读取或传递读取。一次性输入和结果交换使用 exact publication，不为此增加长期引用。

## 材料归属和交付

每个持久 Artifact revision 始终属于产生它的 Scope。publication 指定 source Scope、exact revision 和 target Scope，在 target
形成保留来源记录的新 revision，不复制 source Scope 的其他状态。

| Material | 默认处理 |
| --- | --- |
| Source 和未选择的 Artifact revision | 留在产生它的 Scope |
| 功能制品和验证结果 | 选择 exact revision 后发布 |
| 可复用知识 | 经检查后发布到可共享的 Scope |
| 个人信息、调试片段和未采用结果 | 不选择、不发布 |

publication 需要 source read 和 target write 授权。Parent、Context Reference 或知道 `scope_id` 都不能替代授权。target revision
必须保留 source Scope、exact source ref、content digest 和可解析 provenance。

每个 Scope 保留独立 Handoff。subtree report 保留层级和各 Scope 的 exact Handoff，不合并 Handoff history；exact report 可以
只聚焦一个 child。

## Dashboard 和报告

Dashboard、Statistics 和 Handoff Report 是同一种 observation selection 上的不同投影：

| Selection | 含义 |
| --- | --- |
| `all` | 调用方有权观察的全部 Scope |
| `subtree(root_scope_id)` | 一个顶层 Organization Scope 及其 descendants |
| `exact(scope_ids)` | 临时聚焦或生成确定报告的 Scope 集合 |

`All` 是默认观察选择。配置的默认 Scope 只是写入 binding fallback，不是 `All`，也不自动成为 Report Root。
Report Root 是顶层 Scope 的选择，不是 Project、Scope 类型或持久 View。sub-scope 可以被搜索、聚焦和生成 exact report，
但不自动成为一级 View。

```text
Scope selection
     |
     +-- Overview: aggregate totals
     +-- Organization: preserve Parent structure
     `-- Handoff: exact Handoff or no_handoff per Scope
```

Context References 作为 Context inputs 单独展示，不进入 Organization、统计范围或 Handoff selection。没有有效的已保存 root 时
回退到 `all`，不得根据 workspace、repo、branch、session 或 Agent 自动猜测新的 root。

## 场景表征

以下场景组合前文已经定义的 binding、Organization、Context Reference、publication 和 observation selection，不定义 Feature、
Bug、Project、Agent 或 Session 类型的 Scope。单目录和多目录与场景正交：一个 Scope 可以处理多个目录，多个 Scope 也可以使用
同一目录。

| 场景 | current Scope binding | 是否创建 Scope | 共享与交付 | 展示 |
| --- | --- | --- | --- | --- |
| Session 和工作一对一 | session 绑定工作 Scope；无 binding 时使用默认 Scope | 只在工作需要独立边界时创建 | Context References 读取长期材料 | exact Scope 或所在 subtree |
| 一个 Session 切换多个工作 | 请求边界切换到对应 Scope | 每个独立工作使用不同 Scope | 工作间不自动共享 | 分别聚焦，或由共同 root 汇总 |
| 多个平级 Agent 协同 | 每个请求绑定约定的 Scope | 共享全部状态则复用；需要隔离则创建 child | 引用共享材料，选定结果发布回接收 Scope | root subtree 保留各自 Handoff |
| 主 Agent 驱动 Sub-agent | sub-agent 绑定调用方 Scope 或显式 child | 只有独立推进时创建 child | exact input 进入 child，exact result 返回 parent | child 可随 parent 展示，也可 exact 聚焦 |
| Agent 之间交接工作 | 接收方绑定原 Scope；边界变化时绑定 target Scope | 仅因 Agent 变化不创建 | 同 Scope 读取已提交 Handoff；跨 Scope 发布 exact Handoff 和依赖 Artifact revisions | 原 Scope 连续展示，或分别展示 source 和 target |

### Session 和工作一对一

```text
session -> feature Scope
```

session 不构成额外 Scope。所有写入属于 feature Scope；repo 知识和个人约定通过 Context References 读取。工作结束后提交该 Scope
的 Handoff。

### 一个 Session 顺序切换多个工作

```text
request 1: session -> bug-fix Scope
request 2: session -> feature Scope
request 3: session -> bug-fix Scope
```

integration 在 Prepare Context 前显式切换 binding，并在返回旧工作时复用既有 Scope。普通自然语言不能在请求中途静默改变
binding。两个 Scope 是否具有共同 Parent，只取决于它们是否属于同一结果组织。

### 多个平级 Agent 协同

当前 Runtime 中，同一 Scope 只有一条 Source processing、Memory head 和 Handoff lifecycle。多个 Agent 可以共享这些状态时，
直接绑定同一 Scope。需要隔离中间状态或独立继续时，为独立结果创建 child：

```text
feature Scope
|-- agent-a result
`-- agent-b result
```

child 不自动读取 parent 或 sibling。持续共享使用 Context References；验收后的结果通过 exact publication 进入 feature Scope。

### 主 Agent 驱动 Sub-agent

sub-agent 共享调用方全部状态时复用 current Scope。需要独立推进、交接或授权时绑定显式 child：

```text
main Scope
|-- research result
`-- validation result

input:  selected Artifact -> child
result: child Artifact    -> main Scope
```

Parent 只表示结果分解。host 向 child 交付选定输入，并在完成后选择可交付结果；未选择的调试片段、个人信息和中间材料留在 child。

### Agent 之间交接工作

Agent identity 变化不改变工作归属。接收 Agent 继续同一工作时，交付方在 current Scope 提交 Handoff Artifact，接收方随后绑定同一
Scope，并从该 Handoff 和 Scope 内 Memory 恢复工作：

```text
agent-a -> commit Handoff -> work Scope <- bind <- agent-b
```

这个流程只切换 binding，不创建 Scope、不设置 Parent，也不执行 publication。Handoff revision、Memory family 中的状态和后续
Artifact revisions 仍属于同一 Scope，因此 Dashboard 和报告显示一条连续工作历史。

只有归属、隔离、授权或独立报告边界发生变化时，交接才跨 Scope。host 从 source Scope 选择 exact Handoff revision，以及接收方
继续工作所需的 Artifact revisions，一起发布到 target Scope：

```text
agent-a -> source Scope
              |
              | exact Handoff + selected Artifact revisions
              v
         target Scope <- agent-b
```

接收 Agent 绑定 target Scope，把发布的材料作为初始继续输入；此后各 family 产生的 Artifact revisions 都属于 target Scope。跨
Scope 交接必须指定 exact Handoff revision，不允许在 source Scope 解析 `latest`。如果
接收方还需要持续读取 source Scope，并且获得相应授权，可以另行添加 Context Reference。Context Reference 不替代 Handoff
publication，也不会带入未选择的个人信息和中间材料。

这些规则也适用于非编码场景。例如，一个长期客户事项可以作为 root，独立调研和审批作为 children，团队知识通过 Context
References 读取，最终结论通过 publication 进入客户事项。业务名称不同，边界判断不变。

# Reference-level explanation

## Scope identity and metadata contract

新 Scope 使用以下创建语义：

```text
create_scope(
    title,
    summary,
    parent_scope_id?,
    context_refs[],
    external_refs[],
    idempotency_key,
)
```

Scope application layer 使用密码学安全随机数生成 128-bit payload，并编码为 `scp_` 加 26 个小写 Crockford Base32 字符。
`scope_id` 全局唯一、不透明且不可变。既有格式继续可读；该格式只约束新生成的 ID。

title 和 summary 对新 Scope 必填。metadata 使用 version 条件更新，`scope_id` 不随 metadata、Parent 或 binding 变化。相同调用方以
相同 idempotency key 重试同一创建请求时返回同一 Scope；参数不一致时返回冲突。

## Relation and data-flow contract

1. 写入只进入 current Scope；
2. Context 只来自 current Scope 和显式 Context References；
3. Parent 不授予读取、写入或 publication 权限；
4. Context Reference 非传递，不进入报告层级；
5. Parent 无环，每个 child 都表示可独立继续、交接或观察的子结果；
6. reparent 不改变 Context References、binding 或任何 Artifact 归属，包括 Handoff history；
7. publication 只交付选定的 exact revisions，并保留来源；
8. `scope_id` 不是凭证，所有跨 Scope 行为仍需授权。

## Integration contract

一次 scope-bound 请求按显式 binding、持久外部 binding、`default_scope_id` 的顺序解析一个 current Scope，并在请求结束前
保持不变。session 可以在请求边界切换 binding。multi-scope 操作必须使用 exact Scope IDs，并把 Parent、reference
source、publication source 和 target 限制在 integration 获得授权的范围。

当前 Runtime 串行化同一 Scope 的修改，除非 operation 明确定义幂等或冲突安全的并发语义。Handoff 使用线性 head 和版本检查；
具有独立 objective、state 或 next action 的 Agent 不应并发写入同一 Scope。

Agent 之间交接同一工作时，integration 先提交 source Scope 的 Handoff，再把接收 Agent 绑定到该 Scope。跨 Scope 交接必须发布
exact Handoff revision 和继续工作所需的 exact Artifact revisions，不能使用 source Scope 的 `latest`。切换 Agent identity、session 或
workspace 不能隐式改变 Artifact 归属。

## Observation contract

observation selection 支持 `all`、`exact` 和 `subtree`。报告生成时冻结最终 Scope 集合和每个 Scope 的 exact Handoff。Dashboard
只能把 `all` 或顶层 Organization Scope 保存为一级选择；`exact` 用于临时聚焦，不创建持久 View。

聚合结果必须保留 selection 和 Scope 维度，使总量可以回溯到各 Scope。Statistics、Dashboard 和 Handoff Report 必须使用相同的
selection 解析规则，不能分别维护 Project membership、静态 Scope 列表或隐式 workspace 推断。

## 实施方案

Scope-local Runtime 继续以 `scope_id` 分区 Source、各个 family 的 Artifacts 和 Statistics。在此基础上直接完成以下修改：

1. Scope application layer 提供 Scope 创建、metadata、Parent、Context References 和 observation selection resolution。所有新
   `scope_id` 由该层生成。
2. Codex 和 Claude Code 插件删除基于 Git remote 和目录的 ID 派生。插件先解析显式或持久 binding，否则使用
   `default_scope_id`。只有 host 确认需要独立边界时，插件才调用 `create_scope`，提交 title、summary、external references 和
   idempotency key，再保存返回的 `scope_id`。
3. Dashboard 和 Statistics 共用 observation selection resolver。Dashboard 默认提交 `all`；选择顶层 Scope 时提交 `subtree`；
   临时聚焦提交 `exact`。统计服务展开 selection 后聚合，同时保留每个 Scope 的明细。
4. Handoff Report 接收相同的 observation selection，解析 Scope 集合并冻结每个 Scope 的 exact Handoff。报告页和 Dashboard
   使用同一个 selection picker 和 root 列表。
5. 删除 Project Catalog、Workstream Catalog 及其 Project membership。从 Handoff Report 请求、事件、领域模型、workspace
   binding 和存储中删除 `project_id`。Workstream 的 title、summary、external references 和结果层级分别由 Scope metadata 和
   Parent 提供。

现有 Project 数据不保留为另一套运行模型。仅作为保存范围使用的 Project 重写为 observation selection preference；确实表示独立
结果的 Project 重写为 Scope，并在确认结果包含关系后设置 Parent。无法确定语义的记录不自动转换。完成数据重写后删除
Project 和 Workstream catalog tables。

# Drawbacks

- Parent、Context Reference、publication 和 observation selection 分开后，integration 需要明确维护各自行为。
- 显式 publication 增加一次交付操作，但避免中间材料和个人信息自动扩散。
- Scope 不携带业务类型，UI 需要依靠 title、summary、Organization 和 external references 帮助用户理解。
- 移除 Project Catalog 需要同步修改 OpenAPI、领域模型、存储、Dashboard 和 Agent integrations。

# Rationale and alternatives

| Alternative | 结论 |
| --- | --- |
| Parent 同时表达组织和共享 | 不采用；报告重组会改变读取范围 |
| 固定的 Scope 用途或生命周期类型 | 不采用；业务阶段不是稳定的状态边界 |
| 在 `scope_id` 中编码 repo、Agent 或层级 | 不采用；外部信号变化会使 identity 失效 |
| 完全平面的 Scope 和请求级 filters | 不采用；缺少稳定的结果组织和最小共享边界 |
| 自动提升 child 的全部材料 | 不采用；无法区分交付物、中间材料和个人信息 |
| 保留独立 Project identity 作为报告维度 | 不采用；它与 Scope organization 和 observation selection 重复 |

# Prior art

- RFC 0019 定义 integration-owned 的不透明业务分区，RFC 0048 和 RFC 0082 定义 exact Handoff 和稳定报告输入。
- NowledgeMem 将 Space、Agent Identity 和 Session 分开，并通过显式 linked Spaces 扩展读取范围。
- CocoIndex 将 Component Path、共享 Context 和统计分组分开，说明归属、依赖和观察不应由一种层级承担。
- EverOS 将 app/project partition、owner、session 和 memory lineage 分开，共享材料通过公共 Knowledge 或显式复制流转。
- Mem0 和 Graphiti 使用调用方过滤维度完成平面寻址，但不提供本文所需的结果组织和精确交付语义。

# Future work

## 一个 Scope 内的多个 Memory Artifacts

本文维持 RFC 0019 的 Runtime mapping：一个 Scope 只有一条 active Memory 推进线，`scope_id` 同时选择 Source journal、trigger
cursors、active Memory Artifact identity 和 Handoff Artifact lifecycle。一个 Scope 可以拥有多个 families 的 Artifacts 和多个
revisions，但这不表示它可以独立推进多个 Memory heads。

后续 RFC 如果支持一个 Scope 内多条 Memory 推进线，必须定义稳定的 Memory binding、Source 分配、per-Memory cursor、Prepare
Context 选择和并发冲突。这个扩展不改变 Scope 的授权、归属、Handoff 和 observation 边界。

## Multi-tenancy

本文假定调用方已经处于一个授权域。tenant identity、tenant-local Scope namespace 和跨 tenant publication 需要单独定义，不能
从 Parent、Context Reference 或 `scope_id` 格式推导。
