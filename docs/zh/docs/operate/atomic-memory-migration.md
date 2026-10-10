---
title: 迁移到 Atomic Memory
---

已有 Memory 集合数据库需要停服转换为独立的 `atomic-memory` Artifact。迁移任务为
`powercontext.memory.v1-to-atomic-memory.v1`；它冻结旧内容格式、版本链、身份规则及导入编码，
通过现有配置连接数据库，不启动 Runtime 或 Worker。迁移完成后，旧 Memory 集合只保存在离线归档表
`pc_memory_artifact_archive` 中，不再出现在公共 Artifact 表、检索或任何在线读取路径里。
普通服务启动只做轻量残留检查：公共表中没有旧集合、没有指向旧集合的 lineage，旧 entry 表到公共表的
外键已解除，公共表也不再有旧的 `memory_citations` 列。它不读取归档表或旧 entry 表；逐条完整核验由 `verify` 执行。

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

旧 entry 的精确资源身份上有正式 Owner 时，它迁为新记忆的 Owner；未启用鉴权的部署中没有 Owner 的
entry 迁移后同样没有 Owner。同一 entry 有多个 Owner 或 Owner 不合法时会阻断，集合 Owner 不会分配给
所有新记忆。entry 标签迁为新 Artifact 标签；集合标签保留集合含义。
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
`POWERCONTEXT_SERVER_RUNTIME_MEMORY_WRITE_GATE_ENABLED` 或注入旧 gate 会明确拒绝构造。容量与 compact 旧配置
不约束 Atomic Memory；配置边界见[配置选项](configuration.md#atomic-memory)，接口和 SDK 适配见
[使用 Atomic Memory](../workflows/atomic-memory.md#旧-memory-api-兼容)。

## 归档、旧引用转换与决策文件

apply 按以下顺序执行，每一步都可以在中断后重复：

1. 逐个集合把全部 revision、lineage、head、Owner、标签、授权及授权回执写入
   `pc_memory_artifact_archive`。已归档且内容一致的集合直接复用原快照；归档后集合又发生变化时报冲突，
   不覆盖快照。之后的导入和核验都读取这份快照。
2. 导入每条 entry 的版本链。每个导入 revision 只记录这条 entry 自己的精确 Source 和非集合 Artifact
   依据；第二个及后续 revision 还引用同一新 Artifact 的前一个 revision。
3. 把指向旧 entry 的精确引用改为对应的 Atomic revision：Experience 的 `memory_citations` 转为
   lineage 中的 ArtifactRef；Candidate 的 `memory_citations` 并入 `artifact_refs`；Handoff 正文、
   Work Source（包括 Handoff 回执）中 `kind: memory` 的 citation 改为 `kind: artifact`，
   Handoff 的 lineage 和发布摘要随之更新；Task Outcome 条目摘要变化时，同步重算 recurrence 账本的键。
4. 指向整个旧集合的关系没有单条 Atomic 对应。迁移先把它的原位置和原值记入被引用集合 revision 的
   归档 `incoming_references`，再从在线 lineage、Candidate `artifact_refs`、Task Outcome
   `produced_artifacts`，以及 Handoff 正文和 Work claim/check 中以 `kind: artifact` 引用整个集合的
   citation 中移除。Handoff 回执的 `unavailable_evidence` 记录当时不可用的证据，不表达支持关系：
   其中的集合地址在原字段中保留原值，只描述历史上不可用的对象。读取回执不查归档，
   这些地址不能作为新请求的有效依据；精确 entry citation 仍转换为对应的 Atomic 引用。
5. 已结束的 Dream 运行改为历史格式：原请求、输入清单和请求摘要移入 `historical_data`，
   不再作为可执行请求读取。没有旧格式快照的未结束运行从请求中删除空的旧 entry 引用字段。请求摘要按当前请求
   格式重算，用同一幂等键重试相同请求仍会重放原运行。引用过旧 entry 的请求无法再次提交，其运行保留原摘要，
   复用该幂等键会冲突。
6. 解除保留的旧 entry 表到公共 Artifact 表的外键，然后在旧集合仍在公共表时完整验收：导入历史、
   当前投影、授权转换以及全部引用转换都必须通过，否则不删除任何集合。
7. 先删除所有旧集合自身的 lineage，再删除公共表中的旧集合、集合 head、标签和 Owner，集合之间的
   相互引用不会因标识排序阻断清理。旧 entry 表保留但不再使用。
8. 从 `pc_artifacts` 和 `pc_artifact_candidate_versions` 删除已清空的 `memory_citations` 列。
   最后检查公共表中没有残留。

如果启动时报告公共表仍有 `memory_citations` 列，说明该数据库的引用转换尚未完成，例如中断前的 apply
已经移除了全部集合。保持停服，用相同配置再次执行 apply，它会补完回执、Dream 和删列步骤，可以重复执行。

以下情况在改写任何数据前阻断，并在 plan 中列出：未结束的 Dream 运行固定的输入会被本次迁移改变，
包括旧 entry citation、旧集合，以及正文或 lineage 将被改写的 Artifact 和 Work Source；
旧 Memory Family 的 Candidate 或发布记录；引用无法对应到导入后的精确 Atomic revision；
Handoff 陈述或 verified 的 Work claim/check 移除集合引用后不再有证据；
某个 Candidate 版本删除集合引用后没有任何 Source 或 Artifact 依据。

已经保存旧格式输入清单的未结束 Dream，需要先运行完成再迁移，即使它只选择了没有 Memory 引用的
Experience。旧 Artifact 摘要包含空的旧 lineage 字段，删除该字段也会使快照失效。尚未保存输入清单的
排队任务，在所选输入不受影响时可以迁移；已经使用当前格式保存的快照，在输入不变时仍可继续使用。

如果 Candidate 失去了全部依据，操作者需要通过 `--decisions` 提供决策文件，为每个被阻断的版本指定替代依据：

```json
{
  "format": "powercontext.atomic-memory-reference-decisions.v1",
  "decisions": [
    {
      "carrier": "candidate",
      "scope_id": "team-a",
      "candidate_id": "candidate-1",
      "version": 1,
      "field": "artifact_refs",
      "action": "replace",
      "artifact_refs": [{"family": "experience", "artifact_id": "exp-1", "revision": 2}]
    }
  ]
}
```

```shell
powercontext server atomic-memory-migrate --action plan --env-file .env --decisions decisions.json
powercontext server atomic-memory-migrate --action apply --env-file .env --maintenance-confirmed --decisions decisions.json
```

当前只支持 `replace`。替代依据必须是已存在的精确 Artifact revision，不能是旧 Memory 集合；
决策必须一一对应被阻断的版本，多余或重复的决策同样报错。原引用仍记入归档，并标明使用了替代依据。
部分成功后或全部完成后，可以用同一命令和同一决策文件重跑：已生效的决策按 Candidate 版本当前的
替代依据核对，一致即视为完成，不一致才报冲突。

旧 lineage_only Source 保留原目标，仍可溯源但不进入模型输入。迁移不改变旧 Source 的绑定，
也不生成替代历史时间。

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

旧集合只保存在归档表中，归档表没有在线读取接口。此任务不提供数据库降级；回退数据库应恢复停服升级前的
完整备份。归档、引用转换、移除旧对象和删除旧列由本命令自己执行，不经过 RFC 1771 的迁移执行器：该执行器
目前只覆盖四张 Artifact 表的结构，不能运行数据转换任务。删列只有在引用转换验收通过后才成立，因此没有登记为
RFC 1771 的结构版本。Atomic Memory 内容恢复接口不能替代数据库回退。

## 重建当前检索投影

更换 embedding 模型、profile、维度或归一化方式，或修复当前检索数据时，先备份数据库，停止全部
API、宿主、Worker、自动重启和输入写入，然后使用目标部署配置执行：

```shell
powercontext server atomic-memory-rebuild-projection --env-file .env --maintenance-confirmed
```

命令检查公共表中的旧对象、旧列和外键已清理，且 Atomic 的 head、正文和状态有效。它只重建 active 的
current 行，清除非 active 或孤立的 current 行；head、正文 revision、生命周期状态、保留历史及
Source/处理进度保持原身份和原值。重建不读取归档或旧 entry 表；旧历史是否已完整导入，由停服迁移的
`verify` 负责核验，投影重建不能补做缺失的历史导入。

每条 active 正文在写事务外准备向量。提交前锁定并重新核对精确 revision、state_version 和部署
profile，再在同一个事务中读取最新正式标签。关闭向量时保留正文与全文检索，
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
数量，以及 `archived_collections`、`removed_collections` 和以 `reference_` 开头的引用转换计数。apply 的 `elapsed_ms` 是命令内转换时长，不能代替外部停服总时长。
`legacy_collection_payload_bytes` 和 `atomic_content_payload_bytes` 是正文 payload 字节数，不包含索引、
权限记录、数据库页和复制开销；实际并存空间应由数据库监控记录。

plan、apply 和 verify 读取保留的全部旧历史和 Source/任务快照，首版将这些记录保存在进程内。
这会随历史规模增加读取量和内存消耗。应在备份副本上记录读量、内存峰值、embedding 调用量和总停服
时长，再安排正式维护窗口；这里不声称已经验证生产规模成本。普通启动的残留检查只统计公共表中的
旧对象，不读取旧历史，工作量取决于这些表的数据量和执行计划。

本分支开发过程中创建的 current 表（含读取授权副本或 Owner 列）不属于任何发布版本，初始化时会被
明确拒绝。迁移尚未完成时，apply 会删除并重建这张派生表，结果报告提示随后执行投影重建；迁移已完成
时，停服后直接执行上述投影重建命令。两种方式都只重建这张派生表，并从正式记录恢复活跃记忆，
不改变 Artifact 历史、Family 状态和授权记录。
