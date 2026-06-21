---
name: reset
description: Reset PowerMem plugin-local data after explicit user confirmation.
---

Reset is destructive. First tell the user it will stop the plugin-managed server and delete the plugin data directory containing `.env`, runtime state, logs, pid files, and seekdb data. Only after explicit confirmation, run:

`POWERMEM_RESET_CONFIRM=delete sh "$PLUGIN_ROOT/scripts/reset.sh"`

Resolve `PLUGIN_ROOT` from `$POWERMEM_PLUGIN_ROOT`, `$CLAUDE_PLUGIN_ROOT`, or
`$CODEX_PLUGIN_ROOT` first. If none is set, find the installed plugin root under
`~/.claude/plugins` or `~/.codex/plugins`.

Do not delete project files or unrelated PowerMem servers.
