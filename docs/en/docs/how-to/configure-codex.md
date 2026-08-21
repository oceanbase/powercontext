---
title: Configure Codex
description: Install the PowerContext Codex plugin and control its local behavior.
---

# Configure Codex

## Install or refresh the plugin

Run:

```bash
powercontext setup codex --source oceanbase/powercontext --ref master
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
If the catalog contains multiple Workstreams, Codex first opens a native picker; one Workstream is selected
automatically when it is the only candidate. Codex binds the selected Workstream to the checkout, inspects the current
conversation and repository, assembles the objective, branch and worktree state, changed files, observed checks,
blockers, omissions, and next action, then calls `handoff_current_work` followed by `commit_handoff`. After a successful
commit, Codex reports the selected Workstream and exact Handoff Revision; the user does not need to fill in the Handoff
content or confirm the commit again.

`交接`, `交接当前工作`, and `commit a handoff` use the same behavior. To inspect the proposed content without writing,
ask to `preview the handoff without committing`; the Skill renders the proposed fields in chat and calls no write
tool. Discussing Handoff design or asking how it works does not authorize a write.

Codex resolves scope in this order: an explicit `POWERCONTEXT_CODEX_SCOPE_ID`, a Workstream scope persistently bound
to the current Git workspace, the normalized Git remote, and finally the project path. Later Codex sessions in the
same workspace reuse that scope.

The picker returns the Workstream's human-facing `work_id` and authoritative `scope_id`. The `project-context` Skill
passes that exact scope to the resolver's `--bind-workstream` operation and verifies the result. The binding lives in
`powercontext/codex-workspace.json` below the Git-private directory, outside the worktree and commits. A one-line
Handoff then continues the selected Workstream's Artifact lifecycle and creates the next Revision. If the MCP client
does not support native elicitation, the tool returns structured choices instead; the integration must still obtain an
explicit selection and must not choose silently.

The Hook calls `POST /v1/context/prepare` once before Codex analyzes the prompt. It requests an 8000-byte total budget,
strictly validates `powercontext.prepared-context.v1`, and injects the returned content unchanged. The Runtime labels
Memory-derived items as untrusted history, preserves exact citations, and owns final selection and rendering. Explicit
search remains available through the Client and MCP; it is not a second automatic recall step. Automatically injected
content and Handoffs are historical information. Codex must still check current code, user requests, and system
instructions before acting on them.

Memory stores durable, reusable decisions, constraints, and state. A Handoff temporarily transfers the current task to
another task, session, or model. It must be explicitly prepared, inspected, and delivered, rather than substituted with
a few Memory entries. Read [Memory and Handoff](../explanation/memory-and-handoff.md) for the boundary and
[Hand off work in Codex](handoff-with-codex.md) for the procedure.

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
export POWERCONTEXT_SERVER_AUTH_ENABLED=true
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

For a normal empty result or recall failure, the Hook writes a content-free JSON diagnostic to stderr. Outcomes include
`empty`, `authentication_failed`, `version_mismatch`, `server_unavailable`, and `invalid_response`. The event never
contains the query, scope, prepared content, citation, response body, or authorization value.
