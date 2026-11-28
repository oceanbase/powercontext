# Antigravity integration

`plugins/powercontext` contains the stdlib-only PowerContext thin host shell for Antigravity (desktop app and `agy` CLI).
All shared lifecycle flows (`SessionStart`, `UserPromptSubmit`, Scope binding, prompt privacy) are delegated to the generated `powercontext_lifecycle/` package using system `python3` with zero third-party runtime dependencies.

## Plugin layout

- `plugin.json` — Antigravity native plugin manifest
- `mcp_config.json` — Streamable HTTP MCP endpoint (`http://127.0.0.1:8000/mcp/` by default)
- `hooks.json` — registers 3 Antigravity lifecycle hooks using stdlib `python3`:
  - `SessionStart`: delegates to `run_session_start` to bind/persist the conversation Scope and optionally inject bounded bootstrap context (`injectSteps`)
  - `PreInvocation`: deduplicates turns via `invocationNum` + transcript step projection, delegates to `run_user_prompt_submit` (`/v1/context/prepare` + `/v1/sources/content`), and translates prepared context/diagnostics into `injectSteps`
  - `PreToolUse`: intercepts `call_mcp_tool` (for `powercontext`) and `mcp_powercontext*` calls to inject the resolved `scope_id` / `selection` / `binding_keys` / `key` via `overwrite`
- `scripts/scope_binding.py` — inspect, bind, or clear the workspace Scope
- `skills/powercontext-project-context/` — routing guidance and reference docs

## Environment variables

- `POWERCONTEXT_ANTIGRAVITY_SERVER_URL` (fallback: `POWERCONTEXT_CLIENT_SERVER_URL`, `powercontext.json`, `mcp_config.json`, `http://127.0.0.1:8000`)
- `POWERCONTEXT_ANTIGRAVITY_AUTHORIZATION` (or `0600` `credentials.json` in `${PLUGIN_DATA}` / `~/.gemini/config/powercontext/`)
- `POWERCONTEXT_ANTIGRAVITY_ALLOW_INSECURE_HTTP`
- `POWERCONTEXT_ANTIGRAVITY_SCOPE_ID` (fallback: `POWERCONTEXT_SCOPE_ID`)
- `POWERCONTEXT_ANTIGRAVITY_BOOTSTRAP_CONTEXT` (default `false`), `POWERCONTEXT_ANTIGRAVITY_BOOTSTRAP_MAX_BYTES` (default `4096`), `POWERCONTEXT_ANTIGRAVITY_BOOTSTRAP_HANDOFF`
- `POWERCONTEXT_ANTIGRAVITY_PROMPT_RECALL` (default from `powercontext.json` `prompt_recall`, fallback `true`) — set `false` when model traffic goes through the PowerContext Gateway
- `POWERCONTEXT_ANTIGRAVITY_CAPTURE_PROMPTS` (default from `powercontext.json` `capture_prompts`, fallback `true`)
- `POWERCONTEXT_ANTIGRAVITY_INCLUDE_CODE` (default `false`), `POWERCONTEXT_ANTIGRAVITY_CONTEXT_ASSEMBLY`
- `POWERCONTEXT_ANTIGRAVITY_FLUSH_ON_CAPTURE` (default `false`), `POWERCONTEXT_ANTIGRAVITY_FLUSH_MAX_CALLS` (default `4`)
- `POWERCONTEXT_ANTIGRAVITY_REQUEST_TIMEOUT_SECONDS` (default `6.0`), `POWERCONTEXT_ANTIGRAVITY_HTTP_BUDGET_SECONDS` (default `9.0`)

## Local install

```sh
powercontext setup antigravity --source .
powercontext doctor antigravity
```
