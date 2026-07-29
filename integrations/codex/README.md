# Codex integration

`plugins/powercontext` contains the PowerContext Codex plugin distributed by the repository marketplace.

Install the PowerContext tool first, then configure the plugin:

```bash
powercontext setup codex --source oceanbase/powercontext --ref main
powercontext server run
```

The plugin is a client of the running Server:

- the `UserPromptSubmit` hook asks the Runtime for one final, bounded context value and captures the current prompt as
  independent Source evidence;
- Codex uses Streamable HTTP MCP for explicit Memory reads and writes;
- Server or transport failures do not block normal Codex work.

Automatic recall calls `POST /v1/context/prepare` once per prompt. The Runtime selects and renders untrusted history
with exact citations under the requested total byte budget. The Hook validates `PreparedContext` and injects its
content unchanged; it never performs a second selection or falls back to the old raw search-result renderer. Empty
and error outcomes are written to stderr as content-free diagnostic JSON.

The installed plugin defaults to `http://127.0.0.1:8000/mcp`. Prompt capture can be disabled with
`POWERCONTEXT_CODEX_CAPTURE_PROMPTS=false`. Set `POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE=true` only for tests that need
captured Source evidence processed before the next prompt.

Run the integration tests from a repository checkout:

```bash
uv run pytest tests/codex_plugin tests/e2e/test_codex_service_chain.py tests/e2e/test_mcp_transport.py
```
