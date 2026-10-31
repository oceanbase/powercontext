---
title: Desktop 会话观察
description: 原生请求与用户操作的测量口径、有界保留和安全诊断投影。
---

# Desktop 会话观察

Desktop 为 [#2107](https://github.com/oceanbase/powercontext/issues/2107) 提供内存中的观察窗口。
采集随原生宿主启动，不依赖打开观察页面。原生命令 `session_summary`、`reset_session_observations` 和
`preview_session_diagnostics` 只允许拥有对应 capability 的可信主窗口调用；没有活动连接或配置存储不可用时仍可调用。
这些命令不会发起 HTTP 请求、探测就绪状态、运行本地诊断、写入剪贴板或上传数据。“观察 → 本次会话”读取这些原生投影，
并提供显式的预览与复制交互。

## 测量单位与覆盖范围

每条记录都通过 `unit` 和 `operation` 明确测量单位及所属操作 ID。

| 单位 | 计数对象 | 耗时口径，单位为毫秒 |
| --- | --- | --- |
| `http_request` | 一次交给原生 HTTP 客户端的请求 | 从派发到完成有界响应读取、JSON 解码，或失败、取消的单调时钟耗时 |
| `user_operation` | 一次原生 Memory 或 Scope 操作调用，包括被拒绝的调用 | 从进入原生操作到完成上下文与身份检查、响应校验及结果构造，或失败、取消的单调时钟耗时 |

HTTP 覆盖 `get_server_info`、`get_liveness`、`get_readiness`、`get_capabilities`、`get_access_principal`、`list_scopes`、
`get_scope`、`get_default_scope`、`remember_memory`、`search_memory` 和 `get_artifact_revision`。
连接检查只计入它实际发出的各项 HTTP 请求，不额外产生用户操作。身份复核及现有匿名访问的就绪检查回退也计入 HTTP 请求。
因此，一次用户操作可能产生多个请求；`coverage.httpOperations` 和 `coverage.identityChecksIncluded` 明确说明该范围。

用户操作覆盖 `coverage.userOperations` 中的六项 Memory/Scope 操作：`list_scopes`、`get_scope`、
`get_default_scope`、`remember_memory`、`search_memory` 和 `get_artifact_revision`。不包含配置编辑、连接检查、
渲染层防抖、界面确认等待或剪贴板操作。派发前的本地校验和并发限制可能产生失败的用户操作，而没有 HTTP 记录。
HTTP 延迟是客户端请求耗时，不能解释为 Server 内部处理耗时；用户操作延迟是原生操作耗时，不代表完整界面旅程。

`coverage.desktopSessionOnly` 为 true，`coverage.persistenceEvidence` 为 false。这些数据只覆盖当前 Desktop 进程，
不是全局 Server/Agent 链路，也不是持久化成功的权威证据。应用将原生 Ops 桥接接入同一个采集器，
`coverage.installerOps` 为 true，且不依赖连接状态存储。白名单快照显示在独立的 `installerOps` 区域，契约见下文。
未接入 Ops 源的独立采集器返回 false，界面显示覆盖不可用，不解释为零次安装失败。本地 CLI 诊断不在采集范围内。

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
| `schemaVersion` | 安全摘要模式版本，当前为 2；随包分发的界面和原生宿主必须匹配 |
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
| `installerOps.capacity`、`installerOps.evicted`、`installerOps.droppedSinceStart` | 独立的 Ops 快照上限、重置后的淘汰数及进程累计采集丢失数 |
| `installerOps.recent` | 按调用返回顺序保留的原生 Ops 白名单快照；不是 journal 或实时状态 |
| Ops 的 `action`、`attempt`、`phase`、`attemptPhase`、`outcome` | 现有 `OpsAction`、可空的尝试次数、`OpsServicePhase`、`OpsAttemptPhase`，以及不重新解释的 `OpsOutcome` |
| Ops 的 `operationId` | 原生生成且由 Ops 引用存储支持的规范 UUID，可交给 `ops_operation_status` 查询，不是目标或绑定身份 |
| Ops 的 `errorCode`、`bridgeError`、`observedAtMs` | 可空的现有 `OpsErrorCode` / `OpsBridgeError` 枚举，以及调用返回时的 UTC Unix epoch 毫秒 |

采集器不接收自由格式诊断字段。Memory/Source 内容、查询、提示词与响应、凭据、原始 endpoint、配置名称与 ID、
Principal/Scope/Artifact ID、私有路径、请求头、原始错误和安装输出都不进入记录。
Server 请求 ID 仍可通过现有操作失败结果获取，但不进入会话导出。采集器不创建关联 ID 或执行身份指标标签，
也不配置运行日志、指标、span 或遥测后端。

原生回归检查使用模拟的 loopback HTTP 服务和 mock Tauri IPC，覆盖有界保留、在途写入期间重置、请求与操作计数、
就绪及写入结果、取消、敏感值排除、权限和采集故障隔离。这些检查不构成真实 Server 或安装包剪贴板验收。

## 本地安装失败诊断

原生 Ops 边界只投影已有持久操作引用的 `apply`、`resume`、`operation_status` 和 `cancel` 调用结果。
分配引用前的预检、绑定和授权失败仍由安装流程负责，观察不会虚构操作或引用。引用可跨 Desktop 会话保留，
但摘要只包含本会话及当前窗口实际观测到的调用返回值；分配引用本身不证明任何结果。

每次调用保留最后一个通过校验的进度阶段；有效结果的安装阶段或结构化观察存在时优先使用。
不从错误或进程退出推断阶段。未观测到的阶段为 null，界面明确显示未观测到。响应丢失时仍可保留已知阶段和尝试次数，
同时将结果保留为未知。`pending`、`cancelled`、`failed`、`partial_failure`、`uncertain`、`rejected`、`unknown`
和 `succeeded` 沿用 Ops 原义；请求取消不证明操作已经取消。

独立 FIFO 最多保留 **64 个 Ops 调用快照**。重复的显式状态查询或取消请求是不同观察，不增加安装尝试次数。
记录只使用非阻塞锁且不执行 I/O；竞争、锁中毒或非规范引用会丢失快照并增加独立的进程累计丢失数。
插入工作量为常数且有界；摘要最多复制 64 个 Ops 快照和 512 个请求或操作记录。重置同时清空两个窗口及淘汰数，
保留两个进程累计丢失数和会话起点。重置前已开始的 Ops 调用不能重新填入新窗口，其排除会计为丢失。
重置不会删除权威 Ops 引用存储或 journal，不取消工作，也不改变服务状态。

此区不包含耗时或请求、用户操作计数，不执行探测、轮询、journal 读取或写入重放，也不存储原始 stdout/stderr、
目标摘要、路径、endpoint、配置、执行进程 PID 或历史记录。页面和现有逐字预览、复制流程共用原生白名单投影。
离开页面或切换连接上下文会关闭预览，原生采集仍按进程继续。空窗口不能证明不存在安装失败。

## 本次会话视图与显式复制

没有活动连接、连接状态加载失败时仍可使用本次会话视图。进入标签页时读取一次原生摘要，不轮询，也不探测 Server 或 CLI。
用户手动刷新时才读取新快照。HTTP 与原生用户操作的样本总数、各类结果和最小、平均、最大毫秒耗时分别呈现。
空观察只描述当前终态记录窗口，不表示 Server 没有活动。读取失败时提供显式重试，不将未知数据替换成零；不支持的摘要版本
显示不可用。

页面展示 UTC 会话起点、窗口起点和摘要生成时间，以及保留数量、上限、淘汰数量和进程采集丢失数量。
存在淘汰或丢失时提示覆盖不完整，可以展开查看纳入统计的操作。本地安装及运维使用单独标明的快照表格、覆盖和丢失计数，
分别展示空窗口和未接入状态，不抓取 journal。

重置前使用应用内确认，说明不会影响 Memory 或正在执行的操作，且保留进程采集丢失总数。原生重置后重新读取新窗口。
无法确认重置成功时，提示用户先刷新检查再决定是否重试，不提前清空已有快照，也不自动重复重置。

预览是独立的显式操作。弹窗调用 `preview_session_diagnostics`，将返回的准确文本放入只读、可选择的文本框。
生成中、失败和重试状态分别呈现。复制只将这份文本写入系统剪贴板，不重新获取、重建、脱敏或裁剪内容。
页面显示复制中、成功或失败；失败后继续保留原始文本，供用户选择并手动复制。打开或关闭预览不会复制或上传数据。
复制等待期间不能重复提交，晚到的预览和复制反馈不会填入新的弹窗。

共享模态框支持 Escape、Tab/Shift+Tab 焦点约束及关闭后焦点恢复。离开视图，或原生上下文代次、等待状态变化时，会卸载视图、
关闭预览并使晚到的读取响应失效；进程范围的原生采集继续独立运行。中英文、现有主题、键盘焦点、带标签的可滚动表格和
有高度限制的预览区域支持小窗口及放大显示。

`desktop/tests/installed_session.py` 已接入 Windows 安装包验收 harness。在真实 Memory 流程后，它检查两种测量表格，
预览原生投影，通过真实系统剪贴板复制、粘贴并逐字比较，还验证取消及确认重置和新的原生窗口。
剪贴板 mock 和浏览器布局夹具不代表安装包剪贴板或 macOS 验收；平台执行证据记录在 Desktop 验证指南中。
