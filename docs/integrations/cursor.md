# Cursor

Connect Cursor to PowerMem through MCP. The recommended path is the [PowerMem VS Code extension](https://github.com/oceanbase/powermem/tree/main/apps/vscode-extension/), which runs in Cursor and writes `~/.cursor/mcp.json` for you.

## Recommended setup — let Cursor agent set it up

First download the code and enter the directory:

```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```

Then open the AI agent window in Cursor and paste this one line:

```text
Read and follow apps/vscode-extension/SETUP.md to setup PowerMem
```

The agent follows [`apps/vscode-extension/SETUP.md`](https://github.com/oceanbase/powermem/blob/main/apps/vscode-extension/SETUP.md), reuses or starts `powermem-server` first, and configures Cursor's MCP entry only for the current IDE.

## Prerequisites

- Cursor with MCP support enabled.
- A running PowerMem backend:
  - `powermem-server --host 0.0.0.0 --port 8848`
  - or `powermem-mcp sse` (default port 8848; same as `powermem-mcp sse 8848`)
- PowerMem configured with your LLM provider, API key, and model.

Install `powermem[server]` for the HTTP API server. Install `powermem[mcp]` for the
local MCP command, and add `seekdb` when using the default embedded seekdb
storage/embedder.

## Manual setup

Use this section only when you want to wire Cursor by hand.

### Install

Install the PowerMem VS Code extension in Cursor:

1. Open Cursor Extensions.
2. Install the packaged PowerMem `.vsix`, or run the extension from source.
3. Run **PowerMem: Setup** and set the backend URL.

See [`apps/vscode-extension/SETUP.md`](https://github.com/oceanbase/powermem/blob/main/apps/vscode-extension/SETUP.md) for the full setup checklist.

### Configure

Run **PowerMem: Link to AI Tools**. The extension merges this entry into `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```

If you prefer stdio MCP, set **MCP server path** in **PowerMem: Setup** and rerun **Link to AI Tools**. The extension writes:

```json
{
  "mcpServers": {
    "powermem": {
      "command": "powermem-mcp",
      "args": ["stdio"]
    }
  }
}
```

## Verify

1. Reload Cursor.
2. Open Cursor MCP settings and confirm `powermem` is listed.
3. Ask Cursor to list available MCP tools; PowerMem should expose `add_memory`, `search_memories`, `get_memory_by_id`, `update_memory`, `delete_memory`, `delete_all_memories`, and `list_memories`.
4. Run a small round trip: add a memory containing `dragonfruit-zx9`, then search for `dragonfruit-zx9`.

## Troubleshooting

- If Cursor cannot connect, confirm `http://localhost:8848/api/v1/system/health` is healthy.
- If `~/.cursor/mcp.json` already exists, the extension merges `mcpServers.powermem` and keeps other servers.
- If stdio mode fails, confirm `powermem-mcp stdio` runs in a terminal.

## Uninstall

Remove only the `mcpServers.powermem` entry from `~/.cursor/mcp.json`, then reload Cursor. To remove the extension itself, follow [`apps/vscode-extension/UNINSTALL.md`](https://github.com/oceanbase/powermem/blob/main/apps/vscode-extension/UNINSTALL.md).
