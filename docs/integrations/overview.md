# Ecosystem Integrations

First-party integrations that connect PowerMem to AI clients, IDEs, and agent
frameworks. Every integration points at the same backend (the HTTP API server or
the local `pmem` CLI) — there are no per-client schema rewrites.

## AI clients & IDEs

- **[Claude Code](./claude_code.md)** — Plugin (`memory-powermem`) with silent
  HTTP-mode capture via hooks and an optional MCP mode for in-chat
  `search_memories` / `add_memory` tools.

## Frameworks & SDKs

For LangChain, LangGraph, FastAPI, and custom LLM / embedding / storage
providers, see the **[Integrations Guide](../guides/0009-integrations.md)**.

## See also

- [Getting Started](../guides/0001-getting_started.md) — install, `.env`, first `Memory` usage
- [MCP API](../api/0004-mcp.md) — Model Context Protocol server
- [HTTP API Server](../api/0005-api_server.md) — REST endpoints used by the integrations
