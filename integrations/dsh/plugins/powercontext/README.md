# PowerContext for DeepSeek Harness

Install the PowerContext client separately and expose `powercontext-hook` on the host PATH. Domain operations use the shared client, with bounded responses, deadlines, and explicit unknown write outcomes. Hooks do not install runtime dependencies.


This plugin is a thin DeepSeek Harness integration for a running PowerContext Server. It does not embed storage or start the Server.

Install the released Server and plugin together:

```bash
uv tool install --force "powercontext[cli,server]==1.1.0"
powercontext setup dsh --source oceanbase/powercontext --ref powercontext-v1.1.0
```

`setup dsh` calls `dsh plugin --profile web add --workspace-root`. The plugin talks HTTP only. It does not use MCP.
For development, install the CLI/Server and this built plugin from the same checkout and record its commit:

```bash
uv tool install --force ".[cli,server]"
powercontext setup dsh --source .
```

Run those development commands from the PowerContext repository root. Repeating remote setup with `--ref master`
reuses the cached checkout without fetching; update a local checkout and reinstall both components to refresh it.

Use [the DSH setup guide](../../../../docs/en/docs/integrations/dsh.md) for generation/processing configuration.
Run `powercontext server run --env-file powercontext.env` in one terminal, then set
`POWERCONTEXT_DSH_BASE_URL` in another terminal and run `dsh web`. Restart DSH after changing installation or environment.
Release 1.1.0 includes direct-operation Scope failure handling and the shared Python Doctor and snapshot behavior below.

Before each model step it:

1. recalls bounded context with `POST /v1/context/prepare`;
2. captures the current user input with `POST /v1/sources/content`.

Named `pc_*` tools expose the agent-safe Memory, handoff, experience, skill, and read-only review operations. DSH requests one-time user approval before named mutations run. Review mutations remain explicit human `/pc review` commands; destructive and administrative OpenAPI operations are not model tools.

`/pc doctor` forwards the running connection to the installed Python client's shared liveness, readiness, and
context-schema checks. The report preserves dependency failures without exposing private response text.
`powercontext doctor dsh` verifies native registration and prerequisites; add `--server` to check the discoverable
endpoint. Use `/pc` for local automatic-path observations and `/pc capabilities` for extraction support.

The operations table in `src/operations.generated.ts` is generated from the repository `openapi/powercontext.yaml`. From the PowerContext root:

```bash
make agent-resources
make agent-resources
```

The plugin resolves an explicit Scope, a durable workspace binding, or the Server default. Environment overrides use
the `POWERCONTEXT_DSH_` prefix for `BASE_URL`, `AUTHORIZATION`, `SCOPE_ID`, `CAPTURE_PROMPTS`, and `FLUSH_ON_CAPTURE`.
`timeoutMs`, `requestTimeoutMs`, `maxBytes`, and `flushMaxCalls` are plugin patch settings. Context returned by recall
is labelled as untrusted history. An unavailable Server never blocks normal Harness work.

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

See [runtime acceptance tests](tests/runtime/README.md) for the pinned real DSH host, deterministic CI scenarios,
and the separate real-model and Web acceptance procedure.
