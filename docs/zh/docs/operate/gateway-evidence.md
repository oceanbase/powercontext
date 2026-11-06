---
title: Gateway 调用证据
description: 读取 Rust Gateway 有界、无正文的调用、尝试、usage 与 HTTP 框架观测。
---

# Gateway 调用证据

Rust Gateway 默认向 **stdout** 输出 `powercontext.gateway-evidence.v1` JSON 行，运行日志使用 **stderr**。
无需额外部署观测平台。沿用现有日志采集器的轮转与保留策略；仅将 stdout 重定向到文件不会限制文件大小。

```bash
# 将证据与运行日志分开；采集器应配置有界保留。
./gateway/target/release/pc-gateway | your-existing-log-collector

# 显式关闭证据。启动诊断会说明证据覆盖缺失。
PC_GATEWAY_EVIDENCE=off ./gateway/target/release/pc-gateway
```

生产应用装配 `JsonEvidenceSink`，不再使用测试/演示保留的无界 `BufferedEvidenceSink`。
最多保留 **1024 条排队记录和 1 条写出线程持有的记录**。接收的记录仅含有界标识符、数值和枚举，
序列化后每条含换行不超过 4096 字节。提交使用 `try_send`，队列满时丢弃新记录。
请求侧不执行 I/O，也不等待写出线程退出或队列排空。stdout 卡住最多占用一个写出线程，
不会持续增加线程或队列条目。Gateway 的请求/响应缓冲与并发仍有各自的资源限制。

每行包含 `schema`、`operation=gateway.model_call`、`unit`（`call`、`attempt`、`delivery`）、
`event`（同样的三个值）、`evidence` 和 `loss`。进程内累计的 `loss.dropped`、`loss.write_failed`、
`loss.written` 分别统计被丢弃的提交、失败或无法确认的记录写出、完成写入并 flush 的记录，
采样发生在当前行写出之前。恢复后的输出会携带此前的丢失计数；嵌入宿主也能在拥塞时读取
`JsonEvidenceSink.stats()`。执行身份不作为指标标签。
这些记录是尽力输出：溢出可能丢掉终态或尝试记录，失败/部分写入可能损坏一行，进程退出可能丢掉队列内容。
读取器应跳过畸形行和空行；写出失败后，exporter 会先补换行，再写下一条记录。计数随进程重启清零。
没有重放、计费或审计持久性保证，也不建立财务对账、token 节省或任务成功结论。
写出线程无法启动时，应用输出固定诊断并使用 Noop，不改变业务结果。

## 关联与覆盖范围

`call_id` 标识一次执行，关联它的 `call`、编号 `attempt` 和 `delivery` 记录。
它在请求控制中生成一次，贯穿执行完成与响应体生命周期。
`request_id_fingerprint` 是已绑定请求 ID 的进程内带密钥指纹；没有宿主关联时为 null。
`target_fingerprint` 是实际传输 URL 的指纹，不是客户端请求的模型或自报目标。重定向仍被禁用。
指纹密钥在进程内随机生成，不是可跨重启使用的身份，也不承诺密码学匿名化。
不输出原始 URL、请求 ID、缓存键、会话 ID、Scope、provider 异常、模型名、凭据、prompt 或响应正文。
记录不证明任务身份或 Source 投影覆盖；除非嵌入宿主另行提供关联，否则没有任务关联能力。

覆盖从 `Executor::run` 被轮询时开始。认证、非法请求体、中间件拒绝以及其他执行前入口错误不在本证据流内。
完整模型响应不代表释放成功。`attempts` 只统计实际交给 transport 的尝试，包含降级发送；
准入过期而未发送时为零。重试沿用同一 `call_id`，只有重新准入后真实发送才增加尝试编号。
不确定尝试不重发。演示接口 `record_not_sent(0, ...)` 的 prepare/capture/降级诊断不导出，
也不被当作模型未发送事实。

## 终态与发送事实

`call` 记录包含 `terminal`、`sending`、`attempts`、`upstream_status`、归一化 `usage`、
可选 `interruption` 和计时字段。终态取值为 `release_authorized`、`blocked`、`withheld`、
`send_indeterminate`、`not_sent`、`gate_paused`、`degraded`、`admission_expired`、`abandoned`。
`sending` 独立表示 `no_send`、`definitely_not_sent`、`possibly_sent`、`response_received`。
取得响应后发生释放拒绝或期限过期，仍保留 `response_received` 与已上报的 usage。

pending 执行 future 销毁时输出 `abandoned` 并保留发送事实。
共享取消信号已设置时，`interruption` 为 `cancellation_requested`；否则宿主期限已过时为
`deadline_elapsed`，其余情况为 `future_dropped`。这些是销毁时的观测，**不证明销毁原因**。
沿用 G3-0 的共享生命周期语义：发出取消信号或超过期限本身不会中断执行，所有者必须停止/丢弃 future。
证据层不新增取消权或定时器。传输中取消保持 `possibly_sent` 和未知 usage，不能记成 `not_sent`。

每个 `attempt` 包含 `attempt`、`outcome`、`started_ms`、`duration_ms`、`upstream_status`、归一化
`usage`、`response_first_byte_ms`、`response_complete_ms`。
结果取值为 `possibly_sent`（pending 发送被中断）、`delivered`、`upstream_error`（完整 HTTP 响应且状态码 >= 400）、
`connect_refused`、`rejected_locally`、`lost_after_handoff`、`timeout_awaiting_response`、`buffer_limit_exceeded`。
部分响应失败保留已观察到的状态码与首字节时间，完整响应时间和 usage 保持未知。
transport 交付完整响应后，解码或释放失败不会丢掉 usage。直接丢弃 pending transport 时无法取得其内部的部分观测。

## Usage 映射

`usage_mapping` 为 `open_ai_chat` 或 `open_ai_responses`，由实际线格式 renderer 选择，不使用客户端模型名。
无法识别 renderer 时 mapping 为 null、usage 保持未知。
`usage.input`、`usage.output`、`usage.cache_read`、`usage.cache_write` 各有 `tokens`（非负整数或 null）
与 `completeness`（`known`、`estimated`、`unknown`、`reconciled`）。当前只输出 provider 明确报告的
`known` 或 `unknown`，不执行估算或对账。

| 数量 | OpenAI Chat | OpenAI Responses |
| --- | --- | --- |
| 输入 | `usage.prompt_tokens` | `usage.input_tokens` |
| 输出 | `usage.completion_tokens` | `usage.output_tokens` |
| 缓存读取 | `usage.prompt_tokens_details.cached_tokens` | `usage.input_tokens_details.cached_tokens` |
| 缓存写入 | 未知 | 未知 |

输入数量**包含**缓存输入；不能把缓存读取再加到输入上，也不能从已上报的输入字段减掉它。
不会把 Anthropic 的缓存创建/写入字段猜测为 OpenAI 字段。缺失、null、畸形或负数保持未知；明确上报的零为已知零。
仅有 `total_tokens` 不能确定输入/输出数量。SSE 只使用 Chat usage chunk 中明确的 usage 对象或 Responses
事件的 `response.usage`，保留最后一个对象，不累加累计 chunk。SSE usage 缺失或无法解析时保持未知。
这不会扩展 Core 接受的流式协议范围。

## 计时与 HTTP 交付

所有时长使用单调时钟，单位为毫秒，向下取整。null 表示不可得。

| 字段/边界 | 含义 |
| --- | --- |
| 调用 `duration_ms` | 创建请求控制至执行终态/销毁，包含创建控制后的入口耗时 |
| 尝试 `started_ms` | 创建请求控制至交给 transport |
| 尝试 `duration_ms` | 交给 transport 至得到传输结果或 future 销毁 |
| `response_first_byte_ms` | 从等待响应头**之前**的 transport 发送起点至首个非空 HTTP 正文 chunk |
| `response_complete_ms` | 从同一 transport 起点至完整缓冲响应 |
| `upstream_ttft_ms` | 始终为 null；HTTP JSON/SSE 字节无法证明模型首 token |
| `release_decision_ms` | 创建请求控制至 before-release 检查返回，包含错误与拒绝 |
| `release_authorized_ms` | 创建请求控制至通过校验且未过期的释放批准，位于可选 capture 之后 |
| 交付 `elapsed_ms` | 创建请求控制至所标记的框架观测 |

`delivery` 在获准/降级响应体放入 HTTP 响应时报告 `framework_handoff`，框架首次轮询取出非空 data frame
时报告 `first_body_frame`，包装的 body 报告完成时为 `body_completed`，观察到完成之前被丢弃时为 `body_dropped`。
strict 模式的释放批准先于框架交接与内容 frame 观测。降级响应没有释放批准时间。
这些边界都不证明 socket 写入、客户端收到、呈现或采用。当前宿主无法观察实际 socket 写出。
非流式 Responses JSON 的首字节及重放的 SSE 输出不是 provider TTFT。

## 部署与兼容

原来从 stdout 采集人类可读 tracing 的部署，需要改为采集 stderr，并单独解析 stdout 证据。
无需数据库迁移、新 HTTP 端点、Python operational 字段或 OpenAPI 重新生成。
该 JSON schema 独立于 Server 的 Python 日志白名单与现有 exporter。
自定义嵌入 sink 的同步回调必须有界、不阻塞且不执行 I/O；将阻塞 writer 接入 `JsonEvidenceSink`，
每个宿主保留一个 sink。自定义 writer 应返回 I/O 错误，不应 panic。
Rust 集成改为读取 `CallEvidence.usage` 中逐字段的数量与完整性，不再读取演示记录原来的扁平 token/completeness 字段。
不存在生产记录存储迁移。集成需将 `RawResponse.ttft_ms` 替换为明确的首字节/完整响应字段，并将
`SendOutcome::IncompleteResponse` 视为不确定结果，保留其进度，禁止重试。
