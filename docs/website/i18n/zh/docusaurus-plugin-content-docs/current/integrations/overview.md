# 生态系统集成 {#ecosystem-integrations}

将 PowerMem 连接到 AI 客户端、IDE 和 Agent 框架的一方集成。每个集成都指向相同的后端（HTTP API 服务器或本地 `pmem` CLI）——没有针对客户端的模式重写。

## AI 客户端 & IDE {#ai-clients--ides}

- **[Claude Code](./claude_code.md)** — 插件 (`memory-powermem`)，通过钩子实现静默的 HTTP 模式捕获，并提供可选的 MCP 模式，用于聊天中的 `search_memories` / `add_memory` 工具。
- **[VS Code](./vs_code.md)** — 一方扩展，包含设置界面、状态栏、查询/添加命令、仪表板以及 AI 工具配置链接。
- **[Cursor](./cursor.md)** — 通过 VS Code 扩展进行 MCP 设置，写入 `~/.cursor/mcp.json`。
- **[Windsurf](./windsurf.md)** — 通过 `~/.windsurf/context/powermem.json` 进行 MCP 或 HTTP 上下文设置。
- **[GitHub Copilot](./github_copilot.md)** — 通过 `~/.github/copilot/powermem.json` 进行提供者配置。
- **[Qoder](./qoder.md)** — 为 Qoder IDE 或 Qoder CLI 手动设置 MCP。
- **[OpenClaw](./openclaw.md)** — `memory-powermem` 插件，支持本地 CLI 模式和可选的 HTTP 后端模式。
- **[Cline](./cline.md)** — 为 Cline 标准设置 MCP。
- **[Generic MCP client](./mcp_client.md)** — 为 Claude Desktop、Cline、Codex、OpenCode、Roo Code、Goose 和其他 MCP 客户端设置 Stdio、可流式 HTTP 和 SSE。

## 框架 & SDK {#frameworks--sdks}

- **[LangChain 和 LangGraph](./langchain.md)** — 为 LCEL 链和 LangGraph 工作流提供持久记忆模式。
- **[AgentScope](./agentscope.md)** — 从 AgentScope 工作流连接到 PowerMem MCP 工具以实现长期记忆。

有关 FastAPI 和自定义 LLM / Embedding / 存储提供商的信息，请参阅 **[集成指南](../guides/0009-integrations.md)**。

## 另见 {#see-also}

- [快速开始](../guides/0001-getting_started.md) — 安装、`.env`、首次 `Memory` 使用
- [MCP API](../api/0004-mcp.md) — MCP Server
- [HTTP API 服务器](../api/0005-api_server.md) — 集成使用的 REST 端点
