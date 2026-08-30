---
title: Codex quickstart
description: Install PowerContext and carry project context across Codex sessions.
---

# Codex quickstart

This tutorial installs PowerContext without requiring you to clone the repository. When you finish, you will read,
revise, and retire Memory saved from the first Codex session in a second session for the same project.

## Before you start

You need macOS or Linux, `uv`, Codex CLI, and read access to the PowerContext Git URL. Confirm that Git can reach the
repository with the credentials already configured on your machine.

## 1. Install the tool and plugin

Run these commands from any directory:

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext setup codex --source oceanbase/powercontext --ref master
```

The first command installs an isolated application. The second installs the Codex plugin and prepares PowerContext's
user data directory. For installation, updates, and data locations, see [Install and run](../how-to/install-and-run.md).

## 2. Start the Server

Keep this process running in its own terminal:

```bash
powercontext server run
```

The default service listens at `http://127.0.0.1:8000`. It creates a persistent SQLite database on first start. Keep
this terminal running.

Check the whole installation from another terminal:

```bash
powercontext doctor
powercontext doctor codex
```

Every line from both commands should report `ok`. The first checks the package and Server; the second checks only the
optional Codex integration. If a result is `degraded` or `failed`, read [Troubleshoot](../how-to/troubleshoot.md).

## 3. Save project Memory

Start a new Codex session in a project directory. If Codex asks whether to trust the PowerContext hook, open `/hooks`
and approve it.

Ask Codex:

> Use PowerContext to save three separate project Memory entries: the outcome is “the parser accepts TOML”; the current state
> is “tests pass on Python 3.11”; the next step is “add malformed-input cases”.

Codex should use the project-context skill and confirm the successful Memory writes. Do not put secrets in Memory.

## 4. Read and update it in a later session

End that session and start another one in the same project. Ask:

> List the active PowerContext Memory for this project. Then revise the next step to “document malformed-input errors”
> and retire the old current-state entry.

The second session should list the three active entries before changing them. Revision and retirement preserve history;
they do not overwrite or delete old versions.

Start a third session and ask:

> List the active PowerContext Memory for this project.

The revised next step should be active. The retired current state and superseded next step should not appear in the
active list. This shows that the project scope remains consistent across Codex sessions.

This tutorial verifies durable Memory. Read [Memory and Handoff](../explanation/memory-and-handoff.md) for the
distinction between Memory and a temporary Handoff. To transfer a complete work package to another task, session, or
model, use [Hand off work in Codex](../how-to/handoff-with-codex.md).

## 5. Check graceful degradation

Stop the Server with `Ctrl-C`, then give Codex an ordinary task. PowerContext may report that Memory is unavailable,
but it must not block the task. `powercontext doctor` now exits with a liveness failure, skips readiness, and still
reports the installed package. `powercontext doctor codex` continues to report the Codex integration independently.
If only a configured inference provider fails, the Server remains in traffic and reports readiness as `degraded`;
`doctor` surfaces that non-OK status without reading provider credentials.
