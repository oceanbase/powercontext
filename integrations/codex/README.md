# Codex integration

`plugins/powercontext` contains the PowerContext Codex plugin distributed by the repository marketplace. For
installation, configuration, and troubleshooting, use the user documentation: [Codex quickstart](../../docs/en/docs/tutorials/codex-quickstart.md),
[Configure Codex](../../docs/en/docs/how-to/configure-codex.md), and
[Troubleshoot](../../docs/en/docs/how-to/troubleshoot.md).

The plugin is a client of the running Server:

- the `UserPromptSubmit` hook asks the Runtime for one final, bounded context value and captures the current prompt as
  independent Source evidence;
- Codex uses Streamable HTTP MCP for explicit Memory reads and writes;
- saying `交接`, `交接当前工作`, or `handoff this work` triggers the `project-context` Skill to inspect current facts,
  prepare the current work, and commit the returned Handoff in one turn;
- Server or transport failures do not block normal Codex work.

Automatic recall calls `POST /v1/context/prepare` once per prompt. The Runtime selects and renders untrusted history
with exact citations under the requested total byte budget. The Hook validates `PreparedContext` and injects its
content unchanged; it never performs a second selection or falls back to the old raw search-result renderer. Error
outcomes are returned as content-free diagnostic JSON in the top-level `systemMessage` on stdout; when context is
also available, the same response includes `hookSpecificOutput`.

The installed plugin defaults to `http://127.0.0.1:8000/mcp`. The plugin configuration and Hook use only
environment-backed values for optional credentials; they do not store tokens in the plugin configuration.

Run the integration tests from a repository checkout:

```bash
uv run pytest tests/codex_plugin tests/e2e/test_codex_service_chain.py tests/e2e/test_mcp_transport.py
```
