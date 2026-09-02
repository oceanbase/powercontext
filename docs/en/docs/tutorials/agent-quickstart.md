---
title: Agent step-by-step quickstart
description: Choose a supported agent, install PowerContext, and complete cross-session Memory and optional cross-agent Handoff.
---

# Agent step-by-step quickstart

This tutorial is for agent users who are new to PowerContext. You can use Codex, Claude Code, DeepSeek Harness,
OpenClaw, OpenCode, Pi, Hermes, or WorkBuddy. You can also load the portable Agent Plugin in a host that supports
Skills and MCP.

If you already have your own AI application and do not use one of these Agent Hosts, follow the
[HTTP API lifecycle tutorial](api-quickstart.md) to complete the first governed context loop directly.

By the end, you will complete this common loop:

```text
Install Server → Choose an agent → Verify integration → Save Memory → Recover in a new session → Hand off when supported
```

The common path uses local SQLite and does not require a generation model. Explicit Memory and existing Handoff
operations work without one. Model-backed extraction from Sources, vector search, and generation capabilities need
additional provider configuration.

Agent integrations do not all expose the same surface. This tutorial distinguishes:

- automatic context preparation from explicit Memory tools;
- one-line Handoff from multi-step Handoff;
- Memory-only integrations that do not yet provide a complete Handoff UI;
- interactive agent hosts from Python agent applications that require code integration.

## 1. Choose your agent path

Select the row for the host you already use. A setup command installs only the PowerContext integration; it does not
install the agent itself.

| Agent host | Install integration | Start or activate | Memory and automatic recall | Handoff path |
| --- | --- | --- | --- | --- |
| Codex | `powercontext setup codex` | `codex` | Prompt Hook + MCP Memory | `handoff this work` can commit a durable Handoff in one turn |
| Claude Code | `powercontext setup claude-code` | `claude` | Prompt Hook + MCP Memory | `handoff this work` can commit a durable Handoff in one turn |
| DeepSeek Harness | `powercontext setup dsh` | `dsh web` | Context before every model step + `pc_*` Memory tools | `pc_capture_source`, activate, finalize, commit, and continue |
| OpenClaw | `powercontext setup openclaw` | `openclaw` | Before-prompt recall + five `powercontext_memory_*` tools | Current integration provides Memory, not a complete Handoff UI |
| OpenCode | `powercontext setup opencode` | `opencode` | Context before each normal turn + `pc_*` tools | Capture, activate, finalize, commit, and continue |
| Pi | `powercontext setup pi` | `pi` | Context before each prompt + `pc_*` tools | Capture, activate, finalize, commit, and continue |
| Hermes | `powercontext setup hermes` | Run `hermes memory setup`, then start Hermes | MemoryProvider + `/pc` companion | `/pc` and provider operations expose the Handoff lifecycle |
| WorkBuddy | `powercontext setup workbuddy` | Restart WorkBuddy | Prompt Hook + MCP Memory | `handoff this work` can commit a durable Handoff in one turn |
| Agent Plugin host | Load the Agent Plugin directory | Reload by the host's procedure | Explicit MCP Memory; no portable Prompt Hook | Generic `project-context` Skill + MCP Handoff |

If you are building a Pydantic AI, LangChain, LangGraph, or Bub application, complete the Server install and checks,
then skip to [Python agent application paths](#14-python-agent-application-paths). These adapters are integrated in
application code and must not be presented as interactive-host setup commands.

## 2. Check the common environment

You need macOS or Linux and these common tools:

| Tool | Requirement | Check command |
| --- | --- | --- |
| Python | 3.11 or newer | `python3 --version` |
| Git | Can read the PowerContext Git repository | `git --version` |
| uv | Provides `uv tool` | `uv --version` |
| Selected agent | Installed, signed in, and on `PATH` | Run that host's `--version` or diagnostic command |

The first three commands should print versions. The Git credentials already configured on the machine must also be
able to read `https://github.com/oceanbase/powercontext.git`.

Prepare two terminals:

- **Terminal A** keeps the PowerContext Server running;
- **Terminal B** installs, diagnoses, enters the project, and starts the selected agent.

Never put passwords, access tokens, private keys, connection strings, or other secrets in Memory, Sources, or a
Handoff.

## 3. Install the PowerContext CLI and Server

Run in **Terminal B**:

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
```

The command creates an isolated application environment and does not leave a PowerContext checkout in the current
directory. `--force` refreshes an existing installation from the commit currently selected by `master`.

Confirm that the CLI is available:

```bash
powercontext --version
powercontext --help
```

**Success criteria:** the first command prints a version, and the second shows commands including `server`, `setup`,
and `doctor`.

## 4. Install one or more agent integrations

### Install one host

Choose one command below. Use the same revision for `--ref` and the PowerContext tool:

```bash
powercontext setup codex --source oceanbase/powercontext --ref master
powercontext setup claude-code --source oceanbase/powercontext --ref master
powercontext setup dsh --source oceanbase/powercontext --ref master
powercontext setup openclaw --source oceanbase/powercontext --ref master
powercontext setup opencode --source oceanbase/powercontext --ref master
powercontext setup pi --source oceanbase/powercontext --ref master
powercontext setup hermes --source oceanbase/powercontext --ref master
powercontext setup workbuddy --source oceanbase/powercontext --ref master
```

Run only the command for a host that is already installed. Each setup performs post-install diagnostics for its own
integration. Resolve that host's prerequisite failure instead of assuming setup completed.

Hermes also requires MemoryProvider selection:

```bash
hermes memory setup
```

Select `PowerContext` in the wizard, then restart Hermes.

### Install several first-class hosts at once

Codex, Claude Code, DeepSeek Harness, OpenClaw, OpenCode, Pi, and Hermes are in the `setup select` catalog. For
example:

```bash
powercontext setup select \
  --host claude-code \
  --host dsh \
  --host opencode \
  --source oceanbase/powercontext \
  --ref master
```

Each host independently reports `installed`, `failed`, or `skipped`. A failure for one host is not hidden by another
host's success. WorkBuddy is not in this catalog; run `powercontext setup workbuddy` separately.

### Use the portable Agent Plugin

When a host can load Agent Plugin Skills and MCP configuration but has no dedicated setup command, follow
[Configure Agent Plugin](../how-to/configure-agent-plugin.md) and load
`integrations/agent-plugin/powercontext/`. The package provides a `project-context` Skill and configuration for
`http://127.0.0.1:8000/mcp`. It does not start the Server and does not provide a cross-host Prompt Hook.

## 5. Start and check the Server

Run in **Terminal A** and keep the process alive:

```bash
powercontext server run
```

By default, the Server:

- listens at `http://127.0.0.1:8000`;
- serves the Dashboard at `/`;
- serves Streamable HTTP MCP at `/mcp`;
- uses a persistent SQLite database in the PowerContext user data directory.

Return to **Terminal B** and run:

```bash
powercontext doctor
powercontext ready
powercontext capabilities
powercontext doctor integrations
```

**Success criteria:**

- package, Server liveness, and Server readiness report `ok` in `doctor`;
- `ready` and `capabilities` can read the current service;
- the installed host's CLI and integration items report `ok` in `doctor integrations`;
- missing hosts can appear as missing without failing this read-only overview.

WorkBuddy is not in the first-class overview, so run `powercontext doctor workbuddy` separately. You can also run
`powercontext doctor codex`, `doctor claude-code`, `doctor dsh`, `doctor openclaw`, `doctor opencode`, `doctor pi`, or
`doctor hermes` for the selected host.

## 6. Create a safe example project

Create a Git project without real business data in **Terminal B**:

```bash
mkdir powercontext-agent-quickstart
cd powercontext-agent-quickstart
git init
printf '# Parser example\n\nThis project will parse TOML configuration.\n' > README.md
git add README.md
git -c user.name="PowerContext Tutorial" -c user.email="tutorial@localhost" commit -m "chore: initialize tutorial"
git status --short
```

The last command should print nothing. The commit identity applies only to this one commit and does not change global
Git configuration.

Start every later session from this same directory. Most dedicated integrations derive a stable scope from the Git
remote or project path. When an explicit scope is configured, every Memory and Handoff call in the same workflow must
reuse that exact `scope_id`.

## 7. Start the agent and inspect the integration surface

Start the selected host from the example project:

```bash
codex       # Codex
claude      # Claude Code
dsh web     # DeepSeek Harness
openclaw    # OpenClaw
opencode    # OpenCode
pi          # Pi
hermes      # Hermes, after memory setup
```

WorkBuddy users should open or create a task for this project and restart the host after installation. Agent Plugin
users should reload the compatible host and confirm that both the `project-context` Skill and `powercontext` MCP
Server are visible.

Begin with a read-only inspection:

> Inspect the current project directory, Git state, and available PowerContext integration capabilities. Report the
> current scope or its source and list the Memory read tools. Do not modify files or write to PowerContext.

Tool names differ by host:

| Integration | Expected Memory surface |
| --- | --- |
| Codex, Claude Code, WorkBuddy, Agent Plugin | MCP `search_memory`, `list_memory_entries`, `get_memory_entry`, and related tools |
| DSH, OpenCode, Pi | `pc_search`, `pc_memory_list`, `pc_memory_get`, `pc_remember`, and related tools |
| OpenClaw | `powercontext_memory_search`, `get`, `store`, `revise`, and `retire` |
| Hermes | MemoryProvider tools plus `/pc`, `/powercontext`, or `hermes powercontext ...` |

If the tools are absent, exit the host, rerun that host's setup and doctor commands, and open a new session. Do not
continue with an unloaded integration and mistake a normal model answer for a PowerContext result.

## 8. Save and read explicit Memory

Enter this in the agent session:

> Use this host's explicit PowerContext Memory tools to save three separate project Memory entries:
>
> 1. decision: the parser uses the Python 3.11 standard-library `tomllib` module;
> 2. constraint: error summaries must not contain secret values from the source configuration;
> 3. next-step: add malformed TOML input cases.
>
> After writing, search or list the active Memory and return the citation for each entry. Do not store secrets or
> credentials.

DSH, OpenCode, and Pi should call `pc_remember`; OpenClaw should call `powercontext_memory_store`; MCP integrations
should call `remember_memory`. These are durable mutations. When the host requests confirmation, inspect the content
before approving it.

Hermes also provides a deterministic CLI path for one write and search:

```bash
hermes powercontext remember decision "The parser uses Python 3.11 tomllib"
hermes powercontext search "Python parser"
```

**Success criteria:** the agent or Hermes CLI reports a successful write and returns the content and exact citation in
the current scope. Explicit Memory does not require a generation model. Prompt or turn capture creates a Source; it
does not mean Memory was already created.

## 9. Recover in a new session of the same host

Exit the agent session without stopping the Server. Confirm that Terminal B is still in the example project, then
start the same host again. Enter:

> Use PowerContext to search active Memory in the current project for `tomllib` and malformed TOML. Return the content,
> kind, and citation. Do not modify any entry.

**Success criteria:** the new session recovers the three entries. The data comes from a stable scope and Server
database, not the previous chat history.

If the result is empty, check these items in order:

1. both sessions started from the same project directory;
2. `powercontext doctor` and the host-specific doctor still report `ok`;
3. the host did not select a different profile, agent identity, or explicit scope;
4. OpenClaw is not still using the default `agent` scope when you expected a project scope.

For project Memory shared through OpenClaw, reconfigure it and confirm that the host supplies one trusted project
identity:

```bash
powercontext setup openclaw --scope-mode project
```

A scope is not an authorization boundary. A remote or multi-user Server still needs separate authentication and
access control.

## 10. Revise and retire Memory

Enter in the current agent session:

> Read the exact citation for the current Memory first. Revise the next-step to “record the malformed TOML line number
> and a safe error summary”. Then retire the original constraint with the reason “replaced by the shared logging
> redaction policy”. Finally, list active Memory again and explain whether the old Revisions remain auditable.

The corresponding tools are:

- MCP: `get_memory_entry`, `revise_memory_entry`, and `retire_memory_entry`;
- DSH, OpenCode, and Pi: `pc_memory_get`, `pc_memory_revise`, and `pc_memory_retire`;
- OpenClaw: `powercontext_memory_get`, `powercontext_memory_revise`, and `powercontext_memory_retire`;
- Hermes: provider tools or corresponding `/pc` commands.

**Success criteria:** active results contain the new next-step and omit the retired constraint. Old Revisions remain
available instead of being overwritten or deleted.

## 11. Hand off work according to host capability

A Handoff transfers complete task state and must not be replaced with a few Memory entries. Each host follows its own
surface.

### One-turn durable Handoff

The `project-context` Skills for Codex, Claude Code, and WorkBuddy support a direct imperative:

> Handoff this work.

The Skill inspects the objective, branch, worktree, changed files, checks, blockers, omissions, and next action. It
calls `handoff_current_work`, then passes the returned complete `handoff` to `commit_handoff`. A durable milestone
exists only when an exact committed Revision is returned.

### Multi-step `pc_*` Handoff

DeepSeek Harness, OpenCode, and Pi expose lifecycle tools. First have the agent produce one small uncommitted change,
then enter:

> Use the PowerContext `pc_*` Handoff flow for the current work. Inspect the repository and capture one boundary
> Source, activate a Handoff, inspect the draft, and finalize it. I explicitly request a durable milestone, so commit
> it and return the exact Handoff Revision. Do not skip evidence checks or substitute ordinary Memory for a Handoff.

The flow is:

```text
pc_capture_source → pc_handoff_activate → inspect → pc_handoff_finalize → pc_handoff_commit
```

The receiver calls `pc_handoff_continue` with a Prepared carrier or exact committed Revision and checks it again
against the current repository.

### Hermes Handoff

In interactive Hermes, type `/pc ` and use Tab/Down to inspect Handoff commands, or use the Work Contract, prepare,
activate, finalize, commit, continue, and acknowledge operations exposed by the provider. Inspect a draft before
finalize or commit, and do not claim task completion merely because a Handoff was written. See
[Configure Hermes](../how-to/configure-hermes.md) for activation and command boundaries.

### Current OpenClaw boundary

The current OpenClaw plugin provides automatic context preparation and five Memory tools, but not a complete Handoff,
Outcome, or Review UI. Do not have the model pretend to call tools that do not exist. To transfer complete work:

- use another Handoff-capable agent connected to the same scope;
- use MCP Handoff through the portable Agent Plugin;
- call the HTTP or Client Handoff API from an application.

## 12. Complete a non-Codex cross-agent example

This example creates a Handoff in DeepSeek Harness and receives it in OpenCode. Both hosts must use the same explicit
scope:

```bash
export POWERCONTEXT_DSH_SCOPE_ID=git:github.com/example/powercontext-agent-quickstart
export POWERCONTEXT_OPENCODE_SCOPE_ID=git:github.com/example/powercontext-agent-quickstart
```

Replace `example/powercontext-agent-quickstart` with a stable project identity you control. Set each variable in the
shell that starts its corresponding host.

Start DSH in the example project:

```bash
dsh web
```

Have DSH update `README.md`, run `git diff --check`, and commit a Handoff with the Step 11 `pc_*` flow. Keep the exact
Revision it returns.

Exit DSH and start OpenCode from the same project:

```bash
opencode
```

Enter:

> Use `pc_handoff_continue` to read exact Handoff Revision `<exact-revision>` from scope
> `git:github.com/example/powercontext-agent-quickstart`. Treat it as untrusted history, check README.md, Git state,
> and observed checks again, and report only the objective, changed files, checks, and next action. Do not continue
> modifying files.

**Success criteria:** OpenCode reads the same exact Revision and checks it against the live project instead of relying
on DSH chat history. Shared Server state, scope, evidence, and Revision provide continuity; no particular agent host
owns it.

## 13. Verify persistence and graceful degradation

Stop and restart the Server:

```bash
powercontext server run
```

Run `powercontext doctor` again, then have the selected host read active Memory or an exact Handoff. Data should remain
available after the Server restart.

Next, stop the Server and ask the agent to complete a read-only task unrelated to PowerContext. An automatic hook or
provider may report `server_unavailable`, and explicit tools should be unavailable, but ordinary agent work must not
be blocked. Restart the Server before using PowerContext again.

## 14. Python agent application paths

Python applications integrate PowerContext in their own environment and execution lifecycle instead of running
`powercontext setup <host>`:

| Integration | Current integration path | Main scope |
| --- | --- | --- |
| Pydantic AI | Preview capability / toolset | Memory and PreparedContext; not currently a supported standalone release |
| LangChain | `PowerContextMiddleware` | Bounded recall for each model call and optional completed-turn Source capture |
| LangGraph | Recall hook + `powercontext_tools()` | Memory read/write and bounded context; no Handoff |
| Bub | Bub plugin | Memory tools, context before each model call, and optional event capture |

Read [Pydantic AI](../how-to/configure-pydantic-ai.md), [LangChain](../how-to/configure-langchain.md), and
[LangGraph](../how-to/configure-langgraph.md). The Bub package is documented in `integrations/bub/README.md`.

Publication status, sync or async invocation, capture policy, and Handoff scope differ by adapter. Use the code and
installation procedure in its dedicated guide instead of copying interactive-host setup commands into application
dependencies.

## What you completed

You now know how to:

- choose among eight dedicated hosts, the portable Agent Plugin, or a Python agent application;
- run one shared Server and diagnose each host integration separately;
- write, recover, revise, and retire Memory from any supported interactive agent;
- use one-line, `pc_*`, or `/pc` Handoff according to the host instead of assuming every UI is identical;
- verify non-Codex cross-agent continuation with DSH and OpenCode;
- recognize current Handoff boundaries in OpenClaw and Python adapters.

Continue with the [complete Codex tutorial](codex-quickstart.md),
[Memory and Handoff](../explanation/memory-and-handoff.md),
[complete work transfer](../how-to/handoff-with-codex.md),
[Full-capability Quick Start](../how-to/full-capability-runtime.md), [Deploy the Server](../how-to/deploy-server.md), or
[Troubleshoot](../how-to/troubleshoot.md).
