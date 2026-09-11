---
title: Quick Start
description: Configure full memory, connect Codex, and verify Source capture, Topic Memory evolution, and cross-session recall.
---

# Quick Start

Start with installation, discuss a project in Codex, watch its input become Source evidence and an evolving topic,
then recover the decisions in a new session. These instructions use PowerContext 1.0.0 RC2 for pre-release testing,
with the Agent plugin from the matching `powercontext-v1.0.0rc2` tag.

You need macOS or Linux, Python 3.11+, Git, [uv](https://docs.astral.sh/uv/getting-started/installation/),
and an installed Codex CLI. Full memory also needs working Generation and Embedding model APIs:
prepare their base URLs, model names, and API keys. Signing into a Codex or Claude subscription does not automatically
provide these APIs to the PowerContext Server. Without separate model APIs, select basic memory to test explicit saves
and recall; that does not enable automatic Topic Memory.

## 1. Install and open the wizard

```bash
uv tool install --force "powercontext[cli,server]==1.0.0rc2"
mkdir -p ~/powercontext-demo
cd ~/powercontext-demo
powercontext config init --language en --output .env
```

For a first local installation:

1. **Storage**: SQLite works without another database dependency. To try embedded seekdb, select it and approve the background dependency installation.
2. **Usage scenario**: choose “Only on this machine” when your Agent, browser, and Server share a machine. For a remote Server, first read [Connect to a remote Server](../operate/connect-remote-server.md).
3. **Memory capabilities**: select full memory and enter Generation and Embedding API details. See [Configure models](configure-models.md) for protocols and dimensions.
4. **Dashboard**: enable it to inspect Sources and memories. The wizard creates or reuses a Server token.
5. **Background processing**: start with the recommended schedule for each Artifact. An inspection interval is not a completion deadline; model processing takes additional time.
6. **Agent**: select Codex and plan a new isolated Scope. Select Claude Code next if needed, then choose “Finish Agent configuration”.
7. Review and save.

The wizard writes:

| File | Purpose |
| --- | --- |
| `.env` | Server settings, database, model credentials, and Server token |
| `.env` | Server, client, Agent, and model settings used by this installation |
| `.env.next-steps.md` | Startup, Scope creation, plugin connection, and checks for your choices |

The final screen shows the Dashboard URL, a newly generated token, and the SSH command when selected. Later, look up
`POWERCONTEXT_SERVER_AUTH_TOKEN` in `.env`. These files contain credentials; do not commit them.
If seekdb is still installing, the wizard waits with an activity indicator. Complete any reported dependency recovery
before starting the Server. Saving files or installing dependencies does not start the Server.

## 2. Start the Server

In this terminal, run:

```bash
powercontext config validate --env-file .env
powercontext server run --env-file .env
```

Keep the terminal running. Open the Dashboard URL printed by the wizard; the local default is
`http://127.0.0.1:8000/dashboard/home`. Sign in with the **Server token**, not a model API key.
An empty Dashboard is expected before you capture data. For operation after closing the terminal, use a
[persistent personal service](../operate/deploy-server.md#run-a-persistent-personal-server).

Open another terminal, load the client settings, and check the running service:

```bash
cd ~/powercontext-demo
set -a
. ./.env
set +a
powercontext ready
powercontext capabilities
```

Confirm readiness and the capabilities you selected. A `degraded` result means some dependencies still need attention;
it is not a successful full-memory check. `config validate` checks configuration, while these commands check the running service.

## 3. Create a Scope and install the Codex plugin

Open `.env.next-steps.md` and run the request under “Create the planned isolated Scopes”. The Server returns the real
`scope_id`. Add it to `.env`:

```dotenv
POWERCONTEXT_CODEX_SCOPE_ID=replace-with-returned-scope-id
```

The planned `codex-xxxxxxxx` value is a title, not an ID. For Claude Code, put the ID returned by its creation request
in `POWERCONTEXT_CLAUDE_SCOPE_ID`. Agents can have separate Scopes or explicitly share an existing one.
Changing directories does not create isolation. Use the same Scope in the Dashboard and Agent during this check.

Reload the client settings and install the matching plugin:

```bash
set -a
. ./.env
set +a
powercontext setup codex --ref powercontext-v1.0.0rc2
powercontext doctor codex
codex
```

Confirm that the PowerContext Hook and MCP have both loaded in Codex. For a non-default address, follow the Codex
connection instructions in `.env.next-steps.md`: the installed plugin's `.mcp.json` must use the same Server as the Hook,
and read Authorization from `POWERCONTEXT_CODEX_AUTHORIZATION`. Installing the plugin does not start the Server.

These commands use Codex CLI. A desktop app may not inherit terminal environment variables. Before testing in the
desktop app, verify that both its Hook and MCP receive the same URL, token, and Scope. See
[Codex](../integrations/codex.md) and [Claude Code](../integrations/claude-code.md) for host-specific behavior.

Full memory also needs a generation policy for this real Scope to use Profile. Follow the
[Profile policy steps](configure-models.md#enable-a-profile-policy-for-the-scope) to read its current version and update it;
saving the configuration does not perform this operation.

## 4. Verify Topic Memory with ordinary conversation

Send a concrete project decision in the new Codex session:

```text
We are designing the Aurora acceptance project. Use uv for Python dependencies and .env for configuration.
The first deployment runs on one Linux server, with a Mac connecting through an SSH tunnel.
Restate these constraints without changing any code.
```

Select the bound Scope in the Dashboard and check the following:

1. **Source** contains your input. Prompt capture does not imply that every Agent reply is captured.
2. **Topic Memory** contains a related topic after the configured inspection interval and model processing.
3. Send a related update: “Aurora deployment update: run the service with a user-level service manager. Keep the SSH tunnel.”
4. Confirm that the second Source arrived and that topic content or revision history reflects the update. Waiting one minute is not itself a check.
5. Open a new Codex session with the same Scope and ask: “Find Aurora's dependency-management and deployment decisions in PowerContext, with citations.”

Correct content and its citation complete the capture → evolution → cross-session recall check. A successful
`doctor codex` only checks installation and connectivity; it does not prove this workflow. The model may merge evidence
or decide that no update is needed, so not every message creates a new memory.

In basic-memory mode, explicitly ask the Agent to save Aurora's uv decision in PowerContext, then search for it in a
new session and verify its citation. That test does not cover automatic extraction. Profile, Experience, and Skill have
additional triggers or review requirements; see [Capability behavior](configure-models.md#full-capabilities-do-not-generate-every-artifact-from-every-message).

## Continue

- [Install and run](install-and-run.md): versions, seekdb dependencies, existing storage, and updates.
- [Deploy the Server](../operate/deploy-server.md): background services, SSH, HTTPS, and backups.
- [Configure models](configure-models.md): API protocols, vector dimensions, and extraction checks.
- [Troubleshoot](../operate/troubleshoot.md): service, model, capture, or recall failures.
