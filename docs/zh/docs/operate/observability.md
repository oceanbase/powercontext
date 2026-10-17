---
title: 日志、指标与 Tracing
description: 通过日志定位请求，并查看 Server 指标与 Trace。
---

# 日志、指标与 Tracing

用日志定位操作，用指标观察整体行为，用 Trace 跟踪请求经过的阶段。
这些信号描述服务行为，不能代替对已保存 Memory 或 Source 证据的检查。

## 日志

前台 Server 将运行日志写入进程输出。可配置 `POWERCONTEXT_SERVER_LOGGING_LEVEL`（默认 `INFO`）、
`POWERCONTEXT_SERVER_LOGGING_FORMAT`（`console` 或 `json`）和 `POWERCONTEXT_SERVER_LOGGING_ACCESS`（默认 `true`）。
原生个人服务通过 `powercontext service status` 查看 journal 查询条件或日志路径；Docker 部署查看容器日志。

用响应中的 `X-PowerContext-Request-ID` 关联失败请求与诊断记录。宿主指南还说明不含内容的召回与采集诊断；
没有上下文结果可能是正常情况，不一定代表服务故障。

### Operational fields 与关联

Event 示例、字段扩展和 regression checks 见[开发规范](../../development/observability.md)。

JSON 和 console logs 使用相同的 operational field allowlist，未登记的 extras 不会输出。
两种格式都保留 application/transport 的结果与错误诊断，以及嵌套事件（包括 code stage）的 worker 关联字段。
Code 专用诊断字段仅在 `code.*` events 中输出。

| 字段 | 含义 |
| --- | --- |
| `service_name`、`service_version`、`service_role`、`service_instance_id` | Service name（遵循 `OTEL_SERVICE_NAME`）、已安装 package version、process role 和生成的 process identity |
| `event`、`operation`、`unit`、`outcome` | Event name、已定义的 application operation 或 stage、工作单元和结果 |
| `request_id`、`trace_id`、`span_id` | 已绑定的 ingress request 和当前 trace context；没有上下文时省略 |
| `worker_id`、`family`、`binding`、`request_generation`、`supervisor_group`、`supervisor_generation` | 一次 worker attempt 及其已接受的工作和 fence，用于关联 child 与 supervisor logs |
| `duration_ms`、`error_code`、`error_category` | 毫秒耗时、诊断 code 和有限取值的 operational error category |

Server role 为 `all`（API 与 supervisor）、`api` 或 `background`，child process 使用 `worker`。
显式配置 logging 但没有指定 deployment role 时使用 `server`。JSON identity 字段对应 trace resource attributes：
`service.name`、`service.version`、`service.instance.id` 和 `powercontext.service.role`。
未安装 distribution metadata（例如源码 checkout 或 frozen bundle）时省略 `service_version`。
新进程获得新的 instance ID，worker retry 获得新的 `worker_id`。这些 execution identifiers 不作为 metric labels。

嵌套 request logs 自动继承 request ID，event 显式提供 ID 时保留该值。Scheduled processing 不继承 supervisor
启动时偶然存在的 request ID，包括 supervisor control logs。关闭 recording 后仍可能有 trace ID，
但它不能证明 backend 保存了 trace。
配置 child logging 本身也不等于完成了跨进程 trace propagation。

常用 outcome 为 `success`、`failure`、`cancelled` 和 `noop`。Admission block 使用 `blocked`，不记为 success 或 failure。
Worker 遇到持久处理阻塞时，以 `WARNING` 级别输出 `artifact_processing.worker.blocked`，
outcome 和 error_category 均为 `blocked`，并保留阻塞的诊断代码和 stage。
Worker control result 还可以是 `cursor_conflict`、
`head_conflict` 或 `leadership_lost`。`artifact_processing.worker.returned` 表示 child 返回了结果；
`artifact_processing.worker.acknowledged` 表示 supervisor 验证了 durable acknowledgement。
Child 返回结果不能单独证明业务完成。强制终止进程可能来不及输出 child terminal event，
此时通过 supervisor 的 interruption 或 failure 诊断。

不适用时省略 `error_category`。其取值为 `authentication`、`authorization`、`configuration`、`rate_limit`、
`dependency`、`timeout`、`not_found`、`conflict`、`invalid_request`、`internal`、`cancelled` 和 `blocked`。
Server spans 在 `powercontext.error.category` 中使用相同词汇，各 boundary 根据实际观察的结果分类。
已有诊断 code 继续保留，供进一步排查。
Supervisor 的 worker 和 wait spans 在跨进程后保留 child 的有限错误分类。
策略阻塞与 child 取消分别使用 `blocked` 和 `cancelled` outcome，不设置 error span status；
模型失败保留 timeout、configuration 或 dependency 分类。
Child 取消时，supervisor 以 `INFO` 级别输出 `artifact_processing.failed`，outcome/category 为 `cancelled`，
诊断代码为 `worker_cancelled`。Application spans 根据已映射的诊断代码分类，
即使敏感 exception values 已从 logs 和 traces 中排除，也保留正确分类。

`DEBUG` 级别的 `code.*` events 保留 `seconds`，并增加 `duration_ms`。其 `operation` 为 stage name（`code.<stage>`），
与 span identity 一致；已有的 requested query kind 改放在 `code_operation`。Code diagnostics 可以包含 `selected_count`、
`hit_count`、`hit_count_is_lower_bound`、`cache_bytes`、`status`、`limitations` 和 `reason`。
操作系统支持时，`process_lifetime_max_rss_bytes` 与 `children_lifetime_max_rss_bytes` 表示 process lifetime
的 RSS high-water mark，单位为 bytes。它们不是单次 request allocation，也不是同一时刻的 process-tree 总量，不能相加。
失败的 code stage 保留诊断 `error_code`；领域错误还保留 `status_code`。日志与 span 使用这些值区分超时、冲突、
无效请求和内部故障。

Server 创建的 workers 使用配置的 log level 和 format。通过 SDK 导入或运行 Runtime 时不配置 global logging，
由 embedding application 管理 logging。Logging bootstrap 失败不会使 processing 失败。
Formatting 或写入失败时，Server handler 向 stderr 输出无内容的 `observability.log_failed` 通知，每个 handler
最多输出一次，不会转储失败 record。Operational logs 保持 best-effort，不作为 durable audit 或 billing ledger。
Identity 和 error 字段为新增字段；console 行现在包含 identity。

## 指标与就绪状态

默认在 `/metrics` 提供指标。在 `enforced` 模式下，`/metrics` 和 `capabilities` 请求需要以具备
`server.observe` 权限的 Principal 认证。具体认证和权限配置请参见[诊断与恢复](troubleshoot.md)。
`/health/live` 存活检查和 `/health/ready` 就绪检查保持公开。

`/metrics` 输出 Prometheus 格式的指标。常用指标包括：

| 指标 | 用途 |
| --- | --- |
| `powercontext_server_transport_requests_total` | 按传输方式、操作和结果统计请求量 |
| `powercontext_server_transport_request_duration_seconds` | 观察请求耗时 |
| `powercontext_server_application_operations_total` | 观察应用操作的成功与失败 |
| `powercontext_server_runtime_ready` | 判断 Runtime 是否可以接收操作 |

`powercontext_server_runtime_ready=1` 只表示 Runtime 可以接收操作。即使 Server 处于 `degraded` 状态，该指标也可能为
`1`，不能据此判断模型能力完整可用。请结合 `/health/ready` 返回的 `status` 和 `powercontext capabilities` 一起判断。

如需关闭 `/metrics`，请设置 `POWERCONTEXT_SERVER_METRICS_ENABLED=false`，具体配置方式见[配置选项](configuration.md)。

通过 `powercontext ready` 与 `powercontext capabilities` 区分数据库不可用和可选模型 Provider 降级。
状态定义与恢复步骤见[诊断与恢复](troubleshoot.md)。

## Tracing

安装 `tracing-otlp` extra，启用 `POWERCONTEXT_SERVER_TRACING_ENABLED=true`，并配置 OTLP HTTP 接收端。
完整步骤见 [Phoenix](trace-with-phoenix.md) 或 [Langfuse](trace-with-langfuse.md)。
缺少 extra 时启用 Tracing 会导致启动失败。PowerContext 跟踪自身的传输、应用和模型调用，
不会自动采集宿主中无关的模型调用。Model span 关闭自动 exception event 和 status description，
避免 provider error body 导出 Source、prompt 或 response 内容；仅保留有界的 `error.type` 和 error status。

使用环境文件部署时，将 Tracing 与 OpenTelemetry 变量写入传给 Server 的显式文件。
优先级规则见[环境配置](../get-started/configure-server-environment.md)。

### Background service 与 observation freshness

拆分 API/background roles 需要 OceanBase。SQLite 和 embedded seekdb 继续使用 `all` role。

`background` role 在 `HTTP_HOST` 和独立的 `POWERCONTEXT_SERVER_METRICS_BACKGROUND_PORT`（默认 `8001`）上
只提供 `/health/live`、`/metrics`，不提供 business API、MCP、dashboard 或 OpenAPI。API/all roles 继续使用原 HTTP
端口。关闭 `METRICS_ENABLED` 也会关闭 background HTTP listener。认证和 `server.observe` 授权复用 API metrics 的
Server profile；非 loopback bind 遵循现有 Server 策略。同一 host 部署多个进程时，请在 background 环境文件中配置独立端口。

启用 metrics 后，readiness 在启动时及每 30 秒评估一次。Scrape 不调用模型、不 ping 数据库，也不刷新 readiness。
Inference probe 保留原有正常结果 300 秒、临时失败 30 秒的缓存。`readiness_evaluated_timestamp_seconds` 表示 aggregate
评估时间，不能解释为每个 provider 都刚完成了一次真实请求，也不表示单个 cached probe 的数据年龄。

| Metric（前缀 `powercontext_server_`） | 含义 |
| --- | --- |
| `readiness_state{state}` | `ready`、`degraded`、`not_ready` 的 one-hot 状态；兼容指标 `runtime_ready` 仍只表示可接收操作 |
| `dependency_state{dependency,state}` | 有界 dependency 状态；dependency 缺失表示未配置或未观察，不能理解为健康的零值 |
| `readiness_evaluated_timestamp_seconds` | 最近一次 aggregate 评估的 Unix 秒，评估可能使用 cached probe |
| `artifact_processing_supervisor_alive`、`artifact_processing_leader`、`artifact_processing_degraded` | 按已注册 family 记录本进程 supervisor task、fence 持有状态和 discovery 状态 |
| `artifact_processing_blocked` | 本地保留的 policy-blocked invocation 数量，不表示 worker failure |
| `artifact_processing_discovered_timestamp_seconds`、`artifact_processing_unacknowledged_observed_timestamp_seconds` | 最近成功 discovery 和最近 unacknowledged count 查询的 Unix 秒；两者不是同一次观察 |
| `artifact_processing_last_success_timestamp_seconds` | 最近一次 supervisor 验证 acknowledgement 的时间，不是 child return 时间 |
| `artifact_processing_oldest_observed_pending_timestamp_seconds` | 本地 ready/running/retry 中最早且仍保留的 admission 时间；零表示没有观察到 |
| `artifact_processing_invocation_duration_seconds{family,outcome}` | 已完成 reap 的 invocation attempt histogram，包含 launch、execution、acknowledgement；每次 retry 单独采样 |
| `model_usage_queue_depth`、`model_usage_queue_capacity` | 父进程 recorder 的 queue 和 capacity；不包含正在写入的记录和 child queue |
| `model_usage_dropped_records_total`、`model_usage_write_failures_total` | 父进程 recorder 与已收到的 child report 中累计丢失记录及失败或结果不确定的写入次数；不是 billing ledger |
| `model_usage_worker_reports_total{outcome}` | 收到 recorder 计数的 `received` attempt，以及缺少观察的 `missing` attempt；missing 不表示零丢失 |
| `metrics_collection_failures_total` | Processing snapshot 刷新失败次数；失败时可能保留上一次 snapshot |

Processing timestamp 为 `0` 表示尚未发生该观察。计数、histogram 和 timestamp 属于当前 process/supervisor 生命周期，
重启或移除后会 reset 或消失。Pending timestamp 只提供本地已观察工作的 age lower bound，不代表最早的持久化 Source
或全库 backlog；尚未发现的工作、retry-cache eviction 和重启都可能让历史不可见。应结合 backlog 和 observation freshness
解释。健康的 idle worker 不要求近期有 success；progress alert 应同时检查存在 pending work，policy block 要单独处理。
Invocation outcomes 为 `success`、`failure`、`blocked`、`cancelled`、`conflict`、`timeout`；等待 leadership cleanup 的保留 slot
不会被重复统计为已完成 reap。

Processing labels 仅使用已注册 family 和有界 outcome，不包含 scope/worker/request identity。Scrape 只读取内存 snapshot；
ready/retry/running 观察工作量受 scheduler limits 限制，不随数据库总记录数增长。内置 spawn worker 在 recorder 关闭后，
通过成功或失败结果回传两个累计丢失计数。所属 supervisor 每个 attempt 只累计一次，不依赖 acknowledgement 或 tracing。
一个 child invocation 内创建的多个 recorder 会合并为同一份 report；scrape 不会重复计数。
Endpoint 不提供 child queue depth/capacity 的实时值。运行中的 child 尚未上报；崩溃、强制终止，或从未观察到 recorder 的
invocation 记录为 `missing`，不能当作零丢失。该投影不持久化，父进程重启或 child report 丢失会造成观察缺口。
Custom launcher 需自行连接 report 与 observer；API-only 进程无法观察另一个 background service 的 children。
应采集各 API/background targets，并通过 target labels 区分
role/instance。Target 或 sample 缺失不等于零故障；不要把各进程 snapshot 相加当作持久化总账。

### Worker execution traces 与 integration coverage

安装 `tracing-otlp` 并启用 tracing 后，Server 自动组合的 worker 在启动时仅捕获 W3C `traceparent`/`tracestate`。
每个 child 创建自己的 provider/resource，加入对应 supervisor execution，并为真实 family、Topic Memory model composition
启用 instrumentation。Memory 使用已有 commit stages；Topic Memory 增加 `topic_memory.prepare` 和 `topic_memory.commit`。
Child 的 `artifact_processing.worker.execute` span 描述 child result；业务完成仍由 supervisor 验证 acknowledgement。

Scheduled/coalesced invocation 使用独立 supervisor trace，不继承 capture request ID，不让 HTTP request 一直保持打开，
也不声称它与所有 Source 已有持久化的因果关联。Replay 不会恢复入站 trace context。Custom launcher 自己负责 bootstrap；
SDK 默认不配置 tracing，除非 embedding application 提供可选 worker wrapper。Parent-based sampling 决策会传到 child。
Child export/shutdown 是 best-effort，shutdown 最多等待 1 秒；强制终止可能留下不完整 spans。显式启用 Server tracing 时
缺少 exporter 仍会导致启动失败；child telemetry setup/export failure 不改变 domain result。

| Integration boundary | Coverage |
| --- | --- |
| HTTP、MCP ingress/internal bridge | 保留 W3C propagation 和 logical-operation 语义；不跟踪 infrastructure scrape |
| Python Client | 使用 application 配置的 provider，创建 outbound spans 并注入 context |
| Pydantic AI capability | 使用真实 Agent/capability、Python Client 和本地 HTTP Server/OTLP receiver 验证；模型为 fixture，不代表外部 provider 验收 |
| Built-in Memory、Topic Memory spawn workers | 验证真实 entrypoints、本地 provider HTTP success/timeout、child spans 和 OTLP reception |
| 其他 native host integrations | 保留 host diagnostics；本覆盖不声称已验证自动 host trace propagation |

OTLP 测试使用真实本地 protobuf HTTP receiver，不提供 production trace database。存储和 backend query 验证请按
Phoenix/Langfuse 或所选 backend 的部署文档执行。Metrics dashboard、trace storage、log shipping 和 alert delivery 属于独立部署能力。
