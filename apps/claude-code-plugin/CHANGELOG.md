# Changelog

## Unreleased

- **UserPromptSubmit:** `POWERMEM_PROMPT_SEARCH` defaults to **on** (`POST /api/v1/memories/search` + `additionalContext` per prompt). Set `0`, `false`, `no`, or `off` to disable.

## 0.1.0

Initial release of the PowerMem plugin for Claude Code.

**Connection modes**

- **HTTP mode (default):** Root `.mcp.json` ships with empty `mcpServers`; no PowerMem MCP tools in chat. Hooks always call the PowerMem REST API (`POWERMEM_BASE_URL`, default `http://localhost:8000`).
- **MCP mode (optional):** `scripts/apply-connection-mode.sh mcp` copies `config/mcp-mode.mcp.json` to `.mcp.json` so Claude can use PowerMem MCP (`search_memories`, `add_memory`, etc.) over HTTP `/mcp` or stdio.

**Skills**

- `/memory-powermem:remember` and `/memory-powermem:recall` — backed by MCP tools when MCP mode is enabled; in default HTTP mode they have no tools to invoke.

**Hooks (native `powermem-hook` + `run-hook.sh` / `run-hook.ps1`)**

- **SessionEnd:** Upload full session transcript to `POST /api/v1/memories` (detached worker so large uploads do not block exit).
- **PostCompact:** Upload compact summary to `POST /api/v1/memories`.
- **UserPromptSubmit (optional):** When `POWERMEM_PROMPT_SEARCH=1`, `POST /api/v1/memories/search` and inject hits via `additionalContext` (works in HTTP and MCP modes; off by default in this release).

**Other**

- Optional workspace file poller: `sh hooks/run-hook.sh poll` (see `watcher/README.md`).
- Windows: `hooks/hooks.windows.example.json` + PowerShell `run-hook.ps1` when `sh` is unavailable.
- Packaging: `scripts/package-plugin.sh` / `make package-claude-plugin`; hook binaries via `scripts/build-hook-binaries.sh` (Go 1.22+).
