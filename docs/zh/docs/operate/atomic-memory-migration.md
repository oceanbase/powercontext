---
title: 迁移到 Atomic Memory
---

已有 Memory 集合数据库需要停服转换为独立的 `atomic-memory` Artifact。迁移任务为
`powercontext.memory.v1-to-atomic-memory.v1`；它冻结旧内容格式、版本链、身份规则及导入编码，
通过现有配置连接数据库，不启动 Runtime 或 Worker。普通服务启动只核验导入与当前数据的一致性。

## 执行顺序

先使用新版部署的配置查看只读计划：

```shell
powercontext server atomic-memory-migrate --action plan --env-file .env
```

计划扫描全部 Scope、全部旧 Memory 容器的集合历史与 entry 历史，包括 inactive 和 compact 条目。
输出为 JSON，包含 `counts`、`errors`、`ready` 与 Source/处理进度快照摘要。
`pending_entries` 表示尚未转换的逻辑身份；没有错误但仍有待转换条目时，`ready` 为 false。

SQLite 的 plan 和 verify 要求数据库已存在且持久化。普通文件 URL 与 SQLite file URI 都使用
`mode=ro` 连接；命令只设置连接参数，不创建目录、初始化 schema 或改变数据库的日志模式。
路径不存在或不可读时明确失败。`:memory:`、`mode=memory` 的 file URI 和空 file URI 等进程内
或临时数据库会被拒绝。检查连接忽略 URI 中的 `immutable` 和 `nolock`，保持正常锁协调并读取
WAL 中已提交的数据。SQLite 的 WAL 协调可能使用或创建 `-wal`、`-shm` 辅助文件。

备份数据库，停止全部旧 API、宿主、Worker 及其自动重启，暂停 Source 输入、手工写入和显式触发。
维护确认参数表示操作者已经完成这些条件；命令不会停止外部进程。随后执行：

```shell
powercontext server atomic-memory-migrate --action apply --env-file .env --maintenance-confirmed
powercontext server atomic-memory-migrate --action verify --env-file .env
```

只有 `verify` 返回 `ready: true` 后才能启动新版服务、恢复写入和后台处理。使用有向量的部署配置时，
apply 按正常投影发布流程调用配置的 embedding 服务；只为当前 active 条目准备向量。
无向量配置仍创建和更新全文索引。plan 和 verify 不调用 embedding 服务。

如果处理调度 schema 尚未完成其自身升级，应先按
[Artifact 后台处理状态迁移](./artifact-processing-migration.md)完成该任务。
它的完成标记只证明处理调度 schema；Atomic Memory 通过自己的历史和导入对象核验就绪条件。

## 转换规则与阻断条件

新身份由 `(scope_id, old_memory_artifact_id, entry_id)` 通过固定 UUIDv5 规则生成。
不同容器使用相同 entry_id 不会共用新身份。新 revision 等于旧 entry 的 version；其他条目导致的集合
revision 不会产生该条记忆的新 revision。

导入检查版本从 1 连续增长、前驱指针、内容摘要、精确身份和集合 manifest 的引用。
当前 manifest 或 compact 前最后一次出现的指针必须指向本 entry 的版本链末端。
缺失变化记录、无法解释的缺席、重复身份、跳过版本或落后的 head 都会阻断；迁移不会选取最大版本
来掩盖不一致。旧 active、inactive、compact 分别成为 active、forgotten、retired。
状态版本计数与公共 head 的治理摘要同步。

正式 Owner 必须存在于旧 entry 的精确资源身份上。缺失、冲突或不合法的 Owner 需要操作者先修复，
集合 Owner 不会分配给所有新记忆。entry 标签迁为新 Artifact 标签；集合标签保留集合含义。
共享按精确 entry 资源转换，保留原 binding_id、主体、角色、有效期、撤销信息、授权来源和幂等字段。
不带 entry selector 的旧 Memory 授权不是当前支持的共享格式，会明确阻断。

创建授权的幂等回执包含资源身份，迁移时会核对原请求摘要，并将其转换为新身份对应的摘要。
原请求使用相同幂等键重试时仍返回同一条授权；更换主体、角色、有效期、理由或资源会继续报冲突。
撤销和替换授权的回执不含资源身份，保持原值。回执缺失、关联不符或摘要无法核实时会阻断迁移。

当前为 custom 的旧 `memory.extract` Prompt 会阻断。操作者需要明确将旧 Prompt 当前模式设置为 Auto，
并按新输入和输出契约配置 `atomic_memory.extract`、`atomic_memory.reconcile`；新 Prompt 可使用 Auto。
旧 Prompt 历史继续保留。旧的自定义 CandidatePipeline 需要改为 AtomicMemoryGenerationPipeline。
迁移不会将自定义指导或样例默认为新默认值。未知 cursor 或持久任务字段，以及未处理的旧 Memory
candidate，也会阻断，必须先按原契约明确处置。


旧 MemoryWriteGate 依赖集合写入契约，不能注入 Atomic Runtime；开启
`POWERCONTEXT_SERVER_RUNTIME_MEMORY_WRITE_GATE_ENABLED` 或注入旧 gate 会明确拒绝构造。
低层旧 MemoryService 可独立使用该 gate，但它不是新版服务的 Atomic 写入口。容量与 compact 旧配置
不约束 Atomic Memory；配置边界见[配置选项](configuration.md#atomic-memory)，接口和 SDK 适配见
[使用 Atomic Memory](../workflows/atomic-memory.md#旧-memory-api-兼容)。

## 证据、恢复与重试

旧集合 Artifact、entry version、citation、Source 和已有生命周期区间全部保留。
每个导入 revision 引用产生该 entry version 的精确旧集合 revision，并保留旧 Artifact 依据。
读取时，根据该集合的 manifest 和不可变 entry version 核验确定性的 Atomic 身份与 revision，
只展开这条 entry 原有的精确 Source 和 Artifact 依据。集合锚点仍是可读取的历史记录，
同集合其他 entry 的 Source 不会成为这条记忆的依据。第二个及后续 revision 还引用同一新 Artifact
的前一个 revision，使旧 entry 累积的依据沿精确版本链保持可达。Dream 和自动抽取使用相同的 entry 选择规则；
普通记忆明确引用旧集合时，保留该引用原有含义。旧 lineage_only Source 保留原目标，
仍可溯源但不进入模型输入。迁移不改变旧 Source 的绑定，也不生成替代历史时间。

已经完成此迁移的数据库也适用这一读取规则。升级读取代码即可修正证据解析，不改写已导入内容或 lineage 行；
plan、verify 和重复 apply 继续核验同一不可变导入表示。

Source Cursor、CAS generation、高水位、pending/flush 请求、已接受任务和旧调度键保持原值。
`memory` Family 与 `memory-source-window` binding 继续作为调度兼容身份。
迁移完成前失效旧 Lease 并推进 fence，已消费的 Source 不重新抽取。

每条记忆的历史、head、状态、Owner、标签、授权转换与当前投影在同一个事务提交。
向量准备在事务外完成。中途退出后，可以使用相同配置重复 apply；已提交对象先核验精确导入历史。
与确定性身份对应的内容不同、存在孤立状态或历史时明确报错，不覆盖目标数据。
已经导入的对象后续产生新 revision 或生命周期变化时，迁移不回退其 head、状态或标签。

如果已转换的授权仍保留旧资源摘要，plan 和 verify 会返回 `ready: false`，并在
`pending_grant_receipts` 中报告待修复数量。保持停服，重复 apply 即可修复这些回执；
`migrated_grant_receipts` 报告本次修复数量，授权身份、撤销状态和审计记录保持不变。

此任务不删除旧历史，也不提供数据库降级。回退数据库应恢复停服升级前的完整备份，遵循发布时
对 RFC 1771 的升级和降级说明。Atomic Memory 内容恢复接口不能替代数据库回退。

## 重建当前检索投影

更换 embedding 模型、profile、维度或归一化方式，或修复当前检索数据时，先备份数据库，停止全部
API、宿主、Worker、自动重启和输入写入，然后使用目标部署配置执行：

```shell
powercontext server atomic-memory-rebuild-projection --env-file .env --maintenance-confirmed
```

命令要求旧历史迁移已完成，且权威 head、正文和 Family 状态有效。它只重建 active 的 current 行，
清除非 active 或孤立的 current 行；head、正文 revision、生命周期状态、保留历史及 Source/处理进度
保持原身份和原值。它不能修复或绕过缺失的旧历史导入。

每条 active 正文在写事务外准备向量。提交前锁定并重新核对精确 revision、state_version 和部署
profile，再在同一个事务中读取最新正式标签、Owner 和直接读取授权。关闭向量时保留正文与全文检索，
清除 embedding、profile 和输入摘要。SQLite 的全文辅助索引使用稳定的 Scope/Artifact 身份 token，
不依赖 `VACUUM` 可能改变的 rowid。

OceanBase 和 seekDB 的向量维度改变时，先在停服状态下清除派生向量、替换原生向量索引并调整
current 列的维度。该 DDL 可能独立提交，整个过程必须保持维护条件。维度相同但 profile 改变时，
也会按目标 profile 为全部 active 行重新准备向量；不保留历史向量缓存。

各行分别提交。中断后可能留下部分投影，保持停写并重复执行相同命令即可；重试会重新准备全部
active 行，不使用进度表续跑。`--batch-size` 控制每批读取的身份数量（1–1000，默认 100），不是
向量结果上限。JSON 输出包含 `profile_fingerprint`、`active_rows`、`obsolete_rows`、`rebuilt_rows`、
`removed_rows`、`embedding_calls` 和 `elapsed_ms`。完整 current 核验返回 `ready: true`，且普通启动
检查成功后再恢复服务。错误不会悄悄启用另一种检索模式。

当前有结果上限的普通向量搜索，也会对满足资格的 current 行计算精确 L2；资格过滤先于排序和截断。
相关记忆完整枚举使用同一精确计算，不设结果上限，返回阈值内全部合格结果。计算量随合格行数与向量
维度的乘积增长。原生向量索引已配置，但这些路径没有使用 ANN；本实现不承诺 ANN 性能，也不代表
已完成生产后端验收。

## 资源记录

输出包含集合数、集合历史 revision 数、逻辑 entry 数、entry version 数、各状态数量、已导入和已核验
数量。apply 的 `elapsed_ms` 是命令内转换时长，不能代替外部停服总时长。
`legacy_collection_payload_bytes` 和 `atomic_content_payload_bytes` 是正文 payload 字节数，不包含索引、
权限记录、数据库页和复制开销；实际并存空间应由数据库监控记录。

计划及启动就绪检查读取保留的全部旧历史和 Source/任务快照，首版将这些记录保存在进程内。
这会随历史规模增加读取量和内存消耗。应在备份副本上记录读量、内存峰值、embedding 调用量和总停服
时长，再安排正式维护窗口；这里不声称已经验证生产规模成本。
