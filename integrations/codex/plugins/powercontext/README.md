# PowerContext for Codex

This plugin is a thin Codex integration for a running PowerContext Server. It
does not embed storage or start the server.

The integration uses each public surface for the job it fits:

- the `UserPromptSubmit` hook first calls `POST /v1/context/prepare`, then
  independently captures the current prompt with `POST /v1/sources/content`;
- Streamable HTTP MCP at `http://127.0.0.1:8000/mcp` gives Codex the curated
  memory tools.

Start a local server before using the integration:

```bash
powercontext server run
```

The hook runtime is declared by the plugin's `pyproject.toml` and launched with
`uv`; this keeps its `pydantic-settings` dependency isolated and reproducible.
The hook uses a small synchronous standard-library HTTP adapter because Codex
executes it as a short-lived process. It does not expose that adapter as an SDK.

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
8000-byte output budget; the hook injects validated content unchanged. Authentication is not exposed
until the Server and both transport surfaces enforce one complete policy.

Prompt capture is enabled by default. Set `POWERCONTEXT_CODEX_CAPTURE_PROMPTS=false`
when prompts must not be persisted. Captured Sources are normally processed by
the Server scheduler. For tests or read-your-write workflows, set
`POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE=true`; the hook then flushes until the captured
Source position is processed.

All hook configuration uses the `POWERCONTEXT_CODEX_` prefix. The default
request timeout is one second, the shared HTTP budget is four seconds, and a
flush performs at most four calls. These can be tuned with
`POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS`,
`POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS`, and
`POWERCONTEXT_CODEX_FLUSH_MAX_CALLS`, while the outer Codex hook remains capped
at ten seconds.

Context returned by the hook is labelled as untrusted history. Recall, capture,
and flush fail independently; an unavailable Server never blocks normal Codex
work. For an empty result, version mismatch, unavailable Server, or invalid
response, the hook writes one diagnostic JSON line to stderr. Diagnostics contain
status and byte counts only—never the query, scope, content, citation, or response body.
