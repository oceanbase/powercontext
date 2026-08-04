---
title: 配置
description: PowerContext 路径、Server、Client、推理和 Codex 环境变量。
---

# 配置

PowerContext 进程启动时从环境变量读取配置。

## 用户数据

`POWERCONTEXT_HOME` 可覆盖已安装 Server 使用的数据目录：

```bash
export POWERCONTEXT_HOME=/srv/powercontext
```

未覆盖时，默认目录为：

- Linux：`$XDG_DATA_HOME/powercontext`，未设置时为 `~/.local/share/powercontext`；
- macOS：`~/Library/Application Support/powercontext`。

默认 SQLite 数据库是该目录下的 `powercontext.db`。启用定时处理时，调度状态保存在同一目录的
`scheduler.db`。

## Server

Server 配置使用 `POWERCONTEXT_SERVER_` 前缀。

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_SERVER_HTTP_HOST` | `127.0.0.1` | 监听地址 |
| `POWERCONTEXT_SERVER_HTTP_PORT` | `8000` | 监听端口 |
| `POWERCONTEXT_SERVER_MCP_ENABLED` | `true` | 启用 Streamable HTTP MCP |
| `POWERCONTEXT_SERVER_MCP_PATH` | `/mcp` | MCP 路径 |
| `POWERCONTEXT_SERVER_AUTH_ENABLED` | `false` | HTTP 和 MCP 是否要求一个静态 Bearer token |
| `POWERCONTEXT_SERVER_AUTH_TOKEN` | 未设置 | 静态 Bearer token；启用鉴权时必须设置 |
| `POWERCONTEXT_SERVER_LOGGING_LEVEL` | `INFO` | operational log 级别 |
| `POWERCONTEXT_SERVER_LOGGING_FORMAT` | `console` | `console` 或结构化 `json` 输出 |
| `POWERCONTEXT_SERVER_LOGGING_ACCESS` | `true` | 记录外部 HTTP 和逻辑 MCP request completion |
| `POWERCONTEXT_SERVER_METRICS_ENABLED` | `true` | 在 `/metrics` 暴露 Prometheus metrics |
| `POWERCONTEXT_SERVER_TRACING_ENABLED` | `false` | 启用 span recording 和 OTLP export |
| `POWERCONTEXT_SERVER_DATABASE_URL` | 用户数据目录下的 SQLite 文件 | SQLAlchemy 异步数据库 URL |
| `POWERCONTEXT_SERVER_RUNTIME_SOURCE_WINDOW_LIMIT` | `100` | 单次 activation 最多处理的 Source 数量 |
| `POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS` | 未设置 | Scheduler 间隔；未设置即不启用 |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL` | 未设置 | 用于 Memory extraction 的 Pydantic AI 模型标识 |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_TIMEOUT_SECONDS` | `30` | Generation 超时 |
| `POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS` | 未设置 | Experience 孵化间隔；未设置即不启用该 job |
| `POWERCONTEXT_SERVER_EXTERNAL_SKILLS` | 未设置 | 包含 host identity 和显式 Codex Skill roots 的 JSON object |

静态 Bearer 鉴权默认关闭。启用后，API 和 MCP 请求必须携带 `Authorization: Bearer <token>`；liveness 和
readiness endpoint 仍然公开。明文 HTTP 应只用于 loopback 地址；通过网络暴露启用鉴权的 Server 前必须配置 TLS。

指定 SQLite 路径并启用定时提取的示例：

```bash
export POWERCONTEXT_SERVER_DATABASE_URL=sqlite+aiosqlite:////srv/powercontext/runtime.db
export POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS=30
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
powercontext server run
```

`OPENAI_API_KEY` 等 provider 凭据由所配置的推理 provider 读取。不要把密钥放入命令行参数、文档或
Memory。请把 `provider:model-name` 替换为 Pydantic AI 支持的模型标识。定时提取需要同时配置 generation
model 和 `POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS`；显式 Memory 写入不需要这两项配置。

同一个 generation model 也控制显式 Experience generation、managed Skill generation/evolution，以及
external Skill import/fork。未配置模型时，这些 operation 会在持久化 Candidate 前返回 capability error；
Candidate Review、exact read 和 external Skill scan/list/resolve 仍可使用。

Experience 孵化使用独立的 APScheduler job 和持久化 Source cursor，可通过以下配置启用：

```bash
export POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS=30
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
powercontext server run
```

每次 activation 固定检查最多 32 条 Source，并且只把 metadata 包含 `"kind": "task-outcome"` 的 Content Source
暴露给模型。该 job 会在 Review Inbox 中创建 pending Experience Candidate；它不会自动批准、进入
PreparedContext、创建 managed Skill、将它导出给 Codex 或执行任何内容。Memory 和 Experience job 共用
`POWERCONTEXT_HOME` 下的 APScheduler sidecar，但拥有独立的 job identity 和业务 cursor；取消其中一个 interval
只会移除对应 job。

### 外部 Codex Skill

通过一个 JSON 值配置 host-local roots：

```bash
export POWERCONTEXT_SERVER_EXTERNAL_SKILLS='{
  "host_id": "workstation-1",
  "codex_roots": [
    {
      "root_id": "repository",
      "installation_scope": "project",
      "path": "/srv/project/.agents/skills"
    }
  ]
}'
```

每个 root ID 必须唯一；支持的 installation scope 是 `user`、`project` 和 `plugin`。PowerContext 只扫描这些
显式 root 的直接 Skill package 子目录，不会推断 home 目录、安装 package 或授予执行权限。`host_id`、locator
和 registration 都是本地环境状态，不是跨 host 或跨 Agent contract。

Server 始终创建 non-recording OpenTelemetry request context，从 inbound span 派生 `X-PowerContext-Request-ID`。如需为
CLI 管理的 Server 启用 recording 和 export，请安装 `powercontext[cli,server,tracing-otlp]`、启用 tracing，
并使用 `OTEL_EXPORTER_OTLP_ENDPOINT`、`OTEL_EXPORTER_OTLP_HEADERS` 和 `OTEL_SERVICE_NAME` 等标准
OpenTelemetry 环境变量进行配置。不使用 `powercontext` command 的 programmatic Server integration 可以省略
`cli` extra。

使用 OceanBase 时，通过环境或 secret manager 提供 URL：

```bash
export POWERCONTEXT_SERVER_DATABASE_KIND=oceanbase
export POWERCONTEXT_SERVER_DATABASE_URL="$OCEANBASE_URL"
```

URL 必须使用 `mysql+aoceanbase` driver，包含明确的端口和数据库，并设置 `charset=utf8mb4`。对应
tenant 必须使用 MySQL 兼容模式。

### Embedding

只有同时设置以下三个标识字段，才会启用 embedding 检索：

```bash
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL=provider:embedding-model
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID=embedding-model-v1
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=1024
```

请把示例值替换为所选 provider model、稳定的 profile ID，以及该模型的 dimension。

可选设置包括 `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_NORMALIZATION` 和
`POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_TIMEOUT_SECONDS`。

Embedding normalization 默认为 `unit`。

### SQLite Vec1

SQLite vector 和 hybrid search 还需要 0.7 或更高版本的
[SQLite Vec1](https://sqlite.org/vec1/doc/trunk/doc/vec1.md) loadable extension。PowerContext 不负责下载、构建或更新
这个 native library。请先获取适用于 Server 操作系统和架构的构建产物，再同时配置 extension 路径和完整的
embedding profile：

```bash
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL=provider:embedding-model
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID=embedding-model-v1
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=1024
export POWERCONTEXT_SERVER_DATABASE_VEC1_EXTENSION=/opt/sqlite-extensions/vec1
powercontext server run
```

extension 路径必须指向 SQLite loader 可以打开的 library。Server 打开数据库时，PowerContext 会加载并探测该
extension；如果 library 不兼容或版本低于 0.7，启动会失败。

在另一个终端确认初始化后的 Runtime 已报告 vector 和 hybrid search：

```bash
powercontext client capabilities
```

如果没有可用的 Vec1，请不要设置 `POWERCONTEXT_SERVER_DATABASE_VEC1_EXTENSION`。即使没有 embedding model 或
native extension，SQLite full-text search 仍然可用。

## Client CLI

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_CLIENT_SERVER_URL` | `http://127.0.0.1:8000` | Server base URL |
| `POWERCONTEXT_CLIENT_API_TOKEN` | 未设置 | 发送给启用鉴权的 Server 的 Bearer token |
| `POWERCONTEXT_CLIENT_TIMEOUT` | `10` | HTTP 超时秒数 |

`powercontext client` 为 Server URL 和 timeout 提供对应的单次命令参数。Token 只能通过环境变量提供，避免出现在
命令行参数中。

## Codex 插件

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_CODEX_SCOPE_ID` | 根据 Git remote 或项目路径生成 | 覆盖项目 scope |
| `POWERCONTEXT_CODEX_AUTHORIZATION` | 未设置 | Hook 与 MCP 请求使用的完整 `Bearer <token>` header |
| `POWERCONTEXT_CODEX_CAPTURE_PROMPTS` | `true` | 把用户提示词采集为 Source 证据 |
| `POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE` | `false` | 采集后等待 Source 处理 |
| `POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS` | `1` | Hook 单次请求超时 |
| `POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS` | `4` | Hook 共享 HTTP 时间预算 |
| `POWERCONTEXT_CODEX_FLUSH_MAX_CALLS` | `4` | 每个提示词最多执行的 flush 次数 |

Codex Hook 外层超时为十秒。Server 不可用或拒绝鉴权时，恢复、采集和 flush 独立降级，不会阻塞 Codex。
该变量必须存在于启动 Codex 的进程环境中；修改后需要重启 Codex。

## Builtin CLI

`powercontext builtin` 使用相同的 database、runtime 和 inference 字段，前缀为
`POWERCONTEXT_BUILTIN_`。它默认使用内存 SQLite；如需让多次 CLI 调用共享状态，请设置
`POWERCONTEXT_BUILTIN_DATABASE_URL`。
