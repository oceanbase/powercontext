---
name: status
description: Check whether PowerMem is configured, running, and reachable from Claude Code or Codex CLI.
---

Resolve the PowerMem plugin root first, then run `sh "$PLUGIN_ROOT/scripts/status.sh"`.
Use `$POWERMEM_PLUGIN_ROOT`, `$CLAUDE_PLUGIN_ROOT`, or `$CODEX_PLUGIN_ROOT` when
available; otherwise find the installed plugin root under `~/.claude/plugins` or
`~/.codex/plugins`. Report the data directory, base URL, managed server PID if
any, and health state. Do not print `.env` contents.
