---
title: 完整功能 Quick Start
description: 配置模型、启动 Server，并验证完整 Memory 闭环。
---

# 完整功能 Quick Start

`powercontext server run` 不配置模型也可以运行，但依赖模型的提取和向量检索不会启用。引导式配置会启用 generation、
embedding、定时 Source 处理，并写入 metrics 和 tracing 设置。

| 能力 | 最小 Server | 完整功能 Runtime |
| --- | --- | --- |
| Source capture | 启用 | 启用 |
| Memory extraction | 关闭 | 启用 |
| Search mode | `auto, fts` | `auto, fts, vector, hybrid` |
| Dashboard | 默认 Scope | 默认 Scope 和所有已创建 Scope |
| MCP endpoint | `/mcp` | `/mcp` |

Server 首次启动时创建一个使用不透明 ID 的默认 Scope。Dashboard 从 Server 发现 Scope descriptor，不使用预配置列表。
Integration 可以把 Session 或 workspace 绑定到默认 Scope，也可以绑定到其他已经存在的 Scope。

## 1. 安装并生成配置

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext config init --output .env
```

按提示输入 provider connection 和 credential。本地 provider 忽略鉴权时，使用该 provider 接受的非秘密占位值。

在不打印 credential 的情况下检查并校验配置：

```bash
powercontext config show --env-file .env
powercontext config validate --env-file .env
```

生成文件包含 Server、模型、数据库、Scheduler 和 integration transport 设置。Scope identity 由运行中的 Server 管理，
Config Generator 不会凭空生成 Scope ID。

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

打开 <http://127.0.0.1:8000/>，确认默认 Scope 可见。获取其不透明 ID，供后续 API 检查使用：

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

## 4. 启动 Codex

使用 Config Generator 输出的命令安装插件，加载 `.env` 后启动 Codex。普通 Session 流程不要设置
`POWERCONTEXT_CODEX_SCOPE_ID`：插件依次解析 Session binding、workspace binding 和 Server 默认 Scope。只有宿主必须
显式选择一个已存在 Scope 时才设置该变量。

Codex 启动后发送普通 prompt。插件从绑定 Scope 召回内容，并把 prompt 捕获为 Source 证据。Scheduler 会在配置的间隔内
处理新 Source。

## 数据与重启

没有覆盖数据库设置时，SQLite 在用户数据目录保存 `powercontext.db` 和 `scheduler.db`：

- Linux：`$XDG_DATA_HOME/powercontext`，或 `~/.local/share/powercontext`；
- macOS：`~/Library/Application Support/powercontext`。

按 `Ctrl+C` 停止 Server。使用同一 `.env` 和数据目录重启后，默认 Scope 及其不透明 ID 保持稳定，因为它们保存在数据库中。

| 现象 | 处理方式 |
| --- | --- |
| Dashboard 中缺少 Scope | 确认 Scope 已通过 Scope API 创建，然后刷新页面 |
| Readiness 为 `degraded` | 检查模型标识、credential 和 Base URL |
| 没有 `vector` 或 `hybrid` | 同时配置 Embedding model、profile ID 和 dimension |
| Source 一直 pending | 启用 Scheduler，或调用 `/v1/memory/flush` |
| 已有数据消失 | 恢复原数据库 URL 或 `POWERCONTEXT_HOME` |

更多信息见[故障排查](troubleshoot.md)和[配置](../reference/configuration.md)。
