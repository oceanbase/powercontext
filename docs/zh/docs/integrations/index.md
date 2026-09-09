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

## Agent Hosts

8 个 Agent Host 按 `official`（PowerContext 项目维护）和 `community`（社区贡献）标注。
`official` 不代表宿主厂商背书。

| Agent Host | 维护归属 | 接入方式 |
| --- | --- | --- |
| [Codex](codex.md) | `official` | Prompt Hook + MCP + Skill |
| [Claude Code](claude-code.md) | `community` | Prompt Hook + MCP + Skill |
| [DeepSeek Harness](dsh.md) | `community` | HTTP plugin + pc_* + /pc |
| [Hermes](hermes.md) | `community` | MemoryProvider + /pc |
| [OpenClaw](openclaw.md) | `community` | Memory plugin + lifecycle hooks |
| [OpenCode](opencode.md) | `community` | HTTP plugin + pc_* |
| [Pi Coding Agent](pi.md) | `community` | Extension + pc_* + /pc |
| [WorkBuddy](workbuddy.md) | `community` | Prompt Hook + MCP + Skill |

所有集成都连接独立运行的 Server。安装、连接设置、认证和诊断步骤见上表中的各 Agent 文档。

需要交互选择多个宿主时，可运行：

```bash
powercontext setup select --source oceanbase/powercontext --ref master
```

使用与 Server 相同的 ref。该选择器仅列出 CLI 目录中的宿主，完整支持情况见各 Agent 文档。

## Python Agent 框架

这 3 个适配器均为 `community`，安装到应用环境中，不使用 `powercontext setup <host>`：

| 框架 | 接入方式 | 当前发布状态 |
| --- | --- | --- |
| [Pydantic AI](pydantic-ai.md) | Toolset：Memory 读写和上下文 | `experimental` |
| [LangChain](langchain.md) | Middleware：上下文和可选 Source 采集 | `master_only` |
| [LangGraph](langgraph.md) | Recall hook 和 Memory 工具 | `master_only` |

## 评测集成

[Bub 仅用于评测](evaluation.md)，不计入 Agent Host 或 Python Agent 框架支持列表。

## 能力与发布状态

[能力矩阵](capabilities.md)由 `integrations/capabilities.toml` 生成。
当前 8 个 Host 和 Bub 的能力组合标为 `master_only`；Pydantic AI 标为 `experimental`。
`released` 表示可从指定发布 tag 获取，`master_only` 表示当前实现尚未发布，`experimental` 不承诺稳定性。
这些标签与维护归属、Minimal / Recommended / Full 能力等级分别表达不同信息。

Server 和集成使用相同的发布 tag 或 commit。平台要求见[安装与运行](../get-started/install-and-run.md)：
Windows 支持为 `experimental`，这不表示所有宿主和可选后端都支持 Windows。
