---
title: Desktop 会话观察
description: 原生请求与用户操作的测量口径、有界保留和安全诊断投影。
---

# Desktop 会话观察

Desktop 为 [#2107](https://github.com/oceanbase/powercontext/issues/2107) 提供内存中的观察窗口。
采集随原生宿主启动，不依赖打开观察页面。原生命令 `session_summary`、`reset_session_observations` 和
`preview_session_diagnostics` 只允许拥有对应 capability 的可信主窗口调用；没有活动连接或配置存储不可用时仍可调用。
这些命令不会发起 HTTP 请求、探测就绪状态、运行本地诊断、写入剪贴板或上传数据。本次会话页面以及显式预览、复制交互
由独立的 UI 工作交付；完成集成之前，页面继续显示不可用。

## 测量单位与覆盖范围

每条记录都通过 `unit` 和 `operation` 明确测量单位及所属操作 ID。

| 单位 | 计数对象 | 耗时口径，单位为毫秒 |
| --- | --- | --- |
| `http_request` | 一次交给原生 HTTP 客户端的请求 | 从派发到完成有界响应读取、JSON 解码，或失败、取消的单调时钟耗时 |
| `user_operation` | 一次原生 Memory、Scope 或统计操作调用，包括被拒绝的调用 | 从进入原生操作到完成上下文与身份检查、响应校验及结果构造，或失败、取消的单调时钟耗时 |

HTTP 覆盖 `get_server_info`、`get_liveness`、`get_readiness`、`get_capabilities`、`get_access_principal`、`list_scopes`、
`get_scope`、`get_default_scope`、`get_stats`、`remember_memory`、`search_memory` 和 `get_artifact_revision`。
连接检查只计入它实际发出的各项 HTTP 请求，不额外产生用户操作。身份复核及现有匿名访问的就绪检查回退也计入 HTTP 请求。
因此，一次用户操作可能产生多个请求；`coverage.httpOperations` 和 `coverage.identityChecksIncluded` 明确说明该范围。

用户操作覆盖 `coverage.userOperations` 中的七项 Memory/Scope/统计操作：`list_scopes`、`get_scope`、
`get_default_scope`、`get_stats`、`remember_memory`、`search_memory` 和 `get_artifact_revision`。不包含配置编辑、连接检查、
渲染层防抖、界面确认等待或剪贴板操作。派发前的本地校验和并发限制可能产生失败的用户操作，而没有 HTTP 记录。
HTTP 延迟是客户端请求耗时，不能解释为 Server 内部处理耗时；用户操作延迟是原生操作耗时，不代表完整界面旅程。

`coverage.desktopSessionOnly` 为 true，`coverage.persistenceEvidence` 为 false。这些数据只覆盖当前 Desktop 进程，
不是全局 Server/Agent 链路，也不是持久化成功的权威证据。不采集 Installer/Ops 或本地 CLI 诊断，
`coverage.installerOps` 为 false。后续本地安装诊断区只能投影所属 Installer/Ops 的安全阶段、结果和受支持的不透明操作引用。
不得引入 stdout、路径、配置或 journal 内容，不得另建 journal，也不能将本地生命周期操作计入 HTTP 延迟。

## 结果语义

`outcome` 为 `success`、`failure`、`cancelled` 或 `unknown`。HTTP 成功表示已读取并解码 200 响应；后续操作级校验仍可能失败。
即使适配器正常返回结构化的未就绪事实，readiness 的 503 仍计为 HTTP 失败，`errorCode` 为 `runtime_not_ready`。
HTTP Future 被取消时记录 `cancelled`，不代表远端写入已经取消。

读取类用户操作采用其原生返回结果。`stale_context` 归为取消，包括携带过期上下文发起的调用。
保存操作复用现有 `WriteRecord.status`，不根据外层 `Result` 是否为 `Ok` 判断成功。成功且返回零条记录仍为成功。
明确拒绝的写入仍为失败；派发后的网络故障、500、无效回执或中断可能使用户操作保持 `unknown`。
如果操作级回执校验失败，已正常解码的 HTTP 200 可以与未知的保存结果同时存在。
在现有写入派发边界之后丢弃保存任务，继续保持未知结果。观察不会重定向、重放、重试、取消或确认业务写入。

## 保留、重置与成本

所有测量单位、连接和 Scope 共享一个窗口，按原生完成顺序最多保留 **512 条终态记录**。
尚未结束的工作在完成或取消前不出现在记录中。每次插入的工作量有界且为常量；溢出时淘汰最早的记录。
摘要最多复制和汇总 512 条记录，分组数量受固定的单位及操作词汇约束。没有历史扫描、持久化、后台任务、外部导出器或
按上下文增长的集合。退出 Desktop 会丢弃全部观察；重新打开时建立空会话。

重置清空保留记录、分组和淘汰数量，更新窗口起点；不改变会话起点或业务状态，也不取消任务。
重置前创建的观察守卫不会重新填入新窗口；它们结束时，每条被排除的 HTTP 请求或用户操作都会递增 `droppedSinceStart`。
重置后才开始的 HTTP 请求仍可能出现，即使它所属的用户操作开始于重置前。

连接、Principal 和 Scope 切换保留进程范围内的匿名观察。记录不存储目标身份，历史结果和晚到结果不会被归到新选择的目标。
原有私有上下文失效、读取取消和已派发写入的不可变目标仍由连接管理器负责。

记录过程使用非阻塞锁，不执行 I/O。锁竞争或采集器锁中毒只会丢弃观察，不改变业务结果，也不抑制取消。
`droppedSinceStart` 统计整个进程的此类丢失数量，包括重置前开始、结束时被排除的观察，重置后仍保留，避免重置掩盖覆盖缺失。
读取或重置时遇到锁竞争返回 `busy`，采集器锁中毒返回 `storage`，不会伪造成功的空摘要。

## 安全投影契约

`SessionSummary` 是显式的 Rust 投影，不序列化 `DesktopState`、`WriteRecord`、原始请求、响应或异常。
原生预览只将该投影格式化为 JSON。结构化输出和文本输出采用相同的白名单，安全性不依赖渲染层脱敏。

| 字段 | 含义和单位 |
| --- | --- |
| `schemaVersion` | 安全摘要模式版本，当前为 1 |
| `sessionStartedAtMs`、`windowStartedAtMs`、`generatedAtMs` | UTC Unix epoch 毫秒：进程会话起点、最近重置或启动时间、摘要生成时间 |
| `capacity`、`evicted`、`droppedSinceStart` | 终态记录上限、重置以来的淘汰数量、进程启动以来的采集丢失数量 |
| `coverage` | 上述原生采集范围和限制 |
| `recent` | 按完成顺序保留的记录；存在淘汰或丢失时，不代表完整会话历史 |
| `unit`、`operation`、`outcome` | 固定的测量单位、白名单操作 ID 和结果枚举 |
| `completedAtMs`、`durationMs` | UTC 完成时间的 epoch 毫秒，以及非负的单调时钟耗时，单位为毫秒 |
| `httpStatus`、`errorCode` | 已收到的 HTTP 状态、适用的现有 `SafeError` 枚举；无对应值时为 null |
| `operations` | 仅根据 `recent`，按单位和操作计算的分组 |
| `outcomes`、`sampleCount` | 各类终态的保留数量和样本总数 |
| `minDurationMs`、`meanDurationMs`、`maxDurationMs` | 分组内所有保留终态的最小、平均及最大耗时，单位为毫秒，包含已取消和未知结果 |

采集器不接收自由格式诊断字段。Memory/Source 内容、查询、提示词与响应、凭据、原始 endpoint、配置名称与 ID、
Principal/Scope/Artifact ID、私有路径、请求头、原始错误和安装输出都不进入记录。
Server 请求 ID 仍可通过现有操作失败结果获取，但不进入会话导出。采集器不创建关联 ID 或执行身份指标标签，
也不配置运行日志、指标、span 或遥测后端。

原生回归检查使用模拟的 loopback HTTP 服务和 mock Tauri IPC，覆盖有界保留、在途写入期间重置、请求与操作计数、
就绪及写入结果、取消、敏感值排除、权限和采集故障隔离。这些检查不构成真实 Server 或安装包剪贴板验收。
