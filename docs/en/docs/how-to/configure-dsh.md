---
title: Configure DeepSeek Harness
description: Install the PowerContext DeepSeek Harness plugin and control its local behavior.
---

# Configure DeepSeek Harness

## Install or refresh the plugin

Install DeepSeek Harness first and make sure the web profile exists. Then run:

```bash
powercontext setup dsh --source oceanbase/powercontext --ref master
```

The command installs the plugin from `integrations/dsh/plugins/powercontext` and creates the user data directory. The directory must contain a built `lib/index.js`. It is safe to run again: a valid checkout is reused, and a broken checkout for the same ref is replaced. Pass the same `--ref` used to install the PowerContext tool. `--source` accepts a GitHub slug or a `https://github.com/...` URL.

A local checkout works the same way:

```bash
powercontext setup dsh --source .
```

`setup dsh` calls `dsh plugin --profile web add`. Open a new `dsh web` session after setup.

## Understand what the plugin does

The plugin has two paths to the same Server:

- before each model step it asks the Runtime to prepare one final, bounded context value, then independently captures the user's prompt as Source evidence;
- named `pc_*` tools call the public HTTP API to remember, search, revise, retire, and audit Memory.

The plugin resolves one Server-owned Scope in this order: `POWERCONTEXT_DSH_SCOPE_ID`, a durable binding for the
session workspace, then the Server default. The workspace path is hashed only as an external binding key. A missing
workspace therefore uses the Server default instead of the Harness process directory.

The plugin calls `POST /v1/context/prepare` once before the model analyzes the prompt. Explicit `remember_memory` calls do not require a model.

## Diagnose direct tool and command failures

Named tools and Scope-dependent `/pc` commands return a controlled failure if Scope resolution fails. They stop before
the requested operation, without creating a binding or retrying with another Scope. Cancellation and the existing
per-request timeout also apply to Scope resolution.

Inside DeepSeek Harness:

- `/pc doctor` checks liveness and readiness independently of Scope resolution and reports both results.
- `/pc capabilities` queries the Server capabilities without resolving a Scope.
- Unknown subcommands and missing arguments return local usage help without contacting the Server.
- Bare `/pc` shows the resolved Scope and Server origin. If resolution fails, it returns an error while still showing
  `scope=unresolved`, a controlled error, and the `/pc doctor` recovery hint. Configured Scope IDs are not reported as
  resolved. The displayed origin omits credentials, paths, query strings, and fragments.
- `search`, `remember`, `flush`, `review`, `skills scan`, and `stats` require a resolved Scope. `stats` queries that Scope.

| Result code | Meaning |
| --- | --- |
| `not_found` | A business 404. The optional `error_code` preserves a recognized public reason, such as `scope_not_found` or `memory_not_found`. |
| `version_mismatch` | A required endpoint returned 404 without a business code. Check the Server endpoint and plugin/Server compatibility; this does not establish a particular deployment cause. |
| `authentication_failed` | The Server returned 401. Check the configured Authorization header. |
| `unavailable` | Connection failure, timeout, cancellation, or HTTP 503. Native diagnostics use `server_unavailable`. |
| `unscoped` | The resolver completed without a Scope. |
| `invalid_response` | The client detected an invalid Server response. |

Existing conflict and validation codes, such as `revision_conflict` and `invalid_request`, retain their meaning. Failure
results preserve available HTTP status and request ID, but use fixed messages instead of Server-provided text. Unknown
error codes are omitted from `error_code` and diagnostics; their presence alone does not imply a version mismatch.

## Diagnose automatic recall and capture

Ordinary messages also trigger Scope resolution, context preparation, prompt capture, and optional flush. A failure
in these automatic stages leaves the Harness conversation running. Scope resolution failures stop all subsequent
PowerContext operations for that step; they never select a different Scope or create a binding.

The `powercontext.dsh` logger identifies the stage as `scope_resolve`, `context_prepare`,
`capture_content_source`, `flush_memory`, or `context_inject`. It reports fixed diagnostic outcomes and recognized
public error codes, without Server messages, prompt content, credentials, or request paths. Repeated identical
warnings are suppressed for 60 seconds. Logger failures cannot discard prepared context or interrupt the conversation.

Log visibility depends on the DSH profile's native exporters. The tested DSH 0.1.2-rc.1 Web profile does not export
these warnings to the terminal by default. A profile using Cordis's console exporter
(`@deepseek-ai/cordis-plugin-logger-console`) needs `config.levels.default: 2` to include warnings, or `3` for debug
events as well. Read the terminal running `dsh web` for `powercontext.dsh` records. This uses the host logger and adds
no model message or separate log panel.

A missing required route produces `version_mismatch` only when its 404 has no business code. A Scope business 404
instead records `invalid_response` with `error_code: scope_not_found`. A resolver that completes without a Scope
records `skipped` with `reason: scope_unresolved`. A valid empty recall is normal and logged at debug level.
Use `/pc doctor` and `/pc capabilities` to check the Server even when Scope resolution fails.

Preparation and capture are independent: a prepare failure can still allow capture, and a capture or flush failure
does not discard already prepared context. An accepted Source does not mean Memory has been generated; that requires
successful Server processing. Cancellation stops subsequent operations, while an individual request timeout retains
the existing per-request behavior.

## Inspect recalled context

A non-empty PreparedContext is appended once as a plugin message with `source.form=snapshot` and a `PowerContext`
section. In DSH 0.1.2-rc.1 Web, expand a completed turn's process details, then its **Context injection — powercontext-dsh**
row. Other host versions may expose this in a context browser. The section
contains the same text sent to the model and saved in the session log, including the untrusted-history label and
request-specific replacement wording. Reopening session history retains this metadata.

Empty responses and automatic failures do not create a snapshot or a model-visible error notice. Presentation uses
the host's existing snapshot support; it does not add a separate PowerContext panel or claim receipt/source details
that the Server has not returned.

## Control prompt capture

Prompt capture is enabled by default. Disable it before starting DeepSeek Harness when the current work must not be recorded:

```bash
export POWERCONTEXT_DSH_CAPTURE_PROMPTS=false
dsh web
```

For testing only, make the plugin wait for captured Source processing:

```bash
export POWERCONTEXT_DSH_FLUSH_ON_CAPTURE=true
```

This adds inference latency to each prompt and is not the normal interactive setting. `timeoutMs`, `requestTimeoutMs`, `maxBytes`, and `flushMaxCalls` are plugin patch settings, not environment variables.

## Connect to an authenticated local Server

```bash
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

Start DeepSeek Harness from an environment that contains the matching complete Authorization header:

```bash
export POWERCONTEXT_DSH_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
dsh web
```

Do not put the token in the patch file or the Server URL. If the Server is unavailable, recall and capture fail open. Plugin load still requires the DeepSeek Harness peer modules.

## Verify the installation

```bash
powercontext doctor
powercontext doctor dsh
```

`doctor` checks the package and Server. `doctor dsh` checks the DeepSeek Harness CLI and that dump-config contains the plugin id `powercontext-dsh`.
