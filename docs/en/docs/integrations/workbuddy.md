---
status: community
title: WorkBuddy
description: Install the PowerContext WorkBuddy hooks and control its local behavior.
---

# WorkBuddy

`community`

## Prerequisites

- A running PowerContext Server. Start a local Server with `powercontext server run`.
- An installed PowerContext client and CLI, plus `uvx` and `npx` on `PATH`.
- WorkBuddy with user-level hooks, MCP, and Skills support (the desktop app).
- The installed client's `powercontext-hook` executable on WorkBuddy's `PATH`.

The integration does not start or embed the Server; it only talks to a running
PowerContext Server over HTTP.

## Install with the PowerContext CLI

WorkBuddy supports `setup select` and `doctor integrations`, as well as the dedicated commands below.

The CLI installs the hooks, MCP server, and Skill from a local checkout or a
GitHub source in one step:

```bash
powercontext setup workbuddy
```

For a local checkout, point `--source` at the repository root:

```bash
powercontext setup workbuddy --source /path/to/powercontext
```

The installer writes the hook driver and scope resolver to `~/.workbuddy/hooks`,
merges the `UserPromptSubmit` hook into `~/.workbuddy/settings.json`, registers
the `powercontext` server in `~/.workbuddy/mcp.json`, and installs the
`powercontext-project-context` Skill under `~/.workbuddy/skills`. Existing settings and other
MCP servers are preserved. Skills and MCP resources are generated from the Agent Plugin baseline;
host rules load from the selected repository source without a separate management installation.

Verify the installation with:

```bash
powercontext doctor workbuddy
```

Then keep the Server running and restart WorkBuddy:

```bash
powercontext server run
```

## Understand automatic recall, Memory, and Handoff

The integration has two paths to the same Server:

- a `UserPromptSubmit` hook asks the Runtime to prepare one final, bounded
  context value before WorkBuddy analyzes the prompt, then independently
  captures the prompt as Source evidence;
- MCP gives WorkBuddy explicit tools to read and maintain Memory, plus an
  explicit Handoff workflow.

The generated `powercontext-project-context` Skill follows the Agent Plugin baseline. For a requested transfer,
inspect current facts, call `handoff_current_work`, and return the complete temporary carrier. Call `commit_handoff`
only for an explicitly requested durable milestone. Preview or design requests remain read-only.

The Hook calls `POST /v1/context/prepare` once per prompt, requests an
8000-byte total budget, strictly validates `powercontext.prepared-context.v1`,
and injects the returned content with the resolved Scope for explicit MCP calls. The Runtime labels Memory-derived
items as untrusted history, preserves exact citations, and owns final selection
and rendering. Automatically injected content and Handoffs are historical
information. WorkBuddy must still check current code, user requests, and system
instructions before acting on them.

Memory stores durable, reusable decisions, constraints, and state. A Handoff
temporarily transfers the current task to another task, session, or model. It
must be explicitly prepared, inspected, and delivered, rather than substituted
with a few Memory entries. Read
[Memory and Handoff](../workflows/memory-and-handoff.md) for the boundary.

## Control prompt capture

Prompt capture is enabled by default. Disable it before restarting WorkBuddy
when the current work must not be recorded:

```bash
export POWERCONTEXT_WORKBUDDY_CAPTURE_PROMPTS=false
```

Captured prompts become Source evidence. Turning capture on does not guarantee
automatic Memory extraction; that requires a configured generation model.
Explicit `remember_memory` calls do not require a model.

For testing only, make the hook wait for captured Source processing:

```bash
export POWERCONTEXT_WORKBUDDY_FLUSH_ON_CAPTURE=true
```

This adds inference latency to each prompt and is not the normal interactive
setting.

## Configuration

Environment variables override the hook defaults; restart WorkBuddy after
changing them.

| Variable | Purpose |
| --- | --- |
| `POWERCONTEXT_WORKBUDDY_SERVER_URL` | PowerContext server URL (default `http://127.0.0.1:8000`) |
| `POWERCONTEXT_WORKBUDDY_ALLOW_INSECURE_HTTP` | Explicitly permit non-loopback plaintext HTTP for hooks (default `false`) |
| `POWERCONTEXT_WORKBUDDY_AUTHORIZATION` | Complete authorization header, e.g. `Bearer <token>` |
| `POWERCONTEXT_WORKBUDDY_SCOPE_ID` | Explicit server-owned Scope ID |
| `POWERCONTEXT_WORKBUDDY_CAPTURE_PROMPTS` | Capture user prompts as Sources (default `true`) |
| `POWERCONTEXT_WORKBUDDY_FLUSH_ON_CAPTURE` | Flush until the captured Source is processed (testing only, default `false`) |
| `POWERCONTEXT_WORKBUDDY_REQUEST_TIMEOUT_SECONDS` | Per-request HTTP timeout (default `3.0`) |
| `POWERCONTEXT_WORKBUDDY_HTTP_BUDGET_SECONDS` | Shared wall-clock budget for one prompt (default `6.0`) |
| `POWERCONTEXT_WORKBUDDY_FLUSH_MAX_CALLS` | Maximum flush calls (default `4`) |

The hook validates its PowerContext MCP URL and derives the HTTP API base by
removing the final `/mcp` path segment. MCP URLs cannot contain credentials,
query strings, or fragments. Plain HTTP is allowed on loopback by default; non-loopback HTTP requires
`POWERCONTEXT_WORKBUDDY_ALLOW_INSECURE_HTTP=true`. Setup configures the Hook and native MCP URL, but WorkBuddy's
own MCP policy still applies. HTTPS certificate validation stays enabled. See
[Connect to a remote Server](../operate/connect-remote-server.md).

## Resolve the project scope

The Server resolves Scope for WorkBuddy in this order:

1. an explicit `POWERCONTEXT_WORKBUDDY_SCOPE_ID`;
2. a durable session binding;
3. a durable workspace binding;
4. the Server's default Scope.

Later WorkBuddy sessions in the same workspace reuse that Scope. Explicit binding changes use the Server
Scope binding operations. The workspace path is hashed only as an external binding
key; the plugin never derives a Scope ID from it.

## Connect to an authenticated local Server

Load one token from your local secret manager, then start the Server with
authentication enabled:

```bash
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

Start WorkBuddy from an environment that contains the matching complete
Authorization header:

```bash
export POWERCONTEXT_WORKBUDDY_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
```

Restart WorkBuddy after changing the variable. The prompt Hook reads this value
from the environment. `.mcp.json` stores only the
`${POWERCONTEXT_WORKBUDDY_AUTHORIZATION:-}` template, which WorkBuddy expands
from the same environment; the token itself remains outside the file. Do not put
the token in `.mcp.json` or the Server URL.

When the variable is absent or empty and Server authentication is disabled, the
plugin behaves exactly as it does by default. When Server authentication is
enabled but the header is missing or incorrect, the Hook fails open and emits an
`authentication_failed` diagnostic; MCP tools remain unavailable without
blocking the WorkBuddy session.

## Failure behavior

| Scenario | Behavior |
| --- | --- |
| Server unavailable | Hook recall and capture fail open; the prompt proceeds without injected context. MCP tools report that the service is unavailable |
| Authentication failure | Hook fails open and emits an `authentication_failed` diagnostic; MCP tools remain unavailable |
| Empty prepared context | No context is injected; the hook emits an `empty` diagnostic |
| Version mismatch | Hook fails open and emits a `version_mismatch` diagnostic |
| Invalid or oversized response | Hook fails open and emits an `invalid_response` diagnostic; nothing is injected |
| Hook timeout (30 s) | WorkBuddy continues; the hook process is stopped by the outer hook timeout |

Recall, capture, and flush fail independently. An unavailable Server never
blocks normal WorkBuddy work.

## Diagnostics

For a normal empty result or recall failure, the Hook writes one content-free
JSON diagnostic to stderr. Outcomes include `empty`, `authentication_failed`,
`version_mismatch`, `server_unavailable`, and `invalid_response`. The event
never contains the query, scope, prepared content, citation, response body, or
authorization value.

During each prompt you should see the hook's `Syncing PowerContext` status
message. Verify the whole installation with `powercontext doctor`.

## Uninstall

1. Remove the `UserPromptSubmit` PowerContext entry from `~/.workbuddy/settings.json`.
2. Remove the `powercontext` entry from `~/.workbuddy/mcp.json`.
3. Remove the hook files and the scope resolver from `~/.workbuddy/hooks`.
4. Remove `~/.workbuddy/skills/powercontext-project-context`.
5. Optionally stop the Server and delete its local data directory.
