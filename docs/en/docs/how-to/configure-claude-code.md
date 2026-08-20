---
title: Configure Claude Code
description: Install the PowerContext Claude Code plugin and configure recall, prompt capture, and authentication.
---

# Configure Claude Code

## Check prerequisites

Install PowerContext and Claude Code first, and make sure both commands are available in the environment that will
run setup:

```bash
powercontext --version
claude --version
```

Use the same PowerContext repository ref for the Python package and plugin. The Hook validates a versioned Prepared
Context contract, so mixing an older Server with a newer plugin can disable recall without blocking Claude Code.

## Install or update the plugin

Run:

```bash
powercontext setup claude-code --source oceanbase/powercontext --ref master
```

Before changing Claude Code settings, setup reports the settings entry, plugin cache, persistent data location,
required permissions, and exact rollback commands. It then registers the Marketplace, installs the plugin at user
scope, and verifies the enabled plugin through Claude Code's JSON output.

Claude Code owns the Marketplace registry, versioned plugin cache, and plugin data directory. Claude 2.1.133 does not
accept configuration flags on `plugin install`, so setup atomically merges `server_url` and `capture_prompts` into
the user-level `pluginConfigs` after installation while preserving unrelated settings; on failure it restores the
pre-install snapshot. PowerContext resolves these locations from `CLAUDE_CONFIG_DIR` or Claude Code's default
configuration directory.

For a local checkout, pass its directory:

```bash
powercontext setup claude-code --source ./powercontext
```

Start the Server and open a new Claude Code session after installation:

```bash
powercontext server run
claude
```

Use `/hooks` to confirm the `UserPromptSubmit` Hook and `/mcp` to confirm the `powercontext` Server.

Running setup again updates the plugin configuration and verifies the installed version. It does not remove existing
PowerContext Server data.

## Understand the plugin behavior

For each user prompt, the Hook:

1. derives the same project scope as the Codex integration;
2. calls `POST /v1/context/prepare` at most once;
3. strictly validates `powercontext.prepared-context.v1` and injects it unchanged through `additionalContext`;
4. independently captures the prompt as ordinary Content Source evidence.

The Source pipeline may later extract Memory when a generation model is configured. Prompt capture does not call
`remember_memory`, and the Hook never labels an ordinary prompt as `task-outcome`.

The plugin does not install a `Stop` Hook in v1. It does not read the transcript or automatically capture Claude's
final response. Memory writes and durable Handoff milestones remain explicit MCP operations guided by the bundled
Skill.

Scope resolution uses this order:

1. `POWERCONTEXT_CLAUDE_SCOPE_ID`, when explicitly set;
2. the Git-private Workstream binding shared with Codex;
3. the normalized `remote.origin.url` of the Git top-level directory;
4. a `local:sha256:<digest>` identifier derived from the resolved project directory.

Claude Code and Codex therefore resolve the exact same scope in a checkout that has a Workstream binding. Without a
binding they still share the normalized remote scope. Bind a known Workstream with the bundled resolver:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/project_scope.py" \
  --cwd "$PWD" --bind-workstream "WORKSTREAM_SCOPE_ID"
```

The local fallback is stable for one resolved directory, but it is not intended to join unrelated checkouts. Set an
explicit scope only when that separation or sharing is deliberate.

## Use explicit Memory and Handoff operations

The bundled MCP Server exposes the existing PowerContext operations. Claude can search and list Memory, and can
create, revise, or retire an entry when the user explicitly asks to persist a change.

For a task transfer, the bundled Skill guides Claude to prepare current work with `handoff_current_work` and, for an
explicit imperative such as “handoff this work,” pass the returned `handoff` unchanged to `commit_handoff`. The
receiver uses `continue_handoff` with the exact Revision, verifies it, and records an `acknowledge_handoff` receipt;
completed work uses `record_task_outcome` with that receipt. A Prepared Handoff is temporary, while an exact Revision
is the durable cross-agent transfer point.

Automatic recall does not depend on Claude deciding to call MCP. Conversely, MCP Memory writes do not replace prompt
capture: the Hook stores each enabled prompt as ordinary Source evidence, and the Server decides whether later Source
processing produces Memory.

## Configure the Server endpoint and prompt capture

Set the endpoint during setup:

```bash
powercontext setup claude-code \
  --server-url http://127.0.0.1:9000 \
  --no-capture-prompts
```

Claude Code stores these non-sensitive options in its user `pluginConfigs`. You can also override the Hook process for
one launch:

```bash
export POWERCONTEXT_CLAUDE_SERVER_URL=http://127.0.0.1:9000
export POWERCONTEXT_CLAUDE_CAPTURE_PROMPTS=false
claude
```

Use `POWERCONTEXT_CLAUDE_SCOPE_ID` only when the Memory scope must intentionally differ from both the Git remote and
local project path.

`POWERCONTEXT_CLAUDE_FLUSH_ON_CAPTURE=true` makes the Hook wait for Source processing and is intended for tests, not
normal interactive use.

The timeout and flush controls are listed in the
[configuration reference](../reference/configuration.md#claude-code-plugin). They apply to the Hook process; the MCP
client remains managed by Claude Code.

## Connect an authenticated Server

Start the Server with its token loaded from your secret manager:

```bash
export POWERCONTEXT_SERVER_AUTH_ENABLED=true
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

Start Claude Code from an environment containing the matching complete header:

```bash
export POWERCONTEXT_CLAUDE_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
claude
```

The Hook and MCP `headersHelper` read this process environment value. The helper emits no `Authorization` header when
the variable is absent. It uses a Python 3 command that does not depend on plugin-path expansion, avoiding both Claude
2.1.133's failure to expand `${CLAUDE_PLUGIN_ROOT}` in `headersHelper` and a `python` command that may resolve to
Python 2. Never put the token in the Server URL, plugin options, `.mcp.json`, Source metadata, or logs.

Plain HTTP is accepted only for `127.0.0.1`, `localhost`, or `::1`. Use HTTPS when Claude Code connects to a remote
Server.

## Understand failure behavior

Recall and capture are independent and fail open. A failed recall does not prevent prompt capture, and a failed
capture does not remove valid recalled context. In every case Claude Code continues processing the current prompt.

| Condition | Hook behavior |
| --- | --- |
| Empty Prepared Context | Injects nothing and records the `empty` outcome |
| HTTP 401 | Injects nothing and records `authentication_failed` |
| HTTP 404 | Injects nothing and records `version_mismatch` |
| HTTP 503 or unavailable Server | Injects nothing and records `server_unavailable` |
| Unknown schema, malformed JSON, or oversized response | Injects nothing and records `invalid_response` |

Diagnostics contain the outcome and safe numeric metadata only. They omit the prompt, scope, prepared content,
Authorization value, and response body. The plugin rejects redirects and enforces both response-size and wall-clock
limits.

## Diagnose or roll back

Check the CLI and enabled plugin without contacting the Server:

```bash
powercontext doctor claude-code
```

If setup fails after creating a new Marketplace or plugin entry, it removes only the objects created by that setup
call. A Marketplace or plugin that existed before setup is preserved. Rerun setup after correcting the reported
Claude CLI or repository error; the operation is safe to repeat.

Remove the plugin and Marketplace:

```bash
claude plugin uninstall powercontext@powercontext --scope user
claude plugin marketplace remove powercontext
```

Uninstalling the plugin from its last scope also removes its `${CLAUDE_PLUGIN_DATA}` directory unless Claude Code is
run with `--keep-data`.
