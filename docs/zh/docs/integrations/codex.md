---
status: official
title: Codex
description: 安装 PowerContext Codex 插件并控制其本地行为。
---

# Codex

先安装 PowerContext 客户端，并确保宿主的 PATH 中可以找到 `powercontext-hook`。Setup 会在修改集成前检查该可执行文件。更新后，重新执行 setup 并重启宿主。

`official`

## 安装或刷新插件

先按[快速开始](../get-started/quickstart.md)安装此分支、生成配置并启动 Server。
在运行 Codex 的机器上加载客户端配置后安装匹配插件：

```bash
set -a
. ./.env
set +a
powercontext setup codex
powercontext doctor codex
```

该命令会把仓库添加为 Codex marketplace，安装 PowerContext 插件，并创建用户数据目录。重复执行是安全的。
`--ref` 应与安装 PowerContext 工具时使用的 ref 一致。

配置完成后开启新的 Codex 会话。通过 `/hooks` 查看 PowerContext `UserPromptSubmit` Hook，并在收到提示时
授予信任。

## 理解自动恢复、Memory 和 Handoff

插件通过两条路径访问同一个 Server：

- Prompt Hook 请求 Runtime 准备一个最终、有界的上下文值，然后独立地把用户提示词采集为
  Source 证据；
- MCP 为 Codex 提供读取和维护 Memory 的显式工具，以及明确的 Handoff 工作流。

## 一句话交接当前工作

在已经安装插件且 PowerContext Server 可用的 Codex 会话中，直接输入：

```text
handoff this work
```

生成的 Skill 遵循 Agent Plugin 基准：检查当前事实，调用 `handoff_current_work`，返回完整的临时载体。
只有明确要求持久里程碑时才调用 `commit_handoff`。预览直接使用当前事实，不执行写入。
概念边界见[Memory 和 Handoff](../workflows/memory-and-handoff.md)。

Session 启动时，Codex 按以下顺序解析 Scope：显式的 `POWERCONTEXT_CODEX_SCOPE_ID`、已有 Session binding、
host 管理的 workspace binding、Server 默认 Scope。解析出的 Scope 会固定到当前 Session。仓库和目录身份只用于查找
binding，不生成 Scope ID。Prompt Hook 使用该 binding 完成召回和采集；`PreToolUse` 将同一 binding 注入 data-plane
工具，Agent 输入不能把读写重定向到其他 Scope。Session 切换工作边界时，应由 host 创建或绑定另一个 Scope。

Codex 开始分析提示词前，Hook 只调用一次 `POST /v1/context/prepare`，请求 8000-byte 总预算。它严格校验
`powercontext.prepared-context.v1`，并将返回内容和已解析 Scope 一起注入。Runtime 负责把 Memory 内容标记为不可信历史、保留
精确 citation，并完成最终选择与渲染。显式搜索仍可通过 Client 和 MCP 使用，但不会成为第二次自动召回。自动注入的
内容和 Handoff 都是历史信息；Codex 在据此行动前仍应与当前代码、用户要求和系统指令核对。

Memory 用于长期保存可复用的决策、约束和状态；Handoff 用于临时移交当前任务，不能用几条 Memory 替代。概念边界见
[理解 Memory 和 Handoff](../workflows/memory-and-handoff.md)，操作步骤见[在 Codex 中交接工作](../workflows/handoff-with-codex.md)。

## 选择标准上下文文本

在启动 Codex 前，将 `POWERCONTEXT_CODEX_CONTEXT_ASSEMBLY` 设置为 JSON 组装对象，即可选择 Memory/Experience
的输出类别、顺序、条数和展示信息。完整示例与输出规则见[输出标准上下文文本](../workflows/prepare-context-text.md)。

## 控制提示词采集

默认开启提示词采集。如果当前工作不应被记录，请在启动 Codex 前关闭：

```bash
export POWERCONTEXT_CODEX_CAPTURE_PROMPTS=false
codex
```

采集的提示词会成为 Source 证据。开启采集并不保证自动生成 Memory；后者需要配置 generation model。
显式调用 `remember_memory` 不需要模型。

仅在测试时，可以让 Hook 等待 Source 处理完成：

```bash
export POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE=true
```

这会给每个提示词增加推理延迟，不适合作为日常交互配置。

## 连接启用鉴权的本地 Server

本地 Server 默认关闭认证，需要时再开启；启用 Dashboard 时需要同时开启认证。首次本地配置向导默认不启用 Dashboard。

从本地 secret manager 加载一个 token，然后启用鉴权并启动 Server：

```bash
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

在包含匹配 Authorization header 的环境中执行一次 setup：

```bash
export POWERCONTEXT_CODEX_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
powercontext setup codex
powercontext doctor codex
```

Windows PowerShell 中，通过以下方式为 setup 进程设置该值：

```powershell
$env:POWERCONTEXT_CODEX_AUTHORIZATION = "Bearer $env:POWERCONTEXT_LOCAL_TOKEN"
powercontext setup codex
powercontext doctor codex
```

setup 会把 URL 绑定的凭据保存到 `$CODEX_HOME/powercontext/credentials.json`，默认位置为
`~/.codex/powercontext/credentials.json`，并配置原生 MCP 的 `http_headers_helper` 读取同一份记录。
Linux、macOS 和 Windows 上的新 Codex 会话无需每次导出授权变量。Codex 需支持 `http_headers_helper`，
该路径已在 Codex CLI 0.153.4 上验证。helper 命令只包含本地路径；地址不匹配、存储格式错误或 POSIX 文件权限
不安全时，不会转发保存的凭据。Prompt Hook 读取同一记录，显式 `POWERCONTEXT_CODEX_AUTHORIZATION` 优先，
但不会修改保存的凭据。不要把 token 写入 `.mcp.json`、Server URL 或静态 MCP header。

Windows setup 还会维护当前用户的授权环境，以兼容 Desktop。环境变更不会传给已经运行的进程。
setup 后重启 Codex，让新进程加载更新后的插件配置。再次 setup 不提供 token 时保留凭据，提供新 token 时轮换凭据。

在 Linux 或 macOS 上可这样验证不依赖进程授权变量的新会话：

```bash
unset POWERCONTEXT_CODEX_AUTHORIZATION
powercontext doctor codex
```

doctor 应报告原生 MCP 工具发现成功。如果仍提示保存的凭据无法供宿主使用，升级 Codex，使用匹配的
PowerContext 插件重新 setup，再重启 Codex。旧版宿主可暂时从包含完整 `POWERCONTEXT_CODEX_AUTHORIZATION`
header 的进程启动。

没有保存凭据或配置进程级覆盖，并且 Server 未启用鉴权时，插件行为与默认状态完全一致。如果 Server 已启用
鉴权，但有效凭据缺失或错误，Hook 会正常降级并写出 `authentication_failed` 诊断；MCP tools 不可用，但
不会阻塞 Codex 会话。

Server 不可用时，Hook 的恢复和采集会正常降级，不会阻塞 Codex。显式 Memory 工具会报告服务不可用。

正常空结果或召回失败时，Hook 会输出不含正文的 JSON 诊断。故障 outcome 通过成功 stdout Hook 响应顶层的
`systemMessage` 返回；`empty` 仍只作为本地诊断。outcome 包括 `empty`、`authentication_failed`、
`version_mismatch`、`server_unavailable` 和 `invalid_response`；事件不会包含 query、scope、prepared content、
`citation`、response body 或 authorization value。

## 使用生成的环境文件

如果已通过向导生成配置，在执行 setup 前加载 `.env`。
它提供 URL、Authorization 和选定的 Scope，不需要把 Server `.env` 中的模型 API key 传给 Agent：

```bash
set -a
. ./.env
set +a
powercontext setup codex
```

首次规划新 Scope 时，先执行 `.env.next-steps.md` 的创建请求，把响应的真实 `scope_id` 写入客户端文件的
`POWERCONTEXT_CODEX_SCOPE_ID`，再重新加载文件并开启新会话。规划标题不是 ID。未显式绑定时可能共用 Server 默认 Scope，
切换项目目录本身不会隔离数据。
发送普通 prompt 后，插件从绑定的 Scope 召回上下文，并将 prompt 采集为 Source。Server 的 Scheduler 按配置间隔处理新 Sources。

## 核对 Hook 和 MCP 连接

Hook 的 Server 地址从已安装插件 `.mcp.json` 派生，MCP 也读取同一文件。
本机默认是 `http://127.0.0.1:8000`；自定义端口、SSH 转发或 HTTPS 时，修改该文件使两条路径使用同一地址。
该配置优先于 `POWERCONTEXT_CODEX_SERVER_URL`，不能只靠导出此环境变量改变连接地址。
`setup codex` 会更新已安装插件的 MCP URL，并添加使用本次安装绝对路径的凭据 helper。
基础配置保留以下进程环境覆盖入口：

```json
{
  "mcpServers": {
    "powercontext": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp/",
      "required": false,
      "env_http_headers": {
        "Authorization": "POWERCONTEXT_CODEX_AUTHORIZATION"
      }
    }
  }
}
```

使用 `powercontext setup codex --server-url <server-url>` 同时更新地址和 helper。手工编辑已安装文件时，
保留自动生成的 `http_headers_helper`；上面只展示基础配置。Token 不会被写死在 JSON 中。Scope 由 Hook 绑定，并注入 MCP 数据操作；不要把
规划标题或目录名当成 Scope ID。

桌面 App 不会继承已经运行的终端内部发生的环境变化。在 Windows 上，setup 会把值持久化到当前用户环境，但已
运行的 Desktop 仍需重启才能继承。运行 `powercontext doctor codex`，再分别确认 Hook 采集成功与 MCP 可用。
MCP 显示 connected 也不等于 Source 已采集。
最后完成[Source、主题演进与新会话召回验收](../get-started/quickstart.md#4-用普通对话验收-topic-memory)。

## 环境变量

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_CODEX_ALLOW_INSECURE_HTTP` | `false` | 显式允许 Hook 使用非环回明文 HTTP |
| `POWERCONTEXT_CODEX_SCOPE_ID` | 未设置 | 显式选择一个已存在 Scope，不再解析 binding 和 Server 默认 Scope |
| `POWERCONTEXT_CODEX_AUTHORIZATION` | 未设置 | 完整 `Bearer <token>` 运行时覆盖；setup 保存后供后续 Hook 和原生 MCP 连接使用 |
| `POWERCONTEXT_CODEX_CAPTURE_PROMPTS` | `true` | 把用户提示词采集为 Source 证据 |
| `POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE` | `false` | 采集后等待 Source 处理 |
| `POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS` | `3` | Hook 单次请求超时 |
| `POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS` | `6` | Hook 共享 HTTP 时间预算 |
| `POWERCONTEXT_CODEX_FLUSH_MAX_CALLS` | `4` | 每个提示词最多执行的 flush 次数 |

Hook 默认允许环回 HTTP，远程 HTTP 需要显式同意，HTTPS 证书校验仍然启用。setup 会保存同意并更新已安装插件的
`.mcp.json`，Hook 和原生 MCP 都从该文件读取地址。只修改 Hook 的 URL 环境变量不会改变原生地址；升级覆盖
`.mcp.json` 后需重新运行 setup。Codex 自身的 MCP 策略仍然生效。参见[连接远程 Server](../operate/connect-remote-server.md)。

Codex Hook 外层超时为十秒。Server 不可用或拒绝鉴权时，恢复、采集和 flush 独立降级，不会阻塞 Codex。未显式指定
Scope 时，插件依次解析 Session binding、workspace binding 和 Server 默认 Scope。进程级配置必须存在于启动
Codex 的环境中；Windows setup 会把鉴权写入用户环境，但 Desktop 仍需重启才能继承。
