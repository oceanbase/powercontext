- Proposal Name: `distributed_server_workers`
- Start Date: 2026-09-03
- Tracking Issue: [oceanbase/powercontext#1430](https://github.com/oceanbase/powercontext/issues/1430)
- Related RFCs: [RFC 0011](0011_remote_access_architecture.md)、[RFC 0019](0019_local_source_memory_runtime.md)、
  [RFC 0020](0020_runtime_backed_memory_remote_access.md) 和 [RFC 0046](0046_observability_foundations.md)

# Summary

PowerContext 在本地和分布式后台执行中统一使用数据库持久化的 Work Ledger。API 副本无需进程粘性即可入队和查询；
Scheduler 通过 fence 选出一个 leader，并持久保存 keyset 扫描位置；Worker 只领取当前空闲容量的任务，以数据库时间续租，
并安全恢复遗留 attempt。模型调用保持 at-least-once，但同一逻辑 Source window 最多提交一个数据库结果。

分布式模式只使用 OceanBase 作为协调后端。同一套 ledger 也替代 `single_node/all` 中的 APScheduler SQLite sidecar，
从而只保留一套需要理解和测试的执行协议。Redis、Kafka、公开 Queue SPI、任意用户代码、DAG、优先级、多区域共识以及
Connector 自有 checkpoint 不属于本 RFC。

# 决策

本设计有意减少真相来源：

- 分布式模式下，OceanBase 同时持有任务状态、租约、cursor 和领域提交。
- 执行为 at-least-once。崩溃后 provider 调用可能重复，但 fence 和领域 cursor 会阻止旧 attempt 提交。
- Memory activation 和 Experience incubation 首先接入 ledger；后续 handler 的领域 checkpoint 仍由各自模块持有。
- 默认保持 `single_node/all`；分布式进程必须且只能选择 `api`、`scheduler` 或 `worker`。
- Work payload 只保存带版本的引用和数字窗口边界，不保存 Source 正文、prompt、模型响应、credential 或原始异常。

这避免了数据库、broker 和本地 sidecar 多套协议自然积累的熵。新增复杂度被固化为数据库约束、小型状态机、fencing token、
启动校验和兼容性声明，而不再依赖“系统只有一个进程”之类的隐性经验。

# 架构

```text
API / Scheduler
      |  短事务：确定窗口、去重、入队
      v
OceanBase Work Ledger
      |  短事务：领取 lane head 并签发 lease fence
      v
Worker ---- 在事务外执行 provider/model 调用 ---->
      |  短事务：验证 fence，提交领域状态 + cursor + work
      v
一个逻辑数据库结果
```

Ledger 包含：

| 表 | 职责 |
| --- | --- |
| `pc_work_items` | 带版本任务、scope、lane sequence、安全 payload、状态、租约、attempt 预算及安全结果/错误 |
| `pc_work_attempts` | append-only owner/fence/时间/结果审计和非敏感 trace identity |
| `pc_work_lanes` | 每个一致性域的严格 sequence 和唯一 active head |
| `pc_work_keys` | 当前逻辑窗口的唯一占位 |
| `pc_scheduler_leases` | Scheduler leader 与 migrator 的 owner、单调 fence 和数据库时间 expiry |
| `pc_scheduler_scans` | 各 discoverer 的下次执行时间和 keyset continuation |
| `pc_runtime_members` | 存活角色以及 schema/payload/behavior 兼容性声明 |
| `pc_rate_limit_windows` | 可选共享固定窗口计数，以 principal 摘要和 policy 为键 |

Memory 窗口的 logical key 是 handler kind、scope、cursor name、cursor generation 和 previous cursor 的 SHA-256。
入队时的 high watermark 与 `through` 保存在 payload 中，但不进入 key。因此 manual 与 scheduled discovery 面对同一个
未推进 cursor 时会加入同一任务；之后到达的 Source 由下一窗口处理。

Memory 和 Experience 使用独立 lane（散列前分别为 `memory:{scope}` 与 `experience:{scope}`）。不同 lane 可以并行，
同一 lane 严格按 sequence 推进。终态失败会保留 logical-key 和 lane 占位，直到 operator retry 或 cancel，避免 poison
window 被无限重建。

# 状态机

```text
queued ----> running ----> succeeded
  |             |----> retry_wait ----> running
  |             |----> failed
  |             `----> cancelling ----> cancelled
  `-----------------------------------> cancelled

failed -- 显式 operator retry --> queued
```

取消在 work row 上线性化。如果取消先于最终事务，Worker 丢弃已经准备好的 provider 结果，并把 attempt 完成为
cancelled；如果成功已经提交，取消返回 `409`。取消某个 operation 不会暂停该 scope 后续的 discovery。

# 锁与事务协议

基线是 OceanBase 4.3.5 默认 Read Committed。正确性不依赖无锁读取、单条谓词 update、`SKIP LOCKED` 或
`GET_LOCK()`。所有路径采用统一锁顺序：

```text
scheduler lease（仅 Scheduler 路径）
  -> lane
  -> logical key
  -> work item
  -> domain cursor/head
```

稳定存在的 lease/lane row 会被显式锁定。缺失 row 通过唯一键插入，冲突后重试。claim 先做带索引且有上限的候选扫描，
再逐 lane 用短事务领取。Worker 永远不会预取超过空闲执行槽的任务。

Scheduler 的 enqueue 和 scan 更新都会重新验证精确 leader owner、fence 与数据库 expiry。Worker heartbeat、失败、
取消收敛和最终提交都会验证 `(work_id, owner, fence)`。所有 expiry 判断使用数据库的 `CURRENT_TIMESTAMP(6)`；
应用时钟只用于 poll 和 deadline。

Provider、Connector、网络和文件系统调用绝不处于数据库事务中。Worker 最终事务先验证 claim 未过期且未取消，随后调用
handler 的领域 commit。Memory 与 Experience 复用现有 cursor/head CAS。领域写入、cursor 推进、attempt 完成和
Work success 要么一起提交，要么一起回滚。

# Scheduler 与 Worker

每个 discoverer 持久保存 keyset continuation，每页最多扫描 100 个 scope。崩溃后重复扫描是安全的，因为 logical key
会去重。旧 leader 在另一个 Scheduler 获得更高 fence 后，不能继续 enqueue 或保存 continuation。

Worker 只 claim 当前空闲槽。attempt lease 默认 120 秒，每 30 秒续租。过期 attempt 会先写完审计并进入 retry，再由
更高 fence 重新领取。可重试错误使用 full-jitter 指数退避，默认从 2 秒开始，上限 5 分钟，最多 5 次自动 generation
attempt。无法识别的 payload version 保持在 lane head 可见，并使 Worker readiness 成为 `misconfigured`；不得猜测、
静默丢弃或跳过。

# 进程角色

配置新增 `deployment`、`coordination`、`worker`、`operations` 和 `rate_limit` 组。

- `single_node/all` 在同一进程运行 API、Scheduler 和 Worker，但仍走统一 ledger。数据库 owner lease 会拒绝误启动的
  第二个实例。
- `distributed/api` 暴露 HTTP、Dashboard、MCP、鉴权、共享限流、入队与 operation 查询，不运行 discovery 或后台
  Worker handler。
- `distributed/scheduler` 只暴露 health 和 metrics，不持有 provider credential，只进行有界 discovery。
- `distributed/worker` 只暴露 health 和 metrics，仅持有所注册 handler 必需的 provider credential。

分布式模式要求 OceanBase，并拒绝 `role=all`、SQLite、seekDB 和显式 host-local External Skill target。所有角色可以
复用同一镜像，但应使用独立的最小权限数据库账号。DDL 只属于 migrator 账号，分布式角色绝不自动执行。

每个进程用 boot-unique member identity 心跳上报 build version、当前 schema/payload range 与非敏感
`behavior_revision`。Member metadata 不保存 credential、secret URL、authorization 数据或允许离线猜测 secret 的 hash。

# HTTP 与 Client 契约

`openapi/powercontext.yaml` 仍是唯一来源。`POST /v1/memory/flush` 的行为是：

- 没有待处理 Source，或在等待预算内完成：`200 FlushMemoryResponse`；
- 仍为 queued、running 或 retry_wait：`202 OperationAccepted`，带相对 `Location` 和 `Retry-After: 2`；
- 同一 logical key 已被失败任务阻塞：`409 operation_blocked`，返回 operation ID。

`Prefer: respond-async` 立即返回句柄；`Prefer: wait=N` 在最长 30 秒内等待，默认 10 秒。Operation endpoint 提供授权后
的 get/list、乐观并发 cancel 和 operator retry。Mutation body 必须包含 `expected_version`，非法或过期转换返回 `409`。
公开状态为 `queued`、`running`、`retry_wait`、`cancelling`、`succeeded`、`failed`、`cancelled`。内部 maintenance work
不会通过 Operation API 暴露。

`PowerContextClient.flush_memory()` 通过 submit + poll 保持原有同步式返回；总 deadline 到期时抛出携带 operation ID 的
`OperationPendingError`。需要显式控制的调用方使用 `submit_memory_flush()`、`get_operation()`、`list_operations()`、
`cancel_operation()` 和 `retry_operation()`。

Operation endpoint 不投影为 MCP tool。分布式模式强制 FastMCP stateless HTTP，连续请求可以命中不同 API 副本。
Server-to-client elicitation 和 sampling 关闭；Workstream picker 返回结构化 `needs_selection`。内部 ASGI bridge 会传递
已经认证的 principal，工具可见性绝不作为授权边界。

# 健康、关闭、保留与可观测性

Liveness 只表示进程能响应。API readiness 要求 database、schema、authentication policy、membership 和 behavior
兼容；缺失 Scheduler 或 Worker 只让 API degraded，仍允许持久入队和读取。健康的 Scheduler standby 也是 ready。
缺少必需 provider 或 handler version 的 Worker 停止 claim，并报告精确失败项。

SIGTERM 时，API 先摘除 readiness；Scheduler 停止 discovery 并条件释放 lease；Worker 停止 claim，继续为在途任务
heartbeat，并最多 drain 90 秒。非优雅退出由 lease expiry 恢复。

成功与取消的 work/attempt 默认保留 30 天。持久 maintenance handler 每批最多删除 500 条，并清理过期限流窗口。
阻塞失败任务在 operator 处理前不得删除。未来 Source retention 必须把所有非终态 work window 当作 retention root。

Metric 只使用 kind、status、outcome、role 和 error category 等有界标签，覆盖 queue depth/age、claim/attempt latency、
lease expiry、retry、throughput、leader change 和 member count。Scope、principal 和 work ID 不能作为 metric label。
Enqueue、claim、execute、commit 和 retry 使用独立 span；retry attempt 通过 span link 关联。

日志、span、work row 与 attempt row 均不得包含 Source 正文、prompt、模型输出、credential、authorization header 或完整
secret URL。持久错误只保存有界 category 与 code。

# Schema 与发布

Alembic 管理 forward-only schema chain。`powercontext server migrate` 获取数据库 lease 后升级到 packaged head。新库从
baseline 创建；已知且完整的 legacy schema 先校验，只应用明确识别的扩展，再 stamp 并创建 ledger。未知或部分安装的
schema 直接拒绝。单机启动自动运行同一迁移链；分布式角色只读校验当前 revision。

Schema 遵循 expand、mixed-version deploy、contract。N+1 必须能读取 N/N+1 layout，破坏性删除最早在 N+2。
实际混部期间 Worker 支持当前和前一版 payload；`emit_payload_version` 在旧 Worker 排空前继续发送旧格式。

部署顺序是 migrate、Worker、Scheduler、API；回滚顺序相反，并在恢复旧 Worker 前排空新版 payload。Bridge release
先让 `single_node/all` 全部走 Work Ledger；只有 APScheduler 执行路径完全退出后，才允许同一数据库扩为多角色。
旧 sidecar 只作为 operator 备份产物保留，系统不自动删除。

# 验收

验收覆盖：两个 API 副本间 round-robin 的 HTTP、Dashboard API 与 stateless MCP；不同 lane 并行与同 lane 串行；
manual/scheduled 去重；Worker 在 claim 前、provider 执行中、最终事务中和 commit 后的崩溃恢复；Scheduler takeover
fencing；retry、cancel、operator recovery、retention 与隐私；以及 OceanBase 4.3.5 真实多进程测试。Golden
schema/payload fixture 覆盖混合版本与升级回滚。SQLite 和 seekDB 只保留单机回归支持。

# 未采用方案

- Redis 或 Kafka 会在吞吐尚不需要时引入第二个持久真相和跨系统提交问题。
- 公开 Queue/Coordinator SPI 会在不存在第二种实现时过早冻结多套协调语义。
- `GET_LOCK()` 是 session 级锁，不适合作为连接池环境中的 fencing 原语。
- 单机继续使用 APScheduler 会保留两套细微不同的执行和恢复协议。
- 外部 provider 不可能实现真正 exactly-once；可执行的契约是 at-least-once execution 加 at-most-one fenced
  database commit。
