# Qoder

Connect Qoder to PowerMem through Qoder's MCP support. The PowerMem VS Code extension does not auto-write Qoder config yet, so this integration is configured manually.

Qoder MCP references:

- [Qoder IDE MCP](https://docs.qoder.com/user-guide/chat/model-context-protocol)
- [Qoder CLI MCP servers](https://docs.qoder.com/en/cli/mcp-servers)

## Recommended setup — let Qoder agent set it up

First download the code and enter the directory:

```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```

Then open the AI agent window in Qoder and paste this one line:

```text
Read and follow apps/vscode-extension/SETUP.md to setup PowerMem
```

The agent follows [`apps/vscode-extension/SETUP.md`](https://github.com/oceanbase/powermem/blob/main/apps/vscode-extension/SETUP.md), prefers a reusable `powermem-server` HTTP API backend, and configures Qoder through MCP only for the current Qoder IDE/CLI setup.

## Prerequisites

- Qoder IDE or Qoder CLI.
- A running PowerMem backend:
  - `powermem-server --host 0.0.0.0 --port 8848`
  - or `powermem-mcp sse` (default port 8848)
- PowerMem configured with your LLM provider, API key, and model.

## Manual setup

Use this section only when you want to wire Qoder by hand.

### Install

Install PowerMem with MCP support if you want local stdio MCP:

```bash
pip install "powermem[mcp,seekdb]"
```

Use `powermem[mcp]` only when your `.env` points at non-seekdb storage/embedder
providers.

Then run the installed MCP command:

```bash
powermem-mcp stdio
```

For remote MCP, start the API server:

```bash
powermem-server --host 0.0.0.0 --port 8848
```

### Configure Qoder IDE

Open Qoder MCP settings:

1. Open Qoder settings.
2. Go to **MCP** or **Connectors & MCP**.
3. Add a server named `powermem`.

For remote HTTP/SSE MCP, paste this JSON:

```json
{
  "mcpServers": {
    "powermem": {
      "type": "streamable-http",
      "url": "http://localhost:8848/mcp"
    }
  }
}
```

If your Qoder version uses `http` or SSE naming instead of `streamable-http`, use the same endpoint and select the matching HTTP/SSE transport in the UI. Qoder detects streamable HTTP endpoints in current versions.

For local stdio MCP, use:

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

If your PowerMem server requires auth, add the relevant environment variable for stdio:

```json
{
  "mcpServers": {
    "powermem": {
      "command": "powermem-mcp",
      "args": ["stdio"],
      "env": {
        "POWERMEM_API_KEY": "<your-api-key>"
      }
    }
  }
}
```

### Configure Qoder CLI

Add a user-level stdio MCP server:

```bash
qodercli mcp add -s user powermem -- powermem-mcp stdio
```

If Qoder CLI is already running, reload MCP servers:

```text
/mcp reload
```

For HTTP/SSE transport, use the Qoder IDE JSON flow above or follow Qoder CLI's `qodercli mcp add -t http` syntax for your installed version. Qoder stores MCP configuration in `~/.qoder/settings.json` for user scope, `.qoder/settings.local.json` for local project scope, or `.mcp.json` for shared project scope.

## Verify

1. In Qoder, open the MCP server list and confirm `powermem` is connected.
2. Expand the server and confirm these tools are available: `add_memory`, `search_memories`, `get_memory_by_id`, `update_memory`, `delete_memory`, `delete_all_memories`, and `list_memories`.
3. Add a memory containing `PowerMem Qoder probe: dragonfruit-zx9`.
4. Search for `dragonfruit-zx9` and confirm the result is returned.

## Troubleshooting

- If remote MCP does not connect, verify `http://localhost:8848/api/v1/system/health` is healthy and `http://localhost:8848/mcp` is reachable.
- If stdio MCP fails, verify `powermem-mcp stdio` starts from a terminal.
- If tools time out, increase Qoder's MCP request timeout or check PowerMem server logs for slow LLM extraction.
- If auth fails, set `POWERMEM_API_KEY` for stdio or configure the required HTTP header in Qoder's MCP server settings.

## Uninstall

Remove the `powermem` MCP server from Qoder settings or run the matching Qoder CLI remove command for your installed scope. Then reload Qoder or run `/mcp reload`.
