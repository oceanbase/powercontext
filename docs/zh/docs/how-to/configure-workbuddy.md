---
title: 配置 WorkBuddy
description: 安装 PowerContext WorkBuddy hooks 并控制其本地行为。
---

# 配置 WorkBuddy

## 前置条件

- 已安装并可运行的 PowerContext。从与下方插件相同的 `master` revision 安装 CLI 和本地 Server：
  `uv tool install "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"`。
  然后执行 `powercontext server run` 启动 Server。
- 支持用户级 hooks、MCP 和 Skills 的 WorkBuddy 桌面应用。
- 用于执行 hook 进程的 Python 3.11 或更新版本，且已加入 `PATH`。
- 本仓库中的插件目录：`integrations/workbuddy/plugins/powercontext`。

该集成不会自行启动或内嵌 Server；它只通过 HTTP 与运行中的 PowerContext Server 通信。

## 使用 PowerContext CLI 安装

CLI 可以从本地 checkout 或 GitHub 源一键安装 hooks、MCP Server 和 Skill：

```bash
powercontext setup workbuddy --source oceanbase/powercontext --ref master
```

对于本地 checkout，把 `--source` 指向仓库根目录或插件目录：

```bash
powercontext setup workbuddy --source /path/to/powercontext
```

安装器会把 hook 驱动和 scope resolver 写入 `~/.workbuddy/hooks`，把 `UserPromptSubmit` hook 合并进
`~/.workbuddy/settings.json`，在 `~/.workbuddy/mcp.json` 中注册 `powercontext` server，并把
`project-context` Skill 安装到 `~/.workbuddy/skills`。既有设置和其他 MCP server 会被保留，Skill 中的
命令占位符也会被自动解析。

使用以下命令验证安装：

```bash
powercontext doctor workbuddy
```

然后保持 Server 运行并重启 WorkBuddy：

```bash
powercontext server run
```

## 手动安装（备选）

你也可以手动安装插件。示例使用 `~/.workbuddy/hooks` 作为 WorkBuddy hooks 目录；请替换为你的实际路径，并在下文所有出现
`<WORKBUDDY_HOOKS_DIR>` 的地方使用同一个值。

### 1. 复制插件文件

```bash
PLUGIN=integrations/workbuddy/plugins/powercontext
WORKBUDDY_HOOKS_DIR="${WORKBUDDY_HOOKS_DIR:-$HOME/.workbuddy/hooks}"

mkdir -p "$WORKBUDDY_HOOKS_DIR"
cp "$PLUGIN"/hooks/workbuddy_powercontext_hook.py \
   "$PLUGIN"/hooks/workbuddy_settings.py \
   "$PLUGIN"/hooks/prepared_context.py \
   "$WORKBUDDY_HOOKS_DIR"/
cp "$PLUGIN/scripts/project_scope.py" \
   "$WORKBUDDY_HOOKS_DIR/powercontext_project_scope.py"
```

### 2. 注册 Hook

将下面的 `hooks` 配置合并进 `~/.workbuddy/settings.json`。把 `<POWERCONTEXT_PYTHON>` 替换为能 import
PowerContext 的 Python executable，把 `<WORKBUDDY_HOOKS_DIR>` 替换为 hooks 目录的绝对路径；命令字符串不支持环境变量展开。

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"<POWERCONTEXT_PYTHON>\" \"<WORKBUDDY_HOOKS_DIR>/workbuddy_powercontext_hook.py\"",
            "timeout": 10,
            "statusMessage": "Syncing PowerContext"
          }
        ]
      }
    ]
  }
}
```

### 3. 注册 MCP Server

将下面的 `mcpServers` 配置合并进 `~/.workbuddy/mcp.json`：

```json
{
  "mcpServers": {
    "powercontext": {
      "type": "http",
      "url": "${POWERCONTEXT_WORKBUDDY_SERVER_URL:-http://127.0.0.1:8000}/mcp",
      "headers": {
        "Authorization": "${POWERCONTEXT_WORKBUDDY_AUTHORIZATION:-}"
      },
      "description": "PowerContext agent memory & handoff MCP server (local service on port 8000)"
    }
  }
}
```

### 4. 安装 Skill

```bash
mkdir -p ~/.workbuddy/skills
cp -R integrations/workbuddy/plugins/powercontext/skills/project-context \
  ~/.workbuddy/skills/
cat > ~/.workbuddy/skills/project-context/.powercontext.json <<'EOF'
{"schema": 1, "owner": "powercontext", "integration": "workbuddy"}
EOF
```

然后把 `~/.workbuddy/skills/project-context/SKILL.md` 中的 `${POWERCONTEXT_PYTHON}` 替换为 shell-safe
的 Python executable 参数，把 `${POWERCONTEXT_PROJECT_SCOPE_SCRIPT}` 替换为 shell-safe 的完整
`<WORKBUDDY_HOOKS_DIR>/powercontext_project_scope.py` 路径。

### 5. 启动 Server、重启 WorkBuddy 并验证

```bash
powercontext server run
```

重启 WorkBuddy，使其发现新的 hook、MCP Server 和 Skill。发送任意提示词，hook 运行时会显示
`Syncing PowerContext` 状态消息。使用以下命令验证安装：

```bash
powercontext doctor
```

当 Server 可达时，MCP 工具（`search_memory` 和 Handoff 工具）会出现在 WorkBuddy 会话中。

## 理解自动恢复、Memory 和 Handoff

集成通过两条路径访问同一个 Server：

- `UserPromptSubmit` Hook 在 WorkBuddy 分析提示词前请求 Runtime 准备一个最终、有界的上下文值，然后
  独立地把提示词采集为 Source 证据；
- MCP 为 WorkBuddy 提供读取和维护 Memory 的显式工具，以及明确的 Handoff 工作流。

`project-context` Skill 把两条路径连接起来。诸如 `交接`、`交接当前工作` 或 `handoff this work`
这样的指令会被视为创建持久交接里程碑的明确授权。Skill 会检查当前对话和仓库，调用
`handoff_current_work`，然后通过 `commit_handoff` 立即提交返回的 `handoff`。预览或设计类请求保持只读。

WorkBuddy 开始分析提示词前，Hook 只调用一次 `POST /v1/context/prepare`，请求 8000-byte 总预算。它
严格校验 `powercontext.prepared-context.v1`，并原样注入返回内容。Runtime 负责把 Memory 内容标记为
不可信历史、保留精确 citation，并完成最终选择与渲染。自动注入的内容和 Handoff 都是历史信息；
WorkBuddy 在据此行动前仍应与当前代码、用户要求和系统指令核对。

Memory 用于长期保存可复用的决策、约束和状态；Handoff 用于临时移交当前任务，不能用几条 Memory
替代。概念边界见[理解 Memory 和 Handoff](../explanation/memory-and-handoff.md)。

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
| `POWERCONTEXT_WORKBUDDY_AUTHORIZATION` | 完整的 Authorization header，例如 `Bearer <token>` |
| `POWERCONTEXT_WORKBUDDY_SCOPE_ID` | 显式 scope 或 scope 模板覆盖 |
| `POWERCONTEXT_WORKBUDDY_CAPTURE_PROMPTS` | 是否把用户提示词采集为 Source（默认 `true`） |
| `POWERCONTEXT_WORKBUDDY_FLUSH_ON_CAPTURE` | 是否等待采集的 Source 被处理（仅测试，默认 `false`） |
| `POWERCONTEXT_WORKBUDDY_REQUEST_TIMEOUT_SECONDS` | 单次 HTTP 请求超时（默认 `1.0`） |
| `POWERCONTEXT_WORKBUDDY_HTTP_BUDGET_SECONDS` | 单个提示词共享的墙钟预算（默认 `4.0`） |
| `POWERCONTEXT_WORKBUDDY_FLUSH_MAX_CALLS` | 最大 flush 调用次数（默认 `4`） |

Hook 会校验其 PowerContext MCP URL，并通过去掉末尾 `/mcp` 路径段推导 HTTP API 基地址。MCP URL
不能包含凭据、查询串或片段；明文 HTTP 只允许用于 loopback 主机。

## 解析项目 scope

WorkBuddy 按以下顺序解析 scope：

1. 显式的 `POWERCONTEXT_WORKBUDDY_SCOPE_ID`；
2. 当前 Git 工作区持久绑定的 Workstream scope（存储在 Git 私有目录的
   `powercontext/codex-workspace.json` 中，与 Codex 和 Claude Code 插件共享）；
3. 规范化后的 Git remote；
4. 解析后的本地项目目录的哈希。

同一工作区后续开启的新 WorkBuddy 会话会复用同一个 scope。`project-context` Skill 的
`--bind-workstream` 操作会为选中的 Handoff 持久化 Workstream 绑定；绑定不会进入工作树或提交。

## 连接启用鉴权的本地 Server

从本地 secret manager 加载一个 token，然后启用鉴权并启动 Server：

```bash
export POWERCONTEXT_SERVER_AUTH_ENABLED=true
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
| Hook 超时（10 秒） | WorkBuddy 继续执行；hook 进程被外层 hook 超时机制终止 |

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
3. 从 `<WORKBUDDY_HOOKS_DIR>` 删除 hook 文件和 scope resolver。
4. 删除 `~/.workbuddy/skills/project-context`。
5. 可选：停止 Server 并删除其本地数据目录。
