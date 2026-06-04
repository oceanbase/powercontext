# Generic MCP Client

Use this guide for Claude Desktop, Cline, Codex, OpenCode, Roo Code, Goose, or any other MCP client that accepts a standard MCP server definition.

## Prerequisites

- PowerMem installed with MCP support. Use `powermem[mcp,seekdb]` for zero-config
  local seekdb, or `powermem[mcp]` when your `.env` points at non-seekdb
  storage/embedder providers.
- PowerMem configured with your LLM provider, API key, and model.

## Start an MCP server

Choose one transport.

### Stdio

Use stdio when the client can launch a local command:

```bash
powermem-mcp stdio
```

Client config shape:

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

### Streamable HTTP

Use streamable HTTP for remote or long-running MCP:

```bash
powermem-mcp streamable-http 8848
```

Client config shape:

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```

### SSE

Use SSE when the client specifically expects SSE:

```bash
powermem-mcp sse 8848
```

Use the same URL shape:

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```

OpenCode stores the same server under `mcp.powermem` instead of
`mcpServers.powermem`; see [OpenCode](./opencode.md) for the exact JSON shape.

## Exposed tools

PowerMem exposes core memory tools:

- `add_memory`
- `search_memories`
- `get_memory_by_id`
- `update_memory`
- `delete_memory`
- `delete_all_memories`
- `list_memories`

It also exposes user-profile tools when supported by the client.

## Verify

1. Reload the MCP client.
2. Confirm the `powermem` server is connected.
3. Confirm the tools above are listed.
4. Add and search a probe memory containing `dragonfruit-zx9`.

## Troubleshooting

- If stdio fails, confirm `powermem-mcp stdio` starts from the same environment
  where you installed PowerMem.
- If remote MCP fails, confirm the URL and port match the transport you started.
- If auth is enabled, pass `POWERMEM_API_KEY` through env for stdio or configure headers for remote MCP.
- If memory operations fail, check `.env`, LLM provider, embedding provider, and server logs.

## Uninstall

Remove only the `powermem` MCP server entry from your client config, then reload the client.
