---
title: 数据库迁移
description: 了解数据库迁移命令、备份策略、服务启停及当前验收范围。
---

# 数据库迁移

统一迁移使用 Alembic 管理版本，只新增 `pc_schema_revision(version_num)` 一张控制表。安装或更新程序与迁移数据库分开；结构变化由随包发布、不可改写的 revision 显式执行。
设计关联 [RFC #1771](https://github.com/oceanbase/powercontext/pull/1771) 和 [issue #1756](https://github.com/oceanbase/powercontext/issues/1756)。

## 当前可用范围

本分支提供 `powercontext server db-migrate status/plan/apply/verify`，读取与 Server 相同的部署配置，不接受任意测试 bundle 路径。随 wheel 发布的冻结资源目前用于阶段 A 的 Artifact 四表迁移验收；只有已登记结构的持久 SQLite 数据库可以执行。完整 Server 数据库含有尚未纳管的对象时返回 `unknown_baseline`，seekdb 和 OceanBase 统一迁移返回 `unsupported_backend`。

输出中的 `ready` 仅表示当前迁移 bundle 已通过结构和数据验证；`readiness_scope=registered_bundle`、`server_ready=false` 明确区分它与完整 Server、索引、旧任务及集群可用。不要以此替代生产升级或完整业务库初始化。普通业务启动的全面版本门禁和完整历史基线仍须在三后端验收后接入。

原有 `server processing-migrate` 保留，请按[迁移 Artifact 处理状态](artifact-processing-migration.md)使用。它尚未自动转发到统一入口。服务部署参考[部署 Server](deploy-server.md)。

## 只读检查与一次确认

先检查目标或预览计划；这些命令不会创建缺失数据库、父目录或控制表：

```bash
powercontext server db-migrate status --env-file deployment.env
powercontext server db-migrate plan --env-file deployment.env --backup auto
powercontext server db-migrate verify --env-file deployment.env
```

计划绑定目标身份、源／目标 revision、实际结构摘要、冻结脚本摘要、配置摘要、备份策略和服务范围。配置与凭据值不输出到诊断中；预期错误返回稳定 JSON 类别和非零退出码。

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

## 备份选择

| 策略 | 行为 | 参数 |
| --- | --- | --- |
| PC 自动备份 | 使用支持的数据库原生方法，等待完成并检查；提示大数据量可能耗时较久、占用更多空间 | `--backup auto` |
| 用户已手动备份 | 仅记录声明，不核验文件、任务、时间、目标或可恢复性 | `--backup manual --backup-confirmed`，可加 `--backup-ref BACKUP_ID` |
| 不备份 | 明确接受失败后可能无法恢复原数据的风险 | `--backup skip --accept-no-backup` |

自动备份报告 `completed`，手动备份报告 `user_confirmed`，跳过报告 `skipped`；真正空库初始化报告 `not_required`。自动备份不支持、失败或未完成时停在迁移写入之前，返回 `backup_unsupported` 或备份失败类别，不能自动改成跳过。选择其他策略后需要重新确认计划。

独立 `BackupProvider` 复用后端配置和维护连接。SQLite 使用 Online Backup API 创建独立文件，包含已提交 WAL，并检查文件完整性。seekdb 和 OceanBase 集群的设计采用原生 `FORK DATABASE`，在所有受影响表、依赖和跨表一致性均能覆盖且恢复路径经过验收时可选 `FORK TABLE`。PC 不将不支持 Fork 的远端引擎自动切换到物理备份。

| 产品 | `FORK TABLE` 起始版本 | `FORK DATABASE` 起始版本 |
| --- | --- | --- |
| OceanBase AI 数据库 | V4.6.2 | V4.6.2 |
| seekdb | V1.1.0 | V1.2.0 |

版本满足只是能力检查的起点，还需检查实际产品、租户模式、权限、对象覆盖、后续 DDL 限制和恢复路径。多个表 Fork 不自动代表整库共同快照；Fork 共享底层存储，不提供磁盘损坏保护。当前远端统一迁移尚未通过完整验收，存在 provider 不等于可用生产升级。

旧业务表、Fork 恢复点和 SQLite 备份本次保留，不在迁移成功、普通启动或重试时删除。旧表删除由后续版本单独 revision 实现；恢复点由显式维护操作清理，均需验证依赖、保留窗口及恢复责任。

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

多个 Server 共用一个数据库时，每库只执行一个迁移 Job。部署系统停止流量和任务生产、排空在途请求及需由旧处理器消费的任务、停止全部写入者，并暂停扩容和自动重启。迁移后协调全部相关节点升级，各节点通过自身 schema、任务和能力检查后才恢复流量。迁移锁只排斥迁移进程，不阻止未知外部客户端。

发布作者应提供受影响对象、执行模式、程序／schema 兼容范围、API 变化和任务格式声明。公共 API 不兼容替换需标记弃用并说明替代入口和移除计划；同包内部入口可与调用方一起替换。保留的 API 可转换请求／响应后共用当前实现，不要求每个 handler 支持两套表结构。

旧格式任务需要在只读计划、停写取锁后、迁移后及 Worker 启动前检查。未知格式或不可观察来源不能假报无任务；应按声明兼容消费、幂等转换或有界排空，并保留任务 ID、幂等键、Scope、重试和领域收据。这些完整业务检查尚未由阶段 A bundle 覆盖。

## 存储层与恢复边界

维护执行器复用配置，使用专用连接承载 Alembic 和锁；冻结历史 SQL 不依赖当前 Repository 或应用 metadata。Repository 仅在结构满足后运行；全文／向量索引由版本化资源和受控重建管理。seekdb／OceanBase DDL 可能隐式提交，不能用 Python 事务承诺整个迁移原子化。

单版本表不提供通用 run ID 续跑。中断后先用只读命令检查真实结构和数据；含混状态返回 `recovery_required`，不盲目 stamp、不用部分状态的备份替换原恢复点。库外摘要记录执行结果及备份信息。

恢复独立、显式执行，并保持停写。恢复数据库、必要文件和匹配程序后重新验证；应用回退不等于数据库回滚。manual 不承诺已验证可恢复，skip 不承诺恢复原数据。
