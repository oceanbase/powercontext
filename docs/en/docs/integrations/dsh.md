---
title: DeepSeek Harness
description: Install the PowerContext DeepSeek Harness plugin, configure scoped recall and prompt capture, and verify its native tools.
status: community
---

# DeepSeek Harness

`community`

## Install a published npm package

For a version available on npm, open **Plugins → Add plugin** in DSH Web or Desktop, enter `powercontext-dsh`,
and click **Install**. To pin a version, enter `powercontext-dsh@<version>`. Select the official npm registry if
the Mainland China mirror has not synchronized the version. The npm archive includes built code and its bundle
patch; no checkout or TypeScript build is needed.

The plugin connects to a separately running PowerContext Server. Install a matching Server and configure its
connection using the instructions below; npm installation does not start or configure the Server. The default
endpoint is `http://127.0.0.1:8000`, and saved `powercontext setup dsh` connection settings are recognized.
After installation, restart DSH if requested and run `/pc doctor` in a session. Check both Server readiness and
native MCP discovery before validating capture and recall.

The same package also exposes an independent browser entry, `./client`. After restart, DSH Web and Desktop show a
PowerContext status control above the account area. If the control is absent, the host did not load the browser
entry; existing tools and lifecycle behavior remain available, so this is not a Server failure. Click the control
for connection diagnostics and the **Open Dashboard** action. With a selected conversation, the existing Dashboard
opens in DSH's right sidebar.

The npm version follows the PowerContext Release: `1.6.0rc1` maps to `1.6.0-rc.1`, with equivalent `alpha` and
`beta` suffixes. Stable releases use `latest`; prereleases use `next`. Verify the selected npm version exists
before installation. Local `.tgz` installation verifies the archive path, not npm or mirror availability.

## Install matching Server and plugin versions

Install DeepSeek Harness Web or Desktop. The latest official release checked for this implementation is
`0.2.2-rc.1` (a prerelease published on 2026-11-04); the P1/P2 Web and Desktop bundled-runtime acceptance was run
against that exact package. The deterministic SDK runtime contract tests remain pinned to their older fixture.
Do not infer support for another DSH release from this result. Choose one PowerContext installation path and keep
the Server and plugin together.

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
`setup dsh --source oceanbase/powercontext --ref master` creates a new checkout and clones the requested ref on each
installation. Repeating it resolves the current moving branch again; it does not reuse an earlier checkout. Update
the Server from the same ref as well, and restart DSH after reinstalling the plugin. With `--json`, read
`hosts.dsh.result.plugin_path` and run `git -C <plugin_path> rev-parse HEAD` to record the plugin commit;
record the Server checkout commit separately.

`powercontext setup dsh` asks for `web` or `desktop` in an interactive terminal. Without a terminal, or with
`--json`, an omitted profile defaults to `web`. Explicit selection never prompts:

```bash
powercontext setup dsh --profile web
powercontext setup dsh --profile desktop
powercontext doctor dsh --profile desktop
```

Web installs under `$DSH_HOME/profiles/web`; Desktop installs under `$DSH_HOME/profiles/desktop`
(`DSH_HOME` defaults to `~/.dsh`). Setup calls `dsh plugin --profile <profile> add`, which installs the package
and enables its bundle in that profile's `package.json`. The bundle supplies its own patch. Setup preserves
both profiles' user `cordis.patch.yml` files and does not enable the plugin in the other profile.

Desktop requires its **Desktop-installed command**, not npm/pnpm dsh. Open Desktop once to initialize its
profile, use **Manage dsh Command…** to install the command, open a new terminal, then fully quit Desktop
before setup. Closing the window may only hide it. Setup searches PATH for the Desktop carrier even when
an npm shim comes first. If it is not registered on PATH, pass its installed launcher explicitly:

```powershell
powercontext setup dsh --profile desktop --dsh-command "D:\Apps\DeepSeek Harness\resources\runtime\cli\bin\dsh.cmd"
```

On macOS the launcher is `DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh`.
`--dsh-command` also works with `doctor dsh`. Packaged Windows/macOS Desktop layouts are supported;
unpackaged development launchers are not. Desktop inspection uses the application's Electron runtime and
configuration APIs without booting plugins. Registration success does not establish running-host readiness:
reopen Desktop and run `/pc doctor`. Setup does not start the Server or quit Desktop for you.

Saved `hosts.dsh` connection settings and `$DSH_HOME/powercontext/credentials.json` are shared between Web
and Desktop. Use the same Server for both; changing the shared URL affects either profile that has no native
override. Credentials remain bound to their saved Server URL. Install into each profile separately if you use both.

Existing UI and model patches can remain in place. Setup checks the composed PowerContext connection settings;
see [remote connection configuration](../operate/connect-remote-server.md) for conflicts and dynamic configuration limits.
The check includes children of `group: true` groups and groups named `@deepseek-ai/cordis-plugin-group` or `cordis:group`;
multiple PowerContext entries are rejected before installation and during the installed-configuration check.
Native `@deepseek-ai/cordis-plugin-include` and `cordis:include` trees are checked with the selected host's
YAML/JSON parser and include patches. Relative paths start at the profile directory; nested includes resolve
from their containing file's directory. Unrelated UI/model expressions remain unevaluated during setup.
Enabled include files must already exist. Cycles, unsupported file types or URLs, dynamic paths or patch structure,
and unverifiable group children fail before connection settings or credentials are saved.

An unset, empty, or whitespace-only `DSH_HOME` selects `~/.dsh`. A nonblank path keeps its leading and trailing
spaces. These rules apply to configuration inspection, stored credentials, and plugin dependency lookup.

Setup does not start the Server. Restart DSH after installation.
The model-facing PowerContext surface is registered through DSH's native `@deepseek-ai/dsh-mcp-client`.

Recall queries exclude current DSH `runtime-context` messages (including cleared-context markers) and legacy
`@deepseek-ai/dsh-system-prompt` plugin snapshots, so host catalogs cannot displace the user request.

With `powercontext setup dsh --json`, `hosts.dsh.setup: "complete"` records completed installation/configuration.
Registration is reported separately at `hosts.dsh.checks.plugin.checks.registration: "present"`. The plugin check
also reports package capability independently: `browser_client`, `client_declaration`, and `asset_export` are
`present` or `missing`, and `host_bundle` reports the Host half. A missing browser client is a headless
installation: existing tools continue to work, but the sidebar status is unavailable. Setup reports this rather
than treating it as a Server error. The same report includes Server diagnostics, so its top-level `ok` can be
`false` and its exit code 1 even after installation completed. Inspect the failed entries under
`hosts.dsh.checks`, start or correct the selected Server, and rerun `powercontext doctor dsh --json`. This command
includes separate Server probes; registration itself does not prove running-session connectivity. Restart DSH and
use `/pc doctor` to verify the active session.

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

## Sidebar status and diagnostics

The sidebar control appears above the account area. Its color and accessible label are always paired:

| State | Meaning |
| --- | --- |
| Gray | Not configured, disabled, or not a verified owner-local host |
| Yellow | Connecting, retrying, or an expired observation being re-verified |
| Green | Liveness, readiness, and the required PreparedContext capability were verified |
| Red | Connection, DSH authentication, or a required capability failed |

Clicking the control opens a read-only diagnostics panel in Simplified Chinese by default. The language icon
switches to English; an explicit choice persists in the current browser's local storage, with Chinese as the
fallback. The compact view shows connection state, Server address, workspace and Scope. Expand **Technical details**
for the conversation ID, deployment mode, authentication source (never the credential), readiness dependencies,
context schema and automatic-path observations.
The footer reads `package_version` from `GET /v1/server-info` through the Host and shows the current Server package
version. Unsupported metadata endpoints, failed reads or expired connection observations show **Unknown**, never
the plugin version or API contract version. The bounded metadata read does not determine readiness.
The panel reuses the same in-memory `RuntimeStatus` as `/pc`, rather than reconstructing history from logs.
It never calls a model, sends a diagnostic prompt, or polls `/pc doctor`.

The bridge is the plugin-owned authenticated route `GET /powercontext/status`. DSH's existing `requestRejection`
runs first and is never weakened. The route must also reach a verified local owner surface:

- DSH Desktop is detected from the managed `desktop` profile context provided by the Desktop Host. An Electron
executable running a Web profile is not treated as Desktop authority.
- DSH Web is accepted only for a loopback request when the owner explicitly sets `ownerLocal: true` (or
  `POWERCONTEXT_DSH_OWNER_LOCAL=true`). Set this only on your own local Web host.
- A non-loopback request, an SSH-launched host, or an unconfigured Web host is rejected. The UI reports
  non-loopback requests as an actionable failure, and SSH-launched or unconfigured Web hosts as gray unconfigured;
  none is silently labeled local.

For an explicitly local Web host, add this to the profile or home `cordis.patch.yml`:

```yaml
- id: powercontext-dsh
  config:
    ownerLocal: true
```

Or set the environment variable before starting `dsh web`:

```bash
POWERCONTEXT_DSH_OWNER_LOCAL=true dsh web
```

Do not set this for a remote, shared, container, or SSH-forwarded host. The bridge does not add launch, install,
or target routes.

While the page is visible, the client refreshes every ten seconds. It pauses while hidden, re-verifies immediately
on return, backs off from two to thirty seconds after a failure, and marks a success older than thirty seconds as
stale instead of leaving it green. A 401/403 from DSH, an offline Server, a failed readiness dependency, or a
missing context schema produces a distinct diagnostic. The panel shows only allowlisted status fields; it never
prints the launch token, Authorization value, or a bearer header. A conversation switch changes the observed
session and invalidates responses from the previous selection. With no conversation, the panel says so and does
not create one.

Updating the plugin is an install-and-restart operation; the existing profile and connection settings are
preserved. Uninstalling it removes the browser entry, the `/powercontext/status` and `/powercontext/dashboard` routes, subscriptions, and
timers. A headless host or a package without the browser entry keeps the tools and lifecycle hooks.

## Open the Dashboard in DSH

Enable the [PowerContext Server Dashboard](../operate/configuration.md) explicitly before using this entry. From a
selected DSH conversation, click **PowerContext** above the account area, then **Open Dashboard**. The existing
Dashboard opens in the right sidebar: DSH Web uses its Browser iframe, while the 0.2.2-rc.1 Desktop release uses
an isolated Browser webview. The diagnostics panel remains available from the same PowerContext control. The
Dashboard's own permissions and write confirmations still apply.

The selected conversation's workspace binding determines its Scope. Switching conversations and opening the
Dashboard again selects the new Scope. If no conversation is selected, the panel offers **Open in browser** for
the Server's default Dashboard entry; it does not create a conversation. The local Host checks Dashboard
availability through the DSH-authenticated `GET /powercontext/dashboard` route. The returned URL contains only
the Dashboard path and selected Scope, never a token. The Browser signs in directly to PowerContext when Server
token authentication is enabled; DSH's Server credential is not copied into the page. Sign-in returns to the
selected Scope. A failed sign-in stays on the Dashboard login page for correction.

For local DSH Web, use the same scheme and loopback hostname for DSH and the Server Dashboard; the ports may
differ. For example, use `http://127.0.0.1` for both. If one uses `localhost` and the other uses `127.0.0.1`,
the iframe cannot keep the Dashboard sign-in cookie. The panel explains the mismatch and offers **Open in browser**
for the same Scope.

If Dashboard is disabled, the panel says to enable it in Server configuration. A rejected Host credential or an
unavailable Server is reported separately; the API connection status is not changed by Dashboard availability.
If the DSH Browser container is unavailable, the panel keeps diagnostics open, reports that it could not open
the sidebar, and offers **Open in browser**. A Browser navigation failure is shown by DSH's Browser tab; refresh
it after restoring the Server.
PowerContext Server rejects Dashboard startup with a custom authentication Provider; diagnose that Server startup
error separately from Browser loading. This plugin neither enables Dashboard nor changes Server authentication.
The browser entry has been exercised on the official DSH 0.2.2-rc.1 Web and Desktop release; other host releases
need their own container acceptance.

## Cross-agent handoff Host foundation

The plugin owns the authenticated `/powercontext/targets`, `/powercontext/preflights` and
`/powercontext/deliveries` routes described in [RFC 2528](../../rfcs/2528-dsh-plugin-ui-and-agent-handoff.md).
They require the owner's DSH authentication and a local Desktop or explicitly owner-local Web Host
bound to loopback. Shared, proxied, remote, SSH and unverified deployments cannot use these routes.
Handoff availability is separate from the sidebar's Server connection status.

A preflight checks the exact selected reply, effective directory, target runtime and PowerContext
integration, Server identity, connection configuration and Scope. A missing application requires
manual setup. Installing or repairing its PowerContext integration requires explicit consent for
the observed configuration, followed by verification in that destination. If the source reply
changes during installation, choose the original reply explicitly or start a new preflight.
Cancelling stops continuation and preserves installation changes that already took effect.

Launch requires an existing complete delivery and checks its saved binding and package again. The
Host persists the attempt before calling the fixed macOS opener. `opened` means the OS accepted
the request. The receiving draft, user send and PowerContext acknowledgement are separate events.
An interrupted or ambiguous attempt appears as `unknown`. Check the destination before explicitly
retrying with a new request ID that names the previous attempt. A retry reuses the same saved package
and receipt identity. The Host never reopens automatically after a restart.

The Host uses PowerContext's existing Handoff service. An inspected current-work input or exact
Handoff Revision goes through the verified connection; the complete saved package can then be
read or exported by the owning session. Without inspected input or an exact Revision, this Host
returns `needs_input`. Automatic drafting needs a trusted session evidence reader and a configured
generation path.

The capability catalog only offers native preflight for registered, qualified adapters. Codex Desktop,
Claude Desktop Code, and Claude Code CLI require individual adapters and actual host acceptance.

## Diagnose the running configuration

Run `/pc doctor` inside the affected DSH session. Its report identifies configuration provenance and checks
liveness, readiness, capabilities, declared routes, the current Scope, and a read-only prepare operation independently.
A Scope failure leaves health results available. The endpoint summary shows only its origin, configuration source
and whether a path prefix exists; credentials, prefix text, query strings and fragments are not printed.

Doctor reports the active host's native MCP catalog separately, including the exact visible tool names.
`native_mcp_unconfigured` means no native MCP tools are visible; initialization may still be pending or have failed.
If other native MCP tools are visible but `mcp__powercontext__*` is absent, Doctor reports
`native_mcp_powercontext_missing`. These results do not fail otherwise healthy lifecycle HTTP checks, but HTTP
health alone does not establish that model-facing MCP operations are available.

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
| `memory_protocol_mismatch` / `legacy_memory_operation_unsupported` | Align Server/CLI/plugin contracts, rerun setup for the active profile, restart, and search again for an Atomic reference. Never drop collection CAS. |
| `contract_unavailable` | Declarations are unchecked; expose `/openapi.json` through the same base path or verify the contract separately. |
| `scope_not_found` / `unscoped` | Check the explicit Scope override, workspace binding, and Server default. Doctor does not alter them. |
| `invalid_response` | The response fails protocol checks, even if HTTP status was 200. |
| `extraction_disabled` / prepare `empty` | Valid limited capability/empty result; neither proves a hook or Server failure. |

The route check reads the Server's existing `/openapi.json` and distinguishes declarations from live probes.
Doctor never executes capture, remember, flush, binding changes or injection. A missing contract is unchecked, and
a missing Scope skips prepare with an explicit reason. Write/processing verification belongs to acceptance below.

Standalone `powercontext doctor dsh` checks registration and the installed plugin Memory protocol declaration for the
selected profile. It also checks the Server at the endpoint resolved by the CLI. These checks cannot observe environment
overrides or cached tools inside an already running DSH process. Restart the affected profile and run `/pc doctor` there.
An undeclared installed protocol or unavailable Server contract produces a non-success result; registration alone is
insufficient. Bare `powercontext doctor` uses its own selected client endpoint.

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

The plugin has three deliberately different paths to the same Server:

- before each model step it asks the Runtime to prepare one final, bounded context value, then independently captures the user's prompt as Source evidence;
- DSH's native MCP client connects to `/mcp/` and exposes the Server tools as `mcp__powercontext__<operation>`;
- `/pc` commands and automatic lifecycle stages use the bounded HTTP client for host control and diagnostics.

The MCP surface covers the full PowerContext profile: Memory read/write, Source capture, Work Contract, Handoff and
acknowledgement, task outcome, Experience, managed and external Skill workflows, candidate inspection, Scope
organization, persistent Scope binding, and artifact publication. It also exposes the Server's additional MCP tools that
are outside the profile contract. The plugin does not register Pi/Hermes-style direct HTTP operation tools.

Scope-dependent MCP operations, including those with a path `scope_id`, must use the exact host-resolved Scope
exposed in the current-turn routing metadata. Do not select a different Scope using
`mcp__powercontext__resolve_scope_binding` with `allow_default: true`. The plugin refuses another Scope instead of
rewriting MCP arguments; automatic lifecycle hooks and model operations use the same project context.

Optional native MCP initialization waits at most five seconds during plugin startup. Ordinary DSH conversations
continue if the handshake stalls, and the same client may finish connecting later. Tools are unavailable until
registration completes; check the current tool catalog before selecting an MCP operation.

Native MCP frames pass through a process-local streaming boundary to the fixed configured endpoint; all redirects
are rejected, including during initialization and tool calls. The native client still owns discovery and sessions.
HTTP failures expose controlled status/code information without upstream response bodies or diagnostic headers.
The plugin rejects likely secret content, including nested Handoff state, Drafts and prepared carriers, with
`secret_rejected` before write approval or dispatch. Its MCP catalog and mutation approval classification are generated
from the Server's public `tools/list`, not the HTTP tool table.

`mcp__powercontext__handoff_current_work` captures its own boundary and returns `{boundary, handoff}`. Transfer only
the complete `handoff` member, then pass that exact value to `mcp__powercontext__continue_handoff` with
`selection: "prepared"`. No preliminary Source capture is needed. In the separate low-level capture/activate/finalize
flow, pass activation's top-level `draft` to finalization and transfer the complete native finalization result unchanged.

The plugin resolves one Server-owned Scope in this order: `POWERCONTEXT_DSH_SCOPE_ID`, a durable binding for the
session workspace, then the Server default. The workspace path is hashed only as an external binding key. A missing
workspace therefore uses the Server default instead of the Harness process directory.

The plugin calls `POST /v1/context/prepare` once before the model analyzes the prompt. Explicit `remember_memory` calls do not require a model.

## Diagnose MCP and command failures

Scope-dependent native MCP tools and `/pc` commands return a controlled failure before dispatch if host Scope
resolution fails or returns no Scope. A failed refresh invalidates the session's cached Scope; MCP calls cannot use
a previous turn's value to bypass the failure. Ordinary conversation continues without selecting another Scope or
creating a binding. Cancellation and the existing per-request timeout also apply to lifecycle Scope resolution.

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
| `precondition_failed` | The Server rejected a write because a tag or revision precondition no longer matches the current state. Re-read the current reference, then retry with the fresh value. A 412 that carries a published business code reports that code instead. |
| `precondition_required` | The Server requires the current ETag in `If-Match` for this mutation. Read the resource, then retry with its ETag. |
| `invalid_cursor` | The Server rejected a pagination cursor that is malformed or does not match this request. Restart the listing from the beginning. This is the 400 half of the cursor pair whose expired half is `cursor_expired`. |
| `cursor_expired` | The Server rejected a pagination cursor. Restart the listing from the beginning. |
| `handoff_report_too_large` | The result exceeds the response limit; the Server's only 413 is the Handoff Report size limit. Narrow the selection and retry. |
| `capacity_exceeded` | The Server reached a capacity limit. Retry after a short delay. |

Existing conflict and validation codes, such as `revision_conflict` and `invalid_request`, retain their meaning. A domain
status keeps its own result code and recovery action: only transport failures and HTTP 5xx use the `unavailable` wording,
so a recoverable rejection is never reported as an outage. A response body that carries `error.details` preserves that
object on the tool result. Failure results preserve available HTTP status and request ID, but use fixed messages instead
of Server-provided text. Unknown error codes are omitted from `error_code` and diagnostics; their presence alone does not
imply a version mismatch.

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
contains the same text sent to the model and saved in the session log: the plugin's request-specific replacement
wording, then the Server's prepared context, where the Server's trust policy precedes the recalled items. Reopening
session history retains this metadata.

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

`doctor` checks the package and Server. `doctor dsh` checks the DeepSeek Harness CLI, registration and installed Memory protocol declaration. A package without a declaration is unverified/degraded; update the plugin and restart the actual Web/Desktop profile. See [protocol upgrade recovery](../operate/atomic-memory-migration.md#client-and-agent-host-compatibility).

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `POWERCONTEXT_DSH_BASE_URL` | `http://127.0.0.1:8000` | Server base URL used by the plugin |
| `POWERCONTEXT_DSH_ALLOW_INSECURE_HTTP` | `false` | Explicitly permit non-loopback plaintext HTTP |
| `POWERCONTEXT_DSH_SCOPE_ID` | unset | Explicit existing Scope before workspace binding and Server default |
| `POWERCONTEXT_DSH_AUTHORIZATION` | unset | Complete `Bearer <token>` header for MCP and lifecycle HTTP requests |
| `POWERCONTEXT_DSH_CAPTURE_PROMPTS` | `true` | Capture user prompts as Source evidence |
| `POWERCONTEXT_DSH_FLUSH_ON_CAPTURE` | `false` | Wait for Source processing after capture |

`timeoutMs`, `requestTimeoutMs`, `maxBytes`, and `flushMaxCalls` are plugin patch settings. Server unavailability fails open for recall and capture; restart `dsh web` after changing these variables.

`get_readiness`, including `/pc doctor`, uses `max(requestTimeoutMs, 40000)` milliseconds so cold readiness
can finish the default 30-second inference probe and subsequent access checks (up to 5 seconds), with
transport headroom. Doctor reports this as `configuration.readiness_request_timeout_ms`, alongside the
ordinary `request_timeout_ms` (1000 ms by default). For larger custom Server probe budgets, increase
`requestTimeoutMs` accordingly. Other requests retain their configured deadline; caller cancellation
still stops readiness immediately.

Plain HTTP is allowed on loopback by default. For remote HTTP, explicitly set
`POWERCONTEXT_DSH_ALLOW_INSECURE_HTTP=true`; HTTPS certificate validation stays enabled. The host URL aliases are
checked in the order `BASE_URL`, `SERVER_URL`, then `ENDPOINT`, before `POWERCONTEXT_CLIENT_SERVER_URL`.
See [Connect to a remote Server](../operate/connect-remote-server.md) for setup and saved consent.
