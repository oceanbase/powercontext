---
name: stop
description: Stop the PowerMem server started by the PowerMem plugin.
---

Resolve the PowerMem plugin root first, then run `sh "$PLUGIN_ROOT/scripts/stop.sh"`.
This stops only the server PID tracked in the plugin data directory. Do not kill
unrelated PowerMem processes unless the user explicitly asks.
