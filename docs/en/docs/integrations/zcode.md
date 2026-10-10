---
status: community
title: ZCode
description: Install the PowerContext ZCode plugin and verify automatic Memory generation, recall, and MCP operations.
---

# ZCode

`community`

This integration supports the [open-source ZCode CLI](https://github.com/zai-org/ZCode). The official Windows desktop
app version 3.14.3 has also been exercised with a live PowerContext Server:
ordinary prompt capture, automatic Memory generation, fresh-session recall, and MCP Memory read/write worked.
The current acceptance used GLM-5.3 for the host and `openai-chat:glm-5.3-flash` for Server Generation.
The same release also passed Handoff preparation, temporary resolution, commit, and fresh-session resolution, plus
local Bearer authentication and ordinary task continuity during a Server outage. The open-source CLI has also passed remote HTTPS
validation through an SSH port forward. Direct HTTPS ingress and other official releases remain unverified.
CLI 0.16.9 separately passed automatic prompt capture, live scheduled Memory generation with Source citations, and
fresh-session recall against a real local Server. Desktop 3.14.3 separately passed Scope binding, readonly runtime
diagnostics, Memory citation conflicts, exact-revision Receipt/Outcome association and candidate version authorization.
The repository provides [repeatable CLI acceptance and manual desktop steps](https://github.com/oceanbase/powercontext/tree/master/integrations/zcode/acceptance).
Controlled inference, live models and desktop execution are recorded separately; a passing scenario is not a complete run.

## Install matching Server and plugin versions

Install the open-source ZCode CLI or the official Windows desktop app. Put Node.js 24 or newer on `PATH` for the Hook.
Use one PowerContext checkout for both Server and plugin so their contracts match:

```bash
powercontext setup zcode --source /path/to/powercontext
```

The `powercontext` command should come from that checkout too; install `powercontext[cli,server]` from it for development.
For a GitHub source, pass `--source owner/repository --ref <git-ref>` using the same commit or release tag as the Server.
Omitting `--source` selects the default PowerContext repository. A moving branch can change between installs; its name
alone does not establish that the two components match.

Setup copies the plugin to `~/.zcode/cli/plugins/powercontext` and adds that path to `plugins.dirs` in the shared
`~/.zcode/cli/config.json`. It preserves unrelated model, provider, and plugin settings. Repeating setup refreshes the
PowerContext-managed copy. On Windows, setup detects the official desktop app at
`%LOCALAPPDATA%\Programs\ZCode\ZCode.exe`. For a source CLI build, point to its built entry first:

```powershell
$env:ZCODE_CLI_BIN = 'C:\path\to\ZCode\apps\zcode-cli\packages\cli\dist\zcode.cjs'
powercontext setup zcode --source 'C:\path\to\powercontext'
```

Fully quit ZCode, including its Windows tray process, and reopen it after setup. Closing the window alone may leave
the old process running with its previous plugin configuration.

## Start the Server and the host

To extract Memory automatically from Sources, configure the separately running Server's Generation model, Source-window
schedule, and Memory schedule. For a first-time setup, use the configuration wizard. Preserve existing storage,
listener, and authentication settings when updating a Server that already has data:

```bash
powercontext config init --output powercontext.env
```

For example, a Server processing **coding project** memories with a Z.ai GLM Coding Plan API key can use these settings
in `powercontext.env`. Keep that file out of Git:

```dotenv
POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai-chat:glm-5.3-flash
OPENAI_BASE_URL=https://api.z.ai/api/coding/paas/v4
OPENAI_API_KEY=<your Coding Plan API key>
POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS=60
POWERCONTEXT_SERVER_RUNTIME_MEMORY_SCHEDULE_SECONDS=60
```

Enable both schedules: the first processes captured Source windows, and the second schedules Memory generation.
With only the second, new Sources may never enter automatic extraction. A `ready` Generation health check does not
replace reading back the Memory entry.

For a BigModel China Coding Plan, use `https://open.bigmodel.cn/api/coding/paas/v4` as `OPENAI_BASE_URL`. Coding Plan
endpoints are for coding use and differ from general API endpoints. Check account and model availability in the
[official ZCode model configuration guide](https://zcode.z.ai/cn/docs/configuration). `ZCODE_CODING_PLAN_API_KEY` is
not automatically mapped to the Server's `OPENAI_API_KEY`. Then validate and start the Server:

```bash
powercontext config validate --env-file powercontext.env
powercontext server run --env-file powercontext.env
```

When first enabling Generation processing on an existing persistent database, startup may report
`Processing configuration differs from the completed maintenance manifest`. First inspect the read-only plan with
`powercontext server processing-migrate --action plan --env-file powercontext.env`. Back up the database and stop the
old Server, Workers, and writes before running `apply` and `verify` with a new migration ID as described in
[Artifact processing migration](../operate/artifact-processing-migration.md). Start the new configuration only after
verification returns `ready: true`. Do not discard the database to bypass the migration.

Keep the separate Server process running. Explicit `remember_memory` writes and full-text search need neither
Generation nor Embedding. A Server without them can be healthy, but it cannot automatically turn ordinary Sources into
Memory or provide semantic search. `inference.generation: ready` in `/health/ready` confirms configuration, not a
successful extraction; inspect the Memory entry and its Source references for that. See
[Configure models and complete Memory](../get-started/configure-models.md).

The Hook and MCP use the same Server URL saved by setup, defaulting to `http://127.0.0.1:8000`. For another endpoint,
rerun `powercontext setup zcode --server-url https://host --source /path/to/powercontext`. Non-loopback plaintext HTTP also requires
`--allow-insecure-http` at setup. Changing `POWERCONTEXT_ZCODE_SERVER_URL` in a launch terminal does not replace the
URL saved by a normally installed plugin.

For a remote Server, enable access control and put the Server behind an HTTPS reverse proxy with a valid certificate,
then install the plugin with that `https://host` URL. Configure the Server identity and token as described in
[Deployment authentication](../operate/deploy-server.md), and give the launching ZCode process the full
`POWERCONTEXT_ZCODE_AUTHORIZATION` value described below. Acceptance should check that an unauthenticated request is
rejected, a ZCode MCP tool succeeds, and an ordinary prompt becomes a Source in the same remote Scope. This integration
has been tested across machines over HTTPS through an SSH port forward. When using a private CA, provide its certificate
to the ZCode process and Node-based doctor probe through `NODE_EXTRA_CA_CERTS`. Python API verification can use
`SSL_CERT_FILE`. Keep certificate
verification enabled. This tunnel test does not establish direct HTTPS reachability of the remote listener.

Prepare an existing Scope before launching ZCode: use the Server default, a persistent ZCode workspace/session binding,
or an explicit `POWERCONTEXT_ZCODE_SCOPE_ID`. The Hook does not create a Scope. See
[Scopes and access](../workflows/scopes-and-access.md). The official desktop app manages its own model credentials;
its GLM Coding Plan API key does not automatically configure PowerContext Server Generation.

`powercontext setup --env-file .env zcode` can read setup parameters from `.env`; it does not cause the ZCode process
to load that file. Provide Generation's `OPENAI_API_KEY` to the Server process, for example through its `--env-file`;
ZCode manages its own model credential. PowerContext authorization variables must be available when the host starts.
Keep secrets out of the plugin directory.

## Diagnose installation and running configuration

```bash
powercontext doctor zcode --json
```

This checks CLI or Windows desktop discovery, plugin registration, Hook files and Node syntax, MCP declaration, and
Server readiness, protected API access and Scope resolution independently. `ok: true` proves those static and read-only checks, not that an already running
ZCode process loaded the latest plugin or performed Scope resolution, injection, or an MCP call. Fully restart ZCode
and check for PowerContext tools in a new session.

| Failed check | What to inspect |
| --- | --- |
| `zcode` | CLI on `PATH`, `ZCODE_CLI_BIN` pointing to a build, or the desktop app at a detectable location. |
| `plugin` | The PowerContext entry in `plugins.dirs` and the plugin's `.zcode-plugin/plugin.json`. |
| `hooks` | `node --version`, `hooks/hooks.json`, and `hooks/user_prompt_submit.mjs`. |
| `mcp` | The installed `.mcp.json` and `powercontext.json` selecting one Server, with matching authorization settings. |
| `server` | The Server listener and `/health/ready`; this check performs no Memory operation. |
| `protected_api` / `scope_probe` | The invoking process's authorization, CA, Scope access and explicit Scope; keep certificate verification enabled. |
| `runtime` | Actual data path, timestamp, configuration match and stages; absent history does not prove host discovery. |

## Query Hook observations

The Hook writes content-free observations to the host-provided `ZCODE_PLUGIN_DATA/runtime`. Current-request binding
metadata includes `status_script` and `plugin_data_dir`; use those actual values. Ordinary model tools need not inherit
Hook-only variables. For an external PowerShell terminal:

```powershell
node '<installed status_script>' --cwd (Get-Location).Path --session-id '<exact session ID>' --data-dir '<actual plugin_data_dir>'
# Latest history for this workspace, independent of the current window:
node '<installed status_script>' --cwd (Get-Location).Path --latest --data-dir '<actual plugin_data_dir>'
powercontext doctor zcode --runtime-data-dir '<actual plugin_data_dir>' --prepare --json
```

`powercontext.zcode.runtime-status.v1` distinguishes `observed`, `not_observed`, `configuration_mismatch` and
`invalid_state`. Missing observations are valid query results; invalid arguments, storage problems or invalid records
exit nonzero. `--latest` means workspace history, not the current session. A matching record reports Scope, timestamps
and separate prepare/capture/context-output results. Records older than five minutes are `stale`; interrupted attempts
are `incomplete`. Neither establishes the current runtime state. Unknown delivery of a write remains `unknown`.

`emitted` means the Hook wrote its additionalContext JSON locally; inspect actual host/model input to prove reception.
`accepted` requires a matching Source receipt; it does not prove Memory generation. Disabled capture, sensitive content,
long Sources and unresolved Scope have separate reasons. Prepare failure does not overwrite capture success.

Doctor probes use the invoking process's endpoint, CA and authorization configuration. `--prepare` performs a fixed-query
readonly prepare, with no Source or Handoff write. A ready listener with a protected API returning 401/403 reports the
access failure separately. `mcp: configured` describes installation; `mcp_session: not_observed` does not claim live
host discovery. Missing/stale/incomplete optional history does not fail otherwise successful installation/connectivity
checks; it remains explicitly skipped. Historical stage failures remain visible even when current probes succeed.

Observations omit prompt, returned history, model answer, raw session/path and credentials. Endpoint/profile/session/
workspace fingerprints isolate records locally; they are not an access-control mechanism. Each attempt has its own
atomic file and latest selection uses start time, so late completion cannot replace a newer attempt. Query or saturation
maintenance retains 64 completed records; incomplete records remain. Enumeration stops at 256 owned records or 512
directory entries and reports capacity trouble, including undeleted temporaries. Each record read is limited to 64 KiB.
Missing/unwritable data storage degrades observation only; ordinary prepare/capture continues. Cleanup never deletes
unknown formats or unrelated files. Inspect filesystem permissions and storage capacity before retrying diagnostics.

## Inspect evidence from an automatic turn

This integration has no in-session `/pc` or `/pc doctor` command like DeepSeek Harness. For an ordinary prompt, the
Hook attempts to resolve a Scope, calls `POST /v1/context/prepare`, and captures the prompt with
`POST /v1/sources/content`. Inspect the Server requests, persisted Source, and the context actually sent to the model;
`doctor zcode` cannot substitute for those observations.

| Stage | Evidence of success | Limit |
| --- | --- | --- |
| Scope | Resolver returns an existing `scope_id` | Without one, injection and capture are skipped for that turn. |
| Prepare | `context/prepare` returns a non-empty `ready` result | `empty` is normal; a correct model answer alone does not prove recall. |
| Capture | `sources/content` returns `202 accepted` and the Source exists on the Server | A Source is evidence, not a Memory entry or proof of extraction. |
| Injection | Model input contains historical context beginning `PowerContext context for this request` | The model may reject it; current instructions and repository state take precedence. |

The Hook derives a stable Source ID from Scope, session, turn, and prompt. If ZCode omits `turnId`, repeated identical
text in one session conservatively reuses a Source ID and cannot distinguish a new submission from a retry. Prompt
capture is enabled by default, while text resembling a secret is not captured automatically. Preparation and capture
are independent: one can succeed when the other fails.

## Session lifecycle and optional boundary processing

`SessionStart` resolves Scope and records the event. `startup`/`clear` add no generic recall or Source; the first ordinary
prompt still performs task-related prepare. On `resume`/`compact`, the Hook can prepare up to 8000 bytes using the fixed
query “Current project decisions, constraints, and outstanding work”. Returned history stays untrusted. This generic
query does not guarantee full task recovery, and the next prompt still performs its own prepare.

The tested open-source CLI emitted `startup` and `resume`, and resume context reached the model. Running `/compact`
in that build did **not** emit `SessionStart compact`; its public event type alone does not establish execution.
The handler accepts clear/compact payloads in local tests, while their real-host trigger remains unsupported or
unverified. Official Windows desktop 3.14.3 also completed `/compact` without emitting `SessionStart(compact)`.
Desktop resume emitted the actual Hook with empty and ready results; the ready case emitted context and did not capture
an additional Source. An opt-in desktop Stop timed out after 778 ms and preserved `unknown` tracking and its pause;
this verifies bounded handling, not successful processing.

`boundary_flush` defaults to `false`. Enable it only when you want Stop to request Memory processing, which can invoke
Server Generation and model charges. It does not configure Generation or guarantee a new Memory entry:

```powershell
$env:POWERCONTEXT_ZCODE_BOUNDARY_FLUSH = 'true'
powercontext setup zcode --source 'C:\path\to\powercontext'
# Quit and reopen the host from this terminal, with the same endpoint/auth/CA configuration.
```

Setup saves this option; omission on upgrade preserves it. The launch environment overrides the saved value, using the
same false aliases as capture (`0`, `false`, `no`, `off`). To disable and save it, set the variable to `false` and rerun
setup. Omitting `--capture-prompts` also preserves the saved capture preference. Data remains in the host's plugin data
directory when the managed installation is refreshed or the boundary switch is disabled.

A valid Source receipt becomes a content-free pending record. Scope/session/profile/endpoint isolation is enforced;
workspace fingerprints provide provenance, while a session's claim and pause cover its Scope across workspace changes.
Before flush, Stop resolves the current Scope again. It sends at most one `POST /v1/memory/flush`, containing only
`scope_id`; local target positions are not invented API fields. Other Scopes remain pending.

Stop has a 1000 ms internal budget (800 ms for network work), with a 1500 ms host timeout. A five-second claim window
prevents concurrent/repeated Stop sends; the last second of a window is deferred. Stop never blocks or continues the
model turn, summarizes its answer, creates Handoff/Receipt/TaskOutcome, or retries a write within the boundary.
It does not guarantee processing during cancellation or host exit.

`status` now includes `powercontext.zcode.pending-status.v1`: receipt counts, target position, tracking problems and
paused unknown flushes. `cursor_reached` proves only a validated processing cursor covered the selected receipt snapshot;
read actual Memory and recall separately. A concurrent later receipt stays pending. With an idle/behind cursor, receipts
remain. A capture whose delivery is unknown has no fabricated position and pauses affected automatic tracking.

Flush uncertainty is published completely **before** the request. After publication, timeout, disconnection, invalid
response or interrupted process preserves the pause even after its claim expires. The public contract does not fully confirm in-flight work, so automatic
retry remains paused; the scheduler and ordinary task can continue. After inspecting the Server and explicitly deciding
to accept the risk of repeating an unknown flush, use the metadata's installed `pending_script`:

```powershell
node '<installed pending_script>' resume-flush --cwd (Get-Location).Path --session-id '<exact session ID>' `
  --data-dir '<actual plugin_data_dir>' --scope-id '<current exact Scope ID>' --accept-unknown-outcome
```

This verifies the current Scope and releases only its flush pause; it sends no flush, discards no receipt, and does not
clear an unknown capture or incomplete-tracking marker. `claim_busy` means retry this explicit control after the active
claim window. A later enabled Stop can process the retained receipts. Do not release pauses automatically from a Skill.

Pause files are published atomically without replacing an existing pause, before any flush request is sent.
An incomplete pause from an interrupted write remains paused. When a retained receipt identifies its exact
Server/profile/session and Scope, the same explicit control can recover it without discarding the receipt.
An unreadable pause without matching receipt evidence is rejected rather than automatically cleared.

Pending storage is bounded to 256 receipt/tracking records, 512 directory entries and 16 KiB per record. Saturation
preserves unconfirmed evidence, reports tracking incomplete and stops automatic flush; Source capture continues.
A capture guard is persisted before sending, and is replaced by a receipt only after acceptance. Expired versioned claim
files can be removed; unknown formats and unconfirmed records are retained. The query performs only local derived-state
maintenance. Missing/unwritable storage degrades boundary tracking without blocking ordinary recall/capture.

## Scope for explicit operations and workflows

The prompt Hook supplies current-request binding metadata separately from recalled history: the exact Scope, session
identity and installed Scope script path. This metadata remains available when recall is empty. Never take session or
Scope identity from historical Memory.

Before explicit Memory, Handoff or candidate operations, verify that binding with the script. From PowerShell:

```powershell
$plugin = Join-Path $HOME '.zcode/cli/plugins/powercontext'
node (Join-Path $plugin 'scripts/scope.mjs') resolve --cwd (Get-Location).Path --session-id '<exact current session ID>'
```

An external terminal may omit the session, but `session_key_used: false` proves only workspace resolution, not absence
of a higher-priority session binding. Obtain the real session or configure an existing explicit
`POWERCONTEXT_ZCODE_SCOPE_ID` before session-bound writes. The script and Hook use the same saved endpoint.

On an explicit binding request, use `bind --scope-id <exact ID>` or `unbind` with the same cwd/session. Only workspace
binding changes; the script resolves again and reports binding and effective Scope separately. Explicit or session
binding may shadow the change. Other bindings are never silently removed. If verification fails after a confirmed
write, the output retains that write result; resolve again before claiming the current Scope switched.
Remote-workspace mode disables local binding writes and requires an explicit Scope.

The Skill routes to Scope/Memory, Work Handoff and candidate-review references. Ordinary transfer is temporary;
committing a durable milestone needs explicit intent. Continue and acknowledge preserve an exact prepared/revision
target; task outcomes keep the actual Receipt reference and check results. Memory changes preserve current citations,
and candidate decisions use current versions. Re-read conflicts; old approval does not authorize changed content.
Configured tools are not proof of discovery. Missing MCP operations remain incomplete; HTTP/shell does not substitute
for a required Memory/Handoff/candidate MCP call.

## Verify automatic capture, processing, and fresh-session recall

This acceptance path calls neither `remember_memory` nor `memory/flush` manually:

1. Prepare an isolated Scope, make it the Server default or bind it to the ZCode workspace, and confirm it has no
   matching test fact.
2. Send an ordinary prompt in ZCode containing a unique, durable coding project decision or constraint. Do not ask
   the agent to call a Memory tool. Confirm `POST /v1/sources/content` returns `202 accepted` and the Source exists in
   the same Scope.
3. Wait for the Memory scheduler. Find the new entry with `POST /v1/memory/entries/list` or
   `POST /v1/memory/search` and confirm its `source_refs` point to the Source from step 2. A cursor advance without a
   new entry can mean that the prompt did not meet Memory extraction rules; ordinary questions and facts easily
   recovered from code are not guaranteed to be saved.
4. Start a fresh ZCode session in the same Scope. Ask without including the unique code. Confirm that this turn's
   `context/prepare` returns `ready`, the model input contains the fact injected by PowerContext, and the answer
   gives the code. The answer alone does not establish the recall path.

Official Windows desktop version 3.14.3 completed this path in an isolated Scope with GLM-5.3 for the host and
`openai-chat:glm-5.3-flash` for Server Generation. Public readback confirmed that Memory referenced the Hook's Source.
Fresh-session Hook observations recorded ready preparation and emitted context; the user reported the exact code
without including it in the new question.

## Verify explicit writes and fresh-session recall

This acceptance procedure writes test evidence. After preparing the Server, plugin, and Scope, use a unique synthetic fact:

1. In a new ZCode session, explicitly ask it to call PowerContext `remember_memory` with an existing `scope_id` and a
   fact such as “The aurora project's validation color is violet-cedar-1457.” Inspect the successful tool result rather
   than a model statement that it remembered something.
2. Search for the code with `POST /v1/memory/search` or ZCode's `search_memory` MCP tool and confirm the Server returns a
   Memory entry. Capturing the prompt as Source does not prove that the explicit write succeeded.
3. Open a fresh ZCode session in the same Scope and ask for the validation color. Also inspect that turn's
   `context/prepare` and injected content. A correct final answer alone could come from the prompt or old chat text.

Official Windows desktop version 3.14.3 has passed live-model checks for Hook injection, persisted Source,
`search_memory` returning an existing entry, and `remember_memory` writing an entry searchable through the Server.
This explicit write path is separate from the automatic extraction path above.

For the open-source CLI, run the plugin and host tests:

```bash
node --test integrations/zcode/plugins/powercontext/tests/plugin.test.mjs
node --test integrations/zcode/plugins/powercontext/tests/host.test.mjs
```

The second command needs `ZCODE_CLI_BIN` pointing to a built CLI. It uses a fake model and Server and does not replace
a live-host acceptance run. Official Windows desktop 3.14.3 connected to local unauthenticated and Bearer-authenticated
Servers: MCP `list_scopes` succeeded, and ordinary prompts became readable Sources through the Hook. An ordinary
conversation continued during a Server outage. In the current isolated acceptance, a native MCP call in the same task
after Server restart failed with `Session not found`. Creating a new task restored native MCP without restarting
the app; recovery of the original task and automatic reconnection are not established. Handoff completed
`handoff_current_work` → `continue_handoff` (prepared) → `commit_handoff` → fresh-session
`continue_handoff` (latest), and revision 1 was read back from the Server. Other exercised tools include `get_scope`,
`list_memory_entries`, `capture_content_source`, `list_candidates`, and `list_dream_runs`; the last two
returned valid empty lists. A plugin installed in a fresh directory created by the Windows login user also passed
official desktop MCP `list_scopes` and Hook Source capture against the isolated Server. Other official releases remain
unverified. Open-source ZCode CLI 0.16.9 also completed a real-model, local-Server capture →
automatic Memory generation → fresh-session recall test. In an isolated scope, a normal prompt supplied a durable
project rollback constraint, scheduled processing created a Memory entry citing that Source, and a fresh CLI session
answered the build marker without receiving it in the question. `context/prepare` returned `ready` content containing
the marker. Simple constants that could be recovered cheaply from code were not extracted in the same test, consistent
with the Memory selection policy.
The same CLI connected to a PowerContext Server on a separate Linux machine through Caddy HTTPS and an SSH port forward,
using a trusted private CA and Bearer authentication. An unauthenticated API request returned 401, an authenticated
request returned 200, MCP `list_scopes` returned the remote Scope, and an ordinary CLI prompt produced a Source that was
read back from that Scope. Direct HTTPS ingress on the remote port was not validated: the client-side TLS handshake
failed on that network path.

## Understand what the plugin does

The plugin reaches the same PowerContext Server through two paths:

- The `UserPromptSubmit` Hook requests up to 8000 bytes of PreparedContext before the model analyzes a prompt and
  independently captures that prompt as a Source.
- ZCode's native MCP client loads the plugin's `.mcp.json` and exposes explicit Memory and Handoff tools, including
  `search_memory` and `remember_memory`. For a first Handoff, `base` and `generation` may be `null`; pass the
  `PreparedHandoff` to follow-up tools exactly, without omitting or inventing fields. Resolved content is marked
  `untrusted_history` and should be checked against the current project state.

The Hook selects a Scope through `POWERCONTEXT_ZCODE_SCOPE_ID`, then current session binding, workspace binding, and
finally the Server default. A canonical Git-root or workspace path is hashed into an external binding key; the path
itself is not a Scope ID. For a remote workspace, set `POWERCONTEXT_ZCODE_REMOTE_WORKSPACE=true` and an existing
`POWERCONTEXT_ZCODE_SCOPE_ID` before launching ZCode, so the Hook skips local path inference. MCP tools still need
the correct Scope when called.

Scope IDs are opaque nonblank strings of at most 256 Unicode characters. Punctuation, Unicode and surrounding
whitespace are preserved in resolution, pending receipts and runtime observations.

## Diagnose MCP tools and the automatic Hook

If PowerContext tools are absent, fully quit and reopen ZCode, then inspect `plugins.dirs`, the installed plugin, and
`.mcp.json`. If a tool is present but fails, inspect its result for Scope, authorization, Server URL, and HTTP errors;
the model's prose is not a tool result. `doctor zcode` checks declarations and performs readonly connectivity and
Scope probes. It does not call `search_memory` or `remember_memory`, or inspect the running session's native MCP catalog.
On Windows, the account running the desktop app must be able to read the installed plugin. If another restricted
account created the directory, `doctor` under that account can pass while the desktop app reports
`plugin_manifest_not_found`. Check `Test-Path <plugin directory>\.zcode-plugin\plugin.json` as the desktop user; if
access is denied, reinstall into a fresh directory that user can read.

Automatic Hook failures leave the ZCode conversation running. The Hook writes a redacted
`component=powercontext.zcode`, stage, and code to stderr; whether ZCode displays them depends on its logging setup.

| Code | Meaning and recovery |
| --- | --- |
| `scope_unresolved` | Check explicit Scope, session/workspace binding, or the Server default. |
| `server_unavailable` / `timeout` | Check the Server, saved URL, network, and request latency. |
| `unauthorized` / `forbidden` | Check the complete Authorization header in the running ZCode process and principal permissions. |
| `not_found` / `conflict` | Inspect Scope, route, and business state; a 404 alone does not prove a version mismatch. |
| `invalid_response` / `invalid_server_url` | Check the Server response contract or rerun setup with a valid endpoint. |

An empty recall is normal and injects no error notice. The Hook validates response shape and the 8000-byte bound,
then fails open on read failure. Explicit MCP writes still require checking the tool result.

## Control prompt capture

Capture is on by default. Disable it before starting ZCode, or pass `--no-capture-prompts` at setup:

```powershell
$env:POWERCONTEXT_ZCODE_CAPTURE_PROMPTS = 'false'
```

The environment variable overrides the value saved by setup. Changing an environment variable outside a running
process does not change the current session; restart ZCode. Disabling capture does not disable PreparedContext recall
or explicit MCP tools.

## Connect to an authenticated local Server

When the Server enforces access control, provide the full Authorization header during plugin setup so MCP references
the runtime variable:

```powershell
$env:POWERCONTEXT_ZCODE_AUTHORIZATION = "Bearer $env:POWERCONTEXT_LOCAL_TOKEN"
powercontext setup zcode --source 'C:\path\to\powercontext'
```

The process that starts ZCode must receive the same `POWERCONTEXT_ZCODE_AUTHORIZATION`. The Hook reads the variable,
and MCP expands `${POWERCONTEXT_ZCODE_AUTHORIZATION}`. Do not put the token in `.mcp.json`, `powercontext.json`, or
the Server URL. Rerun setup after enabling authentication to keep the Hook and MCP declarations aligned. See
[Deployment authentication](../operate/deploy-server.md) for Server configuration.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `ZCODE_CLI_BIN` | unset | Select a source CLI build; the official Windows desktop app is auto-detected. |
| `POWERCONTEXT_ZCODE_SCOPE_ID` | unset | Select an existing Scope before session/workspace binding and Server default. |
| `POWERCONTEXT_ZCODE_REMOTE_WORKSPACE` | `false` | Disable local path binding for remote workspaces; requires a Scope ID. |
| `POWERCONTEXT_ZCODE_CAPTURE_PROMPTS` | setup value, `true` by default | Override prompt capture at runtime. |
| `POWERCONTEXT_ZCODE_BOUNDARY_FLUSH` | saved value, `false` by default | Opt into bounded Stop Memory processing; setup saves an explicit environment value. |
| `POWERCONTEXT_ZCODE_AUTHORIZATION` | unset | Complete `Bearer <token>` header for Hook and MCP. |

Setup saves the Server URL, non-loopback plaintext HTTP consent, and default capture setting in `powercontext.json`.
Rerun setup and restart ZCode after changing them. `POWERCONTEXT_ZCODE_SERVER_URL` is only a Hook fallback when no
Server URL was saved; it cannot change the MCP endpoint in a normal installation.

## Uninstall

Remove only the PowerContext path from `plugins.dirs` in `~/.zcode/cli/config.json`. Delete
`~/.zcode/cli/plugins/powercontext` only after confirming it contains `.powercontext-owned`. Fully quit and reopen
ZCode. Uninstalling the plugin does not remove Server data or ZCode model configuration.
