# PowerContext for Claude Code

This plugin adds automatic project-context recall, ordinary user-prompt Source
capture, explicit Memory operations, and inspectable Handoffs to Claude Code.

Automatic recall and prompt capture run on `UserPromptSubmit`. The plugin never
reads the Claude Code transcript or captures Claude's final response in v1.
Prompt Sources are evidence and are never marked as `task-outcome` by the hook.

Project scope is resolved from an explicit override, the normalized Git origin,
or a hash of the resolved local project directory, in that order. The Git rule
matches the Codex plugin so both agents can use the same project Memory.

The plugin defaults to `http://127.0.0.1:8000`. Its Hook and MCP transport share
`POWERCONTEXT_CLAUDE_AUTHORIZATION` when optional bearer authentication is
enabled. Prompt capture can be disabled through the plugin's `capture_prompts`
option or by setting `POWERCONTEXT_CLAUDE_CAPTURE_PROMPTS=false`.

The Hook fails open on transport, authentication, contract, and capture errors.
MCP remains available for explicit Memory maintenance and the inspected
Handoff lifecycle when the Server is reachable.
