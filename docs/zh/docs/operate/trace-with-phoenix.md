---
title: 用 Phoenix 查看 trace
description: 把 PowerContext 的 transport、application 和推理 span 导出到本地 Phoenix 容器。
---

# 用 Phoenix 查看 trace

PowerContext 会为 transport 和 application 操作导出 OpenTelemetry span。启用 tracing 后，PowerContext 自己构造的
generation 与 embedding 调用也会被 trace，因此一条 trace 里可以同时看到请求、Memory 操作，以及其下的模型调用。

本文把这些 span 发送到本地运行的 [Phoenix](https://github.com/Arize-ai/phoenix)。

## 前置要求

准备一台能够运行 PowerContext Server 的 Linux 或 macOS 开发机，并确保：

- 已安装并启动 Docker；macOS 使用 Docker Desktop。
- 已安装 `uv`、Bash、`curl` 和 `python3`（用于提取 API 响应中的 Scope ID）。
- 本机端口 `6006` 和 PowerContext Server 使用的端口（默认 `8000`）未被占用。

开始前可以运行以下命令确认工具可用：

```bash
docker info
uv --version
```

## 启动 Phoenix

```bash
docker run -d --name powercontext-phoenix -p 6006:6006 arizephoenix/phoenix:20.1.0
```

临时容器和下方持久化方案二选一，不要依次运行两个同名容器的启动命令。若希望删除容器后仍保留 trace，请在首次启动时
改用下方命令，将 `PHOENIX_WORKING_DIR` 指向命名卷；已有临时容器时，先备份需要保留的数据并移除旧容器。

```bash
docker volume create powercontext-phoenix-data
docker run -d --name powercontext-phoenix -p 6006:6006 \
  -e PHOENIX_WORKING_DIR=/mnt/data \
  -v powercontext-phoenix-data:/mnt/data \
  arizephoenix/phoenix:20.1.0
```

Phoenix 默认使用 SQLite；官方建议将 `PHOENIX_WORKING_DIR` 指向持久卷。更多存储配置请参考 [Phoenix 存储配置文档](https://arize.com/docs/phoenix/self-hosting/deployment-options/docker)。

Phoenix 的 UI 和 OTLP HTTP 接收端都在端口 `6006`。打开 <http://localhost:6006> 只能确认 UI 可访问、Phoenix
进程已经启动。即使直接请求 `GET /v1/traces` 得到 HTTP 200，也只能说明 HTTP 路由可访问，不能证明 OTLP span
已经被正确接收、存储并可查询。请固定一个明确的镜像 tag，以保证端点和 UI 布局与本文一致。

本文使用 OTLP/HTTP exporter，因此不把 `4317` 端口检查列为必做步骤；`4317` 是 OTLP/gRPC 入口，只有改用 gRPC
exporter 时才需要验证。

## 安装导出依赖

recording 和 export 需要 `tracing-otlp` extra：

```bash
uv tool install --force "powercontext[cli,server,tracing-otlp] @ git+https://github.com/oceanbase/powercontext.git@master"
```

缺少该 extra 时，启用 tracing 会在启动阶段直接报错，而不是静默丢弃 span。

这条命令面向新部署，也会强制重建已有的 `uv tool` 工具环境并替换其中的 PowerContext。执行前请确认现有 Server 的
安装方式和配置文件位置；命令从 `master` 安装当前最新代码，结果会随仓库更新而变化。

如果要让已运行的 PowerContext Server 支持 tracing，需要在**该 Server 实际使用的 Python 环境**中安装完整的
`cli`、`server` 和 `tracing-otlp` extras，然后重启旧进程。对于由 `uv tool` 安装并以前台运行的 Server，先在旧 Server
所在终端按 `Ctrl+C` 停止进程，再执行：

```bash
command -v powercontext
uv tool install --force "powercontext[cli,server,tracing-otlp] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext server run --env-file /path/to/powercontext.env
```

请把示例中的配置文件路径替换为现有 Server 的实际路径。如果 Server 由 systemd、Supervisor 或其他进程管理器启动，
请确认服务指向更新后的 `powercontext` 可执行文件，再通过对应的管理器重启服务；只在另一个环境中安装 exporter 不会让
正在运行的 Server 获得 tracing 能力。

## 配置并启动 Server

`provider:model-name` 只是占位值，不能直接用于运行。请先根据[启用 Memory 提取与向量搜索](../get-started/configure-models.md)
配置一个受支持的 generation model、provider 凭据；仅使用代理或自定义端点时设置 Base URL。下面使用 `openai:gpt-4.1-mini` 作为具体模型标识示例；
实际可用模型仍取决于 provider 账户和区域。

在终端 A 中启用 tracing、把 exporter 指向 Phoenix，并配置 generation model，让 `flush_memory` 触发实际模型调用 span：

```bash
export POWERCONTEXT_SERVER_TRACING_ENABLED=true
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:6006
export OTEL_SERVICE_NAME=powercontext-server
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai:gpt-4.1-mini
powercontext server run
```

`powercontext server run` 会在前台持续运行。保持终端 A 打开，后续检查和请求在终端 B 中执行。

OpenTelemetry SDK 会在 `OTEL_EXPORTER_OTLP_ENDPOINT` 后追加 `/v1/traces`，因此 span 最终发往
`http://localhost:6006/v1/traces`。如果 Phoenix 部署需要鉴权，请使用 `OTEL_EXPORTER_OTLP_HEADERS`。
按所选 generation model 的要求设置 provider 凭据；PowerContext 不会记录凭据。

完整验收需要区分以下三层：

| 检查 | 可以证明什么 |
| --- | --- |
| 打开 <http://localhost:6006>，或检查 HTTP 路由可访问 | Phoenix UI 和 HTTP 服务已启动；不能证明 span 已被接收。 |
| 启动 Server 后发送一次实际操作，并在 Phoenix UI 中查询到对应 span | OTLP/HTTP exporter 已发送有效 span，Phoenix 已完成接收、存储和查询。 |
| `flush_memory` 成功，并在对应 Trace 中看到 `chat <model>` generation span | PowerContext 的实际推理追踪链路可用。 |

在终端 B 中先设置连接地址；如果 Server 使用自定义端口，请替换默认地址。已启用鉴权时，需在该终端提供有效的
`POWERCONTEXT_CLIENT_API_TOKEN`（不要写入文档或版本库）。能力检查还需要 `server.observe` 权限。

```bash
export POWERCONTEXT_BASE_URL="${POWERCONTEXT_BASE_URL:-http://127.0.0.1:8000}"
export POWERCONTEXT_CLIENT_SERVER_URL="$POWERCONTEXT_BASE_URL"
```

随后按以下顺序检查：

1. 使用 `powercontext --json ready` 或直接读取 `/health/ready`，检查返回的 `status` 和 `checks`；自动化场景必须确认
   `status` 为 `ready`，不能只检查命令退出码；
2. 使用 `powercontext capabilities` 确认输出包含 `Memory extraction: enabled`；
3. 执行下面的 `flush_memory` 请求，并在 Phoenix 中确认对应 Trace 包含 `chat <model>` span。

只有第 3 步完成，才能确认实际推理和 tracing 链路可用。仅启动 Server 或 `/health/ready` 返回成功，不能证明 Memory extraction 可用。

## 触发一次推理请求

在终端 B 中执行以下命令。API 示例使用 Bash，并默认访问本机无鉴权实例。如果 Server 已启用鉴权，请先提供客户端 token；下面的请求会统一添加
`Authorization: Bearer` header。创建 Scope 需要 `server.admin` 权限，写入 Source 和 flush 需要对该 Scope 具有
`scope.contribute` 权限。

```bash
export POWERCONTEXT_BASE_URL="${POWERCONTEXT_BASE_URL:-http://127.0.0.1:8000}"
export POWERCONTEXT_IDEMPOTENCY_KEY="tracing-example-$(date +%s)-$"

if [[ -n "${POWERCONTEXT_CLIENT_API_TOKEN:-}" ]]; then
  POWERCONTEXT_AUTH_ARGS=(--header "Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}")
else
  POWERCONTEXT_AUTH_ARGS=()
fi

POWERCONTEXT_SCOPE_ID="$(
  set -o pipefail
  curl --fail --silent --show-error \
    "${POWERCONTEXT_AUTH_ARGS[@]}" \
    --header 'content-type: application/json' \
    --data "{\"title\":\"Phoenix tracing example\",\"summary\":\"Scope for tracing verification\",\"idempotency_key\":\"${POWERCONTEXT_IDEMPOTENCY_KEY}\"}" \
    "${POWERCONTEXT_BASE_URL}/v1/scopes" \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["scope_id"])'
)"
: "${POWERCONTEXT_SCOPE_ID:?创建 Scope 失败，请检查响应和鉴权后重试}"
export POWERCONTEXT_SCOPE_ID
```

上面的命令会把 `create_scope` 返回的 `scope_id` 保存到 `POWERCONTEXT_SCOPE_ID`。如果需要查看创建请求的 HTTP 状态和
`X-PowerContext-Request-ID`，请用 `curl -i` 单独重跑该请求。然后先捕获一个 Source，再把它转成 Memory：

```bash
export POWERCONTEXT_SOURCE_ID="tracing-example-$(date +%s)-$"

curl --fail --show-error -i -X POST "${POWERCONTEXT_BASE_URL}/v1/sources/content" \
  "${POWERCONTEXT_AUTH_ARGS[@]}" \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${POWERCONTEXT_SCOPE_ID}\",\"source_id\":\"${POWERCONTEXT_SOURCE_ID}\",\"content\":\"I always book aisle seats.\"}"
```

```bash
curl --fail --show-error -i -X POST "${POWERCONTEXT_BASE_URL}/v1/memory/flush" \
  "${POWERCONTEXT_AUTH_ARGS[@]}" \
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

## 停止与删除 Phoenix

暂时停止 Phoenix 时，建议保留容器：

```bash
docker stop powercontext-phoenix
```

之后可以使用以下命令恢复运行：

```bash
docker start powercontext-phoenix
```

如果确认不再需要该容器，再删除容器：

```bash
docker rm -f powercontext-phoenix
```

未挂载持久卷时，`docker rm -f` 会一并删除容器内的 SQLite trace 数据。使用上面的命名卷时，删除容器不会删除
`powercontext-phoenix-data` 卷；只有确认不再需要其中的数据时，才单独删除该卷：

```bash
docker volume rm powercontext-phoenix-data
```

span 名与属性遵循 Pydantic AI 的 GenAI 语义约定，跨大版本升级该依赖时可能变化，不应视为稳定契约。
