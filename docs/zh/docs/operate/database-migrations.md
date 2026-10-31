---
title: 数据库迁移
description: 了解数据库迁移命令、备份策略、服务启停及当前验收范围。
---

# 数据库迁移

统一迁移使用 Alembic 管理版本，只新增 `pc_schema_revision(version_num)` 一张控制表。安装或更新程序与迁移数据库分开；结构变化由随包发布、不可改写的 revision 显式执行。
设计关联 [RFC #1771](https://github.com/oceanbase/powercontext/pull/1771) 和 [issue #1756](https://github.com/oceanbase/powercontext/issues/1756)。

## 当前可用范围

`powercontext server db-migrate status/plan/apply/verify` 默认选择 `--bundle server`，使用部署配置中的持久 SQLite、嵌入式 seekDB 或 OceanBase MySQL 模式数据库。维护命令以显式环境文件为准，不接受任意测试 bundle 路径。

完整 Server 框架登记了两个真实安装包来源：

- **1.3.0：**`s1300 → s1400 → s1401 → s1402 → v1_4_1_004 → v1_4_1_005 → v1_4_1_006`。
- **1.4.0：**通过独立冻结的来源桥接，执行 `r1400 → s1400 → s1401 → s1402 → v1_4_1_004 → v1_4_1_005 → v1_4_1_006`。

应用发布版本与 schema revision 分别编号。1.4.0 发行包不等于 `s1400` 检查点：其 Candidate 结构已经是当前格式，
但 Topic 尝试状态仍需登记的桥接。两条路径保留既有引用和历史，到达相同的受管目标结构；原有资源摘要分别保留，
后续追加已登记 revision 时仍可验证。不能据此推导其他发布版本或额外数据库对象同样兼容；它们必须先有精确的
来源登记和验收，才能执行写入。更早的 Memory 集合转换不属于这两条路径。

两个来源均登记了三种后端的 Code 开关配置，并为单位归一化的 L2 向量登记独立 profile。SQLite、OceanBase 的
Code 本地缓存独立于 Server 数据库；嵌入式 seekDB Code 使用三张已登记的表。维护保留已有图数据，为新建的
Code 启用数据库创建登记的表。原生后端的来源与 profile 登记必须通过对应后端的真实安装包升级门禁。

只有结构、领域数据、搜索投影和任务校验全部通过，才返回 `state=ready`、`readiness_scope=server`、`server_ready=true`。发布还必须通过真实安装包在三个后端上的完整验收；实现路径存在不等于某次发布已通过验收，支持状态以安装指南和发布门禁为准。

升级前必须停止所有写入者并排空 1.3.0 中 queued/running Dream 任务；新版不能执行旧提示词契约。终态任务保留原始 payload，待处理 Processing intent 的计数保留，旧 worker lease 被 fencing。迁移按登记规则扩展处理能力，保留原有自动处理选择，不根据当前配置覆盖旧所有权。

1.3.0 路径将 Candidate 旧表保留为 `pc_retained_dream_candidate_heads` 和 `pc_retained_dream_candidate_versions`，用于迁移当次的复制验证。它们与迁移备份都不是线上写入目标；升级后的正常修改不再与旧快照比较。历史 Artifact 新增的来源时间/操作者字段保持未知，既有 Source 引用保持不变。投影重建和数据校验会扫描相关表；请为停机时间、备份与保留副本安排容量。

1.4.0 路径原样保留已是当前格式的 Candidate heads、versions 和 payload，不运行 1.3 的 Candidate 转换。
为统一目标结构，创建相同的两张空保留表；空表不代表虚构的 1.3 历史。写入前检查当前 Processing 声明、Dream
payload 身份及待处理 intent。兼容的当前任务原样保留，不套用仅针对 1.3 Dream 的排空规则；显式维护窗口内使
旧 worker lease 失效。

`s1401` 在三种后端的托管目标结构中登记 `pc_memory_artifact_archive`。该表保存供离线维护使用的 Memory 历史快照，不提供公开 Artifact 读取入口。新数据库的归档表为空；重复迁移保留其原始字节，运行时准入检查验证登记的表结构。本 revision 不新增对其他历史来源结构的支持。

`s1402` 登记可移植归档的恢复回执，并为 MySQL 兼容后端中可移植的 Artifact、Profile、Topic 发布及标签时间戳
保留微秒精度。已有受管库通过显式维护执行这些结构变更；运行时启动不补建回执表，也不修改时间戳列。

修订 `v1_4_1_004` 将 `pc_access_owner_recovery` 纳入管理，用于持久保存 Artifact 和 Candidate 的所有权写入意图。两条已登记发布源路径及向量配置均包含此表。已有受管数据库需要显式维护，新数据库创建已登记的空表。待恢复意图在 Server 重启后保留原有 deployment、Principal 与幂等身份；历史修订资源及校验和保持不变。未登记布局不会仅凭表名被自动接纳。此登记不代表已通过安装发布版或外部 Provider 的升级验收。

`v1_4_1_005` 为文本和向量配置添加可空的 `pc_atomic_memory_current.description` 列。已有受管库需要显式迁移，启动不会修改该列。新增列不改写历史 Artifact 内容；已有投影的摘要保持空值，直到新写入或显式投影重建复制权威摘要。

[可移植归档恢复](portable-archive.md) 中断后，普通启动和 worker 会继续拒绝未完成的检索投影。
请使用同一个通过校验的归档，重新执行 `powercontext archive restore <bundle> --yes`。
仅此显式恢复路径可在验证已登记结构和配置的向量 profile 后打开投影尚未完整的数据库，且不启动 worker。
没有待完成恢复回执时，恢复入口仍执行完整就绪校验。

普通 Server 启动先做只读就绪检查，不修补已有库。空的本地持久 SQLite/seekDB 可通过同一版本链初始化；OceanBase 必须先执行显式维护命令初始化。内存 SQLite 仍为临时运行时存储。迁移后发现结构或投影被篡改，必须停写维护，不能靠重启修复。

Server identity 初始化在受管库上只检查已登记的表，不执行 DDL。首次创建 identity 值以及显式轮换已停止副本的
identity，仍使用原有事务语义。

向量维护要求声明与历史数据完全一致的 embedding profile：profile ID、模型、维度、距离和归一化方式。迁移保留历史向量及 Topic 检索配置，不调用 embedding provider。SQLite 的登记步骤添加并回填 Atomic Memory 的 vec0 辅助索引和事务触发器；原生后端保留登记的向量列与 HNSW 参数。`pc_server_storage_profile` 保存完整的 embedding 配置，不是另一张 revision 表或恢复日志。通用存储读入口校验该声明，Server 启动使用配置或注入模型的实际 profile。受管辅助对象缺失或 profile 变化会在启动修补或写入之前被拒绝。

空的持久 SQLite/seekDB Server 启用向量时，通过同一版本链所选的 profile 初始化。已有未受管库必须显式维护，且来源结构必须与登记布局完全一致。切换配置不能绕过准入检查，不能给受管无向量库直接启用向量，也不能隐式增加向量或 Code 对象。已经受管但没有 Code 表的数据库若要启用嵌入式 Code，需要另行登记配置转换，启动入口与独立 Code CLI 均拒绝隐式扩展。测试目录保留 1.3.0 来源、三个后端、向量开关及 Code 开关组成的 12 种组合。嵌入式 seekDB 要求使用包含恢复事务锁修复的 pylibseekdb 1.4.0.post2 或更新版本。自动 CI 和发布要求全部 12 项通过，包括两个嵌入式 seekDB Code 配置，不豁免任何组合。在 Main 工作流手动选择 `upgrade_selection=seekdb-code` 仅执行诊断子集，不能据此授权发布。1.4.0 桥接仍保留登记，但不纳入 CI 升级验收；门禁通过不能证明该路径已通过验收。Code 配置用各自实际安装的来源版本创建索引，保留缓存文件与嵌入式图数据，并在升级后查询原指纹和符号。向量配置保留非空的历史 Atomic 与 Topic 向量，通过实际安装的目标包验证语义读取和新写入，拒绝 profile 漂移，并对比新建库与升级库。SQLite 还验证辅助对象缺失时的拒绝行为及备份还原。无向量配置通过不能证明向量支持，只有实际目标安装包通过对应配置的验收，才能声明该配置的升级支持。

`--bundle artifacts` 保留 `p0001` 开始的局部 Artifact 原型链；`--bundle topic-memory-recovery` 只处理登记的 Topic 恢复布局。两者均报告 `server_ready=false`，不得用于证明完整 Server 就绪，也不能把其版本号直接 stamp 为完整 Server 版本。

MySQL 兼容后端的结构反射支持 CHECK 表达式中的二元 `+`、`-`、`*`、`/`、`%`、一元正负号及嵌套括号，并保留算术、比较和布尔运算的优先级。未支持的 CHECK 运算符或函数仍被拒绝，错误会标明反射的表名、约束名及未支持的语法，避免将合法分组误报为括号不匹配。表达式解析成功不代表历史结构已登记，也不会绕过迁移或数据完整性检查。

原有 `server processing-migrate` 在受管数据库上转发到 `db-migrate --bundle processing`；未登记 revision 的数据库仍保留领域独立迁移。具体步骤见[迁移 Artifact 处理状态](artifact-processing-migration.md)。服务部署参考[部署 Server](deploy-server.md)。

`server atomic-memory-migrate` 在受管数据库或匹配完整 1.3.0 Server 基线的数据库上转发到
`db-migrate --bundle server`，包括计划确认、备份和恢复要求；未受管的旧 Memory 布局继续使用领域独立转换。
该转换的决策文件不能修改已登记的 Server 版本链。兼容命令在受管库上返回共享 Server 计划或结果，
已就绪时 apply 保持只读，不重复迁移。参见[迁移到 Atomic Memory](atomic-memory-migration.md)。

## 迁移编号与软件版本

目标发布版本为 **PowerContext 1.4.1**。迁移名称采用
`v<主版本>_<次版本>_<补丁版本>_<序号>`，序号在同一版本内使用三位数字。
`000` 表示已登记的历史基线，升级步骤从 `001` 开始。

| 迁移名称 | 所属软件版本 | 步骤 | 兼容保留的 revision | 含义 |
| --- | --- | --- | --- | --- |
| `v1_3_0_000` | 1.3.0 | 基线 | `s1300` | 已发布的 1.3.0 Server 存储 |
| `v1_4_0_000` | 1.4.0 | 基线 | `r1400` | 已发布的 1.4.0 Server 存储 |
| `v1_4_1_001` | 1.4.1 | 第一步 | `s1400` | 完整 Server 存储转换 |
| `v1_4_1_002` | 1.4.1 | 第二步 | `s1401` | 离线 Memory 历史归档 |
| `v1_4_1_003` | 1.4.1 | 第三步 | `s1402` | 可移植恢复回执与时间戳适配 |
| `v1_4_1_004` | 1.4.1 | 第四步 | `v1_4_1_004` | 持久化所有权恢复意图 |
| `v1_4_1_005` | 1.4.1 | 第五步 | `v1_4_1_005` | Atomic Memory 可空摘要投影列 |
| `v1_4_1_006` | 1.4.1 | 第六步 | `v1_4_1_006` | Source write 插件注册 |

两条已登记来源路径都升级到 `v1_4_1_006`；CI 升级验收只选择 1.3.0：

```text
1.3.0: v1_3_0_000 -> v1_4_1_001 -> v1_4_1_002 -> v1_4_1_003 -> v1_4_1_004 -> v1_4_1_005 -> v1_4_1_006
1.4.0: v1_4_0_000 -> v1_4_1_001 -> v1_4_1_002 -> v1_4_1_003 -> v1_4_1_004 -> v1_4_1_005 -> v1_4_1_006
```

`status` 和 `plan` 在原有机器字段之前显示 `source_migration`、`target_migration`、
`pending_migrations`；`apply` 和 `verify` 显示 `migration`。每项包含 `name`、
`software_version`、`sequence` 和兼容保留的 `revision` ID。例如：

```json
{"revision":"v1_4_1_006","name":"v1_4_1_006","software_version":"1.4.1","sequence":6}
```

这里的软件版本描述已登记的数据库状态，不代表当前运行的软件包版本，也不代表中断维护窗口最初的来源。
没有存储变化的软件发布可以复用同一数据库 revision。命名不代表升级已经通过验收，完整发布门禁仍然生效。

现有 `s1300`、`r1400`、`s1400`–`s1402` 继续保存在版本表、计划标识、冻结资源和恢复证据中。
新名称是它们的展示别名，不是额外的可执行 revision，也不能作为替代 ID 手工写入版本表。
这样可以继续使用已有计划和原始备份。局部原型 bundle 保留自己的独立编号。

后续新增 Server 迁移直接使用该命名作为 Alembic `revision`，并正常登记结构、领域步骤和恢复检查点。
`down_revision` 必须引用前一步的实际 ID；若直接追加在当前 head 之后，引用 `v1_4_1_006`，即使它已有展示别名。
框架按照登记的链执行，不按名称字典序推算。已登记的名称和所属发布版本保持不变，版本升级不能重命名历史。

## 初始化与维护职责

下表列出结构变更与投影修复的执行入口。含有 `pc_schema_revision` 的数据库均视为受管库，包括未知 revision；
局部领域检查通过不代表整个 Server 可以使用该库。Profile 准入先验证已登记的整库结构与领域状态。

| 入口或辅助函数 | 受管数据库 | 未受管数据库 |
| --- | --- | --- |
| `SQLiteProfile.open`、`SeekDBProfile.open`、`OceanBaseProfile.open`；`schema.create_tables` | 使用前先做整库就绪检查；所选表必须已经存在 | 保留所选表创建和领域准入 |
| Runtime composition：Skill 分发、标签 Family、Scope 搜索、revision 元数据和 Artifact Head 列 | 跳过兼容性 DDL 与回填，由已登记的 Server 版本链负责 | 保留既有兼容辅助函数 |
| Runtime Atomic Memory 权威记录、current 索引和 SQLite FTS 辅助对象 | 校验 current 数据；结构准入后通过 `db-migrate --bundle atomic-projection` 修复 | 保留领域初始化与离线投影修复 |
| Runtime Experience/Skill 搜索文本与 FTS 索引 | 由已登记的 Server 领域步骤构建和校验；启动不重建 | 保留领域初始化与投影恢复 |
| Runtime Topic Memory 索引、恢复表和检索配置 | 使用 `validate_only=True` 初始化；局部 Topic 恢复拒绝受管库 | 保留领域初始化；局部恢复只覆盖其登记的布局 |
| Processing 结构与所有权配置 | 启动时校验；配置变更走 `db-migrate --bundle processing` | 保留领域 bootstrap 和 `processing-migrate` |
| `ServerIdentityRepository.initialize` | 检查 identity 表，不执行 DDL；创建 identity 值和显式轮换仍使用普通事务 | 保留支持并发的 identity 表初始化 |
| 离线 Memory 归档 | Server 版本链在 `s1401` 登记结构，运行时准入检查该结构 | 旧 Memory 离线转换负责其归档和转换回执 |
| `atomic-memory-migrate`、`processing-migrate` | 转发到共享 Server 或已登记的维护计划 | 保留领域独立行为；识别为历史完整 Server 的库必须先完成整库升级 |
| 向量对象 | 所选 Server profile 负责结构、辅助索引回填及精确配置准入；切换 embedding 空间需要独立转换 | 完整的已发布向量结构可显式迁移；独立未受管 SDK 存储保留领域初始化 |
| 嵌入式 seekDB Code 对象 | 已登记 Code profile 创建并验证三张表及全文索引；Code 启动不修补缺失的受管对象 | 完整的已发布 Code 启用结构可显式迁移；独立未受管 Code 保留既有初始化 |
| SQLite、OceanBase 的独立 Code 缓存 | 保留缓存文件，主数据库走共享版本链 | Code 服务负责本地缓存创建和索引构建 |

这些辅助函数仍供所属 runtime 或维护操作使用；直接调用底层函数不能替代 Profile 准入、已确认的维护计划和
备份恢复要求。未来受管对象的变化应加入已登记的 revision、领域步骤与校验器，不另建启动迁移入口。
只读 SQLite 检查入口与独立维护连接可以在业务初始化之前检查历史存储；检查连接打开成功不代表 Server 已就绪。

Code 的嵌入式 seekDB 存储在部署数据库中添加 `pc_code_generations`、`pc_code_nodes`、`pc_code_edges` 和
`ix_code_search`；其他 Code 部署使用本地 SQLite 缓存。这些派生索引的数据由 Code 服务负责，
冻结扩展为维护与完整 Server 准入登记其结构。部分缺失或发生变化的扩展会被拒绝，不能隐式扩展受管结构。
SQLite、OceanBase 的 Code 缓存不改变主库结构。

## Processing 配置维护

`--bundle processing` 在结构完整、已到当前 revision 的 Server 数据库上登记离线所有权配置操作。
它不推进 `pc_schema_revision`，也不接管旧结构；必须先完成整库升级。
使用切换后的部署配置，每次模式切换指定新的迁移 ID：

```bash
powercontext server db-migrate plan --bundle processing --env-file .env \
  --migration-id global-to-dedicated-1 --backup auto
powercontext server db-migrate apply --bundle processing --env-file .env \
  --migration-id global-to-dedicated-1 --backup auto --plan-id PLAN_ID \
  --maintenance-confirmed --yes
powercontext server db-migrate verify --bundle processing --env-file .env \
  --migration-id global-to-dedicated-1
```

该操作复用结构迁移的目标锁、备份策略、后端维护连接和停写声明。OceanBase 仍要求固定维护主机、
`--evidence-dir` 和 `--lock-coordination single-host`；报告上次运行未结束时，恢复必须提供准确的运行确认 token。
此操作不切换本地服务；执行前须自行停止全部写入者并暂停自动重启。

模式切换的所有阶段在一个事务中提交。提交前失败时，所有权声明和租约撤销一起回滚。
中断后重新查看计划，保留原配置、迁移 ID、备份策略和证据目录恢复；原维护窗口和恢复点继续沿用。
成功后的重试不会再次撤销租约。操作前后都运行完整 Server 校验；配置已匹配时只读返回。
Processing 和 Atomic Memory 投影维护均在接受操作前校验原迁移证据和已记录 revision 的校验和。
两者还会读取数据库保存的 embedding profile；即使命令配置遗漏 embedding，也会在生成计划、备份或维护记录前拒绝向量存储。
Processing 和 Atomic Memory 投影维护均在接受操作前校验原迁移证据和已记录 revision 的校验和。
两者还会读取数据库保存的 embedding profile；即使命令配置遗漏 embedding，也会在生成计划、备份或维护记录前拒绝向量存储。

## Atomic Memory 投影维护

`--bundle atomic-projection` 在当前受管 Server 数据库上修复 Atomic Memory current 行和 SQLite FTS
辅助索引，适用于已登记的无向量配置，包括 SQLite、OceanBase 使用独立缓存的 Code 配置。它保留 Server revision、不可变正文与历史、生命周期、
正式标签、权限和 Source/Processing 状态。

```bash
powercontext server db-migrate plan --bundle atomic-projection --env-file .env --backup auto
powercontext server db-migrate apply --bundle atomic-projection --env-file .env \
  --backup auto --plan-id PLAN_ID --maintenance-confirmed --yes
powercontext server db-migrate verify --bundle atomic-projection --env-file .env
```

该操作与 Processing 维护共用目标锁、备份、停写声明和持久恢复事务。plan 和 verify 只读；
apply 只修复派生行，完整 Server 校验通过后才提交。权威数据损坏或结构未登记时拒绝执行。
投影已就绪则只读返回。提交前中断会回滚修复；使用新的计划、原软件产物、配置、备份策略和证据目录恢复，
继续沿用原恢复点。另一项维护未完成时，须先完成原操作。OceanBase 同样要求固定维护主机协调，
并在报告上次运行未结束时提供准确的确认 token。

检查和修复扫描相关 Atomic 历史、head、标签、lineage 及 current 投影，通过键集分页限制行缓冲；
这是停服执行的全投影操作，不登记向量 profile 切换或嵌入式 seekDB Code 配置。
兼容命令 `server atomic-memory-rebuild-projection` 在受管数据库上转发到此 bundle，
支持 `--action plan/apply/verify` 和同样的确认、备份选项。

## Topic Memory 恢复 bundle

选择 `--bundle topic-memory-recovery` 执行版本化的 `powercontext.topic-memory-recovery.v1` 转换。它识别恢复功能之前的
预算布局和实现 #1966 的 attempts 表布局，保留已消费额度、终止阻塞和已接受回执。它仅验证已有 Server 数据库中
登记的 Topic 对象和共享处理 lease 的 fencing，不改变其他业务表；输出仍报告 `server_ready=false`。
默认 `--bundle server` 的完整升级路径见上文。

此部分 bundle 仅适用于未纳入统一框架的存储。只要数据库包含 `pc_schema_revision`，即使 revision 未知，
也会在备份和写入前拒绝。请使用 `--bundle server` 或已登记的受管维护操作。

先停止全部 API、Worker、调度和 SDK 写入者，并暂停自动重启。审阅并执行一次计划：

```bash
powercontext server db-migrate plan --bundle topic-memory-recovery --env-file deployment.env --backup auto
powercontext server db-migrate apply --bundle topic-memory-recovery --env-file deployment.env --backup auto --plan-id PLAN_ID --yes --maintenance-confirmed
powercontext server db-migrate verify --bundle topic-memory-recovery --env-file deployment.env
```

SQLite 使用整个数据库的原生备份。OceanBase 还须提供 `--evidence-dir` 和 `--lock-coordination single-host`，按下文
选择支持的备份策略并声明。中断后保留原恢复点和证据目录，以相同选项继续。SQLite 回滚未完成的转换；MySQL 兼容
DDL 仅恢复库外证据登记的准确前缀，仍遵守旧远端执行的显式确认规则。

当前尝试 metadata 移入预算；`pc_topic_memory_work_blocks` 仅保留终止阻塞和回执。原 ID、受理时间/generation 和
成本保持不变。旧 attempts 没有可靠的范围来源：原数值进入 `legacy_source_through`，准确 `source_through` 为 null；
`budget_source_through` 仅从同一份仍存在的额度复制，否则为 null。不能用当前额度解释旧回执。验证通过后才删除旧
attempts 表，成功和被取代行仍保存在恢复点中。未知或不一致布局在写入前拒绝。普通启动要求目标格式，不升级已有库。
准确阻塞查询和已受理 retry 回放不自动过期。

空恢复存储先创建阻塞表，再创建预算；SQLite 原子提交两张表。只有冻结、空的阻塞表前缀且没有其他业务状态时才可
继续初始化。允许已知的 Access bootstrap 和独立 Code 图表先存在，且不改变它们；除下述已登记的 SQLite Atomic
维护初始化产物外，其他业务表或回执在写入前拒绝，不能在保留恢复证据的同时静默重建缺失预算。

SQLite 的 `server atomic-memory-migrate --action apply --maintenance-confirmed` 可以在正常 Server 启动前初始化
新数据库。登记的产物包含准确的 Atomic current 表、FTS5 表与触发器、Memory archive，且没有 Atomic 行或归档证据。
正常启动可以原子地补齐初始化，`--bundle topic-memory-recovery` 也支持对该状态执行 `plan/apply/verify`。离线路径
会先备份已有数据库再添加 Topic 表，不将维护产物视为空库。缺失或改变的对象、额外业务表及已有 Atomic 证据仍在
写入前拒绝。此续建路径仅适用于登记的 SQLite 布局，不新增 OceanBase 或 seekDB 基线。

转换通过递增 generation、按 UTC 使 lease 过期并设置维护窗口 holder，一次性撤销所有旧 processing lease；中断
恢复时不重复推进这个 holder。这沿用既有 processing 维护的 fence 转换，要求共享这些 lease 的所有工作流均停写。
已知尝试 owner 保留在预算，未知结束时间仍为 null。即使处理继续禁用、没有新 Supervisor 启动，查询也能固化已停止
工作；升级前 Worker 不能重新获得发布权。

## 只读检查与一次确认

下面的 `deployment.env` 示例使用 SQLite；OceanBase 命令还须传入下一节的证据目录和固定主机协调参数。先检查目标或预览计划，这些命令不会创建缺失数据库、父目录或控制表：

```bash
powercontext server db-migrate status --env-file deployment.env
powercontext server db-migrate plan --env-file deployment.env --backup auto
powercontext server db-migrate verify --env-file deployment.env
```

计划绑定目标身份、源／目标 revision、实际结构摘要、冻结脚本摘要、配置摘要、备份策略、服务范围和迁移协调范围。配置与凭据值不输出到诊断中；预期错误返回稳定 JSON 类别和非零退出码。

配置绑定包含目标部署的规范 Processing 归属模式、能力和自动绑定声明。改变这些声明后必须重新生成计划，不能使用旧计划执行。计划绑定本身不验证或迁移历史 Processing 状态；完整 Server bundle 另由已登记的领域步骤校验持久化声明与待处理 intent。

在支持范围内，终端用户可直接执行 `apply`，默认 PC 自动备份。命令展示计划、备份方法、维护影响及停写责任，确认一次后执行，不逐条 SQL 询问：

```bash
powercontext server db-migrate apply --env-file deployment.env --backup auto
```

这次确认同时声明：所有其他 API、Worker、SDK、调度和自动重启入口均已停写；共享库场景还由操作者协调后续节点升级。PC 无法发现任意外部客户端。计划发生实质变化时拒绝执行，需要重新审阅。

自动化必须显式指定策略和计划，并分别声明维护及备份选择。例如用户自行完成备份并停止全部写入者后：

```bash
powercontext server db-migrate plan --env-file deployment.env --backup manual
powercontext server db-migrate apply --env-file deployment.env --backup manual \
  --plan-id PLAN_ID --backup-confirmed --maintenance-confirmed --yes
```

`--yes` 仅接受计划，不隐含手动备份声明、无备份风险或所有节点已停写。`--shared-database` 将共享库维护责任加入计划，不使 SQLite 获得跨主机部署能力。数据库已达到 bundle 目标且无需持久化变更时，`apply` 返回 `changed=false`，不备份、不启停服务；程序版本切换仍须单独安排。

## 目标配置与库外证据

数据库通过现有 [Server 配置](configuration.md)选择。维护连接不使用当前应用 metadata 初始化业务表。OceanBase 须已有 MySQL 模式用户租户和用户数据库；迁移命令不创建集群、租户或数据库。

OceanBase 维护须能通过 `effective_tenant_id()`、`oceanbase.GV$OB_PARAMETERS`、`oceanbase.DBA_OB_TENANTS` 读取租户 ID、集群 ID 和租户创建信息。权限不足或身份信息不完整时返回 `target_identity_unavailable`，不以代理主机名代替数据库身份。

seekdb 只读检查遇到缺失路径或空目录时不启动引擎；非空目录缺少可识别的引擎结构时返回 `unsupported_target`。未显式配置库名、而旧 `test` 库中仍有 PowerContext 表时返回 `explicit_database_required`，不会隐式选择任一数据库；显式选定库名后重跑该命令。已有引擎中缺少所选数据库时，`status` 和 `verify` 返回带库名和准备指引的 `database_missing`；`plan` 不创建数据库，而是返回绑定所选库的 `uninitialized` 计划。操作员确认计划后，`apply` 才可创建数据库并初始化已登记的结构。访问被拒时返回 `database_access_denied`。可写 seekDB 维护无法创建配置指定的库时返回 `database_creation_required`，权限被拒且无法确认库是否存在时返回 `database_access_denied`；两者都在载荷中保留管理员语句与准备指引。打开有效的已有引擎可能写入原生日志和引擎维护文件；只读迁移命令不执行业务 DDL、不初始化表。

`--evidence-dir PATH` 指定目标库之外的持久目录，保存维护进度及恢复引用。`status`、`plan`、`apply`、`verify` 和重试必须使用同一目录；进程、容器或迁移 Job 退出后仍须保留。每个数据库使用独立目录，并限制为迁移操作者可访问。

| 后端 | 证据目录 |
| --- | --- |
| SQLite | 默认在规范化后的数据库文件旁使用 `<数据库文件名>.pc-migration-state`，可用 `--evidence-dir` 覆盖 |
| 嵌入式 seekdb | 默认在规范化后的引擎目录旁使用 `<目录名>.pc-migration-state`，可用 `--evidence-dir` 覆盖 |
| OceanBase | 四个命令均须显式传入 `--evidence-dir` 和 `--lock-coordination single-host`；同一数据库的所有迁移 Job 使用固定维护主机上的同一持久目录 |

OceanBase 在 `<evidence-dir>/<database-id>` 中使用操作系统文件锁，不使用数据库命名锁。所有迁移 Job、重试和检查命令均固定在同一维护主机，并使用规范化后的同一持久目录。业务 Server 可以分布在多节点共用数据库，迁移命令仍只在这一台主机执行。共享文件系统、复制证据目录或其他主机上的同名目录，不代表已支持跨主机互斥；不要在不同主机并发运行迁移 Job。

替换容器或 Job 时也应保持主机名稳定；主机名改变会与持久主机绑定冲突，即使任务仍调度在同一物理节点上。

计划中的 `coordination` 记录 `mode=single-host`、主机名和规范化后的数据库专属目录。更换主机或目录会改变计划，需要重新审阅。显式参数表示选择这一部署约束，不替代停写声明；交互式 `apply` 将它纳入原有的一次确认，不新增询问步骤。缺少证据目录先返回 `evidence_required`；已有目录参数但未选择协调模式时，在连接 OceanBase 前返回 `coordination_required`。

OceanBase 协调使用配置中的直连或 ODP 连接，不依赖物理会话 ID、`PROCESS` 权限或进程列表观察。持久运行记录与恢复证据一起保存在库外，不新增控制表，也不向 Alembic 版本行写特殊锁标识。获得已释放的本地锁不代表旧远端执行或后台 DDL 已结束；PC 不自动核验中断后的远端执行是否结束。

应保留绑定维护主机的 `<evidence-dir>/coordinator.json`、同目录的 `coordinator.lock`，以及数据库专属目录中的 `migration.lock`、`active-run.json` 和恢复摘要。平台不支持原生锁返回 `migration_lock_unsupported`；其他进程持锁返回 `migration_locked`。旧运行尚未确认结束、运行证据损坏或绑定主机不一致返回 `recovery_required`；锁文件或维护连接丢失返回 `migration_lock_lost`。这些错误均不授权删除协调文件或换主机继续。

只有正常返回且迁移主连接成功关闭后，才清理 `active-run.json`。执行中断或主连接关闭失败会保留记录，包括已知 DDL 完成后按 Ctrl+C。只读计划将其 token 展示为 `coordination.pending_run_id`，纳入 `plan_id`，并报告 `state=recovery_required`。用户须先由 DBA 确认该次运行的全部远端执行和后台 DDL 已结束，再重新审阅计划，执行 `apply --acknowledge-previous-run TOKEN` 并提供准确 token。该参数仅作人工声明，不表示 PC 已核验，不自动审批、不绕过结构检查，也不是通用 run ID 续跑。`--yes` 与交互确认均不会替用户填入 token。缺少或不匹配的声明返回 `recovery_required`；没有遗留运行却提供 token 返回 `stale_ack`。参数仅支持 OceanBase 的 `apply`，SQLite 和 seekdb 拒绝该参数。

seekdb 在 `seekdb.env` 中配置路径后使用同一组命令。下面的手动备份交互示例要求先完成备份并停止所有写入者：

```bash
powercontext server db-migrate status --env-file seekdb.env
powercontext server db-migrate apply --env-file seekdb.env --backup manual
powercontext server db-migrate verify --env-file seekdb.env
```

多个 Server 共用 OceanBase 时，先由用户手动备份，部署系统停止所有写入者。以下所有命令都在固定维护主机执行，使用该主机上的同一持久目录，并在计划和执行时使用相同范围：

```bash
powercontext server db-migrate status --env-file oceanbase.env \
  --evidence-dir /var/lib/powercontext/migration-state --lock-coordination single-host
powercontext server db-migrate plan --env-file oceanbase.env --backup manual \
  --shared-database --evidence-dir /var/lib/powercontext/migration-state --lock-coordination single-host
powercontext server db-migrate apply --env-file oceanbase.env --backup manual \
  --shared-database --evidence-dir /var/lib/powercontext/migration-state --lock-coordination single-host \
  --plan-id PLAN_ID --backup-confirmed --maintenance-confirmed --yes
powercontext server db-migrate verify --env-file oceanbase.env \
  --evidence-dir /var/lib/powercontext/migration-state --lock-coordination single-host
```

将 `PLAN_ID` 替换为返回的计划 ID。不能换到空目录绕过恢复错误：中断迁移的证据缺失或不一致时返回 `recovery_required`；选择手动备份或接受不备份风险，也不能授权未知结构或无法证明的 DDL 进度。

OceanBase 执行中断后，保持所有写入者停止，保留同一主机与目录。先由 DBA 确认旧远端执行和后台 DDL 已结束，再检查并审阅恢复计划：

```bash
powercontext server db-migrate plan --env-file oceanbase.env --backup manual \
  --shared-database --evidence-dir /var/lib/powercontext/migration-state --lock-coordination single-host
powercontext server db-migrate apply --env-file oceanbase.env --backup manual \
  --shared-database --evidence-dir /var/lib/powercontext/migration-state --lock-coordination single-host \
  --plan-id NEW_PLAN_ID --acknowledge-previous-run PENDING_RUN_ID \
  --backup-confirmed --maintenance-confirmed --yes
powercontext server db-migrate verify --env-file oceanbase.env \
  --evidence-dir /var/lib/powercontext/migration-state --lock-coordination single-host
```

使用最新计划中的 `plan_id` 和 `coordination.pending_run_id`。重新声明既有手动备份，不用部分状态的新备份替换原恢复点。正常升级仍只确认一次；中断恢复另需用户显式作出 DBA 声明，PC 展示计划时不会自动替用户确认。

## 原生 SeekDB 重启与恢复验收

嵌入式引擎必须使用 `pylibseekdb>=1.4.0.post2,<2`，事务恢复修复由该原生依赖提供。迁移检查保留
`SHOW TRIGGERS`，并在操作之间释放最后一个客户端。分别在 Python 3.11 和 Python 3.14 环境中，从仓库根目录执行：

```bash
uv sync --locked --extra seekdb
uv run --locked --extra seekdb python scripts/probe_seekdb_migration_lock.py \
  --scenario migration --attempts 12 --timeout 180 --output .powercontext/seekdb-migration
uv run --locked --extra seekdb python scripts/probe_seekdb_migration_lock.py \
  --scenario restart-recovery --restart --attempts 12 --timeout 180 --output .powercontext/seekdb-restart
```

每次运行使用新的输出目录。migration 场景选择原 issue 的
`test_mysql_committed_revision_recovers_before_evidence_advance[seekdb]` 节点，保留自然客户端生命周期。释放客户端不代表
原生进程已经退出，紧接着打开可能复用该进程。restart-recovery 场景检查元数据查询、提交、确认最后一个客户端关闭后
进程退出、基线 DDL，以及重开后内容和标签的保留；即使不通过探针调用，该测试也会等待内核进程退出通知。
每次必须恰好一个测试通过，跳过、报错和超时均不算通过。`--restart` 在每次关闭时最多等待 10 秒确认原生进程退出，
再要求各代 PID 不同，且下一次打开之前存在退出记录。等待超时直接失败，不自动重试。
证据包括 Python 和绑定版本、原生二进制 SHA-256、实例生命周期、
SQL 日志、压缩后的原生日志，以及慢执行时的事务快照。独立的延长观察工作流仅用于诊断，不能用它的较长时限替代正常验收时限。

两个显式重开验收均在 Linux 上执行，每次重开前通过内核退出事件确认上一代进程已结束，不使用固定启动等待。
故障库验收应先在隔离的 `1.4.0.post1` 环境中，
使用 `--scenario restart-recovery --keep-failed-storage` 复现阻塞。
停止引擎后，冻结失败输出中的 `tmp/pc-*/seekdb` 目录，并保持原件不变。此场景在 `powercontext` 数据库中创建合成数据，
包括故障库测试要求的 `preserved` 内容和 `Keep` 标签。健康库、结构或哨兵数据不同的库，不能用于证明该缺陷已恢复。
使用同一冻结故障库的独立副本，对比旧引擎与修复后的引擎：

```bash
uv run --locked --extra seekdb python scripts/probe_seekdb_migration_lock.py \
  --scenario fixture-recovery --fixture /absolute/path/to/frozen/seekdb \
  --restart --attempts 1 --timeout 75 --output .powercontext/seekdb-fixture
```

故障库门禁只打开临时副本：创建并提交基线版本表、验证原始数据；重开后再次验证并提交新写入；第三次打开时读回该写入。
故障库原件与旧版失败证据保存在本地；CI 只上传诊断文件，不上传物理数据库。这些门禁不代表完整升级支持，
发布仍须通过安装产物的全部升级矩阵。

## 备份选择

| 策略 | 行为 | 参数 |
| --- | --- | --- |
| PC 自动备份 | 使用支持的数据库原生方法，等待完成并检查；提示大数据量可能耗时较久、占用更多空间 | `--backup auto` |
| 用户已手动备份 | 仅记录声明，不核验文件、任务、时间、目标或可恢复性 | `--backup manual --backup-confirmed`，可加 `--backup-ref BACKUP_ID` |
| 不备份 | 明确接受失败后可能无法恢复原数据的风险 | `--backup skip --accept-no-backup` |

自动备份报告 `completed`，手动备份报告 `user_confirmed`，跳过报告 `skipped`；真正空库初始化报告 `not_required`。自动备份不支持、失败或未完成时停在迁移写入之前，返回 `backup_unsupported` 或备份失败类别，不能自动改成跳过。选择其他策略后需要重新确认计划。

SQLite 安装版本验收套件将自动备份还原到独立数据库，比较完整来源结构和逐表数据摘要，
使用已安装的旧版 Server 读回历史记录，再升级还原副本并验证新版读取、检索和新写入。
启用 Code 的用例还保留独立的不可变缓存。这项演练仅覆盖 SQLite，不证明 seekDB 或 OceanBase 备份可以还原。
在确认自己的还原部署可用前，应保留原始恢复点。

独立 `BackupProvider` 复用后端配置和维护连接。SQLite 使用 Online Backup API 创建独立文件，包含已提交 WAL，并检查文件完整性。seekdb 和 OceanBase 执行器复用 `ForkBackupProvider`，优先采用原生 `FORK DATABASE`。当前执行器拒绝自动 `FORK TABLE`；启用前须单独登记同库备份对象，并完成受影响依赖、跨表一致性和恢复路径的验收。对于需要备份的升级，实际引擎和结构缺少通过验收的恢复证据时，`--backup auto` 在迁移写入前返回 `backup_unsupported`。用户手动备份与明确接受风险的不备份路径不依赖 Fork 支持。PC 不替用户声明已完成验收，也不自动改为物理备份或其他策略。

| 产品 | `FORK TABLE` 起始版本 | `FORK DATABASE` 起始版本 |
| --- | --- | --- |
| OceanBase AI 数据库 | V4.6.2 | V4.6.2 |
| seekdb | V1.1.0 | V1.2.0 |

版本满足只是能力检查的起点，还需检查实际产品、租户模式、权限、对象覆盖、后续 DDL 限制和恢复路径。多个表 Fork 不自动代表整库共同快照；Fork 共享底层存储，不提供磁盘损坏保护。简单表的 Fork 探针不代表四表 bundle 的完整恢复能力；OceanBase 完整 Fork 恢复仍待验收，存在 provider 不等于可用生产升级。

旧业务表、Fork 恢复点和 SQLite 备份本次保留，不在迁移成功、普通启动或重试时删除。旧表删除由后续版本单独 revision 实现；恢复点由显式维护操作清理，均需验证依赖、保留窗口及恢复责任。

本 bundle 的 SQLite `p0003` 会重建标签表，将旧结构和数据保存在 `pc_retained_p0002_artifact_tags`。seekdb／OceanBase 的 `p0003` 原地修改 CHECK，不替换或删除业务表，因此不创建 SQLite 的历史副本。两条路径均不自动删除备份恢复点。

完整 Server 的 `db-migrate status`、`plan`、`apply`、`verify` 在三个后端都会检查所选证据目录中的
Processing 配置维护和 Atomic Memory 投影维护记录。即使数据库结构已符合目标，存在未完成记录时仍返回
`recovery_required`。应使用原配置和恢复证据继续原维护操作；完整迁移不能绕过这项检查。

本地启动只读探测已存在的 SQLite／嵌入式 seekDB 维护锁，不创建或修改锁文件。发现活动锁时，
新运行实例返回 `migration_locked`。SQLite SDK 和后台启动还会检查默认相邻迁移证据目录：未完成的维护、
已登记 revision 校验和变化、必留历史表缺失都会阻止启动。没有该记录的当前结构数据库，只要结构和领域校验通过，
仍可启动，不额外要求其具备未登记的历史保留对象。使用自定义证据目录时，启动服务前必须指定该目录执行显式验证。
任意服务主机无法观察远端 OceanBase 协调主机上的锁。部署编排仍须在整个维护期间停止全部写入并防止重启；
这些检查不会隔离已经运行的客户端，也不能阻止未来发起的维护。

## 服务停止、启动与升级

本机个人服务提供以下命令，保留注册和配置：

```bash
powercontext service stop
powercontext service start
powercontext service restart
```

手动执行 `service stop` 后，可以在新版环境中通过 `service install` 更新已停止的服务注册，无需先启动旧程序。
如果原服务使用环境文件，请继续传入同一个 `--env-file`。更新注册后仍保持停服并禁止自动启动，直到显式执行
`service start`；尚未验证完成的迁移仍会阻止更新注册。

这些命令只管理当前用户归 PC 所有的服务，不管理集群或其他客户端。`stop` 禁止维护期间自动拉起旧服务；`start` 恢复启动。涉及迁移时不能用一次 `restart` 替代停写维护。

`--bundle server` 的 `--manage-service` 将本机自有服务注册和目标可执行程序绑定到待确认的计划。
必须使用注册时的环境文件和数据目录；实际 Server 配置不一致时，在停止服务前拒绝执行。
先停止流量并排空旧任务，再应用计划。应用会抑制自动重启、停止本机服务、取得迁移锁、重新检查并执行备份策略，
完成迁移和数据库验证后才切换注册。原先运行的服务会重新启动并检查 readiness；原先停止的服务保持停止。
迁移失败时保留旧注册和停止保护，不恢复自动启动。

数据库验证与服务启动分别报告。`service_start_failed` 且 `database_state=ready` 表示迁移已成功，
修复启动问题后使用 `powercontext service start`。如果在数据库验证之后、切换服务注册之前中断，
可以重新审阅并应用 `--manage-service` 计划；数据库已就绪时也能完成服务切换，不重复执行已完成的 revision。

`--manage-service` 仅管理已确认的本机服务，不能代替 `--maintenance-confirmed` 对其他写入者和共享库节点的声明。
部分 Artifact 和 Topic recovery bundle 需要变更时返回 `service_unsupported`；已就绪的空操作不触碰服务。
它们不能用于完整 Server readiness 或业务服务切换。
控制器测试使用模拟的原生服务管理器和真实 SQLite 迁移。原生生命周期套件还通过独立服务注册验证安装后的 CLI：
升级已停止服务的冻结 SQLite 基线，确认升级后仍保持停止并可显式启动；同时验证数据库已就绪时运行中服务的维护恢复。
每个操作系统须分别执行原生测试。冻结基线夹具与实际历史版本安装并写入数据库后的升级验收是不同的证据。

套件还在独立环境中安装真实 1.3.0 wheel，通过原生管理器启动，并用 HTTP 创建 Scope。运行中和已停止两种服务状态
均通过公开维护命令升级，验证备份与旧数据保留，并确认注册切换到目标解释器且保持预期运行状态。
CI 安装时校验来源 wheel 的 SHA-256，并设置 `POWERCONTEXT_NATIVE_UPGRADE_SOURCE_PYTHON`；
本地运行原生测试时也需提供该已安装来源版本的解释器。这些用例覆盖 SQLite 服务切换，
后端崩溃恢复与完整安装版本领域验收仍需分别验证。

失败场景覆盖备份存储不可用、目标服务启动时真实的端口冲突，以及替换服务注册文件时注入的 `ENOSPC`。
启动或切换失败后，保留已停止的服务和恢复记录；数据库验证已经完成时，通过显式启动服务恢复，无需再次执行迁移 apply。

手动管理时，先停旧服务，在已支持的完整迁移成功后确认服务启动定义指向新版，再启动。前台部署则退出旧进程，使用新版环境的现有入口：

```bash
powercontext server run --env-file deployment.env --role all
```

## 共享数据库、接口与旧任务

多个 Server 共用一个数据库时，每库只执行一个迁移 Job。部署系统停止流量和任务生产、排空在途请求及需由旧处理器消费的任务、停止全部写入者，并暂停扩容和自动重启。OceanBase 替换的迁移 Job 仍在固定维护主机使用同一个持久 `--evidence-dir` 和 `--lock-coordination single-host`，保留原始恢复引用及运行证据。迁移后协调全部相关节点升级，各节点通过自身 schema、任务和能力检查后才恢复流量。迁移锁只排斥迁移进程，不阻止未知外部客户端。

发布作者应提供受影响对象、执行模式、程序／schema 兼容范围、API 变化和任务格式声明。公共 API 不兼容替换需标记弃用并说明替代入口和移除计划；同包内部入口可与调用方一起替换。保留的 API 可转换请求／响应后共用当前实现，不要求每个 handler 支持两套表结构。

旧格式任务需要在只读计划、停写取锁后、迁移后及 Worker 启动前检查。未知格式或不可观察来源不能假报无任务；应按声明兼容消费、幂等转换或有界排空，并保留任务 ID、幂等键、Scope、重试和领域收据。完整 Server bundle 拒绝 1.3.0 中 queued/running 的旧提示词契约任务并保留终态 payload；对 1.4.0 校验当前任务的身份与格式，保留兼容的排队任务。局部 Artifact bundle 不作完整业务检查的声明。

## 存储层与恢复边界

独立的 Linux OceanBase 引擎恢复测试要求通过 `POWERCONTEXT_TEST_MIGRATION_OCEANBASE_ENGINE_CRASH_ID`
提供隔离容器的完整 ID，并通过 `POWERCONTEXT_TEST_MIGRATION_OCEANBASE_URL` 提供与其匹配的回环端口及专用
`pc_migration_probe_*` 数据库。测试在已登记 DDL 完成后向 observer 发送 `SIGKILL`，从原目录重启 observer，
验证原恢复记录、必需的确认 token、历史数据字节、最终 revision 和重复执行。它不重新执行 slim 镜像的启动脚本，
因为该脚本会展开预置快照并截断日志。CI 要求这一个测试实际执行且零跳过。
测试使用冻结历史资源和操作员备份声明，不能替代安装版本升级验收，也不代表已验证备份可恢复。

维护执行器复用配置，使用专用连接承载 Alembic；冻结历史 SQL 不依赖当前 Repository 或应用 metadata。seekdb 从启动引擎前到关闭引擎后持有规范化目录的锁。OceanBase 在每条 DDL 前后验证操作系统锁、主机绑定、持久运行记录及维护连接。正常结束时先关闭迁移主连接，才清理运行记录并释放文件锁；引擎随后关闭。执行中断或关闭连接失败均保留记录；后续执行需要准确的旧运行 token，以及 DBA 确认远端执行已结束的人工声明。PC 不从本机进程退出或本地锁可获取推断远端执行结束。锁文件被替换、连接丢失或旧运行未确认时阻止继续。平台不支持原生文件锁时拒绝执行，包括运行时降级为软锁的情况，不重连或静默弱化互斥继续迁移。这些锁不阻止业务写入者。

seekdb／OceanBase DDL 可能隐式提交，不能用 Python 事务承诺整个迁移原子化。局部 Artifact 原型只识别已登记顺序：`p0001` 创建冻结四表及两个索引；`p0002` 添加两个可空的 `MEDIUMBLOB` 引用列；`p0003` 删除标签 family 的 CHECK；`p0004` 添加指向精确 revision 的 Artifact handle。继续执行前须用实际结构、数据检查和持久维护证据证明已完成的已知前缀；revision 的后置条件通过后才推进 Alembic 版本。未知对象、结构或中间状态拒绝执行，不乐观 stamp。

MySQL 结构步骤通过冻结的布局片段注册，由 `manifest.json` 的 `mysql_layouts` 列表选择，合起来必须恰好覆盖 Alembic 版本链，且不能重复声明。新增 revision 追加不可改写的片段，并在 `revision_resources` 中声明其文件，不修改已发布片段。可选 checkpoint 将累积步骤与独立冻结的完整结构对照。反射、下一 revision 的已知前缀恢复和版本表校验使用同一注册链；新增 revision 无需修改识别器中的另一份版本列表。这些结构注册不能代替领域转换、兼容性检查或完整 Server readiness。

新增 MySQL 执行入口也在 `manifest.json` 中登记：`mysql_wrappers` 对象将 revision 映射到冻结的 Python 资源，并将该文件列入对应 revision 的 `revision_resources` 依赖。入口声明与公共链一致的 `revision` 和 `down_revision`，调用 `context.config.attributes["execute_revision"](revision)`，不能覆盖历史执行入口。执行器在打开维护目标前验证完整、线性的入口覆盖；缺失或分叉的执行历史直接判为无效 bundle，不等到前序 revision 提交后才发现。入口字节同时纳入计划的 bundle 摘要和该 revision 的执行摘要。

计划通过完整 bundle 摘要绑定注册资源，未完成维护的重试要求使用同一 bundle。已发布 revision 资源及其记录摘要保持不可改写。
无参数 DDL 使用单语句驱动选项，确保 CHECK 的取余运算符和 SQL 字面量中的百分号原样到达后端。

Repository 仅在结构满足后运行。两条完整 Server 来源链均用 `mysql_state_layouts` 登记逐语句的精确结构变化和可恢复前缀。领域步骤附着在共享 revision 上，使用同一连接、锁和恢复点；校验成功后才推进版本。只读 live 校验与迁移当次的保留副本校验分离。向量配置仍需独立登记。

在线就绪校验在同一快照中读取 Atomic Memory 权威记录与投影，不锁定业务 head 行。离线投影修复仍保留加锁状态比较和写入并发保护。

1.4.0 桥接由不可改写的共享资源和 `server_sources/1.4.0` 来源覆盖层组装。执行器根据完整未登记版本的数据库
结构，或主维护日志中原有的来源链选择路径；不新增版本表，也不改写已完成备份的来源 revision。重复维护和
后续已登记版本升级仍使用同一来源链，普通运行时准入继续使用规范目标结构。新增来源需要自己的精确基线及桥接；
仅新增目标 revision 并不会登记中间所有应用发布版本。

单版本表不提供通用 run ID 续跑。中断后先用只读命令检查真实结构和数据；含混状态返回 `recovery_required`，不盲目 stamp、不用部分状态的备份替换原恢复点。库外摘要在 DDL 前记录执行及备份信息；重试保留原维护窗口和恢复点。在恢复条件允许时可显式重新确认 manual／skip 策略，但不绕过结构验证，也不替代必需的进度证据。

恢复独立、显式执行，并保持停写。恢复数据库、必要文件和匹配程序后重新验证；应用回退不等于数据库回滚。manual 不承诺已验证可恢复，skip 不承诺恢复原数据。

## 旧版 Scope 搜索列的启动恢复

现有 Scope discovery 启动迁移会检查 MySQL 兼容存储中搜索列的实际可空性。如果上次启动在添加可空列或回填后中断，
下次启动会根据原文补齐剩余缺失值，再完成 `NOT NULL` 约束。已为 `NOT NULL` 的列不会再次执行 `MODIFY COLUMN`。
Scope 的原始标题、摘要、外部引用和 binding 标识保持不变。此兼容修复仅供未受管理的配置使用。
受管理的 Server 存储使用登记的迁移链和只读启动准入，不会在启动时调用此修复来升级结构。
