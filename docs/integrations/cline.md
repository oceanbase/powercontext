# Cline

Connect [Cline](https://github.com/cline/cline) to PowerMem through MCP.

## Prerequisites

- Cline installed in VS Code or a compatible editor.
- PowerMem configured with your LLM provider, API key, and model.
- A PowerMem MCP endpoint:
  - Local stdio: `powermem-mcp stdio`
  - Remote MCP: `powermem-mcp streamable-http 8848` or `powermem-mcp sse 8848`

## Recommended setup

For Cline, use the generic MCP client setup:

```text
Read and follow apps/mcp-client/SETUP.md to setup PowerMem
```

When Cline is the target client, configure only Cline's MCP server entry.

## Manual setup

Add a PowerMem MCP server in Cline's MCP settings.

For local stdio MCP:

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

For remote MCP:

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```

If your PowerMem server requires auth, pass `POWERMEM_API_KEY` in the stdio environment or configure the required remote headers in Cline.

## Verify

1. Reload Cline MCP servers.
2. Confirm `powermem` is connected.
3. Confirm tools such as `add_memory`, `search_memories`, `get_memory_by_id`, `update_memory`, `delete_memory`, `delete_all_memories`, and `list_memories` are visible.
4. Add and search a probe memory containing `dragonfruit-zx9`.

## Troubleshooting

- If stdio MCP fails, run `powermem-mcp stdio` in a terminal.
- If remote MCP fails, confirm `http://localhost:8848/mcp` is reachable from Cline.
- If tools time out, check PowerMem logs and your LLM/embedding configuration.

## Uninstall

Remove only the `mcpServers.powermem` entry from Cline's MCP settings, then reload Cline.
