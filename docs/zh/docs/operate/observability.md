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
