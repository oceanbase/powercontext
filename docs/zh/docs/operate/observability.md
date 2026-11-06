---
title: 日志、指标与 Tracing
description: 通过日志定位请求，并查看 Server 指标与 Trace。
---

# 日志、指标与 Tracing

用日志定位操作，用指标观察整体行为，用 Trace 跟踪请求经过的阶段。
这些信号描述服务行为，不能代替对已保存 Memory 或 Source 证据的检查。


需要自动配置的本地 metrics、traces 和 logs 后端时，参见[参考监控环境](reference-observability-stack.md)。

## 日志

前台 Server 将运行日志写入进程输出。可配置 `POWERCONTEXT_SERVER_LOGGING_LEVEL`（默认 `INFO`）、
`POWERCONTEXT_SERVER_LOGGING_FORMAT`（`console` 或 `json`）和 `POWERCONTEXT_SERVER_LOGGING_ACCESS`（默认 `true`）。
设置 `POWERCONTEXT_SERVER_LOGGING_OTLP_ENABLED=true` 后，Server 会通过 OTLP/HTTP 将同一字段白名单发送给
Loki 或 OpenTelemetry Collector。托管 `observability up` 和 `connect` 配置还会写入 OTLP 地址、认证头以及有限的
`OTEL_BLRP_*` 批处理参数。控制台或 JSON 输出仍可用于本地审计，但不是参考 Collector 的输入。导出队列满、
Collector 不可达或导出失败不得改变业务结果。
原生个人服务通过 `powercontext service status` 查看 journal 查询条件或日志路径；Docker 部署查看容器日志。

### 文件输出、轮替与压缩

设置 `POWERCONTEXT_SERVER_LOGGING_FILE=true` 启用文件输出，Server 自动创建目录，将运行日志写入文件以替代 stdout。标准库 `RotatingFileHandler` 默认在接近 20 MiB 时轮替，每个活动文件最多保留十个 gzip 归档。轮替大小、保留数量和压缩方式可按存储及 CPU 预算调整；重启后继续追加活动文件，无需外部轮替工具。

| 环境变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_SERVER_LOGGING_FILE` | `false` | 写入托管文件以替代 stdout |
| `POWERCONTEXT_SERVER_LOGGING_DIRECTORY` | 用户数据目录下的 `logs/` | 日志目录，遵循 `POWERCONTEXT_HOME` |
| `POWERCONTEXT_SERVER_LOGGING_MAX_BYTES` | `20971520` | 按大小轮替的阈值，必须为正数 |
| `POWERCONTEXT_SERVER_LOGGING_BACKUP_COUNT` | `10` | 每个活动文件最多保留的归档数，至少为一 |
| `POWERCONTEXT_SERVER_LOGGING_COMPRESSION` | `gzip` | 归档压缩方式：`gzip` 或 `none` |

```bash
export POWERCONTEXT_SERVER_LOGGING_FILE=true
export POWERCONTEXT_SERVER_LOGGING_DIRECTORY=/var/lib/powercontext/logs
powercontext server run
```

文件按已有 Server 进程角色命名为 `all.log`、`api.log` 或 `background.log`；显式配置但未指定角色时使用 `server.log`。Worker 共用所属 Server 的文件，每条日志保留执行身份。归档依次为 `all.log.1.gz`、`all.log.2.gz`，`.1` 为最新归档；关闭压缩时归档后缀为 `.1`、`.2` 等，切换压缩策略不会删除旧策略的归档。活动文件保持未压缩。大小阈值是近似值，单条大日志可能超过阈值。保留数量仅约束对应活动文件的归档，不清理目录内的其他文件。`POWERCONTEXT_SERVER_LOGGING_FORMAT` 选择可读文本或 JSON 格式，对 stdout 和文件输出均有效。

Worker 在发送端完成格式化和字段白名单过滤，通过各自专用的 socketpair 将日志发送给所属 Server。每条通道在 spawn 前创建，仅发送端传递给对应 Worker。接收器不监听网络端口，不反序列化 Python 对象。JSON 行封装保留输出中的换行，Worker 身份及 request/trace 关联在发送前确定。一个接收线程复用多个通道，一个写入线程消费有界的进程内队列；日志处理不额外创建进程。仅输出标准流时继续使用标准 stream handler。

每个文件使用独占写入锁，相同目录和角色的第二个 Server 会被拒绝；独立副本应使用不同目录。关闭日志前先停止 Worker。关闭时最多等待一秒排空 Worker 日志通道，再通过 `QueueListener.stop()` 完成队列中的输出，最后关闭 handler；文件写入、压缩和关闭使用原生 logging 清理，不另设超时；文件 sink 阻塞时可能延迟 Server 进程退出。残缺记录直接丢弃，不打印其内容；单个 Worker 通道故障不影响其他通道。发送等待有上限，发送失败后关闭该 Worker 的日志通道。接收器最多处理 256 条并发通道，单条 JSON 行封装在 UTF-8 编码后最多 256 KiB。超大记录、队列满、传输故障或进程被强制退出时可能丢失日志，日志仍为 best-effort。

macOS 和 Windows 个人服务默认启用托管文件输出，显式设置 `POWERCONTEXT_SERVER_LOGGING_FILE=false` 可保留标准流输出。Linux 个人服务默认继续使用 journal。宿主管理的 stdout/stderr 文件仍接收启动诊断和直接的标准流写入，不受托管 handler 的轮替策略管理。`powercontext service status` 显示宿主诊断日志目录或 journal 查询条件；自定义托管目录由 `POWERCONTEXT_SERVER_LOGGING_DIRECTORY` 指定。

目录不可用或已有写入者时，文件日志配置失败。运行期间的格式化、传输、入队、写入及压缩失败均使用不含内容的 `observability.log_failed` 通知。gzip 归档完整发布后才删除原始文件。通过 SDK 使用 Runtime 不配置日志，仍由嵌入应用管理。嵌入式 Server 启用文件输出时可以持有仅用于 Worker 的日志接收器，不重新配置嵌入应用的 logger。

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
| `service_target` | 可选的 Prometheus `instance` 端点（`host:port`，IPv6 带方括号）；父服务与所属 Worker 共用，用于精确筛选诊断日志 |
| `service_owner_role` | 所属 Server 角色（`all`、`api`、`background`、`server`）；Worker 继承父角色，实际 `service_role` 仍为 `worker` |
| `event`、`operation`、`unit`、`outcome` | Event name、已定义的 application operation 或 stage、工作单元和结果 |
| `request_id`、`trace_id`、`span_id` | 已绑定的 ingress request 和当前 trace context；没有上下文时省略 |
| `receipt_id` | 成功生成的单次可选 PreparedContext Receipt 的不透明 UUID；通过已绑定 `request_id` 关联日志，不记录查询或证据正文 |
| `worker_id`、`family`、`binding`、`request_generation`、`supervisor_group`、`supervisor_generation` | 一次 worker attempt 及其已接受的工作和 fence，用于关联 child 与 supervisor logs |
| `duration_ms`、`error_code`、`error_category` | 毫秒耗时、诊断 code 和有限取值的 operational error category |
| `stage`、`processing_stage`、`failure_reason`、`input_item_count`、`estimated_input_tokens`、`token_estimator_id`、`effective_budget`、`provider_request_id`、`finish_reason`、`failure_location`、`cause_exception_type` | 处理失败 stage/reason 与安全计数字段；`stage` 表示发出失败的位置或领域位置（Worker/supervisor stage、领域 block 名称或 `worker` 兜底），`processing_stage` 表示有界的领域阶段。详见 [处理失败诊断](../../development/processing-failures.md) |
| `queue_wait_ms` | Processing 的观察边界，以及从本地入队到派发的非负毫秒数；无法取得排队耗时时省略该值 |
| `last_error_code`、`last_error_stage`、`last_error_at` | 进入规则阻止前最近一次 failed attempt 的诊断 code、stage 和 ISO-8601 UTC 时间；没有前置失败时省略 |
| `last_error_worker_id`、`last_error_trace_id` | 最近失败 attempt 的 worker 和 trace；可用于继续查日志或调用链，不表示 trace 已保存 |
| `transport`、`status_code` | 访问事件的传输类型和已观察到的 HTTP 响应码；代码诊断也可携带其映射状态码 |
| `provider`、`model_capability` | Provider 事件使用的有限 SDK 系统名及 `generation`/`embedding` 协议能力；OpenAI 兼容路由可统一显示为 `openai`，不表示实际供应商公司 |
| `provider_retry`、`provider_error_code` | SDK HTTP 尝试标记（`initial`、`retry`、`unknown`）和实际 MiniMax 数字业务码（非负 signed-32-bit 范围）；不携带响应消息 |

`reason` 是保留证据降级的有限原因。`atomic_memory.evidence.neutral` 事件表示已接受的证据无法被佐证，
因此解析为 `untrusted` / `unknown`；该事件不携带材料、声明正文或请求内容。其 `unit` 为保留事实的类型
（`source`、`declaration`、`manifest` 或 `revision`），`outcome` 为 `neutral`，`reason` 取
`schema_mismatch`、`digest_mismatch`、`no_manifest`、`decode_failed`、`legacy_unverifiable`、
`invalid_declaration`、`manifest_conflict`、`definition_missing` 或 `evidence_mismatch` 之一。
已登记的完整性原因以 `WARNING` 输出；可预期的历史缺口保持 `INFO` 或 `DEBUG`。`manifest_conflict` 这类完整性原因
可告警，并与“从未声明”区分开。

Server role 为 `all`（API 与 supervisor）、`api` 或 `background`，child process 使用 `worker`。
显式配置 logging 但没有指定 deployment role 时使用 `server`。JSON identity 字段对应 trace resource attributes：
`service.name`、`service.version`、`service.instance.id` 和 `powercontext.service.role`。
未安装 distribution metadata（例如源码 checkout 或 frozen bundle）时省略 `service_version`。
新进程获得新的 instance ID，worker retry 获得新的 `worker_id`。这些 execution identifiers 不作为 metric labels。

`service_target` 对应 Trace resource 属性 `powercontext.service.target`，不是生成的进程身份。
托管 profile 在 `OTEL_RESOURCE_ATTRIBUTES` 中设置 `powercontext.service.api_target`，拆分部署另设
`powercontext.service.background_target`。服务角色选取所属目标，并传给真实 spawned Worker。
外部单角色 profile 可直接设置 `powercontext.service.target`。目标必须与 Prometheus `instance` 的端点拼写相同，
不包含凭据、路径或查询参数。未知目标省略。旧日志可在全部实例筛选下查看，不能命中精确实例查询。
此字段不增加应用指标标签，也不把执行身份放入指标。
`service_owner_role` 对应 Trace resource 属性 `powercontext.service.owner_role`。诊断日志用它筛选服务角色，
选择 API 时不会混入后台服务的 Worker。旧父进程日志回退到 `service_role`；旧 Worker 的父角色保持未知。

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

Supervisor 在本地入队时输出 `artifact_processing.queued`，开始一次执行时输出
`artifact_processing.dispatched`，结束该次执行时输出 `artifact_processing.attempt.completed`。
`queue_wait_ms` 使用单调时钟，不包含入队前的策略等待和重试退避。
完成事件的 `duration_ms` 单独表示 supervisor 的执行耗时，与排队时间和 child 执行时间分开计算。
这些事件用于执行诊断，不能当作新发布 Artifact 的数量。

不适用时省略 `error_category`。其取值为 `authentication`、`authorization`、`configuration`、`rate_limit`、
`dependency`、`timeout`、`not_found`、`conflict`、`invalid_request`、`internal`、`cancelled` 和 `blocked`。
Server spans 在 `powercontext.error.category` 中使用相同词汇，各 boundary 根据实际观察的结果分类。
已有诊断 code 继续保留，供进一步排查。
Supervisor 的 worker 和 wait spans 在跨进程后保留 child 的有限错误分类。
策略阻塞与 child 取消分别使用 `blocked` 和 `cancelled` outcome，不设置 error span status；
模型失败保留 timeout、configuration 或 dependency 分类。
启用流式聚合（`POWERCONTEXT_SERVER_INFERENCE_GENERATION_STREAMING=auto`）时，`unit` 为 `inference` 的
`inference.generation.usage_missing` WARNING 表示一次 generation 请求成功完成、但 provider 未随流返回 usage，
该请求的用量统计会缺失。该事件不携带 prompt 或响应内容，也不影响本次请求的结果。
`auto` 已设置且模型名前缀不属于 `anthropic`、`openai`、`openai-chat`、`openai-responses` 时，`unit` 为
`inference` 的 `inference.generation.streaming_not_applied` WARNING 表示该模型仍走非流式请求。该事件不携带
prompt 或响应内容，也不改变本次请求的结果。
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

`scope` 表示 Runtime/worker 事件和 `application.scope.completed` 的所属 Scope。后者使用 `application` 单位，统计各 Scope 的参与次数，包括派发后的失败和取消。多 Scope 请求会为每个 Scope 记录一次参与，不能相加当作唯一请求数。span 属性 `powercontext.scope.id` 表示单一归属，`powercontext.scope.ids` 表示应用选择集。[业务与 Scope 看板](reference-observability-stack.md#scope-与业务看板)通过业务统计和日志提供筛选，不增加身份指标标签。

## 指标与就绪状态

默认通过 `/metrics` 输出 Prometheus 格式指标。在 `enforced` 模式下，`/metrics` 与 `capabilities` 需要
具备 `server.observe` 权限的 Principal 认证，参见[诊断与恢复](troubleshoot.md)。API 宿主的
`/health/live` 与 `/health/ready` 保持公开。

`background` 角色使用 `POWERCONTEXT_SERVER_HTTP_HOST` 和 `POWERCONTEXT_SERVER_METRICS_BACKGROUND_PORT`
（默认 `8001`）提供 `/metrics` 和公开的 `/health/live`。指标认证与授权规则和 API 宿主一致，
不提供业务 API、MCP、Dashboard、OpenAPI 或 `/health/ready`。
API 与后台进程同机部署时应使用不同端口。设置 `POWERCONTEXT_SERVER_METRICS_ENABLED=false` 会移除 `/metrics`；
对 `background` 还会阻止启动 listener。抓取后台指标不会执行 API 宿主的模型就绪探测。

`/metrics` 默认输出 Prometheus 格式。请求带 `Accept: application/openmetrics-text` 时协商为 OpenMetrics。
仅当 Server tracing 已启用且当前 trace 被采样时，请求和应用延迟直方图才附带 exemplar。exemplar 中的 trace ID
不是 metric label。采样不保证导出或保留成功，关联查询仍须确认 trace 已存储。常用指标包括：

### 被动观察与新鲜度

指标采集只复制 Runtime/Supervisor 已有的、有界的进程内观察。采集不查询数据库、不扫描积压、不刷新就绪状态、
不调用模型，也不等待 Worker；端点的认证与授权仍可能执行有界的数据库读取。后台 pending 观察、发现和就绪检查
独立于抓取产生观察。因此，抓取成功既不能证明依赖当前健康，也不能证明积压数据足够新；抓取不会调用模型。

下表指标名均以 `powercontext_server_` 开头。时间戳单位为 UNIX 秒，耗时和年龄单位为秒；计数单位见各行。
状态指标采用 one-hot 表示：当前 `state` 为 `1`，其他支持的状态为 `0`。缺少观察不等于健康的零值。
计数器与直方图桶在所属进程或 Runtime/Supervisor 实例生命周期内累计，实例重建时重置，抓取不会重置。
应使用能处理重置的 `rate`/`increase`，并保留 `instance` 和部署身份等抓取目标标签。

| 指标后缀 | 标签与含义 |
| --- | --- |
| `transport_requests_total`、`transport_request_duration_seconds` | `transport`、`operation`、`outcome`：外部请求数量与耗时 |
| `application_operations_total`、`application_operation_duration_seconds` | `operation`、`outcome`：应用操作数量与耗时 |
| `service_role_info` | `role`：`all`、`api`、`background` 或 `server`；值为 `1`，标识当前进程 |
| `runtime_ready` | 兼容性指标：Runtime 是否允许操作，不表示模型能力完整可用 |
| `runtime_readiness_state` | `state`：`unobserved`、`ready`、`degraded`、`not_ready` |
| `runtime_readiness_observed_timestamp_seconds` | 最近完成的就绪评估时间；评估可能使用缓存，首次评估前不存在 |
| `dependency_state` | `dependency`、`state`：`unobserved`、`disabled`、`ready`、`unavailable`、`timeout`、`misconfigured`、`not_ready`、`degraded`、`leader`、`standby` |
| `dependency_blocking` | `dependency`：该依赖阻止就绪时为 `1` |
| `dependency_observed_timestamp_seconds` | `dependency`：最近一次实际完成探测的时间；首次完成前不存在 |
| `dependency_last_success_timestamp_seconds` | `dependency`：最近一次实际成功探测的时间；首次成功前不存在 |
| `dependency_probe_duration_seconds` | `dependency`、`outcome`：实际探测耗时，不包含缓存命中 |
| `telemetry_collection_timestamp_seconds` | 最近成功复制被动快照的时间；首次采集前为 `0` |
| `telemetry_collection_failures_total` | 被动采集失败次数；失败保留先前观察 |
| `telemetry_log_records_dropped_total` | 进程自有 handler 或 Worker 接收端确认未能交付的记录数；干净的 EOF/reset 不计入丢失，未观测到的丢失不计数 |
| `http_responses_total{operation,status_code}` | 按实际状态码统计成功发给 ASGI Server 的最终 REST 响应头 |
| `http_response_observation_enabled` | 此进程启用 REST 响应观测时为 `1`；仅后台监听器为 `0` |

详细探测观察覆盖 `database`、`inference.generation`、`inference.rerank`、`inference.decision` 和
`inference.embedding`。同一 `dependency_state` 指标还包含有界总体检查：`runtime`、
`artifact_processing_supervisor`、已注册的 `artifact_processing.<family>`、`access_provider` 和
`authentication_provider`。同名依赖优先采用详细 Runtime 快照，每个依赖只输出一组 one-hot 状态。
探测时间戳和耗时仍表示实际探测，不是总体评估时间。禁用依赖没有探测时间戳或样本。
探测结果为 `ready`、`unavailable`、`timeout`、
`misconfigured` 或 `cancelled`；取消只增加耗时观察，不替换最近完成的状态与时间戳。
缓存命中既不更新实际探测时间戳，也不增加直方图计数。每个样本代表一次完整的探测刷新；提供方内部重试
（若有）包含在这次耗时中，不计为独立网络尝试。内置缓存通常保留结果 300 秒，`timeout`/`unavailable`
保留 30 秒；仅缓存到期不会自动执行探测。总体评估时间、实际探测时间、快照采集时间和 Prometheus 抓取时间
分别回答不同的新鲜度问题。

HTTP 响应计数排除健康检查、`/metrics`、MCP 挂载路径和内部 bridge。标签仅使用已声明的 operation ID；未知路径、
不支持的方法，以及无法识别已声明路由的前置拒绝归入 `unmatched`。计数覆盖认证、参数校验、重定向和外层异常
处理器产生的响应，只在 ASGI send 成功后记录。响应头发送前取消或断连没有 HTTP 状态码，不编造 `499` 或 `500`。
这些是 PowerContext 对外 REST 状态码，不是上游 Provider 状态、代理改写结果，也不能证明客户端已收到完整响应体。

流式响应发送 `200` 后失败，仍保留一次 `200` 响应观测。原有 transport 计数器在 HTTP 应用执行结束时只记录一次
最终结果，包含流式响应和 response background task 的失败；耗时和进行中数量也覆盖到该执行返回。
状态码和执行结果表达不同事实。访问日志仍按响应体完成尽力记录，可能缺少外层异常处理器的响应，不能用作响应
计数的数据源。参考 Collector 保留已有 `transport`、`status_code` 字段，不将它们提升为 Loki 索引标签。

启用标记可区分“新进程已启用但尚无响应”和“旧 Server 缺少指标”。某状态首次出现后才有对应序列；计数随进程
重启归零，不能补回上线前的状态分布。速率至少需要两次抓取，不能还原首次观察到的计数值。服务页显示状态码
速率和进程累计值；缺少指标或无流量不能证明错误比例为零。全服务总览的执行失败比例包含 transport 失败，
与 HTTP 5xx 比例不同。只有有效且大于零的执行总速率存在时，缺少失败序列才可解释为失败为零。
仅后台进程没有可分类的 REST 流量。

`powercontext_server_runtime_ready=1` 只表示 Runtime 可以接收操作。即使 Server 处于 `degraded` 状态，该指标也可能为
`1`，不能据此判断模型能力完整可用。请结合 `/health/ready` 返回的 `status` 和 `powercontext capabilities` 一起判断。

`runtime_ready=1` 可以与 `degraded` 并存，只表示可以接收操作。结合 `runtime_readiness_state`、依赖状态及
新鲜度判断，也可通过 `powercontext ready` 和 `powercontext capabilities` 区分数据库不可用与可选模型故障。
后台进程在启用指标时于启动阶段及每 30 秒评估就绪状态；`/metrics` 本身不触发探测。
首次评估完成前，或不执行就绪评估的嵌入式应用中，`unobserved` 是有效状态。

### 后台处理指标

下表后缀均以 `powercontext_server_artifact_processing_` 开头。逐 Family 指标使用 `family` 标签，额外标签
在表中注明。只导出已注册且名称合法的 Family。Scope、Worker、请求、Trace、内容、Provider 错误文本或
数据库 URL 均不作为指标标签。

| 指标后缀 | 含义 |
| --- | --- |
| `supervisor_present` | 进程级 `0`/`1`；纯 API 实例没有 Supervisor 不代表故障 |
| `supervisor_running`、`automatic_processing_enabled` | 逐 Family 的 `0`/`1` 生命周期与配置观察 |
| `supervisor_role` | 额外 `state`：`leader`、`standby` |
| `status` | 额外 `state`：`disabled`、`leader`、`standby`、`degraded` |
| `max_workers`、`available_workers`、`used_workers` | 配置容量与当前 Worker 槽位 |
| `ready`、`retry_wait`、`blocked_wait` | 本地就绪队列、保留的等待重试数（含阻塞项）和被阻塞 Scope 数 |
| `unacknowledged_requests` | 最近持久 pending 快照中的待处理 Scope 数，不是请求 generation 数，也不是抓取时的实时查询 |
| `requested_snapshot_timestamp_seconds` | 上述 pending 快照的数据库 UTC 观察时间，单位为 UNIX 秒；首次观察前为 `0` |
| `pending_observation_failures_total` | 后台 pending 观察失败次数，包含超时；失败保留之前的 pending Scope 计数和快照时间戳 |
| `observed_pending_scopes` | 保留的首次观察 pending key 数，最多 1000 |
| `oldest_observed_pending_age_seconds` | 保留的 pending generation 的近似本地年龄下界，单位为秒；没有保留 key 时为 `0` |
| `last_acknowledgement_timestamp_seconds` | 最近验证的持久确认时间；尚未确认时为 `0` |
| `discovery_seconds`、`last_invocation_seconds` | 原有最近发现/调用耗时 gauge |
| `completed_total`、`failed_total`、`timeouts_total` | 原有已确认、失败、超时调用计数器 |
| `attempt_duration_seconds` | 额外 `outcome`：每次 Worker attempt 的耗时，包含重试 |
| `discovery_duration_seconds` | 额外 `outcome`：每次发现流程的耗时 |
| `errors_total` | 额外 `category`：上文日志契约中的有限错误类别 |
| `retries_total` | 额外 `reason`：`failure`、`conflict`、`blocked`；重试调度决策数，包含阻塞复查与溢出 |
| `admission_blocks_total` | 观察到的准入阻塞决策数；重复决策可再次计数 |

持久 pending 观察周期性运行，独立于 Worker 容量，默认间隔 30 秒，每次观察的截止时限为 2 秒。
即使所有 Worker 槽位占满也不会暂停这项观察。失败保留最近成功的快照，并增加 `pending_observation_failures_total`；
新的采集或抓取不能让旧快照变新。这些读取在后台执行，不在指标采集时执行。

pending 快照读取已有的 processing intents，统计该 binding 的 pending Scope 数。不为每个请求 generation
保存时间戳，也不需要新表或 schema 迁移。查询工作量随该 binding 的 intents 数增长；保留的聚合快照占用
O(Families) 内存。首次成功观察前，快照时间戳为零表示未观察，即使计数指标也为零。
重启或 Leader 切换后需要新的观察；不能把保留或缺失的测量值当作当前持久积压的事实。

`oldest_observed_pending_age_seconds` 是近似的**本地下界**，从首次本地观察最多 1000 个保留的已准入 key 起计算。
它跟踪本地观察到的 pending generation，不是请求创建时间或精确的持久积压年龄。
两次发现之间 pending 集合可能陈旧，而本地年龄仍在增长。未观察的 key 可能更老；确认或淘汰可能移除本地观察。
重启与 Leader 切换会重置这段本地历史。本地年龄为 `0` 不能证明持久积压为空，较大的值也不能单独说明停滞：
处理过程可能仍在取得已验证的确认进展。应结合 pending 证据、快照新鲜度、已验证的确认进展和启动宽限期判断。

Attempt outcome 为 `success`、`hold`、`noop`、`failure`、`cancelled`、`blocked`、`cursor_conflict`、
`head_conflict`、`leadership_lost`。Discovery outcome 为 `success`、`failure`、`cancelled`、`leadership_lost`。
耗时直方图输出 `_bucket`、`_count`、`_sum`；桶边界为 0.005、0.01、0.025、0.05、0.1、0.25、0.5、1、2.5、5、
10、30、60 和 `+Inf` 秒。`_count` 统计 attempt/发现次数，不统计唯一请求或业务成功数；一次重试增加一次 attempt。
`hold` 和 `noop` 与业务成功分开。原有 `completed_total` 统计已验证持久确认的调用，可能包含已确认的 hold/noop，
不是 attempt-success 或产出 Artifact 的计数器。直方图与确认计数器都不是计费账本。

### Dashboard 与告警规则

运维方应明确选择并记录抓取失败、快照采集年龄、探测年龄、请求快照年龄、启动宽限期和未确认时长的阈值。
这些是部署专用的告警策略，不是内置默认值。例如可选择采集新鲜度 120 秒、探测新鲜度 600 秒、存在 pending work
时超过 900 秒未确认；必须根据抓取频率、就绪检查流量、调度间隔和 Worker 预期耗时验证这些选择。
不执行探测的宿主不应直接套用 600 秒探测规则。

- 先检查 Prometheus `up`、采集新鲜度和采集失败。采集失败后，即使抓取很新，返回的仍可能是旧快照。
  缺失或为零的时间戳需要单独的未观察分支，不能与陈旧数据混为一谈
- 依赖告警应以已观察且足够新的状态为前提。分别处理 `disabled`、`unobserved`、陈旧、降级和不可用；
  可选模型故障不应自动触发数据库故障级别的告警
- 将停止处理与运行停滞分开。在采集足够新、`supervisor_present=1` 且 `supervisor_running=0` 时，
  若本地保留工作或足够新的 `unacknowledged_requests>0` 快照证明存在 pending work，应触发停止处理告警，
  不能要求此规则满足运行中 Leader 条件。若只有陈旧的最后已知 pending 证据，应报告处理已停止但积压未知并排查；
  抓取目标失联则通过 `up` 单独处理
- 对运行中的 Leader，疑似停滞告警要求足够新的采集，以及 pending work 证据：当前
  `observed_pending_scopes>0`、`used_workers>0`，或同一目标/Family 的
  `requested_snapshot_timestamp_seconds` 非零且足够新，同时 `unacknowledged_requests>0`。
  要求在所选时间窗口内持续存在 pending work 且没有已验证的确认进展，并在重启或 Leader 切换后留出启动宽限期。
  使用 `last_acknowledgement_timestamp_seconds` 和 `completed_total` 的变化判断已验证进展；进程退出或成功 attempt
  的耗时不代表确认。例如，启动宽限期后持续 900 秒存在 pending 证据且没有确认，可提示疑似停滞；
  确认仍在推进时，本地观察到的旧 key 不能说明停滞。
  确认时间为零表示从未确认，不是从 UNIX 纪元起故障；应使用宽限期与持续观察窗口，不能用当前时间减零。
  比较时间戳时应考虑时钟偏差，不能把缺失的观察当作零
- Worker 槽位占满时持久 pending 观察仍会继续，但观察失败可能使数据陈旧。陈旧快照不能压制由当前本地工作证据
  和缺少已验证进展共同支持的疑似停滞告警。持久计数是最后已知的后台观察；本地保留 key 与 Worker 槽位不是全量持久积压。
  `oldest_observed_pending_age_seconds` 仅提供辅助上下文，不能独立触发停滞告警，也不能替代确认进展和新鲜度判断
- 健康空闲实例不应告警。禁用自动处理是配置，Standby 是预期角色，纯 API 缺失 Supervisor 数据不是零容量故障。
  没有当前本地证据的陈旧 pending 快照应触发新鲜度诊断，不能据此编造积压结论。结合阻塞等待、准入阻塞、重试原因、错误与 Worker 利用率排查
- 分别抓取 API 与后台目标。同一部署/Family 的 API 与后台积压快照、Leader 与 Standby 计数不能相加。
  积压与容量面板选取当前运行的 Leader；跨 Family 汇总仍保留部署/group 边界。计数器速率属于各进程，
  角色切换时可能需要汇总，但不能将同一目标重复抓取后计入同一总量
- 监控 pending 观察失败、采集失败与日志丢弃计数的增长。此处不支持 OTLP SDK 队列占用和 exporter 丢弃计数；缺失表示不可用的遥测，
  不是零压力或零丢失。排查 Trace 交付时应查看 Collector/backend 自身诊断

端点配置参见[配置选项](configuration.md)，恢复步骤参见[诊断与恢复](troubleshoot.md)。

## Provider 调用观测

启用 Server metrics 后，Runtime 自有推理适配器分别观察 Provider HTTP exchange 与最终模型操作。
Readiness 探针排除。Runtime 的可选 observer 不配置 SDK 日志或 tracing；自定义推理绑定和客户端保持原行为，
由嵌入应用接入自己的观测。

启用推理隐私检查时，HTTP 观测位于隐私检查内部的实际发送边界。发送前拦截不产生 HTTP exchange；
响应在释放前被拦截时，保留实际 HTTP 状态码，并将最终模型操作记录为失败。

| 指标（前缀 `powercontext_server_provider_`） | 含义 |
| --- | --- |
| `http_attempts_total{provider,capability,execution,outcome,retry}` | 一次到达响应头或在头部前失败的 HTTP exchange；`execution` 为 `parent` 或已收到报告的 `worker` |
| `http_responses_total{provider,capability,execution,status_code}` | 实际收到的响应头，包含重定向；头部前超时、断连和取消不编造状态码 |
| `http_header_duration_seconds{provider,capability,execution,outcome}` | 到响应头或头部前失败的秒数，不包含读取响应体 |
| `operations_total`、`operation_duration_seconds` | 最终 generation/embedding 适配器结果及完整耗时，包含重试、读取响应体和输出校验 |
| `business_errors_total{provider,capability,execution}` | 显式业务拒绝，目前识别 MiniMax 原生 embedding；数字业务码在日志/trace 中保留，不扩展为任意 metric label |
| `http_observation_supported{provider,capability,execution}` | 组合时可观察自有 HTTPX transport 为 `1`，不可用为 `0`；两者均不表示 Provider 是否健康 |
| `worker_reports_total{outcome}`、`worker_report_timestamp_seconds` | received/missing 报告数量和最近接收报告的 Unix 秒时间；时间为零表示尚未观察 |
| `observations_dropped_total{execution}` | 有界 collector 省略的观测数量；大于零表示数据不完整 |

`provider` 使用 SDK 声明的系统名，归入有限词表或 `other`，不根据 URL 或任意模型名猜测供应商。
`capability` 区分生成和嵌入协议；通过生成协议执行的 rerank/decision 仍属于 generation。
Scope、request、worker、模型名和端点地址不进入 metric label。

SDK 标记的 `429 → 429 → 200` 应产生三次 HTTP 尝试、两次 `retry` 尝试和一次最终模型成功。
重试标记只来自 SDK 的显式 `x-stainless-retry-count`；缺少标记时为 `unknown`，不把分批、模型输出修正或重定向
推测为 SDK 重试。重定向的每个 exchange 保留 SDK 标记。现有超时、重试、代理、重定向、TLS 和连接池设置保持不变。

HTTP 200 后响应体或输出校验失败，保留该 HTTP 200，并按适配器既有异常映射记录最终模型失败或超时。
MiniMax 以 HTTP 200 返回非零 `base_resp.status_code` 时，还增加业务拒绝计数。这些观测不包含 Provider 消息、
内容、凭据或 URL。事件 `provider.http.completed`、`provider.operation.completed`、`provider.business.completed`
使用 unit `dependency`、同一套有限 outcome/error 词表、毫秒耗时和已绑定的日志/trace 身份。
录制中的 span 增加对应的无内容事件；存在关联 ID 不代表 trace 已采样或存储。

原生 worker 每次执行最多保留 128 个聚合键，完成或失败时返回固定直方图桶。父进程每份报告只归并一次，重复抓取
不会重复计数。后台指标的时间和速率表示父进程收到观测的时间，不是远程调用发生时间。运行中的 worker 尚未报告；
crash 或强制终止可产生 `missing`，不能补成健康零值。collector 丢弃单独记录。报告不证明 durable acknowledgement
或 Artifact 产出；自定义 launcher 必须提供自己的报告。累计计数和直方图属于当前父进程生命周期，重启时重置。

Providers 看板顶部显示所选窗口调用次数、当前进程累计值、成功/失败/取消比例、P50/P95 和 Worker 报告覆盖。
趋势图分别显示成功与失败、父进程与后台 Worker、耗时以及 HTTP 状态码。窗口计数使用所选范围的 Counter 增量；
进程累计值不受时间选择器控制，并在服务重启后归零。无完成样本或样本不足时耗时保持未知。
Worker 报告覆盖使用同一窗口的 `received / (received + missing)`，不含运行中尚未回报的工作，不代表 Provider
成功率。Counter 增量按抓取间隔外推，窗口数量可能有小数。首次抓取前的批次不可还原。

点击趋势点会打开该点前后 5 分钟的调用日志。日志按指标点所属实例、角色、Provider 和能力筛选，显示逐次耗时、
HTTP 状态或最终模型结果；展开记录可打开调用链。日志筛选要求 `service_target` 与 Prometheus `instance` 一致。
缺失日志或 Trace 保持证据不足，不能仅凭 ID 判断为未采样、未导出或已过期。

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
| `readiness_state{state}` | `ready`、`degraded`、`not_ready` 的 one-hot 状态；与 `/health/ready` 使用同一份合并后的 aggregate，包含 access/authentication provider 的失败。兼容指标 `runtime_ready` 仍只表示可接收操作 |
| `dependency_state{dependency,state}` | 与上文详细 Runtime 观察合并的有界 one-hot 状态。总体检查还包括 runtime、supervisor、access 与 authentication provider；`access_mode` 等配置回显不出现。详细观察中的 disabled/unobserved 依赖保留明确状态 |
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

Processing 看板顶部显示正在执行、排队等待、并发上限、结果待确认、失败/超时率和 P50/P95。结果待确认表示有多少个
Scope 的后台处理结果尚未确认写入成功；长时间大于零可能表示任务卡在最后确认阶段。排队等待包含已进入
本机队列、等待派发或等待重试的 Scope 任务；并发上限表示配置允许同时执行的后台任务总数，不是线程数。趋势只保留
本机队列、待确认、执行结果、耗时和并发占用；只有存在待处理或待确认数据的 family 才进入按处理类型汇总。
策略阻塞不是失败，待确认也不证明产物已经生成。

“策略阻塞”在页面中显示为“被规则阻止”。它不是 Worker 崩溃，也不是一次新的失败；它表示同一批 Source
已经达到允许的重试或资源预算，系统停止继续调用模型。常见 `error_code` 如下：

- `window_attempt_limit`：同一 Cursor 后的这批 Source 已尝试 3 次，仍未完成。请求数和 Token 预算不一定用完。
- `window_provider_budget_exceeded`：同一批 Source 已达到请求数或 Token 预算。
- `source_complexity_limit`：该窗口的 Source 结构超过处理上限。

排查时先在 Processing 页的“被规则阻止”面板读取 `scope`、`source_after`、`source_through`、`attempts`、
`requests` 和 `tokens`，再展开记录查看 `last_error_code`、`last_error_stage`、`last_error_worker_id` 和
`last_error_trace_id`。用同一 Scope 筛选全部处理日志，查看 `artifact_processing.failed` 或
`artifact_processing.worker.failed`，即可定位上次模型、输入或执行错误。若没有 trace，保持证据不足。

修复后，后端会在约 5 分钟的 metadata recheck、下一轮自动准入或新的显式 flush 时重新检查；如果额度和
输入条件已改变，可直接恢复。刷新页面、重复 flush 和重启都不会自动重置额度，也不提供 retry/reset API。
持久输入问题需要按 Topic Memory RFC 的运维边界处理，不能用跳过证据或伪造成功代替。

按处理类型行可打开该实例和 family 的日志。日志显示入队、派发、Worker 返回、确认和完成事件，以及可用的事件耗时、
排队耗时、worker 标识和 trace 标识；展开记录可打开调用链。链接和日志仍要求 `service_target` 与 Prometheus
`instance` 一致。缺失日志或 Trace 保持证据不足，不能仅凭 ID 推断未采样、导出失败或已过期。

Worker 跨子进程保留 `access_denied`、`access_identity_required`、`access_unavailable` 三类安全错误代码。
权限拒绝归类为 `authorization`，缺少身份为 `authentication`，授权服务不可用为 `dependency`。
代码不包含 Principal、Binding、异常正文或凭据；未分类错误仍为 `processing_failed`。
统计观察的 `last_error_worker_id` 保留最近失败的既有 worker 身份，`last_error_trace_id` 在 tracing adapter 提供时保留同次 trace 身份。它们随当前重试周期保留，进入规则阻止、解除阻止后再次排队或执行都不会替换为新 worker；确认完成、重启或缓存淘汰时清除。准入阶段尚未启动 worker 时为空。身份不进入 metric label；有 trace ID 也不证明已采样或保存。
统计接口的每 Scope 重试诊断属于本地观察，不是持久化审计历史；参见[Scope 与业务看板](reference-observability-stack.md#scope-与业务看板)。

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
显式 Dream 派发——包括 Topic Memory 的 Dream 分支——复用同一个 child span，并共享同一套 model instrumentation，
因此两者的 instrumented model span 都会出现。
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

### 检索最终结果观测

`retrieval.completed` 记录经过过滤与组装后的应用结果，复用当前请求、Trace 和 Scope，不包含查询、结果正文或任意提供方错误。`retrieval_family` 取内置 family、`context` 或 `other`；`retrieval_outcome` 为 `nonempty`、`empty`、`failure` 或 `cancelled`。普通 `outcome` 在非空与空结果时都为 `success`。`result_count` 是最终返回条目数；上下文准备和失败不提供该计数。JSON 与控制台使用同一字段白名单。

`powercontext_server_retrieval_requests_total` 与 `powercontext_server_retrieval_duration_seconds` 仅使用有界 operation/family/outcome 标签。直方图单位为秒，日志耗时单位为毫秒。MCP 内部 HTTP bridge 不重复统计应用派发。入口认证及校验拒绝仍由 transport 指标表示。非空不代表相关或被采用；取消单列且继续向上传播。指标随进程重启归零，日志为尽力记录。

### Artifact 直接读取观测

`artifact.read.completed` 记录已派发的 `get_artifact` 和 `get_artifact_revision` 操作，计时包含条件读取判断与响应组装。`family` 是受支持的 Artifact 读取类型或 `other`。字符串字段 `read_outcome` 的值为：`success`（返回内容）、`not_modified`（HTTP 304）、`failure`、`cancelled`。304 的普通 `outcome` 仍为 `success`。失败保留映射后的 `error_code`、`status_code` 和有界 `error_category`。JSON 与控制台使用相同白名单；参考 Collector 保留 `read_outcome` 结构化字段，不将其提升为索引标签。

`powercontext_server_artifact_read_duration_seconds` 是以秒为单位的直方图，仅使用有界 `operation`、`family`、`outcome` 标签；其 `_count` 序列提供各读取结果的完成计数。日志 `duration_ms` 的单位是毫秒。计时覆盖应用派发，不含宿主网络时间。HTTP/MCP 对每次应用派发只统计一次，条件读取也遵循该规则。入口认证和校验拒绝仍由 transport 记录。请求、Trace 与 Scope 复用已绑定标识，仅保留在日志和 Trace 中。不记录内容、Artifact ID、ETag、查询或凭据。

精确读取不进入检索分母，也不证明语义相关或宿主采用。`prepare_context` 内部的 Profile 读取尚未独立计时。取消继续向上传播；观测失败不改变读取结果；指标随进程重启归零，日志为尽力记录。缺少序列表示未知，不表示零次读取。

## Topic 生成拒绝码

Topic 处理可通过 `invalid_topic_placeholder` 标识可识别的编号占位正文，
或通过 `invalid_topic_coordination_content` 标识纯身份协调正文。
这些不含内容的诊断码表示可恢复的生成拒绝，不表示成功 NOOP。
重试消耗现有 Window 持久化预算；耗尽后保留待处理前沿。诊断码由现有 Worker 事件及关联字段承载。
不得将 Source 文本、生成正文或服务商响应写入运维日志。
