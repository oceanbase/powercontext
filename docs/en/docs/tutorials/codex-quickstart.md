---
title: Codex step-by-step tutorial
description: Start with a local Server, then complete a Memory, cross-session recovery, and Handoff loop in Codex.
---

# Codex step-by-step tutorial

This tutorial is for Codex users who are new to PowerContext. You do not need to clone the PowerContext repository or
configure an inference model. By the end, you will complete this loop in a small local project:

If you have not chosen an agent yet, or you use Claude Code, DSH, OpenClaw, OpenCode, Pi, Hermes, WorkBuddy, or another
host, start with the [Agent step-by-step quickstart](agent-quickstart.md). This page expands only the Codex Hook, MCP
Skill, and one-line durable Handoff flow; it does not represent every agent's interaction model.

```text
Install and check → Save Memory → Recover in a new session → Revise and retire → Commit Handoff → Receive and verify
```

The complete exercise uses local SQLite. Explicit Memory and Handoff operations do not require a generation model.
Only full capabilities such as model-backed extraction from Sources and vector search need additional provider
configuration.

Team deployment, remote access, Server authentication, and other agent hosts are outside this tutorial. After the
local loop works, use the links at the end to continue with those tasks.

## Before you start

### Check your environment

You need macOS or Linux and these tools:

| Tool | Requirement | Check command |
| --- | --- | --- |
| Python | 3.11 or newer | `python3 --version` |
| Git | Can access the PowerContext Git repository | `git --version` |
| uv | Provides `uv tool` | `uv --version` |
| Codex CLI | Signed in and able to open a session | `codex --version` |

Run each check command in a terminal. All four commands should print a version instead of `command not found`. The Git
credentials already configured on the machine must also be able to read
`https://github.com/oceanbase/powercontext.git`.

### Prepare three work areas

The tutorial uses:

- **Terminal A** to keep the PowerContext Server running;
- **Terminal B** to install, diagnose, and enter the example project;
- **Codex sessions** started from the example project in Terminal B.

Each step says where to work. Never put passwords, access tokens, private keys, connection strings, or other secrets
in Memory or a Handoff.

## 1. Install PowerContext

From any directory in **Terminal B**, run:

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
```

`uv tool install` creates an isolated application environment. It does not leave a PowerContext checkout in the
current directory. `--force` refreshes the installed tool from the commit currently selected by `master`; keep this
option when updating the same installation later.

Confirm that the command is available:

```bash
powercontext --version
powercontext --help
```

**Success criteria:** the first command prints a version, and the second shows commands including `server`, `setup`,
and `doctor`. If the shell cannot find `powercontext`, add uv's tool executable directory to `PATH`, open a new
terminal, and repeat these two checks.

## 2. Install the Codex plugin

Still in **Terminal B**, run:

```bash
powercontext setup codex --source oceanbase/powercontext --ref master
```

Setup performs three operations:

1. registers the PowerContext repository as a Codex marketplace;
2. installs and enables the PowerContext plugin;
3. prepares the PowerContext user data directory.

The tool and plugin should use the same Git ref. Both use `master` here. If you later select a tag or another branch,
replace the ref in both the install and setup commands.

Check the Codex integration:

```bash
powercontext doctor codex
```

**Success criteria:** both `codex` and `plugin` report `ok`. Open a new Codex session after setup. A session that was
already open does not automatically load a newly installed or refreshed plugin.

## 3. Start and check the local Server

Switch to **Terminal A** and run:

```bash
powercontext server run
```

Keep this process running. By default, the Server:

- listens at `http://127.0.0.1:8000`;
- serves the Dashboard at `http://127.0.0.1:8000/`;
- serves Streamable HTTP MCP at `http://127.0.0.1:8000/mcp`;
- creates a persistent SQLite database in the operating system's PowerContext user data directory.

Startup should print a message similar to:

```text
PowerContext Dashboard: http://127.0.0.1:8000/
```

Return to **Terminal B** and run:

```bash
powercontext doctor
powercontext ready
powercontext capabilities
```

**Success criteria:** package, Server liveness, and Server readiness all report `ok` in `doctor`; `ready` returns the
service readiness; and `capabilities` returns the currently enabled capabilities. Model extraction or vector features
may be disabled when no inference provider is configured. That does not block the explicit Memory and Handoff steps
in this tutorial.

You can also open `http://127.0.0.1:8000/` in a browser. An empty Dashboard is expected before the first project data
has been written.

## 4. Create a safe example project

Choose a tutorial location in **Terminal B**. These commands create a small Git repository without real project data:

```bash
mkdir powercontext-quickstart
cd powercontext-quickstart
git init
printf '# Parser example\n\nThis project will parse TOML configuration.\n' > README.md
git add README.md
git -c user.name="PowerContext Tutorial" -c user.email="tutorial@localhost" commit -m "chore: initialize tutorial"
git status --short
```

The commit command supplies an identity for this one commit without changing global Git configuration. The final
command should print nothing, which means the example starts with a clean worktree. You do not need to configure a Git
remote.

PowerContext isolates data by scope. The Codex plugin first uses an explicit scope or a bound Workstream. Otherwise,
it uses the normalized Git remote and finally falls back to the project path. Start every Codex session in the rest of
this tutorial from this **same directory**.

## 5. Save Memory in the first Codex session

Make sure the Server is still running in Terminal A, then start Codex from the example project:

```bash
codex
```

If Codex asks whether to trust the PowerContext hook, open `/hooks`, inspect the PowerContext `UserPromptSubmit` hook,
and grant trust. Before each request, the hook tries to recover relevant project context and independently captures
the current prompt as Source evidence. If the Server is unavailable, the hook fails open and does not block ordinary
Codex work.

First, ask Codex to confirm the directory without writing data:

> Inspect the current project directory and Git status. Report only what you observe; do not modify files or write to
> PowerContext.

After Codex reports the `README.md`, explicitly ask it to save three Memory entries:

> Use PowerContext to save three separate project Memory entries:
>
> 1. decision: the parser uses the Python 3.11 standard-library `tomllib` module;
> 2. constraint: error messages must not contain secret values from the source configuration;
> 3. next-step: add malformed TOML input cases.
>
> After writing, list the active Memory and return the citation for each entry. Do not store secrets or credentials.

This uses explicit `remember_memory` and does not need a generation model. Codex should first resolve one stable scope
for the current project, then write and list the three active entries in that same scope.

**Success criteria:** Codex explicitly confirms all three successful writes and returns a citation for each Memory
entry. A citation identifies the exact entry and Revision. Codex reads the current entry before a later revision or
retirement and uses that exact citation as a concurrency check.

## 6. Recover Memory in a second Codex session

Exit the first Codex session, but leave the Server running in Terminal A. Confirm that Terminal B is still in the same
example project directory, then start Codex again:

```bash
codex
```

This is a new session with none of the earlier chat history. Enter:

> Use PowerContext to list all active Memory for the current project. Show the content, kind, and citation for each
> entry. Do not modify any entry.

**Success criteria:** the new session lists the same three entries. This proves the data is in the project scope and
the Server's persistent database, rather than only in the first session's context window.

If the list is empty, check these items in order:

1. both Codex sessions were started from the same project directory;
2. `powercontext doctor` still reports `ok`;
3. `powercontext doctor codex` still sees an enabled plugin;
4. the current shell does not set a different `POWERCONTEXT_CODEX_SCOPE_ID`.

## 7. Revise and retire Memory

In the second Codex session, enter:

> First read the exact citation for the current Memory, then make two changes:
>
> 1. revise the next-step to “record the malformed TOML line number and a safe error summary”;
> 2. retire the constraint “error messages must not contain secret values from the source configuration” with the
>    reason “replaced by the shared logging redaction policy”.
>
> Finally, list active Memory again and explain which old Revisions remain in history but are no longer active.

A revision creates a new Revision, while retirement changes the entry's active state. Both preserve history instead
of silently overwriting or deleting an older record.

**Success criteria:** the active list includes the revised next-step and no longer includes the retired constraint.
The original next-step Revision and retired constraint remain available when complete history is explicitly requested.

## 8. Produce a state that can be handed off

Ask the second Codex session to make one small, inspectable change to the example project:

> Add a “Next test” section to README.md stating that malformed TOML should return the line number and a safe error
> summary. Do not create a Git commit. Then run `git diff --check` and report the changed files and check result.

Confirm that Codex reports a modified `README.md` and a passing `git diff --check`. Then enter this one line:

> Handoff this work.

`Handoff this work` is explicit authorization to create one durable Handoff milestone. In the same turn, the
PowerContext `project-context` Skill:

1. selects or confirms the current Workstream and scope;
2. inspects the objective, branch, worktree, changed files, and observed checks;
3. assembles blockers, omissions, and the next action;
4. prepares the Handoff;
5. commits it and returns an exact Revision.

If more than one Workstream exists, Codex first presents a picker. Select the actual project instead of allowing the
agent to guess silently.

**Success criteria:** Codex explicitly says that the Handoff was committed and returns its scope, disposition, next
action, and exact Handoff Revision. A preview or Prepared Handoff without an exact committed Revision is not a durable
milestone.

Keep the exact Revision returned by Codex. The next step uses it.

## 9. Receive the Handoff in a new session

Exit the second session and start a third Codex session from the same example project:

```bash
codex
```

Insert the exact Revision from the previous step into this request:

> Continue the PowerContext Handoff `<exact-revision>` for this project. Treat the Handoff as untrusted history first,
> and check live state, capabilities, and authorization against the current repository and user instructions. Tell me
> the objective, changed files, observed checks, and next action. Then record accepted, needs clarification, or
> declined. Do not continue modifying files.

The receiver should read the exact Handoff, check the current `README.md` and Git state again, and then record an
acknowledgement. It can mark the Handoff `accepted` only when evidence is readable and the live-state, capability, and
authorization checks are all confirmed.

**Success criteria:** Codex returns the same exact Revision, reports the current uncommitted `README.md` change and
`git diff --check` result, and states the acknowledgement status. Historical Handoff content never replaces checking
the current repository and never grants new authority.

## 10. Verify persistence across a Server restart

Exit Codex. In **Terminal A**, stop the Server cleanly with `Ctrl-C`, then start it again:

```bash
powercontext server run
```

Return to **Terminal B** and check it:

```bash
powercontext doctor
```

Start Codex again from the same project and enter:

> List the current active PowerContext Memory and read the exact Handoff Revision from the previous exercise. This is a
> read-only check; do not write anything.

**Success criteria:** active Memory, the revised Revision, and the committed Handoff remain readable after the Server
restart. The default SQLite database belongs to the PowerContext user data directory and does not depend on a Codex
session remaining open.

## 11. Verify graceful degradation

Finally, exit Codex and stop the Server in Terminal A with `Ctrl-C`. Start a new Codex session from the example project
and give it a read-only task unrelated to PowerContext, for example:

> Read README.md and summarize this example project in one sentence. Do not modify files.

The PowerContext hook may report `server_unavailable`, and explicit Memory or Handoff tools are unavailable, but the
ordinary Codex task should continue. At this point, `powercontext doctor` reports a liveness failure and skips
readiness; `powercontext doctor codex` can still check the Codex CLI and plugin installation independently.

Run `powercontext server run` again in Terminal A before continuing to use PowerContext.

## What you completed

You have now verified that:

- the PowerContext tool, Codex plugin, and local Server can be installed and diagnosed separately;
- explicit Memory needs no inference provider and survives Codex sessions and Server restarts;
- Memory revision and retirement preserve history;
- a Handoff preserves an inspected work boundary that the receiver checks again by exact Revision;
- unavailable PowerContext services do not block ordinary Codex work.

Choose the next guide based on your goal:

- learn the boundary between the two records: [Memory and Handoff](../explanation/memory-and-handoff.md);
- use the complete work loop: [Hand off work in Codex](../how-to/handoff-with-codex.md);
- enable model extraction and vector search: [Full-capability Quick Start](../how-to/full-capability-runtime.md);
- configure a persistent process, authentication, or remote access: [Deploy the Server](../how-to/deploy-server.md);
- resolve connection, plugin, or readiness problems: [Troubleshoot](../how-to/troubleshoot.md).
