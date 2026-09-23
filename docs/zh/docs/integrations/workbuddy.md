---
status: community
title: WorkBuddy
description: 安装 PowerContext WorkBuddy hooks 并控制其本地行为。
---

# WorkBuddy

`community`

## 前置条件

- 运行中的 PowerContext Server；本地可使用 `powercontext server run` 启动。
- 已安装 PowerContext client 和 CLI，且 `PATH` 中存在 `uvx` 与 `npx`。
- 支持用户级 hooks、MCP 和 Skills 的 WorkBuddy 桌面应用。
- WorkBuddy 的 `PATH` 中可以找到已安装客户端提供的 `powercontext-hook`。

该集成不会自行启动或内嵌 Server；它只通过 HTTP 与运行中的 PowerContext Server 通信。

## 使用 PowerContext CLI 安装

WorkBuddy 支持 `setup select` 和 `doctor integrations`，也可以使用下方的独立安装和诊断命令。

CLI 可以从本地 checkout 或 GitHub 源一键安装 hooks、MCP Server 和 Skill：

```bash
powercontext setup workbuddy
```

对于本地 checkout，把 `--source` 指向仓库根目录：

```bash
powercontext setup workbuddy --source /path/to/powercontext
```

安装器会把 hook 驱动和 scope resolver 写入 `~/.workbuddy/hooks`，把 `UserPromptSubmit` hook 合并进
`~/.workbuddy/settings.json`，在 `~/.workbuddy/mcp.json` 中注册 `powercontext` server，并把
`powercontext-project-context` Skill 安装到 `~/.workbuddy/skills`。既有设置和其他 MCP server 会被保留。Skill 和 MCP 资源从 Agent Plugin 基准生成，
宿主规则从选定仓库源码加载，无需独立安装管理包。

使用以下命令验证安装：

```bash
powercontext doctor workbuddy
```

然后保持 Server 运行并重启 WorkBuddy：

```bash
powercontext server run
```

## 理解自动恢复、Memory 和 Handoff

集成通过两条路径访问同一个 Server：

- `UserPromptSubmit` Hook 在 WorkBuddy 分析提示词前请求 Runtime 准备一个最终、有界的上下文值，然后
  独立地把提示词采集为 Source 证据；
- MCP 为 WorkBuddy 提供读取和维护 Memory 的显式工具，以及明确的 Handoff 工作流。

生成的 `powercontext-project-context` Skill 遵循 Agent Plugin 基准。收到交接请求后，检查当前事实、调用
`handoff_current_work` 并返回完整的临时载体。只有明确要求持久里程碑时才调用 `commit_handoff`。
预览或设计类请求保持只读。

WorkBuddy 开始分析提示词前，Hook 只调用一次 `POST /v1/context/prepare`，请求 8000-byte 总预算。它
严格校验 `powercontext.prepared-context.v1`，并将返回内容和已解析 Scope 一起注入，供显式 MCP 调用复用。Runtime 负责把 Memory 内容标记为
不可信历史、保留精确 citation，并完成最终选择与渲染。自动注入的内容和 Handoff 都是历史信息；
WorkBuddy 在据此行动前仍应与当前代码、用户要求和系统指令核对。

Memory 用于长期保存可复用的决策、约束和状态；Handoff 用于临时移交当前任务，不能用几条 Memory
替代。概念边界见[理解 Memory 和 Handoff](../workflows/memory-and-handoff.md)。

## 控制提示词采集

默认开启提示词采集。如果当前工作不应被记录，请在重启 WorkBuddy 前关闭：

```bash
export POWERCONTEXT_WORKBUDDY_CAPTURE_PROMPTS=false
```

采集的提示词会成为 Source 证据。开启采集并不保证自动生成 Memory；后者需要配置 generation model。
显式调用 `remember_memory` 不需要模型。

仅在测试时，可以让 Hook 等待 Source 处理完成：

```bash
export POWERCONTEXT_WORKBUDDY_FLUSH_ON_CAPTURE=true
```

这会给每个提示词增加推理延迟，不适合作为日常交互配置。

## 配置

环境变量会覆盖 Hook 默认值；修改后需要重启 WorkBuddy。

| 变量 | 用途 |
| --- | --- |
| `POWERCONTEXT_WORKBUDDY_SERVER_URL` | PowerContext Server URL（默认 `http://127.0.0.1:8000`） |
| `POWERCONTEXT_WORKBUDDY_ALLOW_INSECURE_HTTP` | 显式允许 Hook 使用非环回明文 HTTP（默认 `false`） |
| `POWERCONTEXT_WORKBUDDY_AUTHORIZATION` | 完整的 Authorization header，例如 `Bearer <token>` |
| `POWERCONTEXT_WORKBUDDY_SCOPE_ID` | 显式的服务端 Scope ID |
| `POWERCONTEXT_WORKBUDDY_CAPTURE_PROMPTS` | 是否把用户提示词采集为 Source（默认 `true`） |
| `POWERCONTEXT_WORKBUDDY_FLUSH_ON_CAPTURE` | 是否等待采集的 Source 被处理（仅测试，默认 `false`） |
| `POWERCONTEXT_WORKBUDDY_REQUEST_TIMEOUT_SECONDS` | 单次 HTTP 请求超时（默认 `3.0`） |
| `POWERCONTEXT_WORKBUDDY_HTTP_BUDGET_SECONDS` | 单个提示词共享的墙钟预算（默认 `6.0`） |
| `POWERCONTEXT_WORKBUDDY_FLUSH_MAX_CALLS` | 最大 flush 调用次数（默认 `4`） |

Hook 会校验其 PowerContext MCP URL，并通过去掉末尾 `/mcp` 路径段推导 HTTP API 基地址。MCP URL
不能包含凭据、查询串或片段。环回地址默认允许明文 HTTP；非环回 HTTP 需要显式设置
`POWERCONTEXT_WORKBUDDY_ALLOW_INSECURE_HTTP=true`。setup 会配置 Hook 与原生 MCP 地址，但宿主自己的 MCP
策略仍然生效，HTTPS 证书校验也保持启用。参见[连接远程 Server](../operate/connect-remote-server.md)。

## 解析项目 scope

Server 按以下顺序为 WorkBuddy 解析 Scope：

1. 显式的 `POWERCONTEXT_WORKBUDDY_SCOPE_ID`；
2. 持久 session binding；
3. 持久 workspace binding；
4. Server 的默认 Scope。

同一工作区后续开启的新 WorkBuddy 会话会复用同一个 Scope。显式修改绑定时使用 Server 的 Scope 绑定操作。
工作区路径仅被哈希为外部绑定键，插件不会从路径派生 Scope ID。

## 连接启用鉴权的本地 Server

从本地 secret manager 加载一个 token，然后启用鉴权并启动 Server：

```bash
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

在包含匹配 Authorization header 的环境中启动 WorkBuddy：

```bash
export POWERCONTEXT_WORKBUDDY_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
```

修改该变量后需要重启 WorkBuddy。Prompt Hook 从环境读取这个值；`.mcp.json` 只保存
`${POWERCONTEXT_WORKBUDDY_AUTHORIZATION:-}` 模板，由 WorkBuddy 从同一环境展开，token 本身不会写入文件。
不要把 token 写入 `.mcp.json` 或 Server URL。

没有设置该变量或值为空，并且 Server 未启用鉴权时，插件行为与默认状态完全一致。如果 Server 已启用
鉴权，但 header 缺失或错误，Hook 会正常降级并写出 `authentication_failed` 诊断；MCP 工具不可用，但
不会阻塞 WorkBuddy 会话。

## 故障行为

| 场景 | 行为 |
| --- | --- |
| Server 不可用 | Hook 的恢复和采集正常降级，提示词继续执行且不注入上下文；MCP 工具报告服务不可用 |
| 鉴权失败 | Hook 正常降级并写出 `authentication_failed` 诊断；MCP 工具不可用 |
| 空 prepared context | 不注入任何上下文；Hook 写出 `empty` 诊断 |
| 版本不匹配 | Hook 正常降级并写出 `version_mismatch` 诊断 |
| 无效或超限响应 | Hook 正常降级并写出 `invalid_response` 诊断；不注入任何内容 |
| Hook 超时（30 秒） | WorkBuddy 继续执行；hook 进程被外层 hook 超时机制终止 |

恢复、采集和 flush 各自独立降级。Server 不可用永远不会阻塞 WorkBuddy 的正常工作。

## 诊断

正常空结果或召回失败时，Hook 会向 stderr 写一行不含正文的 JSON 诊断。outcome 包括 `empty`、
`authentication_failed`、`version_mismatch`、`server_unavailable` 和 `invalid_response`；事件不会包含
query、scope、prepared content、citation、response body 或 authorization value。

每个提示词期间应能看到 hook 的 `Syncing PowerContext` 状态消息。使用 `powercontext doctor` 验证整体
安装。

## 卸载

1. 从 `~/.workbuddy/settings.json` 删除 `UserPromptSubmit` 中的 PowerContext 条目。
2. 从 `~/.workbuddy/mcp.json` 删除 `powercontext` 条目。
3. 从 `~/.workbuddy/hooks` 删除 hook 文件和 scope resolver。
4. 删除 `~/.workbuddy/skills/powercontext-project-context`。
5. 可选：停止 Server 并删除其本地数据目录。
