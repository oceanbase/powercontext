# Codex

Connect Codex to PowerMem through MCP. The recommended setup path is the generic [PowerMem MCP client setup](https://github.com/oceanbase/powermem/blob/main/apps/mcp-client/SETUP.md).

## Recommended setup — let your MCP client agent set it up

First download the code and enter the directory:

```bash
git clone https://github.com/oceanbase/powermem
cd powermem
```

Then open the AI agent window where you run Codex and paste this one line:

```text
Read and follow apps/mcp-client/SETUP.md to setup PowerMem
```

The agent follows [`apps/mcp-client/SETUP.md`](https://github.com/oceanbase/powermem/blob/main/apps/mcp-client/SETUP.md), runs `powermem-mcp` directly, and updates only the Codex MCP configuration.

## Prerequisites

- Codex installed and able to read `~/.codex/context.json`.
- A running PowerMem MCP endpoint or local `powermem-mcp` command.
- PowerMem configured with your LLM provider, API key, and model.

## Manual setup

Use this section only when you want to wire Codex by hand.

### Configure

Add PowerMem to `~/.codex/context.json`:

```json
{
  "mcpServers": {
    "powermem": {
      "url": "http://localhost:8848/mcp"
    }
  }
}
```

If the PowerMem MCP endpoint requires auth, add the matching header or pass
`POWERMEM_API_KEY` to a stdio MCP command.

## Verify

1. Restart Codex so it reloads `~/.codex/context.json`.
2. Confirm the `powermem` MCP server is listed.
3. Add a probe memory with content `PowerMem Codex probe: dragonfruit-zx9`.
4. Search for `dragonfruit-zx9` and confirm Codex receives the result.

## Troubleshooting

- If Codex ignores the config, validate that `~/.codex/context.json` is valid JSON.
- If MCP fails, confirm `http://localhost:8848/mcp` is reachable or switch to stdio MCP.

## Uninstall

Remove `mcpServers.powermem` from `~/.codex/context.json`. Leave other providers untouched. For agent-guided cleanup, follow [`apps/mcp-client/UNINSTALL.md`](https://github.com/oceanbase/powermem/blob/main/apps/mcp-client/UNINSTALL.md).
