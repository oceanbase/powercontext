# PowerContext for Codex

This plugin is a thin Codex integration for a running PowerContext Server. It
does not embed storage or start the server.

The integration uses each public surface for the job it fits:

- the `UserPromptSubmit` hook first calls `POST /v1/context/prepare`, then
  independently captures the current prompt with `POST /v1/sources/content`;
- Streamable HTTP MCP at `http://127.0.0.1:8000/mcp` gives Codex the curated
  Memory and work-continuity tools.

The `project-context` skill uses four high-level work operations instead of
assembling the low-level Handoff lifecycle manually: `create_work_contract`,
`handoff_current_work`, `acknowledge_handoff`, and `record_task_outcome`.
When the user says `交接`, `交接当前工作`, `handoff this work`, or an equivalent
imperative, the Skill inspects the current conversation and repository, calls
`handoff_current_work`, and immediately commits the returned `handoff` member
through the artifact-level `commit_handoff` operation. The imperative itself
is the explicit authorization
for that durable milestone; preview or design requests remain read-only.
Acknowledgements and historical authorization notes never grant Codex new
execution authority, and the prompt hook does not infer completion from Stop or
SessionEnd.

Managed Skills use a separate, explicit handoff. A reviewer approves the exact
Candidate through HTTP or the Client CLI, then the user exports that immutable
Skill Revision into a Codex Skill directory:

```bash
powercontext skill export \
  --target codex \
  --scope-id project:example \
  --revision 1 \
  --destination .agents/skills/example-skill \
  SKILL_ID
```

The command creates `SKILL.md` and a `powercontext.json` manifest containing the
exact Artifact reference and content hash. It never replaces an existing path.
Approval alone neither installs nor executes a Skill, and Review operations are
not exposed to the agent through MCP.

Start a local server before using the integration:

```bash
powercontext server run
```

The hook runtime is declared by the plugin's `pyproject.toml` and launched with
`uv`; this keeps its `pydantic-settings` dependency isolated and reproducible.
The hook uses a small synchronous standard-library HTTP adapter because Codex
executes it as a short-lived process. It does not expose that adapter as an SDK.
The `project-context` Skill reuses the installed hook virtual environment when
deriving project scope, so a read-only Codex turn does not need to mutate the
`uv` cache.

Set `POWERCONTEXT_CODEX_SCOPE_ID` to override automatic project scoping. By
default, the scope comes from the normalized Git remote, or from the project
path when no supported remote is available.
`.mcp.json` is the single Server endpoint configuration consumed by Codex and
the hook: the hook validates its PowerContext MCP URL and derives the HTTP API
base by removing the final `/mcp` path segment. Change that file before
installing the plugin when the loopback default is not appropriate. MCP URLs
cannot contain credentials, query strings, or fragments; plain HTTP is accepted
only for loopback hosts.

The hook strictly validates `powercontext.prepared-context.v1`, rejects redirects,
caps response bodies at 1 MiB, and applies both per-request and shared wall-clock
deadlines. The Runtime owns final selection, rendering, exact citations, and the
8000-byte output budget; the hook injects validated content unchanged.

Optional local bearer authentication uses `POWERCONTEXT_CODEX_AUTHORIZATION`,
whose value must be a complete `Bearer <token>` header. `.mcp.json` exposes it to
Codex through an optional environment-backed header, and the hook reads the same
value. Missing or empty values preserve the default unauthenticated flow. Never
put the token in `.mcp.json`, the Server URL, or a static MCP header.

Prompt capture is enabled by default. Set `POWERCONTEXT_CODEX_CAPTURE_PROMPTS=false`
when prompts must not be persisted. Captured Sources are normally processed by
the Server's Memory extraction job. They remain ordinary prompt evidence: the
hook does not label a user request as a completed Task Outcome.
For tests or read-your-write workflows, set
`POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE=true`; the hook then flushes until the captured
Source position is processed.

Scheduled Experience incubation is a separate Server job. It consumes only
Content Sources captured by a completion-aware integration with metadata
`{"kind": "task-outcome"}` and creates pending Experience Candidates for the
Review Inbox. It never approves an Experience, creates or installs a managed
Skill, or grants Codex execution authority.

All hook configuration uses the `POWERCONTEXT_CODEX_` prefix. The default
request timeout is one second, the shared HTTP budget is four seconds, and a
flush performs at most four calls. These can be tuned with
`POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS`,
`POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS`, and
`POWERCONTEXT_CODEX_FLUSH_MAX_CALLS`, while the outer Codex hook remains capped
at ten seconds.

Context returned by the hook is labelled as untrusted history. Recall, capture,
and flush fail independently; an unavailable Server never blocks normal Codex
work. For an empty result, authentication failure, version mismatch, unavailable
Server, or invalid response, the hook returns one content-free diagnostic JSON event through the top-level
`systemMessage` in its successful stdout response. If context is available, `hookSpecificOutput` is returned beside
the diagnostic. Repeated failures are deduplicated per invocation and throttled for 60 seconds across hook processes.
Diagnostics contain status and byte counts only—never the query, scope, content,
citation, response body, or authorization value.
