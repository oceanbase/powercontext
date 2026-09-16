---
status: community
title: DeepSeek Harness
description: Install the PowerContext DeepSeek Harness plugin and control its local behavior.
---

# DeepSeek Harness

`community`

## Install matching Server and plugin versions

Install DeepSeek Harness and make sure its Web profile is available. The real-host acceptance suite pins DSH
0.1.2-rc.1. Choose one PowerContext installation path and keep the Server and plugin together.

For this guided-setup build:

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext setup dsh
```

Keep the Server package and the plugin on this same source branch when following this website's walkthrough.

For development, install both components from one checkout and record its commit:

```bash
git clone --branch master https://github.com/oceanbase/powercontext.git powercontext-dsh-dev
git -C powercontext-dsh-dev rev-parse HEAD
uv tool install --force "./powercontext-dsh-dev[cli,server]"
powercontext setup dsh --source ./powercontext-dsh-dev
```

To update, run `git -C powercontext-dsh-dev pull --ff-only`, record the new commit, and repeat both installation
commands. A local source must contain the checked-in built `lib/index.js`.
`setup dsh --source oceanbase/powercontext --ref master` reuses a valid cached checkout without fetching:
repeating that command does not update a moving branch. A broken checkout is replaced.

`setup dsh` calls `dsh plugin --profile web add`; it does not start the Server. Restart DSH after installation.

## Start the Server and the host

For automatic Source-to-Memory extraction, generate and validate a Server configuration:

```bash
powercontext config init --output powercontext.env
powercontext config validate --env-file powercontext.env
powercontext server run --env-file powercontext.env
```

The Server runs in the foreground; keep this terminal open. Enable generation and scheduled processing.
Embedding enables vector/hybrid retrieval; it is not required for explicit Memory writes or full-text search.
A model-free Server can be healthy while extraction is disabled and recall is empty.
See [the complete Memory loop](../get-started/configure-models.md) for provider and processing configuration.

In another terminal, point DSH at that Server and launch the host:

```bash
export POWERCONTEXT_DSH_BASE_URL=http://127.0.0.1:8000
dsh web
```

In PowerShell, use `$env:POWERCONTEXT_DSH_BASE_URL = "http://127.0.0.1:8000"` before `dsh web`.
If the Server listens elsewhere, change the URL accordingly. Use `POWERCONTEXT_DSH_AUTHORIZATION` for authentication.
Do not copy Server model credentials into the plugin configuration. Leave `POWERCONTEXT_DSH_SCOPE_ID` unset for
the Server default/workspace binding, or set it to an existing Scope intentionally.

Environment overrides take precedence over plugin patch values, which take precedence over defaults. They must be
present in the process launching DSH; changing another terminal's environment does not update a running host.

## Diagnose the running configuration

Run `/pc doctor` inside the affected DSH session. Its report identifies configuration provenance and checks
liveness, readiness, capabilities, declared routes, the current Scope, and a read-only prepare operation independently.
A Scope failure leaves health results available. The endpoint summary shows only its origin, configuration source
and whether a path prefix exists; credentials, prefix text, query strings and fragments are not printed.

Each failed check identifies the operation, a stable code, HTTP status/request ID when available, and a recovery
action. Protocol errors also include `protocol_issue`, identifying the violated JSON, status or PreparedContext field rule.
Readiness retains recognized dependency statuses, including a 503 response. It never forwards raw Server
messages or recalled content. `ok: true` means these read-only checks passed, not that capture or processing occurred.

| Check/result | Meaning and next action |
| --- | --- |
| `invalid_endpoint` | Correct the effective HTTP(S) base URL; remove userinfo, query and fragment. Use Authorization for credentials. |
| `connection_refused` / `dns_lookup_failed` | Check the configured listener or hostname respectively. |
| `request_timeout` | Inspect the named operation's Server latency/dependencies and the effective request timeout. |
| `connection_failed` | Transport failed without a more specific reason; check the endpoint, proxy, network and Server logs. |
| `authentication_failed` / `authorization_failed` | Check the host credential or the principal's operation/Scope permissions respectively. |
| `not_ready` / `degraded` | Inspect the reported dependency, such as `database` or `inference.generation`; use its recovery action. |
| `required_route_missing` | The named operation returned untyped 404. Inspect its proxy route/base path and matching versions; 404 alone does not prove a version mismatch. |
| `required_route_undeclared` | The Server OpenAPI document lacks the listed operation declarations. |
| `contract_unavailable` | Declarations are unchecked; expose `/openapi.json` through the same base path or verify the contract separately. |
| `scope_not_found` / `unscoped` | Check the explicit Scope override, workspace binding, and Server default. Doctor does not alter them. |
| `invalid_response` | The response fails protocol checks, even if HTTP status was 200. |
| `extraction_disabled` / prepare `empty` | Valid limited capability/empty result; neither proves a hook or Server failure. |

The route check reads the Server's existing `/openapi.json` and distinguishes declarations from live probes.
Doctor never executes capture, remember, flush, binding changes or injection. A missing contract is unchecked, and
a missing Scope skips prepare with an explicit reason. Write/processing verification belongs to acceptance below.

Standalone `powercontext doctor dsh` checks Web-profile registration only and reports that the running host
configuration and Server checks were not observed. Exit success means registration checks passed.
`powercontext doctor` uses its own `--server-url` / `POWERCONTEXT_CLIENT_SERVER_URL`; use it for the existing
service/health diagnostics after aligning that URL, without assuming it observes DSH overrides.

## Inspect the last automatic attempt

Run `/pc` in the affected conversation. In addition to Scope and Server origin, the `automatic` object shows
the latest pre-step attempt for that session and workspace. Its stages are recorded independently of debug logs
and diagnostic rate limiting. This view describes observed work; `/pc doctor` checks current service/configuration health.

| Stage | Meaning |
| --- | --- |
| `scope` | `resolved`, or the exact resolution failure/skip reason. |
| `prepare` | `ready` with the validated byte count, `empty` for a normal empty result, or a failure/skip. |
| `capture` | `accepted` means the Server accepted the Source request. It does not prove Memory was produced. |
| `flush` | `completed` / `cursor_reached` means processing reached that Source position. `incomplete` / `flush_budget_exhausted` means the bounded calls did not observe it. Neither proves a Memory entry was created. |
| `injection` | `appended` means the plugin added a snapshot to the returned pre-step messages. It does not prove the model consumed or followed it. A rejected downstream step or failed message wrapper is reported separately. |

Every stage starts as `not_yet_observed`; `running` identifies an in-progress stage. `skipped` includes a specific
reason such as `capture_disabled`, `no_user_text`, `sensitive_content`, `source_too_long`, `scope_unresolved`,
`cancelled`, or `deadline_exceeded`. `unavailable` includes the operation, safe code/message, and HTTP status,
protocol issue or validated request ID when observed. Transport failures distinguish known timeout, cancellation,
connection-refusal, DNS and TLS causes; an unidentified transport failure does not guess a cause.

For example, `prepare: empty` and `capture: accepted` is a valid combination. A capture or flush failure does not
erase a successful prepare. A timed-out or otherwise unconfirmed write carries `confirmation: unconfirmed`:
the request may have taken effect. This includes incomplete successful responses and HTTP failures that do not
establish whether a write took effect. Do not interpret it as proof that nothing was written or blindly retry it.

An observed HTTP 401/403 instead carries `confirmation: rejected`: that request was refused for authentication
or authorization. It does not undo an earlier capture or earlier calls in a bounded flush sequence. Capture
rejection skips flush with `capture_rejected`; unknown capture outcomes use `capture_not_confirmed`. A failure
known to occur before sending the request does not carry an unconfirmed-write marker.

If headers arrive but the body cannot be read, status retains `http_status` and a validated `request_id`, with
`failure_phase: response_body` and `response_body_error` (`request_timeout`, `cancelled`, `connection_failed`, or
`response_too_large`). For example, a stalled 401 body still reports `authentication_failed` and `rejected`,
alongside the body timeout; fix credentials first and use the request ID to locate the request. A stalled 202
body remains unconfirmed and does not start flush. An unread 404 body cannot establish a missing route.

`attempt`, `turn`, `started_at`, per-stage `observed_at`, and `age_ms` identify the observation. `freshness: current`
means the attempt is less than five minutes old and its observed Scope still matches the resolved Scope.
`stale` identifies `age_limit`, `scope_unverified`, `scope_not_observed`, or `scope_changed`. This is the age and
applicability of local observations, not a guarantee of Server Memory freshness. A failed current Scope check
leaves the old observation visible with a stale marker; it never presents that success as verified current work.

The plugin retains only the latest attempt for up to 64 session/workspace pairs in memory. New attempts replace
older observations, including prior successes; late completion of an older attempt cannot overwrite the newer one.
Old sessions are evicted when the limit is reached. Restart or eviction returns `not_yet_observed`. No prompt,
prepared content, credentials, or raw exception text is stored in this status. Status reads may resolve the Scope
read-only, but never trigger prepare, capture, flush, or binding changes. Doctor probes and manual operations do not
overwrite the automatic record.

## Verify capture, processing and fresh-session recall

This explicit check writes test evidence. With the matching installation and extraction configuration above:

1. Run `/pc doctor`. Confirm health, Scope and prepare checks pass and extraction is enabled.
2. Send a distinctive project fact, for example: “The aurora deployment color is violet-cedar-1457.”
3. Verify Source acceptance separately from processing. Wait for the configured Scheduler, or explicitly run
   `/pc flush`. Using the [Memory-loop API checks](../get-started/configure-models.md), verify the processed cursor reaches
   the Source position and a Memory entry cites that Source. A completed flush with no generated entry does not
   prove successful extraction.
4. Open a new session with the same workspace/Scope and ask for the aurora deployment color. Inspect the recalled
   snapshot and confirm it contains the fact. A model answer alone is insufficient evidence of recall.

For deterministic, provider-free acceptance from the same checkout, run `make dsh-runtime-test`.
The pinned real-host fixture checks Source acceptance, processing, fresh-session recall and persisted snapshot
metadata. Its model responses are deterministic; it does not establish external inference-service behavior.
See the [runtime test procedure](https://github.com/oceanbase/powercontext/blob/master/integrations/dsh/plugins/powercontext/tests/runtime/README.md).

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

- `/pc doctor` checks health, capabilities, routes and Scope independently; a Scope failure preserves other results and skips prepare.
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

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `POWERCONTEXT_DSH_BASE_URL` | `http://127.0.0.1:8000` | Server base URL used by the plugin |
| `POWERCONTEXT_DSH_ALLOW_INSECURE_HTTP` | `false` | Explicitly permit non-loopback plaintext HTTP |
| `POWERCONTEXT_DSH_SCOPE_ID` | unset | Explicit existing Scope before workspace binding and Server default |
| `POWERCONTEXT_DSH_AUTHORIZATION` | unset | Complete `Bearer <token>` header for plugin HTTP requests |
| `POWERCONTEXT_DSH_CAPTURE_PROMPTS` | `true` | Capture user prompts as Source evidence |
| `POWERCONTEXT_DSH_FLUSH_ON_CAPTURE` | `false` | Wait for Source processing after capture |

`timeoutMs`, `requestTimeoutMs`, `maxBytes`, and `flushMaxCalls` are plugin patch settings. Server unavailability fails open for recall and capture; restart `dsh web` after changing these variables.

Plain HTTP is allowed on loopback by default. For remote HTTP, explicitly set
`POWERCONTEXT_DSH_ALLOW_INSECURE_HTTP=true`; HTTPS certificate validation stays enabled. The host URL aliases are
checked in the order `BASE_URL`, `SERVER_URL`, then `ENDPOINT`, before `POWERCONTEXT_CLIENT_SERVER_URL`.
See [Connect to a remote Server](../operate/connect-remote-server.md) for setup and saved consent.
