# PowerContext for Pi

`community`

This native Pi package restores bounded project context before each normal prompt and captures eligible user prompts
as Source evidence. It does not sync Pi transcripts or start a PowerContext Server.

Install it from a PowerContext checkout:

```bash
powercontext setup pi --source /path/to/powercontext
```

Start `powercontext server run`, then open a new Pi session in the project. The package supplies the
`project-context` skill, `pc_*` Memory and Handoff tools, and `/pc` diagnostics.

The package resolves an explicit Scope, a durable workspace binding, or the Server default. Use
`POWERCONTEXT_PI_BASE_URL`, `POWERCONTEXT_PI_SCOPE_ID`, and `POWERCONTEXT_PI_CAPTURE_PROMPTS` to adjust the connection,
explicit override, and automatic prompt capture.

Remote HTTP is rejected by default. Explicitly allow it with `POWERCONTEXT_PI_ALLOW_INSECURE_HTTP=true`,
or use `POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP=true` as the common fallback. A host flag of `false` overrides
the common flag. Flags accept only `true/false`, `1/0`, `yes/no`, or `on/off`; HTTPS certificate validation and
redirect rejection remain enabled.

Setup-saved URLs and endpoint-specific consent are read from `~/.config/powercontext/clients.json`
(override with `POWERCONTEXT_CLIENT_CONFIG_FILE`). The host URL environment variable, then
`POWERCONTEXT_CLIENT_SERVER_URL`, override the saved URL and loopback default. Changing the endpoint does not
reuse saved HTTP consent.
