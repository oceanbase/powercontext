---
title: Codex quickstart
description: Install PowerContext and carry project context across Codex sessions.
---

# Codex quickstart

This tutorial installs PowerContext without requiring you to clone the repository, connects Codex, and proves that
Memory survives across sessions.

## Before you start

You need macOS or Linux, `uv`, Codex CLI, and read access to the PowerContext Git URL. Confirm that Git can reach the
repository with the credentials already configured on your machine.

## 1. Install and configure

Run these commands from any directory:

```bash
uv tool install "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@main"
powercontext setup codex --source oceanbase/powercontext --ref main
```

The first command installs an isolated application. The second installs the Codex plugin and prepares PowerContext's
user data directory.

## 2. Start the Server

Keep this process running in its own terminal:

```bash
powercontext server run
```

The default service listens at `http://127.0.0.1:8000`. It creates a persistent SQLite database on first start.

Check the whole installation from another terminal:

```bash
powercontext doctor
```

Every line should report `ok`.

## 3. Save a handoff

Start a new Codex session in a project directory. If Codex asks whether to trust the PowerContext hook, open `/hooks`
and approve it.

Ask Codex:

> Use PowerContext to save three separate handoff entries: the outcome is “the parser accepts TOML”; the current state
> is “tests pass on Python 3.11”; the next step is “add malformed-input cases”.

Codex should use the project-context skill and confirm the successful Memory writes. Do not put secrets in Memory.

## 4. Restore and update it

End that session and start another one in the same project. Ask:

> Restore the PowerContext handoff for this project. Then revise the next step to “document malformed-input errors”
> and retire the old current-state entry.

The second session should recover the three entries before changing them. Revision and retirement preserve history;
they do not overwrite or delete old versions.

Start a third session and ask:

> List the active PowerContext memory for this project.

The revised next step should be active. The retired current state and superseded next step should not appear in the
active list.

## 5. Check graceful degradation

Stop the Server with `Ctrl-C`, then give Codex an ordinary task. PowerContext may report that Memory is unavailable,
but it must not block the task. `powercontext doctor` now exits with a failure for the Server check while continuing to
report the installed package, plugin, and database.
