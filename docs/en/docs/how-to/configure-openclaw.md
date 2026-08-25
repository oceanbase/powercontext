---
title: Configure OpenClaw
description: Install the PowerContext memory plugin for OpenClaw and control recall, capture, scope, and durable memory writes.
---

# Configure OpenClaw

## Install or refresh the plugin

Until a PowerContext release includes OpenClaw, install the CLI and plugin from the same `master` revision:

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext setup openclaw --source oceanbase/powercontext --ref master
```

Without `--server-url`, setup configures the plugin for the Server default at `http://127.0.0.1:8000`.

A local checkout works as well:

```bash
powercontext setup openclaw --source .
```

`setup openclaw` builds the plugin with pnpm, installs it with `openclaw plugins install --link --force`, enables it
as the `memory` plugin slot, adds the PowerContext tools to `tools.alsoAllow`, and restarts the OpenClaw gateway. It
does not start the Server. Start the Server, then start a new OpenClaw session:

```bash
powercontext server run
openclaw
```

The plugin requires OpenClaw 2026.8.1-beta.2 or newer.

## Understand what the plugin does

Before OpenClaw builds a prompt, the plugin calls `POST /v1/context/prepare` once with an 8000-byte default budget.
Recalled content is labelled as untrusted historical evidence. Current system instructions, repository guidance, and
the user's request take precedence.

Eligible user prompts from direct/private sessions are captured separately as Content Sources with a deterministic
source id, so repeated captures are idempotent. Group, channel, and incognito sessions are excluded. The plugin never
synchronizes the complete OpenClaw transcript. Recall, capture, and boundary flushing fail open: an unavailable
Server, timeout, redirect, or invalid response leaves the prompt unchanged and never blocks ordinary work.

The plugin exposes five tools: `powercontext_memory_search`, `powercontext_memory_get`,
`powercontext_memory_store`, `powercontext_memory_revise`, and `powercontext_memory_retire`. The mutating tools
require the model to call them explicitly; OpenClaw controls side-effecting tool execution.

Explicit search and get calls use `/v1/memory/search` and `/v1/memory/entries/get` directly; they do not call
`/v1/context/prepare`. Search limits the query to 8192 characters and clamps the requested result limit to 1–50
(default 10), while each get returns at most 120 lines and 12,000 characters.

## Choose the memory scope

Scope mode defaults to `agent`, which derives the memory scope from the OpenClaw agent identity. Use project scope
when the memory must be shared across agents working in the same project:

```bash
powercontext setup openclaw --scope-mode project
```

Project scope is used only when OpenClaw supplies exactly one trusted project identity for a turn.

## Connect to an authenticated Server

Start an authenticated Server from a protected environment:

```bash
export POWERCONTEXT_SERVER_AUTH_ENABLED=true
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

The plugin reads the Bearer token from the environment variable named by the `tokenEnv` config entry, which defaults
to `POWERCONTEXT_CLIENT_API_TOKEN`. The Gateway service must receive that variable in its own environment. Add the
matching secret value to the Gateway service environment or to `~/.openclaw/.env`:

```dotenv
POWERCONTEXT_CLIENT_API_TOKEN=<same token value>
```

Protect the file and restart the Gateway so the plugin receives the updated environment:

```bash
chmod 600 ~/.openclaw/.env
openclaw gateway restart
```

Do not put credentials in the endpoint. The current configuration accepts both HTTP and HTTPS URLs; use plain HTTP
only for a trusted loopback Server and use HTTPS for every remote Server. This is an operator security requirement,
not a restriction currently enforced by the CLI or plugin.

## Verify the installation

```bash
powercontext doctor
powercontext doctor openclaw
```

`doctor openclaw` checks that the OpenClaw CLI is available and that `openclaw plugins list --enabled --json` reports
`memory-powercontext` as loaded and selected for the memory slot. Restart the OpenClaw gateway after changing
PowerContext configuration.
