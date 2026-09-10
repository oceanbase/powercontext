---
title: 启用 Memory 提取与向量搜索
description: 配置模型、启动 Server，并验证完整 Memory 闭环。
---

# 启用 Memory 提取与向量搜索

以下步骤使用 `master` 和 Bash。Windows 支持为 `experimental`，平台要求见[安装与运行](install-and-run.md)。

`powercontext server run` 不配置模型也可以运行，但依赖模型的提取和向量检索不会启用。`config init` 只负责生成可启动的
基础环境文件，不在部署过程中索要 provider、credential 或 model；需要完整能力时，再显式补充模型配置。

| 能力 | 最小 Server | 已配置 Runtime |
| --- | --- | --- |
| Source capture | 启用 | 启用 |
| Memory extraction | 关闭 | 启用 |
| Search mode | `auto, fts` | `auto, fts, vector, hybrid` |
| Dashboard | 单独启用，要求静态 token | 单独启用，要求静态 token |
| MCP endpoint | `/mcp` | `/mcp` |

Server 首次启动时创建一个使用不透明 ID 的默认 Scope。Integration 可以把 Session 或 workspace 绑定到默认 Scope，
也可以绑定到其他已经存在的 Scope。

## 1. 安装并生成配置

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext config init --output .env
```

该命令不会询问模型或 credential。要启用完整能力，请编辑 `.env`，至少补充下面这些值，并按 provider 要求补充 credential
和 Base URL：

```dotenv
POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:generation-model
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL=provider:embedding-model
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID=provider-embedding-model-1536-unit-v1
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=1536
POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS=60
```

其中 `generation-model` 负责自动抽取和生成，`embedding-model` 负责向量检索；定时 Source 处理也需要 generation model。
本地 provider 忽略鉴权时，使用该 provider 接受的非秘密占位值。

在不打印 credential 的情况下检查并校验配置：

```bash
powercontext config show --env-file .env
powercontext config validate --env-file .env
```

生成文件包含 Server、数据库和 integration transport 设置；模型配置由你显式补充后才会启用 Scheduler。Scope identity 由运行中的
Server 管理，Config Generator 不会凭空生成 Scope ID。

## 2. 启动并检查 Server

```bash
powercontext server run --env-file .env
```

在另一个终端执行：

```bash
set -a
. ./.env
set +a
powercontext doctor
powercontext ready
powercontext capabilities
```

Readiness 为 `ready`、Memory extraction 已启用，并且 search mode 包含 `vector` 和 `hybrid` 时，完整 Runtime 可用。
如果只有 `auto, fts`，检查 Embedding model、profile ID、dimension、credential 和 Base URL。

获取默认 Scope 的不透明 ID，供后续 API 检查使用：

```bash
SCOPE_ID="$(curl -fsS http://127.0.0.1:8000/v1/scopes/default \
  | python -c 'import json, sys; print(json.load(sys.stdin)["scope_id"])')"
export SCOPE_ID
```

## 3. 验证 Memory 闭环

使用唯一 ID 捕获 Source：

```bash
SOURCE_ID="quickstart-$(date +%s)-$$"
curl -fsS -X POST http://127.0.0.1:8000/v1/sources/content \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${SCOPE_ID}\",\"source_id\":\"${SOURCE_ID}\",\"content\":\"PowerContext quick start check: prefer small, verifiable steps.\"}"
```

保留响应中的 `position`，再 flush 同一 Scope：

```bash
curl -fsS -X POST http://127.0.0.1:8000/v1/memory/flush \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${SCOPE_ID}\"}"
```

返回的 `current_cursor` 必须不小于 capture `position`。Scheduler 已经处理 Source 时，`status: "idle"` 也是有效结果。

列出 Memory entry：

```bash
curl -fsS -X POST http://127.0.0.1:8000/v1/memory/entries/list \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${SCOPE_ID}\"}"
```

找到 `source_refs` 包含已捕获 Source 的 entry，记录其 `citation.entry_id`，再验证向量检索：

```bash
curl -fsS -X POST http://127.0.0.1:8000/v1/memory/search \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${SCOPE_ID}\",\"query\":\"verifiable steps\",\"mode\":\"vector\",\"limit\":50}"
```

响应包含 `mode: "vector"`、已记录的 `entry_id`，且 `matched_by` 包含 `vector` 时，该闭环验证通过。检查模型用量：

```bash
powercontext stats --scope-id "$SCOPE_ID"
```

## 4. 接入 Agent

Server 验证通过后，按[对应 Agent 的文档](../integrations/index.md)配置连接、认证和采集行为。

## 数据与重启

生成的配置不指定数据库位置，因此 Server 把数据保存在用户数据目录，而不是项目内文件。在未设置
`POWERCONTEXT_HOME` 时，SQLite 的 `powercontext.db`（包含持久调度与 operation 状态）位于：

- Linux：`$XDG_DATA_HOME/powercontext`，或 `~/.local/share/powercontext`；
- macOS：`~/Library/Application Support/powercontext`；
- Windows（`experimental`）：`%LOCALAPPDATA%\\powercontext`。

如需迁移，在启动 Server 前设置 `POWERCONTEXT_HOME` 即可。之后再修改数据库 URL 会把 Server 指向另一个（可能是
空的）数据库；需要旧数据时请保留原来的值。

## 停止与恢复

在 Server 终端按 `Ctrl+C` 停止进程。数据持久保存在 SQLite 中，重启不会丢失。恢复时重新加载同一个 `.env`，再次
执行 `powercontext server run --env-file .env`；pending 的 Source 会在下一次调度或 flush 时继续处理。默认 Scope 及其
不透明 ID 也保存在同一数据库中，因此重启后保持稳定。

## 快速排障

| 现象 | 处理方式 |
| --- | --- |
| Readiness 为 `degraded` | 检查模型标识、credential 和 Base URL |
| 没有 `vector` 或 `hybrid` | 同时配置 Embedding model、profile ID 和 dimension |
| Source 一直 pending | 启用 Scheduler，或调用 `/v1/memory/flush` |
| 已有数据消失 | 恢复原数据库 URL 或 `POWERCONTEXT_HOME` |

更多信息见[故障排查](../operate/troubleshoot.md)和[配置](../operate/configuration.md)。

需要分类和检索制品或单条记忆时，参见[自定义标签](../workflows/manage-artifact-tags.md)。
