# @oceanbase/openclaw-memory-powercontext

PowerContext-backed semantic memory plugin for OpenClaw. It provides bounded auto-recall, optional Source capture, and
explicit memory tools while OpenClaw remains responsible for agent identity, sessions, transcripts, and lifecycle.

## Install

From a PowerContext checkout, install and configure the plugin with the PowerContext CLI:

```bash
powercontext setup openclaw --source .
```

The command builds the plugin, installs it into OpenClaw, selects it as the active memory slot, and restarts the Gateway.
For a remote source, use `--source oceanbase/powercontext --ref master`; update a local checkout first when refreshing a
mutable ref. OpenClaw 2026.8.1-beta.2 or newer is required.

## What it provides

- `powercontext_memory_search`
- `powercontext_memory_get`
- `powercontext_memory_store`
- `powercontext_memory_revise`
- `powercontext_memory_retire`

Only private direct sessions are captured or searched. Group, channel, and incognito sessions are excluded.

## Configure

See the [OpenClaw configuration guide](../../../../docs/en/docs/how-to/configure-openclaw.md) for scope behavior,
generation-model requirements, authentication, verification, and disabling the plugin.

## Package

- Plugin id: `memory-powercontext`
- Package: `@oceanbase/openclaw-memory-powercontext`
- Minimum OpenClaw host: `2026.8.1-beta.2`
