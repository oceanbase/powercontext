# VS Code

Use the first-party [PowerMem VS Code extension](https://github.com/oceanbase/powermem/tree/main/apps/vscode-extension/) to connect VS Code to PowerMem, query memories from the editor, save selected text, and link other AI tools.

## Recommended setup — let VS Code agent set it up

First download the code and enter the directory:

```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```

Then open the AI agent window in VS Code and paste this one line:

```text
Read and follow apps/vscode-extension/SETUP.md to setup PowerMem
```

The agent follows [`apps/vscode-extension/SETUP.md`](https://github.com/oceanbase/powermem/blob/main/apps/vscode-extension/SETUP.md), reuses or starts the HTTP API backend first, and only falls back to MCP-only when HTTP cannot be made healthy.

## Prerequisites

- VS Code 1.104 or newer.
- A running PowerMem backend:
  - `powermem-server --host 0.0.0.0 --port 8848` for HTTP API + MCP.
  - `powermem-mcp sse` (port 8848) when you only need MCP.
- A configured PowerMem `.env` or equivalent environment variables. Set your LLM provider, API key, and model before starting the backend.

Install `powermem[server]` for the HTTP API server. Install `powermem[mcp]` for the
local MCP command, and add `seekdb` when using the default embedded seekdb
storage/embedder.

## Manual setup

Use this section only when you want to wire VS Code by hand.

### Install

From source:

```bash
cd apps/vscode-extension
npm install
npm run compile
```

Then press `F5` in VS Code to launch an Extension Development Host.

From a packaged `.vsix`:

```bash
code --install-extension powermem-vscode-*.vsix
```

### Configure

1. Open the command palette.
2. Run **PowerMem: Setup**.
3. Set **Backend URL** to `http://localhost:8848` or your remote PowerMem server.
4. Set **API key** only if the server requires `X-API-Key`.
5. Keep **Connection Mode** as `mcp` unless you need HTTP-only context integration.
6. Run **Test connection**.

The full agent-friendly setup prompt is [`apps/vscode-extension/SETUP.md`](https://github.com/oceanbase/powermem/blob/main/apps/vscode-extension/SETUP.md).

### Use

- **PowerMem: Query Memories** searches saved memories.
- **PowerMem: Add Selection to Memory** saves highlighted text.
- **PowerMem: Quick Note** saves a short note.
- **PowerMem: Dashboard** opens the extension dashboard.
- **PowerMem: Link to AI Tools** writes supported client configs for Cursor, Claude, Windsurf, and GitHub Copilot.

## Verify

1. Confirm the status bar shows **PowerMem** without a warning icon.
2. Run **PowerMem: Quick Note** and save `PowerMem VS Code probe: dragonfruit-zx9`.
3. Run **PowerMem: Query Memories** and search `dragonfruit-zx9`.
4. Confirm the probe appears in the results.

## Troubleshooting

- If the status bar is disconnected, verify `powermem.backendUrl` and the backend health endpoint.
- If search returns nothing, check the server logs and confirm the same `userId` scope is used.
- If linked clients do not detect PowerMem, rerun **PowerMem: Link to AI Tools** and reload the target client.

## Uninstall

See [`apps/vscode-extension/UNINSTALL.md`](https://github.com/oceanbase/powermem/blob/main/apps/vscode-extension/UNINSTALL.md).
