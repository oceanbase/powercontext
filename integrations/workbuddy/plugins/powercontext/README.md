# PowerContext for WorkBuddy

This plugin adds automatic project-context recall, ordinary user-prompt Source
capture, explicit Memory operations, and inspectable Handoffs to WorkBuddy.

The integration uses each public surface for the job it fits:

- a `UserPromptSubmit` hook first calls `POST /v1/context/prepare`, then
  independently captures the current prompt with `POST /v1/sources/content`;
- Streamable HTTP MCP at `http://127.0.0.1:8000/mcp` gives WorkBuddy the
  curated Memory and work-continuity tools;
- the `project-context` Skill turns an imperative such as `交接`,
  `交接当前工作`, or `handoff this work` into one durable, committed Handoff.

Automatic recall and prompt capture run on `UserPromptSubmit`. The hook never
reads the WorkBuddy transcript or captures WorkBuddy's final response. Prompt
Sources are evidence and are never marked as `task-outcome` by the hook.

Scope is resolved by the Server from an explicit override, durable session and
workspace bindings, or the Server default, in that order. The plugin hashes a
workspace path only as an external binding key; it never derives a Scope ID.

The plugin defaults to `http://127.0.0.1:8000`. Its Hook and MCP transport share
`POWERCONTEXT_WORKBUDDY_AUTHORIZATION` when optional bearer authentication is
enabled. Prompt capture can be disabled with
`POWERCONTEXT_WORKBUDDY_CAPTURE_PROMPTS=false`.

The Hook fails open on transport, authentication, contract, and capture errors.
MCP remains available for explicit Memory maintenance and the inspected Handoff
lifecycle when the Server is reachable.

See [`integrations/workbuddy/README.md`](../../README.md) for installation steps
and [`docs/en/docs/how-to/configure-workbuddy.md`](../../../../docs/en/docs/how-to/configure-workbuddy.md)
for the full configuration guide.
