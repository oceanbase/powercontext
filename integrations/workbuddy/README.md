# PowerContext integration for WorkBuddy

This directory contains a thin WorkBuddy integration backed by a running
PowerContext server. It does not embed storage or start the server. WorkBuddy
keeps the user interface and agent orchestration while PowerContext provides
external storage, retrieval, context preparation, Memory, and Handoff lifecycle
operations.

The integration has three capability layers:

- a `UserPromptSubmit` hook asks the Runtime to prepare one final, bounded
  context value before WorkBuddy analyzes the prompt, then independently
  captures the prompt as Source evidence;
- Streamable HTTP MCP at `http://127.0.0.1:8000/mcp` gives WorkBuddy explicit
  Memory and work-continuity tools (`search_memory`, `list_memory_entries`,
  `handoff_current_work`, `commit_handoff`, and so on);
- the `project-context` Skill turns an imperative such as `交接`,
  `交接当前工作`, or `handoff this work` into one durable, committed Handoff,
  and restores project memory for continued work.

The hooks driver is pure Python 3.11+ standard library and needs no extra
dependencies. WorkBuddy support for a `powercontext setup workbuddy` CLI
installer is planned; until then, install the plugin manually below.

## Install with the PowerContext CLI

Not yet available. `powercontext setup workbuddy` is planned for a future
release. Use the manual installation instructions below.

<details>
<summary>Manual installation (alternative)</summary>

### Manual installation

These steps copy the plugin into the WorkBuddy user directory, register the
hook and the MCP server, install the Skill, and verify the integration.

#### 1. Copy the plugin files

WorkBuddy loads hook commands from its user-level hooks directory. Copy the
hook driver, its settings modules, and the scope resolver there. This guide
uses `~/.workbuddy/hooks` as the hooks directory; replace it with your own
location and use the same value wherever `<WORKBUDDY_HOOKS_DIR>` appears below.

```bash
PLUGIN=integrations/workbuddy/plugins/powercontext
WORKBUDDY_HOOKS_DIR="${WORKBUDDY_HOOKS_DIR:-$HOME/.workbuddy/hooks}"

mkdir -p "$WORKBUDDY_HOOKS_DIR"
cp "$PLUGIN"/hooks/workbuddy_powercontext_hook.py \
   "$PLUGIN"/hooks/workbuddy_settings.py \
   "$PLUGIN"/hooks/prepared_context.py \
   "$WORKBUDDY_HOOKS_DIR"/
cp -R "$PLUGIN/scripts" "$WORKBUDDY_HOOKS_DIR"/
```

The resulting layout is:

```text
<WORKBUDDY_HOOKS_DIR>/
  workbuddy_powercontext_hook.py
  workbuddy_settings.py
  prepared_context.py
  scripts/
    project_scope.py
```

#### 2. Register the hook

Merge the following `hooks` block into `~/.workbuddy/settings.json`. Replace
`<WORKBUDDY_HOOKS_DIR>` with the absolute path of your hooks directory (for
example `/Users/<you>/.workbuddy/hooks`). The command string cannot expand
environment variables, so the literal path is required here.

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 <WORKBUDDY_HOOKS_DIR>/workbuddy_powercontext_hook.py",
            "timeout": 10,
            "statusMessage": "Syncing PowerContext"
          }
        ]
      }
    ]
  }
}
```

A complete sample is included at
`plugins/powercontext/hooks/hooks.workbuddy.json`.

#### 3. Register the MCP server

Merge the following `mcpServers` entry into `~/.workbuddy/mcp.json`:

```json
{
  "mcpServers": {
    "powercontext": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp",
      "headers": {},
      "description": "PowerContext agent memory & handoff MCP server (local service on port 8000)"
    }
  }
}
```

#### 4. Install the Skill

Copy the `project-context` Skill into the WorkBuddy skills directory:

```bash
mkdir -p ~/.workbuddy/skills
cp -R integrations/workbuddy/plugins/powercontext/skills/project-context \
  ~/.workbuddy/skills/
```

Then open `~/.workbuddy/skills/project-context/SKILL.md` and replace every
`${WORKBUDDY_HOOKS_DIR}` placeholder with the absolute path of your hooks
directory, or export `WORKBUDDY_HOOKS_DIR` in the shell environment that starts
WorkBuddy.

#### 5. Start the Server, restart WorkBuddy, and verify

Keep the PowerContext Server running in one terminal:

```bash
powercontext server run
```

Restart WorkBuddy so it picks up the new hook, MCP server, and Skill. Send any
prompt; the hook reports `Syncing PowerContext` while it runs. To verify the
recall contract directly, inspect the Server logs or run:

```bash
powercontext doctor
```

The MCP tools (`search_memory` and the Handoff tools) become available in the
WorkBuddy session when the Server is reachable.

</details>

## Configuration

The hook uses `http://127.0.0.1:8000` by default. Environment variables
override the defaults; restart WorkBuddy after changing them.

| Variable | Purpose |
| --- | --- |
| `POWERCONTEXT_WORKBUDDY_SERVER_URL` | PowerContext server URL (default `http://127.0.0.1:8000`). |
| `POWERCONTEXT_WORKBUDDY_AUTHORIZATION` | Complete authorization header, e.g. `Bearer <token>` |
| `POWERCONTEXT_WORKBUDDY_SCOPE_ID` | Explicit scope or scope template override |
| `POWERCONTEXT_WORKBUDDY_CAPTURE_PROMPTS` | Capture user prompts as Sources (default `true`) |
| `POWERCONTEXT_WORKBUDDY_FLUSH_ON_CAPTURE` | Flush until the captured Source is processed (testing only, default `false`) |
| `POWERCONTEXT_WORKBUDDY_REQUEST_TIMEOUT_SECONDS` | Per-request HTTP timeout (default `1.0`) |
| `POWERCONTEXT_WORKBUDDY_HTTP_BUDGET_SECONDS` | Shared wall-clock budget for one prompt (default `4.0`) |
| `POWERCONTEXT_WORKBUDDY_FLUSH_MAX_CALLS` | Maximum flush calls (default `4`) |

The hook validates its PowerContext MCP URL and derives the HTTP API base by
removing the final `/mcp` path segment. Change `plugins/powercontext/.mcp.json`
before installing when the loopback default is not appropriate. MCP URLs cannot
contain credentials, query strings, or fragments; plain HTTP is accepted only
for loopback hosts.

## Runtime behavior

- Recall calls `POST /v1/context/prepare` once per prompt, requests an
  8000-byte total budget, strictly validates `powercontext.prepared-context.v1`,
  and injects the returned content unchanged as untrusted history.
- Capture independently posts the prompt to `POST /v1/sources/content` with
  stable, content-addressed `source_id` values.
- Recall, capture, and flush fail independently. An unavailable Server never
  blocks normal WorkBuddy work.
- For an empty result, authentication failure, version mismatch, unavailable
  Server, or invalid response, the hook writes one diagnostic JSON line to
  stderr. Diagnostics contain status and byte counts only—never the query,
  scope, content, citation, response body, or authorization value.

## Authentication

Optional local bearer authentication uses `POWERCONTEXT_WORKBUDDY_AUTHORIZATION`,
whose value must be a complete `Bearer <token>` header. `.mcp.json` keeps an
empty `headers` object; the hook reads the same value from the environment.
Missing or empty values preserve the default unauthenticated flow. Never put
the token in `.mcp.json`, the Server URL, or a static MCP header.

## Manual uninstallation

1. Remove the `UserPromptSubmit` PowerContext entry from `~/.workbuddy/settings.json`.
2. Remove the `powercontext` entry from `~/.workbuddy/mcp.json`.
3. Remove the hook files and the scope resolver from `<WORKBUDDY_HOOKS_DIR>`.
4. Remove `~/.workbuddy/skills/project-context`.
5. Optionally stop the Server and delete its local data directory.
