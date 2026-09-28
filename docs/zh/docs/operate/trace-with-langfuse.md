---
title: 用 Langfuse 查看 trace
description: 通过标准 OTLP 配置，把 PowerContext 的 transport、application 和推理 span 导出到 Langfuse。
---

# 用 Langfuse 查看 trace

PowerContext 会为 transport 和 application 操作导出 OpenTelemetry span。启用 tracing 后，PowerContext 自己构造的
generation 与 embedding 调用也会被 trace，因此一条 trace 里可以同时看到请求、Memory 操作，以及其下的模型调用。

本文把这些 span 通过 OTLP 端点发送到 [Langfuse](https://langfuse.com)。整个过程不需要改动 PowerContext 代码，也不需要
Langfuse SDK：只是把 [用 Phoenix 查看 trace](trace-with-phoenix.md) 中的标准 OpenTelemetry 变量改为指向 Langfuse。

## 前置要求

准备一台能够运行 PowerContext Server 的 Linux 或 macOS 开发机，并确保：

- 已安装 Git。
- 已安装并启动 Docker 与 Docker Compose；macOS 使用 Docker Desktop。
- 已安装 `uv`、Bash、`curl` 和 `python3`（用于提取 API 响应中的 Scope ID）。
- 本机端口 `3000` 和 PowerContext Server 使用的端口（默认 `8000`）未被占用。

开始前可以运行以下命令确认工具可用：

```bash
git --version
docker info
docker compose version
uv --version
```

## 启动 Langfuse

Langfuse 自托管通过 Docker Compose 运行多个服务（web、worker、PostgreSQL、ClickHouse、Redis 和 MinIO）：

Langfuse 仓库中的 Compose 文件包含默认数据库密码、Redis 密码、MinIO 密钥和应用密钥，以下命令沿用这些默认值，
只适合单机临时体验。如果主机可能被其他人访问或用于长期运行，请先替换 Compose 文件中标有 `CHANGEME` 的值。

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

Langfuse 用 project key 组成的 HTTP Basic 认证来鉴权 OTLP 请求。在终端 A 中启用 tracing、把 exporter 指向 Langfuse，并配置
generation model，让 `flush_memory` 触发实际模型调用 span：

```bash
export LANGFUSE_PUBLIC_KEY=pk-lf-replace-me
export LANGFUSE_SECRET_KEY=sk-lf-replace-me
LANGFUSE_AUTH=$(printf '%s:%s' "$LANGFUSE_PUBLIC_KEY" "$LANGFUSE_SECRET_KEY" | base64 | tr -d '\n')

export POWERCONTEXT_SERVER_TRACING_ENABLED=true
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:3000/api/public/otel
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic ${LANGFUSE_AUTH},x-langfuse-ingestion-version=4"
export OTEL_SERVICE_NAME=powercontext-server
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai:gpt-4.1-mini
powercontext server run
```

`powercontext server run` 会在前台持续运行。保持终端 A 打开，后续检查和请求在终端 B 中执行。

OpenTelemetry SDK 会在 `OTEL_EXPORTER_OTLP_ENDPOINT` 后追加 `/v1/traces`，因此 span 最终发往
`http://localhost:3000/api/public/otel/v1/traces`，正是 Langfuse 期望的 traces 端点。Langfuse 只接受 OTLP over HTTP，
与 `tracing-otlp` extra 安装的 exporter 协议一致。`x-langfuse-ingestion-version=4` 头让 Langfuse 立即处理这些
span；Langfuse 文档指出，缺少该头时摄入最多可能延迟十分钟。按所选 generation model 的要求设置 provider 凭据；
PowerContext 既不会记录凭据，也不会记录 exporter 的请求头。

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
3. 执行下面的 `flush_memory` 请求，并在 Langfuse 的 **Traces** 视图中确认对应 Trace 包含 `chat <model>` observation。

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
    --data "{\"title\":\"Langfuse tracing example\",\"summary\":\"Scope for tracing verification\",\"idempotency_key\":\"${POWERCONTEXT_IDEMPOTENCY_KEY}\"}" \
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
