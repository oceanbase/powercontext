# Codex integration

`official`

`plugins/powercontext` contains the PowerContext Codex plugin distributed by the repository marketplace. For
installation, configuration, and troubleshooting, use the user documentation: [Codex quickstart](../../docs/en/docs/workflows/codex-workflow.md),
[Configure Codex](../../docs/en/docs/integrations/codex.md), and
[Troubleshoot](../../docs/en/docs/operate/troubleshoot.md).

The plugin is a client of the running Server:

- the `UserPromptSubmit` hook asks the Runtime for one final, bounded context value and captures the current prompt as
  independent Source evidence;
- a ten-second-bounded `Stop` hook reports scoped recall-token estimates after each completed turn;
- Codex uses Streamable HTTP MCP for explicit Memory reads and writes;
- the generated `powercontext-project-context` Skill uses the Agent Plugin baseline for Memory and temporary work
  transfer; committing a durable Handoff requires explicit milestone intent;
- Server or transport failures do not block normal Codex work.

Automatic recall calls `POST /v1/context/prepare` once per prompt. The Runtime selects and renders untrusted history
with exact citations under the requested total byte budget. The Hook validates `PreparedContext` and injects its
content alongside the resolved Scope; it never performs a second selection or falls back to the old raw search-result renderer. Error
outcomes are returned as content-free diagnostic JSON in the top-level `systemMessage` on stdout; when context is
also available, the same response includes `hookSpecificOutput`.

The installed plugin defaults to `http://127.0.0.1:8000/mcp/`. Setup configures a native MCP credential helper
that reads the same URL-bound credential as the Hook. Process authorization remains an override;
tokens are never stored in the plugin configuration.

Run the integration tests from a repository checkout:

```bash
uv run pytest tests/codex_plugin tests/e2e/test_codex_service_chain.py tests/e2e/test_mcp_transport.py
```
