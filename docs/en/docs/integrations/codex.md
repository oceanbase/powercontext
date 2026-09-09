---
status: official
title: Codex
description: Install the PowerContext Codex plugin and control its local behavior.
---

# Codex

`official`

## Install or refresh the plugin

Run:

```bash
powercontext setup codex --source oceanbase/powercontext --ref master
powercontext doctor codex
```

The command adds the repository as a Codex marketplace, installs the PowerContext plugin, and creates the user data
directory. It is safe to run again. Pass the same `--ref` used to install the PowerContext tool.

Open a new Codex session after setup. Use `/hooks` to inspect and, when prompted, trust the PowerContext
`UserPromptSubmit` hook.

## Understand automatic recall, Memory, and Handoff

The plugin has two paths to the same Server:

- a prompt hook asks the Runtime to prepare one final, bounded context value, then independently captures the
  user's prompt as Source evidence;
- MCP gives Codex explicit tools to read and maintain Memory, plus an explicit Handoff workflow.

## Hand off the current work in one turn

In a Codex session with the plugin installed and the PowerContext Server available, enter:

```text
handoff this work
```

The `project-context` Skill treats that imperative as explicit authorization to create one durable Handoff milestone.
Codex inspects the current conversation and repository, assembles the objective, branch and worktree state, changed
files, observed checks, blockers, omissions, and next action, then calls `handoff_current_work` followed by
`commit_handoff` in the current Session Scope. After a successful commit, Codex reports the exact Handoff Revision; the
user does not need to fill in the Handoff content or confirm the commit again.

`交接`, `交接当前工作`, and `commit a handoff` use the same behavior. To inspect the proposed content without writing,
ask to `preview the handoff without committing`; the Skill renders the proposed fields in chat and calls no write
tool. Discussing Handoff design or asking how it works does not authorize a write.

At Session start, Codex resolves Scope in this order: an explicit `POWERCONTEXT_CODEX_SCOPE_ID`, an existing Session
binding, a host-managed workspace binding, and the Server's default Scope. The selected Scope is fixed to the Session.
Repository and directory identities are lookup inputs only; they never generate a Scope ID. The prompt hook uses the
binding for recall and capture, while `PreToolUse` injects it into data-plane tools so Agent input cannot redirect a
read or write. The host must create or bind a different Scope when the Session changes work boundaries.

The Hook calls `POST /v1/context/prepare` once before Codex analyzes the prompt. It requests an 8000-byte total budget,
strictly validates `powercontext.prepared-context.v1`, and injects the returned content unchanged. The Runtime labels
Memory-derived items as untrusted history, preserves exact citations, and owns final selection and rendering. Explicit
search remains available through the Client and MCP; it is not a second automatic recall step. Automatically injected
content and Handoffs are historical information. Codex must still check current code, user requests, and system
instructions before acting on them.

Memory stores durable, reusable decisions, constraints, and state. A Handoff temporarily transfers the current task to
another task, session, or model. It must be explicitly prepared, inspected, and delivered, rather than substituted with
a few Memory entries. Read [Memory and Handoff](../workflows/memory-and-handoff.md) for the boundary and
[Hand off work in Codex](../workflows/handoff-with-codex.md) for the procedure.

## Control prompt capture

Prompt capture is enabled by default. Disable it before starting Codex when the current work must not be recorded:

```bash
export POWERCONTEXT_CODEX_CAPTURE_PROMPTS=false
codex
```

Captured prompts become Source evidence. Turning capture on does not guarantee automatic Memory extraction; that
requires a configured generation model. Explicit `remember_memory` calls do not require a model.

For testing only, make the hook wait for captured Source processing:

```bash
export POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE=true
```

This adds inference latency to each prompt and is not the normal interactive setting.

## Connect to an authenticated local Server

Load one token from your local secret manager, then start the Server with authentication enabled:

```bash
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

Start Codex from an environment that contains the matching complete Authorization header:

```bash
export POWERCONTEXT_CODEX_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
codex
```

Restart Codex after changing the variable. The plugin's MCP configuration reads this optional header from the
environment, and the prompt Hook reads the same value. Do not put the token in `.mcp.json`, the Server URL, or a
static MCP header.

When the variable is absent or empty and Server authentication is disabled, the plugin behaves exactly as it does by
default. When Server authentication is enabled but the header is missing or incorrect, the Hook fails open and emits
an `authentication_failed` diagnostic; MCP tools remain unavailable without blocking the Codex session.

If the Server is unavailable, hook recall and capture fail open. Codex work continues, and explicit Memory tools
report that the service is unavailable.

For a normal empty result or recall failure, the Hook emits a content-free JSON diagnostic. Failure outcomes are
returned through the top-level `systemMessage` in the successful stdout hook response; `empty` remains a local
diagnostic. Outcomes include `empty`, `authentication_failed`, `version_mismatch`, `server_unavailable`, and
`invalid_response`. The event never contains the query, scope, prepared content, citation, response body, or
authorization value.

## Use a generated environment file

If you generated `.env` with [Enable extraction and vector search](../get-started/configure-models.md), install the plugin
using the command printed by Config Generator, then load the file in the terminal that starts Codex:

```bash
set -a
. ./.env
set +a
codex
```

Leave `POWERCONTEXT_CODEX_SCOPE_ID` unset for normal sessions; set it only to select a known existing Scope explicitly.
After an ordinary prompt, the plugin recalls from the bound Scope and captures the prompt as Source evidence.
The Server Scheduler processes new Sources at the configured interval.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `POWERCONTEXT_CODEX_SCOPE_ID` | unset | Explicitly select an existing Scope instead of resolving bindings and the Server default |
| `POWERCONTEXT_CODEX_AUTHORIZATION` | unset | Complete `Bearer <token>` header for Hook and MCP requests |
| `POWERCONTEXT_CODEX_CAPTURE_PROMPTS` | `true` | Capture user prompts as Source evidence |
| `POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE` | `false` | Wait for Source processing after capture |
| `POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS` | `1` | Per-request hook timeout |
| `POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS` | `4` | Shared hook HTTP budget |
| `POWERCONTEXT_CODEX_FLUSH_MAX_CALLS` | `4` | Maximum flush calls per prompt |

The outer Codex hook timeout is ten seconds. Recall, capture, and flush fail independently and never block Codex when
the Server is unavailable or rejects authentication. Without an explicit Scope, the plugin resolves the Session
binding, workspace binding, then Server default. Configuration variables must be present in the environment that
starts Codex; restart Codex after changing them.
