# Codex integration

`plugins/powercontext` contains the PowerContext Codex plugin distributed by the repository marketplace.

Install the PowerContext tool first, then configure the plugin:

```bash
powercontext setup codex --source oceanbase/powercontext --ref main
powercontext server run
```

The plugin is a client of the running Server:

- the `UserPromptSubmit` hook searches Memory and captures the current prompt as Source evidence;
- Codex uses Streamable HTTP MCP for explicit Memory reads and writes;
- Server or transport failures do not block normal Codex work.

The installed plugin defaults to `http://127.0.0.1:8000/mcp`. Prompt capture can be disabled with
`POWERCONTEXT_CODEX_CAPTURE_PROMPTS=false`. Set `POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE=true` only for tests that need
captured Source evidence processed before the next prompt.

Run the integration tests from a repository checkout:

```bash
uv run pytest tests/codex_plugin tests/e2e/test_codex_service_chain.py tests/e2e/test_mcp_transport.py
```
