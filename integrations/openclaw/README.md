# OpenClaw integration

`plugins/memory-powercontext` contains the PowerContext memory plugin for
[OpenClaw](https://github.com/openclaw/openclaw). The plugin registers a `memory` capability backed by a running
PowerContext Server: bounded recall before each prompt, capture of eligible user prompts as Source evidence, and
explicit `powercontext_memory_*` tools for durable Memory operations.

The plugin talks HTTP only. It never starts or embeds a PowerContext Server, and an unavailable Server never blocks
normal OpenClaw work.

## Requirements

- OpenClaw 2026.8.1.2-beta.2 or newer, available on `PATH`
- Node.js 20 or newer and `pnpm` to build the plugin from source
- A running PowerContext Server (see `powercontext server run`)

## Install or refresh the plugin

Install OpenClaw, then install the plugin from the same PowerContext ref as the CLI:

```bash
powercontext setup openclaw --source oceanbase/powercontext --ref master
```

A local checkout works as well:

```bash
powercontext setup openclaw --source .
```

`setup openclaw` builds the plugin with pnpm, installs it with `openclaw plugins install --link --force`, enables it as
the `memory` plugin slot, adds the PowerContext tools to `tools.alsoAllow`, and restarts the OpenClaw gateway. It does
not start the Server.

Start the Server, then start a new OpenClaw session:

```bash
powercontext server run
openclaw
```

To change the Server endpoint or memory scope during setup:

```bash
powercontext setup openclaw --server-url http://127.0.0.1:8765 --scope-mode project
```

Run `setup openclaw` again to refresh an existing installation.

## Understand what the plugin does

Before OpenClaw builds a prompt, the plugin calls `POST /v1/context/prepare` once with an 8000-byte default budget.
Recalled content is labelled as untrusted historical evidence; current system instructions, repository guidance, and
the user's request always take precedence. The same preparation happens before explicit memory reads.

Eligible user prompts are captured separately as Content Sources with a deterministic source id, so repeated captures
are idempotent. Private sessions are never captured. The plugin never synchronizes the complete OpenClaw transcript.
Recall, capture, and boundary flushing fail open: an unavailable Server, timeout, redirect, or invalid response leaves
the prompt unchanged and never blocks ordinary work.

The plugin exposes five tools: `powercontext_memory_search`, `powercontext_memory_get`,
`powercontext_memory_store`, `powercontext_memory_revise`, and `powercontext_memory_retire`. Mutating tools
(`store`, `revise`, `retire`) are marked side-effecting in the plugin manifest.

## Memory scope

Scope mode defaults to `agent`, which derives the memory scope from the OpenClaw agent identity. Project scope is used
only when OpenClaw supplies exactly one trusted project identity for a turn. Set an explicit `--scope-mode` when the
memory must be shared across agents in the same project or isolated differently.

## Connect to an authenticated Server

Start an authenticated Server from a protected environment:

```bash
export POWERCONTEXT_SERVER_AUTH_ENABLED=true
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

The plugin reads the Bearer token from the environment variable named by the `tokenEnv` config entry, which defaults
to `POWERCONTEXT_CLIENT_API_TOKEN`. Export the matching token for the OpenClaw gateway process before starting it:

```bash
export POWERCONTEXT_CLIENT_API_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
openclaw
```

Do not put credentials in the endpoint. The plugin accepts plain HTTP only for loopback Servers; use HTTPS for any
remote Server.

## Verify the installation

```bash
powercontext doctor
powercontext doctor openclaw
```

`doctor openclaw` checks that the OpenClaw CLI is available and that `openclaw plugins list` reports the
`memory-powercontext` plugin. Restart the OpenClaw gateway after changing PowerContext configuration.

## Development

Build and test the plugin from this repository:

```bash
make openclaw-plugin-build
```

Run the plugin unit tests and the CLI tests:

```bash
pnpm --dir integrations/openclaw/plugins/memory-powercontext test
uv run pytest tests/test_openclaw_cli.py
```
