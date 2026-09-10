---
title: 迁移 Artifact 后台处理状态
---

已有 Topic Memory 处理数据库必须先完成显式维护迁移，再启动按 Scope 调用的
Artifact Processing Supervisor。全新的空数据库自动初始化。已有数据但没有迁移完成标记时，
普通启动会在后台处理开始前拒绝运行。

## 查看计划并执行

使用迁移后部署所采用的配置查看只读计划：

```shell
powercontext server processing-migrate --action plan --env-file .env
```

先备份数据库，停止全部旧后台候选及 Worker，停用它们的自动重启，并暂停输入写入及显式触发。
命令不能代替操作者完成这些外部条件。随后执行：

```shell
powercontext server processing-migrate --action apply --env-file .env \
  --migration-id rfc1515 --batch-size 100 --maintenance-confirmed
powercontext server processing-migrate --action verify --env-file .env
```

`apply` 按有限步骤分别提交事务。中途退出后，使用相同迁移 ID 和配置重新执行即可；
不要清除迁移标记、收据或旧自动目标来“重新开始”。每个 Scope 的收据与 intent、Topic 目标更新
同事务提交，重复已提交的分页不会再次增加请求计数。MySQL DDL 在恢复时逐步检查实际状态，
不会把“新列已经存在”当作数据迁移完成。

只有验证返回 `ready: true` 后，才恢复 API 写入和后台进程。各副本必须采用一致的运行模式及
binding/Family 映射。普通启动会拒绝未完成迁移或不兼容的部署配置；调整周期、Worker 额度和
超时不要求重新进行模式迁移。

## 保留哪些状态

迁移保留 Topic Pending、Source Cursor 值及 CAS generation、证据和发布数据。未覆盖的 Source
转为普通 dirty；未确认 flush 与未完成自动目标转为已接受调用。Topic 的冻结输入目标仍保存在
领域表中，后来普通 Source 不会扩大已知的旧自动目标。

旧实现可能在自动波次之外启动 catch-up，并在这些调用仍运行时结束原波次。因此，没有 wave 行
不能证明某个 dirty Scope 从未获准运行。没有冻结目标确定边界时，迁移会保守保留旧 Topic Pending
对应的调用责任，由处理器从 Cursor 恢复。关闭新配置中的自动调度不会撤销这些恢复责任。
缺少旧 Pending 的 Source 仅校准为普通 dirty，不因此接受新的业务动作。

旧自动完成时间用作新调度检查基准，保留剩余等待，不从迁移当前时间重新计时。只有替代请求、
Topic 目标与 Cursor 一致性验证通过后，才清理旧自动目标行。旧表和迁移收据保留，运行时不再把
旧表用作活动调度队列。

## 切换 global 与 dedicated

双向切换均采用同样的协调停机和写入暂停流程。修改所有副本的模式配置，查看计划，然后使用
**新的迁移 ID** 执行，例如 `global-to-dedicated-1`；切回也需要新的 ID。迁移会推进并作废旧模式的
全部 Lease generation，再允许新模式启动，同时保留请求计数、dirty、Topic 目标、Cursor 和调度
检查时间，不会重复导入旧处理责任。

SQLite 与 embedded SeekDB 在两种模式下都保留单宿主 `all` 角色；切换模式不会开启这两个后端的
进程角色拆分能力。不支持滚动混用两种模式。
