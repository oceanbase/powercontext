# PowerContext for DeepSeek Harness

This plugin is a thin DeepSeek Harness integration for a running PowerContext Server. It does not embed storage or start the Server.

The package has two independent Host faces and one optional browser face:

- `lib/index.js` (the package root) contains the native MCP registration, lifecycle hooks, commands, and Host bridge.
- `lib/client.js` is the `./client` browser entry declared through `dsh.client`; DSH serves it as a module-loader
  factory and shows the sidebar connection status. It is optional for headless hosts: without it the root entry
  continues to provide tools, recall, capture, and diagnostics.
- `lib/assets/powercontext.svg` is the packaged client mark exported through `./assets/*`.

The sidebar control reuses the plugin's in-memory `RuntimeStatus` for recall/capture/flush/injection observations
and the existing liveness, readiness, and capability operations for connectivity. It does not call a model or poll
`/pc doctor`. For a local Web host, set `ownerLocal: true` in the plugin patch (or
`POWERCONTEXT_DSH_OWNER_LOCAL=true`) to affirm that the loopback host is your own. Desktop is identified by the
managed `desktop` profile context; an Electron executable running a Web profile is not Desktop authority.
SSH-forwarded, remote, shared, and unconfigured Web hosts are rejected rather than labeled local.

## Install from npm

Once the desired version is published on npm, open **Plugins → Add plugin** in DeepSeek Harness Web or Desktop,
enter `powercontext-dsh`, and click **Install**. Use `powercontext-dsh@<version>` to select a specific release.
If the Mainland China mirror has not synchronized it yet, select the official npm registry.
The package includes compiled JavaScript and its Cordis bundle patch; installation requires no source build.

Start a matching PowerContext Server separately and configure its URL and credentials before using memory features.
The default URL is `http://127.0.0.1:8000`; an existing `powercontext setup dsh` connection is also recognized.
Restart the host if requested, then run `/pc doctor` in a session to inspect the running plugin and MCP tools.
Installing the bundle alone does not establish Server readiness, extraction, or cross-session recall.

Release archives use the PowerContext Release version. Python prereleases such as `1.6.0rc1` become npm
`1.6.0-rc.1`; prereleases use the `next` dist-tag, while versions without a prerelease suffix normally use `latest`.
See the [installation guide](https://github.com/oceanbase/powercontext/blob/master/docs/en/docs/integrations/dsh.md)
and [npm release instructions](https://github.com/oceanbase/powercontext/blob/master/docs/en/development/releasing.md#dsh-npm-package).

## Install from source

Install the released Server and plugin together:

```bash
uv tool install --force "powercontext[cli,server]==1.5.1"
powercontext setup dsh --source oceanbase/powercontext --ref powercontext-v1.5.1
```

`setup dsh` calls `dsh plugin --profile web add`. The model-facing PowerContext surface is registered through DSH's native `@deepseek-ai/dsh-mcp-client`.
For development, install the CLI/Server and this built plugin from the same checkout and record its commit:

```bash
uv tool install --force ".[cli,server]"
powercontext setup dsh --source .
```

Run those development commands from the PowerContext repository root. Each remote setup with `--ref master` creates a new checkout and clones the requested ref. Reinstall the Server
from the same ref and restart DSH after updating the plugin. For a local source, update that checkout before
reinstalling both components.

Use [the DSH setup guide](https://github.com/oceanbase/powercontext/blob/master/docs/en/docs/integrations/dsh.md) for generation/processing configuration.
Run `powercontext server run --env-file powercontext.env` in one terminal, then set
`POWERCONTEXT_DSH_BASE_URL` in another terminal and run `dsh web`. Restart DSH after changing installation or environment.
Release 1.1.0 includes direct-operation Scope failure handling, MCP/Scope failure handling, and the layered Doctor and
snapshot behavior below.

Before each model step it:

1. recalls bounded context with `POST /v1/context/prepare`;
2. captures the current user input with `POST /v1/sources/content`.

The model-facing operation surface is native MCP at `${POWERCONTEXT_DSH_BASE_URL}/mcp/`. DSH exposes the Server tools
as `mcp__powercontext__<operation>`, including Memory, Work Contract, Handoff, Experience, Skill, candidate review,
external Skill, Scope organization, persistent Scope binding, and artifact publication. The MCP Server annotations and
the DSH pre-execute policy preserve the host approval boundary, including candidate review mutations. Scope-dependent
calls must use the exact host-resolved `scope_id` and workspace binding key exposed in the current-turn routing
metadata; the plugin refuses calls for another Scope instead of rewriting MCP arguments. If the host Scope cannot
be resolved, Scope-dependent MCP calls are denied before dispatch; a failed refresh invalidates the session's cached Scope.

The native client connects through a process-local streaming boundary that forwards MCP frames only to the configured
endpoint and rejects every redirect. It preserves native discovery, session headers, streaming and connection cleanup;
it does not register HTTP operation tools or change the host's global fetch. HTTP failures expose controlled status/code
information, not upstream response bodies or diagnostic headers. Content writes, including nested Handoff state, Drafts
and prepared carriers, are rejected with `secret_rejected` before approval or dispatch when they contain likely secrets.
The plugin's MCP catalog and approval classification are generated from the Server's public `tools/list` results,
separately from the lifecycle HTTP operations table.

`handoff_current_work` captures its own boundary and returns `{boundary, handoff}`. Transfer only the complete
`handoff` member to `continue_handoff` with `selection: "prepared"`; do not capture a preliminary Source. For the
separate low-level capture/activate/finalize flow, use activation's top-level `draft` and transfer the complete native
finalization result unchanged.

Optional MCP initialization waits at most five seconds during plugin startup. A stalled handshake leaves ordinary
DSH conversations available while the same native client continues connecting in the background. Its tools become
available only after registration completes; check the current tool catalog before calling them. Closing DSH also
closes pending native MCP connections.

The automatic pre-step and Source capture above are host lifecycle hooks, not model-facing tools, so they retain the
plugin's bounded HTTP client. `/pc` commands are local diagnostics and explicit administrative controls; they also retain
their existing HTTP client and are not substitutes for the MCP operation surface.

`/pc doctor` checks the running plugin configuration, health, capabilities, declared routes, current Scope and
read-only prepare independently. Failures identify the operation, a specific code, available HTTP status/request ID,
safe dependency statuses and recovery actions. The endpoint summary omits credentials and path text. A successful
report does not prove capture or processing: write routes are declared by OpenAPI but never executed by Doctor.
Doctor also reports the exact visible native MCP tool names separately. An unavailable catalog does not fail healthy
lifecycle HTTP checks, but HTTP health alone does not establish that model-facing MCP tools are available.
Standalone `powercontext doctor dsh` verifies Web-profile registration and explicitly cannot observe the running
host's overrides. A healthy Server with extraction disabled may return valid empty recall.

`get_readiness`, including `/pc doctor`, uses `max(requestTimeoutMs, 40000)` milliseconds. This covers the
default cold inference probe budget (30 seconds), subsequent access checks (up to 5 seconds), and transport
headroom. Doctor reports both `request_timeout_ms` and `readiness_request_timeout_ms`. For custom Server
probe budgets above these defaults, increase `requestTimeoutMs` accordingly. Other requests retain their
configured deadline (1000 ms by default), and caller cancellation still stops readiness immediately.

The operations table used by the lifecycle client in `src/operations.generated.ts` is generated from the repository
`openapi/powercontext.yaml`. From the PowerContext root:

```bash
make js-api-generate
make js-api-generate-check
```

The plugin resolves an explicit Scope, a durable workspace binding, or the Server default. Environment overrides use
the `POWERCONTEXT_DSH_` prefix for `BASE_URL`, `AUTHORIZATION`, `SCOPE_ID`, `CAPTURE_PROMPTS`, and `FLUSH_ON_CAPTURE`.
`timeoutMs`, `requestTimeoutMs`, `maxBytes`, `flushMaxCalls`, and `ownerLocal` are plugin patch settings. Context
returned by recall is labelled as untrusted history. An unavailable Server never blocks normal Harness work.

Remote HTTP is rejected by default. For an explicitly trusted plaintext connection, set
`POWERCONTEXT_DSH_ALLOW_INSECURE_HTTP=true` or the plugin setting `allowInsecureHttp: true`.
The common `POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP` flag applies when the host flag is absent; a host flag of
`false` overrides it. Environment flags accept only `true/false`, `1/0`, `yes/no`, or `on/off`.
HTTPS certificate validation and redirect rejection remain enabled.

Setup-saved URLs and endpoint-specific consent are read from `~/.config/powercontext/clients.json`
(override with `POWERCONTEXT_CLIENT_CONFIG_FILE`). URL environment overrides, including
`POWERCONTEXT_CLIENT_SERVER_URL`, take precedence over explicit plugin URLs, then saved URLs and the loopback default.
Changing the endpoint does not reuse saved or native HTTP consent. Native `allowInsecureHttp: true` must accompany
the matching `baseUrl`; an environment URL override needs its own matching or explicit environment consent.

Automatic failures are reported through the native `powercontext.dsh` logger with a stage, a safe outcome, and an
optional public error code. They do not become model messages. Scope failure stops that step's PowerContext work;
prepare and capture otherwise fail independently. Cancellation prevents subsequent operations, and logger failures
cannot discard a successful recall. Accepted Source evidence still needs Server processing before it becomes Memory.

Non-empty recall uses a persisted plugin `snapshot` with a `PowerContext` section for the host context browser.
Its displayed text is the same untrusted, request-specific context sent to the model. Empty recall creates no snapshot.

See [runtime acceptance tests](https://github.com/oceanbase/powercontext/blob/master/integrations/dsh/plugins/powercontext/tests/runtime/README.md) for the pinned real DSH host, deterministic CI scenarios,
and the separate real-model and Web acceptance procedure.

## Web and Desktop setup targets

`powercontext setup dsh` prompts for Web or Desktop on a TTY; `--json` and non-interactive invocations default to Web.
Use `--profile web` or `--profile desktop` explicitly in scripts. Each profile has independent package dependencies
and bundle activation. Desktop requires its installed dsh launcher, an initialized profile, and a fully quit application.
Use Desktop's **Manage dsh Command…** menu or pass `--dsh-command` with its installed launcher path.
`powercontext doctor dsh --profile desktop` checks registration without launching the reserved profile; reopen
Desktop and run `/pc doctor` to inspect the active session. Connection settings and URL-bound credentials remain shared.
See [the installation guide](https://github.com/oceanbase/powercontext/blob/master/docs/en/docs/integrations/dsh.md) for paths and complete examples.

With `powercontext setup dsh --json`, distinguish `hosts.dsh.setup: "complete"` and
`hosts.dsh.checks.plugin.checks.registration: "present"` from top-level `ok`. Separate Server diagnostics can
still fail with exit code 1 after installation completes. Inspect `hosts.dsh.checks`, start or correct the Server,
and rerun `powercontext doctor dsh --json`; restart DSH and use `/pc doctor` to verify the active session.
