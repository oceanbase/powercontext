---
title: Troubleshoot
description: Diagnose PowerContext installation, Server, database, and host integration problems.
---

# Troubleshoot

Start with:

```bash
powercontext doctor
```

The command checks the package, Server liveness, and Server readiness. It exits with status 1 unless every check is
`ok`; a `degraded` readiness result is usable but is not a complete diagnostic success. Add `--json` for automation;
the top-level result and every check include `ok` and `status`. Check optional host integrations separately:

```bash
powercontext doctor integrations
powercontext doctor codex
powercontext doctor claude-code
powercontext doctor dsh
powercontext doctor openclaw
powercontext doctor opencode
powercontext doctor pi
powercontext doctor hermes
```

`doctor integrations` prints every first-class host. A host whose CLI is not on PATH is `missing` and does not fail
the command. A present host that is broken still exits 1. Single-host commands such as `doctor codex` stay fail-closed
when that CLI is missing.

## Installation cannot read the Git URL

Confirm that Git can read the repository:

```bash
git ls-remote https://github.com/oceanbase/powercontext.git HEAD
```

If this fails, configure the credential helper or SSH key used by Git, then rerun `uv tool install`. `uv` uses Git's
credential configuration; PowerContext does not accept or store repository credentials.

## A PowerContext or host CLI is not found

Run:

```bash
uv tool dir --bin
command -v powercontext
command -v codex
command -v claude
command -v dsh
command -v openclaw
command -v opencode
command -v pi
command -v hermes
```

Add the uv tool bin directory to `PATH` if needed. `powercontext setup codex`, `powercontext setup claude-code`,
`powercontext setup dsh`, `powercontext setup openclaw`, `powercontext setup opencode`, `powercontext setup pi`, and
`powercontext setup hermes` report an error rather than attempting installation when the host CLI is unavailable.
`powercontext setup select` installs only the hosts you choose. A selected host that is missing still fails that row
and does not block the other selected hosts. An unselected host is skipped even if its CLI is on `PATH`.

## The plugin is missing or stale

Confirm the integration failure without involving the Server:

```bash
powercontext doctor codex
powercontext doctor dsh
powercontext doctor pi
```

Reinstall it from the same ref as the tool:

```bash
powercontext setup codex --source oceanbase/powercontext --ref <ref>
codex plugin list --json
```

Then start a new Codex session. Check `/hooks` if prompt recall and capture do not run.

For Claude Code, run:

```bash
powercontext doctor claude-code
powercontext setup claude-code --source oceanbase/powercontext --ref <ref>
claude plugin list --json
```

Then start a new Claude Code session. Check `/hooks` and `/mcp`; the plugin inventory should contain one
`UserPromptSubmit` Hook and one `powercontext` MCP Server.

If setup fails while creating new user-scoped objects, it attempts to remove only the plugin and Marketplace entries
created by that invocation. Existing entries are preserved. Correct the reported Claude CLI or repository error and
rerun the same setup command.

For DeepSeek Harness, run:

```bash
powercontext doctor dsh
powercontext setup dsh --source oceanbase/powercontext --ref <ref>
dsh --profile web --dump-config
```

Then start a new DeepSeek Harness session and confirm dump-config lists `id: powercontext-dsh`. The DSH plugin
directory must contain `lib/index.js`.

For Pi, run:

```bash
powercontext doctor pi
powercontext setup pi --source oceanbase/powercontext --ref <ref>
pi list
```

Then start a new Pi session and confirm `pi list` includes the PowerContext package source.

## The Server check fails

Start the service:

```bash
powercontext server run
```

If port 8000 is already in use, stop the conflicting process. For a different Server endpoint, pass its
base URL when checking it:

```bash
powercontext doctor --server-url http://127.0.0.1:9000
powercontext --server-url http://127.0.0.1:9000 ready
```

The bundled Codex and Claude Code plugins and Pi package use port 8000 by default. A liveness failure means the process
cannot answer health requests, so readiness is not checked. `not_ready` with HTTP 503 means the Runtime or database cannot accept work.
`degraded` with HTTP 200 means a configured inference capability failed while database-backed operations remain
available. Human and JSON output retain the Server's individual check statuses.

## The Server cannot open its database

The database is created when the Server starts, not when the tool is installed. Inspect the Server startup error before
rerunning `powercontext doctor`.

To use a controlled location:

```bash
export POWERCONTEXT_HOME=/path/with/write/access
powercontext server run
```

Use the same environment variable whenever you start or diagnose that instance. PowerContext creates missing parent
directories for a file-backed SQLite database.

## An inference readiness check fails

When generation or embedding is configured, Server readiness makes one minimal real provider request. This catches
credentials and endpoints that can be validated only by sending a request, including a base URL that is missing the
provider's API prefix. Stable statuses are `ready`, `unavailable`, `timeout`, and `misconfigured`; responses never
include credentials, provider response bodies, or configured URLs.

An inference failure makes overall readiness `degraded` with HTTP 200 instead of removing the whole Server from
traffic. `ready` and `misconfigured` results are cached for 300 seconds; temporary `timeout` and `unavailable` results
are retried after 30 seconds. Concurrent health requests share one refresh. Restart the Server to apply corrected
static configuration immediately, or wait for the cached result to expire.

## Memory writes work but captured prompts do not become Memory

Explicit Memory operations do not require a model. Converting captured Source evidence into Memory does. Configure a
generation model and its provider credentials, then either enable the scheduler or flush the scope explicitly. Check
the Server's advertised behavior:

```bash
powercontext capabilities
```

`Memory extraction: disabled` means the Server has no generation model.

## Host-visible integration diagnostics

The Codex, Claude Code, DSH, OpenClaw, Pi, and Hermes integrations are fail-open: a PowerContext outage does not
block the host task. They also expose a bounded, content-free diagnostic through the host's supported channel:

| Host | Diagnostic channel | Component |
| --- | --- | --- |
| Codex | Hook stdout `systemMessage` | `powercontext.codex.recall` |
| Claude Code | Hook stdout `systemMessage` | `powercontext.claude_code.recall` |
| DSH | Host logger warning | `powercontext.dsh` |
| OpenClaw | Plugin logger warning | `powercontext.openclaw` |
| Pi | Host terminal warning | `powercontext.pi` |
| Hermes | Python host logger warning | `powercontext.hermes` |

For example, a transport failure is returned in the hook's top-level `systemMessage`; its value is a single-line,
content-free JSON event such as:

```json
{"systemMessage":"{\"component\":\"powercontext.codex.recall\",\"event\":\"context_prepare\",\"outcome\":\"server_unavailable\",\"recovery\":\"powercontext doctor\"}"}
```

The stable outcomes remain distinct: `authentication_failed`, `version_mismatch`, `server_unavailable`, and
`invalid_response`. Diagnostics never include prompts, recalled content, scopes, URLs, credentials, response bodies,
or exception text. Repeated outcomes are deduplicated within one invocation and throttled for 60 seconds using local
state shared across hook processes; a diagnostic failure never changes the host task result.

Bub is not included in this first host-diagnostic slice. Its integration will be qualified separately when its host
diagnostic channel and native lifecycle behavior are specified.

## The coding agent continues when the Server is down

This is expected. The supported integrations fail open so a Memory outage cannot block ordinary work. Inspect the
host-visible diagnostic and run `powercontext doctor`; restart the Server to restore recall and capture. The existing
database is reopened automatically.

## Codex does not inject recalled context

For failures, inspect the Hook's top-level `systemMessage`; its value is the single-line JSON event. `empty` means the
Runtime prepared no context for this turn and remains a local diagnostic rather than a host warning.
`version_mismatch` means the installed plugin expects
`POST /v1/context/prepare` but the Server does not provide it—reinstall the plugin and tool from the same ref, then
restart the Server. `server_unavailable` and `invalid_response` distinguish transport and contract failures. These
events intentionally omit the query and prepared content.

Run `powercontext capabilities` and confirm that `powercontext.prepared-context.v1` appears under Context
versions.

## Claude Code does not inject recalled context

First separate installation from Server health:

```bash
powercontext doctor claude-code
powercontext doctor
```

The first command checks the Claude CLI and enabled plugin without contacting the Server. The second checks Server
liveness and readiness. For failures, inspect the Hook's top-level `systemMessage`; its value is the single-line JSON
event. Claude Code uses the same Prepared Context
contract as Codex, with component `powercontext.claude_code.recall`:

| Outcome | Action |
| --- | --- |
| `empty` | No relevant Memory was prepared; no action is required |
| `authentication_failed` | Export the complete `POWERCONTEXT_CLAUDE_AUTHORIZATION` header before starting Claude Code |
| `version_mismatch` | Install the package and plugin from the same ref, then restart both processes |
| `server_unavailable` | Start the Server or correct `POWERCONTEXT_CLAUDE_SERVER_URL` |
| `invalid_response` | Check for a proxy, redirect, incompatible schema, malformed JSON, or an oversized response |

The diagnostics never log the token, query, scope, prepared content, or response body. Prompt capture is independent
of recall; a capture failure cannot suppress valid context, and a recall failure cannot suppress capture.

## Claude Code MCP authentication fails

The Hook and MCP `headersHelper` read `POWERCONTEXT_CLAUDE_AUTHORIZATION` from the environment that starts Claude
Code. Stop the current process, export the complete header, and start it again:

```bash
export POWERCONTEXT_CLAUDE_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
claude
```

Do not add the token to `.mcp.json`, the Server URL, or plugin options. Use `/mcp` after restart to confirm that the
`powercontext` Server is connected.

## Pi does not inject recalled context

First check the package and Server separately:

```bash
powercontext doctor pi
powercontext doctor
```

Restart Pi after installing the package or changing `POWERCONTEXT_PI_*` variables. In a new Pi session, run
`/pc doctor` to check the configured Server directly. Recall is fail-open and reports a content-free host terminal
warning when the Server is unavailable, redirects, times out, or returns an invalid PreparedContext; Pi continues
without adding context. Restore the Server, then run `powercontext capabilities` and confirm that Context versions lists
`powercontext.prepared-context.v1`.
