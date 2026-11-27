---
title: OpenCode
description: 安装 PowerContext OpenCode 插件，配置自动召回、提示词采集和 Server 认证。
status: community
---

# OpenCode

`community`

## 安装或刷新插件

插件要求 OpenCode 1.18.21 或更新的 1.x 版本。插件应与 PowerContext Server、CLI 使用同一 Git ref：

```bash
powercontext setup opencode
```

该命令会全局注册原生插件，并把属于 PowerContext 的 `powercontext-project-context` Skill 安装到 OpenCode 配置目录。
如果同名 Skill 不是 PowerContext 安装的，命令会停止并保留原文件。本地 checkout 同样可以使用：

```bash
powercontext setup opencode --source .
```

启动 Server，再打开新的 OpenCode 会话：

```bash
powercontext server run
opencode
```

## 理解插件行为

每个正常用户回合，插件通过 `POST /v1/context/prepare` 获取一个有界上下文，同时通过
`POST /v1/sources/content` 独立采集符合条件的提示词。召回内容会标记为不可信历史，并在模型分发前临时
注入，不会写入 OpenCode 会话记录。OpenCode 的模型流量经过 gateway 时，请关闭提示词时召回，由 gateway 准备上下文，
见[把提示词时上下文交给 gateway](#把提示词时上下文交给-gateway)。

具名 `pc_*` 工具提供精选的 Memory、Handoff、Experience、Skill 和只读 Candidate 操作。持久化变更前
OpenCode 会要求确认；Candidate 的批准和拒绝仍由用户通过 CLI 显式执行。

这项确认对应 OpenCode 的 `powercontext` 权限，插件将它设为 `ask`。你配置的匹配规则优先，例如 `permission` 或某个
agent 的 `permission` 中的 `"*": "allow"`。每次请求以操作名作为模式，因此
`"permission": {"powercontext": {"remember_memory": "allow"}}` 会让保存 Memory 时不再询问，其他写入仍会询问。
选择 “Allow always” 只批准当前这一次调用。

在 `opencode run` 中，OpenCode 会拒绝每个权限请求并结束当前回合，因此非交互运行会停在第一次 PowerContext 写入。
请为这类运行允许它需要的操作，例如 `"permission": {"powercontext": "allow"}`，而不是批准这次运行的所有权限。

## 配置连接

启动 OpenCode 前设置环境变量：

```bash
export POWERCONTEXT_OPENCODE_BASE_URL=http://127.0.0.1:8000
export POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS=true
opencode
```

每个 OpenCode session 按显式的 `POWERCONTEXT_OPENCODE_SCOPE_ID`、持久 Session binding、workspace binding、
Server 默认 Scope 的顺序解析。解析结果会固定到 Session，resume 时继续使用同一边界。显式变量只能指向一个
已存在且由 Server 管理的 Scope。

启用可选 Bearer 鉴权时，将完整请求头写入 `POWERCONTEXT_OPENCODE_AUTHORIZATION`，不要把凭据写入 URL。
环回地址默认允许明文 HTTP；非环回地址使用 HTTPS，或显式设置 `POWERCONTEXT_OPENCODE_ALLOW_INSECURE_HTTP=true`，
HTTPS 证书校验仍然启用。安装与持久化同意见[连接远程 Server](../operate/connect-remote-server.md)。
不应采集当前提示词时，设置 `POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS=false`。
`POWERCONTEXT_OPENCODE_PROMPT_RECALL` 默认为 `true`；设为 `false` 后，插件不再为每个提示词准备和注入上下文。
关闭召回后，插件也不提供 `pc_prepare_context` 工具；显式检索仍可使用 `pc_search`。

## 把提示词时上下文交给 gateway

OpenCode 的模型请求经过 PowerContext gateway 时，gateway 为每一轮准备并注入上下文；设置 `PC_GATEWAY_SEDIMENT=on`
后，gateway 也会采集这一轮。启动 OpenCode 前，关闭插件的提示词时召回和提示词采集：

```bash
export POWERCONTEXT_OPENCODE_PROMPT_RECALL=false
export POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS=false
opencode
```

关闭召回后，插件不发送准备请求，不向用户消息添加上下文，也不提供 `pc_prepare_context` 工具。
`POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS` 为
`true` 时插件仍会采集提示词；两项都关闭时，插件不再为提示词发送任何请求。Session Scope、其他 `pc_*` 工具和 TUI
照常工作。OpenCode 直接调用模型时，gateway 不承载这些流量，请保留默认值。

你可以继续已有的 OpenCode 会话。OpenCode 会丢弃提示词时召回添加到用户消息中的上下文，并将
`pc_prepare_context` 工具结果保存在会话历史中。当重发的工具结果包含完整的 PowerContext prepared-context 块时，
gateway 会将该结果的文本替换为 `PowerContext plugin prepared context omitted by the gateway.`，并保留工具调用 ID。
请求和响应仍会经过 Core 的常规检查。

关闭召回后，提示词时上下文只来自 gateway。gateway 在 `PC_GATEWAY_DEFAULT_SCOPE` 指定的 Scope 中准备和采集，
而不是插件为 Session 或 workspace 解析的 Scope。请把它设为同一个 Scope。

OpenCode 在每个模型请求的 `x-opencode-session-id` 头中发送会话 ID。因此，通过 UDS 连接 Core 的 gateway 默认为
OpenCode 的每一轮准备并注入上下文；独立运行的 gateway 需要设置 `PC_GATEWAY_INJECTION=on`。见
[网关上下文注入](../operate/deploy-server.md#网关上下文注入)。

OpenCode 总是发送流式请求。指向 gateway `/gateway/v1` 的 OpenCode provider 请使用 `@ai-sdk/openai`，它使用
Responses 路由。gateway 以非流式方式向上游模型请求 Responses 结果，再把放行的结果以流式重放，因此在 Core 的任何
[隐私模式](../operate/inference-protection.md#配置受支持的目标)下，流式都不会阻止这一轮完成或被采集。enforcing 模式的
Core 仍会执行它的其他请求和响应规则。`@ai-sdk/openai-compatible` 使用 Chat Completions 路由。
enforcing 模式的 Core 会扣留流式 Chat Completions 响应，gateway 返回 HTTP 400。非 enforcing 模式的 Core
（包括默认的 `disabled` 模式）下，gateway 会原样放行流式响应。设置 `PC_GATEWAY_SEDIMENT=on` 后，
gateway 还会拼接终止流式响应中的助手文本，并采集已完成的回合。

在任何隐私模式下（包括 `disabled`），Core 都需要为 `PC_GATEWAY_UPSTREAM_PROFILE` 指定的 profile（未设置时为
`default`）配置目标。没有目标时，Core 以 `target_unsupported` 扣留每个请求，gateway 对两条路由上的每一轮都返回
HTTP 400，而不只是流式的 Chat Completions 轮次。gateway 响应体的 `reason_codes` 字段写明原因，可据此区分这两种情况。
见[配置受支持的目标](../operate/inference-protection.md#配置受支持的目标)。

## 验证安装

### 恢复失败的更新

如果 setup 报告无法恢复某个目标，请保留报错中列出的备份，按报错路径还原对应目标后再重试。
目标缺失且仍有备份时，setup 不会将其当成首次安装。

如果已有当前 Skill 或多个备份，先比较并整理内容，再选择要保留的版本。请保留个人文件及管理区域外的笔记；
仅为绕过检查而删除备份可能丢失这些内容。恢复后重新执行 setup，并运行下面的检查。
恢复覆盖可捕获的文件操作失败，不保证进程崩溃或并发安装时的恢复。

### 检查插件

```bash
powercontext doctor
powercontext doctor opencode
```

集成专用 doctor 会检查 OpenCode 版本、解析后的插件配置和 PowerContext 所有的 Skill。Server 状态由默认
doctor 单独检查；Server 不可用不会阻止 OpenCode 正常工作。

## 移除插件

```bash
powercontext setup opencode --remove
```

该命令删除 `plugins/powercontext-opencode.js` 及其清单 `.powercontext-opencode.json`、
PowerContext 拥有的 `skills/powercontext-project-context` Skill，以及 `tui.json` 或 `tui.jsonc` 中的
PowerContext 条目。其他条目和注释会保留。它还会删除 setup 为远程源克隆到 PowerContext 数据目录中的 checkout、
`$OPENCODE_CONFIG_DIR/powercontext/credentials.json` 中的凭据和已保存的 OpenCode 连接。
OpenCode CLI 负责报告配置目录，所以 `PATH` 中必须有它。移除会保留哪些内容见 [CLI](../develop/interfaces.md#cli)。

## 自动输入隐私

本宿主的捕获默认值、关闭方式、凭据识别局限及独立只读查询行为见[自动 prompt 隐私策略](index.md#自动-prompt-隐私策略)。识别到凭据的输入会跳过捕获，已接受的 Source 正文和事件身份不被改写。
