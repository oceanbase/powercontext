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

The project scope comes from the normalized Git remote, falling back to a hash of the worktree path. Set
`POWERCONTEXT_OPENCODE_SCOPE_ID` to override it. Other configuration uses the same prefix with `BASE_URL`,
`AUTHORIZATION`, `CAPTURE_PROMPTS`, `FLUSH_ON_CAPTURE`, `REQUEST_TIMEOUT_MS`, `HTTP_BUDGET_MS`, `MAX_BYTES`, and
`FLUSH_MAX_CALLS`.

OpenCode 1.18.21 or newer in the 1.x line is required. Server failures are fail-open and never block normal work.
