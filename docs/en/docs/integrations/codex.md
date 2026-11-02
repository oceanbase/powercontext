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

When refreshing setup, client endpoint declarations in the setup `.env` file or process environment supersede saved
client settings and the previously installed MCP URL. Without a new declaration, setup reuses saved endpoints.
Conflicting file and process declarations still require an explicit choice; use `--server-url` and align or unset
runtime URL overrides before rerunning setup. `--source` selects plugin code, not the Server endpoint.

On Windows, setup and diagnostics resolve the Codex executable on PATH, including npm `.cmd` launchers.
A launcher found in the working directory is not preferred over PATH: discovery enumerates PATH directories
directly, while explicit entries - including `.` and empty entries - keep their usual position and meaning.
An empty `PATH` value means no search at all, and an unset `PATH` falls back to the standard system search
roots, matching `shutil.which` in both cases.
For `.cmd` / `.bat` launchers, PowerContext rejects shell metacharacters (`& | < > ( ) ^ % ! "`) and ASCII control characters
in the executable path or command arguments before starting that command. Spaces and Unicode are supported.
This avoids silently changing a source or ref through batch interpretation; use a native executable or a launcher path
and arguments without these characters. The restriction does not apply to native executables or other platforms.
Unsupported source/ref arguments are rejected before marketplace registration. If a later command is rejected because
of a returned marketplace name, registration may already have completed; this does not roll back the installation.

### First-session Hook trust

Open a new Codex session after setup. Before sending the first message, use `/hooks` to inspect PowerContext's hooks
and, when prompted, grant trust. `SessionStart` resolves the Session Scope and optionally loads startup context;
`UserPromptSubmit` prepares relevant context and independently captures the user input as a Source. The plugin also
uses `PreToolUse` for MCP Scope binding. Loading the plugin or discovering MCP tools does not prove that these hooks ran.

If trust was skipped or denied, inspect the PowerContext entries in `/hooks` and follow the host's available controls
to enable or trust them. Follow any instruction to open a new session; check the hooks again before retrying capture.
Controls and prompts depend on the Codex build. If the expected hooks are absent, check plugin installation and host
support using the diagnostics below; do not treat a missing trust prompt as evidence that the hooks are enabled.

With `UserPromptSubmit` disabled, automatic recall and prompt capture do not run. A working MCP connection is a separate
path and is not evidence of automatic behavior. After enabling the hook, send a distinctive project decision and verify
that it appears in the bound Scope's Source journal, then run the [memory checks](../get-started/quickstart.md#4-verify-memory-in-your-selected-mode).
Prompt capture is enabled by default; see [Control prompt capture](#control-prompt-capture) if you intentionally disabled it.

### Installation and connectivity diagnostics

`powercontext doctor codex --json` reports basic MCP connectivity as `mcp_tools` and complete tool coverage as
`mcp_full_profile`. Full coverage requires every capability-bearing tool in the Codex integration's `server-mcp`
manifest, including Memory maintenance, Scope management and Handoff operations. Missing tools produce a degraded
result and are listed by name; discovery does not prove model readiness, Hook execution or write authorization.
Setup reports installation completion separately from diagnostics. Basic Memory support can remain usable while `doctor` reports
incomplete full coverage. Authentication and basic connection failures still produce a failed result.

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

The `powercontext-project-context` Skill inspects the conversation and repository, assembles the objective, current
state, observed checks, omissions, and next action, then calls `handoff_current_work` in the current Session Scope.
An ordinary transfer returns the complete temporary Handoff carrier for the receiver; it does not commit a durable
Revision. `交接` and `交接当前工作` use the same temporary-transfer path.

To save a durable milestone, explicitly ask to `commit a durable handoff`. The Skill prepares the boundary and calls
`commit_handoff`, reporting the exact Revision only after success. Existing authorization does not need a second
confirmation. To inspect proposed content without writing, ask to `preview the handoff without committing`; the Skill
renders the proposed fields in chat and calls no write tool. Discussing Handoff design does not authorize a write.

At Session start, Codex resolves Scope in this order: an explicit `POWERCONTEXT_CODEX_SCOPE_ID`, an existing Session
binding, a host-managed workspace binding, and the Server's default Scope. The selected Scope is fixed to the Session.
Repository and directory identities are lookup inputs only; they never generate a Scope ID. The prompt hook uses the
binding for recall and capture, while `PreToolUse` injects it into data-plane tools so Agent input cannot redirect a
read or write. The host must create or bind a different Scope when the Session changes work boundaries.

To keep later sessions in one Git root on an existing Scope, bind that checkout:

```bash
uv run --frozen --quiet --project "$PLUGIN_ROOT" python "$PLUGIN_ROOT/scripts/scope_binding.py" \
  --cwd "$PWD" --bind-scope "SCOPE_ID"
```

`$PLUGIN_ROOT` is the installed Codex plugin root, the same value the hooks use. In an ordinary terminal, replace it
with that directory, such as `powercontext/powercontext/<version>` in the plugin cache or
`integrations/codex/plugins/powercontext` in this repository. The command stores a binding with
`integration=codex` and `kind=workspace`. Its external id is the SHA-256 of the Git root path. It does not create a
Scope, and it does not derive a `scope_id` from the path, remote, or branch. Run the script again without
`--bind-scope` to print the Scope resolved for that directory. `--clear-scope` removes only that workspace binding.

`POWERCONTEXT_CODEX_SCOPE_ID` still takes precedence. A Session that already started keeps the Session binding written
at startup. A new Session in that Git root uses the workspace binding when no explicit variable and no earlier Session
binding apply. The MCP `set_scope_binding` tool changes only the current Session and does not replace this command.

The Hook calls `POST /v1/context/prepare` once before Codex analyzes the prompt. It requests an 8000-byte total budget,
strictly validates `powercontext.prepared-context.v1`, and injects the returned content unchanged. The recall query is
bounded before the request is built: a prompt of at most 8,192 characters and 8,192 UTF-8 bytes is sent as submitted,
while a longer prompt recalls from its most recent text, cut to the last 8,192 characters and then to the last 8,192
bytes. A long prompt therefore narrows the query instead of failing the recall; prompt capture is unaffected and keeps
its own limit. The Runtime labels
Memory-derived items as untrusted history, preserves exact citations, and owns final selection and rendering. Explicit
search remains available through the Client and MCP; it is not a second automatic recall step. Automatically injected
content and Handoffs are historical information. Codex must still check current code, user requests, and system
instructions before acting on them.

Memory stores durable, reusable decisions, constraints, and state. A Handoff temporarily transfers the current task to
another task, session, or model. It must be explicitly prepared, inspected, and delivered, rather than substituted with
a few Memory entries. Read [Memory and Handoff](../workflows/memory-and-handoff.md) for the boundary and
[Hand off work in Codex](../workflows/handoff-with-codex.md) for the procedure.

## Opt in to Session bootstrap context

Bootstrap context is off by default. To load a small, deterministic package before Codex's first prompt on
`startup`, `resume`, `clear`, `compact`, or `fork`, start Codex with:

```bash
export POWERCONTEXT_CODEX_BOOTSTRAP_CONTEXT=true
codex
```

The fixed `powercontext.scope-bootstrap.v1` profile includes at most six active Atomic Memory Artifacts explicitly
tagged `bootstrap-context`, considering the current Scope before at most eight direct Context References. It never
scans all Memory, Sources, transcripts, pending Candidates, or generated instructions. Secret-like Memory kinds are
excluded. Historical text is marked as untrusted and cannot override current user, developer, system, or repository
instructions.

The default package limit is 4096 UTF-8 bytes. It can be set from 512 through 8192 bytes:

```bash
export POWERCONTEXT_CODEX_BOOTSTRAP_MAX_BYTES=6144
```

No Handoff is selected automatically. To add one exact committed Handoff from the current Scope, provide its immutable
identity before starting Codex:

```bash
export POWERCONTEXT_CODEX_BOOTSTRAP_HANDOFF='{"artifact_id":"HANDOFF_ID","revision":3}'
```

Use the Atomic Memory Artifact tag API to add `bootstrap-context` only to reviewed decisions, constraints, objectives,
verified state, or repository guidance suitable for every new Session. A content-free delivery receipt makes retries
idempotent; after successful injection, the first ordinary query sends that receipt so the Runtime suppresses only
fully delivered exact Atomic Memory revisions. Artifacts truncated during bootstrap and newer revisions remain eligible
for recall.
Preparation, validation, and receipt failures all fail open and inject nothing.
Failures before a new lifecycle package is established, including invalid configuration, also clear any stale saved receipt.

## Experience and Skill

Alongside Memory, Source capture, context injection,
Work Contracts, Handoffs, acknowledgements, Task Outcomes and candidate review, MCP exposes:

| Capability | Tools |
| --- | --- |
| Experience | `get_experience`, `generate_experience`, `propose_experience` |
| Managed Skill | `list_managed_skills`, `get_skill`, `generate_skill`, `propose_skill` |
| External Skill | `scan_external_skills`, `list_external_skills`, `resolve_external_skill`, `import_external_skill` |

For example, ask Codex to “generate an Experience candidate from these exact Source references” or “list the approved
Skills in this Scope.” Generation requires the corresponding Server model configuration; caller-authored proposals
do not. Generated and proposed content enters review as a pending candidate. A `no_op` result means no candidate was
created. Only an explicit review decision produces an approved Artifact; reading or importing does not install or
execute a Skill.

External Skill discovery uses configured roots on the **Server host**. Configure `POWERCONTEXT_SERVER_EXTERNAL_SKILLS`
for custom roots and host identity; see [Agent Skill targets](../workflows/configure-agent-skill-targets.md).
A remote Server cannot scan the Codex workstation. Under enforced access control, scanning requires `server.admin`,
listing and resolving require
`server.observe`, and import requires contribution access to the bound Scope. Select an exact external Skill ID and
fingerprint from the scan/list result before resolving or importing it. Changed content makes the old fingerprint
unavailable. `mode: import` captures the exact package into review without generation; `mode: fork` requires a Skill
generation model. See [Experience and Skill lifecycle](../workflows/experience-and-skill-lifecycle.md).

Upgrade the Server and refresh the plugin together, then open a new Codex session to discover the tools. These MCP
operations share the existing Session Scope binding and Server authorization checks. The shared MCP surface also
provides these capabilities to Claude Code and WorkBuddy; `full` describes the capability profile, not automatic
approval or guaranteed model availability.

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

Local Server authentication is disabled by default and can be enabled independently of Dashboard.
A fresh local configuration wizard leaves Dashboard off unless you select it; selecting it does not require a token.

Load one token from your local secret manager, then start the Server with authentication enabled:

```bash
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

Run setup once from an environment that contains the matching complete Authorization header:

```bash
export POWERCONTEXT_CODEX_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
powercontext setup codex
powercontext doctor codex
```

On Windows PowerShell, set the value for the setup process like this:

```powershell
$env:POWERCONTEXT_CODEX_AUTHORIZATION = "Bearer $env:POWERCONTEXT_LOCAL_TOKEN"
powercontext setup codex
powercontext doctor codex
```

Setup stores a URL-bound credential under `$CODEX_HOME/powercontext/credentials.json` (by default,
`~/.codex/powercontext/credentials.json`) and configures a native MCP `http_headers_helper` to read that same record.
New Codex sessions on Linux, macOS, and Windows do not need an authorization export. Use a Codex build supporting
`http_headers_helper`; this path is verified with Codex CLI 0.153.4. The helper command contains only local paths,
and saved credentials are rejected for a different endpoint, malformed storage, or unsafe POSIX permissions.
The prompt Hook reads the same record. An explicit `POWERCONTEXT_CODEX_AUTHORIZATION` value overrides saved
authorization without changing it. Do not put the token in `.mcp.json`, the Server URL, or a static MCP header.

On Windows, setup also maintains the current user's authorization environment for Desktop compatibility. Existing
processes do not receive environment changes. Restart Codex after setup to load the updated plugin configuration.
Rerunning setup without a token preserves the saved credential; supplying a new token rotates it.
If the Server endpoint changes without a new token, the old credential remains bound to the previous endpoint and is
not forwarded. Setup and doctor probe the new endpoint without that credential, so an unauthenticated Server can
connect normally. For an authenticated Server, supply its token through `POWERCONTEXT_CODEX_AUTHORIZATION` or
`POWERCONTEXT_CLIENT_API_TOKEN` while rerunning setup; the new token replaces the saved record for the selected URL.

To verify a new session without a process override on Linux or macOS:

```bash
unset POWERCONTEXT_CODEX_AUTHORIZATION
powercontext doctor codex
```

Doctor must report native MCP tool discovery. If it reports a saved credential unavailable to the host, upgrade
Codex and rerun setup with the matching PowerContext plugin, then restart Codex. For an older host, start Codex from
a process containing the complete `POWERCONTEXT_CODEX_AUTHORIZATION` header.

When no stored credential or process override is configured and Server authentication is disabled, the plugin behaves
exactly as it does by default. When Server authentication is enabled but the effective credential is missing or
incorrect, the Hook fails open and emits an `authentication_failed` diagnostic; MCP tools remain unavailable without
blocking the Codex session.

If the Server is unavailable, hook recall and capture fail open. Codex work continues, and explicit Memory tools
report that the service is unavailable.

For a normal empty result or recall failure, the Hook emits a content-free JSON diagnostic. Failure outcomes are
returned through the top-level `systemMessage` in the successful stdout hook response; `empty` remains a local
diagnostic. Outcomes include `empty`, `authentication_failed`, `version_mismatch`, `server_unavailable`, and
`invalid_response`. The event never contains the query, scope, prepared content, citation, response body, or
authorization value.

## Use a generated environment file

After generating configuration with the wizard, load `.env` before running setup.
It supplies the URL, Authorization, and selected Scope without exposing the Server's model API keys:

```bash
set -a
. ./.env
set +a
powercontext setup codex
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
`setup codex` updates the installed MCP URL and adds a credential helper with absolute paths for that installation.
Keep the environment override in the base configuration:

```json
{
  "mcpServers": {
    "powercontext": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp/",
      "required": false,
      "env_http_headers": {
        "Authorization": "POWERCONTEXT_CODEX_AUTHORIZATION"
      }
    }
  }
}
```

Use `powercontext setup codex --server-url <server-url>` to update the endpoint and helper together. If editing the
installed file, preserve its generated `http_headers_helper`; the example above is only the base configuration.
The token is never hard-coded in JSON. The
Hook binds the Scope and injects it into MCP data operations; a planned title or directory name is not a Scope ID.

Desktop apps do not inherit changes made inside an already running terminal. On Windows, setup persists the value in
the current user's environment, but an already running Desktop instance must still be restarted. Run `powercontext
doctor codex`, then check Hook capture and MCP separately. An MCP connected status does not prove Source capture.
Complete the [Source, topic evolution, and cross-session recall check](../get-started/quickstart.md#4-verify-memory-in-your-selected-mode).

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `POWERCONTEXT_CODEX_ALLOW_INSECURE_HTTP` | `false` | Explicitly permit non-loopback plaintext HTTP for hooks |
| `POWERCONTEXT_CODEX_SCOPE_ID` | unset | Explicitly select an existing Scope instead of resolving bindings and the Server default |
| `POWERCONTEXT_CODEX_AUTHORIZATION` | unset | Complete `Bearer <token>` runtime override; setup saves it for subsequent Hook and native MCP connections |
| `POWERCONTEXT_CODEX_BOOTSTRAP_CONTEXT` | `false` | Inject the curated bootstrap profile on supported `SessionStart` events |
| `POWERCONTEXT_CODEX_BOOTSTRAP_MAX_BYTES` | `4096` | Bootstrap package limit; valid values are 512 through 8192 bytes |
| `POWERCONTEXT_CODEX_BOOTSTRAP_HANDOFF` | unset | JSON identity of one exact committed Handoff, for example `{"artifact_id":"...","revision":3}` |
| `POWERCONTEXT_CODEX_CAPTURE_PROMPTS` | `true` | Capture user prompts as Source evidence |
| `POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE` | `false` | Wait for Source processing after capture |
| `POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS` | `3` | Per-request hook timeout |
| `POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS` | `6` | Shared hook HTTP budget |
| `POWERCONTEXT_CODEX_FLUSH_MAX_CALLS` | `4` | Maximum flush calls per prompt |

Hooks allow loopback HTTP by default; remote HTTP requires explicit consent, and HTTPS certificate validation stays
enabled. Setup saves consent and updates the installed plugin's `.mcp.json`, which supplies the URL for both hooks
and native MCP. Changing only a Hook URL variable does not change that native endpoint; rerun setup if an upgrade
replaces `.mcp.json`. Codex's own MCP policy still applies. See
[Connect to a remote Server](../operate/connect-remote-server.md).

The outer Codex hook timeout is ten seconds. Recall, capture, and flush fail independently and never block Codex when
the Server is unavailable or rejects authentication. Without an explicit Scope, the plugin resolves the Session
binding, workspace binding, then Server default. Process-level settings must be present in the environment that starts
Codex. Windows setup writes authorization to the user environment, but Desktop must be restarted to inherit it.

## Automatic input privacy

See [Automatic prompt privacy](index.md#automatic-prompt-privacy) for this host's capture default, disable control, credential recognition limits and independent read-only query handling. Recognized inputs are skipped for capture; accepted Source content and event identity are not rewritten.
