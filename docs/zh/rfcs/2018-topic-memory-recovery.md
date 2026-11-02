---
title: Topic Memory 阻塞处理的诊断与恢复
---

- Proposal Name: `topic_memory_blocked_processing_recovery`
- Start Date: 2026-10-15
- RFC PR: [oceanbase/powercontext#2018](https://github.com/oceanbase/powercontext/pull/2018)
- Tracking Issue: [#1885](https://github.com/oceanbase/powercontext/issues/1885)
- Implementation PR: [#1966](https://github.com/oceanbase/powercontext/pull/1966)；合并前按本设计验证实现一致性。
- 修订：[RFC 1417](1417_topic_memory.md)、[RFC 1515](1515_artifact_processing_supervisor.md)
- 相关：[RFC 1549](1549_artifact_family_unification.md)、[RFC 1771](1771-unified-database-migrations.md)

# 摘要

为 Topic Memory 的阻塞处理提供具有 Scope 权限的诊断查询和显式有限重试。复用现有预算表保存当前尝试，复用
processing intent 申请调度，复用 lease 验证执行权。新增一张 Topic 领域的恢复表，仅在 frontier 进入阻塞时保存快照，
在同一条记录中保存重试受理回执。成功和被后续尝试取代的执行不形成永久历史。阻塞快照和已接受回执不自动过期，
确保重启或清理后重复请求仍不能再次获得额度。

# 动机

Topic 窗口耗尽额度后，同一 Cursor 后面的 Source 仍然积压。停止反复派发可以避免空转，但管理员还需要在不直接
修改数据库的情况下查看原因并恢复处理。恢复必须保留证据，并由管理员明确授权新增模型成本。

恢复所需的持久证据是“因何阻塞”以及“管理员是否已经授权另一份额度”。这不直接推出“所有尝试，包括正常成功
的尝试，都需要长期保存”。本提案在实现前明确存储生命周期、职责归属、保留策略和复用边界。

# 使用说明

管理员查询 Scope 的处理状态，得到 Cursor、Source head、保留的 target，以及阻塞记录。仍在执行的最后一次尝试
显示为 pending，因为它仍可能成功发布。

管理员修复原因后，提交查询得到的 `block_id` 申请重试。受理代表发放一次新的有限额度并接受处理请求，不保证生成
成功。Source、Cursor 和 target 保持完整。每份额度继续遵守三次尝试、512 次请求和 64,000,000 预留 token 的上限。

如果受理响应丢失，重复提交同一 ID 会返回原受理结果。重启、后续处理、新的阻塞，以及离线配置变更禁用处理后，
这条规则仍成立。尚未重试且已经不是当前阻塞的 ID 返回冲突。普通 flush、查询、重启和模型变更不会发放新额度。

查询需要 `scope.read`；重试及其回放需要 `scope.admin`。记录仅包含受控 metadata，不包含 Source 正文、prompt、
模型输出、凭据或任意异常消息。

# 详细设计

## 职责和现有存储复用

源码盘点基准为 `master` 的 `a1ed8553149ced3e15756642905a8ea315a3a4a8`。

| 现有存储 | 生命周期与复用决策 |
| --- | --- |
| `pc_topic_memory_work_budgets` | Cursor 上的权威额度记录；执行中修改，成功或受理重试后删除。扩展它以保存当前尝试诊断，不把旧预算长期保留为历史。 |
| processing pending/intent | 可变调度状态和 generation 计数。在受理事务中复用它们申请处理；后续请求覆盖状态后，不能凭它们回放任意历史阻塞的受理结果。 |
| processing lease | 被续约或替换的执行权。复用它验证发布资格；它不能提供不可变的历史诊断和回执。 |
| Artifact revision/head | 可以加入同一事务，技术上能够复用，但需要额外的内部 Family 和生命周期约束，见下文。 |

`ArtifactRepository.create` 接受调用方提供的 connection，因此不能以“不支持原子事务”为由排除 Artifact。可行的
Artifact 方案是以 `block_id` 为身份，通过 revision/head CAS，在同一事务中记录受理、重置预算并创建 intent。
还必须注册带类型的内容模型；防止通用写入、发布、合并和生命周期操作改变恢复记录；执行 Scope 查询与管理员权限；
在清理和备份中保留回放需要的准确 revision。复用可公开改写的 Family 或删除 head，不能抹去授权证据。

具体生命周期为：阻塞时创建 → 准确查询 → 仅接受一次 retry → 无限期准确查询和回放。两种存储都能加入预算重置和
intent 创建事务，原子性没有区别。

| 关注点 | 私有 Artifact Family | Topic 专用阻塞表 |
| --- | --- | --- |
| 身份与受理 | 以 `block_id` 为 Artifact ID，通过 revision/head CAS 更新；保留受理 revision 和当前 head。 | 一个稳定主键和一对只能转换一次的可空受理字段。 |
| 权限 | 注册私有 typed Family，限制通用写入、发布、合并和生命周期操作；仅提供 Scope 授权的恢复操作。 | 恢复 service 拥有读写边界，执行 `scope.read` 和 `scope.admin`。 |
| 原子性 | 调用方 connection 让受理、预算重置和 intent 创建在同一事务完成。 | 同一 connection 提交阻塞更新、预算重置和 intent 创建。 |
| 清理 | 必须让所需 head 和准确 revision 不受通用删除、压缩和 Family 清理影响。 | 不过期、不提供普通删除操作；与处理状态一起备份。 |
| 维护 | 为没有发布、合并和检索需求的记录维护私有内容 schema、capability 限制和 revision 保留规则。 | 维护一份冻结的关系格式及离线转换，不新增 Artifact 能力和 revision 生命周期。 |

选择专用表：表达这一次性的运维状态转换需要维护的独立生命周期规则更少。这是整体复杂度取舍，不表示 Artifact
事务或权限能力不足。当前执行 metadata 留在现有预算，成功和被取代尝试不形成保留历史。

Topic Memory 拥有 Source 范围、终止判断、额度和请求/token 上限。Supervisor 拥有执行、fencing 和受控 Worker
错误分类；通过有界 metadata 回调，让领域保留已停止 Worker 的诊断，不发放额度。不新增公共执行历史表或通用
Job 框架。未来若引入共享记录，需要第二个真实流程验证相同的身份、重试和保留契约。

## 可变的当前尝试

在现有预算行增加可空 metadata：最近一次准入尝试实际选择的 Source 终点、开始和结束的观察时间、执行
holder/group/generation、受控的最后错误。现有 `attempt_id` 和累计尝试/请求/token 计数仍是权威状态。准入事务同时
初始化这些 metadata；更新时必须核对尝试身份仍匹配。新尝试替换旧的当前 metadata，成功则在原子发布和 Cursor
事务中随预算删除。

已停止 Worker 的受控错误必须先写入匹配的当前尝试，再固化终止快照；两步同时发生时应处于同一事务。迟到回调
不能改写替换后的尝试或不可变阻塞证据。如果崩溃使错误类别确实未知，保留 null，不能推测为超时。

预算现有的 `source_through` 继续表示累计覆盖范围，不能替代最后一次实际选择的终点。例如三次选择终点为
`2, 1, 1` 时，最后尝试范围是 `(0, 1]`，累计覆盖仍可为 `2`，保留的 target 为 `3`。

## 持久阻塞和回执

新增 `pc_topic_memory_work_blocks`，每个独立终止阻塞保存一行，不为每次尝试建行：

| 字段组 | 必需证据 |
| --- | --- |
| 身份 | 服务端生成的 `block_id`；`scope_id`、`binding_name`、Cursor `source_after` 和最后 `attempt_id`。 |
| 范围与目标 | 可空的准确选择终点 `source_through`；独立且可空的累计覆盖 `budget_source_through`；保留导入歧义原值的可空 `legacy_source_through`；观察阻塞时从保留目标捕获的 `target_source_through`。 |
| 诊断 | 预留的 `attempts`、`requests`、`tokens`；终止 `error_code`；可空的受控 `last_error_code`；首次观察时间 `blocked_at`。 |
| 受理 | 初始为空的 `retried_at` 和 `retry_request_generation`；受理时一并设置且只能设置一次。 |

以 `block_id` 为主键，增加 `(scope_id, binding_name, source_after, attempt_id)` 唯一键，使重复观察收敛到同一身份。
约束验证范围与非负用量，不要求为旧数据伪造实际选择终点。阻塞证据创建后不可变，仅受理字段对允许转换一次。
同一行既表达已诊断阻塞，也保存持久回执，不需要为回执再建表。

按与发布兼容的锁序观察当前 Cursor、预算和 target。只有已回收该准确 Worker 的 dispatcher，或失败的 Worker 自身，
可以确认它已停止；其他调用方必须核对当前 lease/fence，才能把未结束尝试固化为阻塞。MySQL 必须使用加锁的当前读，
避免 repeatable-read 的旧快照。仍存活的最后一次尝试保持 pending；失效 owner 不能通过与阻塞观察或 retry 竞争重新
取得发布权。

观察同时需要两种锁时，先锁 lease，再锁 Source head，然后在 head 锁内重新读取 Cursor/预算。如果尝试已经变化，
保持 pending 并在后续重新观察，不能在 head 锁内再获取替换尝试的 lease 锁。retry 仅消费已经固化且尝试仍匹配的
阻塞，不反转上述锁序来重新查找活跃 owner。

包括状态查询在内的首次观察可以保存 metadata 快照，但不发放额度、不申请调度。快照创建不读取 Source 正文，也不
调用模型。快照中的 target 是观察时的事实，不能被解释为未知历史失败时刻的 target。

## 重试事务与身份

鉴权后，使用现有 Scope Source-head 写锁串行化操作，读取准确 Scope/binding/block：

1. 已有受理结果时，先返回原结果，再考虑今天的预算和处理配置；不执行写入。
2. 否则要求权威 Cursor 和最后尝试仍匹配、预算已耗尽、处理可用，且没有仍能发布的活跃尝试。过期但未重试的阻塞
   返回冲突；未知或跨 Scope ID 返回不存在。
3. 在一个事务中删除这份准确的耗尽预算，复用现有 repository 请求 pending 和 intent，同时记录受理时间及请求
   generation。保留 Source、Cursor 和 target。

回滚必须恢复上述全部变化。提交后、返回前崩溃由第 1 步覆盖。并发请求同一阻塞只能获得一次受理；后续新阻塞需要
它自己的查询 ID。调用方提交未知 ID 不会创建阻塞或发放额度。重试不退还现有额度中失败或结果含混的成本。

## 对外契约

新增处理状态、准确阻塞查询和重试的 HTTP operation，并提供对应 Python Client SDK 和 CLI。拟议 HTTP 路径为
`/v1/topic-memory/processing/status`、`/v1/topic-memory/processing/blocks/get`、
`/v1/topic-memory/processing/retry`，均使用 POST。MCP 工具目录保持现有范围。

状态接口返回当前 Cursor/head/target，以及 `idle`、`pending` 或 `blocked`。阻塞查询返回稳定快照和受理结果，
retry 返回同一已受理记录。鉴权后，过期但未重试的阻塞返回 409；未知或跨 Scope 阻塞返回 404。禁用处理后仍必须
能回放已经受理的 retry。

实际选择终点未知时，`source_through` 为 null。`budget_source_through` 表示已记录的累计覆盖，新阻塞必有值；仅在
导入的历史阻塞已没有原额度时允许为 null。`legacy_source_through` 在新阻塞中为 null，导入时保留旧尝试表含义
不明确的原始范围，既不声明准确选择终点，也不声明累计覆盖。客户端必须区分这些字段并支持 null，不能从今天的
预算或尝试历史推测旧额度。精确 wire schema 由 OpenAPI 定义；实现时同步重新生成 HTTP 模型、SDK/宿主
operation metadata 和桌面类型。

## 保留、清理与存储增长

在保留的数据库中，永久准确查询阻塞和回放原 retry 受理结果是产品保证，处理进展、重启和禁用处理后仍成立。
对过期的已受理 ID 返回 404 虽能阻止再次发额度，却不满足回放保证；本 RFC 明确选择持久回放并承担其存储成本。
在保留的数据库中无限期保存每个阻塞快照和受理结果。不引入 TTL、逐次执行历史、成功历史、批量 purge 或回执自动
压缩。尚未重试的阻塞在过期后仍可查询。备份/恢复必须一致地保留预算、阻塞/回执、Cursor、target 和处理状态。
部分恢复或删除单条记录不属于受支持的恢复操作。

这一保留策略明确没有时间上限：成本随独立阻塞增长，包括管理员反复重试后再次形成的阻塞；不会随每个正常成功窗口
增长。未来清理必须显式定义准确历史查询和回执回放契约；即使删除后把旧 ID 拒绝为未知，静默删除已受理结果仍违反
本 RFC。任何清理都不能让旧 ID 重新获得发放额度的资格。

设 `W` 为处理窗口数，`A` 为每窗口平均准入尝试次数，`B` 为独立阻塞数，包含受理重试后再次形成的阻塞。完整历史
大致保留 `W × A` 行；本方案保留 `B` 行，加上当前预算行。正常观察、受理和完成都按准确键读写，不扫描或重写历史。

一次 SQLite 3.50.4 合成存储测量使用实现提交 `358b0b59` 的冻结恢复 schema，每种布局写入 100,000 行，使用
4 KiB page、1,000 个 32 字符 Scope ID、`topic-memory-source-window` binding 和种子 2018 生成的 UUID。
各布局包含主键/唯一键，尝试历史还包含原 frontier 索引。成功尝试样本填写两个时间戳和执行身份；已受理阻塞样本
填写两个回执时间戳、三次尝试、九次请求、1,000 token、`window_attempt_limit` 和 `model_timeout`，旧范围来源为
null。扣除空 schema 页面后，20 字段尝试布局增长 52,895,744 字节，最终 17 字段阻塞/回执布局增长 42,901,504
字节，约为每行 529 和 429 字节。这仅测量隔离 schema 的存储，不验证运行时性能、模型执行、写入延迟、OceanBase
或 seekDB；不含预算新增列、WAL、备份和其他表。部署容量和各后端索引仍需独立测量。

作为示例而非生产观测，若每天一百万窗口、`A = 1.1`、每天一万次阻塞，则完整历史每天新增 110 万行，本方案新增
一万条保留记录。套用上述 SQLite 样本宽度，额外保留记录约为每天 582 MB 与 4.3 MB。即使一份额度最多三次尝试，
成功尝试历史仍构成明确的存储承诺；单份额度上限不等于历史保留上限。本文不据此声称后端性能提升或部署容量足够。

## 迁移与兼容性

实现必须遵循 RFC 1771，为所有受支持后端的预算新增列、恢复表、约束和索引交付显式版本化离线迁移，登记影响对象、
已识别基线、验证和备份范围。正常启动不得静默修改已有库。空库初始化遵守同一受支持 revision 链。本 RFC 本身
不引入迁移代码或运行时行为。

版本化 Topic 恢复 bundle 使用现有 `server db-migrate` 维护入口，显式选择 `topic-memory-recovery` bundle。
plan 仅覆盖本领域登记对象，并报告 `server_ready=false`，不把局部 Artifact 迁移 bundle 当作完整 Server 就绪保证。
复用维护 connection、目标锁、plan/备份声明和持久恢复证据。普通启动在任何业务初始化写入前检查恢复 schema。
空库使用同一冻结目标 revision 初始化，不能把旧的部分数据库当作空库。

空库先创建阻塞表，再创建预算；SQLite 使用显式写事务包住两步。非事务 DDL 中断后，只有准确、空的阻塞表前缀，
且没有已知 Access bootstrap 和独立 Code 图存储以外的业务表时，才可不依赖维护证据继续初始化。这些独立表可以
先于恢复存储存在，且保持不变。含有回执或其他业务状态的前缀必须拒绝，不能静默重建缺失额度或丢弃恢复证据。

写入前只接受以下已验证输入：

| 输入 | 转换 |
| --- | --- |
| 空数据库中的空恢复存储 | 初始化冻结的目标 schema。 |
| 已识别的恢复功能之前的预算布局，没有 attempts 表 | 保留预算和计数，增加值为 null 的当前尝试 metadata 和空阻塞表。 |
| #1966 使用的已识别 attempts 表布局 | 复制每个终止阻塞和受理的准确 ID、计数、错误类别、target、首次观察、受理时间和 generation；把匹配当前尝试的已知执行 metadata 复制到预算，不改变额度。 |
| 目标 schema | 验证并返回无变更，不重置预算、不重新生成身份。 |
| 未知列/约束/索引、没有匹配恢复证据的部分布局、无效受理字段对或冲突阻塞身份 | 在业务和迁移写入前拒绝；不能靠删表或接纳未知格式修复证据。 |

旧 attempts 布局没有记录范围来源，部分历史已受理额度也已删除。转换后的准确 `source_through` 为 null，原数值
保留在 `legacy_source_through`；只有相同 frontier 和最后 `attempt_id` 仍匹配的原额度才能提供
`budget_source_through`，否则为 null。不能从后续预算、成功历史或无关尝试的最大值推断。这是显式 wire 契约
转换：旧数值及其原有歧义仍可读取，retry 身份和原受理保持不变。新准入尝试始终记录准确选择终点。

迁移前停止包括 Worker 在内的所有写入者。备份一致覆盖预算、旧 attempts 表、阻塞、Source、Cursor、target、
pending/intent 和 lease。验证复制证据和预算成本未变后才移除旧 attempts 表；成功和被取代行不需要运行时历史，
仍保存在恢复点中。MySQL 兼容 DDL 可能独立提交：执行前在库外保存源/目标指纹和步骤证据，只允许恢复已识别前缀，
中断后必须使用同一维护目标和恢复点。SQLite 原子提交 schema 和数据转换。通过验证后才能重新开放业务。缺失的
错误/owner/时间事实不能重建，也不能把 `processing_failed` 解释为超时。

开放业务前沿用既有维护 fence 转换撤销所有升级前 processing lease：递增 generation、按 UTC 使其过期，并设置
维护窗口 holder。仅更新 holder 不同于该窗口的行，使中断恢复保持幂等；共享这些 lease 的全部工作流必须停写。
保留已知尝试 owner，未知结束时间仍为 null。即使处理保持禁用、没有新 Supervisor，查询也应能固化已停止工作。
转换后任何升级前 fence 均不得发布。

不支持新旧写入者混跑，也不支持接受重试后的破坏性 downgrade。任何回退都必须保留已接受授权证据；如果恢复到
受理前的备份，不能声称幂等性仍跨越了已丢弃的数据库历史。对 RFC 1417 中 retry/reset API 与持久 Job history 的
排除，仅修订为允许这些管理操作和终止恢复记录。RFC 1515 增加有界失败 metadata 回调，Supervisor 不重置 Topic
预算。

## 验收要求

- 阻塞不再次启动已耗尽的工作，不推进 Cursor、不丢 Source/target、不妨碍其他 Scope 处理。
- 活跃的最后尝试保持 pending；停止或被撤销的尝试保留诊断。重启后仍区分 Worker 与模型超时，准确选择范围、累计
  覆盖和 target 独立表达。
- 重复/并发 retry、响应丢失、重启、后续新阻塞和禁用处理均回放同一受理，不再发额度；过期、跨 Scope、未知和未
  授权请求不能重置工作。
- 成功不新增保留的执行历史；重复观察保持同一 block ID 和不可变证据，受理仅改变回执字段。
- 支持的旧预算和 attempts 表阻塞/回执迁移不重置成本、不改变身份、不编造历史；文件数据库重启、备份恢复以及受支持后端的实际事务均保持身份和恢复语义。
- OpenAPI、Python SDK、CLI、桌面与声明支持管理操作的宿主契约一致，MCP 目录不扩展。仅 SQLite 或 mock 输入的测试
  不代表真实 OceanBase/seekDB 或原生宿主验收。

# 代价

本方案仍新增领域持久表和预算可空列，需要离线 schema 升级，并且没有阻塞记录的时间保留上限。无法查询完整执行
历史，也不能还原每次中间失败。管理员反复重试形成的阻塞仍带来长期存储成本。

# 理由与替代方案

| 方案 | 决策 |
| --- | --- |
| 保留包括成功在内的每次尝试 | 取证信息更全，但恢复不需要完整历史，尚无明确消费者和保留预算。 |
| 仅在现有预算保存阻塞 | 成功/reset 删除预算后不能回放受理；保留旧 generation 会改变预算对当前 frontier 的职责。 |
| 复用 Artifact | 事务上可行，但需内部 Family，以及 revision、capability 和清理的持久约束。选择更窄的运维生命周期。 |
| 新表同时保存临时尝试与永久阻塞 | 避免扩展预算列，但在权威预算旁新增当前尝试存储；优先复用现有当前工作记录。 |
| 立即引入公共处理历史 | 需要另一个真实流程验证身份和保留语义；改名或假设未来消费者不能证明复用。 |
| 依赖日志，或让 flush 重置预算 | 日志不保证持久回放；普通 flush 不应授权新增模型成本。 |

# 既有设计

[RFC 1417](1417_topic_memory.md) 定义 frontier 额度与原子发布；
[RFC 1515](1515_artifact_processing_supervisor.md) 提供公共调度和 fencing；
[RFC 1771](1771-unified-database-migrations.md) 定义显式 schema 升级。
[Issue #1885](https://github.com/oceanbase/powercontext/issues/1885) 记录运维恢复需求。

# 未决问题

接受本 RFC 前，需要决定“预算＋阻塞记录”是否是足够小的持久生命周期、已评估的 Artifact 方案是否有具体优势，以及
无限期保留阻塞/回执是否可以接受。对这些选择的调整仍须满足上述身份与原子性保证。后端迁移和容量测量证据属于
实现验收要求，不能因接受设计而视为已通过。

# 后续可能性

有独立需求的取证历史、经另一流程验证的共享执行记录，以及带明确回放过期语义的有限保留，可以后续单独提出。
阻塞 Topic 恢复不依赖这些能力。
