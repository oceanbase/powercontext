---
title: Antigravity
description: 将 Antigravity 接入 PowerContext，使用按 Scope 召回、可选的 Prompt 捕获和 MCP 工具。
---

# Antigravity

安装原生 PowerContext 插件时，请使用与运行中的 Server 相同的源代码 ref。Hook 需要 Python 3.9 或更高版本。请使用运行 Antigravity 的同一用户账号执行 PowerContext CLI。

```bash
powercontext setup antigravity --server-url http://127.0.0.1:8000
powercontext doctor antigravity
```

Setup 会将插件包复制到 `~/.gemini/config/plugins/powercontext/` 并配置 Streamable HTTP MCP 端点。重启 Antigravity，并在需要时通过插件管理器启用 PowerContext。Doctor 检查本地插件包、Hook 注册和 Server 连接。[Antigravity 插件管理器](https://antigravity.google/docs/plugins?tab=cli) 会显示宿主是否已启用插件。

使用 `powercontext config agent antigravity --server-url <url>` 更改端点。如果 Server 需要 Bearer Token，请在安装或配置时通过 `POWERCONTEXT_ANTIGRAVITY_AUTHORIZATION` 提供。PowerContext 会将请求头写入当前用户的本地 MCP 配置，并在 Hook 请求访问该端点时复用。重新配置同一端点时会保留 Token。切换端点时会移除原 Token，除非你提供新的 Token。

## 自动上下文与捕获

- 在 `SessionStart` 时，插件解析并持久化对话的 Scope，并注入可用的启动上下文。
- 在 `PreInvocation` 时，插件使用最新的用户文本召回上下文。同一对话中，文本相同的连续调用会合并，直到文本发生变化。Antigravity 的 Hook 载荷提供调用序号，但没有用户轮次 ID，因此也会合并连续提交的相同文本。
- 捕获默认关闭。使用 `powercontext config agent antigravity --capture-prompts` 启用，使用 `--no-capture-prompts` 关闭。共享隐私过滤器会从召回查询中清除识别到的凭据，并跳过这些 Prompt 的捕获。

如果 PowerContext Gateway 处理宿主的模型请求，请使用 `POWERCONTEXT_ANTIGRAVITY_PROMPT_RECALL=false` 启动 Antigravity，并保持 Prompt 捕获关闭，让 Gateway 在轮次中负责这两项操作。

随附的 `powercontext-project-context` Skill 使用插件的 MCP 工具显式执行记忆和交接操作。原生事件载荷请参见 [Antigravity Hook 契约](https://antigravity.google/docs/hooks)。
