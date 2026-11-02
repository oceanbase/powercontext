---
title: Manually start PowerContext
description: Configure and start the PowerContext Server manually without the setup wizard, connect Codex, and verify Memory.
---

# Manually start PowerContext

This page explains how to configure and start the PowerContext Server without using the powercontext config init setup wizard, then bind Codex to a Scope.

This guide applies to macOS and Linux only. It uses POSIX shell syntax such as `source`, `env -i`, and here-documents; Windows PowerShell is not covered by this guide.

Use this page if you want to understand each step, troubleshoot configuration, or control the Server address, port, Dashboard, and authentication yourself. If you prefer the interactive setup wizard, see [Quick Start](quickstart.md).

## Manual startup versus Quick Start

Quick Start uses the setup wizard:

```text
config init → generate .env → start Server → create Scope → connect Agent
```

Manual startup does not run config init. You prepare the environment variables or configuration file yourself:

```text
write configuration → validate → server run → check Server → create Scope → bind Agent
```

Manual startup does not mean compiling PowerContext from source. It means running PowerContext without relying on the configuration wizard and choosing the Server settings yourself.

## Prerequisites

You need Python 3.11 or newer, Git, [uv](https://docs.astral.sh/uv/getting-started/installation/), and an Agent CLI such as Codex CLI if you want to connect an Agent.

```bash
python3 --version
git --version
uv --version
```

## 1. Install PowerContext

If uv is not installed, run:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
```

Install the PowerContext CLI and local Server:

```bash
uv tool install --force "powercontext[cli,server]==1.4.1"
powercontext --version
powercontext --help
```

## 2. Stop a possibly running personal service

If you previously ran powercontext service install, a personal background Server may already be running. It may occupy the default port and make it unclear which Server you are checking.

```bash
powercontext service status
```

If the personal service is running, stop it:

```bash
powercontext service stop
```

This stops the personal service and prevents automatic activation while keeping its configuration and data.

## 3. Create a manual configuration file

```bash
mkdir -p ~/powercontext-manual
cd ~/powercontext-manual
nano manual.env
```

Put the following values in manual.env:

```dotenv
POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1
POWERCONTEXT_SERVER_HTTP_PORT=18080
POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true
POWERCONTEXT_SERVER_ACCESS_MODE=disabled
```

The settings mean:

| Setting | Purpose |
| --- | --- |
| POWERCONTEXT_SERVER_HTTP_HOST | Listen only on the local machine |
| POWERCONTEXT_SERVER_HTTP_PORT | Listen on port 18080 |
| POWERCONTEXT_SERVER_DASHBOARD_ENABLED | Enable the Dashboard |
| POWERCONTEXT_SERVER_ACCESS_MODE | Disable authentication for local testing |

This configuration does not specify a database or models, so it uses the default SQLite database and does not enable inference capabilities.

## 4. Validate the manual configuration

```bash
env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 \
  powercontext config validate \
  --env-file ~/powercontext-manual/manual.env
```

A successful command prints Configuration is valid. This step only validates the configuration; it does not start the Server.

## 5. Start the Server manually

```bash
env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 \
  powercontext server run \
  --env-file ~/powercontext-manual/manual.env
```

Keep this terminal open. A successful startup looks similar to:

```text
PowerContext Server is ready
Uvicorn running on http://127.0.0.1:18080
```

Press Ctrl-C to stop the foreground Server. If Generation and Embedding models are not configured, PowerContext prints a notice, but explicit Basic Memory saves and recall still work.

## 6. Open the Dashboard

Open the following address in a browser:

```text
http://127.0.0.1:18080/dashboard/home
```

Authentication is disabled, so the page can be opened directly from the local machine. An empty Dashboard on the first visit is expected.

## 7. Check the Server

Keep the Server running and open another terminal:

```bash
env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 \
  POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:18080 \
  powercontext doctor
```

Continue with:

```bash
env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 \
  POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:18080 \
  powercontext ready
```

```bash
env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 \
  POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:18080 \
  powercontext capabilities
```

Expected output includes:

```text
server liveness: ok - http://127.0.0.1:18080
server readiness: ok - http://127.0.0.1:18080
access_mode: disabled
database: ready
```

doctor checks the installation, Server liveness, and readiness. ready checks whether the Server can provide requests. capabilities shows the currently enabled capabilities.

## 8. Create a Scope manually

A Scope is an isolated space for project context. It separates Sources, Memory, Topic Memory, Handoffs, and Agent recall data.

Use the Python Client to call the Server and create a Scope manually. Keep the Server running and execute the following in another terminal:

```bash
cd ~/powercontext-manual
POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:18080 \
uv run --with "powercontext[client]==1.4.1" python - <<'PY'
import asyncio
import os
from powercontext.client import PowerContextClient
from powercontext.http import CreateScopeRequest

async def main():
    async with PowerContextClient(os.environ["POWERCONTEXT_CLIENT_SERVER_URL"]) as client:
        scope = await client.create_scope(
            CreateScopeRequest(
                title="manual-powercontext-demo",
                summary="Scope created during manual setup",
                idempotency_key="manual-powercontext-demo-v1",
            )
        )
        print("scope_id:", scope.scope_id)

asyncio.run(main())
PY
```

The Server returns the real scope_id. Use this ID when binding the Agent; do not construct an ID from the Scope title.

## 9. Bind the Codex Agent

Set the real Scope ID in the current terminal:

```bash
export POWERCONTEXT_CODEX_SCOPE_ID=replace-with-the-scope-id-returned-by-the-server
```

Install the Codex plugin matching the Server version and point it to the manually started Server:

```bash
powercontext setup codex \
  --ref powercontext-v1.4.1 \
  --server-url http://127.0.0.1:18080
powercontext doctor codex
```

The checks should confirm that the plugin, MCP, and tool discovery are working. Installing the plugin does not start the Server, so the Server terminal must remain open.

## 10. Verify explicit Memory and cross-session recall

Start a new Codex session from a terminal where POWERCONTEXT_CODEX_SCOPE_ID is set:

```bash
codex
```

Send a message that explicitly asks Codex to save a decision:

```text
Please save the following decision to PowerContext:

The Aurora project uses uv to manage Python dependencies and .env for configuration. The first phase will be deployed on a Linux server.
```

Exit Codex, start a new session with the same Scope, and ask:

```text
Please retrieve the Aurora project's dependency management and deployment decision from PowerContext.
```

If the Agent retrieves the same content and shows the Memory as active with its source and revision, manual startup, Scope binding, explicit saving, and cross-session recall have all been verified.

## Capability boundaries

This page does not configure Generation or Embedding models. It therefore verifies explicit Memory, full-text search, and cross-session recall, but it does not verify automatic Memory extraction or automatic generation of Topic Memory, Experience, or Skill. To enable those capabilities, see [Configure models](configure-models.md).

## Troubleshooting

### capabilities shows extraction disabled

This means that Generation and Embedding models are not configured. It does not prevent explicit Basic Memory saves and recall.

### capabilities returns 401

The Server is usually enforcing authentication while the client has no Token. Confirm that manual.env contains POWERCONTEXT_SERVER_ACCESS_MODE=disabled and use env -i to avoid inheriting old environment variables.

### The check connects to port 8000 instead of 18080

Set the client address explicitly:

```bash
POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:18080 powercontext ready
```

### The Dashboard has no data

An empty Dashboard on the first visit is expected. Write a Source or Memory through an Agent or API, and confirm that the Dashboard and Agent use the same Server and Scope.

## Related documentation

- [Quick Start](quickstart.md): use the setup wizard for a complete memory walkthrough;
- [Configure the Server environment](configure-server-environment.md): learn about configuration files and environment variables;
- [Configure models](configure-models.md): enable automatic extraction and vector retrieval;
- [Deploy the Server](../operate/deploy-server.md): run a personal background service or remote Server;
- [Troubleshoot](../operate/troubleshoot.md): investigate Server, model, and connection issues.
