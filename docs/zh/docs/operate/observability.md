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
Topic 阻塞的 `source_after` 和已知的 `source_through` 表示 Source journal 位置。准确选择终点未知时，阻塞日志
省略 `source_through`，仍保留尝试、请求和 token 计数。
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
不会自动采集宿主中无关的模型调用。

使用环境文件部署时，将 Tracing 与 OpenTelemetry 变量写入传给 Server 的显式文件。
优先级规则见[环境配置](../get-started/configure-server-environment.md)。
