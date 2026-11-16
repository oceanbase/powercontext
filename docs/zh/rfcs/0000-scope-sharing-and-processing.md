---
title: "RFC 0000：Scope 间共享、查询与目标制品维护"
description: "定义如何在授权范围内持续使用多个 Scope 的材料，维护一个目标制品，并管理需要级联处理的业务数据。"
---

- Proposal Name: `scope_sharing_and_processing`
- Start Date: 2026-11-09
- RFC PR: [oceanbase/powercontext#2611](https://github.com/oceanbase/powercontext/pull/2611)

# Summary

PowerContext 为 Agent 提供上下文数据基础设施。本提案约定如何跨 Scope 使用材料，以及归档和删除如何影响这些材料。应用与来源方事先约定共享范围，系统自动发现其中符合条件的现有和未来材料。查询保留命中的来源地址。加工则固定本次输入，在一个目标 Scope 内比较证据、解释冲突并维护正式制品。需要级联管理的业务关系可映射到普通 Scope 的父子树。归档只停止指定 Scope 的普通写入和本地加工，显式删除按确认的 Scope 及其所属资源执行。

```text
Orders project                          Orders knowledge space
  Inputs: Alice and Bob's work records    Owns: Interface and Help
  Reviewed output: project Profile@5      Shared library: Operations / Runbook
  Cites: Alice's Retry trace@3            Delete Orders subtree: Orders, Interface, Help
```

# Motivation

订单项目需要回答“回调超时后能不能重试”。新版通道带幂等键时重试成功，旧通道却可能重复扣款。只向成员发两份排障记录，使用者还得自己判断适用条件。项目需要能读懂和修正的正式结论，还需要在新事实出现后重新评估结论。已有个人画像能根据本 Scope 新采集的记录（Source）更新。工作材料分散在多个 Scope 时，还需要一条受控的持续取材路径。

RAG 有另一项需求。一个知识空间关闭时，可能要停止其所拥有知识库的写入、处置未完成的导入任务并清理索引。RFC #2570 的过渡设计在一个 Scope 内用 `knowledge-base` Artifact 管理文档。原有制品之间没有需要这种级联的父子关系。按该过渡设计，制品层还要协调父级失效后子文档的可见性、待处理任务和资源清理。本提案将这种业务级联关系映射到 Scope 结构，文档内容和版本仍由制品管理。共享进来的外部库可以参与查询，生命周期由其归属方管理。下图用缩进和 `+--` 标出接口库及其负责的文档和导入任务。[知识库提案](https://github.com/oceanbase/powercontext/blob/71e842cd2a34a7943ab0adecb3542771e3da387e/docs/zh/rfcs/2570-knowledge-base.md)

```text
RFC #2570: Orders Scope                  Proposed: Orders Scope
  +-- Interface knowledge-base Artifact    +-- Interface Scope
       +-- API document@3                       +-- API document@3
       +-- pending import for API@4             +-- pending import for API@4
```

# Guide-level explanation

Scope 标识一组数据的归属。Source 是服务采集的一条原始记录，例如一次复盘。Artifact 是可以按版本读取的制品，Family 表示它的种类，例如 Experience（经验记录）、Profile（画像）或 Skill（操作指南）。图中的 `Retry trace@3` 表示《重试记录》第 3 版。读回它的完整地址还包含所属 Scope、Family 和制品 ID。Dream 根据证据提出修订，待审核的修订称为 Candidate。

- **团队／项目记忆。** Alice 允许订单项目使用其工作 Scope 中符合约定的排障记录。以后有新材料时，她无需逐件分享。项目读者看到的正式结论可以是“新版通道携带幂等键时可重试。旧通道先查交易状态”。有权查看原始依据的人可以打开对应版本。项目维护者能修正结论，个人画像仍由个人在自己的 Scope 使用。
- **RAG。** 在订单知识空间搜索时，结果可以同时来自自有的接口库和共享的运维手册。读者能从命中的段落打开对应文档版本。管理员移除共享手册入口时，运维空间保留原件。

```text
Orders project                         Orders knowledge space
  read: Service behavior@5               search: Interface + shared Runbook
  inspect evidence: separate grant       open: Runbook / Timeouts@2 / chunk 4
```

# Reference-level explanation

应用把业务 ID 映射到 Scope，约定共享条件、用途、受众和触发时机。PowerContext 及注册服务负责解析范围、在真实来源鉴权、按目标 Family 的规则提交，并控制归档和删除的生效。HTTP、SDK、MCP 和注册扩展必须通过公共运行时执行这些检查。验收时从相应入口发起操作，既有本地调用保留原契约。新增端点和字段待定。

下表中“现有”指固定代码基线，“可组合”指应用可调用现有服务完成的步骤。持续共享规则、通用跨 Scope Dream 以及公共归档和删除均待实现。末节的 document、chunk 和持久导入任务来自 #2570 的设计快照。文档服务、跨库检索、文件原件的消费资格隔离及相应产品入口均待接入。[实现基线 1cb0e75db](https://github.com/oceanbase/powercontext/tree/1cb0e75dbd56227bb13550d63f301b8f39feb931) · [知识库提案](https://github.com/oceanbase/powercontext/blob/71e842cd2a34a7943ab0adecb3542771e3da387e/docs/zh/rfcs/2570-knowledge-base.md)

| 能力 | 现有或可组合 | 本提案要提供 |
| --- | --- | --- |
| 确定操作范围 | 普通 Scope、单父树、直接引用、观察范围解析 | 区分真实后代和外部来源，有界地解析和确认范围 |
| 交付或引用材料 | Scope／逻辑制品授权、部分 Family 的独立发布 | 执行来源、用途、受众和撤回约定 |
| 持续取得输入 | 本地 Source 采集、按窗口生成、应用调度 | 在约定范围内自动发现材料，记录每轮评估的输入与结果 |
| 查询并打开结果 | 单 Scope 搜索、有限的直接引用 Prepare、应用聚合 | 约定跨来源查询的预算、覆盖范围与精确读回 |
| 维护一个正式制品 | 本地 Dream、候选审核、按目标版本提交 | 按操作接纳跨 Scope 的 Source 或制品，按其真实来源复核 |
| 管理归档和删除 | 关系版本、授权撤销和个别 Family 的状态 | 指定停止写入或访问的范围，跟踪各资源处理结果 |

## Scope 关系与操作范围

**现状。** Scope 用不透明 ID 标识。`parent_scope_id` 定义无环的单父树，`context_references` 保存直接引用的 Scope ID，修改关系需带 `expected_version`。`ScopeSelection` 的 `exact`、`subtree` 和 `all` 返回 Scope 描述信息。HTTP 对 `exact` 的每个 Scope 检查 `scope.read`，后两种范围要求 `server.observe`。当前子树解析需读取全部 Scope 描述符。查看树中的节点和读取节点里的内容分别鉴权。[Scope 模型与关系校验](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/scope/application.py#L195) · [范围解析](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/scope/application.py#L293)

**图示约定。** 后文使用不同连线区分归属、使用来源和证据出处，箭头方向如下。

| 画法 | 含义 |
| --- | --- |
| 缩进与 `+--` | 在 Scope 树中，Scope 下的 Scope 是真实子级，Scope 下的 Source 或 Artifact 存放于该 Scope。流程图中的同类分支仅列举步骤或选项。 |
| `Orders ..> Runbook` | **使用方指向来源。** Orders 引用或选入 Runbook 的材料，Runbook 的结构归属不变。具体是现有 Context Reference 还是提议的共享入口，由图中文字说明。 |
| `Profile@5 ~~> Retry trace@3` | **产物修订指向证据。** Profile 第 5 版以 Retry trace 第 3 版为依据。这是 lineage，记录该版结论的出处。 |
| `Source -> Dream -> Candidate` | **输入指向处理步骤或结果。** 普通箭头表示图中标注的数据流、处理顺序或版本变化。 |

引用和证据连线均不授予权限，也不扩展子树删除范围。方括号标注操作集合或状态。

```text
Current
  Orders                            Operations
    +-- Interface                     +-- Runbook
    +-- Help
  Orders ..> Runbook                direct Context Reference for Prepare

Proposed
  [Orders, Interface, Help]         confirmed structural subtree
  Orders ..> Runbook                also selectable for authorized use
  Orders / Profile@5 ~~> Runbook / Timeout notes@2
                                   evidence for one revision
```

**提议。** 服务按父子关系计算真实后代。应用可另选外部 Scope 或精确的制品版本，结果保留选入路径。例如，Runbook 若再引用第三个 Scope，Orders 的直接引用并不把第三个 Scope 纳入范围。只有规则明确覆盖第三个 Scope 时，服务才可在限定的深度与数量内选入它，并在该来源独立鉴权。制品的 lineage（哪一版依据哪些材料形成）可以跨越结构与共享范围。图中 Runbook 既能提供材料又能出现在出处中，删除 Orders 子树时其归属仍是 Operations。一次删除所选中的真实子级，即使被外部引用，也属于此次操作。

应用根据业务 ID 查到实际 Scope 后再调用服务。个人、团队、项目和知识空间都可这样映射。团队与项目各自选择目标，也可位于不同子树。产品可组合一个团队的多个项目或一个项目的多个团队，并在创建、移动时施加自己的组织限制。若多个入口共用同一 Scope，归档或删除都会影响全部入口。只关闭其中一个入口时，应用可以调整它的映射、所选材料或相关授权。大规模操作中，应能按父级定位后代，并限制节点数和深度。

## 共享材料的交付、使用与披露

**现状。** 整个 Scope 的搜索要求该 Scope 的读权。受支持 Family 的逻辑制品授权可允许精确读取某一制品，授权键不含修订号。固定第 3 版的地址决定读哪份内容，不能据此声称调用者只获准第 3 版。现有 Scope 授权可直接给成员和任务身份使用，应用按需接入成员目录。[Scope 与访问控制](../docs/workflows/scopes-and-access.md) · [制品授权](1396_handoff_access_control.md)

| 如何交付材料 | 现有能力与限制 |
| --- | --- |
| 接收方可以独立保存一份原文 | **复制（Copy）**。Experience、Skill 和 Handoff 的精确版本发布要求来源 `artifact.share`、目标 `scope.admin`。Topic Memory 通过专用流程发布，要求两端 `scope.admin`。副本在目标以新身份从第 1 版开始。Atomic Memory、Profile、Prompt 不支持 publication。 |
| 读者按来源当前权限使用材料 | **引用（Ref）**。保留原地址，访问时检查来源权限。现有直接 Scope 引用只供受支持的 Prepare 取材，指定版本可按现有权限精确读回。规则驱动的跨 Scope 加工还需实现。 |

```text
Current
  Alice / Experience@3 -> publication -> Orders / Experience copy@1
  Alice / Profile@3 [publication unsupported]
  Orders ..> Alice work Scope [direct reference grants no read authority]

Proposed
  Material selected for Orders
    +-- Copy: deliver an independently retained revision
    +-- Ref: read the selected revision under a current origin grant
  Proposed Orders conclusion -> check permitted audience and retention
```

**提议。** 双方约定材料范围、执行账号、用途、目标及读者。读取或复制原文、用作证据、审阅候选和向目标读者披露结论，各有相应的权限要求。例如，Alice 可以允许处理账号用私密记录生成项目结论，只有指定审核者可以人工查阅原文。目标 Scope 标识产物归属。服务按实际执行账号检查各来源的读取权限和目标的写入权限，但仅有这两项权限仍不足以向新的受众披露。采用这种模式前，Access 设计须说明谁能授权该用途、谁能看原文、服务如何限制结论的受众及留存。仅限某入口或某版本的使用也须由 Access 执行。若服务无法执行所要求的限制，须拒绝该用途。[发布实现](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/publication.py#L151) · [Dream 授权](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/server/dream_access.py#L79)

单次复制停在交付的版本，固定版本的引用也只指向该版。目标以后是否更新，取决于能否获得新材料、谁发起处理及如何提交。来源授权到期或撤回后，须阻止依赖该权限的读取和待审候选提交。已经按约定获准独立留存的副本和正式结论继续按目标规则管理。留存结论本身并不授权保存原文快照。只有核验、重建或留存约定确需原文且获准保存时，才保留相应快照。要求随来源撤权而停止展示的模式，须另外规定停止披露和重建的条件。

## 持续发现材料与固定本次输入

**现状。** 应用可把获准采集的工作记录直接写入目标 Scope。Profile 和 Topic Memory 的常规处理各自从本地 Source Journal 按窗口取材，以游标记录进度。Profile 处理器用上一版正式 Profile 作比较基线，用本窗口符合条件的 Source 更新结论。如果结论未变，游标可以推进而不创建新修订。配置画像策略和生成模型后，应用可显式触发处理，也可启用默认关闭的后台调度。[画像生成与调度](../docs/workflows/use-profiles.md) · [Profile 处理窗口](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/artifacts/profile/service.py#L223)

```text
Current: Orders
  Source Journal: reviews A and B [consumed], review C [after cursor]
  Profile@4 and review C -> local Profile processing

Proposed: rule v2 for Orders
  +-- Alice work / Retry trace@3 [selected]
  +-- Bob work / incident Source [selected]
  Alice private [outside rule]

  Run 1 and its retry: same inputs, including Retry trace@3
  Alice publishes Retry trace@4 -> eligible for selection in a new run
```

**提议。** 接入应用让双方先约定长期有效的共享范围。规则可以覆盖指定工作 Scope 内现有及未来符合条件的记录和制品版本。获准流程也可以自动发现新的来源 Scope 并建立所需授权。成员标签、树上的新节点和规则本身都不授予读权。人工选择用于补充和纠正，无需贡献者逐件批准规则已覆盖的材料。

**一次运行。** 规则随目标 Scope 管理，说明以后可以到哪里找材料。每次加工另行固定规则版本、目标与基线，以及本次各输入的真实地址、摘要和角色。同一地址经多个入口选入时保留各路径，按适用用途鉴权，证据只计一次。同一次重试沿用这份清单并复核权限。新的来源版本留给下一轮选材。原规则失效时停止依赖它的运行，按有效规则重新选材。临时鉴权故障可重试，撤权后须重新获得授权。

目标的处理记录保存本轮的规则、输入、基线和评估结果，供重复通知查回。补充证据或显式请求重新评估时可以另开一轮。只有正式提交的修订才记录该版最终采用的证据。使用外部输入不推进目标本地 Source Journal 的游标。

限制每次发现的 Scope 数、输入数和处理工作量。要求完整输入的任务若超预算，应由调用方明确缩小范围，或分成可追踪的批次并在采用结果前核对完整性。应用可定时触发发现，按规则选取新增材料。来源归档后，外部规则仍可按有效权限读取材料。来源删除后，任务须报告依据不可用，并按是否要求完整输入决定后续处理。目标自身更新和同一内容的反复转换都不能作为新事实触发下一轮修订。

## 跨 Scope 查询与结果读回

**现状。** Artifact Search 一次在一个 Scope、一个 Family 检索当前可检索的版本。内置支持 Experience、Skill、Topic Memory 和 Atomic Memory。应用可分别查询获准的 Scope 后合并。Prepare 在当前与直接引用的 Scope 中召回 Experience、Atomic Memory，也可显式选择 Profile 快照。Topic Memory 的 Prepare 检索限于当前 Scope。含引用的 Prepare 不支持上下文复用。树上的子级不会被自动搜索。[制品搜索](../docs/workflows/search-artifacts.md) · [Prepare 组装](../docs/workflows/prepare-context-text.md) · [直接引用用例](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/tests/e2e/test_builtin_runtime.py#L335)

```text
Current
  Search(Interface, Experience) -> Retry trace@3
  Search(Help, Experience)      -> FAQ correction@1
  Runbook / Timeout notes@2 [exact read under an item grant]

Proposed: Orders query
  +-- Interface -> (Interface, Experience, Retry trace, 3)
  +-- Help      -> (Help, Experience, FAQ correction, 1)
  +-- Runbook   -> item search only if permitted and supported
  Open Retry trace hit -> check access to Interface / Retry trace@3
```

**提议。** 应用聚合与平台聚合都须解析明确的来源范围，在各来源鉴权，并限制 Scope 数、候选数和总返回数。按 `(scope_id, family, artifact_id, revision)` 去重。独立发布的副本有自己的地址。分数不可直接比较时，按各来源内部排名融合，同等排名保持稳定次序。调用方要求完整结果时，必要来源失败应返回错误。允许部分成功时，只返回获准结果。必要来源失败或范围未查全须标明覆盖不完整。重新打开结果、通过插件读取片段或通过 Prepare 装入 Agent 上下文时，都要重验来源权限与可见状态。

仅获准搜索某个制品或版本时，检索服务必须在打分、截断和生成摘要前限制候选。搜完整个库再过滤可能漏掉允许的命中，也可能暴露无权查看的内容。只索引当前版本的服务须拒绝针对历史版本的搜索。精确读取或独立发布各按本身的授权和 Family 能力处理。例如，接口库发布《重试记录》第 4 版后，已有命中仍指向第 3 版。打开时须按该地址检查当前权限和历史版本的可用性。

## 从 Source 或 Artifact 维护一个目标制品

**现状。** 本地 Dream 解析运行 Scope 内的 `SourceRef` 和 `ArtifactRef`。操作规定可用的 Family 和角色，例如 `revise_profile` 接收当前目标 Profile，并可使用合格的 Experience 或 Source。`derive_skill` 则不要求已有目标 Skill。服务记录输入及目标基线，生成待审候选、`no_change`（当前结论仍适用）或 `needs_evidence`（证据不足）。提交成功才得到新的正式修订。Dream 独立于 Family 日常消费 Source 的进度。[Dream 操作与输入](1715_multi_artifact_dreaming.md) · [操作绑定](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/dream/bindings.py#L71)

```text
Current: Orders
  local Source and local Experience -> Dream with Profile@4 -> Candidate
  published Experience copy [searchable, unresolved for Dream]

Proposed: Dream with Orders / Profile@4
  +-- Alice work / Retry trace@3 [supports retry with an idempotency key]
  +-- Bob work / Duplicate callback@1 [counterexample on the old channel]
  +-- Orders / incident Source [local observation]
  Evidence and baseline -> no_change, needs_evidence, or Candidate
  Approved Candidate -> Orders / Profile@5
```

**提议。** 各加工操作声明可接受的输入和角色，以及要求已有目标还是允许创建目标。使用外部证据创建首份 Profile，也须有明确的初始化契约。处理器可以合并重复经验、解释冲突、补充适用条件或废止旧建议，形成目标读者能直接使用的结论。后续读者和加工任务以已提交的目标制品为入口，需要查证时再按权限打开出处。

| 输入 | 处理器实际读什么 | 准入与记录 |
| --- | --- | --- |
| 原始 Source | 已获准的原始观察、工作记录或反例。可持续写入目标本地 Journal | 本地常规处理遵循 Family 窗口。加工操作接纳外部 Source 时，须记录来源 Scope、Source 身份及摘要，分别检查访问和用途。 |
| 已有 Artifact | 来源某一版的结构化内容，如已审核的排障经验 | 保留 Scope、Family、ID、修订、摘要和证据角色。哪些操作可直接采用该版结论，哪些须读取底层 Source、附件或根证据，由各操作明确。每一次额外读取都需授权。 |
| 制品内容的转换结果 | 同一修订按处理器需要转换的正文或字段 | 记录原地址、转换方式的版本和摘要。转换结果和原件只算一份依据。若处理器要求把派生 Source 落盘，还须定义其角色、幂等写入和游标语义。 |

**现有副本的限制。** 目标 Scope 可以检索已发布的 Experience 副本。Dream 的证据解析器却将带 `publication_source` 的 Experience 标成 `unresolved`，不把其正文送入生成。因此复制适合交付、查阅或人工采纳。要让加工直接使用这类材料，必须实现并验证该操作的输入契约。制品创建、替换或恢复产生的内部 `lineage_only` Source 只记录出处。禁止把它或制品投影包装成新的独立事实，以绕过 Family 准入或重复计算支持证据。[Experience 副本与索引](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/publication.py#L171) · [副本的证据解析](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/evidence/resolver.py#L390) · [Source 准入](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/persistence/generation_sources.py#L28)

跨 Scope 的清单、根证据分组、候选引用和正式修订依据都须记录来源 Scope。制品地址沿用 `ArtifactAddress`，不同 Scope 的同 ID 同版本制品是两个输入。两个输入如经核验都来自同一条原始观察，该观察只能算一份独立依据。相同的 ID 或内容摘要本身不能证明同源。旧的本地引用和证据仍在各自原运行 Scope 中解析，迁移须保留其原有身份和摘要。处理器比较支持证据、反例和目标基线。即使历史原文已无法打开，已获准独立留存的目标仍可作为基线。目标当前版本不能作为支持自身修订的新事实。

**审核与提交。** 接收请求、后台取材、写候选和批准时，服务按当时的操作检查权限与状态。审核者须有权查看差异，并须有相应的来源权限才能打开原始依据。审核者可以修改候选正文，目标和证据须保留。批准时重新检查规则版本、真实来源、用途与受众、审核权限（`scope.review`）、目标写权、可写状态和基线版本。必要来源被撤权后须阻止依赖它的候选提交，不能通过删掉反证让候选获批。目标被人工改版后，Family 的条件写入拒绝旧候选，重新评估须以新版本开始另一轮运行。阅读正式结论和打开原始依据分别鉴权。[候选批准](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/review/service.py#L676) · [本地 Dream 撤权用例](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/tests/e2e/test_artifact_dreaming.py#L1781)

**运行结束后。** 提议沿用 Dream 运行、Candidate 决策和制品修订，按下表记录跨 Scope 加工的结果。批准响应丢失时，应先查询原 Candidate 决策及对应制品版本。已提交则按权限取回结果，无法确认时保留未决状态，不重复生成或发布。

| 本轮结果 | 目标 Scope 可据此判断什么 |
| --- | --- |
| `no_change` | 该清单已在指定基线上完成评估，正式修订保持不变。 |
| `needs_evidence` | 记录本轮评估及缺少的依据。保留现行结论，补充材料后可重新评估。 |
| 候选待审或已拒绝 | 可以查到提议、依据与审核状态。它没有成为正式修订。 |
| 候选已批准 | 已存决定标识提交的目标修订，该修订保留实际采用的依据。 |

## Scope 归档、删除与清理

**现状。** Scope 已有关系版本和可撤销的授权，少数 Family 有自己的状态。Atomic Memory 的 `forgotten` 会使该条目退出普通召回。内部处理状态可以汇总本地进度和阻塞。公共接口尚不能归档或删除整个 Scope。[Scope 接口](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/openapi/powercontext.yaml#L759) · [本地处理状态](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/processing_status.py)

```text
Current: Orders
  +-- Interface
  +-- Help
  Orders ..> Operations / Runbook [direct reference]
  No public Scope archive or delete

Proposed: archive Orders
  Orders [ordinary local writes, processing and Candidate commits stopped]
    +-- Interface [own state, still writable]
    +-- Help [own state, still writable]
  Reliability ..> Orders [valid grant to use archived material in Reliability]

Proposed: explicitly delete the confirmed subtree [Orders, Interface, Help]
  +-- access cutoff -> ordinary reads and Candidate commits stop in that set
  +-- resource cleanup -> Interface index [retry pending]
  +-- physical erasure [separate terms, if supported]
  Operations / Runbook [outside, retained]
  Analytics [outside] ..> Interface [unavailable]
```

**归档与恢复。** 归档只停止指定 Scope 新增 Source、写入制品内容、本地普通加工和候选提交。真实子级保持各自状态，其他 Scope 仍可按有效来源授权使用归档材料并写入自己的目标。授权管理、恢复和已生效删除的善后仍可进行。入队、开始计算、持久化候选和最终发布都须检查状态。状态转换与写入共用事务锁或等效版本条件，在途工作不得越过生效点。恢复使用同一 Scope ID，重新调度时重验当前权限和目标基线。

**删除与留存。** 删除须显式发起。建议删除单个仍有子级的 Scope 时返回冲突。显式删除子树时先预览集合，再对每个目标检查权限。只检查根版本会漏掉新挂入的后代。集合确认与新增、移动和发布必须串行化或进行等效校验。集合变化需重新确认。只删除确认集合中的 Scope 及其所属资源。服务解除从集合内指向外部的共享引用。外部引用方随后看到其指向的集合内来源不可用，外部 Scope 继续存在。

| 操作结果 | 可以向调用者作出的承诺 |
| --- | --- |
| 停止访问已生效 | 普通读取、历史读回、证据预览、Prepare、检索和插件读取均不可再访问目标，旧任务不能提交。管理入口按权限查询有限状态。逻辑不可见可以保留存储中的正文。 |
| 本次清理已完成 | 必需的资源处理器均完成约定动作。任一索引、缓存或任务清理失败则保留失败／未完成状态，可从未完成项重试。 |
| 已按约定物理擦除 | 必须先约定原件、历史、副本及存储的具体覆盖范围，并有对应完成证据。搜索无命中不足以证明擦除。 |

Scope 服务持久保存操作身份、确认集合、访问截止和分项进度。各注册服务声明归其管理的资源、可见性检查和幂等处理方式，按 Scope 处理制品、Source、共享规则及检查点、文档登记、在途任务、chunk、索引和缓存。必需服务无法执行所选动作时，服务须在接受请求前拒绝该模式。应用决定何时发起操作、保留多久以及如何处理自己控制的副本。跨存储清理和无结构归属的多 Scope 批量操作不保证整体原子性。前者按分项进度继续，后者逐 Scope 返回结果。

旧 Scope 初始可写。引入删除状态时须迁移相应记录。移除部署默认 Scope 前须显式更换默认指针，并清理或修复受影响的持久绑定。过期的业务映射也必须遵守访问截止。现有持久绑定或默认指针指向缺失目标时，解析会报告 `409 scope_binding_target_missing`，需显式修复。来源清除后，已获准留存的正式结论应继续显示精确出处及其不可用状态。现有出处和 publication 外键会约束来源记录的删除。一种迁移方式是保留不含正文的地址墓碑，保存原身份和摘要。具体存储方案、删除恢复及物理擦除模式待定。[约束表](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/persistence/tables.py#L330) · [绑定解析](../docs/workflows/scopes-and-access.md)

**验收。** 归档生效后，即便本地任务已入队也不能发布。删除预览之后若新增或移动子级改变了确认集合，必须重新确认。索引清理失败时，普通读者已无法打开删除集合中的版本，管理员仍可查到未完成项并重试。

## 团队／项目记忆如何使用和维护

项目负责人为“订单项目”选择一个普通 Scope 存放正式 Profile。稳定性团队可以另选 Scope 保存值班 Skill。成员通过现有 Scope 授权访问各自获准的内容。接入产品应让用户可以查看当前记忆、管理持续使用的材料，以及审核或手工修正结论。

```text
Alice's personal Scope
  captured Sources -> Profile policy -> personal Profile -> Prepare

Orders project Scope [review required]
  +-- current: local review Sources -> local Profile processing
  +-- proposed: Alice and Bob's work inputs -> cross-Scope Dream
  Candidate -> review -> Orders / Profile@5 -> members and their Agents
  Orders / Profile@5 ~~> Bob work / Duplicate callback@1
```

**个人如何用。** 用户选择允许采集的工作记录。主体绑定把用户 ID 映射到普通 Scope。写入记录需要写权，建立绑定另需绑定权限。服务可以在一次事务中把指定记录写入业务 Scope 和个人 Scope，两份 Source 的 ID 和摘要相同，但 Journal 位置和处理进度各自独立。重新发起同一 HTTP 请求会创建新的一对 Source，调用方须按此处理重试。新建主体 Scope 时启用画像策略，绑定已有 Scope 时沿用其原策略。

用户可启用后台调度或显式触发生成。默认激活模式为 `automatic`。如需逐次审核，应选择 `review_required`，用户可以修改候选正文、批准或拒绝。修改候选须保留其目标和证据。用户也可用当前版本的 ETag 手工替换正式画像，或将历史正文作为新修订恢复。人工编辑不会重放已消费的 Source。

要在某次 Agent 会话中使用画像，用户须在 Prepare 显式选择 Profile。读取正式画像本身不会启动生成。产品可通过插件提供采集、策略配置、触发处理和选择上下文的入口。[个人画像工作流](../docs/workflows/use-profiles.md) · [Agent 上下文配置](../docs/workflows/prepare-context-text.md) · [Cindy 操作](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/integrations/cindy/plugins/powercontext-hooks/node/worker.cjs#L192)

**材料交付与持续维护。** 如果项目获准独立留存某一版排障经验，贡献者可以发布 Experience 副本，成员可以检索、查阅或人工采纳这份记录。项目要维护自己的正式结论，则由持续规则自动选取获准的外部材料，也可结合本地采集的复盘、决定和反例更新目标制品。项目以 Profile 为目标时，每个 Scope 最多保存一份 `profile/profile`。需要维护者逐次审核时，使用 `review_required`。

**跨来源加工的预期。** 项目和来源方约定哪些工作记录可用于何种产物、谁能看依据、谁能读最终结论。系统自动选取符合条件的更新。订单项目比较 Alice 的成功重试和 Bob 的重复扣款记录，把宽泛建议改成带通道和幂等条件的结论。成员打开的是项目的正式版本。审核者能在授权范围内查看具体反例。直接采用外部 Experience，还是必须展开其原始 Source，要由 `revise_profile` 等具体操作规定。同类规则也可用于更新个人 Scope 的 Profile，前提是来源允许该用途且执行账号获准写入个人目标。

如果成员只贡献整理好的经验，就可能遗漏事故背景或没有写成制品的反例。产品应让管理员补上已知遗漏、改正错误，并能决定以后持续采集哪些项目事实。维护者手工改版后，旧候选无法获批，审核者仍可拒绝它。如需新的建议，要基于新版本发起另一轮加工。管理员换人只需给继任者相应权限，集体记录仍由同一 Scope 持有，无须转移前任的个人制品。擦除账号数据时，应用明确个人数据集合，以及哪些集体产物已经获准独立留存，服务按相应的范围和完成条件处理。

## RAG 如何入库、共享和检索文档

**预期形态。** 在“订单知识空间”中，用户看到自有的接口库、帮助库和共享的运维手册。若订单空间负责前两库的生命周期，应用可将它们映射为真实子 Scope。只用作导航的知识空间也可以只保存业务映射。用户选择一个库导入文件，文档服务返回持久任务。界面显示处理阶段、失败原因和可用版本。成功发布后，搜索结果能定位到具体的文档版本和片段。[RFC #2570 的导入和检索设计](https://github.com/oceanbase/powercontext/blob/71e842cd2a34a7943ab0adecb3542771e3da387e/docs/zh/rfcs/2570-knowledge-base.md)

```text
Orders space Scope                              Operations space Scope
  +-- Interface Scope                             +-- Runbook Scope
  |     +-- immutable file Source                   +-- Timeouts@2 / chunk 4
  |     +-- import job: file Source -> API@3
  |     +-- API@3 with versioned chunks
  +-- Help Scope

Orders space Scope ..> Operations / Runbook Scope (shared entry, authorized use)

  Search Orders: authorized calls -> hit (Runbook, Timeouts@2, chunk 4)
  Remove shared entry: detach it     Delete Orders subtree: Interface + Help + Orders
```

**导入与更新。** #2570 复用 Source 存储保存不可变文件原件。原件供文档任务解析，制品内保存整篇 `document` 修订，文档服务管理 chunk、原文定位和索引。文件变化或显式重解析可发布新修订。发布失败仍能使用上一已发布版本。文档任务另行记录进度，不借用普通记忆的 Source 游标。接入时须在写入通知、启动恢复、自动消费和手动生成入口落实消费资格，让专用原件只进入文档流程。这些原件与仅记录操作出处的 `lineage_only` 内部 Source 用途不同。

**共享与读回。** 订单空间引用运维手册库时，查询会在获准的库中检索新发布的内容。应用把业务库 ID 解析为 Scope，文档服务负责本库的候选过滤、索引和排名，返回 `(scope_id, document_ref, chunk_id)` 及原文位置。聚合结果保留每个命中的来源与库内排名含义。手册更新后，已有命中仍指向原文档版本，打开时重验该版本的权限及可用性。若共享仅覆盖单篇文档或指定版本，精确读取须遵守该范围。要在其中搜索，还须由 Access 层执行相应授权，文档服务在打分前限定候选，索引覆盖所选版本。独立交付固定版本需要 document 支持复制，且接收方获准留存。把命中用于生成 Topic Memory 还需选定可写目标，并按操作契约验证 document 或 chunk 输入。

**归属与关闭。** 移除共享手册只改变订单入口。归档接口库停止其普通写入及本地导入发布，获准的其他空间仍可使用原有版本。显式删除订单空间及其真实子树时，处理接口库、帮助库及其登记的原件、未完成任务、片段和索引。运维手册留在运维空间，清理失败可以继续跟踪。

**模型配置与重建。** #2570 的过渡方案允许一个 Scope 内多个库按库配置解析和切块，共用 embedding／索引配置。切换模型需要重建该共享 Scope。映射调整后必须明确共享哪份配置、一次切换覆盖哪些库，以及旧的 `knowledge_base_ids` 和任务如何解析到对应 Scope。[配置和模型切换](https://github.com/oceanbase/powercontext/blob/71e842cd2a34a7943ab0adecb3542771e3da387e/docs/zh/rfcs/2570-knowledge-base.md#81-更换模型并重建索引)

# Drawbacks

持续规则需要发现更新、记录处理进度和处理撤权。等待审核时来源越多，越容易遇到权限或基线变化。多库查询要限制工作量并说明缺失覆盖。跨存储删除要记录每项清理的结果。RAG 文档服务还要调整配置和任务的归属，才能参与 Scope 级操作。

```text
Orders / Candidate [pending] -> Bob's required grant expires -> commit blocked
Delete Interface [access stopped] -> index cleanup fails -> cleanup incomplete
```

# Rationale and alternatives

普通 Scope 已有稳定身份、结构和授权。把需要级联的业务关系映射到 Scope，可以让新增资源服务参加同一套范围和生命周期管理。继续沿用知识库 Artifact 也能集中组织文档，但每种此类集合都要在制品层处理父级失效、未发布资源和清理。内建团队或项目类型则会把各产品的组织规则带入共同数据模型。保留应用映射可以同时支持有结构归属的空间和纯导航入口。

应用可逐件发布允许独立留存的材料，或直接向目标写入获准采集的 Source。这两种做法已能交付固定版本和积累本地事实，但要持续采用仍归来源方管理的更新，还需在约定范围内反复发现，并固定每轮输入。按原处授权引用可省去副本的存储和更新工作，代价是后续读取依赖来源权限。删除若沿引用或证据链展开，会影响其他业务所属的材料。显式确认真实子树可以限定这个范围。

```text
Knowledge-base Artifact approach       Scope approach
  +-- Interface Artifact                  +-- Interface Scope
       +-- API@4 import job                    +-- API@4 import job
  Artifact service coordinates closure    Scope confirms range, job owner handles cleanup
```

# Prior art

PowerContext 的 Profile 处理和 Dream 审核提供了两条可复用的更新路径。RFC #2570 的文件原件、逐文档任务、版本化片段和索引提供 RAG 的文档处理设计。[Profile](1485_profile_artifact.md) · [Dream](1715_multi_artifact_dreaming.md) · [RAG](https://github.com/oceanbase/powercontext/pull/2570)

GitHub 按仓库分配团队角色，Linear 汇集多个团队的项目时仍限制私有内容的访问。Nowledge Mem 的 Spaces 和 Crystals 展示了从多个允许的空间读取、在一个空间保存结论并记录出处的用法。[GitHub 仓库角色](https://docs.github.com/en/organizations/managing-user-access-to-your-organizations-repositories/managing-repository-roles/repository-roles-for-an-organization) · [Linear 私有团队](https://linear.app/docs/private-teams) · [Spaces](https://mem.nowledge.co/docs/spaces) · [Crystals](https://mem.nowledge.co/docs/concepts/crystals)

```text
Personal Scope: Profile@4 and new Sources -> Profile processing
Orders Scope:   local evidence -> Dream -> Candidate -> review -> Artifact revision
```

# Unresolved questions

| 待定问题 | 推荐方向与确定条件 |
| --- | --- |
| 谁能改规则？谁能批准用私密证据形成项目可见的结论？ | 与 Access 设计共同明确规则配置者、执行账号、证据审核者和产物读者。用审核期间撤权、正式提交后撤权两个例子，说明谁还能读哪一版结论或原文，以及何时需要停止披露。 |
| 哪些输入足以让某个操作修订或创建目标？ | 逐操作确定可接受的 Source、Artifact Family 与角色、须展开的原件或附件，以及首次没有正式基线时的行为。若处理器确需持久化派生 Source，还要给出同一版本重试时的身份、去重和游标规则。 |
| 各删除模式覆盖哪些资源，怎样判定完成？ | 按所选动作列出必需资源、截止点和完成证据。以默认指针或绑定未处理、来源清除后仍有出处、索引清理失败为例，确定参与服务的职责和迁移要求。删除恢复与物理擦除各自需要明确的契约。 |
| 如何限定单件和历史版本搜索？每次操作能展开多大范围？ | 明确哪些授权可用于搜索以及索引覆盖哪些版本。用单件授权验证打分前的候选范围，并按命中的精确地址验证读回权限。分别按发现、查询和加工所需的完整性与开销确定预算。 |
| RAG 改用 Scope 表达库的归属后，模型切换覆盖哪些库？ | 给出一个从 `knowledge_base_ids` 和在途任务到目标 Scope 的映射，列出共用活动配置且必须一起重建的库。 |

```text
Alice work / incident Source [private] -> Orders / proposed project conclusion
  +-- origin grant withdrawn before commit: block Candidate, retain Profile@4
  +-- origin grant withdrawn after a lawful commit: which terms govern Profile@5?
```

# Future possibilities

事件订阅可以缩短等待时间，合并通知可以减少重复调度。应用可封装本业务的批量操作。多跳共享还需逐跳定义选材、权限和撤回。更多 Family 和外部存储也可以加入资源处理协议。

```text
Alice publishes Retry trace@4 -> notifications collected -> one discovery run
  Orders ..> Alice work [recheck rule and grants before selecting @4]
```
