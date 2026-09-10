---
status: official
title: Codex
description: Install the PowerContext Codex plugin and control its local behavior.
---

# Codex

`official`

## Install or refresh the plugin

First follow [Quick Start](../get-started/quickstart.md) to install this branch, generate configuration, and start the Server.
On the Codex machine, load the client settings and install the matching plugin:

```bash
set -a
. ./.env
set +a
powercontext setup codex
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

## Choose standard context text

Set `POWERCONTEXT_CODEX_CONTEXT_ASSEMBLY` to a JSON assembly object before starting Codex. This opts into
readable text with configurable Memory/Experience sections, limits, and metadata. See
[Prepare standard context text](../workflows/prepare-context-text.md) for a complete example and output rules.

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

After generating configuration with the wizard, load `.env` in the terminal that starts Codex.
It supplies the URL, Authorization, and selected Scope without exposing the Server's model API keys:

```bash
set -a
. ./.env
set +a
codex
```

For a planned new Scope, run the creation request in `.env.next-steps.md`, put the returned real `scope_id` in
`POWERCONTEXT_CODEX_SCOPE_ID` in the client file, reload it, and open a new session. The planned title is not an ID.
Without an explicit binding, Agents may share the Server default; changing project directories does not create isolation.
After an ordinary prompt, the plugin recalls from the bound Scope and captures the prompt as Source evidence.
The Server Scheduler processes new Sources at the configured interval.

## Check both Hook and MCP connections

The Hook derives its Server URL from the installed plugin's `.mcp.json`, which MCP also reads.
Both default to `http://127.0.0.1:8000`. For a custom port, SSH forwarding, or HTTPS, update that shared file.
It takes precedence over `POWERCONTEXT_CODEX_SERVER_URL`; exporting that variable alone does not change the endpoint.
`setup codex` does not update the MCP URL automatically. Follow the generated `.env.next-steps.md` configuration
and retain this authentication form:

```json
{
  "mcpServers": {
    "powercontext": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp",
      "required": false,
      "env_http_headers": {
        "Authorization": "POWERCONTEXT_CODEX_AUTHORIZATION"
      }
    }
  }
}
```

Replace the URL with your actual MCP endpoint and preserve other servers in the file. Read the token from the process
environment rather than hard-coding it in JSON. The Hook binds the Scope and injects it into MCP data operations;
a planned title or directory name is not a Scope ID.

Desktop apps may not inherit terminal environment variables; loading the file does not configure an already running
desktop app. Restart the host you actually use, then check Hook capture and MCP separately. An MCP connected status
does not prove Source capture. Complete the [Source, topic evolution, and cross-session recall check](../get-started/quickstart.md#4-verify-topic-memory-with-ordinary-conversation).

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `POWERCONTEXT_CODEX_ALLOW_INSECURE_HTTP` | `false` | Explicitly permit non-loopback plaintext HTTP for hooks |
| `POWERCONTEXT_CODEX_SCOPE_ID` | unset | Explicitly select an existing Scope instead of resolving bindings and the Server default |
| `POWERCONTEXT_CODEX_AUTHORIZATION` | unset | Complete `Bearer <token>` header for Hook and MCP requests |
| `POWERCONTEXT_CODEX_CAPTURE_PROMPTS` | `true` | Capture user prompts as Source evidence |
| `POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE` | `false` | Wait for Source processing after capture |
| `POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS` | `1` | Per-request hook timeout |
| `POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS` | `4` | Shared hook HTTP budget |
| `POWERCONTEXT_CODEX_FLUSH_MAX_CALLS` | `4` | Maximum flush calls per prompt |

Hooks allow loopback HTTP by default; remote HTTP requires explicit consent, and HTTPS certificate validation stays
enabled. Setup saves consent and updates the installed plugin's `.mcp.json`, which supplies the URL for both hooks
and native MCP. Changing only a Hook URL variable does not change that native endpoint; rerun setup if an upgrade
replaces `.mcp.json`. Codex's own MCP policy still applies. See
[Connect to a remote Server](../operate/connect-remote-server.md).

The outer Codex hook timeout is ten seconds. Recall, capture, and flush fail independently and never block Codex when
the Server is unavailable or rejects authentication. Without an explicit Scope, the plugin resolves the Session
binding, workspace binding, then Server default. Configuration variables must be present in the environment that
starts Codex; restart Codex after changing them.
