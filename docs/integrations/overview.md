# Ecosystem Integrations

First-party integrations that connect PowerMem to AI clients, IDEs, and agent
frameworks. Every integration points at the same backend (the HTTP API server or
the local `pmem` CLI) — there are no per-client schema rewrites.

## AI clients & IDEs

- **[Claude Code](./claude_code.md)** — Plugin (`memory-powermem`) with silent
  HTTP-mode capture via hooks and an optional MCP mode for in-chat
  `search_memories` / `add_memory` tools.
- **[VS Code](./vs_code.md)** — First-party extension with setup UI, status bar,
  query/add commands, dashboard, and AI-tool config linking.
- **[Cursor](./cursor.md)** — MCP setup through the VS Code extension, writing
  `~/.cursor/mcp.json`.
- **[Windsurf](./windsurf.md)** — MCP or HTTP context setup through
  `~/.windsurf/context/powermem.json`.
- **[GitHub Copilot](./github_copilot.md)** — Provider config through
  `~/.github/copilot/powermem.json`.
- **[Qoder](./qoder.md)** — Manual MCP setup for Qoder IDE or Qoder CLI.
- **[OpenClaw](./openclaw.md)** — `memory-powermem` plugin with local CLI mode
  and optional HTTP backend mode.
- **[Cline](./cline.md)** — Standard MCP setup for Cline.
- **[Generic MCP client](./mcp_client.md)** — Stdio, streamable HTTP, and SSE
  setup for Claude Desktop, Cline, Codex, OpenCode, Roo Code, Goose, and other MCP clients.

## Frameworks & SDKs

- **[LangChain and LangGraph](./langchain.md)** — Persistent memory patterns
  for LCEL chains and LangGraph workflows.
- **[AgentScope](./agentscope.md)** — Connect to PowerMem MCP tools from
  AgentScope workflows for long-term memory.

For FastAPI and custom LLM / embedding / storage providers, see the
**[Integrations Guide](../guides/0009-integrations.md)**.

## See also

- [Getting Started](../guides/0001-getting_started.md) — install, `.env`, first `Memory` usage
- [MCP API](../api/0004-mcp.md) — Model Context Protocol server
- [HTTP API Server](../api/0005-api_server.md) — REST endpoints used by the integrations
