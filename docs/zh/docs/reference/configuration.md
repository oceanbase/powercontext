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
| `POWERCONTEXT_SERVER_DATABASE_URL` | 用户数据目录下的 SQLite 文件 | SQLAlchemy 异步数据库 URL |
| `POWERCONTEXT_SERVER_RUNTIME_SOURCE_WINDOW_LIMIT` | `100` | 单次 activation 最多处理的 Source 数量 |
| `POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS` | 未设置 | Scheduler 间隔；未设置即不启用 |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL` | 未设置 | 用于 Memory extraction 的 Pydantic AI 模型标识 |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_TIMEOUT_SECONDS` | `30` | Generation 超时 |

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

## Client CLI

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_CLIENT_SERVER_URL` | `http://127.0.0.1:8000` | Server base URL |
| `POWERCONTEXT_CLIENT_TIMEOUT` | `10` | HTTP 超时秒数 |

`powercontext client` 也提供对应的单次命令参数。

## Codex 插件

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_CODEX_SCOPE_ID` | 根据 Git remote 或项目路径生成 | 覆盖项目 scope |
| `POWERCONTEXT_CODEX_CAPTURE_PROMPTS` | `true` | 把用户提示词采集为 Source 证据 |
| `POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE` | `false` | 采集后等待 Source 处理 |
| `POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS` | `1` | Hook 单次请求超时 |
| `POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS` | `4` | Hook 共享 HTTP 时间预算 |
| `POWERCONTEXT_CODEX_FLUSH_MAX_CALLS` | `4` | 每个提示词最多执行的 flush 次数 |

Codex Hook 外层超时为十秒。Server 不可用时，恢复、采集和 flush 独立降级，不会阻塞 Codex。

## Builtin CLI

`powercontext builtin` 使用相同的 database、runtime 和 inference 字段，前缀为
`POWERCONTEXT_BUILTIN_`。它默认使用内存 SQLite；如需让多次 CLI 调用共享状态，请设置
`POWERCONTEXT_BUILTIN_DATABASE_URL`。
