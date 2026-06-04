# OpenCode

Connect OpenCode to PowerMem through OpenCode's MCP support.

OpenCode MCP references:

- [OpenCode MCP servers](https://opencode.ai/docs/mcp-servers/)
- [OpenCode config](https://opencode.ai/docs/config/)

## Recommended setup — let your MCP client agent set it up

First download the code and enter the directory:

```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```

Then open the AI agent window in OpenCode and paste this one line:

```text
Read and follow apps/mcp-client/SETUP.md to setup PowerMem
```

The agent follows [`apps/mcp-client/SETUP.md`](https://github.com/oceanbase/powermem/blob/main/apps/mcp-client/SETUP.md), runs `powermem-mcp` directly, and configures only the current OpenCode setup.

## Prerequisites

- OpenCode installed.
- A PowerMem backend:
  - Preferred for PowerMem UI/API features: `powermem-server --host 0.0.0.0 --port 8848`
  - Preferred for OpenCode MCP tools: `powermem-mcp stdio` or `powermem-mcp streamable-http 8848`
- PowerMem configured with your LLM provider, API key, and model.

Install `powermem[server]` for the HTTP API server. Install `powermem[mcp]` for the
local MCP command, and add `seekdb` when using the default embedded seekdb
storage/embedder.

## Manual setup

Use this section only when you want to wire OpenCode by hand.

### Choose a config scope

OpenCode reads MCP config from `opencode.json`. Common locations are:

| Scope | File |
|-------|------|
| Global user config | `~/.config/opencode/opencode.json` |
| Project config | `opencode.json` |
| Project-local config | `.opencode/opencode.json` |

Use global config for a personal PowerMem setup. Use project config only when the team wants to share the MCP server definition.

### Local stdio MCP

For local use, add PowerMem under the `mcp` object:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "powermem": {
      "type": "local",
      "command": ["powermem-mcp", "stdio"],
      "enabled": true
    }
  }
}
```

If your PowerMem server requires auth, pass environment variables:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "powermem": {
      "type": "local",
      "command": ["powermem-mcp", "stdio"],
      "enabled": true,
      "environment": {
        "POWERMEM_API_KEY": "<your-api-key>"
      }
    }
  }
}
```

### Remote MCP

Start a streamable HTTP MCP endpoint:

```bash
powermem-mcp streamable-http 8848
```

Then configure OpenCode:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "powermem": {
      "type": "remote",
      "url": "http://localhost:8848/mcp",
      "enabled": true
    }
  }
}
```

If you expose PowerMem MCP behind a remote URL, replace `http://localhost:8848/mcp` with that URL and add headers if required.

## Verify

1. Restart OpenCode or reload MCP servers.
2. Confirm `powermem` is listed as an enabled MCP server.
3. Confirm tools such as `add_memory`, `search_memories`, `get_memory_by_id`, `update_memory`, `delete_memory`, `delete_all_memories`, and `list_memories` are visible.
4. Add a memory containing `PowerMem OpenCode probe: dragonfruit-zx9`.
5. Search for `dragonfruit-zx9` and confirm the result is returned.

## Troubleshooting

- If local MCP fails, run `powermem-mcp stdio` in a terminal and fix missing dependencies or `.env` values.
- If remote MCP fails, verify `powermem-mcp streamable-http 8848` is running and that `http://localhost:8848/mcp` is reachable from OpenCode.
- If tools time out, check PowerMem server logs and consider increasing the OpenCode MCP timeout.
- If auth fails, set `POWERMEM_API_KEY` for local MCP or configure remote MCP headers.

## Uninstall

Remove the `mcp.powermem` entry from the OpenCode config file you edited, then restart OpenCode or reload MCP servers. For agent-guided cleanup, follow [`apps/mcp-client/UNINSTALL.md`](https://github.com/oceanbase/powermem/blob/main/apps/mcp-client/UNINSTALL.md).
