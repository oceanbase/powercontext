---
title: 数据库迁移设计
description: 了解统一迁移的设计、共享数据库升级、备份选择、服务启停与兼容性检查。
---

# 数据库迁移设计

PowerContext 的统一数据库迁移方案使用 Alembic 管理结构版本，安装新版与迁移已有数据库分开。本文介绍
[统一迁移 RFC](https://github.com/oceanbase/powercontext/pull/1771) 的运维契约，关联
[issue #1756](https://github.com/oceanbase/powercontext/issues/1756)。

## 当前可用范围

**统一迁移仍处于原型阶段。本文中的 `server db-migrate`、`BackupProvider`、`service start/stop/restart`
及 `--manage-service` 是拟议设计，尚不能作为已发布命令使用。** 原型只验证隔离的四表测试库，不接入业务启动，
其 `ready` 不代表完整 Server 可用；原型中的执行记录也不代表最终单表持久化设计已落地。

当前前台启动入口是 `powercontext server run`，个人服务命令提供 `install/status/uninstall`。
已有 Artifact 处理状态的维护请使用[迁移 Artifact 处理状态](artifact-processing-migration.md)中的
`server processing-migrate`，不要用原型替代生产迁移。服务部署参考[部署 Server](deploy-server.md)。

## 版本与更新边界

最终框架只新增 `pc_schema_revision(version_num)` 一张迁移控制表，沿用 Alembic 的版本读写；不新增通用执行、
步骤、任务台账。每次受管结构变更新增独立且不可改写的 revision，沿依赖链执行；没有结构变化的发布无需新脚本。
业务版本、API 版本和 schema revision 分开管理。数据任务、验证器与影响声明随包发布，日志和备份记录保存在库外。

安装或升级软件包不迁移已有库；本地库、远端库均通过显式命令迁移。真正空白的本地库允许在配置许可且持锁确认后
初始化；远端库和多节点共享库使用单独初始化任务。已有库不兼容时新版拒绝业务启动，提示 `migration_required`。
框架启用后，所有受管结构变更 PR 都必须提交 migration 和验证；框架与 PR #1716 独立推进。

## 一次确认完成维护

交互用户可以直接执行拟议的 `apply`，不用先手工拼装多个迁移步骤。以下示例供理解接口，当前不可直接执行：

```bash
powercontext server db-migrate apply --env-file deployment.env --manage-service
```

它汇总目标库、来源／目标版本、受影响的表和接口、旧任务、停机要求、备份与启停范围。用户在同一页面选择后确认一次，
随后执行停写、取锁复核、备份策略、结构与数据转换、索引重建和最终验证；不逐条 SQL 确认。
只有只读检查且没有持久化变更时，返回“无需变更”，不备份、不启停服务。

希望提前审阅时使用拟议的只读 `status`、`plan`；出现实质变化时需要重新确认。自动化场景明确提供计划与策略，
例如用户自行备份、部署系统已停止全部写入者后：

```bash
powercontext server db-migrate plan --env-file deployment.env --backup manual
powercontext server db-migrate apply --env-file deployment.env --plan-id PLAN_ID --backup manual --backup-confirmed --maintenance-confirmed --yes
```

`--yes` 仅接受该计划，不等于手动备份声明或接受无备份风险。计划绑定数据库、版本、结构和脚本摘要、兼容性、
备份选择与启停范围；缺失所需确认返回 `confirmation_required`。任何备份选择都不能绕过锁、活跃写入者或数据检查。

## 多个 Server 共用一个数据库

本方案的多节点指共享一个数据库；不设计每个节点拥有独立数据库的升级。每个数据库只运行一个迁移 Job。

1. 在独立环境准备新版程序；禁用新请求和任务生产，按计划排空在途操作及必须由旧处理器消费的任务。
2. 停止全部 API、Worker、调度器和 SDK 写入者，暂停自动扩容、自动重启及会拉起旧程序的调度。
3. 一个新版迁移 Job 执行 `apply`，取得数据库级锁，复核计划和旧任务后处理备份并迁移。
4. 迁移返回 `ready` 后启动新版节点，各节点仍须检查 schema、任务格式及必要能力，通过 readiness 才恢复流量。
5. 迁移失败保持维护；数据库已就绪但某个节点启动失败时，单独处理启动问题，不重跑迁移或自动降级。

迁移锁只排斥其他迁移进程，不能自动阻止任意外部客户端写入。`--maintenance-confirmed` 是运维停写声明。
无法建立停写条件时拒绝执行。PC 升级不重启 OceanBase 数据库集群。 SQLite／嵌入式 seekDB 仍受现有单机 `all` 角色限制，迁移设计不使其获得跨主机共享目录或多节点部署能力；共享数据库的多节点服务使用已有支持的远端部署方式。

## 备份选择与原生方法

| 选择 | 行为 | 拟议参数 |
| --- | --- | --- |
| PC 备份 | 推荐支持的原生方法，提示大数据量可能耗时较久、占用更多空间，等待完成与适配器检查 | `--backup auto` |
| 已手动备份 | 仅记录用户声明，不检查文件、备份任务、时间、目标或可恢复性；引用可选 | `--backup manual --backup-confirmed`，可加 `--backup-ref BACKUP_ID` |
| 不备份 | 提示失败后可能无法恢复原数据，明确接受风险才继续 | `--backup skip --accept-no-backup` |

手动模式显示 `user_confirmed`，不显示 `verified`；跳过显示 `skipped`；真正空库显示 `not_required`。
PC 自动备份失败、未完成或不支持所需对象时，迁移停在写入之前。改变策略需重新确认，不能自动跳过。

独立 `BackupProvider` 属于数据库维护能力，复用 Profile 的配置、目标身份和引擎生命周期，由后端适配器实现：
`capabilities(context)` 报告能力，`create_backup(context)` 发起备份，`inspect_backup(ref)` 查询 PC 备份，
`restore_plan(ref)` 提供显式恢复步骤。手动和跳过分支不调用它核验用户备份。

| 后端 | 推荐方法 | 限制 |
| --- | --- | --- |
| SQLite | 停写后使用 SQLite Online Backup API 创建独立数据库文件，检查可打开及完整性 | 包含已提交 WAL；不能只复制主文件；恢复验证还包含扩展、索引和业务数据 |
| seekDB | 经过版本、对象覆盖和恢复验收的 `FORK DATABASE`，保存同实例迁移前恢复点 | 不默认逐表 fork；同实例共享存储，不提供磁盘损坏保护 |
| OceanBase | 数据库原生物理备份和日志归档，通过配置好的管理接口或备份平台发起并查询完成 | 检查任务和日志覆盖；租户级恢复可能影响其他应用，不能默认覆盖整个共享租户 |

原生能力参考：[SQLite Backup API](https://sqlite.org/backup.html)、
[OceanBase 备份架构](https://en.oceanbase.com/docs/common-oceanbase-database-10000000001168918)。

seekDB V1.2.0 发布说明提供整库共同快照能力，部分非表对象及跨库外键不复制。Fork 使用写时复制并共享底层存储，
因此经验证后可以用于引擎／存储健康情况下的迁移失败恢复，不要求同时另做物理备份；独立灾备由用户按需安排。
逐表 `FORK TABLE` 不能默认替代整库一致性保护。
[seekDB V1.2.0](https://github.com/oceanbase/seekdb/releases/tag/v1.2.0)、
[Fork 机制](https://en.oceanbase.com/blog/fork-table-ready-for-agents)。

自动 fork 启用前必须验收实际嵌入式版本、索引／约束覆盖、源库后续 DDL、引擎重启和完整恢复。
当前 `SeekDBConfig.database` 固定为 `test`，不能直接承诺修改连接指向 fork 库；需要实现恢复到原库名，
或另行提供可配置目标及统一切换。`MERGE TABLE` 不用于自动回滚 schema。对象覆盖或恢复路径不满足时返回
`backup_unsupported`，可重新选择手动或跳过，不复制运行中的目录，也不自动升级数据库引擎。

## 服务启停与重启

不兼容升级采用：停止旧写入者 → 备份策略 → 迁移 → 验证 → 启动新版 → readiness → 恢复流量。
安装新版不等于切换进程；延后切换时保留可独立运行的旧环境。

拟议的 `service stop/start/restart` 只管理当前用户归 PC 所有的本机服务，并保留注册和配置。
`--manage-service` 显示并确认目标可执行程序、配置及原运行状态，自动排空停服、抑制重启；只在迁移成功后
更新已确认的启动定义并恢复原本在运行的服务。原本停止的服务保持停止，失败不自动拉起旧服务。
不按 PATH 猜新版本，不使用可能立即启动服务的 `service install` 作为维护中间步骤。

集群和外部服务由部署系统启停。手工维护时，拟议命令是先 `powercontext service stop`，执行 `apply` 成功后，
确认服务定义已指向新版，再运行 `powercontext service start`。`restart` 不能替代停服期间的数据库迁移。
前台服务在迁移成功且旧进程退出后，通过现有命令从新版环境启动：

```bash
powercontext server run --env-file deployment.env --role all
```

## 表、接口与旧任务的发布判断

变更作者提供随包影响清单，操作者据此选择部署方式，不能仅靠“改了表”或“内部接口”猜测：

| 声明 | 运维判断 |
| --- | --- |
| `affected_objects` | 表、约束、索引和外部文件范围，大表复制、资源与不可逆转换 |
| `execution_mode` 与程序／schema 支持范围 | 无持久化变更可普通发布；不兼容变更默认停写维护；在线迁移必须有已实现且验证过的后端与混合版本支持 |
| `api_changes` | 是否内部同步发布，是否独立调用方，旧接口保留／弃用及最早移除版本或日期 |
| `task_formats` | 待执行、运行中、延迟、重试和死信任务的版本，兼容消费／幂等转换／排空方式 |

操作者可选择更保守的停机方式，不能选择“在线”来绕过已知不兼容。首期不承诺通用在线 DDL。
内部同包入口可随调用方一起替换；独立部署的 Worker／SDK 即使称为内部也需要兼容安排。
Artifact 等公共 API 不兼容替换时标记 `deprecated`，说明替代入口和移除计划；仅表变化而 API 契约不变无需弃用。
保留的旧 API 可转换请求／响应后共用当前实现，不要求每个 handler 同时兼容新旧表。

**升级检查旧格式任务。** `plan` 只读统计相关队列、Lease 和 payload 格式，停写取锁后、迁移后及 Worker 启动前复查。
没有格式版本字段时用冻结识别器；未知或不可观察的来源阻止相关迁移／Worker，不假报无任务。
未知格式返回 `unsupported_task_format`，未排空返回 `legacy_tasks_pending`。先停生产，再让旧 Worker 有边界地排空，
最后停 Worker 才执行 DDL；或按计划兼容消费／幂等转换。保留任务 ID、幂等键、Scope／身份、重试和领域收据，
不删队列或把未完成任务标成功。移除旧处理器前必须处理遗留任务；外部 API 调用方升级由发布计划协调。

## 与现有存储层结合

| 现有层 | 升级中的职责 |
| --- | --- |
| Profile | 复用连接配置和引擎生命周期，拆出无 `create_tables` 副作用的 inspection／maintenance 入口 |
| `AsyncDatabase` | 提供专用维护连接及事务；`AsyncConnection.run_sync` 将同一连接交给 Alembic，锁和 DDL 不换连接 |
| Alembic | 冻结 revision 执行 DDL，验证后推进唯一版本表 |
| Repository | schema 满足后复用稳定领域操作；历史转换优先冻结 SQL／映射，不依赖不断变化的当前 Repository |
| 索引 | 结构定义进入版本资源，受控重建派生数据并检查，普通启动不隐式改表 |
| 启动门禁 | 先检查 schema、任务和必要能力，再装配 Repository、索引和 Worker |

例如新增必填列：先 revision 加可空列，数据脚本分批幂等回填，校验后下一 revision 收紧约束，重建相关索引，
最后才启用新版 Repository。seekDB／OceanBase DDL 可隐式提交，不能用一个 Python 事务假装整个升级原子化。
已有领域收据与对应数据批次一起提交，索引从权威数据重建。

## 中断与恢复

中断后只读检查 `status`、`verify`、`plan`，仅在实际状态证明可安全重试时继续。单一版本表不提供通用按 run ID
续跑；含混状态返回 `recovery_required`。auto 保留原始恢复点和库外记录，不用部分迁移后的备份覆盖它；manual
仍不核验，skip 不承诺恢复原数据。所有模式均检查真实结构、数据和任务，不盲目 stamp。

恢复独立、显式执行并保持停写；恢复数据库、必要文件及匹配程序后验证。应用回退不等于数据库回滚。
迁移 `ready` 与服务启动结果分别报告，不在成功后立即删除恢复点。生产支持仍以 RFC 的实际后端验收为准。
