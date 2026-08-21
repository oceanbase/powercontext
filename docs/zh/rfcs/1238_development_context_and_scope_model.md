- 提案名称：`development_context_and_scope_model`
- 起始日期：2026-08-12
- 状态：草稿（Draft）
- 跟踪 Issue：[oceanbase/powercontext#1219](https://github.com/oceanbase/powercontext/issues/1219)
- 相关 RFC：[RFC 0002](0002_core_sdk_product_model.md)、[RFC 0019](0019_local_source_memory_runtime.md)、
  [RFC 0028](0028_context_pack.md)、[RFC 0048](0048_handoff_artifact.md)、
  [RFC 0072](0072_scoped_statistics_and_usage.md)、[RFC 0082](0082_handoff_report.md)

# 摘要（Summary）

PowerContext 从项目派生出一个 `scope_id`，用它在多个 Session 之间共享持久化上下文。此后 Handoff 又复用同一个 scope
作为「一条线性 Workstream」的身份。于是 `scope_id` 同时承载了三种被混为一谈的语义：**项目**（Codex 集成按 Git remote 或
路径派生它）、**记忆隔离单元**（Runtime 以它为键选择一条 Source journal、一个 Memory head、一组 Trigger cursor）、以及
**Workstream**（Handoff 为每个 scope 维护一条线性历史）。这种混淆无法表达同一项目内的并行工作，也无法表达横跨多个项目的
开发。

权衡过三个方案：**A** —— 引入新 `workstream_id` 的三层模型（最干净，但推翻 RFC 0082 的两条决策、且迫使全量回填迁移）；
**B** —— 保持 `scope_id` 为 Workstream 身份，另加一个可选启用的 Project 层来承载*共享*语义；**C** —— 仅文档层澄清（不加
能力，并未真正解决问题）。**本 RFC 采用 B。** 三者的逐项对照见*理由与备选方案*一节；本文档其余部分即是对 B 的规约。

具体而言，本 RFC 固定 **Project**、**Workstream**、**Session**、**scope** 之间的身份边界。它决定：

- **`scope_id` 仍是 Workstream 身份与 Runtime 分区键** —— 本版不引入 `workstream_id`。
- **Project** 成为显式的应用层分组，承载*共享的*项目上下文，而每个 Workstream 保持*隔离的*历史。
- **Session** 是短暂的参与者边界，永不作为身份。
- **`project_id`** 仍是独立的、服务端所有的身份，仅存在于 Builtin Runtime 应用层，永不进入 Core Protocol。
- 既有以 `scope_id` 为键的 API、CLI、集成与存量数据契约不变；新增的共享是加法式、可选启用。

本 RFC 只决定模型与不变量，不固化详细的用户流程编排、迁移工具或具体的 API/CLI/Dashboard 形态；这些在接受后作为后续工作
创建。

# 动机（Motivation）

当前模型对单条线性项目可行，但在两种常见场景下失效：

- **同一项目内的并行工作。** 两个 feature，或一次长期重构与并行进行的缺陷修复，拥有不同的目标与下一步动作，且由不同
  Agent 各自继续。今天要隔离它们的历史，唯一办法是派生出不同的 `scope_id`——但这同时也把它们本应共享的项目上下文切碎了。
- **横跨多个项目的开发。** 一次跨仓库的改动没有一等的关系表达，唯一可用的关联是弱引用 `external_refs`。

两者同出一因：`scope_id` 被三种语义重载，而*共享*语义希望 scope **相同**、*隔离*语义希望 scope **不同**——一个键无法同时
承担两者。

| 语义 | 谁依赖它 | 它作为什么的键 | 希望 scope 是… |
| --- | --- | --- | --- |
| **项目**（共享单元） | Codex 集成、README | 由仓库 Git remote（或路径）派生的项目级上下文 | **相同**（以共享） |
| **记忆隔离单元**（Runtime 分区键） | Core Runtime | 一条 Source journal + 一个 Memory head + 一组 Trigger cursor | ——（机械性） |
| **Workstream**（隔离单元） | Handoff、RFC 0048/0082 | 一条线性 Handoff 历史 | **不同**（以隔离） |

本 RFC 通过把第一行的*共享*语义上移到 Project 层、把*隔离*语义留在 `scope_id` 上来解决一、三行的冲突。（系统今天其实已把
二者当作两层未打通地运行——Core/Runtime/Memory 里是扁平的 `scope_id`，Handoff Report 里是 `Project → Workstream(≡scope_id)`
目录；见「现有工作」。）跟踪 Issue 的承重不变量——保留跨 Session 的共享上下文、保持 Workstream 历史隔离、绝不把 Session 等同于
Workstream、不假定当前 `project_id` 是最终形态、把迁移视为后续——在下文逐条保留。

# 指南级说明（Guide-level explanation）

四个概念；其绑定定义即参考级说明中的不变量 I1–I8，这里只给心智模型。

- **scope** —— 不透明、集成方所有的分区字符串（≤256 字符），与 RFC 0019 一致。一个 scope 作为键选一条 Source journal +
  一个 Memory head + 一组 Trigger cursor。
- **Workstream** —— 一条可继续的工作线（单一目标、单条线性 Handoff 历史）。**它的身份*就是*其 `scope_id`**；无独立
  `workstream_id`。当且仅当两段工作可各自独立继续时，它们才是不同的 Workstream、使用不同的 scope（RFC 0082 的判据）；分支
  切换/改名/rebase 复用同一个 scope。
- **Project** —— 对 Workstream 的显式、应用层分组（一个仓库、服务或长期项目），带不可变的 `project_id`。它是*聚合*（RFC
  0082，已有）与*共享上下文*（新增）的边界，且**永不是** Handoff scope。
- **Session** —— 在一个工作区间内读取/准备/接收上下文的短暂参与者边界。永不是实体、身份或 Workstream；仅以可选的
  `session_id` 归属出现。

默认行为是**共享、不隔离**：`derive_scope_id` 只依据 Git remote/路径、不读取分支，因此换分支解析到*同一个* scope。若要把某段
工作作为独立 Workstream 推进，就**显式声明**一个新 scope（例如用 git worktree 配置专属的 `POWERCONTEXT_CODEX_SCOPE_ID`）；分支
永不作为分区键。声明入口留待后续设计。

**什么*不会*开启新 Workstream。** 另开一个 CLI、终端或 Agent 进程,是一个新 *Session*,而非新 Workstream（I6）。指向*同一个*
scope 的两个 CLI,是同一条 Workstream 上的两个 Session——它们共享那唯一的 Memory head 与唯一的线性 Handoff 历史,并发提交由
RFC 0048 的 CAS 冲突兜底,而不是分叉出第二条历史。新 Workstream 只在「*声明了不同的 scope*」时出现;判据永远是「能否各自独立
继续」（不同目标、不同线性历史）,而不是「是不是换了个进程、分支或 CLI」。所以:同一个声明 scope 上的两个 CLI = 一条
Workstream;两个不同声明 scope 上的两个 CLI = 两条 Workstream——CLI 从来不是决定因素。

```
Project  (project_id —— 服务端所有的分组；共享上下文 + 聚合；永不是 Handoff scope)
  ├── Workstream A  (scope_id = git:host/repo#featureX)  ── 隔离的 Sources / Memory / Handoff / Stats
  ├── Workstream B  (scope_id = git:host/repo#refactorY) ── 隔离 ...
  └── Workstream C  (scope_id = git:host/repo)           ── 隔离 ...
        ▲
        └─ 共享的 Project 上下文可从任意 Workstream 的 Session 读取，
           但每个 Workstream 已提交的 Handoff 历史保持私有。

Session  (短暂；把活动归属到某个 Workstream；永不作为身份)
```

`#featureX` / `#refactorY` 仅示意*显式声明*的 scope，并非由分支派生——默认 `derive_scope_id` 只会得到 `git:host/repo`（即
Workstream C）。隔离位于 scope 层（不变），共享位于 Project 层（新增、加法式、可选启用）。现有单 Workstream 用户不受影响：一个
未分组的 scope 行为与今天完全一致。

# 参考级说明（Reference-level explanation）

自此处起直到「缺点」一节，规约的都是**备选 B**——即被采用的模型。这并非中立综述：下文的不变量、数据模型与解析都属于 B。
凡**备选 A** 会走上实质不同路径之处，都以*换作 A：……*就地标出；A 与 B 的完整对照见「理由与备选方案」。

## 模型与不变量

以下八条不变量（I1–I8）是本 RFC 的有约束力决策；文档其余内容皆由它们推导而来。

- **I1 —— scope 不透明且 Runtime 所有。** 与 RFC 0019 一致：非空字符串 ≤256 字符；Runtime 不从中解析结构。（不改动
  `validate_scope_id`、`MAX_SCOPE_ID_LENGTH` 或 OpenAPI 的 pattern。）
- **I2 —— Workstream 身份即 `scope_id`。** 本版不引入 `workstream_id`。Workstream 的 Sources、Memory、Handoff、
  Statistics 按其 scope 分区并与其他 Workstream 隔离。RFC 0048/0082 的「每 scope 一条线性历史」与 CAS 冲突保证原样保留。
  *换作 A：* 唯一会翻转的正是这条——`scope_id` 变为路由键，改由新增的 `workstream_id` 承载身份；I2 之后的每条不变量都属于
  B，因为 B 把身份留在 scope 上。
- **I3 —— Project 是应用层分组，而非 Core 概念。** `project_id` 服务端所有且不可变；仅存在于 Builtin Runtime 应用层。
  Core Protocol（RFC 0002；`core-protocol.md`）仍完全不知道 scope、Project、Workstream、Session。Project 成员关系**不建立
  到 Core 表（Sources、Artifacts、Memory、Handoff）的外键**，与既有 `catalog_store` 的决策一致。
- **I4 —— 一个 scope 至多属于一个 Project。** 把一个 scope 注册到第二个 Project 是冲突（`scope_already_grouped`），与
  RFC 0082 一致。一个 scope 也可以不属于任何 Project（未分组），此时行为与今天完全一致。
- **I5 —— Project 不是 Handoff scope。** Project 永不拥有 Handoff 历史、永不接收已提交的 Handoff、永不写回某个
  Workstream。Handoff 与 Continue 仍是 scope 绑定的（RFC 0048）。
- **I6 —— Session 短暂且非身份。** Session 永不作为域身份持久化，永不决定某个 Workstream。它既不等于 Workstream，也不等于
  scope。
- **I7 —— Branch 不是身份轴。** 与 RFC 0082 一致：分支元数据是弱信号与不可信的活动归属，永不作为 Handoff 边界。
- **I8 —— Workstream 边界是被声明的、语义的，而非从分支或任务派生。** 决定因素是「能否独立继续」（I2 / RFC 0082），
  不是 git 分支或任务数量。默认是每个仓库（其 Git remote）一个 scope；显式声明开启一条独立的 Workstream。分支与 git
  worktree 都不改变派生出的 scope——只有显式的 `POWERCONTEXT_CODEX_SCOPE_ID`（或不同的 remote）才会。

## 共享 Project 上下文（新增能力）

今天 RFC 0019 通过 `MemoryBindingStore` 持久化一个严格的 `scope_id → Memory Artifact` 一对一绑定，而 RFC 0019 本身已点明
扩展路径：「支持多个实例需要对 application mapping 做显式扩展」。本 RFC 正是使用这一接缝。下面的机制固定扩展的*形状*；排序/
合并策略与写入表层仍为待定（见下）。

### 数据模型

- **per-Workstream 绑定（不变）。** `MemoryBindingStore` 保持其 `scope_id → Memory Artifact` 一对一映射。每个 Workstream
  仍恰好拥有一个 Memory head，与其他任何 scope 隔离。
- **Project 上下文绑定（新增）。** 一个 Project **可以**额外拥有一个 Project 级绑定 `project_id → Memory Artifact`，解析到
  一个独立于任何 Workstream 的 Memory Artifact。这就是 **Project 上下文**。它是*应用层映射里的第二行，而非某个 scope 上的
  第二个 head*：没有任何 `scope_id` 获得第二个绑定，per-scope 的一对一不变量不受触动。
- 两个绑定都只存在于 Builtin Runtime 应用层。两者都不向 Core 表引入外键（I3）；Core 仍只从一个不透明键解析 Memory
  Artifact，完全不知道 Project。

完整模型是三条应用层目录记录 + 那条不变的 per-scope head——只有最后一行是新增的：

| 记录 | 形状 | 基数 | 来源 |
| --- | --- | --- | --- |
| per-scope Memory head | `scope_id → Memory Artifact` | 每个 scope 一条 | RFC 0019 `MemoryBindingStore`（不变） |
| Project | `{ project_id, project_key, title }` | 每个 Project 一条 | RFC 0082 目录 |
| 成员关系 | `scope_id → project_id` | 每个 scope ≤ 1（I4） | RFC 0082 Workstream 目录（`WorkstreamDescriptor`） |
| Project 上下文 | `project_id → Memory Artifact` | 每个 Project ≤ 1 | **新增（本 RFC）** |

scope 自身的 head 直接解析；它的 Project 上下文间接解析——先沿成员关系找到 `project_id`，再解析 Project 上下文绑定。两个
Memory Artifact 始终是不同的句柄。

### 存储接缝

Project 上下文绑定与既有的 Project 目录（RFC 0082 的 `catalog_store`）一起持久化，以 `project_id` 为键。解析它复用
per-scope head 已在使用的同一套 Memory Artifact 机制——唯一新增的是*哪个键选中哪个 Artifact*，而非 Memory Artifact 如何存储、
修订或检索。不改动任何 Core Memory 表、修订格式或检索路径。

### 解析（Resolution）

每一次读与写都从请求的 `scope_id` 确定性地解析出来，调用表层不新增任何必填参数：

1. **定位 scope。** 应用层在目录里查 `scope_id`。
2. **未分组 → 一个绑定。** 若该 scope 不属于任何 Project，解析止于其自身的 `scope_id → Memory Artifact` head——逐字节即
   今天的路径。不查询、也不创建任何 Project 上下文。
3. **已分组 → 两个绑定。** 若该 scope 带有指向某 `project_id` 的成员关系，则额外解析该 Project 的上下文绑定。若该 Project
   尚无上下文绑定，则退化为未分组情形（只有自身 head）。
4. **读与写。** *读*把两个解析出的 head 交给下面的组合步骤；*写*只落到该 scope 自身的 head（I2、I5）——本版中 Project
   上下文永不出现在写路径上。

「哪个键选中哪个 Artifact、按什么顺序解析」在此完全已定。只有「两个已解析的读 head 如何排序与合并」留待后续——即下一子节。

### 读取组合（形状已定，策略留待定）

当某 Session 为一个「其 scope 已归组到某 Project」的 Workstream 准备上下文时，应用层解析**两个** Memory head——该 Workstream
自身 scope 级的 head 与 Project 上下文——并把它们组合进准备好的上下文。这里有两点是**已决**的：

- **两者皆可读。** Workstream 自身的 head 总会被读取；Project 上下文*可从*任一成员 Workstream 的 Session *读取*。
- **组合是只读叠加。** 组合永不写入任一绑定，永不把一个 Workstream 的条目复制到另一个，也永不改动 Project 上下文。隔离在
  *结构上*被保留——两个 head 仍是两个独立的 Artifact，因此任何 Workstream 的私有历史都不可能落入另一个的存储。

仍为**待定**（推迟到 Memory/Context 后续工作）：两个 head 之间的优先级、当两者都浮现某条目时的合并/去重/排序规则、以及读取
时如何界定跨 Workstream 泄漏。本 RFC 固定「组合发生在两个隔离的 Artifact 之上」；它不固定它们*如何*排序。

### 写入隔离

Workstream 已提交的 Handoff 历史永不写入 Project，Project 也永不写回 Workstream（I5）。Session 是否能*写入* Project 上下文、
通过哪个操作、带何种信任标记，推迟到后续；本版默认 **Project 上下文只做读共享**。

### Context Pack（RFC 0028）

Context Pack 保持其「一次请求一个 scope、不混合 scope」的契约不变。若要通过 `prepare_context` 暴露 Project 级读取，需要一个
新的契约版本或一个显式的 Project 参数；本 RFC 不修改既有单 scope 契约，把该表层选择留给后续。

## Project 实体与注册

Project 是 Builtin Runtime 应用层里的一条目录记录（RFC 0082 的 `ProjectDescriptor`，位于 `catalog_store`），而非 Core
实体（I3）。它承载三个字段：

- `project_id` —— 服务端生成、不可变（`prj_<uuid>`），持久身份。永不由客户端提供，退休后永不复用。
- `project_key` —— 目录内唯一的人类可读键（如 `acme/api`），用于在不知道 `project_id` 时查找 Project。
- `title` —— 可变的展示标签；改动它永不改变身份。

**注册。** 一个 scope 通过在 Workstream 目录里把其 `scope_id` 绑定到某个 `project_id` 而成为 Project 成员（既有的
`WorkstreamDescriptor` / `create_workstream` 接缝，以 `scope_id` 为键）。把一个已归组到*不同* Project 的 scope 再次绑定，
就是既有的 `scope_already_grouped` 冲突（I4）——一个 scope 至多一个 Project。**未分组**的 scope 是默认且无需注册：它照旧完全按今天的方式拥有自己的 Sources/Memory/Handoff/Statistics，且不读取任何
Project 上下文。

**归组状态。** 一个 scope 恰处于两态之一，本版只定义两态之间的一条转移：

- **未分组** —— 无成员关系记录；行为与今天完全一致。
- **归组于 P** —— 成员关系 `scope_id → P`；额外读取 P 的 Project 上下文。

`未分组 → 归组(P)` 即上述注册。改挂（`归组(P) → 归组(Q)`）与解组（`归组 → 未分组`）在此**均不定义**——对已归组的 scope 再向
不同 Project 注册会被 `scope_already_grouped` *拒绝*，绝不静默改挂（见待定：在 Project 间移动 scope）。归组一个 scope 永不重写、
移动或合并它既有的 per-scope Memory 或 Handoff 历史；它只是新增「可读取 P 的 Project 上下文」这一能力。

**生命周期。** `title` 可变；`project_id` 在 Project 的整个生命周期内不可变。一旦至少有一个 scope 绑定到它（或后续若新增显式
创建入口，一旦被显式创建），Project 即存在。在 Project 间移动 scope、以及合并或拆分 Project，均**不在本 RFC 范围内**（列入待定 /
后续工作）——本版只固定「一个 scope 至多属于一个 Project」以及「成员关系是一条应用层绑定」。

## 端到端示例：同一 Project 内的两条并行 Workstream

某团队开发 `acme/api` 服务。他们注册了一个 Project：

- `project_id = prj_ac…`、`project_key = acme/api`、`title = "ACME API"`。

两条工作线并行推进，且不得共享同一条 Handoff 历史：

- **W-main** —— 在默认 checkout 上进行的持续缺陷修复。`derive_scope_id` 只读取 Git remote，因此其 scope 是
  `git:github.com/acme/api`。不编码任何分支。
- **W-refactor** —— 一次必须可独立继续的长期存储重写。同一仓库的 worktree 共享*同一个* Git remote，否则会派生出与
  W-main *相同*的 scope；因此团队通过 `POWERCONTEXT_CODEX_SCOPE_ID = git:github.com/acme/api#storage-rewrite` **显式声明**
  一个独立 scope，`derive_scope_id` 会逐字采用。worktree 只是让两个 checkout 在磁盘上共存——*仅凭那个被声明的 scope* 才
  产生隔离，而 `#storage-rewrite` 后缀是*声明*出来的，并非从分支派生（I8）。

两个 scope 都绑定到 `prj_ac…`。于是：

- **读取。** W-refactor 上的一个 Session 准备上下文，解析**两个** head——W-refactor 自身 scope 级的 Memory head ⊕ 绑定到
  `prj_ac…` 的 Project 上下文——以只读叠加组合。它因此能看到项目级的共享知识（构建怪癖、服务约定），却**看不到** W-main
  在途的缺陷修复历史。W-main 上的 Session 对称地看到自身 head ⊕ 同一个 Project 上下文。
- **写入。** 每个 Session 已提交的 Handoff 只追加到自身 scope 的那条线性历史：W-refactor 的 Handoff 永不进入
  `git:github.com/acme/api`，两个 Workstream 也都不写回 Project（I5）。本版中 Project 上下文只做读共享。

两个不变量**同时**成立：共享（两个 Session 读取同一个 Project 上下文）与隔离（两条独立的线性 Handoff 历史、两个独立的 Memory
head）——这正是单个被重载的 `scope_id` 无法表达的。一个未分组的 `acme/api` checkout（未绑定 Project）则只读取自身 head、不读取
任何 Project 上下文，行为与今天完全一致。

## 验收场景（Acceptance scenarios）

把模型钉到可观察行为上。每条只断言*结构*,不断言留待定的读取组合策略。

| # | Given | When | Then | 不变量 |
| --- | --- | --- | --- | --- |
| 1 | scope 未绑定任何 Project | 某 Session 准备上下文 | 只解析出自身 `scope_id → Artifact` head;不读取也不创建 Project 上下文——逐字节即今天 | I4、解析② |
| 2 | scope 归组于 `P`,且 `P` 有上下文绑定 | 某 Session 准备上下文 | 解析出两个**不同**句柄(自身 head ⊕ `P` 的上下文),以只读叠加组合 | 解析③ |
| 3 | scope 归组于 `P`,但 `P` 尚无上下文绑定 | 某 Session 准备上下文 | 退化为未分组情形(只有自身 head) | 解析③ |
| 4 | 同一 `P` 下的两条 Workstream | 其一提交 Handoff | 只追加到自身 scope 的线性历史;兄弟的历史与 `P` 的上下文永不被写入 | I2、I5 |
| 5 | 同一 `P` 下的两条 Workstream | 其一的 Session 读取 | 能看到 `P` 的共享上下文,但**看不到**兄弟的 in-flight head(两个 Artifact 始终独立) | 读取组合(结构) |
| 6 | scope 已归组于 `P` | 再注册到 `Q` | `scope_already_grouped` 冲突;绝不静默改挂 | I4 |
| 7 | 一个仓库 checkout | 切换 git 分支 | 解析到**同一个** scope;不产生新 Workstream | I7、I8 |
| 8 | 任意 Project 成员关系 | 检查 Core 表 | 没有指向 `project_id` 的 Core 外键;Core 仍只从不透明键解析 Artifact | I3 |

场景 2 与 5 刻意只断言「解析出两个隔离的 Artifact」——两者之间的优先级/合并顺序为待定(见下),现在不得由测试钉死。

## 与既有子系统的交互

- **Memory（RFC 0019）。** per-scope 绑定不变。通过文档化的 application-mapping 扩展新增可选的 Project 级绑定。不改动
  Core Memory、Revisions 或检索。
- **Handoff（RFC 0048）。** 完全不变。每 scope 一条线性历史；CAS 冲突；Prepared 与 committed；evidence/Continue 的
  `untrusted_history` 语义全部保留。Workstream 仍 ≡ scope。
- **Handoff Report（RFC 0082）。** 予以协调，且大体重申。Project → Workstream 目录、`WorkstreamDescriptor`、
  `WorkspaceBinding`、活动存储与 `handoff-reports` API 保持现有形态。唯一的澄清是：Project 现在除了*聚合*之外，还承载
  *共享上下文*；RFC 0082 的「只聚合、绝不写回」规则得以保留（Project 上下文是独立的读绑定，而非 Handoff 的写回）。
- **Statistics（RFC 0072）。** per-scope 统计不变。可选的 Project/Workstream 汇总**推迟**到后续（本 RFC 现在不要求跨
  scope 聚合，但记录它是聚合的自然落点）。
- **Core Protocol（RFC 0002）。** 不变，且对 Project/Workstream 明确越界（I3）。

## 兼容性

- **存量数据。** 无需重写。现有 scope 的 Sources、Artifacts、Memory、Handoff、Statistics 保持不变。归组与 Project 上下文
  都是加法式；未分组的 scope 不读取任何 Project 上下文，行为与今天完全一致。
- **API。** 每个既有的以 `scope_id` 为键的接口（Memory、Handoff、stats、sources、context）契约不变。新的 Project 上下文
  读取是加法式、可选的。没有任何既有请求形态改变含义。
- **CLI。** 既有的 `--scope-id` 命令不变。
- **Codex 集成。** `derive_scope_id`（Git remote → 路径回退）不变。`POWERCONTEXT_CODEX_SCOPE_ID` 保持其含义。把派生出的
  scope 注册到某个 Project 之下是一个额外的、可选的步骤。
- **弃用。** 本 RFC 无。

## 身份编码（不变的表层，供参考）

- `scope_id`：不透明字符串 ≤256，客户端提供；由 Git remote 派生，否则回退为 `local:` 路径哈希；
  `POWERCONTEXT_CODEX_SCOPE_ID` 逐字覆盖。
- `project_id`：服务端生成且不可变（`prj_<uuid>`），目录所有。
- `project_key`：目录内唯一的人类可读键；`title`：可变展示名。
- 本版无 `workstream_id`。

# 缺点（Drawbacks）

- **并行工作仍需派生多个 scope。** 由于 Workstream 仍 ≡ scope，表达并行工作是一种命名/派生纪律（同一 Project 下的不同
  scope），而不是一等的「一个 Workstream、多条分支」身份。想让单个 Workstream 横跨多分支或多仓库的团队，本版不予支持。
- **跨 Project 工作仍建模薄弱。** 横跨多个 Project 的开发仍依赖 `external_refs`，而非一等的跨 Project 关系。本 RFC 不弥合
  这一缺口。
- **两个 Memory 绑定引入组合问题。** 新增 Project 上下文意味着一个 Session 可能读取两个 Memory 来源；其优先级/合并策略
  是推迟到后续的实际设计工作，若边界画错，可能通过共享层把一个 Workstream 的私有上下文泄漏到另一个。
- **将来可能仍想要第二身份。** 通过刻意不引入 `workstream_id`，本 RFC 赌这一延后的扩展是可接受的；若并行分支身份成为硬
  需求，后续 RFC 必须重开 RFC 0082 的中心身份决策。

# 理由与备选方案（Rationale and alternatives）

考虑了三个方向。**A 与 B 才是两个真正的候选**（C 是不增能力的基线）。两者都从同一个问题出发——`scope_id` 背负三种语义
——但拆分方式不同。自上而下读这张表：**A 把每种语义各自搬到独立的键上**（三个键），而 **B 把隔离留在 `scope_id` 上，只把
*共享*语义抬到一个新的、加法式的 Project 层**（一个键 + 一个可选启用的层）。

| 设计维度 | 今天（`scope_id`） | **备选 A** —— 三层（延后） | **备选 B** —— Project 层（本 RFC） |
| --- | --- | --- | --- |
| 共享（项目上下文） | `scope_id` | `project_id`（一等） | 新增 **Project 层**（加法式） |
| Runtime 隔离 / 分区键 | `scope_id` | `scope_id`（仅路由） | `scope_id`（不变） |
| Workstream 身份 | `scope_id` | 新增 `workstream_id` | `scope_id`（不变） |
| `project_id` 角色 | 薄，应用层 | 一等身份 | 应用层分组（不变） |
| 一个 Workstream 跨分支/仓库 | 否 | **是**（一等） | 否（派生不同 scope） |
| 存量 scope 迁移 | —— | **需要**（回填每个 scope） | **无** |
| RFC 0082 身份决策 | —— | **推翻**（两条） | 保留 |
| Core Protocol 改动 | —— | 很可能 | 无 |

### 备选 A —— 引入独立 `workstream_id` 的三层模型（延后）
**做法。** 让每种被混淆的语义各占一个键。`project_id` 成为一等身份（承载共享与聚合）；`scope_id` 降级为纯 Runtime 分区/
路由键，不再*表示*「Workstream」——一个 `project_id` 可映射到多个 scope，scope 只是一个存储分区；新增 `workstream_id` 作为
Handoff/历史身份，于是单个 Workstream 可横跨多个 scope/分支/仓库，因为它的身份不再骑在分区键上。

**换来什么。** 并行与跨仓库工作成为一等：一个 Workstream 可在分支或仓库间移动而不切碎其历史；Branch 与 Session 获得自然的
非身份位置；共享与隔离因落在不同键上而在构造上永不相争。这是对三种语义最干净的表达。

**代价。** 它反转了 RFC 0082 的两处中心决策（「`scope_id` 是唯一的 Workstream 身份」；「不存在独立的 `workstream_id`」）；
在 Handoff、Handoff Report、Runtime 全链路加一层每次操作都要穿过的解析/间接（`workstream_id` → scope(s) → Artifacts）；
迫使**每个存量 scope 回填迁移**进新的三键模型；并很可能触及 Core Protocol 边界。对第一版而言影响面过大。

**结论 —— 延后，而非弃用。** 作为 Future possibility 保留，且可*在 B 之上*触达而不违背 B 的不变量——B 是同一条路上更小的
第一步。

### 备选 B —— 保持 `scope_id` 为 Workstream 身份，新增 Project 共享上下文层（本 RFC，采用）
**做法。** 让隔离原地不动——scope ≡ Workstream，`scope_id` 作为身份与分区键均不变——并把*共享*作为一个新的、加法式、可选
启用的 Project 上下文绑定（`project_id` → Memory Artifact）加上去。不降级、不重新指派任何键；不引入 `workstream_id`。

**换来什么。** 它以**零迁移、无 Core 改动**同时满足 Issue 的两条硬约束（共享上下文*与*隔离历史）；它落在既有 RFC 已预留的
接缝里（RFC 0048 的「parallel workstreams, derived scopes」；RFC 0019 的「通过显式 application-mapping 扩展支持多实例」；
RFC 0082 延后的跨 Project 报表），而非推翻 RFC 0048 的线性历史模型；并让备选 A 作为日后扩展仍可触达。

**代价（已接受）。** 并行工作仍是一种 scope 派生纪律，而非一等的「一个 Workstream、多条分支」身份；跨 Project 工作仍建模
薄弱；两绑定的读取组合策略是推迟到后续的实际工作。这些即上文的「缺点」，作为「小而可回退的第一步」的代价被接受。

**结论 —— 采用。**

### 备选 C —— 仅澄清语义，不新增分组能力
**做法。** 只在文档层澄清 `scope_id` 的三种混淆含义，规范派生与并行工作的命名约定，并可选地给 Statistics 加 Project 汇总。
不新增任何被建模的能力。

**结论 —— 不采用。** 它并未真正解决 Issue 的核心诉求：并行工作与「共享但隔离」的上下文仍停留在约定，而非被建模保证。

**不做的影响。** `scope_id` 继续背负三种含义；团队要么为了隔离而切碎共享上下文，要么为了共享而污染某个 Workstream 的
历史。RFC 0019/0048/0082 继续编码一个未言明的模型，而每个新特性都必须各自重新发现它。

# 现有工作（Prior art）

- **RFC 0048** 首次断言「每 scope 一个当前 workstream」，并把*并行 workstream*与*派生 scope*明确放进 Future
  possibilities —— 本 RFC 所依托的接缝。
- **RFC 0082** 规定了当前的 `Project → Workstream(≡scope_id)` 目录、「Project 只聚合、绝不写回」规则、以及「branch 非
  身份」规则；本 RFC 重申这些并新增共享上下文。
- **RFC 0019** 记录了 `MemoryBindingStore` 的一对一映射，并点明了这里所用的多实例扩展路径。
- RFC 流程本身（`docs/en/rfcs/README.md`）：先与 maintainer 验证问题，把初始范围收窄到可评审、可实现。

# 待定问题（Unresolved questions）

合并前需解决：

- **上下文组合与优先级。** 当某 Workstream 的 scope 归组到某 Project 时，读取时 Workstream 的 Memory head 与 Project
  上下文究竟如何组合？优先级如何？如何防止跨 Workstream 泄漏？（阻塞 Memory/Context 后续工作。）
- **写入 Project 上下文。** Session 能否写入 Project 上下文、通过哪个操作、带何种信任标记？本版默认只读共享。
- **Context Pack 表层。** Project 上下文应通过 `prepare_context` 暴露（新契约版本）还是走独立的读路径？（RFC 0028 目前
  仍保持单 scope。）
- **Workstream 边界的声明（I8）。** 通过什么入口、在何时显式覆盖「每仓库一个」的默认？这需与 RFC 0082 的「并行分支使用不同
  scope」规则、以及当前只按仓库取键的 `derive_scope_id` 对齐。

有意排除在外（后续决策，可能需要各自的 RFC）：

- 引入独立的 `workstream_id`（备选 A）。
- 超越 `external_refs` 的一等跨 Project 关系；Project 之上的 Portfolio/Program 实体。
- 跨 Project 或 Workstream 组的 Statistics 汇总。
- 在 Project 间移动、合并、拆分 Workstream。
- 迁移工具与详细的用户流程编排。

# 未来可能性（Future possibilities）

- **备选 A 作为扩展。** 若并行分支或跨仓库的 Workstream 身份成为硬需求，后续 RFC 可在本模型之上引入 `workstream_id`，把
  `scope_id` 降级为路由键——在不违背此处所设不变量的前提下即可触达。
- **跨 Project 聚合。** Portfolio/Program 级读取与跨 Project 的 Handoff Report（RFC 0082 已延后）。
- **Project 级 Statistics 汇总**（RFC 0072 的自然扩展）。
- **更丰富的 Session 语义**（并发信号、实时状态提示），只要 Session 仍保持非身份即可——包括*同一 scope 上的并发 Session*
  如何在 RFC 0048 的 CAS 冲突之外协调（即「换个 CLI 是否开启新 Workstream」的开放残留:答案是否,但同一 scope 上并发的多个
  CLI 仍可能想要实时状态信号）。
