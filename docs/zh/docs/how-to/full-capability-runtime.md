---
title: 完整功能 Quick Start
description: 5 分钟启动 PowerContext 完整功能。
---

# 完整功能 Quick Start

## 最小运行与完整能力的差别

不带任何配置执行 `powercontext server run`，得到的是最小 Server：进程可以启动、可以接收 Source，但依赖模型的
能力默认关闭。通过 `config init` 引导生成的一份 `.env` 可以把全部能力打开：

| 能力 | 默认最小运行 | 完整能力运行 |
| --- | --- | --- |
| Source 采集 | 启用 | 启用 |
| Memory 提取 | 关闭；Source 保持 pending | 启用；Scheduler 每 60 秒处理一次 |
| 搜索模式 | 仅 `auto, fts` | `auto, fts, vector, hybrid` |
| Dashboard Scope | 未配置 | 可见 `project:quickstart` |
| MCP 端点 | `/mcp` 启用 | `/mcp` 启用 |

两种模式默认都使用 SQLite。向量搜索额外使用内置的 `sqlite-vec` 扩展；当 Embedding model 或其 profile 未配置时，
Server 会回退到 SQLite FTS 并报告 `Search modes: auto, fts`。此时召回仍然可用，但语义搜索和混合搜索需要配置
Embedding model。

## 先确定 Scope ID

Scope ID 是 PowerContext 的数据命名空间，可以把它理解成“项目 ID”。Source、Memory 和 Handoff 都归属于某个
Scope；只有 Dashboard 和 Coding Agent 使用同一个 Scope ID，网页里才能看到 Agent 写入的数据。

同一个 Server 可以保存多个 Scope。Server 启动时配置的是 **Dashboard 可以查看哪些 Scope**，Coding Agent 启动时
配置的是 **本次会话把数据读写到哪个 Scope**：

```text
Coding Agent ──读写──> project:quickstart <──展示── Dashboard
```

Scope ID 可以使用任意简短、稳定、非空的字符串，不要包含密钥或其他秘密。例如：

```text
project:quickstart
git:github.com/oceanbase/powercontext
team:payment-service
```

下面的 Quick Start 统一使用：

```text
project:quickstart
```

## 快速启动

### 第一部分：启动 Server

#### 1. 安装

```bash
uv tool install "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
```

#### 2. 生成配置

```bash
powercontext config init --output .env
```

按照提示填写 provider credential。Pydantic AI provider 在构造时要求提供 credential；如果本地服务忽略认证，
请填写该服务允许的非敏感占位值。

生成完成后会列出 Codex、Claude Code、DeepSeek Harness、OpenCode 和 Pi 的全部 setup 与启动命令。生成的 `.env`
按组写入了原本需要手工拼装的配置：Server HTTP、Dashboard、Scope、Generation model、Embedding model（含
profile ID 与维度）、数据库类型与位置、调度间隔，以及各宿主集成 URL。随时可以用
`powercontext config show --env-file .env` 查看（凭据显示为 `<redacted>`），用
`powercontext config validate --env-file .env` 校验语法和模型配置。

#### 3. 启动 Server

```bash
powercontext server run --env-file .env
```

使用 `--env-file` 时，文件内的赋值覆盖 shell 中的同名值；文件中没有的旧 `POWERCONTEXT_SERVER_*` 变量会被忽略。
因此 `config validate` 与 `server run` 使用同一份 Server 配置。

#### 4. 验证 Server

在第二个终端执行：

```bash
set -a
. ./.env
set +a
powercontext doctor
powercontext ready
powercontext capabilities
```

确认输出包含：

```text
package: ok - powercontext <version>
server liveness: ok - http://127.0.0.1:8000 status=ok
server readiness: ok - http://127.0.0.1:8000 status=ready
Status: ready
Memory extraction: enabled
Search modes: auto, fts, vector, hybrid
```

`doctor` 全部检查为 `ok`、`Status: ready`、`Memory extraction: enabled`、四种搜索模式齐全，并且
<http://127.0.0.1:8000/> 的 Dashboard 中存在 `Quick Start`，说明完整功能已经启动。

如果 `powercontext capabilities` 只列出 `auto, fts`，Server 处于仅 FTS 的回退模式，vector 和 hybrid 搜索
不可用，因此不满足上面的完整能力检查。

### 第二部分：验证 Memory 闭环

提取发生在 Source flush 时，因此在启动 Coding Agent 前，先验证一次完整闭环。使用唯一 Source ID，保证重复执行
本指南时仍能验证本轮行为。在加载了同一环境变量的终端里执行：

```bash
SOURCE_ID="quickstart-$(date +%s)-$$"
curl -fsS -X POST http://127.0.0.1:8000/v1/sources/content \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"project:quickstart\",\"source_id\":\"${SOURCE_ID}\",\"content\":\"PowerContext quick start check: prefer small, verifiable steps.\"}"
```

Server 返回 `202`、`"status":"accepted"` 和数字 `position`；记录 Source ID 与 position。然后 flush 该 Scope，
这一步会执行 Memory 提取：

```bash
curl -X POST http://127.0.0.1:8000/v1/memory/flush \
  -H 'content-type: application/json' \
  -d '{"scope_id":"project:quickstart"}'
```

flush 响应包含 `current_cursor`，它必须大于等于 capture 响应中的 `position`。Scheduler 可能已经抢先处理该
Source；只要 cursor 已到达该 position，`status:"idle"` 也是合法结果。如果尚未到达，请再次 flush。

然后列出当前 Memory entry：

```bash
curl -fsS -X POST http://127.0.0.1:8000/v1/memory/entries/list \
  -H 'content-type: application/json' \
  -d '{"scope_id":"project:quickstart"}'
```

找到 `source_refs` 中包含本次 capture `source_id` 的 entry，并记录它的 `citation.entry_id`。这才能证明本次
Source 生成了 Memory。如果没有 entry 引用该 Source，说明提取已运行但没有产生候选项；请换一个新的 Source ID，
写入更明确、可长期保留的事实或偏好后重试。

通过向量搜索确认 Embedding 可用：

```bash
curl -X POST http://127.0.0.1:8000/v1/memory/search \
  -H 'content-type: application/json' \
  -d '{"scope_id":"project:quickstart","query":"verifiable steps","mode":"vector","limit":50}'
```

只有响应同时包含 `"mode":"vector"`，并且某个 hit 的 `citation.entry_id` 等于上一步记录的 source-linked entry，
同时其 `matched_by` 包含 `"vector"`，才能确认本轮 Embedding 可用。命中其他 entry、空 `hits` 或
`"mode":null` 都不能证明本轮闭环。此时检查 `powercontext capabilities`：
如果 `Search modes` 中没有 `vector`，Server 处于仅 FTS 的回退模式；如果存在 `vector`，则本次闭环尚未产生
可供向量搜索的 Memory。当已有 Memory 但 vector capability 不可用时，显式 vector 请求会返回 HTTP 422。

最后检查模型使用统计：

```bash
powercontext stats --scope-id project:quickstart
```

```text
Embedding: 1 requests, ...
```

Embedding 请求计数是累计值。非零计数只能辅助说明模型曾被调用；只有上面的 source-linked vector hit 能证明本轮闭环。

### 第三部分：启动 Coding Agent

Config Generator 已经打印全部受支持 Coding Agent 的 setup 和启动命令。新开一个终端，找到要使用的 Agent，复制它下面
的两行即可；第一行安装 PowerContext 集成，第二行加载刚生成的 `.env` 并启动 Agent，因此不需要再次填写 Scope ID。

Coding Agent 启动后，在项目中发送一条普通 prompt。集成会先从 `project:quickstart` 召回相关 Memory，再把本轮 prompt
保存为 Source；Scheduler 会在大约 60 秒内从新 Source 提取 Memory，因此第二部分的 flush 只需做一次用于验证闭环。

## 数据存在哪里

生成的配置不指定数据库位置，因此 Server 把数据保存在用户数据目录，而不是项目内文件。在未设置
`POWERCONTEXT_HOME` 时，SQLite 的 `powercontext.db` 与调度状态的 `scheduler.db` 位于：

- macOS：`~/Library/Application Support/powercontext/`
- Linux：`~/.local/share/powercontext/`

如需迁移，在启动 Server 前设置 `POWERCONTEXT_HOME` 即可。之后再修改数据库 URL 会把 Server 指向另一个（可能是
空的）数据库；需要旧数据时请保留原来的值。

## 停止与恢复

在 Server 终端按 `Ctrl+C` 停止进程。数据持久保存在 SQLite 中，重启不会丢失。恢复时重新加载同一个 `.env`，再次
执行 `powercontext server run --env-file .env`；pending 的 Source 会在下一次调度或 flush 时继续处理。

## 快速排障

| 现象 | 处理方式 |
| --- | --- |
| Dashboard 为空 | 对比 Dashboard 与 Agent 的完整 scope 字符串 |
| `ready` 为 `degraded` | 检查 Generation、Embedding 的模型、密钥和 Base URL |
| 没有 `vector`、`hybrid` | 同时配置 Embedding model、profile ID 和正确维度；未配置时召回保持 FTS（`auto, fts`） |
| Source 一直 pending | 启用 scheduler，或调用 `/v1/memory/flush` |
| 原有数据不见了 | 恢复之前的数据库 URL 或 `POWERCONTEXT_HOME` |

更多错误状态见[排查问题](troubleshoot.md)，完整变量见[配置参考](../reference/configuration.md)。
