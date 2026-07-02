---
description: Check whether PowerMem is configured, running, and reachable from Claude Code.
---

Run `sh "${CLAUDE_PLUGIN_ROOT}/scripts/status.sh"` when available. Report the
data directory, connection mode (hook / mcp / both / none), hook base URL if
configured, MCP URL if configured, managed server PID if any, and health
state. Do not print `.env` contents.

**CLAUDE_PLUGIN_ROOT handling:** same rule as the init skill — if
`$CLAUDE_PLUGIN_ROOT` is unset, `export` it on its own line via
`find ~/.claude/plugins/cache/powermem/memory-powermem -maxdepth 2 -name scripts -type d 2>/dev/null | head -1 | xargs dirname`,
never write `CLAUDE_PLUGIN_ROOT=... sh "$CLAUDE_PLUGIN_ROOT/..."` on one line.

**Interpreting the connection mode line:**
- `hook` — only `runtime.env` is configured; hooks call the REST API at the
  Hook base URL.
- `mcp` — only `${CLAUDE_PLUGIN_ROOT}/.mcp.json` is configured; Claude Code
  MCP client connects to the MCP URL. No local server expected.
- `both` — both files configured; hooks and MCP coexist.
- `none` — neither file is configured; user has not run `/memory-powermem:init`.

The Health line probes whichever base URL applies (hook URL if set, otherwise
the MCP URL with `/mcp` stripped). For `none` mode, status.sh reports
"no base URL configured" and skips the health check.

