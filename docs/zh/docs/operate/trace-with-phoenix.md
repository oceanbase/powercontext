---
title: 用 Phoenix 查看 trace
description: 把 PowerContext 的 transport、application 和推理 span 导出到本地 Phoenix 容器。
---

# 用 Phoenix 查看 trace

PowerContext 会为 transport 和 application 操作导出 OpenTelemetry span。启用 tracing 后，PowerContext 自己构造的
generation 与 embedding 调用也会被 trace，因此一条 trace 里可以同时看到请求、Memory 操作，以及其下的模型调用。

本文把这些 span 发送到本地运行的 [Phoenix](https://github.com/Arize-ai/phoenix)。

## 启动 Phoenix

```bash
docker run -d --name powercontext-phoenix -p 6006:6006 arizephoenix/phoenix:20.1.0
```

Phoenix 的 UI 和 OTLP HTTP 接收端都在端口 `6006`。打开 <http://localhost:6006> 确认已启动。请固定一个明确的
镜像 tag，以保证端点和 UI 布局与本文一致。

## 安装导出依赖

recording 和 export 需要 `tracing-otlp` extra：

```bash
uv tool install --force "powercontext[cli,server,tracing-otlp] @ git+https://github.com/oceanbase/powercontext.git@master"
```

缺少该 extra 时，启用 tracing 会在启动阶段直接报错，而不是静默丢弃 span。

## 配置并启动 Server

启用 tracing、把 exporter 指向 Phoenix，并配置一个 generation model，让推理 span 有内容可记录：

```bash
export POWERCONTEXT_SERVER_TRACING_ENABLED=true
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006
export OTEL_SERVICE_NAME=powercontext-server
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
powercontext server run
```

OpenTelemetry SDK 会在 `OTEL_EXPORTER_OTLP_ENDPOINT` 后追加 `/v1/traces`，因此 span 最终发往
`http://localhost:6006/v1/traces`。如果 Phoenix 部署需要鉴权，请使用 `OTEL_EXPORTER_OTLP_HEADERS`。
按所选 generation model 的要求设置 provider 凭据；PowerContext 不会记录凭据。

## 触发一次推理请求

将 `POWERCONTEXT_SCOPE_ID` 设置为 `create_scope` 返回的已有 ID，先捕获一个 Source，再把它转成 Memory：

```bash
curl -X POST http://localhost:8000/v1/sources/content \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${POWERCONTEXT_SCOPE_ID}\",\"source_id\":\"task-1\",\"content\":\"I always book aisle seats.\"}"
```

```bash
curl -X POST http://localhost:8000/v1/memory/flush \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${POWERCONTEXT_SCOPE_ID}\"}"
```

Memory extraction 发生在 flush 阶段，而不是捕获阶段。

## 查看 trace

打开 <http://localhost:6006>，选择 `default` project，打开 `powercontext-server` 最新的一条 trace。这次 flush
在同一条 trace 中产生五层嵌套 span：

| Span | 含义 |
| --- | --- |
| `HTTP flush_memory` | 入站 HTTP 请求。`powercontext.request.id` 与响应头 `X-PowerContext-Request-ID` 一致。 |
| `powercontext flush_memory` | application 操作，与调用它的 transport 无关。 |
| `memory.flush` | 实际处理 Source window 的 Runtime stage。extraction 跑起来时，推理 span 嵌套在它下面。 |
| `invoke_agent memory_extraction` | 一次 PowerContext generation 任务。名字标识用途，不是模型名。 |
| `chat <model>` | 一次发往模型 provider 的请求，包含 token 用量和耗时。 |

scope 相关操作还会在 application operation 之下添加以下内部 stage span。只读查询不获取写锁，因此不会产生
`scope.lock` span：

| Span | 含义 |
| --- | --- |
| `scope.context` | 从配置的 provider 解析该 scope 的 context；内建 provider 下接近零，provider 在此做 I/O 时才可见。 |
| `scope.lock` | 等待该 scope 的写锁，在获取到锁的瞬间结束。`powercontext.scope.lock.contended` 表示进入时是否已被其他操作持有。 |
| `memory.flush` | 一次 Source-window flush，出现在 `flush_memory` 或定时激活之下。 |
| `memory.search` | `search_memory` 或 `prepare_context` 中的 Memory 查询；存在 embedding 或 reranking span 时，它们嵌套在其下。 |
| `memory.rerank` | 一次实际 reranker 调用；使用模型的 reranking 会在其下嵌套 `invoke_agent memory_rerank`。 |
| `experience.search` | `prepare_context` 中的 Experience recall；未配置 recall 时也会产生。 |
| `experience.incubation` | 一次进程内 Experience incubation 操作。 |
| `context.build` | 根据召回候选同步选择并渲染最终 prepared context 的步骤。 |

其他 generation 任务遵循同样的命名约定：`experience_incubation`、`experience_generation`、`skill_generation`、
`handoff_generation` 和 `memory_rerank`。配置了 embedding model 时，embedding 调用会作为 `embeddings <model>`
span 挂在触发它的操作之下。

span 是批量导出的，刷新前请稍等几秒。MCP 请求会用 `MCP mcp.tools.call` 取代 `HTTP` span。readiness 探活被有意
排除在 trace 之外，因此健康检查不会产生只含单个 span 的 trace。

## 后台 Worker span

每次 Scope Worker 调用都会开启独立 trace，包括 Family 定时计划与显式 flush 触发的请求。Memory、Topic Memory、
Experience 和 Profile 使用相同的生命周期 span。根 span 的 `powercontext.operation.unit` 为 `background`，
不会继承 HTTP 或 MCP 请求的 trace：

| Span | 含义 |
| --- | --- |
| `artifact_processing.worker` | 一次有界 Scope 调用，包含进程启动与持久化完成确认。 |
| `artifact_processing.worker.start` | 创建并启动隔离的子进程。 |
| `artifact_processing.worker.wait` | 等待子进程结果或本次调用超时。 |
| `artifact_processing.worker.acknowledge` | 核对当前 fence，以及分配的请求代数是否已经持久化确认。 |

根 span 仅在请求持久化确认后记为 `success`，包括已持久化的 NOOP。子进程正常退出但没有确认请求时记为
`failure`。其他 outcome 包括 `failure`、`cancelled`、`cursor_conflict` 和 `head_conflict`。每次重试产生新的独立根 span。

生命周期 span 记录 `powercontext.artifact_processing.family`；失败根 span 还记录有界的
`powercontext.artifact_processing.failure` 类别，例如 `timeout`、`worker_failed`、
`missing_durable_acknowledgement` 或 `leadership_lost`。其中不含 Scope ID、request ID、Source 数据或模型正文。
这些父进程 span 描述 Worker 生命周期和完成确认；推理在隔离子进程中执行，不会把模型 span 挂入父进程生命周期 trace。

## 哪些内容不会被导出

PowerContext 在配置推理 instrumentation 时关闭了内容记录。span 只携带模型标识、token 用量、耗时和错误类别；
prompt、模型响应、Memory 内容和向量都不会被导出，消息类属性只记录每条消息的结构，不记录正文。

## 停止 Phoenix

```bash
docker rm -f powercontext-phoenix
```

span 名与属性遵循 Pydantic AI 的 GenAI 语义约定，跨大版本升级该依赖时可能变化，不应视为稳定契约。
