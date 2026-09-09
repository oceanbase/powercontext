---
title: Quick Start
description: Install PowerContext, connect one Agent, and recover a saved decision in a new session.
---

# Quick Start

Use one running PowerContext Server to keep project context across Agent sessions. This path uses Codex and explicit
Memory writes; it needs no generation model or embedding provider.

## 1. Install and start

You need Python 3.11+, Git, uv, and an installed Codex host. PowerContext supports macOS and Linux;
Windows support is `experimental`. Host and optional database requirements are listed in
[Install and run](install-and-run.md). The commands below use the current, unreleased `master` integration:

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext server run
```

Keep this terminal open. The default Server listens at `http://127.0.0.1:8000`, stores data in a persistent local SQLite
database, and serves the Dashboard at `/` and MCP at `/mcp`.

For the tagged release installation, see [Install and run](install-and-run.md#choose-a-version).
Keep the Server package and integration on the same tag or commit.

## 2. Connect an Agent

In another terminal:

```bash
powercontext setup codex --source oceanbase/powercontext --ref master
powercontext doctor codex
```

Open Codex in the project whose context you want to keep. Start a new session after installation so the plugin is
loaded. The plugin resolves an existing Scope through a session or workspace binding, falling back to the Server's
default Scope. Projects are not automatically assigned separate Scopes; use
[Scopes and access](../workflows/scopes-and-access.md) to establish separate project boundaries.

For another host, use its [integration guide](../integrations/index.md). WorkBuddy needs its own
`setup workbuddy` command; Hermes additionally requires `hermes memory setup` and selecting PowerContext.

## 3. Save and recover one decision

Ask the Agent to save an explicit, non-sensitive decision:

```text
Remember this project decision in PowerContext: use uv for Python dependency management.
```

Ask it to search PowerContext for that decision. A successful write and read should return the decision with its
Memory citation. Then open a new Agent session in the same project and ask:

```text
Search PowerContext: which tool does this project use for Python dependency management?
```

Verify the returned decision and citation. Persistence comes from using the same Server data and resolved Scope,
not from keeping the original conversation open. Current project files and user instructions still take precedence.

## Continue with your work

- [Memory and context](../workflows/memory-and-context.md): revise or retire saved information and control recall.
- [Work continuity](../workflows/memory-and-handoff.md): transfer current work with Handoff.
- [Enable extraction and vector search](configure-models.md): process captured Sources with models.
- [Deploy the Server](../operate/deploy-server.md): run a persistent personal service.
- [Troubleshoot](../operate/troubleshoot.md): diagnose installation, Server, or recall problems.
