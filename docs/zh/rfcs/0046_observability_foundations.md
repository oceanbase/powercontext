- Proposal Name: `observability_foundations`
- Start Date: 2026-07-29
- RFC PR: [oceanbase/powercontext#46](https://github.com/oceanbase/powercontext/pull/46)
- Tracking Issue: [oceanbase/powercontext#39](https://github.com/oceanbase/powercontext/issues/39)
- Related RFCs: [RFC 0016](0016_pydantic_ai_inference_integration.md)、
  [RFC 0019](0019_local_source_memory_runtime.md)、
  [RFC 0020](0020_runtime_backed_memory_remote_access.md)

# Summary

本 RFC 定义 PowerContext Server 与内置 Runtime 的可观测性边界。PowerContext 将提供：

- operational logging；
- Prometheus-compatible metrics；
- OpenTelemetry tracing 和 context propagation。

这些信号使用统一、稳定的 operation vocabulary 和 correlation model。`X-PowerContext-Request-ID` 继续作为始终可用、
由 Server 持有的支持标识，其值来自 inbound Server span ID。Trace recording 和 export 是可选能力；即使
span 不采样，request context 仍然存在。

本提案界定行为与职责，不规定详细实现。现有 HTTP、MCP、Client 和后台处理边界需要先完成内部设计和代码
校准。只有这些边界验证完成后，才开始实现各类信号。

# Motivation

PowerContext 已经提供 liveness、readiness 和 request ID，也记录了部分失败。这些能力还不足以形成完整的
运行视图。

Operator 需要了解：

- Server 与 Runtime 是否可用；
- 某个被报告的 request 对应哪项 operation failure；
- operation rate、latency、concurrency 和 failure trend；
- 后台 Source processing 是否持续推进；
- 工作如何经过 HTTP、MCP、Runtime 和 remote dependency。

设计还需要防止意外暴露数据和产生误导性 measurement。MCP 通过内部 HTTP call 投影 PowerContext
operation。Telemetry 必须关联外部 MCP request 与 application operation，但不能把内部 bridge 描述成另一条
外部 request。

如果没有一致边界，logging、metrics 和 tracing 可能使用不同名称、统计不同单位，或暴露无界和敏感值。

# Guide-level explanation

## 三类信号

PowerContext 将 logging、metrics 和 tracing 视为互补能力：

| Signal | 用途 |
| --- | --- |
| Logs | 诊断单次 failure 和 lifecycle change |
| Metrics | 观察 aggregate health、traffic、latency 和后台进度 |
| Traces | 跨 transport、application 和 dependency boundary 跟踪一次执行流程 |

Logging 和 metrics 属于 ready-to-run Server。OpenTelemetry request context 始终存在，trace recording 和
export 则是可选能力。

## Correlation

PowerContext 使用三种 identifier：

- `request_id` 标识一项用于支持和诊断的 request；
- `trace_id` 标识一个 execution flow；
- `span_id` 标识该流程中的一项 operation。

Server 从 inbound transport span ID 派生 `request_id`。Caller 通过标准 OpenTelemetry trace context 传播
上下文，而不是传播 request ID。关闭 trace export 时，由 non-recording OpenTelemetry context 提供相同标识。
对于 MCP，logical protocol request span 持有 request ID，internal HTTP bridge 复用该值。

Logs 可以包含三种 identifier。Metrics 不会把它们作为 label。

## 可观测工作单元

PowerContext 区分以下工作单元：

| Unit | 含义 |
| --- | --- |
| Transport request | 一次外部 HTTP 或 MCP protocol request |
| Application operation | 一项稳定的 PowerContext operation，例如 `search_memory` |
| Background activation | 一次手动或定时 Source processing activation |
| Dependency call | 一次对其他 service 或 provider 的 outbound call |

直接 HTTP call 会产生一次 external request 和一次 application operation。MCP tool call 同样产生一次
external request 和一次 application operation。其内部 HTTP bridge 不计为第二次 external request。

Application operation identity 与 transport 无关。相同 PowerContext behavior 通过 HTTP 和 MCP 使用相同
operation name。

## Data safety

正常 telemetry 禁止包含：

- Source 或 Memory content；
- search query；
- prompt、model response 或 vector；
- request 或 response body；
- credential、authorization header 或完整 database URL。

Metrics 还会排除 request ID、trace ID、`scope_id`、Source ID、Memory ID 和 raw path 等无界 identity。

未预期 failure 可以在 Server log 中包含 traceback。Traceback 周围的 structured field 仍遵守相同的数据策略。

## 诊断流程

Operator 可以：

1. 使用 liveness 和 readiness 判断 process 是否应该接收 traffic；
2. 使用 Client error 中的 request ID 查找关联 log；
3. 使用 metrics 判断问题是单点还是普遍现象；
4. 启用 tracing 后，通过 trace 跟踪 request 和 dependency call。

正常 local operation 不要求 PowerContext 连接外部 telemetry backend。

# Reference-level explanation

## Ownership

Ready-to-run Server 拥有 observability configuration 和 lifecycle。将 PowerContext 作为 library 导入时，不会
配置 global logging 或启动 exporter。

Server 可以观察 transport 和 application behavior，但 observability 不负责 domain decision、persistence、
cursor movement、error mapping 或 retry policy。

内置 Runtime 暴露后台处理的 lifecycle 和 result，但不依赖 logging、Prometheus 或 OpenTelemetry
implementation。Embedded Runtime user 可以在没有 Server observability 的情况下运行。

## 统一 operation vocabulary

Remote application operation 使用 HTTP contract 定义的稳定 operation ID。MCP 为 projected tool 使用相同
identity。

Health probe 和 MCP protocol traffic 等 infrastructure behavior 使用一个独立的小规模 vocabulary。Raw path、
Python function name 和 implementation class name 不是稳定 operation identity。

Signal-specific implementation 可以增加 attribute，但 logging、metrics 和 tracing 必须对 operation 及其
outcome 的含义保持一致。

## Logging boundary

Server logging 覆盖：

- startup、readiness change 和 shutdown；
- request 和 application operation failure；
- 有诊断价值的 operation completion event；
- 后台处理 outcome；
- 需要 operator 处理的 observability configuration 或 export failure。

Server 支持 human-readable 和 structured output，并允许配置 log level。Routine health 和 metrics traffic
不应占据正常日志。

Operational log 默认写入 standard stream。首批实现不提供 file sink、log rotation、retention 或 shipping。
这些能力可以由 service manager、container runtime 或 log collector 提供。

Logging 用于诊断，不是 audit trail。它不承诺 durable storage、delivery、跨 process ordering 或 retention。

## Metrics boundary

首批 metrics 使用 Prometheus-compatible surface，覆盖：

- external request count、latency、failure 和 concurrency；
- application operation count、latency 和 outcome；
- Runtime readiness；
- 后台 Source processing count、latency、outcome 和 progress。

Metrics 使用来自 declared operation 和小规模 outcome vocabulary 的有界 label，不使用 caller-controlled 或
content-derived label。

Metrics endpoint 属于 infrastructure，不进入 domain OpenAPI contract 或 MCP tool surface。

首个提案不定义 custom application metric、dashboard、alert 或 service-level objective。

## OpenTelemetry boundary

OpenTelemetry 提供 tracing 和 context propagation。首批 integration 覆盖：

- incoming HTTP 和 MCP request；
- PowerContext application operation；
- scheduled background work；
- outbound PowerContext Client call；
- internal MCP bridge。

PowerContext 使用 W3C Trace Context，并支持 OTLP export。Vendor-specific tracing configuration 不属于首批
设计。

Trace recording 和 OTLP export 是可选能力。关闭时，Server 使用 non-recording OpenTelemetry context，
request ID、propagation、logging、metrics 和 domain behavior 无需 telemetry backend 也能继续工作。
因此 OpenTelemetry API 和 SDK 属于 Server role，OTLP exporter 则通过 `tracing-otlp` extra 安装。

首批实现不包括 logs 和 metrics 的 OTLP export。未来增加该能力时，不应改变本 RFC 定义的 signal semantic。

## 相邻能力

Audit record、Source 或 Memory data collection、inference input/output monitoring 和 usage analytics 是独立的
产品能力，不属于本 RFC 定义的 observability signal。

现有 `powercontext doctor` 是 installation diagnostics 的起点。后续可以扩展 diagnostic bundle，但这不要求
operational log 成为 durable record。

## HTTP、MCP 与后台处理一致性

HTTP 和 MCP 是同一 application behavior 的不同 entrypoint。它们的 transport telemetry 可以不同，但
application operation telemetry 必须一致。

MCP implementation 包含 internal HTTP bridge。该 bridge 可以在有助于解释 trace 时保持可见，但它不是
external traffic，不能扩大 external request metric，也不能产生误导性的重复 access record。

Scheduled processing 没有 incoming request ID。它使用相同的 application outcome vocabulary，并在启用
tracing 时启动自己的 trace。

## Failure isolation

Observability 不属于 authoritative operation result：

- log formatting failure 不能改变 response；
- metrics collection failure 不能改变 Runtime state；
- unavailable exporter 不能使 Server 变为 not ready；
- cancellation 继续传播；
- shutdown 可以有限等待 telemetry flush，但不能阻止 Runtime cleanup。

No-op Source processing 是成功结果，不是 failure。

## Compatibility

本 RFC 不修改 Source、Artifact、Trigger、Memory、inference、persistence 或 cursor semantic。

`X-PowerContext-Request-ID` response header 和 Client error field 保持兼容。本 RFC 在发布前细化 RFC 0020：
request ID 是 Server-owned span identifier。Metrics endpoint 和新增 configuration 是 additive infrastructure
surface，并且不进入 domain OpenAPI contract。

Documented event name、metric name、label 和 tracing attribute 发布后会成为 operational compatibility
surface。增加 field 通常是兼容变更。重命名或移除这些值需要评审，因为它可能破坏 query、dashboard 和
alert。

首批 internal observability hook 不是 public extension API。

## Acceptance criteria

- Logging、metrics 和 tracing 使用相同的 application operation identity 和 outcome semantic。
- 对相同 behavior 的 direct HTTP 和 MCP call 只产生一次 application operation。
- Internal MCP bridge 可被关联，但不计为 external traffic。
- 关闭 trace recording 和 export 时 request ID 仍然可用。
- Metrics 具有 bounded cardinality。
- Telemetry 排除本 RFC 定义的禁止数据类别。
- Background success、no-op、failure 和 cancellation 可以区分。
- Observability failure 不改变 domain behavior 或 readiness。
- 启用 OpenTelemetry 后，context propagation 覆盖受支持的 inbound 和 outbound boundary。
- 默认 test suite 不要求外部 telemetry service。
- 英文和中文 user documentation 保持同步。

# Drawbacks

Observability 会增加 dependency、runtime overhead、configuration 和新的 compatibility surface。

区分 transport request 和 application operation 比单一 access log 引入更多概念。为了正确表达 MCP，这种
区分是必要的。

首批 metrics 使用 Prometheus，tracing 使用 OpenTelemetry，因此形成两类 signal integration。这样可以避免
首个 metrics contract 与 OpenTelemetry metrics exporter 耦合，但需要在两者之间保持命名一致。

# Rationale and alternatives

## 直接对现有代码插桩

这种方案能更快提供可见信号，但可能把当前 internal HTTP 和 middleware behavior 固化为公开 telemetry
model。本提案先对齐 semantic boundary。

## 立即使用 OpenTelemetry 处理所有信号

单一 SDK 最终可能简化 export，但会在 PowerContext 验证 signal contract 前，让首批 logging 和 metrics 与
OpenTelemetry SDK choice 耦合。本提案从 Python logging、Prometheus metrics 和 OpenTelemetry tracing
开始。

## 只提供 logs 和 metrics

这种方案覆盖 local diagnostic 和 aggregate behavior，但不能跨 MCP、Client 和 remote Server boundary 传播
execution context。Tracing 保持可选，但属于共同设计。

## 使用 raw path 和 scope identifier

这些值容易采集，但不稳定、可能敏感且通常无界。本提案使用 declared operation identity 和 bounded outcome。

# Prior art

BentoML 分别使用 Python logging、Prometheus request metrics 和 OpenTelemetry tracing。它将 log 与 trace
context 关联，记录 request rate 和 latency，并在 Server 与 Client boundary 之间传播 context。BentoML
还会将 model monitoring data 写入 rotating file。该能力记录 inference data，与 stream-oriented
operational logging 分离。

PowerContext 采用这种信号分离方式和同样的 Server-span-derived request ID model，使用 operation ID 代替
raw path，也不引入 BentoML 的 multiprocess metrics、model data collection 或 usage analytics。

RFC 0016 定义 inference telemetry 的 privacy rule。RFC 0019 定义后台 Runtime processing。RFC 0020 定义
request ID、operation ID、HTTP error behavior、MCP projection 和 Server lifecycle。本提案在发布前细化
request ID ownership，并使 observability 与这些 contract 对齐。

# Unresolved questions

- Prometheus metrics 是否应该对每个 ready-to-run Server profile 默认启用？
- 首次 public preview 需要哪些 application 和 background measurement？
- 首个 tracing release 是否应该包含显式 inference span？
- 哪些 telemetry name 应该从首个 release 开始视为稳定值？

这些问题影响功能范围，必须在对应实现开始前解决。不改变本 RFC 边界的 internal mechanic 可以在实现评审中
决定。

# Future possibilities

后续工作可以增加：

- OTLP metrics 和 logs；
- 当 PowerContext 拥有 background service profile 时，提供包含 rotation 和 retention 的 managed file sink；
- 基于 `powercontext doctor` 生成经过脱敏的 diagnostic bundle；
- dashboard、alert 和 service-level objective；
- inference usage 和 dependency metric；
- database 和 provider span；
- Prometheus metric 中的 trace exemplar；
- 受支持的 custom instrumentation API；
- OpenTelemetry Collector deployment example。

这些新增能力必须保持本 RFC 定义的共享 operation vocabulary、correlation model、data policy 和 failure
isolation。
