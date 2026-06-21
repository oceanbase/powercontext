# Codex CLI

Connect Codex CLI to PowerMem with the native `memory-powermem` plugin plus an
explicit MCP server entry.

The Codex plugin installs PowerMem skills and reuses the same backend bootstrap
as the Claude Code plugin. It intentionally does **not** register Codex hooks or
write a hard-coded MCP URL during plugin install. After init writes
`~/.powermem/runtime.env`, add the MCP endpoint with `codex mcp add`.

## Recommended Setup

Install the PowerMem marketplace and plugin:

```bash
codex plugin marketplace add oceanbase/powermem
codex plugin add memory-powermem@powermem
```

For branch testing from a fork:

```bash
codex plugin remove memory-powermem 2>/dev/null || true
codex plugin marketplace remove powermem 2>/dev/null || true
codex plugin marketplace add https://github.com/<owner>/powermem.git --ref <branch>
codex plugin add memory-powermem@powermem
```

Start a new Codex thread after installing or updating the plugin so Codex loads
the new skills. Then ask Codex:

```text
Use the memory-powermem init skill to initialize PowerMem.
```

The init skill prepares shared local state under `~/.powermem/`:

```text
~/.powermem/.env
~/.powermem/runtime.env
~/.powermem/powermem.pid
server log under the local state directory
~/.powermem/venv/
```

After init succeeds, wire Codex MCP to the managed server:

```bash
. "$HOME/.powermem/runtime.env"
codex mcp remove powermem 2>/dev/null || true
codex mcp add powermem --url "${POWERMEM_BASE_URL%/}/mcp"
```

## Prerequisites

- Codex CLI with `codex plugin` and `codex mcp` commands.
- A supported Python runtime for the PowerMem backend. The init script creates
  `~/.powermem/venv` and installs `powermem` there.
- Anthropic credentials available from the environment or `~/.claude/settings.json`.
  `ANTHROPIC_API_KEY` works by itself. `ANTHROPIC_AUTH_TOKEN` must be paired with
  `ANTHROPIC_BASE_URL`.

## Manual MCP Only

If you do not want the Codex plugin skills, you can still connect Codex as a
plain MCP client:

```bash
codex mcp add powermem --url http://localhost:8848/mcp
```

Use this only when a PowerMem HTTP server is already running. The native plugin
path is preferred because the `init`, `status`, `stop`, and `reset` skills manage
the local server lifecycle for you.

## Verify

1. Confirm the managed server is healthy:

   ```bash
   . "$HOME/.powermem/runtime.env"
   curl -fsS "${POWERMEM_BASE_URL%/}/api/v1/system/health"
   ```

2. Confirm Codex has the MCP entry:

   ```bash
   codex mcp list
   ```

3. In Codex, ask it to remember a probe such as
   `PowerMem Codex probe: dragonfruit-zx9`, then search for `dragonfruit-zx9`.

## Troubleshooting

- If the plugin skills do not appear, start a new Codex thread after
  `codex plugin add`.
- If MCP uses the wrong port, reload `~/.powermem/runtime.env` and re-run
  `codex mcp remove powermem` followed by `codex mcp add`.
- If the server fails to start, read the server log under the local state directory.
- If package installation is slow in CN networks, init detects the current
  machine region and adds the Tsinghua PyPI mirror for `pip install`.

## Uninstall

Remove the MCP entry and plugin:

```bash
codex mcp remove powermem
codex plugin remove memory-powermem 2>/dev/null || true
```

If your Codex CLI does not expose `plugin remove`, use `codex plugin list` to
inspect the installed plugin and remove it through the CLI version's supported
plugin management command.
