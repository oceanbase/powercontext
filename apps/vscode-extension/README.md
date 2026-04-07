# PowerMem for VS Code

Give Cursor, Claude Code, Codex, Windsurf, and Copilot access to [PowerMem](https://github.com/oceanbase/powermem) intelligent memory with one click.

## Features

- **One-click link**: Auto-writes MCP or HTTP config for Cursor, Claude, Codex, Windsurf, and GitHub Copilot so they can use PowerMem.
- **Query memories**: Search your PowerMem from the editor (selection or query).
- **Add to memory**: Save selection or a quick note to PowerMem.
- **Dashboard**: Quick access to query, quick note, and setup.
- **Health check**: Status bar shows connection state; reconnect from the menu.

## Requirements

- A running **PowerMem** backend:
  - **HTTP API + MCP**: `powermem-server --host 0.0.0.0 --port 8000` (default), or
  - **MCP only**: e.g. `uvx powermem-mcp sse` (port 8000) or `uvx powermem-mcp stdio`.
- PowerMem is configured (e.g. `.env` next to the server or in project root).

## Quick Start

1. Install this extension in VS Code or Cursor.
2. Start your PowerMem backend (see above).
3. Click the **PowerMem** status bar item; if disconnected, run **Setup** and set **Backend URL** (e.g. `http://localhost:8000`).
4. Once connected, choose **Link to AI tools** to write configs for Cursor, Claude, Codex, Windsurf, and Copilot.
5. Use **Query memories** or **Add selection to memory** from the command palette or status bar menu.

## Settings

| Setting | Description | Default |
|--------|-------------|---------|
| `powermem.enabled` | Enable the extension | `true` |
| `powermem.backendUrl` | PowerMem backend URL | `http://localhost:8000` |
| `powermem.apiKey` | API key (X-API-Key) if required | (empty) |
| `powermem.useMCP` | Write MCP config for AI tools; if false, write HTTP where supported | `true` |
| `powermem.mcpServerPath` | Optional path/command for local MCP (e.g. `uvx`); empty = use backendUrl/mcp | (empty) |
| `powermem.userId` | User ID for memory scope; empty = auto-generated | (empty) |
| `powermem.projectName` | Project name; empty = workspace name | (empty) |

## Commands

- **PowerMem: Status Bar Menu** – Open the main menu (link, query, add, setup, etc.).
- **PowerMem: Query Memories** – Search PowerMem (uses selection or prompts for query).
- **PowerMem: Add Selection to Memory** – Save the current selection to PowerMem.
- **PowerMem: Quick Note** – Add a one-line note to PowerMem.
- **PowerMem: Link to AI Tools** – Write MCP/HTTP config for Cursor, Claude, Codex, Windsurf, Copilot.
- **PowerMem: Setup** – Change backend URL, API key, MCP path, test connection.
- **PowerMem: Dashboard** – Open the simple dashboard panel.

## Where configs are written

- **Cursor**: `~/.cursor/mcp.json` (merged with existing `mcpServers`).
- **Claude**: `~/.claude/providers/powermem.json`.
- **Codex**: `~/.codex/context.json` (merged).
- **Windsurf**: `~/.windsurf/context/powermem.json`.
- **Copilot**: `~/.github/copilot/powermem.json`.

After linking, restart or reload the respective AI tool/IDE so it picks up the new config.

## Links

- [PowerMem](https://github.com/oceanbase/powermem)
- [PowerMem API](https://github.com/oceanbase/powermem/blob/master/docs/api/0005-api_server.md)
- [PowerMem MCP](https://github.com/oceanbase/powermem/blob/master/docs/api/0004-mcp.md)
