---
title: OpenCode
description: Install the PowerContext OpenCode plugin and configure automatic recall, prompt capture, and authenticated Server access.
status: community
---

# OpenCode

`community`

## Install or refresh the plugin

OpenCode 1.18.21 or newer in the 1.x line is required. Install the plugin from the same PowerContext Git ref as the
Server and CLI:

```bash
powercontext setup opencode
```

The setup command registers the native plugin globally and installs its owned `powercontext-project-context` Skill under the
OpenCode config directory. It refuses to replace an existing same-name Skill that is not owned by PowerContext. A
local checkout is also supported:

```bash
powercontext setup opencode --source .
```

Start the Server, then open a new OpenCode session:

```bash
powercontext server run
opencode
```

## Understand the behavior

For each normal user turn, the plugin asks `POST /v1/context/prepare` for one bounded context value and independently
captures eligible prompt text through `POST /v1/sources/content`. Prepared context is labelled as untrusted history
and inserted transiently before model dispatch; it is not stored in the OpenCode transcript. When OpenCode's model
traffic goes through the gateway, turn prompt-time recall off so the gateway prepares context; see
[Leave prompt-time context to the gateway](#leave-prompt-time-context-to-the-gateway).

Named `pc_*` tools expose curated Memory, Handoff, Experience, Skill, and read-only Candidate operations. OpenCode
asks for confirmation before a durable mutation. Candidate approval and rejection remain explicit human CLI actions.

The plugin gives OpenCode per-turn guidance to call `pc_task_outcome` when the task is actually complete or
explicitly interrupted. Intermediate turns, `Stop`, and session end leave the task outcome open on their own.
Recording uses the current Scope and the `record_task_outcome` permission pattern.

The confirmation is OpenCode's `powercontext` permission, which the plugin sets to `ask`. A rule you configure that
matches it, such as `"*": "allow"` in `permission` or in an agent's `permission`, takes precedence. Each request uses
the operation as its pattern, so `"permission": {"powercontext": {"remember_memory": "allow"}}` saves memories
without asking while other writes still ask. Answering "Allow always" approves only that call.

In `opencode run`, OpenCode rejects each permission request and ends the turn, so a non-interactive run stops at its
first PowerContext write. Allow the operations such a run needs, for example with
`"permission": {"powercontext": "allow"}`, rather than approving every permission for the run.

## Configure the connection

Set variables before starting OpenCode:

```bash
export POWERCONTEXT_OPENCODE_BASE_URL=http://127.0.0.1:8000
export POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS=true
opencode
```

Each OpenCode session resolves an explicit `POWERCONTEXT_OPENCODE_SCOPE_ID`, its durable Session binding, its
workspace binding, then the Server default. The resolved Scope is fixed to the Session so resume keeps the same
boundary. In a linked Git worktree that has no workspace binding of its own, the binding of the same directory in the
main checkout applies, so a checkout's binding also covers its linked worktrees. That fallback comes from the real path
that Git reports, so it matches a checkout binding made from a path without symlinks. Set the explicit variable only to an existing Server-owned Scope.

For a Server using optional bearer authentication, set the complete header in
`POWERCONTEXT_OPENCODE_AUTHORIZATION`. Never put credentials in the URL. Plain HTTP is allowed on loopback by default;
for non-loopback HTTP, explicitly set `POWERCONTEXT_OPENCODE_ALLOW_INSECURE_HTTP=true`. HTTPS certificate validation
stays enabled. See [Connect to a remote Server](../operate/connect-remote-server.md) for setup and saved consent.
Set `POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS=false` when prompts must not be persisted as Source evidence.
`POWERCONTEXT_OPENCODE_PROMPT_RECALL` defaults to `true`; set it to `false` to stop preparing and injecting context
for each prompt. With recall off, the plugin also does not offer the `pc_prepare_context` tool; `pc_search` stays
available for explicit retrieval.

## Leave prompt-time context to the gateway

When OpenCode sends its model requests through the PowerContext gateway, the gateway prepares and injects context for
each turn and, with `PC_GATEWAY_SEDIMENT=on`, captures the turn. Turn the plugin's prompt-time recall and prompt
capture off before starting OpenCode:

```bash
export POWERCONTEXT_OPENCODE_PROMPT_RECALL=false
export POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS=false
opencode
```

With recall off, the plugin sends no preparation request, adds no context to the user message, and does not offer the
`pc_prepare_context` tool. The plugin still captures prompts while
`POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS` is `true`; with both off, it sends no request for a prompt. The Session Scope,
the other `pc_*` tools, and the TUI keep working. Keep the default where OpenCode calls its model directly, because the
gateway does not carry that traffic.

You can continue an existing OpenCode session. OpenCode discards the context added to a user message by prompt-time
recall and keeps `pc_prepare_context` tool results in the session history. When a replayed tool result carries
a complete PowerContext prepared-context block, the gateway replaces that result's text with
`PowerContext plugin prepared context omitted by the gateway.` and keeps its tool call ID. The request and response
still go through Core's normal checks.

With recall off, prompt-time context comes only from the gateway. The gateway prepares and captures in the Scope
selected by `X-PowerContext-Scope-ID` and uses `PC_GATEWAY_DEFAULT_SCOPE` when the header is absent. To match the Scope
the plugin resolves for the Session or workspace, use that Scope in the header or as the default. See
[Gateway request Scope](../operate/deploy-server.md#gateway-request-scope).

OpenCode sends its session ID in the `x-opencode-session-id` header of every model request. A gateway connected to
the Core over UDS therefore prepares and injects each OpenCode turn by default; a standalone gateway needs
`PC_GATEWAY_INJECTION=on`. See [Gateway context injection](../operate/deploy-server.md#gateway-context-injection).

OpenCode always streams. Configure the OpenCode provider that points at the gateway's `/gateway/v1` with
`@ai-sdk/openai`, which uses the Responses route. The gateway requests a Responses result from the upstream model
without streaming and replays the released result as a stream, so streaming does not stop these turns or their
capture in any Core [privacy mode](../operate/inference-protection.md#configure-a-supported-target). An enforcing
Core still applies its other request and response rules. `@ai-sdk/openai-compatible` uses the Chat Completions route
and requires a Core target profile with the `openai-chat` protocol.
An enforcing Core withholds streamed Chat Completions responses, and the gateway returns HTTP 400. With a
non-enforcing Core, including the default `disabled` mode, the gateway releases the stream unchanged. With
`PC_GATEWAY_SEDIMENT=on`, it also assembles the assistant's text from a terminal stream and captures the completed turn.

`@ai-sdk/openai-compatible` uses the Chat route and requires `PC_GATEWAY_UPSTREAM_PROFILE` to select a Core target
whose endpoint, `openai-chat` protocol and model match the request. `@ai-sdk/openai` uses Responses and requires
`PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES` to select the corresponding `openai-responses` target; the Chat setting does
not enable Responses. Without the Responses setting, requests return HTTP 501 `responses_route_disabled`. Do not set
`PC_GATEWAY_UPSTREAM_URL*`; these legacy variables prevent startup. Core access control must be `enforced`,
`PC_GATEWAY_CORE_TOKEN` must identify an authenticated service principal, and a nonempty `PC_GATEWAY_API_KEY` is
mandatory even on loopback. Grant the service principal `gateway.user` on every Scope selected with
`X-PowerContext-Scope-ID`, not only `PC_GATEWAY_DEFAULT_SCOPE`, and `server.observer` on the Server resource for
`POWERCONTEXT_SERVER_ACCESS_DEPLOYMENT_ID` (default `powercontext`). Because capture is
enabled, grant `scope.contributor` instead of `scope.viewer`. See [Gateway authentication and target requirements](../operate/deploy-server.md#gateway-model-destination).

## Verify the installation

### Recover a failed refresh

If setup reports that it could not restore a target, keep the backup path named in the error. Restore that backup
to the reported target before retrying. Setup refuses to treat a missing target with a retained backup as a fresh
installation.

If an active Skill or multiple backups exist, compare and reconcile their contents before choosing a version.
Preserve personal files and notes outside managed regions; deleting backups just to bypass the check can discard
them. After recovery, rerun setup and the checks below. Recovery covers caught filesystem failures; it does not
guarantee recovery from process crashes or concurrent setups.

### Check the plugin

```bash
powercontext doctor
powercontext doctor opencode
```

The integration-specific doctor checks the OpenCode version, resolved plugin configuration, and the owned Skill.
An unavailable Server does not block OpenCode and is checked separately by the default doctor command.

## Automatic input privacy

See [Automatic prompt privacy](index.md#automatic-prompt-privacy) for this host's capture default, disable control, credential recognition limits and independent read-only query handling. Recognized inputs are skipped for capture; accepted Source content and event identity are not rewritten.
