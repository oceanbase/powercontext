---
title: 数据库迁移
description: 了解数据库迁移命令、备份策略、服务启停及当前验收范围。
---

# 数据库迁移

统一迁移使用 Alembic 管理版本，只新增 `pc_schema_revision(version_num)` 一张控制表。安装或更新程序与迁移数据库分开；结构变化由随包发布、不可改写的 revision 显式执行。
设计关联 [RFC #1771](https://github.com/oceanbase/powercontext/pull/1771) 和 [issue #1756](https://github.com/oceanbase/powercontext/issues/1756)。

## 当前可用范围

`powercontext server db-migrate status/plan/apply/verify` 使用部署配置中的持久 SQLite、嵌入式 seekdb 或 OceanBase MySQL 模式数据库，不接受任意测试 bundle 路径。维护命令以显式环境文件为准，应使用部署的环境文件，不依赖临时 shell 环境覆盖。随 wheel 发布的冻结资源用于阶段 A 的 Artifact 四表迁移：`pc_artifacts`、`pc_artifact_heads`、`pc_artifact_candidate_versions`、`pc_artifact_tags`。仅接受空目标和已登记的历史结构；完整 Server 数据库含有尚未纳管的对象时拒绝执行。

输出中的 `ready` 仅表示当前迁移 bundle 已通过结构和数据验证；`readiness_scope=registered_bundle`、`server_ready=false` 明确区分它与完整 Server、索引、旧任务及集群可用。不要以此替代生产升级或完整业务库初始化。普通业务启动的全面版本门禁和完整历史基线尚未纳管。已通过 OceanBase AI 4.6.3 的 ODP 入口完成隔离四表验收，覆盖固定维护主机互斥、DDL 提交后中断的显式确认恢复、数据保留与重复执行。这不代表完整 Server 升级、崩溃后远端 DDL 自动判定或完整 Fork 恢复已通过验收。

原有 `server processing-migrate` 保留，请按[迁移 Artifact 处理状态](artifact-processing-migration.md)使用。它尚未自动转发到统一入口。服务部署参考[部署 Server](deploy-server.md)。

### 能力与验收范围

| 能力 | 当前范围 | 尚需完成的验收 |
| --- | --- | --- |
| 四表迁移 | 已登记历史结构、空目标、数据校验及中断恢复 | 每个新增支持的引擎／版本组合须独立验证 |
| 完整 Server 数据库 | 不支持含未纳管对象的完整业务库 | 真实历史发布的完整基线、对象所有权及数据不变量 |
| 旧任务与索引 | 不证明旧任务可消费性或全文／向量投影就绪 | 格式识别、转换／排空、投影重建与业务验证 |
| 启动门禁与旧入口 | 尚未接管完整 Server／Worker／SDK 启动；`processing-migrate` 保持独立 | 初始化前的只读检查，移除已接管的启动 DDL 并统一维护入口 |
| 服务托管 | 独立启停命令可用；此 bundle 不授权自动切换业务服务 | 完整迁移后启动及 readiness 验收 |
| 自动备份 | SQLite Online Backup；未通过完整恢复验收的 Fork 升级备份不可用 | 实际 Fork 恢复及匹配程序运行，不仅检查创建是否成功 |
| 标准变更门禁 | 四表测试不证明所有模型变更都有迁移 | 受管模型差异、对应 revision、冻结资源和三后端验收成为必需检查 |

`server_ready=false` 表示此命令不建立完整 Server 就绪结论，不是对正在运行的服务作健康诊断。阶段 A 的 `ready` 或退出码为零不能用来自动启动业务服务、恢复集群流量或宣告完整升级成功。`apply` 本身不停止外部写入者；操作者须先完成停写，再接受计划。

完整生产升级的启用条件是：登记受支持历史发布的完整数据库与任务格式；完成结构、数据／旧任务和投影迁移；Server、Worker、SDK 在建表及业务初始化前验证兼容性，并移除已接管的隐式启动变更。随后在三个真实后端验证“旧发布建库写入 → 停写 → 迁移 → 新版启动 → API 读写、任务消费与检索”，包含中断恢复和重复执行；自动备份另需实际恢复验收。满足条件后才启用完整服务托管，并将标准变更门禁、贡献指南和 PR 模板一并启用。

## 只读检查与一次确认

下面的 `deployment.env` 示例使用 SQLite；OceanBase 命令还须传入下一节的证据目录和固定主机协调参数。先检查目标或预览计划，这些命令不会创建缺失数据库、父目录或控制表：

```bash
powercontext server db-migrate status --env-file deployment.env
powercontext server db-migrate plan --env-file deployment.env --backup auto
powercontext server db-migrate verify --env-file deployment.env
```

计划绑定目标身份、源／目标 revision、实际结构摘要、冻结脚本摘要、配置摘要、备份策略、服务范围和迁移协调范围。配置与凭据值不输出到诊断中；预期错误返回稳定 JSON 类别和非零退出码。

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

seekdb 只读检查遇到缺失路径或空目录时不启动引擎；非空目录缺少可识别的引擎结构时返回 `unsupported_target`。打开有效的已有引擎可能写入原生日志和引擎维护文件；只读迁移命令不执行业务 DDL、不初始化表。

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

## 备份选择

| 策略 | 行为 | 参数 |
| --- | --- | --- |
| PC 自动备份 | 使用支持的数据库原生方法，等待完成并检查；提示大数据量可能耗时较久、占用更多空间 | `--backup auto` |
| 用户已手动备份 | 仅记录声明，不核验文件、任务、时间、目标或可恢复性 | `--backup manual --backup-confirmed`，可加 `--backup-ref BACKUP_ID` |
| 不备份 | 明确接受失败后可能无法恢复原数据的风险 | `--backup skip --accept-no-backup` |

自动备份报告 `completed`，手动备份报告 `user_confirmed`，跳过报告 `skipped`；真正空库初始化报告 `not_required`。自动备份不支持、失败或未完成时停在迁移写入之前，返回 `backup_unsupported` 或备份失败类别，不能自动改成跳过。选择其他策略后需要重新确认计划。

独立 `BackupProvider` 复用后端配置和维护连接。SQLite 使用 Online Backup API 创建独立文件，包含已提交 WAL，并检查文件完整性。seekdb 和 OceanBase 执行器复用 `ForkBackupProvider`，优先采用原生 `FORK DATABASE`。当前执行器拒绝自动 `FORK TABLE`；启用前须单独登记同库备份对象，并完成受影响依赖、跨表一致性和恢复路径的验收。对于需要备份的升级，实际引擎和结构缺少通过验收的恢复证据时，`--backup auto` 在迁移写入前返回 `backup_unsupported`。用户手动备份与明确接受风险的不备份路径不依赖 Fork 支持。PC 不替用户声明已完成验收，也不自动改为物理备份或其他策略。

| 产品 | `FORK TABLE` 起始版本 | `FORK DATABASE` 起始版本 |
| --- | --- | --- |
| OceanBase AI 数据库 | V4.6.2 | V4.6.2 |
| seekdb | V1.1.0 | V1.2.0 |

版本满足只是能力检查的起点，还需检查实际产品、租户模式、权限、对象覆盖、后续 DDL 限制和恢复路径。多个表 Fork 不自动代表整库共同快照；Fork 共享底层存储，不提供磁盘损坏保护。简单表的 Fork 探针不代表四表 bundle 的完整恢复能力；OceanBase 完整 Fork 恢复仍待验收，存在 provider 不等于可用生产升级。

旧业务表、Fork 恢复点和 SQLite 备份本次保留，不在迁移成功、普通启动或重试时删除。旧表删除由后续版本单独 revision 实现；恢复点由显式维护操作清理，均需验证依赖、保留窗口及恢复责任。

本 bundle 的 SQLite `p0003` 会重建标签表，将旧结构和数据保存在 `pc_retained_p0002_artifact_tags`。seekdb／OceanBase 的 `p0003` 原地修改 CHECK，不替换或删除业务表，因此不创建 SQLite 的历史副本。两条路径均不自动删除备份恢复点。

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

完整升级的托管设计为：排空请求、停服并禁止自动重启 → 取迁移锁并复核 → 按策略备份 → 迁移 → 验证 → 将启动定义切换到已确认新版程序 → 启动原本运行的服务 → readiness。迁移失败保持停服；原本停止的服务保持停止。数据库验证成功而服务启动失败时分别报告，处理启动问题而不重跑迁移。

`--manage-service` 的范围始终只是已确认的本机服务，不能代替 `--maintenance-confirmed` 对其他写入者和共享库节点的声明。阶段 A 的有限 bundle 未完成完整 Server readiness 验收；需要持久化变更时，`--manage-service` 返回 `service_unsupported`，不能据此自动切换生产服务。

手动管理时，先停旧服务，在已支持的完整迁移成功后确认服务启动定义指向新版，再启动。前台部署则退出旧进程，使用新版环境的现有入口：

```bash
powercontext server run --env-file deployment.env --role all
```

## 共享数据库、接口与旧任务

多个 Server 共用一个数据库时，每库只执行一个迁移 Job。部署系统停止流量和任务生产、排空在途请求及需由旧处理器消费的任务、停止全部写入者，并暂停扩容和自动重启。OceanBase 替换的迁移 Job 仍在固定维护主机使用同一个持久 `--evidence-dir` 和 `--lock-coordination single-host`，保留原始恢复引用及运行证据。迁移后协调全部相关节点升级，各节点通过自身 schema、任务和能力检查后才恢复流量。迁移锁只排斥迁移进程，不阻止未知外部客户端。

发布作者应提供受影响对象、执行模式、程序／schema 兼容范围、API 变化和任务格式声明。公共 API 不兼容替换需标记弃用并说明替代入口和移除计划；同包内部入口可与调用方一起替换。保留的 API 可转换请求／响应后共用当前实现，不要求每个 handler 支持两套表结构。

旧格式任务需要在只读计划、停写取锁后、迁移后及 Worker 启动前检查。未知格式或不可观察来源不能假报无任务；应按声明兼容消费、幂等转换或有界排空，并保留任务 ID、幂等键、Scope、重试和领域收据。这些完整业务检查尚未由阶段 A bundle 覆盖。

## 存储层与恢复边界

维护执行器复用配置，使用专用连接承载 Alembic；冻结历史 SQL 不依赖当前 Repository 或应用 metadata。seekdb 从启动引擎前到关闭引擎后持有规范化目录的锁。OceanBase 在每条 DDL 前后验证操作系统锁、主机绑定、持久运行记录及维护连接。正常结束时先关闭迁移主连接，才清理运行记录并释放文件锁；引擎随后关闭。执行中断或关闭连接失败均保留记录；后续执行需要准确的旧运行 token，以及 DBA 确认远端执行已结束的人工声明。PC 不从本机进程退出或本地锁可获取推断远端执行结束。锁文件被替换、连接丢失或旧运行未确认时阻止继续。平台不支持原生文件锁时拒绝执行，包括运行时降级为软锁的情况，不重连或静默弱化互斥继续迁移。这些锁不阻止业务写入者。

seekdb／OceanBase DDL 可能隐式提交，不能用 Python 事务承诺整个迁移原子化。恢复只识别已登记顺序：`p0001` 创建冻结四表及两个索引；`p0002` 添加两个可空的 `MEDIUMBLOB` 引用列；`p0003` 删除标签 family 的 CHECK。继续执行前须用实际结构、数据检查和持久维护证据证明已完成的已知前缀；revision 的后置条件通过后才推进 Alembic 版本。未知对象、结构或中间状态拒绝执行，不乐观 stamp。

Repository 仅在结构满足后运行。完整业务的全文／向量索引版本管理及受控重建、旧任务 readiness 尚未包含在本验收 bundle 中。

单版本表不提供通用 run ID 续跑。中断后先用只读命令检查真实结构和数据；含混状态返回 `recovery_required`，不盲目 stamp、不用部分状态的备份替换原恢复点。库外摘要在 DDL 前记录执行及备份信息；重试保留原维护窗口和恢复点。在恢复条件允许时可显式重新确认 manual／skip 策略，但不绕过结构验证，也不替代必需的进度证据。

恢复独立、显式执行，并保持停写。恢复数据库、必要文件和匹配程序后重新验证；应用回退不等于数据库回滚。manual 不承诺已验证可恢复，skip 不承诺恢复原数据。
