# PowerContext for Pi

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
