# PowerMem for VS Code

Give VS Code, Cursor, Windsurf, GitHub Copilot, and Qoder access to [PowerMem](https://github.com/oceanbase/powermem) intelligent memory. The recommended path is to let the current IDE's AI agent follow [`SETUP.md`](SETUP.md), which reuses or starts the right backend and configures only the current IDE/client unless you ask for a broader setup.

For Codex, OpenCode, and other generic MCP clients, use [`../mcp-client/SETUP.md`](../mcp-client/SETUP.md).

## Features

- **Agent-guided setup** — Paste one prompt in your IDE agent window and let it follow [`SETUP.md`](SETUP.md) (Section 0 automates install, MCP, settings, and verification — no F5 or manual Setup wizard).
- **Seamless mode (default)** — Linked AI tools (Cursor, Claude Code, Codex, etc.) retrieve memories via MCP automatically. No need to `@powermem` in chat.
- **Auto-capture on save** — In seamless mode, matching files are added to memory when you save (configurable glob patterns and size limits).
- **Current-client linking** — Configure the current IDE/client first; use broad linking only when you explicitly want every supported client config written.
- **Query memories** — Search PowerMem from the editor (selection or query).
- **Add to memory** — Save selection or a quick note to PowerMem.
- **@powermem chat participant** — When seamless mode is off, use `remember`, `save`, and `search` commands in VS Code chat with auto-retrieve and periodic auto-summarize.
- **Dashboard** — Quick access to query, quick note, and setup.
- **Health check** — Status bar shows connection state; reconnect from the menu.

## Requirements

- VS Code 1.104+, Cursor, Qoder, or another VS Code–compatible IDE that can use PowerMem.
- A configured PowerMem checkout or install. At minimum, set your LLM provider, API key, and model in `.env` or equivalent environment variables.
- Backend priority:
  1. Reuse an existing healthy HTTP API server if another IDE already started one:
     `curl -s -m 5 http://localhost:8848/api/v1/system/health`
  2. Otherwise start the HTTP API server:
     `powermem-server --host 0.0.0.0 --port 8848`
  3. Fall back to MCP-only only when the HTTP API cannot be made healthy:
     `powermem-mcp streamable-http 8848`, `powermem-mcp sse 8848`, or `powermem-mcp stdio`

## Recommended Setup

First download the code and enter the directory:

```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```

Copy [`.env.example`](../../.env.example) to `.env` and set your LLM credentials, then open the AI agent window in your IDE and paste:

```text
Read and follow apps/vscode-extension/SETUP.md to setup PowerMem
```

The agent-guided setup prefers `powermem-server`, reuses an existing healthy server when another IDE already started one, falls back to MCP-only only when HTTP API fails, and configures only the current IDE/client by default. After a fresh install, restart the IDE once so the extension and MCP config take effect.

## Manual Quick Start

Use this only when you want to wire the extension by hand:

1. Install this extension in VS Code, Cursor, or Qoder.
2. Reuse or start `powermem-server --host 0.0.0.0 --port 8848`.
3. Click the **PowerMem** status bar item; if disconnected, run **Setup** and set **Backend URL** to the healthy HTTP API server, usually `http://localhost:8848`.
4. Configure only the current IDE/client. Use **PowerMem: Link to AI Tools** only when you explicitly want the extension to write all supported client configs.
5. Use **Query memories** or **Add selection to memory** from the command palette or status bar menu.

For a full setup checklist, see [SETUP.md](SETUP.md). For uninstall and cleanup, see [UNINSTALL.md](UNINSTALL.md).

## Seamless Mode vs @powermem Chat

| Mode | Behavior |
|------|----------|
| **Seamless (default)** | Linked AI tools get memory retrieval via MCP. File content is auto-captured on save (unless disabled). No `@powermem` needed. |
| **Classic (`seamlessMode: false`)** | Use `@powermem` in VS Code chat with `remember`, `save`, and `search` commands. Auto-retrieve runs before each reply; auto-summarize saves every N turns. |

Toggle **Seamless mode** in PowerMem settings (`powermem.seamlessMode`).

## Settings

| Setting | Description | Default |
|--------|-------------|---------|
| `powermem.enabled` | Enable the extension | `true` |
| `powermem.backendUrl` | PowerMem HTTP API base URL (MCP mode: root for `{url}/mcp`) | `http://localhost:8848` |
| `powermem.apiKey` | API key (`X-API-Key`) if required | (empty) |
| `powermem.connectionMode` | `mcp` for AI tools, or `http` where a client needs HTTP context | `mcp` |
| `powermem.useMCP` | Deprecated; prefer `powermem.connectionMode` | `true` |
| `powermem.mcpServerPath` | Local MCP command/path; empty = remote MCP at `{backendUrl}/mcp` | (empty) |
| `powermem.userId` | User ID for memory scope; empty = auto-generated | (empty) |
| `powermem.projectName` | Project name; empty = workspace name | (empty) |
| `powermem.seamlessMode` | Zero-friction mode: linked AI + auto-capture; disable for `@powermem` chat | `true` |
| `powermem.autoCapture.onSave` | Add file content to memory on save | `true` in seamless mode |
| `powermem.autoCapture.include` | Glob patterns for auto-capture (comma-separated) | `**/*.md,**/*.txt,**/docs/**` |
| `powermem.autoCapture.maxChars` | Max characters per file on auto-capture | `8848` |
| `powermem.chat.autoSummarizeEveryNTurns` | In `@powermem` chat: auto-summarize every N turns (`0` = off) | `10` |
| `powermem.chat.autoRetrieve` | In `@powermem` chat: retrieve relevant memories before answering | `true` |

## Commands

- **PowerMem: Status Bar Menu** — Open the main menu (link, query, add, setup, reconnect, etc.).
- **PowerMem: Query Memories** — Search PowerMem (uses selection or prompts for query).
- **PowerMem: Add Selection to Memory** — Save the current selection to PowerMem.
- **PowerMem: Quick Note** — Add a one-line note to PowerMem.
- **PowerMem: Link to AI Tools** — Write MCP/HTTP config for supported clients. Use when a broad multi-client setup is desired; otherwise configure only the current IDE/client with [SETUP.md](SETUP.md).
- **PowerMem: Setup** — Change backend URL, API key, MCP path, test connection.
- **PowerMem: Dashboard** — Open the simple dashboard panel.

## Client Setup Details

The setup flow handles the current IDE/client first. Use the per-client guide when you need manual configuration details:

| Client | Details |
|--------|---------|
| VS Code | [`../../docs/integrations/vs_code.md`](../../docs/integrations/vs_code.md) |
| Cursor | [`../../docs/integrations/cursor.md`](../../docs/integrations/cursor.md) |
| Windsurf | [`../../docs/integrations/windsurf.md`](../../docs/integrations/windsurf.md) |
| GitHub Copilot | [`../../docs/integrations/github_copilot.md`](../../docs/integrations/github_copilot.md) |
| Qoder | [`../../docs/integrations/qoder.md`](../../docs/integrations/qoder.md) |

**Link to AI Tools** auto-writes configs for Cursor, Claude Code, Codex, Windsurf, and GitHub Copilot when broad linking is explicitly requested. Qoder is configured through MCP; [`SETUP.md`](SETUP.md) includes the client-specific steps. Codex and OpenCode can also use the generic MCP client flow in [`../mcp-client/SETUP.md`](../mcp-client/SETUP.md). After linking, restart or reload the respective AI tool/IDE so it picks up the new config.

## Development

From source:

```bash
cd apps/vscode-extension
npm install
npm run compile
```

Press `F5` in VS Code to launch an Extension Development Host, or package and install a `.vsix`:

```bash
code --install-extension powermem-vscode-*.vsix
```

## Links

- [PowerMem](https://github.com/oceanbase/powermem)
- [Apps overview](../README.md)
- [PowerMem API](https://github.com/oceanbase/powermem/blob/master/docs/api/0005-api_server.md)
- [PowerMem MCP](https://github.com/oceanbase/powermem/blob/master/docs/api/0004-mcp.md)
- [Ecosystem integrations](../../docs/integrations/overview.md)
