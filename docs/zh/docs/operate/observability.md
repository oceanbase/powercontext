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

默认通过 `/metrics` 输出 Prometheus 格式指标。在 `enforced` 模式下，`/metrics` 与 `capabilities` 需要
具备 `server.observe` 权限的 Principal 认证，参见[诊断与恢复](troubleshoot.md)。API 宿主的
`/health/live` 与 `/health/ready` 保持公开。

`background` 角色使用 `POWERCONTEXT_SERVER_HTTP_HOST` 和 `POWERCONTEXT_SERVER_HTTP_PORT` 启动仅提供指标的
HTTP listener，指标认证与授权规则和 API 宿主一致，不提供 API、MCP、Dashboard、OpenAPI 或健康检查路由。
API 与后台进程同机部署时应使用不同端口。设置 `POWERCONTEXT_SERVER_METRICS_ENABLED=false` 会移除 `/metrics`；
对 `background` 还会阻止启动 listener。抓取后台指标不会执行 API 宿主的模型就绪探测。

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
| `dependency_state` | `dependency`、`state`：`unobserved`、`disabled`、`ready`、`unavailable`、`timeout`、`misconfigured` |
| `dependency_blocking` | `dependency`：该依赖阻止就绪时为 `1` |
| `dependency_observed_timestamp_seconds` | `dependency`：最近一次实际完成探测的时间；首次完成前不存在 |
| `dependency_last_success_timestamp_seconds` | `dependency`：最近一次实际成功探测的时间；首次成功前不存在 |
| `dependency_probe_duration_seconds` | `dependency`、`outcome`：实际探测耗时，不包含缓存命中 |
| `telemetry_collection_timestamp_seconds` | 最近成功复制被动快照的时间；首次采集前为 `0` |
| `telemetry_collection_failures_total` | 被动采集失败次数；失败保留先前观察 |
| `telemetry_log_records_dropped_total` | 进程自有日志 handler 未能交付的记录数 |

依赖标签限于 `database`、`inference.generation`、`inference.rerank`、`inference.decision` 和
`inference.embedding`。禁用依赖没有探测时间戳或样本。探测结果为 `ready`、`unavailable`、`timeout`、
`misconfigured` 或 `cancelled`；取消只增加耗时观察，不替换最近完成的状态与时间戳。
缓存命中既不更新实际探测时间戳，也不增加直方图计数。每个样本代表一次完整的探测刷新；提供方内部重试
（若有）包含在这次耗时中，不计为独立网络尝试。内置缓存通常保留结果 300 秒，`timeout`/`unavailable`
保留 30 秒；仅缓存到期不会自动执行探测。总体评估时间、实际探测时间、快照采集时间和 Prometheus 抓取时间
分别回答不同的新鲜度问题。

`runtime_ready=1` 可以与 `degraded` 并存，只表示可以接收操作。结合 `runtime_readiness_state`、依赖状态及
新鲜度判断，也可通过 `powercontext ready` 和 `powercontext capabilities` 区分数据库不可用与可选模型故障。
纯后台进程可能正常保持 `unobserved`；`/metrics` 不会使它执行就绪检查。

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
| `oldest_pending_timestamp_seconds` | 最老未确认请求 generation 的数据库 UTC 创建时间，单位为 UNIX 秒；没有 pending work 或任一 pending Scope 的最老年龄未知时省略 |
| `oldest_pending_age_seconds` | 快照时刻的精确持久最老年龄：数据库观察时间减去最老请求时间，单位为秒；被动读取不会使其增长，空闲、未观察或任一 pending 最老年龄未知时省略 |
| `pending_age_unknown_scopes` | 最老未确认 generation 缺少持久时间戳的 pending Scope 数，用于区分旧数据年龄未知与空积压 |
| `pending_observation_failures_total` | 后台 pending 观察失败次数，包含超时；失败保留之前的计数、最老时间戳与年龄、未知计数和快照时间戳 |
| `observed_pending_scopes` | 保留的首次观察 pending key 数，最多 1000 |
| `oldest_observed_pending_age_seconds` | 最早保留的 pending generation 从首次观察起的年龄；没有保留 key 时为 `0` |
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

启动时创建新增的 `pc_artifact_processing_requests` 表，为每个未确认请求 generation 保存一个数据库 UTC 时间戳
（SQLite 精度为毫秒，MySQL 精度为微秒）。启动新版本前应停止旧版本的写入进程；混合版本写入会继续产生缺少时间戳的请求。
请求创建、确认时的清理与对应的 intent 状态转换原子提交。确认会删除截至已确认 generation 的时间戳，保留更新的请求。
重启与 Leader 切换不会丢失持久时间戳；下一次成功观察会恢复快照。
已有的、缺少时间戳的 pending generation 不会被赋予虚构的迁移时间作为创建日期。即使新增了带时间戳的请求，
这些旧 generation 在得到确认前，最老年龄仍然未知。只要 `pending_age_unknown_scopes>0`，就省略整个 Family 的
`oldest_pending_timestamp_seconds` 和 `oldest_pending_age_seconds`，不会将已知的最老请求伪装成精确结果。首次观察前，快照时间戳为零表示未观察，
即使计数指标也为零。

计数查询检查该 binding 的 intents，并为每个 pending Scope 通过主键探测时间戳；工作量随 binding intents 数量增长，
不遍历所有保留的请求 generation。最老时间戳查询使用 binding/time 索引和 `LIMIT 1`。
保留的聚合快照占用 O(Families) 内存。持久时间戳存储占用 O(未确认请求 generation 数)，通过确认清理；
不使用不安全的硬上限或有损合并。因此，持续收到请求但停滞的 Scope 在确认前可能不断增加存储，尽管 pending 计数只计为一个 Scope。

`oldest_observed_pending_age_seconds` 仍是独立的**本地下界**，从首次本地观察最多 1000 个保留的已准入 key 起计算。
它跟踪本地观察到的 pending generation，不是持久请求创建时间。两次发现之间 pending 集合可能陈旧，而本地年龄仍在增长。
未观察的 key 可能更老；确认、淘汰、Leader 切换或重启可能移除或重置本地观察。
本地年龄为 `0` 不能证明持久积压为空；应结合持久计数、时间戳、未知计数与新鲜度判断。

Attempt outcome 为 `success`、`hold`、`noop`、`failure`、`cancelled`、`blocked`、`cursor_conflict`、
`head_conflict`、`leadership_lost`。Discovery outcome 为 `success`、`failure`、`cancelled`、`leadership_lost`。
耗时直方图输出 `_bucket`、`_count`、`_sum`；桶边界为 0.005、0.01、0.025、0.05、0.1、0.25、0.5、1、2.5、5、
10、30、60 和 `+Inf` 秒。`_count` 统计 attempt/发现次数，不统计唯一请求或业务成功数；一次重试增加一次 attempt。
`hold` 和 `noop` 与业务成功分开。原有 `completed_total` 统计已验证持久确认的调用，可能包含已确认的 hold/noop，
不是 attempt-success 或产出 Artifact 的计数器。直方图与确认计数器都不是计费账本。

### Dashboard 与告警规则

运维方应明确选择并记录抓取失败、快照采集年龄、探测年龄、请求快照年龄、pending age 和未确认时长的阈值。
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
- 对运行中的 Leader，停滞告警要求足够新的采集，以及 pending work 证据：当前 `observed_pending_scopes>0`、
  `used_workers>0`，或足够新的 `unacknowledged_requests>0` 快照。再将未确认时长或 pending-age 下界与所选阈值比较。
  Worker 槽位占满时持久 pending 观察仍会继续，但观察失败可能使数据陈旧。陈旧快照不能压制由当前本地工作证据
  支持的停滞告警。持久计数是最后已知的后台观察；本地保留 key 与 Worker 槽位不是全量持久积压。
  确认时间为零表示从未确认，不是从 UNIX 纪元起故障；应单独结合启动宽限期与持续 pending work 判断
- `oldest_pending_age_seconds` 表示成功数据库观察时刻的年龄，不是抓取时刻的年龄。需要基于快照推算时，
  仅当同一目标/Family 的 `unacknowledged_requests>0`、`pending_age_unknown_scopes=0`，且
  `requested_snapshot_timestamp_seconds` 非零且足够新时，使用
  `time() - powercontext_server_artifact_processing_oldest_pending_timestamp_seconds` 计算持久最老年龄，
  同时要求采集足够新。分别处理旧数据年龄未知、未观察和陈旧快照；缺少最老时间戳不等于年龄为零。
  选择阈值时需考虑 Prometheus 与数据库之间的时钟偏差
- 健康空闲实例不应告警。禁用自动处理是配置，Standby 是预期角色，纯 API 缺失 Supervisor 数据不是零容量故障。
  没有当前本地证据的陈旧 pending 快照应触发新鲜度诊断，不能据此编造积压结论。结合阻塞等待、准入阻塞、重试原因、错误与 Worker 利用率排查
- 分别抓取 API 与后台目标。同一部署/Family 的 API 与后台积压快照、Leader 与 Standby 计数不能相加。
  积压与容量面板选取当前运行的 Leader；跨 Family 汇总仍保留部署/group 边界。计数器速率属于各进程，
  角色切换时可能需要汇总，但不能将同一目标重复抓取后计入同一总量
- 监控 pending 观察失败、采集失败与日志丢弃计数的增长。此处不支持 OTLP SDK 队列占用和 exporter 丢弃计数；缺失表示不可用的遥测，
  不是零压力或零丢失。排查 Trace 交付时应查看 Collector/backend 自身诊断

端点配置参见[配置选项](configuration.md)，恢复步骤参见[诊断与恢复](troubleshoot.md)。

## Tracing

安装 `tracing-otlp` extra，启用 `POWERCONTEXT_SERVER_TRACING_ENABLED=true`，并配置 OTLP HTTP 接收端。
完整步骤见 [Phoenix](trace-with-phoenix.md) 或 [Langfuse](trace-with-langfuse.md)。
缺少 extra 时启用 Tracing 会导致启动失败。PowerContext 跟踪自身的传输、应用和模型调用，
不会自动采集宿主中无关的模型调用。

使用环境文件部署时，将 Tracing 与 OpenTelemetry 变量写入传给 Server 的显式文件。
优先级规则见[环境配置](../get-started/configure-server-environment.md)。
