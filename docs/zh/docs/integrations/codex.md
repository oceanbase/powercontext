---
status: official
title: Codex
description: 安装 PowerContext Codex 插件并控制其本地行为。
---

# Codex

`official`

## 安装或刷新插件

执行：

```bash
powercontext setup codex --source oceanbase/powercontext --ref master
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
交接
```

`project-context` Skill 会把这句话视为创建持久交接里程碑的明确授权。Codex 在同一轮中检查当前对话和仓库，整理目标、
分支与工作区状态、改动文件、已执行检查、阻塞项、缺失项和下一步，然后在当前 Session Scope 中依次调用
`handoff_current_work` 和 `commit_handoff`。提交成功后，Codex 返回 exact Handoff Revision；用户不需要再填写交接
内容或重复确认提交。

`交接当前工作`、`把当前工作交接出去` 和 `handoff this work` 使用相同行为。若只想检查内容而不写入，请明确说
`预览交接，不要提交`；Skill 此时只在对话中渲染建议内容，不调用写工具。讨论 Handoff 设计或询问 Handoff
如何工作也不会触发持久化。

Session 启动时，Codex 按以下顺序解析 Scope：显式的 `POWERCONTEXT_CODEX_SCOPE_ID`、已有 Session binding、
host 管理的 workspace binding、Server 默认 Scope。解析出的 Scope 会固定到当前 Session。仓库和目录身份只用于查找
binding，不生成 Scope ID。Prompt Hook 使用该 binding 完成召回和采集；`PreToolUse` 将同一 binding 注入 data-plane
工具，Agent 输入不能把读写重定向到其他 Scope。Session 切换工作边界时，应由 host 创建或绑定另一个 Scope。

Codex 开始分析提示词前，Hook 只调用一次 `POST /v1/context/prepare`，请求 8000-byte 总预算。它严格校验
`powercontext.prepared-context.v1`，并原样注入返回内容。Runtime 负责把 Memory 内容标记为不可信历史、保留
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

从本地 secret manager 加载一个 token，然后启用鉴权并启动 Server：

```bash
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

在包含匹配 Authorization header 的环境中启动 Codex：

```bash
export POWERCONTEXT_CODEX_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
codex
```

修改该变量后需要重启 Codex。插件的 MCP 配置从环境读取这个可选 header，Prompt Hook 读取同一个值。不要把
token 写入 `.mcp.json`、Server URL 或静态 MCP header。

没有设置该变量或值为空，并且 Server 未启用鉴权时，插件行为与默认状态完全一致。如果 Server 已启用鉴权，
但 header 缺失或错误，Hook 会正常降级并写出 `authentication_failed` 诊断；MCP tools 不可用，但不会阻塞
Codex 会话。

Server 不可用时，Hook 的恢复和采集会正常降级，不会阻塞 Codex。显式 Memory 工具会报告服务不可用。

正常空结果或召回失败时，Hook 会输出不含正文的 JSON 诊断。故障 outcome 通过成功 stdout Hook 响应顶层的
`systemMessage` 返回；`empty` 仍只作为本地诊断。outcome 包括 `empty`、`authentication_failed`、
`version_mismatch`、`server_unavailable` 和 `invalid_response`；事件不会包含 query、scope、prepared content、
`citation`、response body 或 authorization value。

## 使用生成的环境文件

如果已按[启用提取与向量搜索](../get-started/configure-models.md)生成 `.env`，使用 Config Generator 输出的插件安装命令，
再在启动 Codex 的终端中加载该文件：

```bash
set -a
. ./.env
set +a
codex
```

普通会话无需设置 `POWERCONTEXT_CODEX_SCOPE_ID`；只有需要显式选择已存在的 Scope 时才设置。
发送普通 prompt 后，插件从绑定的 Scope 召回上下文，并将 prompt 采集为 Source。Server 的 Scheduler 按配置间隔处理新 Sources。

## 环境变量

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `POWERCONTEXT_CODEX_SCOPE_ID` | 未设置 | 显式选择一个已存在 Scope，不再解析 binding 和 Server 默认 Scope |
| `POWERCONTEXT_CODEX_AUTHORIZATION` | 未设置 | Hook 与 MCP 请求使用的完整 `Bearer <token>` header |
| `POWERCONTEXT_CODEX_CAPTURE_PROMPTS` | `true` | 把用户提示词采集为 Source 证据 |
| `POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE` | `false` | 采集后等待 Source 处理 |
| `POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS` | `1` | Hook 单次请求超时 |
| `POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS` | `4` | Hook 共享 HTTP 时间预算 |
| `POWERCONTEXT_CODEX_FLUSH_MAX_CALLS` | `4` | 每个提示词最多执行的 flush 次数 |

Codex Hook 外层超时为十秒。Server 不可用或拒绝鉴权时，恢复、采集和 flush 独立降级，不会阻塞 Codex。未显式指定
Scope 时，插件依次解析 Session binding、workspace binding 和 Server 默认 Scope。配置变量必须存在于启动 Codex 的
进程环境中；修改后需要重启 Codex。
