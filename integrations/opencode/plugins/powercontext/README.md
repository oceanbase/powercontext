# PowerContext for OpenCode

This package is a thin OpenCode 1.x plugin for a running PowerContext Server. It does not embed storage or start the
Server.

Install it from the matching PowerContext checkout:

```bash
powercontext setup opencode --source oceanbase/powercontext --ref master
powercontext server run
opencode
```

For each normal user turn, the plugin recalls bounded project context and independently captures the prompt as Source
evidence. Recalled content is inserted transiently before model dispatch and is labelled as untrusted history. It is
not persisted into the OpenCode transcript. Curated `pc_*` tools expose Memory, Handoff, Experience, Skill, and
read-only Candidate operations. OpenCode asks before a named durable mutation.

Each Session resolves an explicit Scope, its durable Session or workspace binding, or the Server default. Set
`POWERCONTEXT_OPENCODE_SCOPE_ID` only to force an existing Scope. Other configuration uses the same prefix with `BASE_URL`,
`AUTHORIZATION`, `CAPTURE_PROMPTS`, `FLUSH_ON_CAPTURE`, `REQUEST_TIMEOUT_MS`, `HTTP_BUDGET_MS`, `MAX_BYTES`, and
`FLUSH_MAX_CALLS`.

Remote HTTP is rejected by default. Explicitly allow it with `POWERCONTEXT_OPENCODE_ALLOW_INSECURE_HTTP=true`,
or use `POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP=true` as the common fallback. A host flag of `false` overrides
the common flag. Flags accept only `true/false`, `1/0`, `yes/no`, or `on/off`; HTTPS certificate validation and
redirect rejection remain enabled.

Setup-saved URLs and endpoint-specific consent are read from `~/.config/powercontext/clients.json`
(override with `POWERCONTEXT_CLIENT_CONFIG_FILE`). The host URL environment variable, then
`POWERCONTEXT_CLIENT_SERVER_URL`, override the saved URL and loopback default. Changing the endpoint does not
reuse saved HTTP consent.

OpenCode 1.18.21 or newer in the 1.x line is required. Server failures are fail-open and never block normal work.
