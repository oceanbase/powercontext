---
title: Configure OpenClaw
description: Install and operate the PowerContext memory provider for OpenClaw.
---

# Configure OpenClaw

PowerContext integrates with OpenClaw as its exclusive memory provider. OpenClaw keeps ownership of agent and session
identity, transcripts, and lifecycle hooks, while the plugin uses PowerContext Server for scoped recall and durable
Memory.

## Install and configure the plugin

Install OpenClaw 2026.8.1-beta.2 or newer and `pnpm`, then run setup from a PowerContext CLI that includes the OpenClaw
integration:

```bash
powercontext setup openclaw \
  --source oceanbase/powercontext \
  --ref master \
  --server-url http://127.0.0.1:8000
```

A local checkout works as well:

```bash
powercontext setup openclaw --source . --server-url http://127.0.0.1:8000
```

Setup builds and links the plugin, selects it as OpenClaw's memory slot, enables automatic recall and capture, adds its
tools to `tools.alsoAllow`, and restarts the Gateway. Run setup again to refresh an existing installation. It does not
start PowerContext Server.

Start the Server in one terminal:

```bash
powercontext server run
```

Then begin a new OpenClaw session in another terminal:

```bash
openclaw tui
```

## Choose the memory scope

The default `agent` scope isolates Memory by OpenClaw agent. Keep it unless agents must deliberately share project
Memory. To use a trusted project identity when OpenClaw provides exactly one for the turn, reinstall with:

```bash
powercontext setup openclaw \
  --source oceanbase/powercontext \
  --ref master \
  --server-url http://127.0.0.1:8000 \
  --scope-mode project
```

Group, channel, and incognito sessions are not captured or searched. Recall and capture fail open, so an unavailable
PowerContext Server does not block an ordinary OpenClaw turn.

## Use the memory tools

The plugin registers `powercontext_memory_search` and `powercontext_memory_get` for reads. Explicit durable changes use
`powercontext_memory_store`, `powercontext_memory_revise`, and `powercontext_memory_retire`. Automatic recall and source
capture are enabled by setup.

## Connect to an authenticated Server

When Server authentication is enabled, set `POWERCONTEXT_CLIENT_API_TOKEN` in the environment of the OpenClaw Gateway.
Its value must match the Server token. The plugin reads the token at request time and sends it as a Bearer credential;
do not put credentials in `--server-url`. Use HTTPS for a Server reached over an untrusted network.

## Verify or disable the integration

Confirm that OpenClaw loaded the plugin and selected its memory slot:

```bash
openclaw plugins list
openclaw config get plugins.slots.memory
openclaw config get plugins.entries.memory-powercontext.config.endpoint
```

To restore OpenClaw's built-in file memory:

```bash
openclaw config set plugins.slots.memory memory-core
openclaw config set plugins.entries.memory-powercontext.enabled false
openclaw gateway restart
```

The linked plugin can then be removed with `openclaw plugins uninstall memory-powercontext`.
