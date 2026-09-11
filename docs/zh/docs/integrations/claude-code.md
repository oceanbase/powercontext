---
status: community
title: Claude Code
description: 安装 PowerContext Claude Code 插件，并配置召回、提示词采集和认证。
---

# Claude Code

`community`

## 检查前置条件

先安装 PowerContext 和 Claude Code，并确认执行 setup 的环境可以找到这两个命令：

```bash
powercontext --version
claude --version
```

Python package 和插件应使用同一个 PowerContext 仓库 ref。Hook 会校验带版本的 Prepared Context contract，
因此旧 Server 与新插件混用时，召回可能被禁用，但不会阻塞 Claude Code。

## 安装或更新插件

先按[快速开始](../get-started/quickstart.md)生成配置并启动 Server。在运行 Claude Code 的机器加载客户端文件：

```bash
set -a
. ./.env
set +a
powercontext setup claude-code --server-url "$POWERCONTEXT_CLAUDE_SERVER_URL"
```

修改 Claude Code 设置前，setup 会报告设置项、插件缓存、持久化数据位置、所需权限和准确的回滚命令。
之后命令会注册 Marketplace、以 user scope 安装插件，并通过 Claude Code 的 JSON 输出确认插件已启用。

Marketplace registry、按版本保存的插件缓存和插件数据目录由 Claude Code 管理。Claude 2.1.133 的
`plugin install` 不支持配置参数，因此 setup 会在安装成功后，把 `server_url` 和 `capture_prompts`
原子合并到用户级 `pluginConfigs`，并保留其他设置；失败时恢复安装前快照。PowerContext 从
`CLAUDE_CONFIG_DIR` 或 Claude Code 默认配置目录解析这些位置。

使用本地 checkout 时，传入目录：

```bash
powercontext setup claude-code --source ./powercontext
```

安装完成并确认 Server 正在运行后，检查安装并开启新的 Claude Code 会话：

```bash
powercontext doctor claude-code
claude
```

使用 `/hooks` 确认 `UserPromptSubmit` Hook，使用 `/mcp` 确认 `powercontext` Server。
两者分别负责采集/召回和显式工具调用，必须都连接成功。`doctor claude-code` 主要检查安装状态，
完整记忆还需完成[Source 与主题验收](../get-started/quickstart.md#4-用普通对话验收-topic-memory)。

为新 Scope 执行 `.env.next-steps.md` 中的创建请求，把返回的真实 `scope_id` 写入
`.env` 的 `POWERCONTEXT_CLAUDE_SCOPE_ID`，然后重新加载并开启新会话。
`claude-code-xxxxxxxx` 是标题，不是 ID；不同 Agent 不会仅因名称或目录不同就自动隔离。
客户端加载 `.env` 即可；该文件包含完整安装配置，只应复制到可信机器。

MCP endpoint 来自 setup 保存的 `server_url`，Hook 可被 `POWERCONTEXT_CLAUDE_SERVER_URL` 覆盖。
修改环境地址后也要用同源 setup 的 `--server-url` 更新持久配置，保证两条路径一致。
认证使用客户端文件的 `POWERCONTEXT_CLAUDE_AUTHORIZATION`，Hook 与 MCP headersHelper 都需要它。
桌面或其他启动方式可能不继承终端环境，改文件后要重启实际使用的 Claude 进程。

再次执行 setup 会更新插件配置并验证已安装版本，不会删除已有的 PowerContext Server 数据。

## 理解插件行为

对于每条用户 prompt，Hook 会：

1. 按显式、session、workspace 和默认 binding 解析当前 Scope；
2. 最多调用一次 `POST /v1/context/prepare`；
3. 严格校验 `powercontext.prepared-context.v1`，再通过 `additionalContext` 原样注入；
4. 独立地将 prompt 采集为普通 Content Source 证据。

配置 generation model 后，Source pipeline 可能进一步提取 Memory。提示词采集不会调用 `remember_memory`，
Hook 也不会把普通 prompt 标记为 `task-outcome`。

v1 不安装 `Stop` Hook，不读取 transcript，也不自动采集 Claude 的最终回复。Memory 写入和持久化 Handoff
里程碑仍然是由随附 Skill 指导的显式 MCP 操作。

scope 按以下顺序解析：

1. 显式设置的 `POWERCONTEXT_CLAUDE_SCOPE_ID`；
2. PowerContext 保存的持久 session binding；
3. PowerContext 保存的持久 workspace binding；
4. Server 的默认 Scope。

使用随附 resolver 将 checkout 绑定到一个已知 Scope：

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_scope.py" \
  --cwd "$PWD" --bind-scope "SCOPE_ID"
```

resolver 只把 workspace 路径哈希用作外部 binding key，不会根据 Git remote 或目录生成 Scope ID。只有确实需要
主动隔离或共享时，才设置显式 Scope。

## 使用显式 Memory 和 Handoff 操作

随附的 MCP Server 暴露已有的 PowerContext 操作。Claude 可以搜索和列出 Memory；只有用户明确要求持久化变更时，
才创建、修订或废弃 Memory entry。

转交任务时，随附 Skill 会引导 Claude 用 `handoff_current_work` 准备当前工作，并在“交接”等明确命令下把返回的
`handoff` 原样传给 `commit_handoff`。接收方用 `continue_handoff` 读取 exact Revision，核验后通过
`acknowledge_handoff` 回执；完成任务时用 `record_task_outcome` 关联该回执。Prepared Handoff 仍是临时载体，
exact Revision 才是跨 Agent 的持久交接点。

自动召回不依赖 Claude 是否决定调用 MCP。反过来，MCP Memory 写入也不能替代 prompt 采集：启用采集后，Hook
会把每条 prompt 保存为普通 Source 证据，之后是否从 Source 生成 Memory 由 Server 决定。

## 配置 Server 地址和提示词采集

安装时设置 endpoint：

```bash
powercontext setup claude-code \
  --server-url http://127.0.0.1:9000 \
  --no-capture-prompts
```

Claude Code 会把这些非敏感选项保存在用户级 `pluginConfigs` 中。也可以只覆盖一次 Hook 进程：

```bash
export POWERCONTEXT_CLAUDE_SERVER_URL=http://127.0.0.1:9000
export POWERCONTEXT_CLAUDE_CAPTURE_PROMPTS=false
claude
```

只有当前工作必须有意覆盖持久 binding 和 Server 默认 Scope 时，才设置 `POWERCONTEXT_CLAUDE_SCOPE_ID`。

`POWERCONTEXT_CLAUDE_FLUSH_ON_CAPTURE=true` 会让 Hook 等待 Source 处理，只适合测试，不适合日常交互。

timeout 和 flush 控制项见[环境变量](#环境变量)。这些设置作用于 Hook
进程；MCP client 仍由 Claude Code 管理。

## 连接启用认证的 Server

从 secret manager 加载 token，再启动 Server：

```bash
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

在包含匹配完整 header 的环境中启动 Claude Code：

```bash
export POWERCONTEXT_CLAUDE_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
claude
```

Hook 会直接读取该进程环境变量，MCP 配置则把它展开到 `Authorization` header。变量不存在时，MCP header
使用空值。不要把 token 放入 Server URL、插件选项、`.mcp.json`、Source metadata 或日志。

环回地址默认允许明文 HTTP。连接非环回 Server 时，使用 HTTPS，或显式设置
`POWERCONTEXT_CLAUDE_ALLOW_INSECURE_HTTP=true`。引导安装可以保存该同意，并同时配置 Hook 与原生 MCP 地址；
宿主自身的 MCP 策略仍然生效。参见[连接远程 Server](../operate/connect-remote-server.md)。

## 理解失败行为

召回与采集彼此独立，并且都会 fail open。召回失败不会阻止 prompt 采集，采集失败也不会移除有效的召回上下文。
无论哪种情况，Claude Code 都会继续处理当前 prompt。

| 条件 | Hook 行为 |
| --- | --- |
| Prepared Context 为空 | 不注入内容，并记录 `empty` outcome |
| HTTP 401 | 不注入内容，并记录 `authentication_failed` |
| HTTP 404 | 不注入内容，并记录 `version_mismatch` |
| HTTP 503 或 Server 不可用 | 不注入内容，并记录 `server_unavailable` |
| 未知 schema、错误 JSON 或超大响应 | 不注入内容，并记录 `invalid_response` |

诊断只包含 outcome 和安全的数字 metadata，不包含 prompt、scope、Prepared Context 正文、Authorization 值或
响应正文。插件会拒绝重定向，并限制响应大小和 wall-clock 时间。

## 诊断或回滚

在不连接 Server 的情况下检查 CLI 和已启用插件：

```bash
powercontext doctor claude-code
```

如果 setup 在创建新的 Marketplace 或插件项后失败，它只删除本次 setup 创建的对象；setup 前已存在的 Marketplace
或插件会保留。修正命令报告的 Claude CLI 或仓库错误后可重新执行 setup，该操作可以安全重复。

移除插件与 Marketplace：

```bash
claude plugin uninstall powercontext@powercontext --scope user
claude plugin marketplace remove powercontext
```

从最后一个 scope 卸载插件时，Claude Code 也会删除 `${CLAUDE_PLUGIN_DATA}`；除非卸载时传入
`--keep-data`。

## 环境变量

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_CLAUDE_SERVER_URL` | `http://127.0.0.1:8000` | Hook 使用的 Server base URL |
| `POWERCONTEXT_CLAUDE_ALLOW_INSECURE_HTTP` | `false` | 显式允许 PowerContext 请求使用非环回明文 HTTP |
| `POWERCONTEXT_CLAUDE_SCOPE_ID` | 未设置 | 覆盖持久 binding 和 Server 默认 Scope |
| `POWERCONTEXT_CLAUDE_AUTHORIZATION` | 未设置 | Hook 与 MCP 请求使用的完整 `Bearer <token>` header |
| `POWERCONTEXT_CLAUDE_CAPTURE_PROMPTS` | `true` | 把用户 prompt 采集为普通 Source 证据 |
| `POWERCONTEXT_CLAUDE_FLUSH_ON_CAPTURE` | `false` | 采集后等待 Source 处理 |
| `POWERCONTEXT_CLAUDE_REQUEST_TIMEOUT_SECONDS` | `1` | Hook 单次请求超时 |
| `POWERCONTEXT_CLAUDE_HTTP_BUDGET_SECONDS` | `4` | 召回、采集和可选 flush 共用的 Hook HTTP 时间预算 |
| `POWERCONTEXT_CLAUDE_FLUSH_MAX_CALLS` | `4` | 每个 prompt 最多执行的 flush 次数；有效值为 1 到 16 |

`powercontext setup claude-code` 会把 `server_url` 和 `capture_prompts` 保存为非敏感的 Claude Code 插件
选项。启动 Claude Code 的进程中，对应的 `POWERCONTEXT_CLAUDE_*` 环境变量优先级更高。
Authorization 只能来自环境变量，不能加入 Server URL 或插件选项。

`UserPromptSubmit` Hook 的外层超时为十秒。召回与采集共用一个 wall-clock 时间预算，但会独立降级。
显式允许 HTTP 不会关闭 HTTPS 证书校验。修改环境变量后需要重启 Claude Code。
