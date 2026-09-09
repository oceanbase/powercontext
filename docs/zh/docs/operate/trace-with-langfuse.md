---
title: 用 Langfuse 查看 trace
description: 通过标准 OTLP 配置，把 PowerContext 的 transport、application 和推理 span 导出到 Langfuse。
---

# 用 Langfuse 查看 trace

PowerContext 会为 transport 和 application 操作导出 OpenTelemetry span。启用 tracing 后，PowerContext 自己构造的
generation 与 embedding 调用也会被 trace，因此一条 trace 里可以同时看到请求、Memory 操作，以及其下的模型调用。

本文把这些 span 通过 OTLP 端点发送到 [Langfuse](https://langfuse.com)。整个过程不需要改动 PowerContext 代码，也不需要
Langfuse SDK：只是把 [用 Phoenix 查看 trace](trace-with-phoenix.md) 中的标准 OpenTelemetry 变量改为指向 Langfuse。

## 启动 Langfuse

Langfuse 自托管通过 Docker Compose 运行多个服务（web、worker、PostgreSQL、ClickHouse、Redis 和 MinIO）：

```bash
git clone https://github.com/langfuse/langfuse.git
cd langfuse
docker compose up -d
```

打开 <http://localhost:3000>，创建用户、organization 和 project，然后在 project 设置里创建一对 API key。记下 public key
（`pk-lf-...`）和 secret key（`sk-lf-...`），下文用它们为 exporter 鉴权。OTLP 端点要求 Langfuse v3.22.0 及以上；本文
在 Langfuse 4.10.0 上验证。

如需可复现的本地环境，[headless initialization](https://langfuse.com/self-hosting/headless-initialization) 可以通过
环境变量直接创建 organization、project、用户和 key，而不必经过 UI。Langfuse Cloud 的用法与自托管相同：跳过 compose
步骤，把下文的 `http://localhost:3000` 换成所在区域的基础 URL，例如 `https://cloud.langfuse.com` 或
`https://us.cloud.langfuse.com`。

## 安装导出依赖

recording 和 export 需要 `tracing-otlp` extra：

```bash
uv tool install --force "powercontext[cli,server,tracing-otlp] @ git+https://github.com/oceanbase/powercontext.git@master"
```

缺少该 extra 时，启用 tracing 会在启动阶段直接报错，而不是静默丢弃 span。

## 配置并启动 Server

Langfuse 用 project key 组成的 HTTP Basic 认证来鉴权 OTLP 请求。启用 tracing、把 exporter 指向 Langfuse，并配置一个
generation model，让推理 span 有内容可记录：

```bash
export LANGFUSE_PUBLIC_KEY=pk-lf-replace-me
export LANGFUSE_SECRET_KEY=sk-lf-replace-me
LANGFUSE_AUTH=$(printf '%s:%s' "$LANGFUSE_PUBLIC_KEY" "$LANGFUSE_SECRET_KEY" | base64 | tr -d '\n')

export POWERCONTEXT_SERVER_TRACING_ENABLED=true
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:3000/api/public/otel
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic ${LANGFUSE_AUTH},x-langfuse-ingestion-version=4"
export OTEL_SERVICE_NAME=powercontext-server
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
powercontext server run
```

OpenTelemetry SDK 会在 `OTEL_EXPORTER_OTLP_ENDPOINT` 后追加 `/v1/traces`，因此 span 最终发往
`http://localhost:3000/api/public/otel/v1/traces`，正是 Langfuse 期望的 traces 端点。Langfuse 只接受 OTLP over HTTP，
与 `tracing-otlp` extra 安装的 exporter 协议一致。`x-langfuse-ingestion-version=4` 头让 Langfuse 立即处理这些
span；Langfuse 文档指出，缺少该头时摄入最多可能延迟十分钟。按所选 generation model 的要求设置 provider 凭据；
PowerContext 既不会记录凭据，也不会记录 exporter 的请求头。

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

打开 <http://localhost:3000>，选择 project，进入 **Traces** 视图。Langfuse 用根 span 命名 trace，因此这次 flush 显示为
`HTTP flush_memory`。PowerContext 的每个 span 都会成为一个 observation，Langfuse 根据 span 上的 GenAI 属性推断
observation 类型：

| Observation | 类型 | 含义 |
| --- | --- | --- |
| `HTTP flush_memory` | SPAN | 入站 HTTP 请求。其 metadata 中的 `attributes.powercontext.request.id` 与响应头 `X-PowerContext-Request-ID` 一致。 |
| `powercontext flush_memory` | SPAN | application 操作，与调用它的 transport 无关。 |
| `memory.flush` | SPAN | 实际处理 Source window 的 Runtime stage。其他 stage span（如 `scope.context`、`scope.lock`、`memory.search`、`context.build`）同样是 SPAN observation。 |
| `memory_extraction run` | AGENT | 一次 PowerContext generation 任务。Langfuse 取 span 的 `logfire.msg` 属性作为名称，因此 Pydantic AI 的 `invoke_agent memory_extraction` span 以这个名字出现。 |
| `chat <model>` | GENERATION | 一次发往模型 provider 的请求，包含模型名、耗时，以及 input、output 和 total token 用量。 |

其他 generation 任务遵循同样的模式，例如 `experience_incubation run` 和 `memory_rerank run`。

MCP 请求以 `MCP mcp.tools.call` 作为根 observation。FastMCP 会添加一个以工具名命名的 `TOOL` observation，
`powercontext <operation>` span 及其 stage 嵌套在其下。readiness 探活被有意排除在 trace 之外。

span 属性以 `attributes.<name>` 的形式出现在每个 observation 的 metadata 中，resource 属性则是
`resourceAttributes.<name>`。要定位某次请求的 trace，请用响应头 `X-PowerContext-Request-ID` 的值过滤 metadata key
`attributes.powercontext.request.id`。失败的操作带有 `ERROR` level 和 `attributes.error.type`。

Langfuse 根据模型定义匹配模型名来推算 generation 成本；未识别的模型只显示用量而没有成本，直到你在 project 的模型
设置中添加定义。之后即可在 Langfuse 的 dashboard 与 Metrics API 中汇总 token 用量和成本。

span 是批量导出的，刷新前请稍等几秒。定时后台激活会以独立 trace 到达，见
[用 Phoenix 查看 trace](trace-with-phoenix.md) 中的「定时后台 span」一节。

## 哪些内容不会被导出

PowerContext 在配置推理 instrumentation 时关闭了内容记录。observation 只携带模型标识、token 用量、耗时和错误类别；
prompt、模型响应、Memory 内容、搜索 query 和向量都不会被导出，因此 generation 的 input 与 output 面板只显示每条
消息的 role 和 part 类型，不会显示正文。PowerContext 也不设置 Langfuse 的 user、session 或 tag 属性，因此用户与
会话视图保持为空，trace 需要通过 metadata 定位。

## 停止 Langfuse

```bash
docker compose down
```

追加 `-v` 可同时删除已存储的 trace。

span 名与属性遵循 Pydantic AI 的 GenAI 语义约定，跨大版本升级该依赖时可能变化，不应视为稳定契约。
