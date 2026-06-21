# PowerMem connection mode templates

**Default (standard):** the plugin root `.mcp.json` matches [`http-mode.mcp.json`](http-mode.mcp.json) — REST hooks only, no PowerMem MCP in chat.

| File | Use when |
|------|----------|
| [`http-mode.mcp.json`](http-mode.mcp.json) | **HTTP mode (default)** — same as shipped `.mcp.json`. |
| [`mcp-mode.mcp.json`](mcp-mode.mcp.json) | **MCP mode** — Claude gets PowerMem tools. Edit `url` (or stdio) for your server, then copy to `.mcp.json`. |

Copy one to the plugin root as `.mcp.json`:

```bash
cp config/http-mode.mcp.json .mcp.json  # standard HTTP-only (default)
cp config/mcp-mode.mcp.json .mcp.json   # enable MCP tools
```

Or from the plugin directory:

```bash
bash scripts/apply-connection-mode.sh http   # default
bash scripts/apply-connection-mode.sh mcp
```

Restart Claude Code after changing `.mcp.json`.
