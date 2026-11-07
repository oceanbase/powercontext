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
`"permission": {"powercontext": {"*": "ask", "remember_memory": "allow"}}` 会让保存 Memory 时不再询问。
`opencode run` 未加 `--auto`（或旧参数 `--dangerously-skip-permissions`）时会拒绝权限请求。

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

## 把提示词时上下文交给 gateway

OpenCode 的模型请求经过 PowerContext gateway 时，gateway 为每一轮准备并注入上下文；设置 `PC_GATEWAY_SEDIMENT=on`
后，gateway 也会采集这一轮。Core 检查 gateway 转发的每个请求，拒绝不是 gateway 注入的 prepared-context 块，
因此同时带有插件上下文的一轮会失败。启动 OpenCode 前，关闭插件的提示词时召回和提示词采集：

```bash
export POWERCONTEXT_OPENCODE_PROMPT_RECALL=false
export POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS=false
opencode
```

关闭召回后，插件不发送准备请求，也不向用户消息添加上下文。`POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS` 为 `true`
时它仍会采集提示词；两项都关闭时，插件不再为提示词发送任何请求。Session Scope、`pc_*` 工具和 TUI 照常工作。
OpenCode 直接调用模型时，gateway 不承载这些流量，请保留默认值。

关闭召回后，提示词时上下文只来自 gateway。gateway 在 `PC_GATEWAY_DEFAULT_SCOPE` 指定的 Scope 中准备和采集，
而不是插件为 Session 或 workspace 解析的 Scope。请把它设为同一个 Scope。

OpenCode 总是发送流式请求，而 gateway 只在 Responses 路由上完成流式的一轮。指向 gateway `/gateway/v1` 的 OpenCode
provider 请使用 `@ai-sdk/openai`。在 Chat Completions 路由（`@ai-sdk/openai-compatible`）上，Core 会扣留流式响应，
这一轮会失败。

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

## 自动输入隐私

本宿主的捕获默认值、关闭方式、凭据识别局限及独立只读查询行为见[自动 prompt 隐私策略](index.md#自动-prompt-隐私策略)。识别到凭据的输入会跳过捕获，已接受的 Source 正文和事件身份不被改写。
