# PowerContext for Claude Code

This plugin adds automatic project-context recall, ordinary user-prompt Source
capture, explicit Memory operations, and inspectable Handoffs to Claude Code.

Automatic recall and prompt capture run on `UserPromptSubmit`. The plugin never
reads the Claude Code transcript or captures Claude's final response in v1.
Prompt Sources are evidence and are never marked as `task-outcome` by the hook.

Scope is resolved by the Server from an explicit override, the current session
or workspace binding, and finally the Server default. Bindings let multiple
agents share the same Scope without deriving identities locally.

The plugin defaults to `http://127.0.0.1:8000`. Its Hook and MCP transport share
`POWERCONTEXT_CLAUDE_AUTHORIZATION` when optional bearer authentication is
enabled. Prompt capture can be disabled through the plugin's `capture_prompts`
option or by setting `POWERCONTEXT_CLAUDE_CAPTURE_PROMPTS=false`.

HTTPS and loopback HTTP work by default. To persist an explicit non-loopback
HTTP endpoint for this host:

```bash
powercontext setup claude-code --server-url http://memory.example:8000 --allow-insecure-http
```

Setup configures the native MCP URL and stores nonsecret settings under
`hosts.claude-code` in `~/.config/powercontext/clients.json` (override with
`POWERCONTEXT_CLIENT_CONFIG_FILE`). The hook selects its URL from
`POWERCONTEXT_CLAUDE_SERVER_URL`, the `server_url` plugin option,
`POWERCONTEXT_CLIENT_SERVER_URL`, saved settings, then the loopback default.
Keep the native MCP URL and hook URL aligned when making manual changes.

For HTTP consent, an explicit settings constructor argument takes precedence,
then `POWERCONTEXT_CLAUDE_ALLOW_INSECURE_HTTP`,
`POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP`, the optional `allow_insecure_http`
plugin option, and saved consent. Explicit `false` overrides lower-priority
consent, and invalid booleans are rejected. Plugin-option and saved consent
apply only to their configured endpoint after removing trailing slashes and
`/mcp`; an environment URL change does not authorize another server.

This setting governs the PowerContext hook. Claude Code owns native MCP
transport policy. HTTP exposes request content and authorization headers on
the network; the opt-in never disables HTTPS certificate verification.

The Hook fails open on transport, authentication, contract, and capture errors.
MCP remains available for explicit Memory maintenance and the inspected
Handoff lifecycle when the Server is reachable.
