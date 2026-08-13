---
title: Troubleshoot
description: Diagnose PowerContext installation, Server, database, and Codex plugin problems.
---

# Troubleshoot

Start with:

```bash
powercontext doctor
```

The command checks the package, Server liveness, and Server readiness. It exits with status 1 unless every check is
`ok`; a `degraded` readiness result is usable but is not a complete diagnostic success. Add `--json` for automation;
the top-level result and every check include `ok` and `status`. Check the optional Codex integration separately:

```bash
powercontext doctor codex
```

## Installation cannot read the Git URL

Confirm that Git can read the repository:

```bash
git ls-remote https://github.com/oceanbase/powercontext.git HEAD
```

If this fails, configure the credential helper or SSH key used by Git, then rerun `uv tool install`. `uv` uses Git's
credential configuration; PowerContext does not accept or store repository credentials.

## `powercontext` or `codex` is not found

Run:

```bash
uv tool dir --bin
command -v powercontext
command -v codex
```

Add the uv tool bin directory to `PATH` if needed. `powercontext setup codex` reports an error rather than installing a
plugin when Codex CLI is unavailable.

## The plugin is missing or stale

Confirm the integration failure without involving the Server:

```bash
powercontext doctor codex
```

Reinstall it from the same ref as the tool:

```bash
powercontext setup codex --source oceanbase/powercontext --ref <ref>
codex plugin list --json
```

Then start a new Codex session. Check `/hooks` if prompt recall and capture do not run.

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

The bundled Codex plugin uses port 8000 by default. A liveness failure means the process cannot answer health
requests, so readiness is not checked. `not_ready` with HTTP 503 means the Runtime or database cannot accept work.
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

## Codex continues when the Server is down

This is expected. The prompt hook fails open so a Memory outage cannot block ordinary Codex work. Restart the Server
to restore recall and capture; the existing database is reopened automatically.

## Codex does not inject recalled context

Inspect the Hook's single-line JSON event on stderr. `empty` means the Runtime prepared no context for this turn.
`version_mismatch` means the installed plugin expects
`POST /v1/context/prepare` but the Server does not provide it—reinstall the plugin and tool from the same ref, then
restart the Server. `server_unavailable` and `invalid_response` distinguish transport and contract failures. These
events intentionally omit the query and prepared content.

Run `powercontext capabilities` and confirm that `powercontext.prepared-context.v1` appears under Context
versions.
