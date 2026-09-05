# PowerContext for DeepSeek Harness

This plugin is a thin DeepSeek Harness integration for a running PowerContext Server. It does not embed storage or start the Server.

Install it from a PowerContext checkout so the Server and plugin stay on the same ref:

```bash
powercontext setup dsh --source oceanbase/powercontext --ref master
powercontext server run
dsh web
```

`setup dsh` calls `dsh plugin --profile web add` on this directory. The plugin talks HTTP only. It does not use MCP.

Before each model step it:

1. recalls bounded context with `POST /v1/context/prepare`;
2. captures the current user input with `POST /v1/sources/content`.

Named `pc_*` tools expose the agent-safe Memory, handoff, experience, skill, and read-only review operations. DSH requests one-time user approval before named mutations run. Review mutations remain explicit human `/pc review` commands; destructive and administrative OpenAPI operations are not model tools. `/pc doctor` checks Server liveness and readiness.

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
