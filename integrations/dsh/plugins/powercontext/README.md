# PowerContext for DeepSeek Harness

This plugin is a thin DeepSeek Harness integration for a running PowerContext Server. It does not embed storage or start the Server.

Install the released Server and plugin together:

```bash
uv tool install --force "powercontext[cli,server]==0.2.0"
powercontext setup dsh --source oceanbase/powercontext --ref powercontext-v0.2.0
```

`setup dsh` calls `dsh plugin --profile web add`. The plugin talks HTTP only. It does not use MCP.
For development, install the CLI/Server and this built plugin from the same checkout and record its commit:

```bash
uv tool install --force ".[cli,server]"
powercontext setup dsh --source .
```

Run those development commands from the PowerContext repository root. Repeating remote setup with `--ref master`
reuses the cached checkout without fetching; update a local checkout and reinstall both components to refresh it.

Use [the DSH setup guide](../../../../docs/en/docs/how-to/configure-dsh.md) for generation/processing configuration.
Run `powercontext server run --env-file powercontext.env` in one terminal, then set
`POWERCONTEXT_DSH_BASE_URL` in another terminal and run `dsh web`. Restart DSH after changing installation or environment.
Release 0.2.0 includes direct-operation Scope failure handling; the layered Doctor and snapshot behavior below
require the current development checkout.

Before each model step it:

1. recalls bounded context with `POST /v1/context/prepare`;
2. captures the current user input with `POST /v1/sources/content`.

Named `pc_*` tools expose the agent-safe Memory, handoff, experience, skill, and read-only review operations. DSH requests one-time user approval before named mutations run. Review mutations remain explicit human `/pc review` commands; destructive and administrative OpenAPI operations are not model tools.

`/pc doctor` checks the running plugin configuration, health, capabilities, declared routes, current Scope and
read-only prepare independently. Failures identify the operation, a specific code, available HTTP status/request ID,
safe dependency statuses and recovery actions. The endpoint summary omits credentials and path text. A successful
report does not prove capture or processing: write routes are declared by OpenAPI but never executed by Doctor.
Standalone `powercontext doctor dsh` verifies Web-profile registration and explicitly cannot observe the running
host's overrides. A healthy Server with extraction disabled may return valid empty recall.

The operations table in `src/operations.generated.ts` is generated from the repository `openapi/powercontext.yaml`. From the PowerContext root:

```bash
make js-api-generate
make js-api-generate-check
```

The plugin resolves an explicit Scope, a durable workspace binding, or the Server default. Environment overrides use
the `POWERCONTEXT_DSH_` prefix for `BASE_URL`, `AUTHORIZATION`, `SCOPE_ID`, `CAPTURE_PROMPTS`, and `FLUSH_ON_CAPTURE`.
`timeoutMs`, `requestTimeoutMs`, `maxBytes`, and `flushMaxCalls` are plugin patch settings. Context returned by recall
is labelled as untrusted history. An unavailable Server never blocks normal Harness work.

Automatic failures are reported through the native `powercontext.dsh` logger with a stage, a safe outcome, and an
optional public error code. They do not become model messages. Scope failure stops that step's PowerContext work;
prepare and capture otherwise fail independently. Cancellation prevents subsequent operations, and logger failures
cannot discard a successful recall. Accepted Source evidence still needs Server processing before it becomes Memory.

Non-empty recall uses a persisted plugin `snapshot` with a `PowerContext` section for the host context browser.
Its displayed text is the same untrusted, request-specific context sent to the model. Empty recall creates no snapshot.

See [runtime acceptance tests](tests/runtime/README.md) for the pinned real DSH host, deterministic CI scenarios,
and the separate real-model and Web acceptance procedure.
