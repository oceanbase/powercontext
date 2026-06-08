---
description: Reset PowerMem plugin-local data after explicit user confirmation.
---

Reset is destructive. First tell the user it will stop the plugin-managed server and delete the plugin data directory containing `.env`, runtime state, logs, and venv. Only after explicit confirmation, run:

`POWERMEM_RESET_CONFIRM=delete sh "${CLAUDE_PLUGIN_ROOT}/scripts/reset.sh"`

Do not delete project files or unrelated PowerMem servers.
