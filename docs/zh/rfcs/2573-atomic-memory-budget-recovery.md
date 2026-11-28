---
title: "RFC 2573：Atomic Memory 输入预算阻塞恢复"
description: 完整提取输入超过本地预算时的持久阻塞与条件式恢复。
---

# Atomic Memory 输入预算阻塞恢复

跟踪 issue：[#2573](https://github.com/oceanbase/powercontext/issues/2573)。
本契约扩展 [RFC 1515](1515_artifact_processing_supervisor.md) 的处理准入，沿用
[RFC 2018](2018-topic-memory-recovery.md) 的阻塞与回执语义。跨 Family 恢复仍由
[#2128](https://github.com/oceanbase/powercontext/issues/2128) 跟踪。

## 失败与范围

Atomic Memory 在生成前检查生效的提取指令与完整渲染证据。包含多个 journal 位置的窗口继续使用现有的持久化缩窗。
窗口只有一个位置时，本地 `atomic_memory.extract` 预算拒绝转为持久的 `input_budget_exceeded` 阻塞。
cursor 位置、Source 材料与处理 intent 保持不变，不发送提取请求，也不发布部分 Memory。

该策略覆盖自动 Source 窗口提取路径及该路径的显式 flush。协调预算拒绝（包括最小比较单元上的拒绝）保留现有缩减与重试行为；
持久协调恢复不属于本契约。提供方错误、输出校验和超时继续使用各自的恢复行为。Source 阻塞不禁止独立排队的 Dream 请求，其他 Scope 和独立调度的 Family 继续运行。
同一 cursor 后面的 Source 等待恢复；跳过证据需要单独的完整性契约。
超大证据分片仍须遵守 [#2074](https://github.com/oceanbase/powercontext/issues/2074) 的评估要求。

## 持久身份与准入

`pc_memory_work_blocks` 保存一份不可变的拒绝快照：服务端生成的阻塞 ID、Scope、binding、精确的单位置 journal 区间、
失败阶段、生效指令与渲染输入的 SHA-256、估算输入 token 数、生效预算、估算器 ID 和首次拒绝时间。
记录不包含 Source 正文、提示词、模型输出或凭据。
活动标记与 frontier 唯一键保证一个当前阻塞。
`input_fingerprint` 是完整提取预算输入摘要，仅用于关联拒绝时的提取快照，不是恢复前后的输入相等条件。
修正提示词或渲染配置可以改变完整输入；恢复检查当前完整输入是否符合预算，保留原始拒绝摘要。
Source、提示词、渲染器与依赖保持不变且渲染确定时，该摘要可重新计算。估算与拒绝哈希消费同一份渲染字符串；
摘要从不替代阻塞身份、frontier 或 cursor generation 检查。协调失败不携带提取指纹。
只有接受恢复时才修改活动标记，并写入接受时间与处理请求 generation。
接受回执不会自动过期，处理成功或关闭生成后仍保留；成功工作不创建恢复记录。

失败 Worker 取得与 Atomic 发布一致的 Source head 锁、验证执行 fence，并通过所选 cursor 的 generation CAS 后才保存阻塞。
发布和恢复保留 cursor CAS 保护，过期 Worker 不能覆盖已替换 frontier 的阻塞或发布结果。
调度准入只读取 cursor 与阻塞元数据，不投影证据、不估算输入。
普通调度、新增 Source、flush、配置变化和重启均不解除阻塞。

## 管理与恢复

三个管理操作均使用 POST，并对当前 Scope 授权：

| 路径 | 权限 | 行为 |
| --- | --- | --- |
| `/v1/atomic-memory/processing/status` | `scope.read` | 只读 cursor、head 与当前阻塞，不改变状态。 |
| `/v1/atomic-memory/processing/blocks/get` | `scope.read` | 读取一份精确保留的拒绝与回执。 |
| `/v1/atomic-memory/processing/retry` | `scope.admin` | 条件式接受观察到的阻塞 ID 的恢复。 |

已接受的 ID 在检查生成可用性或当前预算之前返回原回执。否则处理器验证阻塞所属的当前 cursor，授权完整 Source 选择，
绑定当前提示词快照，投影证据并检查当前完整输入预算，不调用模型。仍然超限时返回 HTTP 409 并保留阻塞。
不再符合生成条件的阻塞 Source 返回 HTTP 409 `atomic_memory_block_source_ineligible`，并携带阻塞 ID 与 Source 引用，阻塞保持不变。
Source 读取授权在判断生成条件之前先作用于原始窗口，因此无读取权限的恢复返回授权错误，绝不泄露 Source 引用。生成不可用时返回 503。

预检通过后，一个事务锁定 frontier、重新读取回执和 cursor generation、复核 Source 权限与生成条件、推进 cursor generation 使旧执行失效，
请求 pending 处理及其 intent，并记录接受结果。并发请求收敛到一个回执；取消或回滚不会提交 intent 或回执。
尚未接受的过期阻塞返回 409，未知或跨 Scope 的阻塞在授权后返回 404。响应丢失后重复相同阻塞 ID 即可恢复结果。
自动处理关闭时，已接受的工作保持 pending，等待显式 flush。后续 Worker 始终重新校验完整输入，接受恢复后的配置变化不能绕过准入。
一次已接受恢复后的新失败创建独立阻塞，需要针对新阻塞再次显式恢复。

普通 `/v1/memory/flush` 遇到该阻塞返回 HTTP 409 和阻塞 ID。Python SDK 提供对应管理方法，CLI 命令为：

```bash
powercontext atomic-memory status --scope-id SCOPE
powercontext atomic-memory block --scope-id SCOPE --block-id BLOCK_ID
powercontext atomic-memory retry --scope-id SCOPE --block-id BLOCK_ID
```

提高限制前先检查生效的输入预算和提示词，并确认模型能容纳完整请求与输出预留。
本地准入不保证提供方接收或生成成功。恢复预检还要求当前 Source 读取与 Memory 创建权限，`scope.admin` 不授予这些权限。嵌入式 Runtime 调用方负责管理授权；HTTP 强制执行上述权限。MCP 不变。

## 状态与诊断

Dashboard 自动处理状态独立于进程内 Supervisor 观察读取持久阻塞，展示阻塞 ID、失败阶段、拒绝时的估算和预算、估算器与支持的恢复动作。
这些数值描述原始拒绝，并非当前配置。不虚构尝试次数、提供方请求数或计费 token 数。
现有安全诊断字段通过 `ArtifactProcessingBlock` 跨 Worker 边界传播；不变的元数据检查不会重复产生处理失败。
`atomic_memory.retry.wake_failed` 表示接受事务提交后的尽力唤醒失败；周期发现仍能读取持久 intent，回执继续保持已接受状态。

## 存储兼容

新增表归入冻结 head `v1_6_0` 之后的累计 Server 迁移 `000`，覆盖 SQLite、MySQL 兼容后端与两条已注册来源路径。
已发布数据库必须在启动前执行显式托管迁移，启动过程不增加表，之前的冻结资源不变。
未发布 `000` 创建的数据库在草稿变化时需要重建。
备份将注册的阻塞表与 Source、cursor、pending 工作、intent 和 lease 一起保存。
不从日志回填历史记录；现有超大 frontier 在升级后首次执行时进入阻塞。
旧、新写入程序不能并发运行。

## 保留与容量

已接受回执和拒绝快照没有过期时间、没有 TTL，也没有自动清理。表没有固定上限，因此容量规划就是历史上不同阻塞 ID 的数量。
再次失败的恢复会创建新的阻塞 ID 并增加一行；成功窗口、轮询、状态读取和重复的已接受 ID 重试都不增加。
已接受行保留其填写的接受列，并继续占用表和 frontier 索引；`active IS NULL` 行既不会被回收也不会被移动。
手动删除行会破坏精确 ID 重放的承诺。

每个阻塞的成本是表存储加主键和 frontier 索引。2026-11-28 在 Linux x86_64、Python 3.11.15、SQLite 3.50.4
上按当前仅覆盖提取的字段测量：36 字符 UUID、代表性 30 字符 Scope ID、实际 20 字符 `memory-source-window` binding，
以及近上限 256/128 字符标识符与 128 字符估算器 ID。单 Scope、10 万行的约值如下；两个索引均包含已接受的
`active IS NULL` 行：

| 标识符 | 状态 | 表 B/行 | 主键 B/行 | frontier B/行 | 总计 B/行 |
| --- | --- | --- | --- | --- | --- |
| 代表性 | active | ~256.7 | ~50.1 | ~74.1 | ~380.8 |
| 近上限 | active | ~821.2 | ~50.3 | ~455.1 | ~1326.7 |
| 代表性 | accepted | ~293.4 | ~50.1 | ~74.1 | ~417.5 |
| 近上限 | accepted | ~821.2 | ~50.5 | ~455.1 | ~1326.9 |

脚本覆盖 1000、1 万和 10 万行，分别测量 active/accepted、代表性/近上限字段。
100 个 Scope 的 mixed 负载另测不同 frontier 和同一 frontier 反复阻塞：10 万行时，总成本分别约 411.5 和 407.2 B/行。
重复 frontier 的 mixed 负载只在每个 Scope 保留最后一个活动块；较早 ID 均为已接受回执。
标识符长度会改变大小，这些值不是行大小上限。

查询读取完整持久行。脚本从真实存在的活动行选择命中参数，并验证命中一行；使用不存在的 Scope 验证未命中。
accepted-only 数据没有活动命中，因此该项输出 `null`。精确 ID 读取也验证命中一行。
本次进程内热缓存查询中位数约 2.4–8.2 微秒；回归检查同时运行，后台负载未隔离。报告另含完整数据库分配/空闲页、
每个索引、临时内存备份时间和备份行数；内存备份计时不认证恢复流程。复现命令：

```bash
PYTHONPATH=src uv run python scripts/benchmark_memory_block_capacity.py \
  --counts 1000 10000 100000 --states active accepted \
  --field-modes representative near_limit
PYTHONPATH=src uv run python scripts/benchmark_memory_block_capacity.py \
  --counts 1000 10000 100000 --states mixed --scope-counts 100 \
  --histories distinct repeated
```

这些是当前结构下的本地 SQLite 合成数据观测，不是冷缓存、生产容量保证或原生后端容量证据。
seekDB/OceanBase 的表和索引占用、备份恢复及生产负载目标需要独立测量。

任何未来清理都必须通过兼容契约保留精确 ID 重放，例如仍能回答精确读取的最小回执、能回答精确 ID 读取并从备份恢复的归档层，
或显式的有限重放窗口。归档只有在精确读取、备份恢复和旧 ID 重放都能正常工作时才能取代在线保留。字段或索引变化后必须重新测量容量。

## 验收

验证持久超大证据、拒绝时零生成、Source/cursor/发布结果不变、较小窗口恢复、仅元数据准入、真实派生 Worker 传播、
重复调度和重启、无关 Scope 的进展、授权、精确读取、预算不足时拒绝恢复、并发恢复、取消回滚，以及处理完成或关闭生成后的已接受 ID 重放。

SQLite 和真实嵌入式 seekDB 配合本地脚本提供方验证本地处理。seekDB 验证覆盖持久阻塞、Runtime 重开、
并发恢复、取消回滚、旧回执与新阻塞隔离，以及当前累计 Server 迁移创建恢复表和重复应用。
原生测试通过 `POWERCONTEXT_TEST_MIGRATION_SEEKDB=1` 显式启用，并使用独立临时存储。
这些检查不认证已安装发布版本的原生升级、真实 OceanBase 服务或外部模型提供方；这些验收仍需独立执行。
