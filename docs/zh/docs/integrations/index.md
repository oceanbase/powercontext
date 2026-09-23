---
title: 接入 Agent
description: 选择插件、MCP、Skills、Agent Host 或 Python 框架接入。
---

# 接入 Agent

先选择接入方式，再选择宿主或框架：

- **Plugins** 将宿主需要的配置、工具、Hook 或 Skill 打包。专用插件可接入宿主生命周期；通用 Agent Plugin 提供 MCP 配置和 Skill。
- **MCP** 在 Server 的 `/mcp` 暴露 Agent 工具。MCP 本身不提供自动 Prompt 采集、召回 Hook 或 Skill 安装。
- **Skills** 提供 Agent 可发现的工作指令。安装 Skill 不等于启动 Server，也不会增加宿主工具权限。
  在兼容宿主中使用[通用 Agent Plugin](agent-plugin.md)，或[导出受管理 Skill](../workflows/configure-agent-skill-targets.md)。

## Agent Host

安装指南：[Codex](codex.md)、[Claude Code](claude-code.md)、[DSH](dsh.md)、[Hermes](hermes.md)、
[OpenClaw](openclaw.md)、[OpenCode](opencode.md)、[Pi](pi.md) 和 [WorkBuddy](workbuddy.md)。
MiniMax 使用[插件分发](../../development/plugin-distribution.md)中的原生生成包。

远程地址配置见[连接远程 Server](../operate/connect-remote-server.md)：PowerContext 客户端默认允许环回 HTTP，
非环回 HTTP 需要显式同意；宿主原生 MCP 策略独立生效。

Setup 与宿主 doctor 直接加载所选集成源码中的公共 Python 规则，无需额外安装管理包。
步骤见[源码选择与更新](../../development/plugin-distribution.md#开发与分发)。需要交互选择多个宿主时，可运行：

```bash
powercontext setup select
```

选择兼容的集成源码与 ref。该选择器读取所选源码中的目标，完整支持情况见各 Agent 文档。

## Python Agent 框架

这 3 个适配器均为 `community`，使用共享的 Python 安装流程并指定应用解释器：

```bash
powercontext setup langchain --python /app/.venv/bin/python
powercontext doctor langchain --server
```

Bub、OpenDAL、MiniMax 和通用 Agent Plugin 同样使用统一的 setup 与 doctor。
安装位置和资源生成方式见[插件分发](../../development/plugin-distribution.md)。

使用 [Pydantic AI](pydantic-ai.md)、[LangChain](langchain.md) 或 [LangGraph](langgraph.md) 适配器。

## 评测集成

[Bub 仅用于评测](evaluation.md)，不计入 Agent Host 或 Python Agent 框架支持列表。

## 集成目录

原生绑定直接从[构建目录](capabilities.md)读取。已安装状态使用 `powercontext doctor integrations --json` 检查，
当前权限以宿主实际工具目录为准。

Server 和集成使用相同的发布 tag 或 commit，平台要求见[安装与运行](../get-started/install-and-run.md)。
