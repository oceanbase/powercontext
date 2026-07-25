# Codex integration

`plugins/powercontext` is the repository-local Codex plugin. It is a client of
the existing PowerContext Server and keeps transport responsibilities separate:

- the prompt hook uses public HTTP endpoints to recall Memory, then capture the
  current Codex input as a Content Source;
- Codex uses the Server's curated Streamable HTTP MCP projection for agent
  reads and explicit writes.

## Deploy the Server

Source-derived Memory requires an extraction model. Scheduled processing is the
recommended production mode:

```bash
export POWERCONTEXT_SERVER_STORAGE='{"kind":"sqlite","path":"/var/lib/powercontext/runtime.db"}'
export POWERCONTEXT_SERVER_INFERENCE='{"generation_model":"<provider:model>"}'
export POWERCONTEXT_SERVER_RUNTIME='{"source_window_limit":100,"schedule_seconds":30}'
uv run powercontext server run --host 127.0.0.1 --port 8000
```

Verify that `/health/ready` is ready and that `/v1/capabilities` reports
`memory_extraction: true` before installing the plugin.

## Install and use the plugin

```bash
codex plugin marketplace add integrations/codex
codex plugin add powercontext@powercontext-local
```

The hook requires `uv`; its isolated plugin project supplies the validated
`pydantic-settings` configuration runtime.

Codex and the hook use the same endpoint from the plugin's `.mcp.json`. The hook
validates that MCP URL and derives the HTTP API base from its final `/mcp` path
segment, so there is no second environment URL to drift.

Start a new Codex thread after installation. On each prompt, the plugin recalls
the current Memory head and captures the new input as a Content Source. The
Server scheduler later processes pending Source windows. Set
`POWERCONTEXT_CODEX_CAPTURE_PROMPTS=false` to opt out.

For a local read-your-write workflow without a scheduler:

```bash
export POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE=true
```

This makes the hook wait until the captured journal position has been processed;
it adds extraction latency to every prompt and is intended for tests and
interactive debugging.

## Validate

```bash
uv run pytest tests/codex_plugin tests/e2e/test_mcp_transport.py
```
