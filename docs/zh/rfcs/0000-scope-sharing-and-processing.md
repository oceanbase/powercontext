---
title: "RFC 0000：Scope 间共享、跨 Scope 查询与本层加工"
description: "从团队／项目记忆与 RAG 需求出发，定义业务级联关系的 Scope 表达，以及共享、查询、加工和生命周期能力。"
---

- Proposal Name: `scope_sharing_and_processing`
- Start Date: 2026-11-09
- RFC PR: 待创建

# Summary

本提案扩展 PowerContext 面向 Agent 上下文的数据基础能力，用 Scope 结构表达需要级联管理的业务概念。共享先约定持续有效的授权范围，再自动发现其中符合条件的材料，并在每次运行固定实际输入。第三方 Scope 的 Dream 或后加工负责协调这些输入、解决冲突并形成自己的产物，读取和继续加工以目标 Artifact 为入口。Scope 定义数据归属和访问范围，Artifact 保存版本化内容，输入通过精确出处追溯。

团队／项目记忆与 RAG 是这套能力的用例。接入应用负责业务映射、共享规则和操作触发，PowerContext 执行范围解析、授权、加工及生命周期控制。归档停止指定 Scope 的普通写入和加工，已有获准使用方仍可在自己的 Scope 使用其材料。删除须显式发起，并分别约定停止访问、资源清理和物理擦除的完成条件。

```text
Standing authorization + sharing rule -> automatic discovery -> pinned inputs
    +-- query -> results retain exact origins
    `-- Dream / processing -> reviewed target Artifact
Explicit Scope lifecycle operation -> bounded resource handling
```

# Motivation

个人会在多个工作 Scope 中积累制品，团队和项目需要在事前约定的边界内自动获得可用材料，协调不同人的结论，并形成自己的 Profile、Skill 或其他产物。用户应能设置覆盖未来内容的规则，系统据此选材和发起加工。知识库 RFC 2570 提出文档接入、切块、检索与删除，其中以 `knowledge-base` Artifact 组织文档是一个过渡性方案。 [知识库 PR #2570](https://github.com/oceanbase/powercontext/pull/2570)

团队记忆中的来源读取、加工用途和结果披露都需要授权，成员关系不能直接决定这些权限。RAG 的过渡方案则暴露出另一个问题：原有 Artifact 之间没有实质性的父子级联关系，引入知识库 Artifact 后，制品层还需处理级联生命周期和加工，包括父对象失效后的子文档可见性、未完成任务和资源清理。本提案扩展普通 Scope，使业务中具有级联关系的概念可以通过 Scope 结构表达，并在 Scope 层管理相应范围内的数据和处理。文档仍保存为版本化 Artifact。 [RFC 2570 的对象与删除设计](https://github.com/oceanbase/powercontext/blob/71e842cd2a34a7943ab0adecb3542771e3da387e/docs/zh/rfcs/2570-knowledge-base.md)

```text
Team/project memory                   Transitional RAG model
Authorized work Artifacts             knowledge-base Artifact -> documents
         |                                      |
         v                                      v
Target Profile or Skill               Artifact lifecycle + processing cascades
         +----------------------+---------------+
                                v
              Scope structure, sharing, processing, lifecycle
```

| 场景要求 | 需要的公共能力 | 场景特有逻辑 |
| --- | --- | --- |
| 多人制品形成项目或团队记忆 | 持续共享边界、自动选材、逐来源授权、单目标加工及审核。 | 材料选择、接收和审核流程、生成策略。 |
| 知识库组织文档并跨库检索 | Scope 归属、结构与共享入口、有界跨 Scope 查询。 | 文档解析、chunk、检索投影和领域配置。 |
| 集合关闭、材料撤回或显式删除 | 写入控制、读取可见性、在途任务约束和可恢复清理。 | 业务触发条件、保留规则及操作展示。 |

# Guide-level explanation

## 共同的数据与操作模型

Scope 标识一组数据的归属。Family 表示制品种类，如 Experience、Profile、Skill，以及待接入的 document。图中使用显示名，`Retry trace@3` 表示《重试记录》第 3 版。完整地址包含 Scope ID、Family、制品 ID 和版本。Dream 根据证据生成修订，Candidate 是待审核的修订。

```text
Scope                   Family       Artifact title   Revision
Alice's investigations  Experience   Retry trace      3
```

接入应用配置持续共享边界和选材规则，系统为每次操作解析 Scope 范围及精确输入，并在实际读取、加工和提交时检查权限。手工选材可用于例外和纠正。底层请求仍固定具体地址以便复现，这些地址可以由规则自动生成。一次操作可读取多个来源，保存加工结果时指定一个目标 Scope。

```text
Selected source Scopes and exact Artifacts
                    |
             check each operation
                /           \
       query and read       process with current target
              |                       |
      qualified results       Candidate -> review -> target revision
```

## 团队、项目与个人记忆

以订单排障为例，Alice 和 Bob 分别在自己的工作 Scope 保存 Experience。订单项目指定一个 Scope 保存 Profile，稳定性团队另选 Scope 保存值班 Skill。这些 Scope 可以位于不同结构子树，使用材料前须取得相应操作的授权。

```text
Alice's investigations / Retry trace@3 --------+
Bob's adapter reviews / Duplicate callback@1 --+-> Orders project Profile@4
                                                  -> Dream -> review -> @5

Authorized shift-review Experiences -> Reliability team Scope / on-call Skill
```

项目 Profile 写着“回调超时可重试”。Alice 记录了携带幂等键的新版通道重试成功，Bob 则记录了旧通道重复处理。证据足以解释差异时，Dream 提出带适用条件和出处的修订。证据不足时保留现行结论。管理员手工更新目标后，旧候选须按新基线重新评估。个人 Profile 也可作为同一条路径的目标，仍遵守来源授权、Family 准入和审核规则。

共享边界可覆盖指定工作 Scope 中满足条件的现有及未来材料。例如，约定将 Alice 排障和 Bob 适配评审中的可用 Experience 用于订单项目后，新增制品和版本会自动成为输入候选，个人私密 Scope 保持边界之外。规则明确用途、处理身份和产物受众，运行时重验权限。成员访问先复用现有 Scope 授权，外部目录按需接入，应用指定各业务目标 Scope。 [Scope 与访问控制](../docs/workflows/scopes-and-access.md)

## 知识库、知识空间与共享材料

知识空间和知识库是业务中具有级联关系的概念，下面用它们演示如何映射到 Scope 结构。Scope 管理结构范围内的生命周期和处理控制，document Artifact 保存文档版本，文档服务维护 chunk 和索引。RFC 2570 中的解析、持久任务和版本化片段设计可继续适配这一结构。 [RAG 文档处理提案](https://github.com/oceanbase/powercontext/blob/71e842cd2a34a7943ab0adecb3542771e3da387e/docs/zh/rfcs/2570-knowledge-base.md)

```text
Transitional RAG model
Workspace Scope -> knowledge-base Artifact -> document Artifacts
                   Artifact lifecycle and processing cascades

Business concepts with cascade relationships -> mapped Scope hierarchy
Workspace Scope
  +-- Interface Scope -> document revisions and processing resources
  +-- Help Scope      -> document revisions and processing resources
```

知识空间可以对应一个上层 Scope，也可以只由应用维护映射。下图中，订单空间拥有接口和帮助两个真实子 Scope，并获准使用运维空间中的手册 Scope。产品可以将这些关系显示为拥有和共享，服务按具体的结构、选材和授权执行操作。

```text
Orders space Scope                   Operations space Scope
  +-- owns Interface Scope             +-- owns Runbook Scope
  +-- owns Help Scope                           ^
  +-- selects shared Runbook Scope -------------+  authorized use

Application-only Orders entry -> [Interface, Help, Runbook] Scope IDs
```

查询订单空间时，入口解析出获准知识库 Scope，逐个调用文档检索并保留 Scope、文档版本和 chunk 标识。只分享《回调排障手册》第 3 版时，需要对应的单件和版本授权，不能把它扩成整个手册 Scope 的搜索。结果加工为 Topic Memory 还需选定可写目标与受支持的输入路径。

归档接口 Scope 会停止其普通内容写入，其他获准空间仍可使用其中材料。显式删除订单空间 Scope 及其真实后代时，接口和帮助 Scope 会被处理，外部手册 Scope 仍按自己的生命周期管理。移除共享入口只影响该入口及相关授权的使用。

# Reference-level explanation

## 支持状态与实施边界

实现基线为 [master fd9f1682b](https://github.com/oceanbase/powercontext/tree/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a)。RFC 2570 是设计输入，document、chunk 和知识库任务仍需文档服务接入。下表区分已实现的行为、接入应用可组合的用法，以及本提案提出的扩展。

**现状图（master）**

Orders 下已经可以组织 Interface 和 Help，但搜索仍按单个 Scope 调用，Dream 的输入和目标也都在运行 Scope 内。已有结构关系本身不会把子级材料纳入这两种操作，Scope 归档和删除尚无公共入口。

```text
Orders Scope
+-- Profile: Service behavior@4
+-- Interface Scope
|   `-- Experience: Retry trace@3
`-- Help Scope
    `-- Experience: FAQ correction@1

Search(Interface, Experience) -> Interface hits only
Dream(Orders) -> evidence and target inside Orders only
No public Scope archive or delete operation
```

**预期图（提议）**

提案将选定来源纳入统一的授权、查询和加工控制，使 Orders 能用多个 Scope 的材料维护自己的 Profile。查询可先由应用组合现有调用，跨 Scope Dream 和 Scope 生命周期仍需实现，来源归属保持不变。

```text
Orders Scope [target]
+-- Profile: Service behavior@4 -> review -> @5
+-- Interface Scope
|   `-- Experience: Retry trace@3
`-- Help Scope
    `-- Experience: FAQ correction@1

Runbook Scope [outside Orders]
`-- Experience: Timeout notes@2

Authorized inputs: Interface + Help + Runbook
  +-- query -> results keep each origin
  `-- Dream -> one Candidate in Orders
Explicit Orders subtree delete: Orders + Interface + Help only
```

| 能力 | 已有或可组合 | 本提案扩展 |
| --- | --- | --- |
| 组织与选材 | Scope 身份、无环单父树、直接 Context Reference、观察范围解析。 | 持续规则下自动解析可用输入，有界展开并固定每次运行的精确清单。 |
| 授权与共享 | Scope 及受支持 Family 的逻辑制品授权、撤销和到期，部分 Family 的独立发布。 | 确定跨来源加工和结果披露的授权方式，以及入口／版本限制的执行方式。 |
| 查询与读取 | 单 Scope、单 Family 搜索。Prepare 已有受限跨直接 Scope 召回。接入应用可聚合获准单库调用。 | 明确结果身份、预算、失败和单件候选过滤。RAG 按 Scope 接入 chunk 检索。 |
| 目标内加工 | 本地 Dream、证据清单、审核、Family 条件写入。 | 来源 Scope 和输入视图贯穿证据及提交，以加工产物为目标维护。 |
| 生命周期 | 关系版本检查、Binding 生命周期与 Family 状态。内部通用处理状态投影已实现，尚无公共通用入口。 | Scope 归档、恢复、显式删除及资源参与机制。 |

## Scope 身份、结构与入口

**已支持。** Scope ID 为不透明身份。描述符保存标题、外部引用、`parent_scope_id`、`context_references` 和版本。修改关系使用 `expected_version`。一个 Scope 最多一个结构父级，父子关系无环。Context Reference 当前只保存 Scope ID，供 Prepare 使用直接来源。[Scope 模型](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/scope/models.py#L55) · [关系校验与更新](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/scope/application.py#L195)

下图沿用订单场景的显示名。树枝连接两个 Scope 时表示真实父子关系，连接制品时表示该制品存于此 Scope。`..>` 表示引用或选入路径，`~~>` 表示证据溯源。引用本身不授予权限，图中预期的文档资源仍需另行接入。

**现状图（master）**

Interface 和 Help 是 Orders 的真实子级，Runbook 则位于 Operations 下，只通过直接引用参与 Orders 的 Prepare。当前子树选择返回描述符，图中 Skill 的证据关系也局限在 Interface 内。

```text
Orders Scope                                 Operations Scope
+-- Profile: Service behavior@4              `-- Runbook Scope
+-- Interface Scope                              `-- Experience: Timeout notes@2
|   +-- Experience: Retry trace@3
|   `-- Skill: Callback checks@1
|         ~~> Retry trace@3 [same Scope]
`-- Help Scope
    `-- Experience: FAQ correction@1

Orders Scope ..> Runbook Scope [direct Context Reference]
Prepare(Orders): Orders + direct Runbook, for supported Families
subtree(Orders): descriptors of Orders, Interface, Help
```

**预期图（提议）**

同一棵树将用于显式范围操作，Profile 的证据地址则扩展到不同来源 Scope。框内标出一次明确选定的子树，Runbook 仍在框外，因此可参与获准查询或加工，但不会因引用或证据关系被纳入该子树的删除。

```text
Explicitly selected Orders subtree           Outside that subtree
+--------------------------------------+     Operations Scope
| Orders Scope                         |     `-- Runbook Scope
| +-- Profile: Service behavior@5      |         `-- Experience: Timeout notes@2
| +-- Interface Scope                  |
| |   +-- Experience: Retry trace@3    |
| |   `-- Skill: Callback checks@1     |
| `-- Help Scope                       |
|     `-- Experience: FAQ correction@1 |
+--------------------------------------+

Orders Scope ..> Runbook Scope [separately selected and authorized]
Orders / Service behavior@5
  +~~> (Interface Scope, Retry trace@3)
  +~~> (Help Scope, FAQ correction@1)
  `~~> (Runbook Scope, Timeout notes@2)

Explicit subtree delete selects boxed Scopes only.
References and lineage do not enlarge that set.
```

业务对象的外部标识可以映射到 Scope ID，调用时传入实际 Scope 地址。需要级联管理的业务概念通过 Scope 结构表达，文档配置由注册服务按映射后的 Scope 保存。个人、团队、项目和知识空间都可通过这种映射组织，应用决定它们之间的关系。

结构关系决定真实后代，引用和业务映射决定可选来源。增加共享入口后，来源的身份和归属保持不变。服务校验结构及并发版本，并限制每次操作的工作量，包括展开节点数和遍历深度。接入应用可以在创建和移动流程中施加更严格的组织规则，并决定产品显示的层级。

## 选材、授权与共享路径

**已支持。** `ScopeSelection` 可解析 `exact`、`subtree`、`all`，返回 Scope 描述符。HTTP 对 exact 的每个 Scope 检查 `scope.read`，subtree 和 all 要求 `server.observe`。观察集合不能代替内容读取检查。逻辑制品授权的资源键不含 Revision，精确版本地址也不会收窄授权范围。[范围解析](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/scope/application.py#L293) · [观察权限](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/server/app.py#L5732) · [Family 授权](1396_handoff_access_control.md)

**现状图（master）**

同一身份可以搜索 Interface，却只能精确读取 Help 中获准的 FAQ 制品，且没有 Runbook 读权。它们在业务上属于哪个入口，不改变这三种授权结果。

```text
Request identity
+-- Scope read grant: Interface Scope
`-- Artifact read grant: Help / FAQ correction

Orders Scope
+-- Interface Scope   [Scope search and exact reads allowed]
`-- Help Scope        [exact FAQ read allowed; Scope search denied]

Runbook Scope         [no read authority]
subtree(Orders) observes descriptors; HTTP requires server.observe
```

**预期图（提议）**

将材料选入同一操作后，仍须分别检查实际来源，并在加工提交时重验必要权限。图中 Runbook 的必要读权被撤回，候选便不能提交。固定输入版本只确定取材地址，版本和用途限制还须由 Access 模式执行。

```text
Orders input rule [standing boundary + application mapping]
+..> Interface Scope                [whole-Scope input]
+..> Help / FAQ correction@1         [pinned Artifact input]
`..> Runbook Scope                  [required read permission withdrawn]

Selected inputs
+-- query/read -> authorize each actual origin
`-- Dream -> Orders / Service behavior@4
             `-- Candidate cannot commit with a required input revoked

Pinned address does not imply a revision-only grant.
Entry, use, and revision restrictions need an enforceable Access mode.
```

先固定本次选材范围，按精确地址去重并保留选入路径，再对实际操作鉴权。父子关系和共享入口均不自动授予权限。现有子树解析会读取全部 Scope 描述符，大规模部署需要改为按父级定位后代，并限制工作量。加工若要求完整输入，超过预算时须缩小选择后重试。

| 动作 | 现有契约与扩展边界 |
| --- | --- |
| 搜索整个 Scope | 当前要求该 Scope 的 `scope.read`。单件制品读权只支持相应精确读取。 |
| 用材料生成目标 | 本地 Dream 已检查证据、贡献与目标权限。跨 Scope 须在真实来源检查，并确认用于目标及披露结果的依据。 |
| 查看并批准候选 | 本地审核检查证据、`scope.review` 和操作所需目标写权。扩展后同样检查真实来源。 |
| 独立交付版本 | Experience 发布检查来源 `artifact.share` 与目标 `scope.admin`，Topic Memory 检查两端 `scope.admin`。受支持 Family 的副本有独立地址及生命周期。 |
| 仅经某入口使用固定版本 | 入口、用途、版本和撤回语义仍须与 Access 设计共同确定，无法执行这些限制时应拒绝对应模式。 |

如果原文和结论都获准独立交付，可以先通过 publication 发布副本，再在目标加工。若私人证据只能用于生成团队可见的结论，还需定义处理主体、审核者、受众和撤回效果。发布原文会将原文交付给接收方，无法满足这种保密要求。现有读权也不包含向新受众披露的权限。 [发布权限](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/server/app.py#L5752) · [Dream 授权](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/server/dream_access.py#L79)

接入应用将共享边界、用途和受众配置成持续有效的规则，系统在此范围内自动发现材料。规则可以覆盖未来内容，手工分享保留为补充方式。授权仍可能到期或被撤销，业务映射和规则都不能使它永久有效。

### 持续共享与自动选材

共享应先约定持续有效的边界，系统再自动纳入符合条件的材料。边界包括来源 Scope 范围、可用制品及版本条件、处理身份、允许用途、目标和产物受众。它可以覆盖将来新增的内容，无需用户逐件点击分享。涉及新的来源 Scope 时，由有权的接入流程按既定规则建立相应授权，结构挂接或成员标签本身不产生读权。

来源范围可以从 Scope 关系或业务映射动态解析，规则不必逐个列举制品。新增来源 Scope 的发现和授权也可由有权的接入流程自动完成，PowerContext 无需维护团队或项目成员表。

**现状图（master）**

已有整 Scope 读权可以覆盖其中新增的可读内容，逻辑制品读权可以覆盖该制品的新版本。现有查询和读取可用于发现这些材料，但尚无本文提出的持续跨 Scope 规则、输入检查点与目标加工的一体流程。

```text
Orders task identity [existing read grants]
+..> Alice work Scope
|    `-- Experience: Retry trace@3 -> @4 [readable under the Scope grant]
`..> Bob work Scope
     `-- Experience: Duplicate callback@1

Applications can call existing discovery and read operations.
Cross-Scope recurring selection and Dream are not integrated.
```

**预期图（提议）**

应用将事前约定配置成可复用规则。新增制品或版本满足规则时自动成为可用输入，下一次任务固定实际采用的清单。自动纳入材料后，目标制品仍按自己的 Family 规则生成、审核和提交。

```text
Standing rule [version=2, target=Orders, permitted use and audience]
+..> Alice work Scope [current approved Experiences]
+..> Bob work Scope   [current approved Experiences]
`-- Alice private Scope [outside the range]

New revision or periodic discovery
  -> resolve rule + check current authority
  -> pin exact input list and target baseline
  -> Dream / processing -> Candidate -> reviewed Orders Artifact

Retry -> same pinned inputs; next run may select newer revisions.
```

PowerContext 需要提供可版本化的选材规则解析、可恢复的输入处理状态，以及与查询、Dream 共用的授权检查。每次运行记录规则版本、目标、输入精确地址及摘要和目标基线。调度可先由应用定时调用，后续接入事件触发。处理状态记录上次成功采用的输入和目标修订，用于合并重复通知和跳过已处理输入，不复用目标的本地 Source 游标。

目标当前版本作为比较基线，不作为支持自身修订的新事实。自动提交产生的目标更新不能单独触发反复加工自己。输入视图和派生 Source 都保留证据来源，规则须避免把同一内容的转换或回流当成新增独立支持。

规则只在有效授权范围内执行。规则修改、来源撤权或目标停写后，任务须复核是否仍可运行和提交。规则版本失效时重新解析，运行重试则保持已固定输入，不在同一次重试中改用新 head。每次展开、发现和处理均须限制目标数、输入数及工作量，过大的范围拆分为可追踪任务。

规则配置、输入检查点和任务状态归目标 Scope 管理。目标归档时停止相关普通加工，删除时纳入该 Scope 的资源处理。来源 Scope 归档后仍可被外部规则按有效授权读取，来源删除则按规则的完整性要求报告不可用并重新评估后续运行。

执行身份与数据 Scope 分开。任务在团队或项目 Scope 发起，也须使用获准的任务身份读取来源、写入目标。接入应用负责把业务共识落实为授权和规则配置，PowerContext 在每次使用及提交时执行它们。现有授权无法表达的用途或受众限制，需要先补齐 Access 契约。

## 跨 Scope 查询与精确读回

**已支持与可组合。** Artifact Search 以一个 Scope 和 Family 检索当前可用 head，内置支持 Experience、Skill、Topic Memory、Atomic Memory。Profile 可以精确读或显式选择为 Prepare 快照。Prepare 对当前与直接引用 Scope 召回 Atomic Memory、Experience，Profile 快照也可覆盖这些 Scope。Topic Memory 只查当前 Scope。含引用的 Prepare 不支持 context reuse。[搜索契约](../docs/workflows/search-artifacts.md) · [Prepare 取材](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/runtime/application.py#L1330) · [本地 Topic 查询](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/runtime/application.py#L2148)

**现状图（master）**

三个来源可以分别检索，应用负责合并各次获准调用的结果。Prepare 已有直接引用召回，但它不会因为 Interface 和 Help 是 Orders 的子级，就自动执行对子级的查询。

```text
Orders Scope
+-- Interface Scope -> Search(Interface, Experience) -> Retry trace@3
`-- Help Scope      -> Search(Help, Experience)      -> FAQ correction@1

Operations Scope
`-- Runbook Scope   -> Search(Runbook, Experience)   -> Timeout notes@2

Application merges these separately authorized calls.
Prepare(Orders) reads current + direct references, not implicit children.
```

**预期图（提议）**

业务入口先解析为明确的 Scope 选择，再由应用或拟议的聚合接口执行有界查询。RAG 还需接入文档检索，结果保留来源 Scope、文档版本和 chunk，后续读回按该地址重新鉴权。父子结构仍不自动授予检索权限。

```text
Orders Scope                                   Operations Scope
+-- Interface Scope                            `-- Runbook Scope
|   `-- document: Callback API@3 / chunk 2         `-- document: Timeout notes@2 / chunk 4
`-- Help Scope
    `-- document: Retry FAQ@1 / chunk 1

Orders query ..> [Interface, Help, Runbook] [explicit mapping + authority]
                   `-- document retrieval -> bounded result merge

One returned hit: (Runbook Scope, Timeout notes@2, chunk 4)
Readback -> recheck that exact origin and current resource state
```

接入应用可先用现有接口编排多库查询。PowerContext 若提供聚合接口，须复用相同的选材和授权规则，并限制目标数、候选数和返回总数。Artifact 结果按 `(scope_id, family, artifact_id, revision)` 去重，独立副本保持各自地址。各目标的分数不可直接比较时，按目标内排名融合，并保持稳定次序。完整模式在必要目标失败时返回对应错误。部分成功模式须标明覆盖不完整，且只返回获准结果。

在上述 RAG 映射下，接入应用将业务查询解析为获准的 Scope 选择，文档服务返回 `(scope_id, document_ref, chunk_id)` 和原文定位。文档服务决定哪些 chunk 可参与检索，并负责索引和排名，聚合接口保留这些响应含义。业务的 `knowledge_base_ids` 须通过映射解析成 Scope 选择，库级配置和任务归属也需与 Scope 结构及生命周期衔接。document 检索、精确读取和交付仍待实现。

精确读取一个版本时，读权可能仍覆盖该制品的其他版本。仅授权这个版本还需要额外限制。只分享某个 document 版本时，服务须在打分、截断和摘要生成前限制候选范围。先搜整库再过滤会影响排名并漏召回，只索引最新版也无法检索旧版内容。相关索引和授权能力未就绪时，只支持已有精确读权或适用 Family 的独立交付。打开结果、读取 chunk 和后续装配上下文时，都须重验当前权限及资源状态。

## 从多个来源加工一个目标

**已支持。** Dream 请求在运行 Scope 内解析 `ArtifactRef`、`SourceRef`，记录精确证据和目标基线，生成 Candidate、`no_change` 或 `needs_evidence`。接收请求、后台执行、候选生成与审核分别有授权或证据校验。写入由目标 Family 执行版本条件检查。[Dream RFC](1715_multi_artifact_dreaming.md) · [本地证据解析](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/evidence/resolver.py#L90) · [生成与提交](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/dream/service.py#L419)

**现状图（master）**

Orders 内的项目复盘和现有 Profile 可参与本地 Dream，候选审核后按目标版本条件提交。Alice 的材料位于另一 Scope，现有本地引用和证据解析不能直接将它作为该次输入。

```text
Orders Scope [Dream run and target]
+-- Experience: Project review@2 [evidence]
+-- Profile: Service behavior@4  [baseline]
`-- Candidate: revised behavior [pending review]

Project review@2 + behavior@4 -> local manifest -> Dream -> Candidate
Approve with valid evidence and unchanged baseline -> behavior@5

Alice work Scope
`-- Experience: Retry trace@3 [outside run Scope; not a local input]
```

**预期图（提议）**

扩展的是证据的来源范围，结果仍只写入 Orders。候选须保留 Alice 和 Bob 各自的精确地址，审核时重验来源、目标和基线，继续使用现有 Family 的条件写入机制。

```text
Alice work Scope                         Orders Scope [target]
`-- Experience: Retry trace@3            +-- Profile: Service behavior@4
                                         `-- Candidate: conditional retry rule
Bob work Scope                               +~~> (Alice work, Retry trace@3)
`-- Experience: Duplicate callback@1         `~~> (Bob work, Duplicate callback@1)

Selected remote evidence + target@4 -> Dream -> Candidate in Orders
Review rechecks both origins, target authority, writable state, and baseline.
Only a valid approval writes Orders / Service behavior@5.
```

1. 按有效规则自动发现输入，扩展请求使用已有 `ArtifactAddress` 形式，本地引用仍按运行 Scope 解释。固定规则版本、输入清单和目标基线，清单节点、根证据分组、候选引用和最终修订依据均记录来源 Scope。同 ID 同版本位于不同 Scope 时是不同输入，多条路径命中同一地址只计一次。既有清单保留原身份和摘要。
2. 在实际来源检查权限、Family 准入和状态，按输入视图及操作契约解析证据。确需读取底层 Source、Lineage 或附件时，分别检查其访问权限。将支持证据、反证和当前目标一起比较，派生材料及已有结论不能循环计为独立支持。证据不足或无需修改时，分别返回相应结果。
3. 只向获准审核者展示差异和依据。批准时复核候选、规则版本、来源用途、产物受众、目标权限、基线和可写状态。必要授权被撤回后阻止旧候选提交，不能通过丢弃反证使其获批。目标已改版时基于新基线重新评估。结果只写入目标，并按该产物的留存及披露规则维护。

幂等重试保留本次选定的输入和目标，不能自动改用新的来源 head。鉴权因暂时故障无法完成时，保留错误并按恢复机制重试。授权已撤回时，须重新取得有效依据。实际写入和审核操作都须执行这些检查。 [证据清单](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/evidence/models.py#L90) · [候选批准](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/review/service.py#L676)

### 加工产物与输入视图

团队、项目等目标 Scope 的 Dream 或后加工，以形成和维护目标产物为目标。处理可以合并重复结论、解释冲突、增加适用条件或淘汰旧结论。读取和继续加工以已提交的目标 Artifact 为入口，来源记录提供证据及出处，不要求目标逐段保留原文。运行也可能返回 `no_change` 或 `needs_evidence`。

**现状图（master）**

现有 Dream 已区分 `sources` 与 `artifacts`，解析后形成证据投影，操作还限制输入 Family 和角色。例如，`revise_profile` 只允许当前目标 Profile，`revise_skill` 也不接受目标之外的 Skill。解析会按 Family 规则展开证据与来源，尚不能把任意其他 Scope 的现成制品直接接入。[操作绑定](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/dream/bindings.py#L71) · [输入与目标校验](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/dream/models.py#L123)

```text
Orders Scope
+-- sources: captured Source references
+-- artifacts: local Experience + current target Profile
`-- revise_profile
    -> Family and role checks
    -> evidence resolution -> EvidenceProjection -> Candidate

Another Profile or unsupported Family -> rejected by operation admission
```

现有系统已将制品写入、替换或恢复产生的内部 Source 标记为 `lineage_only`，生成选材会排除它们。这种用于溯源的记录不能直接变成新一轮加工的原始事实。[Source 用途标记](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/sources/content.py#L87) · [生成读取规则](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/persistence/generation_sources.py#L28)

**预期图（提议）**

优先保留来源制品的身份和 Family，用明确的输入视图适配处理器。需要内容的处理器可以接收该版本的受控投影，投影仍记录自己来自加工制品。支持新 Family 或新角色时须扩展服务端准入及验证规则，不能通过包装成 Source 绕过现有约束。

```text
Alice work Scope
`-- Experience: Retry trace@3 [exact origin and digest]
    +-- Artifact view: Family snapshot + provenance --------+
    `-- Content view: derived projection + adapter version -+
                                                           |
                                                           v
Orders Scope [authorized use and output audience]
+-- pinned input manifest [one origin per input, admitted view]
+-- Profile: Service behavior@4
`-- Dream / processing -> reviewed Service behavior@5
```

| 输入视图 | 保留的信息与处理方式 | 需要补充的能力 |
| --- | --- | --- |
| 现成 Artifact | 以精确来源版本参与加工，保留 Family、结构化内容、摘要和已获准的溯源信息。 | 带 Scope 的解析、输入角色准入，以及制品边界上的证据校验。 |
| 制品内容投影 | 将来源版本适配成处理器需要的内容，保留来源地址、转换版本和摘要，明确它是派生材料。 | 注册适配器及投影验证。临时投影不自动变成目标 Source Journal 中的新事实，也不推进其游标。 |

同一制品的不同视图保留同一个证据来源，不能计为两份独立支持。如果需要复用必须读取 Source Journal 的处理器，还需定义显式的派生 Source 接入，保留来源版本和转换身份、保证写入幂等，并明确相应游标如何推进。现有普通 Source 接入本身没有这套派生语义。

提议将获准使用的来源制品作为输入单元。若某个 Family 的验证要求核验原始 Source、附件或根证据，则在该操作中单独读取和鉴权。制品读权不自动授予底层数据访问权，证据关系也不产生额外的独立支持。哪些操作可直接采用制品、哪些必须展开根证据，需要定义各自的输入契约，现有解析不能仅靠关闭检查完成这一扩展。

输入契约还须区分目标基线与新增支持证据。已获准留存的目标产物作为比较基线时，不能因历史原文不再可用就被当成不存在，也不能被算成支持自身的新证据。现有解析和各 Family 的修订流程需要分别核对、适配这一边界。

独立发布副本仍可用于原文交付，但不作为第三方 Scope 加工的必经步骤。来源准入须覆盖加工用途和结果受众，目标产物按约定的留存及披露规则管理。读取已提交的结论与查看原始证据分别鉴权。是否还需要保留原文快照，由对应验证、重建或留存要求决定。

### Profile、Topic Memory 与文档服务

**已支持。** 主体绑定通过 `integration=subject`、`kind=user`、`external_id=subject_key` 解析普通 Scope。首次写入可以指定既有 Scope，也可由服务创建。后续写入沿同一绑定解析，为个人画像提供稳定的主 Scope。服务可在同一事务中将 Source 写入业务和主体 Scope，各 Scope 独立维护至多一份 `profile/profile`。Profile 和 Topic Memory 按本地 Source 窗口及游标处理。服务双写 Source，画像在各 Scope 分别加工。 [Source 双写](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/scope/subject_sources.py#L99) · [Profile 处理](1485_profile_artifact.md) · [Topic Memory](1417_topic_memory.md)

**现状图（master）**

个人画像已经有由稳定绑定解析的主 Scope。双写会在两个 Scope 分别保存 Source，各自沿本地窗口和游标处理，并不共享一个画像处理进度。

```text
Subject binding: (subject, user, alice) ..> Alice profile Scope

One Source capture, one transaction
+-- Orders work Scope
|   `-- Source: Shift review -> local processing and cursor
`-- Alice profile Scope
    +-- Source: Shift review -> local processing and cursor
    `-- Profile: Preferences@2

Each Scope processes its own Source Journal.
```

**预期图（提议）**

目标可以直接使用获准的远端制品作为证据，同时保留自己的本地 Source 处理进度。新增跨 Scope 清单记录这些输入，目标 Family 仍负责准入和提交。远端材料无需伪装成本地 Source。

```text
Alice work Scope
`-- Experience: Retry trace@3
          |
          | admitted, authorized remote evidence
          v
Orders Scope [target]
+-- Profile: Service behavior@4 -> reviewed revision@5
+-- Local Source Journal + cursor [unchanged by remote evidence]
`-- Qualified manifest
    `~~> (Alice work Scope, Retry trace@3)

The registered Family admits and commits the target revision.
```

团队、项目等目标也可通过普通 Scope 和映射指定，并遵守每个 Scope 内的 Profile 约束。跨 Scope 证据须单独记录，使用远端制品不能伪造本地 Source 或推进本地游标。从远端证据初始化首份 Profile 需要明确初始化条件。Dream 仍限制输入 Family，接入 document 或 chunk 还须适配其证据和处理规则。注册文档服务可提供解析、索引和任务处理，并须执行公共的 Scope 状态检查和提交条件。

## Scope 生命周期与级联

### 归档、恢复及状态生效

**已支持。** Scope 只有版本化元数据与关系，尚无公共归档或删除操作。Access Binding 可以撤销或到期。Atomic Memory 的 `forgotten` 会退出普通召回并保留历史。它们分别控制访问和 Family 状态。内部 processing-status 投影已能汇总本地处理进度、阻塞及 Dream 状态，尚未接入公共通用入口。[Scope 模型](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/scope/models.py#L144) · [Atomic 生命周期](../docs/workflows/atomic-memory.md) · [内部只读处理状态](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/processing_status.py#L1)

**现状图（master）**

现有授权撤销控制某个身份的访问，Atomic Memory 的遗忘状态控制该条记忆的召回。Scope 自身还没有能统一停止本层内容写入和加工的归档状态。

```text
Orders Scope [metadata version=7; no archive state]
+-- Interface Scope
|   +-- Experience: Retry trace@3
|   `-- Atomic Memory: Retry hint [forgotten; excluded from recall]
`-- Help Scope

Consumer identity ..> Interface [grant can be active, revoked, or expired]
Processing status remains an internal read-only projection.
```

**预期图（提议）**

归档 Interface 后，它的普通写入和加工停止，Orders、Help 及外部 Reliability 保持各自状态。Reliability 仍可按有效授权读取其中材料并写入自己的目标，归档与授权撤销分别控制不同的行为。

```text
Orders Scope [writable]
+-- Interface Scope [ARCHIVED]
|   +-- Experience: Retry trace@3 [readable with current authority]
|   `-- New Sources, local Dream, Candidate commits [blocked]
`-- Help Scope [writable]

Reliability Scope [writable, outside Orders]
+..> Interface / Retry trace@3 [authorized source use continues]
`-- Dream -> own on-call Skill [allowed with required authority]

Reopen Interface -> same Scope identity, writable again
Archive changes only Interface; deletion still requires an explicit request.
```

归档只作用于指定 Scope，停止新增 Source、制品内容写入、普通加工和候选提交。真实子级和引用方保持各自状态。服务仍允许管理查询、授权撤销和恢复，也允许完成已生效删除的清理。已有来源授权继续按原范围和期限生效。

状态转换须与数据写入、候选创建、批准和插件发布共用事务锁，或使用等效的版本条件。服务在入队、开始计算、持久化候选和最终提交时分别检查状态，取消在途计算或丢弃其结果。恢复后保持原身份，重新调度时复核当前权限和基线。删除始终须显式发起。

### 删除集合、共享引用与清理结果

**现状图（master）**

现有父子和引用记录约束数据关系，但没有公开的 Scope 删除及资源清理流程。持久绑定指向缺失 Scope 时，解析会报冲突，仍需显式修复。

```text
Orders Scope                          Operations Scope
`-- Interface Scope                   `-- Runbook Scope
    `-- Experience: Retry trace@3

Orders Scope ..> Runbook Scope [stored Context Reference]
Structural parent and incoming-reference FKs use RESTRICT.
There is no public Scope delete operation.

Persistent binding ..> missing Scope
  `-- resolution -> 409 scope_binding_target_missing
```

**预期图（提议）**

删除将成为针对已确认集合的显式操作。框外 Runbook 保留原件，Analytics 也继续存在，但它引用的 Interface 在集合内，随后会显示来源不可用。停止访问和清理完成分开报告，物理擦除还须明确单独的范围和完成条件。

```text
Confirmed deletion set      Outside the deletion set
+---------------------+     Operations Scope
| Orders Scope        |     `-- Runbook Scope [retained]
| +-- Interface Scope |
| `-- Help Scope      |     Analytics Scope [retained]
+---------------------+     `..> Interface Scope [origin becomes unavailable]

Orders ..> Runbook [detach reference; retain Runbook]
Confirmed set -> access cutoff -> owned-resource handling
Cleanup progress is separate from any physical-erasure completion.
```

移除业务入口可以只更新映射或引用。删除知识库本体须选定对应 Scope。单 Scope 删除遇到真实子级时，建议返回冲突。显式子树删除先预览集合，再检查每个目标的权限和结构状态。只比较根版本会漏掉新挂接的后代，因此须与新增子级、移动和发布串行化，集合变化后重新确认。共享边和证据 lineage 均不能扩大删除集合。选中的后代即使仍被外部入口引用，也按本次动作处理，引用方随后会看到来源不可用。保留共享入口无法保证原件留存。

Scope 管理保存操作身份、目标集合、停止访问的状态和资源处理进度。参与服务按 Scope 处理制品、原文、选材规则及检查点、未发布文档登记、在途任务、chunk、索引和缓存。多个存储可分阶段处理，重试接续未完成工作。Scope 对普通读取不可见后，通用读取、搜索、证据预览、Prepare、插件读取和旧任务提交均须检查该状态。获准的管理入口仍可查询有限状态。

删除请求须明确动作和完成条件。逻辑删除可以保留正文和历史，清理完成表示本次约定的处理已完成，物理擦除还需明确原件、历史、副本及存储范围。必需处理失败时，操作仍为未完成或失败，不能用搜索无命中证明擦除完成。本提案定义归档恢复。删除恢复和物理擦除需要分别确定契约。

### 清理参与、依赖与留存

**已支持。** 父子关系、来源证据与 publication 记录有存储约束。持久绑定或默认指针指向缺失 Scope 时，现有解析返回 `409 scope_binding_target_missing`，要求显式修复。[证据与发布约束](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/persistence/tables.py#L330) · [绑定解析](../docs/workflows/scopes-and-access.md)

**现状图（master）**

Orders 已能保存独立发布的副本，但 publication 记录仍以外键约束来源身份。直接移除来源记录会遇到存储约束，现有各资源持久化路径也没有统一的 Scope 清理协调。

```text
Alice work Scope
`-- Experience: Retry trace@3

Orders Scope
`-- Experience: Published retry trace@1 [independent copy]

Publication record ~~> (Alice work Scope, Retry trace@3) [FK RESTRICT]
Source identity is constrained while the independent target is retained.
Families and other resource stores currently use their own persistence paths.
```

**预期图（提议）**

Scope 操作由各资源处理器报告进度。即使制品和 Source 已处理，索引待重试时也不能报告整体完成。Orders 的独立副本按自己的留存规则保留，来源清除后仍需保存不可用的精确出处，这部分需要存储迁移。

```text
Lifecycle operation: Alice work Scope [access cutoff effective]
+-- Artifact / Source handler [complete]
+-- Index handler             [retry pending]
`-- Overall cleanup          [incomplete]

Orders Scope [outside the operation set]
+-- Experience: Published retry trace@1 [retained]
`-- Exact origin ~~> (Alice work Scope, Retry trace@3) [unavailable]

Retaining an unavailable origin while purging source content needs
storage changes, such as a content-free address tombstone.
```

参与服务声明其 Scope 资源、可见性检查和幂等处理方式。若某类资源尚不能满足选定的删除或擦除动作，须在接受前拒绝该模式。各服务实现自己的资源处理，Scope 管理协调停止访问、汇总进度并按约定条件报告完成。文档服务通过这套机制参与集合级联。

清除来源正文并保留目标溯源，需要解决现有 RESTRICT 外键。一种可选实现是引入不含正文的地址墓碑，通过存储迁移保留精确身份和不可用状态，以及既有修订的身份与摘要。已按允许独立留存的用途和受众提交的加工产物及独立副本，继续按目标规则管理。来源更新、撤权或删除会约束后续读取及加工，不自动删除这些已提交产物。若某种披露模式要求持续来源授权，须单独约定暂停披露和重建规则。

删除默认 Scope 前须明确更换指针，其他持久绑定须清理或修复。接入应用更新自己维护的业务映射，服务拒绝旧映射对已不可用资源的访问。擦除个人账号数据时，业务方确定个人数据集合，服务按指定范围执行，保留集体 Scope 中的团队记录。管理员换人后，获准继任者继续维护同一目标。

## 组合入口与职责

PowerContext 接口接收已解析的 Scope、动作和资源。业务入口可以组合若干调用，公共服务在实际使用和提交时执行约束。注册的 Family 服务和文档服务也是这套数据能力的实现，接入应用通过其接口完成制品处理和文档操作。

```text
Product action -> application mapping and operation selection
                         |
              Scope / Access / query / Dream / lifecycle
                         |
              registered Family and resource services
```

| 操作 | PowerContext 及注册服务保证 | 接入应用选择或封装 |
| --- | --- | --- |
| 组织集合 | Scope 身份、结构有效性、关系版本检查与受控范围解析。 | 业务对象映射、可见层级、创建和移动流程。 |
| 使用共享材料 | 持续范围解析、当前权限检查、精确输入及模式限制。 | 共享边界、自动纳入条件、用途及产物受众。 |
| 查询与形成上下文 | 有界调用、结果身份、来源可见性及状态检查。 | 查询入口、业务筛选、排序策略和呈现。 |
| 形成长期制品 | 输入视图及 Family 准入、规则版本、证据清单、审核与条件提交。 | 目标产物、处理策略、调度、提示及审核分工。 |
| 关闭或清理集合 | 归档、显式删除的生效点、资源处理和可查询结果。 | 触发条件、保留期、审批和外部副本管理。 |

多个业务对象共用一个 Scope 时，Scope 级操作影响所有相关入口。若只停止某个入口使用材料，可调整该入口的选材、任务或相关授权。若要整体停写，则归档目标 Scope。业务批量操作涉及多个无结构归属的 Scope 时，接入应用须处理各目标的结果，这些操作不保证跨存储原子性。

## 接口演进与验收

新增 HTTP 和 SDK 字段尚待确定，现有 Scope、Artifact 和授权接口保留原契约。实现时先在公共运行时服务中增加选材、来源解析和生命周期检查，再由 HTTP、SDK、MCP 和注册扩展调用同一路径。旧 Scope 初始为可写，旧证据仍在原运行 Scope 中解析，并保留历史身份和摘要。跨 Scope 加工、资源参与和删除模式须明确相应的持久化迁移。

```text
Existing local calls and records -> preserve their interpretation
Extended contracts -> shared service checks -> HTTP / SDK / MCP / extensions
```

| 验收场景 | 现行基础 | 扩展后的检查 |
| --- | --- | --- |
| 已授权工作 Scope 新增制品或版本 | 整 Scope 读权可覆盖可读新增内容，持续规则待实现。 | 自动发现符合规则的材料，私密范围不被纳入，固定本次输入。 |
| 只有目标自身自动提交产生更新 | 现有 target 角色及 `lineage_only` 规则避免自证。 | 不把目标更新或内容投影当成新增独立事实反复触发加工。 |
| 相同变更重复通知或任务重试 | 本地已有幂等与版本检查基础。 | 同一规则、输入和目标基线不重复采用，重试不切换输入 head。 |
| 外部制品按不同视图参与加工 | 本地已有 Artifact／Source 区分及 Family 约束。 | 保留来源与派生角色，拒绝通过 Source 包装绕过准入。 |
| 来源失效但加工产物已获准独立留存 | 目标制品有自己的身份与修订。 | 停止失效来源的后续使用，按产物约定继续管理目标，证据查看单独鉴权。 |
| 同一材料经团队与项目入口选入 | 精确地址、现有 Scope／制品授权。 | 同地址去重，逐用途检查，保留选入路径。 |
| 直接引用还引用了第三个 Scope | Prepare 当前只取直接引用。 | 有限展开，读权及预算有效，结构范围不等于内容授权。 |
| 两份 Experience 修订目标 Profile | 本地 Dream、Family 写入与审核。 | 不同来源身份保留，反证不能丢弃，证据不足不提交。 |
| 审核期间撤权或手工改版 | 本地重新鉴权、目标版本检查。 | 在真实来源重验，拒绝失效候选或重新评估新基线。 |
| 知识空间查询共享知识库 | 单 Scope 查询及授权可复用，RAG 服务待接入。 | 每个库按 Scope 鉴权，document／chunk 来源保留，单件权不扩成整库搜索。 |
| 来源或目标归档 | Scope 归档待实现。 | 来源获准读取继续，目标本层普通写入和提交被阻止，清理仍可完成。 |
| 删除空间的真实子树 | Scope 删除与资源参与待实现。 | 确认集合，外部共享来源保留，失败可续做，截止与完成状态准确。 |
| 个人擦除与管理员换人 | Scope 身份与数据归属独立。 | 按指定个人集合擦除，集体记录由获准继任者维护。 |

现有用例覆盖直接引用、来源读权、本地 Dream 撤权和审核竞争，可作为回归基础。跨 Scope 和生命周期的新路径还需端到端验收。图中的预期流程属于待实现设计。 [直接引用](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/tests/e2e/test_builtin_runtime.py#L335) · [来源读权](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/tests/e2e/test_access_control_regressions.py#L330) · [Dream 撤权](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/tests/e2e/test_artifact_dreaming.py#L1781) · [审核竞争](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/tests/e2e/test_artifact_dreaming.py#L3130)

# Drawbacks

操作需要检查权限和资源状态，涉及结构范围时还需检查 Scope 关系。持续共享增加增量发现、去重和重试状态，跨来源任务也可能在审核期间失去输入。内容投影须保留派生关系，跨存储生命周期还需对账。业务级联关系映射到 Scope 后，RAG 的库级配置、归属和查询接口也须适配。

```text
More source Scopes -> more checks and possible invalidation
More resource stores -> staged cleanup and explicit completion accounting
```

# Rationale and alternatives

普通 Scope 已有身份、授权和结构，可用于表达需要级联管理的业务概念。业务的父子关系映射到 Scope 结构，共享使用通过引用和授权表达。团队／项目记忆与 RAG 都可使用这些通用能力，Artifact Family 继续管理内容和版本，资源服务负责具体处理。

```text
Business cascade -> collection Artifact -> Artifact lifecycle/processing cascades
Business cascade -> Scope structure    -> common lifecycle + resource handlers
```

- 继续以知识库 Artifact 表达业务层级，需要为原本没有实质级联关系的制品新增生命周期和处理规则。将这类业务关系映射到 Scope 结构，可以集中管理级联范围，让 Artifact 继续负责内容和版本。
- 内建团队、项目和知识空间类型，会把各业务的对象关系与产品规则带入公共数据模型。普通 Scope 与外部映射可表达所需入口。
- 独立发布副本适合可独立交付的内容。复制会增加身份和更新责任，来源撤权也不会自动撤回独立副本。
- 沿整张引用图删除会影响其他集合。删除按明确 Scope 归属和真实子树执行，共享来源通过引用与当前授权使用。

# Prior art

团队和项目记忆所需的来源授权、Profile 演进和审核，可以复用现有 Scope、Access、Dream 和 Family 设计。RAG RFC 2570 以知识库 Artifact 管理文档的过渡方案暴露了制品侧的级联需求，其文档处理、持久任务和版本化片段设计仍可在业务关系映射到 Scope 后适配。 [知识库提案](https://github.com/oceanbase/powercontext/pull/2570)

GitHub 按仓库分配团队角色，Linear 的跨团队项目保留私有 issue 的访问限制。项目汇集多个团队的工作时，私有内容仍按原权限可见。 [GitHub 仓库角色](https://docs.github.com/en/organizations/managing-user-access-to-your-organizations-repositories/managing-repository-roles/repository-roles-for-an-organization) · [Linear 私有团队](https://linear.app/docs/private-teams)

Nowledge Mem 支持读取多个获准 Space 的材料，将结果写入指定 Space，并记录多来源结论的出处。它的读取、写入和溯源边界可用于参考。PowerContext 的授权和生命周期仍按自身规则执行。 [Spaces](https://mem.nowledge.co/docs/spaces) · [Crystals](https://mem.nowledge.co/docs/concepts/crystals)

```text
Resource roles -> authorized material
Read several spaces -> save in one destination
Derived conclusion -> retain contributing origins
```

# Unresolved questions

- 持续共享规则由谁配置和修改，任务身份如何绑定，现有 Access 如何落实加工用途、产物受众及撤回效果？
- 哪些操作可将已审核 Artifact 作为输入边界，哪些必须核验底层 Source？何时需要把制品投影显式落盘为派生 Source，以及如何保持证据独立性与游标语义？
- 首期跨 Scope Dream 接入哪些 Family 和输入角色，怎样初始化首份目标制品，并保持现有证据语义？
- Scope 删除首期支持哪些动作、保留规则和资源参与协议？地址墓碑、默认指针与外部存储的迁移如何定义？
- RAG 的库级配置、模型切换和持久任务如何与业务到 Scope 的映射及生命周期契约衔接？哪些技术预算需要在服务端固定或配置？

```text
Requested mode -> supported authorization and resource capabilities?
                     +-- yes -> enforce the complete path
                     +-- unresolved -> define the contract before admission
```

# Future possibilities

持续选材与自动纳入属于本提案能力，首期可由应用定时触发。后续可增加事件订阅和调度优化，以及面向特定应用的批量操作封装。多跳共享还需明确每一跳的选材、权限和撤回效果，新的 Artifact Family 和存储服务也可接入资源参与机制。

```text
Event subscription -> coalesced scheduling -> standing rules -> current checks
```
