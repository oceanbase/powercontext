---
title: Quick Start
description: Install PowerContext, connect Codex, and recover a saved decision in a new session.
---

# Quick Start

Use one running PowerContext Server to keep project context across Agent sessions. This example uses Codex and explicit
Memory writes; it needs no generation model or embedding provider.

## 1. Install and start

You need Python 3.11+, Git, uv, and an installed Codex. PowerContext supports macOS and Linux;
Windows support is `experimental`. Host and optional database requirements are listed in
[Install and run](install-and-run.md). The commands below use the current, unreleased `master` integration:

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext server run
```

Keep this terminal open. The default Server listens at `http://127.0.0.1:8000`, stores data in a persistent local SQLite
database, and serves MCP at `/mcp`. The personal Dashboard is disabled by default; see [Install and run](install-and-run.md) to enable it.

For the tagged release installation, see [Install and run](install-and-run.md#choose-a-version).
Keep the Server package and integration on the same tag or commit.

## 2. Connect Codex

In another terminal, install the plugin and verify the integration:

```bash
powercontext setup codex --source oceanbase/powercontext --ref master
powercontext doctor codex
```

Open a new Codex session in your project to load the plugin. Connection, authentication, and other settings are covered
in the [Codex guide](../integrations/codex.md). For another host, follow its [integration guide](../integrations/index.md).

Different projects do not automatically receive separate Scopes. Use [Scopes and access control](../workflows/scopes-and-access.md)
when projects need isolated data.

## 3. Save and recover one decision

Ask Codex to save an explicit, non-sensitive decision:

```text
Remember this project decision in PowerContext: use uv for Python dependency management.
```

Ask it to search PowerContext for that decision. A successful write and read should return the decision with its
Memory citation. Then open a new Codex session in the same project and ask:

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
