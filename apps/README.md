# PowerMem IDE Apps

## Contents

| Directory | Description |
|-----------|--------------|
| **vscode-extension** | VS Code extension that links PowerMem to Cursor, Claude Code, Codex, Windsurf, and Copilot. Provides commands: Query memories, Add selection, Quick note, Link to AI tools, Setup, Dashboard. |
| **claude-code-plugin** | Claude Code plugin: **HTTP mode by default** (REST hooks; empty `mcpServers`). Optional **MCP mode** via `config/mcp-mode.mcp.json`. See `claude-code-plugin/README.md`. |

## Quick start

1. **Backend**: Start PowerMem (e.g. `powermem-server --port 8000` or `uvx powermem-mcp sse`).
2. **VS Code / Cursor**: Install the extension from `vscode-extension/` (Run and Debug or package as `.vsix`), set backend URL in PowerMem settings, then use **PowerMem: Link to AI tools**.
3. **Claude Code only**: `claude --plugin-dir /path/to/powermem/apps/claude-code-plugin`. **HTTP mode is default**; run `scripts/apply-connection-mode.sh mcp` for in-chat tools (see plugin README).

See each subdirectory’s `README.md` for details.
