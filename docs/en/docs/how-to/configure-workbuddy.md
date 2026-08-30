---
title: Configure WorkBuddy
description: Install the PowerContext WorkBuddy hooks and control its local behavior.
---

# Configure WorkBuddy

## Prerequisites

- A running PowerContext installation. Install the CLI and local Server from the same `master` revision used below:
  `uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"`.
  Start the Server with `powercontext server run`.
- WorkBuddy with user-level hooks, MCP, and Skills support (the desktop app).
- Python 3.11 or newer on `PATH` for the hook process.
- The plugin directory from this repository: `integrations/workbuddy/plugins/powercontext`.

The integration does not start or embed the Server; it only talks to a running
PowerContext Server over HTTP.

## Install with the PowerContext CLI

The CLI installs the hooks, MCP server, and Skill from a local checkout or a
GitHub source in one step:

```bash
powercontext setup workbuddy --source oceanbase/powercontext --ref master
```

For a local checkout, point `--source` at the repository root or the plugin
directory:

```bash
powercontext setup workbuddy --source /path/to/powercontext
```

The installer writes the hook driver and scope resolver to `~/.workbuddy/hooks`,
merges the `UserPromptSubmit` hook into `~/.workbuddy/settings.json`, registers
the `powercontext` server in `~/.workbuddy/mcp.json`, and installs the
`project-context` Skill under `~/.workbuddy/skills`. Existing settings and other
MCP servers are preserved, and the Skill's command placeholders are resolved
automatically.

Verify the installation with:

```bash
powercontext doctor workbuddy
```

Then keep the Server running and restart WorkBuddy:

```bash
powercontext server run
```

## Manual installation (alternative)

You can also install the plugin manually. The examples use `~/.workbuddy/hooks`
as the WorkBuddy hooks directory; replace it with your own location and use the
same value wherever `<WORKBUDDY_HOOKS_DIR>` appears below.

### 1. Copy the plugin files

```bash
PLUGIN=integrations/workbuddy/plugins/powercontext
WORKBUDDY_HOOKS_DIR="${WORKBUDDY_HOOKS_DIR:-$HOME/.workbuddy/hooks}"

mkdir -p "$WORKBUDDY_HOOKS_DIR"
cp "$PLUGIN"/hooks/workbuddy_powercontext_hook.py \
   "$PLUGIN"/hooks/workbuddy_settings.py \
   "$PLUGIN"/hooks/prepared_context.py \
   "$WORKBUDDY_HOOKS_DIR"/
cp "$PLUGIN/scripts/project_scope.py" \
   "$WORKBUDDY_HOOKS_DIR/powercontext_project_scope.py"
```

### 2. Register the hook

Merge the following `hooks` block into `~/.workbuddy/settings.json`. Replace
`<POWERCONTEXT_PYTHON>` with the Python executable that can import PowerContext,
and `<WORKBUDDY_HOOKS_DIR>` with the absolute path of your hooks directory; the
command string cannot expand environment variables.

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"<POWERCONTEXT_PYTHON>\" \"<WORKBUDDY_HOOKS_DIR>/workbuddy_powercontext_hook.py\"",
            "timeout": 10,
            "statusMessage": "Syncing PowerContext"
          }
        ]
      }
    ]
  }
}
```

### 3. Register the MCP server

Merge the following `mcpServers` entry into `~/.workbuddy/mcp.json`:

```json
{
  "mcpServers": {
    "powercontext": {
      "type": "http",
      "url": "${POWERCONTEXT_WORKBUDDY_SERVER_URL:-http://127.0.0.1:8000}/mcp",
      "headers": {
        "Authorization": "${POWERCONTEXT_WORKBUDDY_AUTHORIZATION:-}"
      },
      "description": "PowerContext agent memory & handoff MCP server (local service on port 8000)"
    }
  }
}
```

### 4. Install the Skill

```bash
mkdir -p ~/.workbuddy/skills
cp -R integrations/workbuddy/plugins/powercontext/skills/project-context \
  ~/.workbuddy/skills/
cat > ~/.workbuddy/skills/project-context/.powercontext.json <<'EOF'
{"schema": 1, "owner": "powercontext", "integration": "workbuddy"}
EOF
```

Then replace `${POWERCONTEXT_PYTHON}` in
`~/.workbuddy/skills/project-context/SKILL.md` with a shell-safe Python
executable argument. Replace `${POWERCONTEXT_PROJECT_SCOPE_SCRIPT}` with a
shell-safe complete path to
`<WORKBUDDY_HOOKS_DIR>/powercontext_project_scope.py`.

### 5. Start the Server, restart WorkBuddy, and verify

```bash
powercontext server run
```

Restart WorkBuddy so it discovers the new hook, MCP server, and Skill. Send any
prompt; the hook reports `Syncing PowerContext` while it runs. Verify the
installation with:

```bash
powercontext doctor
```

The MCP tools (`search_memory` and the Handoff tools) appear in the WorkBuddy
session when the Server is reachable.

## Understand automatic recall, Memory, and Handoff

The integration has two paths to the same Server:

- a `UserPromptSubmit` hook asks the Runtime to prepare one final, bounded
  context value before WorkBuddy analyzes the prompt, then independently
  captures the prompt as Source evidence;
- MCP gives WorkBuddy explicit tools to read and maintain Memory, plus an
  explicit Handoff workflow.

The `project-context` Skill binds the two paths together. An imperative such as
`交接`, `交接当前工作`, or `handoff this work` is treated as explicit
authorization to create one durable Handoff milestone. The Skill inspects the
current conversation and repository, calls `handoff_current_work`, then
immediately commits the returned `handoff` member through `commit_handoff`.
Preview or design requests remain read-only.

The Hook calls `POST /v1/context/prepare` once per prompt, requests an
8000-byte total budget, strictly validates `powercontext.prepared-context.v1`,
and injects the returned content unchanged. The Runtime labels Memory-derived
items as untrusted history, preserves exact citations, and owns final selection
and rendering. Automatically injected content and Handoffs are historical
information. WorkBuddy must still check current code, user requests, and system
instructions before acting on them.

Memory stores durable, reusable decisions, constraints, and state. A Handoff
temporarily transfers the current task to another task, session, or model. It
must be explicitly prepared, inspected, and delivered, rather than substituted
with a few Memory entries. Read
[Memory and Handoff](../explanation/memory-and-handoff.md) for the boundary.

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
| `POWERCONTEXT_WORKBUDDY_AUTHORIZATION` | Complete authorization header, e.g. `Bearer <token>` |
| `POWERCONTEXT_WORKBUDDY_SCOPE_ID` | Explicit scope or scope template override |
| `POWERCONTEXT_WORKBUDDY_CAPTURE_PROMPTS` | Capture user prompts as Sources (default `true`) |
| `POWERCONTEXT_WORKBUDDY_FLUSH_ON_CAPTURE` | Flush until the captured Source is processed (testing only, default `false`) |
| `POWERCONTEXT_WORKBUDDY_REQUEST_TIMEOUT_SECONDS` | Per-request HTTP timeout (default `1.0`) |
| `POWERCONTEXT_WORKBUDDY_HTTP_BUDGET_SECONDS` | Shared wall-clock budget for one prompt (default `4.0`) |
| `POWERCONTEXT_WORKBUDDY_FLUSH_MAX_CALLS` | Maximum flush calls (default `4`) |

The hook validates its PowerContext MCP URL and derives the HTTP API base by
removing the final `/mcp` path segment. MCP URLs cannot contain credentials,
query strings, or fragments; plain HTTP is accepted only for loopback hosts.

## Resolve the project scope

WorkBuddy resolves scope in this order:

1. an explicit `POWERCONTEXT_WORKBUDDY_SCOPE_ID`;
2. a Workstream scope persistently bound to the current Git workspace (stored
   in `powercontext/codex-workspace.json` below the Git-private directory,
   shared with the Codex and Claude Code plugins);
3. the normalized Git remote;
4. a hash of the resolved local project directory.

Later WorkBuddy sessions in the same workspace reuse that scope. The
`project-context` Skill's `--bind-workstream` operation persists a Workstream
binding for a selected Handoff; the binding never enters the worktree or
commits.

## Connect to an authenticated local Server

Load one token from your local secret manager, then start the Server with
authentication enabled:

```bash
export POWERCONTEXT_SERVER_AUTH_ENABLED=true
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
| Hook timeout (10 s) | WorkBuddy continues; the hook process is stopped by the outer hook timeout |

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
3. Remove the hook files and the scope resolver from `<WORKBUDDY_HOOKS_DIR>`.
4. Remove `~/.workbuddy/skills/project-context`.
5. Optionally stop the Server and delete its local data directory.
