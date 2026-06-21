# PowerMem IDE Apps

First-party setup flows that connect PowerMem to AI clients and IDEs. Every path points at the same backend — no per-client schema rewrites.

## Pick your setup path

| If you use… | Use this directory | One-line agent prompt |
|-------------|-------------------|------------------------|
| **Cursor**, **VS Code**, **Windsurf**, **GitHub Copilot**, **Qoder** | [`vscode-extension/`](vscode-extension/) | `Read and follow apps/vscode-extension/SETUP.md to setup PowerMem` |
| **Claude Desktop**, **Cline**, **OpenCode**, Roo Code, Goose, or any other MCP client | [`mcp-client/`](mcp-client/) | `Read and follow apps/mcp-client/SETUP.md to setup PowerMem` |
| **Codex CLI** | [`agent-plugin/`](agent-plugin/) | Install `memory-powermem` with `codex plugin`, then ask Codex to use the init skill. |
| **Claude Code** (hook-based plugin) | [`agent-plugin/`](agent-plugin/) | `Read and follow apps/agent-plugin/SETUP.md to set up PowerMem memory for Claude Code.` |

> **Codex CLI** has a native plugin path under `agent-plugin/`; generic
> MCP-only Codex setup can still use `mcp-client/`. **OpenCode** belongs under
> `mcp-client/`, not `vscode-extension/`. The VS Code extension flow is for
> VS Code–compatible IDEs only.

For **OpenClaw**, use the separate [`memory-powermem`](https://github.com/ob-labs/memory-powermem) plugin — see [OpenClaw integration](../docs/integrations/openclaw.md).

## Contents

| Directory | Description |
|-----------|-------------|
| **[vscode-extension](vscode-extension/)** | VS Code extension and agent-guided setup for Cursor, VS Code, Windsurf, GitHub Copilot, and Qoder. Commands: Query memories, Add selection, Quick note, Link to AI tools, Setup, Dashboard. |
| **[mcp-client](mcp-client/)** | Agent-guided setup for generic MCP clients (Claude Desktop, Cline, OpenCode, and others). Uses `powermem-mcp` directly; prefers SSE on port `8848`. |
| **[agent-plugin](agent-plugin/)** | Claude Code and Codex CLI plugin descriptors. Claude Code uses **HTTP mode by default** (REST hooks; empty `mcpServers`). Codex CLI uses skills plus explicit `codex mcp add`. Optional Claude **MCP mode** via [`config/mcp-mode.mcp.json`](agent-plugin/config/mcp-mode.mcp.json). |

## Quick start

1. Clone the repo and enter it:

   ```bash
   git clone https://github.com/oceanbase/powermem
   cd powermem
   ```

2. Copy [`.env.example`](../.env.example) to `.env` and set your LLM provider, API key, and model (minimum required credentials).

3. Open the AI agent window in your client and paste the one-line prompt from the table above.

Each setup guide is **idempotent**: re-running it reuses an existing healthy backend and updates the target client config instead of duplicating entries.

For manual installs, `powermem[server]` provides the HTTP API server and `powermem[mcp]`
provides the MCP entry point. Add `seekdb` (for example `powermem[server,seekdb]` or
`powermem[mcp,seekdb]`) when you want the default embedded seekdb storage/embedder.

## Backend strategy

All setup flows share the same backend priority:

1. **Reuse** an existing healthy HTTP API server if one is already running:
   `curl -s -m 5 http://localhost:8848/api/v1/system/health`
2. **Start** the HTTP API server when needed:
   `powermem-server --host 0.0.0.0 --port 8848`
3. **Fall back** to MCP-only only when HTTP is unavailable:
   `powermem-mcp sse 8848` (or streamable HTTP / stdio when the target client requires it)

The `mcp-client/` path uses `powermem-mcp` directly and prefers SSE on port `8848`. The `vscode-extension/` path prefers the HTTP API and links the current IDE/client first. The `agent-plugin/` path defaults to HTTP hooks for Claude Code, and provides a Codex CLI plugin that manages init/status/stop/reset skills while MCP is added explicitly with `codex mcp add`.

## Setup & uninstall guides

| Directory | Setup | Uninstall | Details |
|-----------|-------|-----------|---------|
| `vscode-extension/` | [SETUP.md](vscode-extension/SETUP.md) | [UNINSTALL.md](vscode-extension/UNINSTALL.md) | [README.md](vscode-extension/README.md) |
| `mcp-client/` | [SETUP.md](mcp-client/SETUP.md) | [UNINSTALL.md](mcp-client/UNINSTALL.md) | — |
| `agent-plugin/` | [SETUP.md](agent-plugin/SETUP.md) | [UNINSTALL.md](agent-plugin/UNINSTALL.md) | [README.md](agent-plugin/README.md) |

## Per-client manual guides

When you prefer to wire things by hand instead of using an agent:

| Client | Guide |
|--------|-------|
| VS Code | [docs/integrations/vs_code.md](../docs/integrations/vs_code.md) |
| Cursor | [docs/integrations/cursor.md](../docs/integrations/cursor.md) |
| Windsurf | [docs/integrations/windsurf.md](../docs/integrations/windsurf.md) |
| GitHub Copilot | [docs/integrations/github_copilot.md](../docs/integrations/github_copilot.md) |
| Qoder | [docs/integrations/qoder.md](../docs/integrations/qoder.md) |
| Claude Code | [docs/integrations/claude_code.md](../docs/integrations/claude_code.md) |
| Cline | [docs/integrations/cline.md](../docs/integrations/cline.md) |
| Codex | [docs/integrations/codex.md](../docs/integrations/codex.md) |
| OpenCode | [docs/integrations/opencode.md](../docs/integrations/opencode.md) |
| Any MCP client | [docs/integrations/mcp_client.md](../docs/integrations/mcp_client.md) |
| OpenClaw | [docs/integrations/openclaw.md](../docs/integrations/openclaw.md) |

Full ecosystem overview: [docs/integrations/overview.md](../docs/integrations/overview.md).
