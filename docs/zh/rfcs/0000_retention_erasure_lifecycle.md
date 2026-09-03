- Proposal Name: `retention_erasure_lifecycle`
- Start Date: 2026-09-03
- Status: Draft
- RFC PR: [oceanbase/powercontext#0000](https://github.com/oceanbase/powercontext/pull/0000)
- Tracking Issue: [oceanbase/powercontext#1425](https://github.com/oceanbase/powercontext/issues/1425)
- Related Issues: [oceanbase/powercontext#1219](https://github.com/oceanbase/powercontext/issues/1219)、
  [oceanbase/powercontext#1321](https://github.com/oceanbase/powercontext/issues/1321)、
  [oceanbase/powercontext#1395](https://github.com/oceanbase/powercontext/issues/1395)、
  [oceanbase/powercontext#1397](https://github.com/oceanbase/powercontext/issues/1397)
- Related RFCs: [RFC 0014](0014_memory_layer_design.md)、[RFC 0046](0046_observability_foundations.md)、
  [RFC 0048](0048_handoff_artifact.md)、[RFC 0050](0050_artifact_candidate_review_inbox.md)、
  [RFC 0051](0051_experience_skill_artifact_families.md)、[RFC 0082](0082_handoff_report.md)、
  [RFC 1345](1345_scope_organization_and_agent_integration.md)、
  [RFC 1351](1351_standard_skill_package_lifecycle.md)、[RFC 1396](1396_handoff_access_control.md)、
  [RFC 1400](1400_source_definition_and_observation_model.md)

# Summary

本 RFC 定义 PowerContext 如何在不重写不可变历史的前提下，对 Source 与各类 context Artifact 进行保留、隐藏、归档、清理与物理擦除。

五种操作必须保持区分，因为它们回答的问题不同，后果也不同：

- **逻辑遗忘（logical forgetting）** 把内容从常规检索与注入中移除，但不重写历史。它就是现有的、带 Revision 的 Memory `forget()` 与 `reactivate()` 行为。
- **治理（governance）** 改变一个 Artifact 是否可被发现或发布。它就是现有的 `active`、`deprecated`、`retired` head 状态。
- **归档（archival）** 改变整个 Scope 的默认可见性，同时保留其持久历史。
- **保留清理（retention purge）** 在带版本的策略窗口过后，删除有界的运行记录与无引用的材料。
- **物理擦除（physical erasure）** 移除不得继续保留的权威内容，连同其所有已声明的投影与副本，并且只在必须保持引用有效的位置留下无内容的 tombstone（墓碑行）。

本 RFC 新增：按部署与 Scope 分层的带版本 Lifecycle Policy；一个包含 preview、plan digest、按 digest 批准、有界分批、崩溃恢复、校验与无内容运行回执的 Lifecycle Run 协议；阻止清理与擦除、并在每次 preview 中可见的 Legal Hold；对已擦除权威行的原地无内容 tombstone；针对 Source 擦除的影响报告与显式的派生 Artifact 策略；精确引用的稳定 erased 状态；针对全文、向量、head、缓存、包与远端 Receiver 副本的清理规则；Scope 归档；以及一份 append-only 的脱敏生命周期审计。

本 RFC 中没有任何机制会自动删除或重写历史。默认策略保留一切，因此升级后在操作员设置 policy version 和至少一个窗口之前，行为不发生任何变化。自动重要性衰减不是生命周期机制；任何基于年龄或访问频率的策略都必须显式、可预览，并且独立于检索排序进行配置。

# Motivation

PowerContext 已经有三套局部的生命周期机制，各自在其领域内是正确的，也各自明确止步于保留与擦除之前：

- RFC 0014 把 Memory `forget()` 定义为一个把条目标记为 `inactive` 的新 Revision，把 `reactivate()` 定义为其逆操作。RFC 0014 写明遗忘"只阻止后续检索或注入，不满足物理擦除要求"，并把物理擦除留给单独设计。HTTP 操作 `retire_memory_entry` 执行的就是这种逻辑遗忘；没有公开操作暴露重新激活。
- RFC 1351 定义了 Artifact head 治理，包含 `active`、`deprecated`、`retired` 三态和 `pc_artifact_heads` 上的 `governance_generation` CAS。公开的生命周期操作只接受 `family = 'skill'`。RFC 1351 同时写明首个实现"不执行自动的包垃圾回收"，后续收集器"只能在记录了保留期之后，删除没有任何可达 Artifact、Candidate 或 Source 引用的包"。
- RFC 1400 保护所有被持久 Artifact revision 引用的 Source 观察不受常规保留与垃圾回收影响，声明 head 删除不授权移除证据，并把保留策略推迟到一个先定义精确证据如何报告内容不可用、以及法律或用户请求的删除如何与不可变血缘交互的设计。

因此若干存储在没有任何策略的情况下持续增长：

- `pc_sources` 保存每条最多 4 MiB 的捕获 payload。Handoff Receipt、Work Contract、Task Outcome 和 Skill 使用捕获也都是 Source，所以运行证据与用户内容以同样的速度累积。
- `pc_artifact_candidate_versions` 永久保留每个被拒绝或已批准 Candidate 的完整 proposal，尽管已批准内容存在于 Artifact Revision 中，而被拒绝的 proposal 再也不会被读取。
- `pc_skill_packages` 保留每个规范包归档，无论是否仍有 Revision 引用它。
- `pc_model_usage_daily` 与 `pc_recall_token_daily` 只随其 Scope 一起删除，而目前不存在 Scope 删除。

有三项生产需求无法由任何现有机制满足：

- **外部删除义务。** Connector 可能观察到外部对象已被正向删除。RFC 1400 只把它记录为 head 状态。有些部署还必须在一个有界窗口之后停止保留捕获到的值。
- **擦除请求与离场。** 用户或管理员可能要求特定内容、或整个 Scope 中的一切，不再存在于 PowerContext 中。如今唯一的选项是保留文本的逻辑 `forget`，或破坏 schema 的手工数据库修改。
- **Legal hold（法律保全）。** 部署可能需要对选定材料暂停所有计划删除，并证明自己做到了。

schema 决定了解法的形状。行之间的每一条引用都使用 `ondelete="RESTRICT"`：Artifact 血缘到 `pc_sources` 与 `pc_artifacts`，head 到其 Revision，Memory entry version 与 head 到其 Revision，Candidate head 到其 version。在不先摧毁那些让精确引用与血缘可信的引用之前，删除一条被引用的行是不可能的。因此擦除必须保留行、移除内容。

那些替代方案看起来诱人却是错的。每张表一个 TTL 列，对权威性、级联、引用、投影或审计什么都没说。删除旧 Revision 会破坏血缘、精确引用、Handoff 校验与回滚。把 Ebbinghaus 式的相关性衰减当作删除策略，会把排序与用户意图、合规和持久历史混为一谈。逻辑 retire 之后永久保留一切，则无法满足存储限制、外部删除义务或擦除请求。本 RFC 给每项需求各自一个显式操作。

# Guide-level explanation

## 五个问题，五种操作

| 问题 | 操作 | 对象 | 可逆 | 重写历史 | 物理变更 |
| --- | --- | --- | --- | --- | --- |
| 这条内容还应被召回吗？ | `forget` / `reactivate` | Memory entry | 是 | 否，新增 Revision | 否 |
| 这个 Artifact 还应被发现或发布吗？ | `deprecate` / `retire` | Experience 或 Skill head | 弃用可逆，退休不可逆 | 否 | 否 |
| 这个 Scope 还应出现在默认视图里吗？ | `archive` / `unarchive` | Scope | 是 | 否 | 否 |
| 这条记录是否已超过窗口？ | `purge` | 运行记录、无引用材料 | 否 | 否 | 是，删除行 |
| 这份内容必须不再存在吗？ | `erase` | 权威内容 | 否 | 否，tombstone 保留身份 | 是，移除内容 |

前三种操作只改变可见性，普通贡献者与审阅者可以通过现有领域 API 使用。后两种操作会变更存储，只能通过 Lifecycle Run 协议使用，而该协议在变更之前总是先 preview。

## 用记录类别思考

每一条存储的行都属于一个记录类别（record class）。类别决定哪些生命周期操作可以触及它。

| 记录类别 | 示例 | 生命周期操作 |
| --- | --- | --- |
| 权威内容 | Source payload、Artifact Revision 内容、Memory entry 文本、Candidate proposal、Skill 包字节 | 显式请求时 `erase`；仅当无引用且超出策略窗口时 `purge` |
| 身份与血缘 | 主键、血缘行、发布来源、journal position、内容 digest | 只要有任何行依赖它们就永不移除；它们正是 tombstone 所要保持有效的东西 |
| 可重建投影 | head 搜索文本、Memory entry head、全文与向量索引、进程内 Scope 组合 | 作为 `erase` 的一部分被清理；可从权威内容重建；永不作为权威 |
| 运行记录 | Source cursor、Connector checkpoint、发布期望状态、已撤销远端 target、日统计、已完成的 Lifecycle Run | 策略窗口过后 `purge` |
| 审计记录 | 生命周期审计行，以及 RFC 1396 实现后的 Access Audit | 至少保留到它们所解释的 tombstone 消失为止；只在自身窗口过后压缩为汇总 |

## 示例 1：遗忘并重新激活一条 Memory entry

这个流程没有任何变化。用户要求 Agent 不再记住某个偏好。Agent 携带精确的 `MemoryCitation` 调用 `retire_memory_entry`。PowerContext 提交一个新的 Memory Revision，其 manifest 把该条目标记为 `inactive` 并记录 `op="deactivate"`。条目文本、其版本以及所有更早的 Revision 都仍可通过精确引用读取。检索与 Prepare Context 不再返回该条目。

之后用户改变了主意。Agent 调用 `reactivate_memory_entry`，这是本 RFC 为现有 Runtime `reactivate()` 新增的公开对应操作。PowerContext 提交另一个 Revision，把同一 entry version 重新标记为 `active`。两个方向都不会创建内容版本。

逻辑遗忘永不触发清理或擦除。inactive 条目不会比 active 条目更有资格被擦除，而已擦除的条目无法重新激活，因为已经没有内容可以恢复。

## 示例 2：预览一次保留运行

操作员首次启用保留策略：

```yaml
lifecycle:
  policy_version: "2026-09"
  max_items_per_run: 1000
  retention:
    terminal_candidates_days: 90
    unreferenced_sources_days: 365
    unreachable_skill_packages_days: 30
    statistics_days: 400
    completed_runs_days: 365
  unreferenced_sources_by_type:
    handoff-receipt: 180
```

在删除任何东西之前，操作员先预览一个 Scope：

```text
powercontext lifecycle preview --scope ws_7f3a --action purge
```

```text
policy_version: 2026-09          effective_policy_digest: sha256:9b1c…
plan_digest: sha256:4e0f…        truncated: false

record_class  family / source_type   reason_code      disposition  count
operational   candidate               policy_expiry    selected     41
operational   candidate               policy_expiry    held         3     hold: lh_a91b
authoritative source: handoff-receipt unreferenced     selected     118
authoritative source: content         unreferenced     protected    2     referenced by lineage since selection
authoritative skill package           unreachable      selected     6
operational   statistics              policy_expiry    selected     212
```

对有界选择集而言，每个计数都是精确的。`held` 行标出排除它们的 Legal Hold。`protected` 行解释为什么一条匹配窗口的行仍然不符合条件。preview 不做任何变更，可以安全重复。

要执行，操作员携带 plan digest 提交同一请求：

```text
powercontext lifecycle apply --scope ws_7f3a --action purge --plan-digest sha256:4e0f…
```

Server 重新计算 plan。如果自 preview 以来任何被选中的身份发生了变化，它返回 `lifecycle_plan_stale`，操作员重新 preview。否则它创建一个 Lifecycle Run，分有界批次删除，并返回带最终 disposition 计数的运行回执。回执与审计行只包含身份和计数，绝不包含内容。

## 示例 3：擦除一条 Source 观察

捕获到某个 Scope 的一页 wiki 含有一个密钥。遗忘不够；payload 必须消失。管理员针对精确的 `SourceRef` 请求擦除预览：

```text
powercontext lifecycle preview --scope ws_7f3a --action erase \
  --source wiki_page:page-8841@obs-3 --derived retain
```

preview 返回影响报告：

```text
target      source wiki_page  identity_digest sha256:71aa…             disposition selected
derived     memory entry versions citing the Source                     2   policy retain
derived     experience revisions with the Source in lineage             1   policy retain
derived     handoff revisions citing the Source                         1   policy retain
copies      artifact publications of derived revisions in other scopes  0
holds       none
```

使用 `retain` 时，派生 Artifact 保留其内容。它们的血缘仍然指向该 Source，而对该 Source 的每条精确引用都解析为稳定的 `erased` 状态。使用 `invalidate` 时，PowerContext 额外在新 Revision 中退休派生的 Experience 与 Skill head，并遗忘派生的 Memory entry。使用 `cascade` 时，它同时擦除派生的 Revision 与 entry version，并且 preview 逐条列出它们，确保没有任何东西被静默级联。

apply 之后：

- 对该精确观察的 `get_source` 返回 `410 content_erased`，附带身份、`erased_at` 与 `run_id`。
- `pc_sources` 行仍然存在但 payload 为空，因此 Artifact 血缘行保持有效，Scope journal position 不变。
- 对引用过该 Source 的 Revision 执行 Handoff Continue 时，该证据被报告为 `unavailable`，原因为 `erased`；它绝不会替换为另一条观察。
- 搜索投影中不含任何由该 payload 派生的内容，并且运行的校验阶段会在运行完成前证明这一点。

## 示例 4：Legal hold

法务要求在一次审查结束前，某个 Scope 中不得删除任何东西。管理员创建一个 hold：

```text
powercontext lifecycle hold create --scope ws_7f3a --selector scope --reason legal --reference CASE-2026-14
```

从这一刻起，该 Scope 中的每次 preview 都把匹配项列为 `held` 并附带 hold 标识，每次 apply 都跳过它们。计划清理同样如此，并记录被跳过的计数。遗忘、治理与归档仍然可用，因为它们保留内容。hold 需显式释放，创建与释放都出现在生命周期审计中。

## 示例 5：归档一个已完成的 Scope

一个 Workstream 几个月前就结束了。它的 Handoff 与 Memory 应保持可读，但应不再出现在默认列表、Scope 选择、Context Reference 展开以及计划处理中。所有者将其归档：

```text
powercontext scope archive ws_7f3a --expected-version 12
```

精确读取继续工作。新的写入以 `scope_archived` 被拒绝，这样一个过期的集成绑定无法悄悄恢复一个已关闭的 Workstream。`unarchive` 以下一个 version 恢复该 Scope。归档不删除任何东西，也没有任何保留后果。

## 对现有部署的变化

默认情况下没有变化。新列均可为空，新表通过 Skill 分发所用的同一条可加性 schema 路径创建。现有操作保持名称与行为。只有在设置了策略窗口并且运行被 apply 或被调度时，清理才会执行。只有在被显式请求并通过 plan digest 批准时，擦除才会执行。

# Reference-level explanation

## Goals and non-goals

本 RFC 旨在：

- 原样保留现有的带 Revision 的 Memory `forget()` 与 `reactivate()` 语义，以及现有的 Artifact head 治理；
- 为 Memory entry、Experience 与 Skill head、Handoff Revision、Candidate、Source 观察、Skill 包、Scope 与远端 target 定义生命周期状态与允许的迁移；
- 定义按部署、Scope、记录类别、family 与 Source 类型分层的带版本 Lifecycle Policy；
- 固定 Legal Hold、管理员擦除、显式用户操作、外部系统删除证据与策略到期之间的优先级；
- 定义一个包含 preview、plan digest、授权、按 digest 批准、有界分批、幂等 apply、崩溃恢复、校验与无内容回执的 Lifecycle Run 协议；
- 定义在擦除后保持身份、血缘与引用完整性有效的原地 tombstone；
- 定义 Source 被擦除时派生 Artifact 如何被保留、失效或级联，并且总是附带影响报告；
- 定义遗忘、归档、清理与擦除之后的精确读取与引用行为；
- 定义全文、向量、head、缓存、包与远端 Receiver 副本的清理与校验；
- 定义哪些 append-only 记录可以压缩，哪些权威记录永不可重写；
- 定义一份脱敏的、append-only 的生命周期审计。

本 RFC 不定义：

- Scope 删除；对 `pc_scopes` 的每条引用都是 `RESTRICT`，Scope 模型属于 RFC 1345 与 issue #1219；
- Memory manifest 压缩或 Memory 大小上限；那是 issue #1321；
- 单个 Artifact head 上的 `archived` 状态；
- 自动重要性衰减、自动退休，或任何由排序、使用计数或相似度驱动的删除；
- 对存储 payload 的密码学粉碎（cryptographic shredding）；
- 召回在擦除之前已交付给 Agent、模型 Provider、导出文件或主机的内容；
- 授权实现；本 RFC 以 RFC 1396 的词汇命名动作，并定义该 RFC 实现之前的行为；
- Dashboard 编辑界面；首个版本通过 HTTP 与 CLI 暴露生命周期操作。

## Vocabulary

| 术语 | 含义 |
| --- | --- |
| Lifecycle action | `forget`、`reactivate`、`deprecate`、`retire`、`archive`、`unarchive`、`purge`、`erase` 之一 |
| Record class | `authoritative`、`identity`、`projection`、`operational` 或 `audit` |
| Lifecycle Policy | 一份带版本的保留窗口与擦除默认值文档；部署策略叠加可选的 Scope 覆盖 |
| Effective policy digest | 支配某次运行的规范化合并策略的 SHA-256 |
| Lifecycle Run | 在一个 Scope 中的一次有界 `purge` 或 `erase` 执行，只能从 plan digest 创建 |
| Plan | preview 计算出的有界、有序选择集，加上计数与影响报告 |
| Plan digest | 规范化的已选身份与 disposition 列表的 SHA-256；apply 要求 digest 相等 |
| Disposition | 一项为何被处理或不被处理：`selected`、`held`、`protected`、`blocked`、`skipped`、`erased`、`purged`、`forgotten`、`retired`、`remote_pending`、`verification_failed` |
| Reason code | 记录在审计中的稳定枚举：`policy_expiry`、`unreferenced`、`unreachable`、`user_request`、`external_deletion`、`administrator`、`legal`、`derived_invalidate`、`derived_cascade`、`declared_copy` |
| Legal Hold | 一个持久化的选择器，在释放之前把匹配项排除在 purge 与 erase 之外 |
| Tombstone | 内容列持有规范空值、并且已设置 `erased_at` 与 `erasure_run_id` 的行 |
| Declared copy | PowerContext 自身从某个精确 Revision 写到别处的内容：其他 Scope 中的发布副本、Skill 包，以及 Receiver 安装的包 |
| Derived Artifact | 其血缘或引用直接或传递地包含被擦除目标的 Revision 或 Memory entry version |

## 记录类别与存储

| 记录类别 | 存储 | 说明 |
| --- | --- | --- |
| Authoritative | `pc_sources.payload` | 捕获的观察值，最多 `MAX_SOURCE_OBSERVATION_BYTES` |
| Authoritative | `pc_artifacts.content` | 任意 family 的一个精确 Revision |
| Authoritative | `pc_memory_entry_versions.text` | 条目文本；`source_refs`、`artifact_refs` 与 `entry_content_hash` 属于身份 |
| Authoritative | `pc_artifact_candidate_versions.proposal`、`reason` | proposal 内容与自由文本 reason |
| Authoritative | `pc_skill_packages.archive_bytes`、`manifest` | 规范包；digest 与大小属于身份 |
| Identity | `pc_artifact_lineage_sources`、`pc_artifact_lineage_artifacts`、`pc_artifact_publications`、`pc_source_journal_heads`、所有主键 | 只要存在依赖行就永不擦除或清理 |
| Projection | `pc_artifact_heads.searchable_text`、`pc_memory_entry_heads`、SQLite 的 `pc_memory_entry_fts`、`pc_memory_vector_entries`、`pc_memory_entry_vec`、OceanBase 的 FULLTEXT 索引与向量表、Experience 与 Skill 搜索投影、进程内 Scope 组合 | 由现有 `rebuild_projections` 路径重建，且必须跳过 tombstone |
| Operational | `pc_source_cursors`、`pc_connector_checkpoints`、`pc_skill_publications`、状态为 `revoked` 的 `pc_agent_skill_targets`、`pc_scope_creation_requests`、`pc_model_usage_daily`、`pc_recall_token_daily`、已完成的 `pc_lifecycle_runs`、发布器暂存目录 | 按窗口清理；永不被引用 |
| Audit | `pc_lifecycle_audit` | append-only；见"生命周期审计" |

Handoff Receipt、Work Contract、Task Outcome 与 Skill 使用捕获是专用类型的 Source，遵循 Source 规则。它们的保留窗口可以按 Source 类型设置。

## 按 family 的生命周期状态与迁移

现有状态机不变。本 RFC 只新增权威行上正交的内容状态 `erased`、Scope 状态 `archived`，以及终态运行记录的清理资格。

```text
Memory entry (RFC 0014)          active <-> inactive           forget / reactivate，每次新增 Revision
Experience 或 Skill head (1351)  active <-> deprecated         governance_generation 上的 CAS
                                 active | deprecated -> retired   不可逆
Candidate (RFC 0050)             pending -> approved | rejected   终态
Source head (RFC 1400)           active | deleted              目录状态，本 RFC 不改
Remote target (RFC 1351)         pending -> active -> revoked
Scope（本 RFC）                   active <-> archived           scope version 上的 CAS
权威行（本 RFC）                   retained -> erased            不可逆，与上述状态正交
```

各 family 规则：

- **Memory。** entry version 是唯一的 Memory 擦除目标。Memory Revision 是一份 manifest，永不擦除；擦除它会破坏之后的每一份 manifest。擦除一个在当前 head 中为 `active` 的 entry version 时，先以 reason `lifecycle_erasure` 提交现有的 forget 路径，这样每份 head manifest 都与"active 条目必有内容"的规则保持一致。已擦除的条目无法重新激活。本 RFC 不定义 Memory head 治理。
- **Experience 与 Skill。** head 治理通过一个通用操作向 `family = 'experience'` 开放，使用同样的三态、同样的迁移与同样的 CAS。擦除 head Revision 不改变 `lifecycle_state`；head 读取报告 erased 状态，该 Revision 的 Skill 发布收敛到 `unpublished`。
- **Handoff。** 已提交的 Revision 保持不可变。没有 Handoff head 治理。擦除以一个精确 Revision 为目标。当被擦除的 Revision 是已提交 head 时，`latest` 返回 erased 状态，绝不解析到更早的 Revision；提交一个新 Handoff 才是前进的方式。
- **Candidate。** 终态 Candidate 在 `terminal_candidates_days` 之后可被清理。pending Candidate 永不按年龄清理。显式擦除一个 pending Candidate version 时，先以 `decision_reason = "lifecycle_erasure"` 将其拒绝，再对该 version 打 tombstone，从而满足 head 的 CHECK 约束。
- **Source。** 一条观察处于 `retained` 或 `erased`。Connector 的正向删除证据按 RFC 1400 只改变 head；只有在设置了 `external_deletion.erase_after_days` 时它才成为擦除候选，而该候选仍然需要一次已批准的运行。
- **Skill 包。** 只要任何未擦除的 Skill Revision 内容、Candidate proposal 或 skill-package Source 引用其 `tree_digest`，包就是 `reachable`。不可达的包在 `unreachable_skill_packages_days` 之后可被清理。擦除最后一个引用某个包的未擦除 Revision 时，同一运行中对包字节打 tombstone，因为包是该 Revision 的 declared copy。
- **Scope。** `archive` 设置 `archived_at` 并递增 `version`；`unarchive` 将其清空。归档以单个 Scope 为单位；归档一个子树是客户端对后代的循环。
- **Remote target。** 已撤销的 target 在 `revoked_targets_days` 之后可被清理；RFC 1351 已保证它们不持有可用凭据。

## 引用图、保护与可达性

当另一条权威行或身份行依赖某行时，该行被视为**被引用**：

- Source 观察被任何 `pc_artifact_lineage_sources` 行引用，也被任何 `source_refs` 包含它的 Memory entry version 引用；
- Artifact Revision 被任何 `pc_artifact_lineage_artifacts` 行、任何以它为 source 或 target 的 `pc_artifact_publications` 行、任何以它为目标的 Candidate version、任何 `artifact_refs` 包含它的 Memory entry version 引用，并且在它是其逻辑 Artifact 的 head 期间也被视为被引用；
- Skill 包按上文定义被引用。

通用血缘是主要的引用索引。Memory 与 Handoff 在提交 Revision 时已经把它们引用的每个 Source 与 Artifact 记入通用血缘。每个提交 Revision 的 family 都必须保持这一性质，一致性测试套件会针对每个 family 验证其引用是其血缘的子集。因此实现可以从 `pc_artifact_lineage_sources` 与 `pc_artifact_lineage_artifacts` 计算可达性，并且必须额外查询 Memory entry 的 `source_refs` 与 `artifact_refs` 以获得条目级派生项。

被引用的权威内容受到**保护**，不会被清理。策略到期永不删除它。只有 `erase` 可以移除它，并且只能通过一次 plan 中列出了它的已批准运行。

可达性在 apply 事务内对每一项计算，而不只是在 preview 时计算。在 preview 与 apply 之间变为被引用的项被记录为 `skipped`，原因为 `protected`。

## Lifecycle Policy

部署策略是配置。Scope 覆盖是一份同形状的持久化文档；只有它设置的键会覆盖部署值。

```yaml
lifecycle:
  policy_version: "2026-09"            # 操作员标签；设置任何窗口或 auto_apply 时必填
  max_items_per_run: 1000              # 每次运行的有界选择集，1..10000
  auto_apply: false                    # 仅限计划 purge；erase 永不自动 apply
  schedule_seconds: null               # auto_apply 为 true 时必填
  retention:
    terminal_candidates_days: null     # 已批准与已拒绝的 Candidate head 与 version
    unreferenced_sources_days: null    # 无引用的 Source 观察
    unreachable_skill_packages_days: null
    statistics_days: null              # pc_model_usage_daily、pc_recall_token_daily
    revoked_targets_days: null
    completed_runs_days: 365           # 已完成、失败与已取消的 Lifecycle Run
    audit_days: null                   # purge 审计压缩；永不低于任何其他窗口
  unreferenced_sources_by_type: {}     # source_type -> days；覆盖 unreferenced_sources_days
  external_deletion:
    erase_after_days: null             # 正向删除证据 -> 擦除候选
  source_erasure_derived_artifacts: retain   # retain | invalidate | cascade
```

`null` 表示永久保留。校验会拒绝：设置了任何窗口却没有 `policy_version`；设置了 `auto_apply` 却没有 `schedule_seconds`；`audit_days` 低于其他任一窗口。Scope 覆盖以其 `generation` 做 CAS 写入，并记录自己的 `policy_version`。

一次运行的有效策略是部署策略叠加 Scope 覆盖。其规范化 JSON 的 digest 即 effective policy digest。每次运行、回执与审计行都记录 `policy_version` 与 effective policy digest，因此即使配置之后发生变化，操作员也能证明是哪个策略授权了哪次变更。

窗口按可信的 Server 时间与记录自身的时间戳评估：包、运行、target、Source 与 Revision 用 `created_at`，统计用 `usage_date`，Candidate 用 `decided_at`。今天缺少该时间戳的表，由实现新增一个写入时填充的可空列；没有时间戳的行永不按年龄符合条件。

## 优先级

当多条规则作用于同一项时，以下第一条匹配的规则胜出：

1. **Legal Hold。** 一个匹配的活跃 hold 将该项排除在 `purge` 与 `erase` 之外，无论请求者、策略或外部证据如何。hold 永不阻止 `forget`、`reactivate`、治理或归档，因为它们保留内容。
2. **管理员擦除。** 一次已批准的 erase 运行移除内容，无论保留窗口与该项的可见性状态如何。它不能绕过 hold；必须先释放 hold，且释放会被审计。
3. **显式用户操作。** `forget`、`reactivate`、`deprecate`、`retire`、`archive` 与 `unarchive` 只改变可见性。它们永不使某项具备清理资格，也永不移除内容。
4. **外部系统删除证据。** 正向删除证据更新 Source head。在设置了 `external_deletion.erase_after_days` 时，该观察在窗口过后成为擦除候选，并以原因 `external_deletion` 出现在 erase preview 中。没有已批准的运行它永不被擦除。
5. **策略到期。** 窗口只驱动 `purge`。purge 永不移除被引用的权威内容，也永不创建 tombstone。

## Legal Hold

```text
pc_lifecycle_holds
  hold_id            PK，不透明身份
  scope_id           FK pc_scopes RESTRICT
  selector_kind      scope | source_type | family | source | artifact | memory_entry
  selector           仅含身份的 canonical payload
  reason_code        legal | administrator | user_request
  reference          可空，<= 128 字符，不透明的工单或案件引用
  created_at, created_by（可空的不透明 principal）
  released_at, released_by（可空）
  generation         CAS
```

hold 在 `released_at` 为空时处于活跃状态。当选择器覆盖某项时该项匹配该 hold：整个 Scope、某一类型的全部 Source、某一 family 的全部 Artifact，或一个精确的 Source、Revision 或 entry version。对某个 Revision 的 hold 同时覆盖其 declared copy 与其包。

效果：

- `preview` 把匹配项列为 disposition `held` 并附带 hold 标识，同时报告按 hold 的计数；
- `apply` 与计划 purge 跳过匹配项，将其计为 `held`，并正常完成；
- 显式的 `targets` 选择器若指名一个被 hold 的项，在计算任何 plan 之前就以 `legal_hold_active` 被拒绝，而策略选择只是跳过被 hold 的项；释放 hold 是一次单独的、被审计的操作；
- 创建或释放 hold 写入一条审计行，永不触及内容或投影。

## Lifecycle Run 协议

### 请求

```text
LifecycleRunRequest
  scope_id
  action              purge | erase
  selection
    policy            {}                       按有效策略窗口与外部删除候选选择
    targets           [Selector...]            显式的 erase 目标
  derived_policy      retain | invalidate | cascade   仅 erase；默认取自策略
  reason_code         administrator | user_request | external_deletion | legal | policy_expiry
  reference           可空的不透明引用
  plan_digest         apply 必填，preview 不带

Selector
  source              SourceRef
  artifact            ArtifactRef                    除 memory 外的任意 family
  memory_entry        MemoryCitation
  memory_artifact     artifact_id                    某个 Memory 的全部 entry version
  scope               {}                             该 Scope 中所有可擦除的权威行
```

Memory Revision、Scope、血缘行或仅含身份的行都不是合法的 erase 目标；请求以 `lifecycle_target_invalid` 失败，且不计算任何 plan。

### Preview 与 plan

preview 是无状态且幂等的。它在一个读事务中计算：

1. 选择集的候选集合，按 `max_items_per_run` 有界，以确定顺序排列（Source 按 journal position，然后按 family、Artifact 身份、revision、entry version）；
2. 每个候选的 disposition：`selected`、`held`、`protected`，或当该项位于调用者无权变更的 Scope 中时为 `blocked`；
3. 对 `erase`，影响报告：派生的 Memory entry version、通过传递血缘闭包按 family 得到的派生 Revision、其他 Scope 中的 declared copy，以及将变为不可达的包，每项都带有所选 `derived_policy` 所隐含的 disposition；
4. 按记录类别、family 或 Source 类型、reason code、disposition 与 hold 分组的精确计数；
5. 当候选集合超出上界时 `truncated = true`。

```text
LifecycleRunPlan
  scope_id, action, reason_code
  policy_version, effective_policy_digest
  plan_digest
  truncated
  items[]      {selector_kind, identity, record_class, reason_code, disposition, hold_id?}   有界
  counts[]     {record_class, family?, source_type?, reason_code, disposition, hold_id?, count}
  impact       {derived_policy, derived[], declared_copies[], packages[]}
```

`plan_digest` 是对每一项（包括派生项与 declared copy）的规范化有序 `(selector_kind, identity, disposition)` 列表计算的 SHA-256。对未变化数据的两次 preview 产生相等的 digest。

### Apply、分批与恢复

`apply` 按同样的规则重新计算 plan 并比较 digest。不匹配返回 `409 lifecycle_plan_stale` 且不写入任何东西。digest 相等时创建一次运行：

```text
pc_lifecycle_runs
  run_id                  PK，不透明身份
  scope_id                FK pc_scopes RESTRICT
  action                  purge | erase
  state                   running | completed | completed_with_skips | verification_failed | failed | cancelled
  policy_version, effective_policy_digest, plan_digest
  reason_code, reference（可空）
  plan                    plan 项的 canonical payload，仅含身份
  cursor                  可空的 canonical payload：下一个未处理项的索引
  counts                  disposition 计数的 canonical payload
  requested_by            可空的不透明 principal
  lease_until             可空，恢复时的围栏
  error_code              可空
  created_at, updated_at, completed_at（可空）
```

执行规则：

- 各项按 plan 顺序分批处理；每一批是一个数据库事务，同时完成该项的变更、其投影清理、审计行与 cursor 推进；
- 每一项都在其批次内重新校验：仍然存在、仍然符合条件、未被 hold、尚未擦除；重新校验失败的项被记录为 `skipped` 并附原因，运行继续；
- 在运行处于 `running` 期间，携带同一 plan digest 的第二次 `apply` 返回该运行；运行完成后则返回 `409 lifecycle_run_conflict` 并附已完成运行的身份，因为该 plan 已不可能被重新计算为相等；
- 在 `running` 状态下租约过期的运行由 `resume_lifecycle_run` 或调度器恢复；恢复时重新读取 `cursor` 并继续，因此批次之间的崩溃既不丢失也不重复任何项；
- `cancel_lifecycle_run` 在批次之间停止运行；已完成的批次保持已应用并被审计；
- 对 `erase`，其他 Scope 中的 declared copy 在同一运行内、同一授权决定下被擦除；若任一副本所在 Scope 未获授权，`preview` 把这些项报告为 `blocked`，`apply` 在创建运行之前以 `lifecycle_forbidden_scope` 拒绝整个 plan。

### 校验与回执

最后一批之后，`erase` 运行校验每个已擦除身份：没有 `pc_memory_entry_heads` 行、没有全文行、没有向量元数据或向量行、已擦除 head Revision 的 `searchable_text` 为空，且 tombstone 标记存在。任何残留投影都使运行进入 `verification_failed`，并把该身份记入审计。补救手段是现有的 `rebuild_projections`（它从权威行重建并且必须跳过 tombstone），随后执行 `verify_lifecycle_run`。远端副本通过 Receiver 观察校验：在每个 target 报告该精确包已 unpublished 之前，运行把它们计为 `remote_pending`，回执只以 `target_id` 列出待处理的 target。

回执就是 `get_lifecycle_run` 返回的运行行本身：状态、digest、disposition 计数、待处理远端 target 与时间戳。它不含任何内容、payload、文本或自由文本 reason。

### 调度

设置了 `auto_apply` 与 `schedule_seconds` 时，Runtime 调度器对每个未归档与已归档的 Scope 运行一次策略选择的 `purge`，每个 Scope 以 `max_items_per_run` 为界，使用同一条 preview 后 apply 的路径与内部计算的 digest。调度器永不运行 `erase`。外部删除候选会出现在 erase preview 中，但始终需要显式 apply。

## Tombstone 契约

擦除永不删除有其他行依赖的权威行。它把该行转换为 tombstone：

```text
tombstone(row):
  内容列               := 规范空值（canonical payload 列为零长度字节，文本列为 ""）
  erased_at            := 可信的 Server 时间
  erasure_run_id       := run_id
  其他所有列            不变
```

| 表 | 清空的内容列 | 保留的列 |
| --- | --- | --- |
| `pc_sources` | `payload` | `scope_id`、`source_type`、`source_id`、`journal_position` |
| `pc_artifacts` | `content` | 身份、`revision` |
| `pc_memory_entry_versions` | `text` | 身份、`version`、`previous_version_id`、`kind`、`source_refs`、`artifact_refs`、`entry_content_hash`、`created_in_revision` |
| `pc_artifact_candidate_versions` | `proposal`、`reason` | 身份、`family`、`source_refs`、`artifact_refs`、target 列 |
| `pc_skill_packages` | `archive_bytes`、`manifest` | `tree_digest`、`archive_digest`、`file_count`、各大小、`created_at` |

读取方在解码任何内容列之前检查 `erased_at`，并且永不解码 tombstone。`NOT NULL` 约束、主键、外键与 CHECK 约束全部继续成立，因为没有列被设为 `NULL`，也没有大小或计数列发生变化。tombstone 不可逆；不存在取消擦除。

`entry_content_hash`、`content_digest`、`tree_digest` 与 `archive_digest` 得以保留，因为 manifest、发布与 Receiver 通过它们校验身份。已擦除内容的 digest 属于身份而非内容；Drawbacks 一节记录了极短文本的残余风险。

tombstone 行是否被引用，遵循与保留行相同的规则。无引用的 tombstone 在与无引用保留行相同的窗口下可被清理，因此除非仍有东西引用它们，tombstone 不会永远累积。

## 按目标的擦除过程

每个过程都运行在同时写入审计行并推进运行 cursor 的批次事务内。

### Memory entry version

1. 如果该 entry version 在当前 head manifest 中为 `active`，以 reason `lifecycle_erasure` 针对当前 head 提交现有的 forget 路径。这是一个 `op="deactivate"` 的普通 Revision；head CAS 冲突时批次针对新 head 重试。RFC 0014 已要求 forget 路径删除 head 投影行及其全文与向量行。
2. 对 `pc_memory_entry_versions` 行打 tombstone。
3. 删除该 version 残留的任何 `pc_memory_entry_heads`、全文、向量元数据与向量行，然后校验不存在。
4. 之后的 manifest 继续引用 `entry_version_id` 与 `entry_content_hash`。除非设置 `include_erased`，`entries()` 与 `list_memory_entries` 排除已擦除的 version；设置时该 version 以 `erased_at`、空文本与其身份出现。对已擦除 version 的 `get_memory_entry` 与 `validate_citation` 报告 erased 状态。

`memory_artifact` 选择器展开为该 Memory 的全部 entry version，并逐个 version 执行同样的步骤。Memory Revision 本身永不被触及。

### Artifact Revision

1. 对该精确 Revision 的 `pc_artifacts` 行打 tombstone。
2. 如果该 Revision 是 head，将 `pc_artifact_heads.searchable_text` 设为 `NULL` 并移除 Experience 或 Skill 搜索投影。`lifecycle_state`、`replacement_artifact_id` 与 `governance_generation` 不变。`latest` 与 head 列表报告 erased 状态，绝不回退到更早的 Revision。
3. 对 Skill Revision，把发布该精确 Revision 的每一条 `pc_skill_publications` 行设为 `desired_state = unpublished`。主机本地发布器移除已安装的包；远端 Receiver 收敛并报告观察结果。在观察到之前，运行把每个 target 计为 `remote_pending`。
4. 对 Skill Revision，当没有其他未擦除的 Revision、Candidate version 或 Source 引用其 `tree_digest` 时，对所引用的 `pc_skill_packages` 行打 tombstone。
5. 擦除每个 declared copy：每条以该 Revision 为 source 的 `pc_artifact_publications` 行都指向另一个 Scope 中 `content_digest` 相等的 target Revision；该 target 以同样的过程被擦除，其自身的副本亦传递擦除。
6. Handoff Continue、Resolve 与 Handoff Report 把已擦除的 Handoff Revision 视为不可用内容；报告 schema 的变化属于 RFC 0082。

### Source 观察与派生 Artifact

1. 对 `pc_sources` 行打 tombstone。journal position 与每一条血缘行保留。
2. 计算派生集合：`pc_artifact_lineage_sources` 中包含该 Source 的 Revision、通过 `pc_artifact_lineage_artifacts` 位于其下游的 Revision，以及 `source_refs` 包含该 Source 的 Memory entry version。该集合在 preview 时计算以纳入 plan digest，并在批次内重新校验。
3. 应用 `derived_policy`：
   - `retain`：派生行不变。它们的血缘保留精确的 `SourceRef`；对该 Source 的每条精确引用解析为 `erased`。
   - `invalidate`：额外通过治理 CAS 把 head Revision 属于派生集合的 Experience 与 Skill head 迁移到 `retired`，并在新 Revision 中以 reason `source_erased` 遗忘派生的 active Memory entry。Handoff Revision 不可变且保持不变；其证据检查报告 `erased`。
   - `cascade`：额外按上述过程擦除每个派生 Revision 与 entry version，包括它们的 declared copy 与包。
4. RFC 1400 定义的 Source head 不变；擦除不是删除证据。

`invalidate` 与 `cascade` 创建的普通 Revision 或 tombstone 出现在 plan 中，并以原因 `derived_invalidate` 或 `derived_cascade` 出现在审计中。除非批准该运行的 plan digest 包含了某个派生项，否则它永不被改变。

### Scope 范围擦除

`scope` 选择器按 journal 与身份顺序选中该 Scope 中的每一条权威行，以 `max_items_per_run` 为界，`truncated = true` 直到某次 preview 不再返回任何可选项。每次运行都让 Scope 处于一致状态：每个已擦除 Revision 都是 tombstone，head 仍可解析，血缘完整，Memory manifest 仍然有效。Scope 范围擦除不归档也不删除 Scope；操作员可以在之后归档它。

## 保留清理类别

purge 删除行。它只允许作用于没有任何东西引用、且已超出其窗口的行。

| 类别 | 符合条件的行 | 删除 |
| --- | --- | --- |
| 终态 Candidate | 处于 `approved` 或 `rejected` 的 head 及其 version，决定时间早于 `terminal_candidates_days` | 先删除 head 行，再删除 version 行 |
| 无引用 Source | 无任何引用的观察（保留或已 tombstone），提交时间早于按类型或默认的窗口 | 删除该行；`pc_source_journal_heads.position` 不变 |
| 不可达 Skill 包 | 没有任何未擦除 Revision、Candidate version 或 Source 引用的包，`created_at` 早于窗口 | 删除该行 |
| 统计 | `usage_date` 早于 `statistics_days` 的 `pc_model_usage_daily` 与 `pc_recall_token_daily` 行 | 删除这些行 |
| 已撤销远端 target | 状态为 `revoked` 且 `updated_at` 早于窗口的 `pc_agent_skill_targets` | 删除该行及其发布行 |
| 已完成运行 | 处于终态且 `completed_at` 早于 `completed_runs_days` 的 `pc_lifecycle_runs` | 删除该行；审计行保留 |

cursor 与 Connector checkpoint 永不按年龄清理，因为它们是活跃绑定的恢复状态。移除绑定会移除它们，这是现有行为。

## 投影、缓存与副本清理

每个批次事务内的清理顺序：

1. 权威 tombstone 或行删除；
2. head 投影：`pc_memory_entry_heads` 行、`pc_artifact_heads.searchable_text`；
3. 全文行：SQLite 中按身份删除 `pc_memory_entry_fts` 行；OceanBase 中 FULLTEXT 索引随 head 行一起变化；
4. 向量行：SQLite 的 `pc_memory_vector_entries` 元数据与 `pc_memory_entry_vec` 行；OceanBase 的 `pc_memory_vector_entries` 行；
5. 审计行与运行 cursor。

批次提交之后：

6. Runtime 驱逐该 Scope 的进程内组合与锁，即现有的 `evict` 路径；
7. 发布期望状态的变化已经持久化；发布器与 Receiver 异步收敛，并在 reconcile 时移除暂存目录；
8. 校验阶段回读每个已擦除身份的每个投影。

Memory、Experience 与 Skill 的 `rebuild_projections` 必须跳过 tombstone，并且不得因它们失败。因此擦除之后的重建是一种合法的补救手段，且永不可能让已擦除文本复活。PreparedContext 与 Context Pack 不持久化，因而无需清理。客户端缓存、Agent 对话记录与模型 Provider 日志位于 PowerContext 之外，也在本 RFC 范围之外。

## 生命周期操作之后的读取与引用行为

| 目标状态 | 精确读取 | `latest` 或 head | 列表 | 搜索与召回 | Handoff 证据检查 |
| --- | --- | --- | --- | --- | --- |
| 已遗忘的 Memory entry | 可读 | head 排除它 | 仅在 `include_inactive` 时包含 | 排除 | `available` |
| 已弃用的 head | 可读 | 可读 | 包含并标记 | 仅在请求时包含 | `available` |
| 已退休的 head | 可读 | 可读 | 默认排除 | 排除 | `available` |
| 已归档的 Scope | 可读 | 可读 | Scope 默认排除 | 从 Context Reference 展开中排除 | `available` |
| 已清理的行 | 未找到 | 不适用 | 不存在 | 不存在 | `unavailable`，原因 `missing` |
| 已擦除的行 | `410 content_erased` | head Revision 已擦除时为 `410 content_erased` | 仅在 `include_erased` 时给出身份与 `erased_at` | 不存在 | `unavailable`，原因 `erased` |

`410` 响应体携带身份、`erased_at` 与 `run_id`，别无其他。精确引用永不解析到另一个 Revision、entry version、观察或 `latest`。`HandoffEvidenceCheck` 新增 `unavailable_reason`，取值 `erased` 与 `missing`；当策略拒绝某条引用时，RFC 1396 可以增加 `denied`。只读取 `status` 的现有消费者不受影响。

## Scope 归档

`pc_scopes` 新增可空的 `archived_at`。`archive_scope` 与 `unarchive_scope` 接受 `scope_id` 与 `expected_version`，更新 `archived_at`，递增 `version`，并返回描述符。version 冲突返回现有的 scope 冲突错误。

已归档的 Scope：

- 除非设置 `include_archived`，从 `list_scopes`、`resolve_scope_selection`、Dashboard 聚合与 Handoff Report 候选检测中排除；
- 当它作为另一个 Scope 的 Context Reference 出现时，被 Prepare Context 跳过；
- 被计划的 Source flush 与 Experience 孵化跳过；
- 以 `409 scope_archived` 拒绝写入：Source 捕获、`remember`、Handoff 提交、Candidate 创建、向该 Scope 的发布，以及远端 enrollment；
- 保持每一种读取、精确解析、`get_scope`、生命周期 preview、apply 与 hold 操作可用；
- 其子 Scope 不变；归档不会继承；
- `resolve_scope_binding` 报告其已归档，这样集成可以提示创建新 Workstream，而不是写入一个已关闭的 Workstream。超出该标志之外的绑定行为属于 RFC 1345。

归档不删除任何东西，不创建 tombstone，也不改变任何保留资格。

## Append-only 记录与压缩

- **Source journal。** journal position 永不复用或重新编号，`pc_source_journal_heads.position` 永不减小。purge 留下空洞。Source window、flush、cursor 与 `entries()` 必须容忍空洞；一致性测试套件对此验证。
- **生命周期审计。** 审计是 append-only 的。解释某个现存 tombstone 的行永不被清理。关于 purge、hold 与 skip 的行可以在 `audit_days` 之后被压缩为每个 Scope、action、记录类别、reason code 与 disposition 每日一条带计数的汇总行；压缩本身写入一条审计行。
- **Artifact 权威。** `pc_artifacts` 行永不重写或重新编号，Memory manifest 不由本 RFC 压缩，血缘行在两端任一方存在期间永不删除。
- **相邻模块。** RFC 0082 的 Activity Event 保留与 purge 仍是该模块自己的契约，且必须遵循同样的数据最小化规则。

## 生命周期审计

```text
pc_lifecycle_audit
  audit_id                 PK，单调
  scope_id                 FK pc_scopes RESTRICT
  run_id, hold_id          可空
  recorded_at
  action                   purge | erase | hold_create | hold_release | policy_set | archive | unarchive | compaction | verification
  record_class
  family, source_type      可空
  identity                 可空的不透明身份 canonical payload；Source 目标为 NULL
  identity_digest          含 scope_id 的规范身份元组的 SHA-256
  reason_code
  disposition
  policy_version, effective_policy_digest   可空
  principal_id             可空的不透明标识
```

审计包含身份、digest、计数、代码与时间戳。它永不包含 payload、Artifact 内容、Memory 文本、Candidate proposal、包字节、自由文本 reason、可能嵌入路径或 URL 的 Source 标识、凭据、主机路径或原始错误。Source 目标只以 `source_type` 与 `identity_digest` 记录；Artifact 与 Memory 目标以其不透明的生成标识记录。运行回执、日志、指标、trace 与错误响应体遵循同一边界，即 RFC 0046 与 RFC 1396 已经定义的边界。

## 持久化与迁移

新表：`pc_lifecycle_runs`、`pc_lifecycle_holds`、`pc_lifecycle_audit` 与 `pc_lifecycle_scope_policies`。

```text
pc_lifecycle_scope_policies
  scope_id          PK，FK pc_scopes RESTRICT
  policy            覆盖文档的 canonical payload
  policy_version
  generation        CAS
  updated_at
```

新的可空列：`pc_sources`、`pc_artifacts`、`pc_memory_entry_versions`、`pc_artifact_candidate_versions` 与 `pc_skill_packages` 上的 `erased_at` 与 `erasure_run_id`；`pc_scopes` 上的 `archived_at`；`pc_sources` 与 `pc_artifacts` 上的 `created_at`；以及 `pc_artifact_candidate_heads` 上的 `decided_at`，在 Candidate 进入终态时设置。

迁移遵循现有的可加性路径：新表通过共享 metadata 中的 `create_all(checkfirst=True)` 创建，新列通过幂等的 `ensure_lifecycle_schema(connection)` 按方言发出 `ALTER TABLE ... ADD COLUMN`，并在组合时紧邻 `ensure_skill_distribution_schema` 调用。不改 CHECK 约束，不重建表，不重写数据。现有行在每个新列上都为 `NULL`，因此处于保留、未归档状态，并且在存在时间戳之前不按年龄符合条件。

SQLite 与 OceanBase 共享表定义与契约测试。全文与向量清理按后端不同，正如它们的投影本来就不同。

擦除之后降级对 schema 是安全的，对 tombstone 的读取则不安全：旧代码解码空 payload 时会以现有的 invalid-stored-payload 错误失败。操作员文档必须说明这一点。

## Runtime 与服务边界

Runtime 在现有领域应用旁暴露生命周期操作：

```python
class LifecycleApplication(Protocol):
    def for_scope(self, scope_id: str, /) -> ScopedLifecycle: ...


class ScopedLifecycle(Protocol):
    async def preview(self, request: LifecycleRunRequest, /) -> LifecycleRunPlan: ...
    async def apply(self, request: LifecycleRunRequest, /) -> LifecycleRun: ...
    async def resume(self, run_id: str, /) -> LifecycleRun: ...
    async def verify(self, run_id: str, /) -> LifecycleRun: ...
    async def cancel(self, run_id: str, /) -> LifecycleRun: ...
    async def get_run(self, run_id: str, /) -> LifecycleRun: ...
    async def list_runs(self, *, cursor: str | None = None, limit: int = 50) -> LifecycleRunPage: ...
    async def create_hold(self, request: CreateLegalHold, /) -> LegalHold: ...
    async def release_hold(self, hold_id: str, expected_generation: int, /) -> LegalHold: ...
    async def list_holds(self, *, include_released: bool = False) -> tuple[LegalHold, ...]: ...
    async def effective_policy(self) -> EffectiveLifecyclePolicy: ...
    async def set_policy_override(self, request: SetScopeLifecyclePolicy, /) -> EffectiveLifecyclePolicy: ...
    async def list_audit(self, *, cursor: str | None = None, limit: int = 100) -> LifecycleAuditPage: ...
```

tombstone 感知位于持久化 repository 中，而不在调用方。Source、Artifact、Memory、Candidate 与包 repository 抛出来自 `powercontext.errors` 的类型化 `ContentErasedError`，它同时是 `PowerContextError` 与 `LookupError`，并带有按 family 的子类。family 服务把它翻译为各自的领域结果：Handoff 证据检查产生原因为 `erased` 的 `unavailable`，Memory `validate_citation` 抛出它，Server 将其映射为 `410 content_erased`。

`MemoryEntryVersion` 新增可选的 `erased_at`。`ArtifactCatalog.revisions()` 排除已擦除的 Revision；对已擦除 Revision 的 `get()` 抛出该类型化错误；已擦除的身份可通过带 `include_erased` 的列表与审计发现。

## 公开 API

`openapi/powercontext.yaml` 仍是唯一事实来源；生成的客户端用 `make api-generate` 重新生成。所有操作与现有的 Scope、Memory 与 Skill 操作一样，使用 `POST`、JSON 请求体与显式 `scope_id`。

| 操作 | 路径 | 目的 | 动作 |
| --- | --- | --- | --- |
| `preview_lifecycle_run` | `/v1/lifecycle/runs/preview` | 计算 plan，不做变更 | `lifecycle.preview` |
| `apply_lifecycle_run` | `/v1/lifecycle/runs/apply` | 从 plan digest 创建并启动运行 | `lifecycle.apply` |
| `resume_lifecycle_run` | `/v1/lifecycle/runs/resume` | 继续一个租约已过期的运行 | `lifecycle.apply` |
| `verify_lifecycle_run` | `/v1/lifecycle/runs/verify` | 补救后重新校验 | `lifecycle.apply` |
| `cancel_lifecycle_run` | `/v1/lifecycle/runs/cancel` | 在批次之间停止 | `lifecycle.apply` |
| `get_lifecycle_run` | `/v1/lifecycle/runs/get` | 读取运行回执 | `lifecycle.preview` |
| `list_lifecycle_runs` | `/v1/lifecycle/runs/list` | 分页列出 Scope 中的运行 | `lifecycle.preview` |
| `create_legal_hold` | `/v1/lifecycle/holds/create` | 创建 hold | `lifecycle.hold` |
| `release_legal_hold` | `/v1/lifecycle/holds/release` | 以 CAS 释放 hold | `lifecycle.hold` |
| `list_legal_holds` | `/v1/lifecycle/holds/list` | 列出 hold | `lifecycle.preview` |
| `get_lifecycle_policy` | `/v1/lifecycle/policy/get` | 某个 Scope 的有效策略与 digest | `lifecycle.preview` |
| `set_scope_lifecycle_policy` | `/v1/lifecycle/policy/set` | 以 CAS 更新 Scope 覆盖 | `lifecycle.policy` |
| `list_lifecycle_audit` | `/v1/lifecycle/audit/list` | 分页读取审计行 | `lifecycle.audit.read` |
| `archive_scope` | `/v1/scopes/archive` | 以 CAS 归档 | `scope.archive` |
| `unarchive_scope` | `/v1/scopes/unarchive` | 以 CAS 取消归档 | `scope.archive` |
| `reactivate_memory_entry` | `/v1/memory/entries/reactivate` | `retire_memory_entry` 的公开对应操作 | 与 `retire_memory_entry` 相同 |
| `update_artifact_lifecycle` | `/v1/artifacts/lifecycle/update` | `experience` 与 `skill` 的 head 治理 | 与 `update_skill_lifecycle` 相同 |

`retire_memory_entry`、`update_skill_lifecycle` 与每一个现有读取都保持其契约。对已擦除内容的精确读取返回 `410`；列表响应新增可选的 `erased_at` 与 `include_erased`；Scope 响应新增可选的 `archived_at`。

| 错误码 | 状态码 | 含义 |
| --- | --- | --- |
| `content_erased` | 410 | 精确目标是 tombstone；响应体携带身份、`erased_at`、`run_id` |
| `lifecycle_plan_stale` | 409 | 重新计算的 plan digest 与提交的不同 |
| `lifecycle_run_conflict` | 409 | 该 plan 的运行已经完成，或运行不处于允许该操作的状态 |
| `legal_hold_active` | 409 | 显式 erase 目标处于活跃 hold 之下 |
| `scope_archived` | 409 | 试图向已归档 Scope 写入 |
| `lifecycle_forbidden_scope` | 403 | plan 包含调用者无权变更的 Scope 中的 declared copy |
| `lifecycle_target_invalid` | 422 | 选择器不是可擦除目标 |
| `lifecycle_policy_invalid` | 422 | 策略文档未通过校验 |

CLI 新增 `powercontext lifecycle preview|apply|resume|verify|cancel|runs|run|hold create|hold release|hold list|policy show|policy set|audit`、`powercontext scope archive|unarchive` 与 `powercontext memory reactivate`。首个版本中 MCP 不暴露生命周期变更操作。Dashboard 必须把已擦除与已归档的项渲染为不可用且不含内容；编辑界面是未来可能性。

## 授权

动作使用 RFC 1396 的词汇，授予该 Scope 的 `scope.admin` 与覆盖所有 Scope 的 `server.admin`：

| 动作 | 含义 |
| --- | --- |
| `lifecycle.preview` | 预览 plan，读取运行、hold 与有效策略 |
| `lifecycle.apply` | 创建、恢复、校验与取消运行 |
| `lifecycle.hold` | 创建与释放 Legal Hold |
| `lifecycle.policy` | 设置 Scope 策略覆盖 |
| `lifecycle.audit.read` | 读取生命周期审计 |
| `scope.archive` | 归档与取消归档 Scope |

擦除其他 Scope 中 declared copy 的 plan 需要在每一个这样的 Scope 中拥有 `lifecycle.apply`，或拥有 `server.admin`；否则这些项为 `blocked`，apply 拒绝该 plan。在 RFC 1396 实现之前，每个生命周期操作只对 RFC 1396 视为遗留本地管理员的调用者可用：配置的 bearer token，或在禁用认证时的 loopback 调用者。PDP 不可用时，生命周期操作以 `503` fail closed，不做任何变更。

## 配置

`LifecycleConfig` 是位于 Builtin 与 Server 配置中、与 `runtime`、`inference`、`external_skills` 并列的 pydantic 模型，通过同一套 settings 机制加载。其形状即上文的策略文档。校验错误在启动时是配置错误，对 Scope 覆盖则是 `lifecycle_policy_invalid`。

## 可观测性

遵循 RFC 0046，生命周期发出：

- 指标 `powercontext_lifecycle_items_total{action, record_class, disposition}`、`powercontext_lifecycle_runs_total{action, state}` 与 `powercontext_lifecycle_run_duration_seconds{action}`；标签是有界枚举，永不包含 `scope_id`、身份或 hold 标识；
- 结构化日志事件 `lifecycle.run.completed`、`lifecycle.run.failed`、`lifecycle.run.verification_failed` 与 `lifecycle.hold.changed`，携带 `run_id`、`scope_id`、`action`、计数与错误码；
- 每次运行一个 `lifecycle.run` span，带同样的属性。

没有任何信号携带内容、自由文本 reason、Source 标识或原始后端错误。

## 兼容性

- 现有部署在策略设置窗口或操作员 apply 一次运行之前，观察不到任何变化。
- `forget()`、`reactivate()`、`retire_memory_entry`、head 治理、Candidate 评审与 Source 捕获保持其语义；`reactivate_memory_entry` 与 `update_artifact_lifecycle` 是可加性的。
- OpenAPI 变化是可加性的：新操作、可选响应字段，以及精确读取上的一个新状态码。
- Python 变化是可加性的：`MemoryEntryVersion` 上可选的 `erased_at`、`include_erased` 与 `include_inactive` 列表选项、类型化的 erased 错误，以及证据检查上的 `unavailable_reason`。
- schema 变化在两个后端上都是可加且幂等的。
- 降级后 schema 仍可用；旧代码读取 tombstone 会以现有的 invalid-stored-payload 错误失败。

## Implementation slices

每个切片都让系统保持一致并可独立验证：

1. **Schema 与 tombstone 读取。** 新列与新表、`ensure_lifecycle_schema`、感知 tombstone 的 repository、`ContentErasedError`、`410 content_erased`、证据检查上的 `unavailable_reason`、`include_erased` 列表，以及跳过 tombstone 的 `rebuild_projections`。
2. **策略与 preview。** `LifecycleConfig`、带 CAS 的 Scope 覆盖、effective policy digest、引用图与可达性、purge 与 erase 规划、影响报告、plan digest 与 preview 操作。
3. **Hold 与 purge 运行。** `pc_lifecycle_holds`、hold 匹配、运行行、分批、cursor、租约、resume、cancel、审计行、回执，以及两个后端上每个类别的 purge。
4. **擦除。** Memory entry、Artifact Revision、带 `retain`、`invalidate`、`cascade` 的 Source、declared copy、包处理、投影清理、校验与远端收敛计数。
5. **归档与治理对等。** `archived_at`、`archive_scope`、`unarchive_scope`、已归档 Scope 的效果、`reactivate_memory_entry`，以及面向 Experience 的 `update_artifact_lifecycle`。
6. **界面与运维。** OpenAPI、生成的客户端、CLI、面向 purge 的调度器自动 apply、操作员 how-to 文档，以及 Dashboard 对已擦除与已归档项的渲染。

## Test and acceptance plan

只有当以下可观察场景在 SQLite 与 OceanBase 上都通过时，实现才算完成：

- 用户通过公开 API 遗忘并随后重新激活一条 Memory entry；不创建内容版本，且每个更早的 Revision 读取结果不变；
- 对未变化数据的两次 preview 返回相等的 plan digest；任何符合条件的变化之后的 preview 返回不同的 digest，携带旧 digest 的 apply 返回 `lifecycle_plan_stale` 且不写入任何东西；
- preview 按记录类别、family 或 Source 类型、reason code 与 disposition 报告精确的有界计数，并在选择集超出上界时报告 `truncated`；
- 一次 purge 运行只删除超出窗口的无引用行，保持 journal position 与 cursor 有效，且 Source window 与 flush 容忍空洞；
- 在批次之间被中断的运行从其 cursor 恢复，完成时既不重复也不遗漏任何项，且其计数等于 plan 减去已记录的 skip；
- 已擦除的 Memory entry、Artifact Revision 与 Source 观察各自留下 tombstone 行，保留每一个外键，精确读取返回 `410 content_erased`，在搜索、召回、全文、向量与 head 投影中不存在，且在 `rebuild_projections` 之后仍然不存在；
- 已擦除 head Revision 的 `latest` 返回 `410` 且永不返回更早的 Revision；引用了已擦除证据的 Handoff 把该证据解析为原因 `erased` 的 `unavailable`，且永不替换为另一条观察或版本；
- 以 `retain` 擦除 Source 不改变任何派生行；`invalidate` 恰好退休并遗忘 plan 中列出的派生项；`cascade` 恰好擦除它们；除非 plan digest 包含了某个派生项，否则它不发生变化；
- 擦除一个 Skill Revision 使其发布在本地与远端 target 上收敛到 `unpublished`，在包不可达时对其打 tombstone，并且只在获得授权时擦除其他 Scope 中的 declared copy；
- 活跃的 Legal Hold 在 preview 中以 `held` 计数与 hold 标识出现，apply 跳过被 hold 的项，对被 hold 项的显式 erase 返回 `legal_hold_active`，释放被审计；
- 已归档的 Scope 从默认列表、选择、Context Reference 展开与计划处理中排除；精确读取可用；写入返回 `scope_archived`；unarchive 恢复它；
- 计划 purge 只在设置了 `auto_apply` 时运行，永不运行 `erase`，并记录与手工运行相同的审计与回执；
- 放入被擦除内容中的哨兵字符串，永不出现在该运行产生的日志、指标标签、trace、审计行、运行回执或错误响应体中；
- 相同的契约向量在两个后端上产生相同的 disposition 与计数。

跨组件场景放在 `tests/e2e/` 中，并通过公开 HTTP 契约断言。聚焦测试覆盖策略校验与合并、可达性、plan 排序与 digest、hold 匹配、tombstone 读取方、批次重新校验，以及每个 family 的"血缘覆盖引用"一致性，且不冻结私有调用顺序。

# Drawbacks

- 除非行变为无引用，tombstone 永久保留身份、digest、大小与时间戳。极短已擦除文本的 digest 是一个弱 oracle；认为这不可接受的部署需要密码学粉碎，而本 RFC 不提供。
- 擦除之前已交付的内容无法召回：Agent 对话记录、模型 Provider 日志、已导出的 Handoff Report，以及再也不会重连的 Receiver 主机。运行回执让远端缺口可见，但无法弥合它。
- 擦除以 Revision 与 entry version 为粒度。Memory change 与 Handoff omission 中的自由文本 `reason` 字段无法按字段擦除；移除它们意味着擦除整个 Revision。
- 四张新表、七张现有表上的 tombstone 与时间戳列，以及按方言的迁移，增加了两个后端必须同步维护的 schema 面。
- 按 plan digest 批准迫使每当符合条件的数据变化时都要重新 preview。繁忙的 Scope 可能需要更小的 `max_items_per_run` 才能完成 apply。
- 派生 Memory entry 通过 `source_refs` 查找，而它是一个 JSON 列；除非之后新增引用索引，大 Scope 要付出一次扫描。
- 在已归档 Scope 中拒绝写入比纯粹的可见性变化更严格，可能让绑定仍指向该 Scope 的集成感到意外。
- 校验会为每个已擦除身份回读每个投影，这让大规模 erase 运行比单纯的变更更慢。

# Rationale and alternatives

## 选择：原地 tombstone、显式运行、单一策略文档

schema 中的每一条引用都是 `RESTRICT`，因此唯一能让精确引用、血缘、manifest 与发布保持可验证的擦除方式，就是保留行、移除内容。带 preview、digest、分批与回执的运行协议，是同时满足有界预览、授权、幂等、崩溃恢复与审计的最小机制。一份带版本的策略文档覆盖部署、Scope、记录类别、family 与 Source 类型，而无需规则引擎。

## 替代方案：每张表一个 TTL 列

否决。它无法表达权威性、按引用保护、级联、引用行为、投影清理、hold 或审计，并且会静默删除被引用的行或在外键上失败。

## 替代方案：删除旧 Revision

否决。删除 Revision 会破坏血缘、精确引用、Handoff 校验与回滚，并违反 RFC 0048 与 RFC 1400。

## 替代方案：用 Ebbinghaus 式衰减作为删除策略

否决。相关性衰减是排序信号。用它做删除会把检索质量与用户意图、合规和持久历史混为一谈，并且它无法作为一项决定被预览、被 hold 或被审计。

## 替代方案：逻辑 retire 之后永久保留一切

否决。它无法满足存储限制、外部删除义务、擦除请求或离场。

## 替代方案：独立的 tombstone 表并删除原始行

否决。来自血缘、head、manifest 与发布的外键都指向原始行。把身份迁到另一张表需要重写每一条引用，而这正是本 RFC 禁止的历史重写。

## 替代方案：带级联外键的物理行删除

否决。级联会删除血缘行、head 与 manifest，摧毁证据图，并让精确引用解析为空或解析到错误的东西。

## 替代方案：对 payload 做密码学粉碎

推迟。按 Scope 或按观察的密钥可以让擦除变成密钥销毁，但全文与向量投影仍持有派生明文，密钥管理引入运维依赖，而 tombstone 已经满足验收标准。粉碎可以之后叠加。

## 替代方案：在服务端持久化 preview 并按标识批准

首个版本否决。重新计算 plan 并比较 digest 给出同样的批准保证而无需 plan 存储，并且它能检测到存储的 plan 会掩盖的并发变化。

## 替代方案：Artifact head 上的 `archived` 状态

推迟。改变 `lifecycle_state` 取值需要在两个后端上改 CHECK 约束，并在 SQLite 上重建表。Scope 归档用一个可空列与现有 CAS 就满足了可见性需求。

# Prior art

- RFC 0014 定义了带 Revision 的 `forget()` 与 `reactivate()`，并明确把物理擦除留给单独设计；本 RFC 就是那个设计，并保持 Memory 契约不变。
- RFC 1351 定义了 head 治理，把包垃圾回收推迟给带有记录保留期的收集器，并要求取消发布只移除精确完整的包。本 RFC 定义了那个收集器，并复用发布期望状态模型做擦除收敛。
- RFC 1400 保护被引用的观察，把 head 删除与证据分离，并要求不可用证据被报告而不是解析到别处。本 RFC 实现了这些要求。
- RFC 1396 定义了审计数据最小化边界、动作词汇，以及 Continue 把已删除或已退休引用标记为不可用的规则。本 RFC 复用这三者并新增 `erased` 原因。
- RFC 0082 定义了带 purge 操作的 Activity Event 保留，以及 Workstream 的 archived 目录状态；二者都为本文的清理类别与 Scope 归档提供了参考。
- RFC 0046 定义了遥测永不可包含的内容；生命周期信号原样遵循。
- 在 PowerContext 之外，日志压缩流中的 tombstone 记录、内容寻址存储中带宽限期的不可达对象垃圾回收、云存储中的 object-lock legal hold，以及搜索引擎中的索引生命周期阶段，都采用同样的身份、内容、可见性与物理移除之间的分离。

# Unresolved questions

- Scope 删除。对 `pc_scopes` 的每条引用都是 `RESTRICT`；Scope 是否可以被删除，以及这对子 Scope 与发布意味着什么，属于 issue #1219 中的 Scope 模型。
- 在选择加入的部署中，外部删除的擦除候选是否可以在窗口过后自动 apply，还是必须像本 RFC 要求的那样始终手工批准。
- 向已归档 Scope 的写入应当如本文规定被拒绝，还是应当允许并给出警告。
- 除运行之外，preview 是否也应为审计而持久化。
- Memory 与 Handoff head 是否需要治理状态，单个 Artifact head 是否需要 `archived` 状态。
- issue #1321 的 Memory manifest 压缩必须如何处理已擦除的 entry version。
- 是否需要对自由文本 `reason` 与 omission 字段做字段级擦除。
- 审计压缩为汇总是否需要在首个实现中提供，还是可以等到真实数据量出现。
- 针对 Memory entry `source_refs` 与 `artifact_refs` 的持久化引用索引是否应成为首个实现的一部分。

# Future possibilities

- 面向运行、hold、策略与审计的 Dashboard 生命周期页面，以及用于归档的 Dashboard 操作。
- 以同一份文档表达的常见合规制度的策略模板。
- 基于 RFC 1400 Connector 保证，按 Source Definition 把正向删除证据映射到保留行为。
- 在同一运行协议之下，对捕获的 payload 与包叠加密码学粉碎。
- 擦除前导出：在内容被移除之前为请求者产出一份可验证的副本。
- 通过 MCP 暴露只读的生命周期状态，供必须解释证据为何不可用的 Agent 使用。
- 如 RFC 0082 所预期的、面向 Handoff Report 的 Project 级保留、pinning 与 legal hold。
