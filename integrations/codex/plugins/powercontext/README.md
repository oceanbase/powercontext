# PowerContext for Codex

This plugin is a thin Codex integration for a running PowerContext Server. It
does not embed storage or start the server.

The integration deliberately uses each public surface for the job it fits:

- the `UserPromptSubmit` hook first calls `POST /v1/memory/search`, then
  captures the current prompt with `POST /v1/sources/content`;
- Streamable HTTP MCP at `http://127.0.0.1:8000/mcp` gives Codex the curated
  memory tools;
- the Python `PowerContextClient` is used by the isolated evaluation harness
  to seed and verify state through the same OpenAPI-backed HTTP contract.

Start a local server before using the integration:

```bash
uv run powercontext server run
```

Set `POWERCONTEXT_SCOPE_ID` to override Git-based project scoping. Set
`POWERCONTEXT_HTTP_URL` only when the hook should call a different base URL.
Plain HTTP is accepted only for loopback hosts.

Prompt capture is enabled by default. Set `POWERCONTEXT_CAPTURE_PROMPTS=false`
when prompts must not be persisted. Captured Sources are normally processed by
the Server scheduler. For tests or read-your-write workflows, set
`POWERCONTEXT_FLUSH_ON_CAPTURE=true`; the hook then flushes until the captured
Source position is processed.

Memory returned by the hook is labelled as untrusted history. Recall, capture,
and flush fail independently; an unavailable Server never blocks normal Codex
work.
