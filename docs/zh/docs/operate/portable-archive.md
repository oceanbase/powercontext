---
title: 导出和恢复可移植归档
description: 创建、验证并恢复经过校验的 PowerContext 逻辑归档。
---

# 导出和恢复可移植归档

可移植归档是用于迁移或恢复完整 PowerContext scope 的 `.pcb` 文件。它是逻辑归档：保存领域 identity 和不可变历史，
而不是复制 SQLite 文件。它适用于受控的本地备份、离线传输，以及未来的后端迁移。

这是一个离线运维命令，不调用远程 Server API。不传 `--env-file` 时打开默认 SQLite 数据库；传入后使用该部署的
SQLite、SeekDB 或 OceanBase 配置。恢复前应停止 PowerContext 写入进程；导出使用一个数据库 transaction 获取一致的
逻辑 snapshot。MySQL 模式的后端会为导出事务使用 `REPEATABLE READ`，即使部署默认采用
`READ COMMITTED`；后续事务仍使用部署的默认隔离级别。

## 选择恢复格式

需要完整数据库、部署本地状态或时间点恢复时，应使用部署原生备份。`.pcb` 解决的是更窄的需求：在受支持的后端之间迁移
选定的完整 scope，保留原始 identity、revision、evidence 和重试语义，但不复制访问绑定、宿主本地选择、调度状态和派生索引。
SQLite 数据库副本不能直接作为 OceanBase 数据库使用；回放后端的 SQL dump 也不会执行这些应用层的范围选择和投影重建。
schema migration 负责升级现有部署的表结构，并不替代这种 scope 迁移操作。

Checksum 用于发现数据损坏，不提供生产者身份认证，也不证明导出内容相对源数据库完整。恢复前仍会执行领域解码和依赖校验。

## 前提条件

从创建本地数据的同一个 PowerContext revision 安装 CLI。归档命令使用
`POWERCONTEXT_HOME/powercontext.db` 中持久化的 SQLite 数据库；未设置 `POWERCONTEXT_HOME` 时，PowerContext
会使用操作系统用户数据目录。

```bash
uv tool install "powercontext[cli] @ git+https://github.com/oceanbase/powercontext.git@master"
export POWERCONTEXT_HOME="$HOME/.local/share/powercontext"
```

归档包含恢复所需的内容和元数据。应按项目数据的访问控制和保留要求，将它放在受保护的位置。格式会排除凭据和已配置的
provider secret，但不会加密项目内容。

## 创建并检查归档

传入每一个需要包含的完整 scope。不能用前缀选择 scope，也不能只导出某一种 record。

```bash
powercontext archive export \
  --scope-id project:payments \
  --output ./backups/payments.pcb

powercontext archive inspect ./backups/payments.pcb
```

外层存储已经压缩时可增加 `--no-compress`。导出授权一定发生在第一次数据库查询之前；离线命令以操作系统文件权限和数据库
读取权限作为 operator authority，不复用或导出 Server bearer token。无内容的 JSON 进度写到 stderr，最终 receipt
仍以 JSON 写到 stdout。

`export` 会输出 JSON，其中包括 bundle ID、record 数和 checksum。`inspect` 不打开目标数据库，只校验 ZIP 结构、
单 record digest、总 digest、record 数和所选 scope。若外部备份系统需要独立校验记录，请把输出的 checksum 一并保存。
导出在发布文件前还会检查格式、资源限制和依赖完整性。验证失败时，输出路径上的旧备份保持不变。
输出路径不能是源 SQLite 数据库或对应的 `-wal`、`-shm`、`-journal` 文件；导出会拒绝这些路径，不会用归档替换正在使用的存储。同目录中的这些保留文件名不区分大小写，在大小写敏感的文件系统上也一样。

## 在恢复前验证

恢复前始终先针对目标环境验证：

```bash
powercontext archive restore ./backups/payments.pcb --dry-run
```

dry-run 不会写入领域数据。它会校验 checksum、必需的 Source 和 Artifact 依赖，以及已配置 Runtime 是否支持归档中的
source type 和 Artifact family。归档无效或无法检查本地 SQLite 文件名时返回退出码 `2`，不兼容或目标冲突时返回 `3`；
错误信息不打印 record body。成功报告
包含 `already_present`、`conflicts`、所需和不支持的 Source type/Artifact family，以及目标是否支持 projection rebuild。
可用 `--env-file ./target.env` 检查另一个部署。

Candidate version 和 decision head 使用正常读取路径的完整解码器，校验 family 一致性、evidence、原因、生命周期状态，
以及 family 自有的 proposal 协议和可选的 Dream audit envelope。格式错误或不支持的 Candidate 会在写入领域数据前被拒绝。
已有 SQLite 目标以只读方式检查，支持带 `uri=true` 的 `file:` URI 文件名。
目标不存在时，dry-run 不会创建数据库文件或父目录。
这包括 Windows 的绝对 `file:///C:/...` 路径及经过百分号编码的文件名。不支持的 URI authority、无法读取的目标、目录目标
或非目录的父路径会使验证失败，不会被当成空数据库。

发布关系验证会沿精确的 Artifact Revision 地址检查完整来源链。即使所有端点和 checksum 都有效，链中的环和不一致的
content digest 仍会在领域写入前被拒绝。兼容性检查也会比对待恢复 Revision 在目标中已有的发布关系。归档未声明的目标发布
关系即使自身合法，也会作为冲突拒绝，防止恢复后的原始来源被改变。关系图使用本地临时存储建立索引，目标关系按批读取，
不扫描无关的发布历史。存在冲突时不会覆盖目标历史；已经损坏的历史需要使用有效备份恢复到干净目标。

标签及完整标签集合使用正常领域校验，每个标签目标最多包含 32 个标签。新标签 identity 会追加到目标，不删除已有标签，
合并后的集合也必须满足同一上限。归档内的标签集合超限会作为无效输入拒绝；与目标合并后超限则作为冲突在写入前拒绝。

原生 Source 需要目标环境安装对应的 Python adapter。验证会在写入前使用正常读取路径的已注册 Source 解码器，检查 payload
结构与索引 identity 是否一致，也覆盖受支持的 legacy payload。远程 worker 已物化的 `SourceObservation` 自带捕获内容和投影，
其存储 envelope 也使用同一解码器验证，但不需要加载 worker adapter。归档只携带所选 scope 实际引用的 Source Definition
manifest，并在恢复前校验 observation
的身份和定义指纹。缺失定义或与目标定义冲突会阻止恢复；未引用的全局定义不会随包导出。
Observation 的 payload 和每个 projection 还必须通过正常写入路径所使用的 manifest schema 校验。恢复不会请求远程 schema。

要执行写恢复，必须显式确认：

```bash
powercontext archive restore ./backups/payments.pcb --yes
```

结果为 JSON，包含 `inserted`、`already_present` 和 `projections_ready`。只有 `projections_ready` 为 `true`
时才能宣称搜索已就绪。Runtime 会在恢复权威数据后重建 Memory、Topic Memory 和 Experience 搜索投影，
包括已配置的 Memory 和 Topic Memory 向量投影。

## 归档保留的内容

格式版本 1 支持以下可移植关系记录：

| 会保留 | 不可移植 |
| --- | --- |
| Scope identity、层级、context/external reference 和创建 identity | Scope access binding 和 host-local default selection |
| Source journal head、Source record 和引用的远程 Source Definition manifest | 搜索 projection 和 index |
| Artifact Revision 及其时间戳、actor、回滚元数据；稳定句柄、lineage、跨 Scope 发布来源和 head | Source cursor 和 scheduler state |
| Atomic Memory 合并关系和带合并输入标记的 lineage | External Skill registration 和 host-local installation state |
| Candidate version、decision head 及其 evidence reference | Audit event、usage fact、evaluation receipt 和 restore receipt |
| 托管 Skill 包的文件内容与 manifest；Artifact 标签 | 下载地址和主机本地 Skill 路径 |
| Profile policy、Prompt revision 和 Topic Memory 发布元数据 | 目标环境的 embedding 配置 |
| 作为 Source 保存的 Work contract、task outcome、Handoff boundary 和 receipt | Credential、bearer token、provider secret 和 host-local Skill 安装状态 |

归档包含带版本的 manifest（`format_version`、producer version、scope、数量、排除项和总 checksum）以及 NDJSON record。
逐条 digest 用于比较 canonical 内容是否冲突，总 digest 用于校验 record 流。
它是权威的 round-trip 格式。CSV 可以单独用于有边界的分析，但无法保留不可变 revision、lineage 或 evidence reference，
不得用于恢复。

归档保留已冻结的 recurrence 匹配决策与事件，包括原始幂等键、Source 位置和精确 Artifact revision。
恢复前会校验候选 revision、Handoff/receipt 引用及事件关联的匹配记录。Experience 当前版本推进后，
历史决策仍保持不变；恢复后的重放复用已保存的决策。

恢复后，已合并输入仍保持冻结，带标记的合并输入 lineage 仍可用于撤销合并。这些字段也参与冲突检查：
相同 revision 但合并关系不同的记录，不会被误判为相同内容。

Atomic Memory 迁移后，归档保留迁移后的 Atomic Memory identity、不可变 revision 和 lineage。
原 collection 已退出公共读路径的旧 entry 表记录，以及离线 Memory collection archive，不属于可移植 Runtime 状态。
导出不会修改或删除源数据库中保留的这些历史。若需要原始迁移前 collection 快照、迁移记录或访问控制历史，
请同时保留部署原生备份。

## 恢复和冲突处理

恢复是幂等的。重复回放同一归档时，内容相同的 record 会计入 `already_present`。已有不可变 identity 对应不同 payload
时，恢复绝不会覆盖它：命令以退出码 `3` 结束，并回滚本次写 transaction。解决目标数据冲突，或选择一个干净的目标后，
再次运行同一个恢复命令。

restore 命令会以维护模式打开 Runtime，不预先创建新的默认 scope。因此，包含规范默认 scope 的归档可以在干净的目标中恢复
原始 identity 和创建幂等键；下次正常启动时，默认 scope 初始化会复用该创建 identity。默认选择绑定仍属于部署本地状态，
恢复不会覆盖现有部署的选择，也不会覆盖冲突的 scope。
维护 Runtime 不会启动后台 Artifact 处理 supervisor，即使目标部署配置了后台处理。完成恢复后，再启动正常部署以继续后台工作。

如果命令在完成前中断，不能宣称恢复已成功。权威 record 会在 transaction 中写入；失败的写入不会留下看似成功的部分
恢复。先确认上一次命令已经停止，再对同一目标重新运行同一个归档。

目标数据库会按 `bundle_id` 保存 durable receipt。`authoritative_restored` 表示逻辑数据已经提交但搜索尚未重建完成；
`ready` 表示 projection rebuild 已完成。若重建失败，重新恢复同一 bundle 会跳过相同记录，只有验证完成后才把 receipt
推进为 `ready`。
只要存在 `projections_ready=false` 的回执，正常 Runtime/Server 启动与 worker 启动都会被拒绝，即使归档仅包含 Atomic Memory
或 Experience。显式 restore 路径可以重新打开目标及未完成的 Topic Memory 投影，以继续恢复。请使用相同的目标 embedding
配置重跑同一归档，完成后再提供服务。恢复未完成时，export 返回退出码 `3`，要求先完成恢复。

恢复会在目标写事务开始前将记录存入临时磁盘索引，请为归档副本及解压后的记录预留临时磁盘空间。
已有 MySQL 模式数据库可能需要在 Runtime 启动时将可移植时间戳从 `DATETIME` 升级为 `DATETIME(6)`；
升级前备份数据库，并为升级提供修改表结构的权限。

## 从 SQLite 迁移到 OceanBase

分别创建环境文件，数据库凭据不会写入 bundle：

```bash
powercontext archive export --env-file ./sqlite.env \
  --scope-id project:payments --output ./payments.pcb
powercontext archive restore ./payments.pcb --env-file ./oceanbase.env --dry-run
powercontext archive restore ./payments.pcb --env-file ./oceanbase.env --yes
```

环境文件使用 Server 配置文档中的 `POWERCONTEXT_SERVER_DATABASE_KIND` 和
`POWERCONTEXT_SERVER_DATABASE_URL`。写入前，dry-run 应报告不存在不支持的 family，且冲突数为零。

## 格式兼容窗口

Bundle ID 必须使用带连字符、小写的标准 UUID 表示。完整归档文件连同 ZIP 前缀或尾随字节不得超过 512 MiB；manifest
上限为 256 KiB，解压后的 record 流上限为 2 GiB，最多包含 2,000,000 条 record，每行 NDJSON 不超过 8 MiB。
超限文件会在恢复复制前被拒绝；复制 snapshot 时也会执行相同上限，防止读取期间源文件变化绕过限制。

格式版本 `1` reader 还必须支持归档中出现的全部 record type。即使归档使用格式版本 `1`，旧 reader 也可能拒绝新增的
record type。producer version 仅用于诊断，不能替代 archive schema version 或记录类型兼容性检查。
writer 只生成版本 `1`，不支持降级到格式 `0`。遇到未知 archive 或 record schema
version 时，reader 会在目标写入前拒绝。跨 PowerContext 大版本升级时，应保留旧二进制，直到恢复演练通过。

`artifact_revision`、`artifact_head`、`artifact_lineage_artifact` 和 `candidate_head` 要求 record schema version `2`。
reader 会拒绝这些记录不完整的 schema version `1` 表示，而不是丢弃 revision 元数据或补上默认合并状态、Candidate 类型。
旧数据库中 revision 元数据为空时，归档保留显式 null，不会编造 actor、时间戳或回滚历史。目标缺少归档所需列时，
应先用当前 Runtime 升级；dry-run 会报告不兼容，不会修改目标数据库。
归档不满足兼容检查时，请用当前二进制从源数据库重新导出。
二进制字段使用 canonical Base64；时间戳使用受支持数据库 profile 输出的 canonical、无时区 ISO 文本。恢复会在写入前拒绝
替代编码、携带时区偏移的时间戳及超出存储范围的值，不会接受读回后 digest 改变、再次恢复却产生冲突的表示。

## 部署原生备份

逻辑导出不能替代 crash-consistent 部署备份：

- SQLite：复制数据库前停止所有 PowerContext writer，或使用 SQLite online backup API，例如
  `sqlite3 powercontext.db ".backup './backups/powercontext.db'"`。随后执行
  `sqlite3 ./backups/powercontext.db "PRAGMA integrity_check"`，按源数据库等级保护备份，并测试重新打开。
- OceanBase：根据实际部署版本启用 tenant data backup 和 log archiving，独立保管备份目标与加密材料，并在隔离 tenant
  进行恢复演练。point-in-time recovery 使用原生备份；逻辑跨后端迁移使用 portable bundle。

## 当前边界

目标兼容性检查按记录类型分组，每条 SQL 最多检查 100 条记录。待处理分组总计最多使用 900 个绑定参数，
编码后的查询键和预期摘要总计最多 1 MiB；较宽的复合键会使批次提前拆分。缓存只保留键和摘要，不保留记录正文。
结果游标每次读取一条目标记录，内存峰值仍包含单条已解码归档记录、单条目标记录、规范编码缓冲和数据库驱动开销。
因此，目标中的大正文不会按批次条数成倍驻留内存。这是工作集边界，不是固定的进程内存上限。

批量查询保留目标数据库的键比较规则，同时检查逻辑身份和其他物理唯一键。任何命中项内容不同，都将该归档记录
计为一次冲突；可空唯一键继续遵循 SQL 的 null 语义。dry-run 结果不会缓存给后续恢复使用：restore 会重新检查
当前目标，写入阶段的冲突仍会回滚整个事务。记录检查完成后，继续执行发布链和合并标签集合的完整性检查。

目标检查每完成一批就报告一次进度，即使累计记录数跨过了 1,000 条记录的报告阈值。目标表不存在的记录仍按逐条处理的周期报告进度。

对于一个 Scope、一个日志头和 N 条小 Source 组成的代表性归档，目标表已初始化时，兼容性查询预算为
`ceil(N / 100) + 2`：100、1,000、5,000 条 Source 分别最多执行 3、12、52 条查询。
目标表不存在时，不执行对应记录的兼容性查询。表结构探测、本地依赖索引查询、发布链和标签完整性查询单独计算。

运行 `uv run python scripts/benchmark_archive_validation.py --sources 100 1000 5000 --runs 3`，
可在临时文件型 SQLite 数据库上测量完整校验耗时和查询次数。计时不包含数据准备和导出。
单独报告的恢复耗时包含重新校验、记录暂存、权威数据写入和回执，未配置投影重建器，不能解释成纯写入或搜索重建耗时。
跨提交比较时使用同一脚本和环境。SQL 执行次数减少不代表完整校验耗时同比下降，归档解析、依赖检查、解码和摘要计算
仍需要逐条完成。嵌入式 SeekDB 在本地运行；远程 OceanBase 的成本与部署拓扑有关，需要在真实后端上测量。

2026-10-23 在 Windows 11 build 26200、CPython 3.12.13、SQLite 3.50.4 上，对基线 `4a21da08`
（PR #1561 与当前 master 的合并代码）和批处理实现运行上述脚本。使用默认 SQLiteProfile 配置，Source 正文为
256 字节，未注入网络延迟。以下耗时为三次完整校验的中位数，每次查询计数一致，不包含表结构探测。

| 记录数 | 目标状态 | 查询数：基线 → 批处理 | 秒：基线 → 批处理 |
| ---: | --- | ---: | ---: |
| 102 | 空表 | 102 → 3 | 0.3469 → 0.2828 |
| 102 | 已有相同数据 | 102 → 3 | 0.3755 → 0.3039 |
| 1,002 | 空表 | 1,002 → 12 | 2.6401 → 1.9954 |
| 1,002 | 已有相同数据 | 1,002 → 12 | 3.2584 → 2.3064 |
| 5,002 | 空表 | 5,002 → 52 | 16.3445 → 9.1713 |
| 5,002 | 已有相同数据 | 5,002 → 52 | 16.9955 → 10.7552 |
| 5,002 | 表不存在 | 0 → 0 | 7.9935 → 7.5444 |

不执行兼容性查询的缺表对照也有耗时波动，因此这些本地诊断数据不能作为吞吐量保证。
5,002 条记录的一次不含投影重建的恢复，基线耗时 17.6685 秒，批处理耗时 10.5197 秒，均包含内部校验和暂存。
本次未测量投影重建，也未在该环境执行原生 SeekDB 或 OceanBase。针对这些配置，仍需运行参数化归档验收测试，
取得真实后端执行结果后才能确认本优化的适用性。

导出会把数据库 row 流式写到临时 NDJSON 文件。验证在临时磁盘 index 中保存 record identity 和 Candidate version，
用于校验每个 head 对应的精确版本。恢复则按依赖层流式
回放，因此归档的聚合 payload 不会常驻进程内存；Scope metadata 由 manifest scope list 约束。命令不是远程 Server
API，也不会把部署配置或数据库凭据写入归档。

归档支持 Artifact Candidate；tag-change Candidate 会在导出发布文件或恢复写入记录前被明确拒绝，
不会被静默丢弃或错误解释成 Artifact Candidate。
