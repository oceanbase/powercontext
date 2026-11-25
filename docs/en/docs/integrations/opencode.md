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
boundary. Set the explicit variable only to an existing Server-owned Scope.

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
each turn and, with `PC_GATEWAY_SEDIMENT=on`, captures the turn. Core checks every request the gateway forwards and
rejects a prepared-context block that the gateway did not inject, so a turn that also carries the plugin's context
fails. Turn the plugin's prompt-time recall and prompt capture off before starting OpenCode:

```bash
export POWERCONTEXT_OPENCODE_PROMPT_RECALL=false
export POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS=false
opencode
```

With recall off, the plugin sends no preparation request, adds no context to the user message, and does not offer the
`pc_prepare_context` tool. That tool's result stays in the session and carries a prepared-context block. Core
would reject the request that returns the result to the model, and every later request of the session, with HTTP 422
in every privacy mode; the gateway returns HTTP 503 `gate_paused`. The plugin still captures prompts while
`POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS` is `true`; with both off, it sends no request for a prompt. The Session Scope,
the other `pc_*` tools, and the TUI keep working. Keep the default where OpenCode calls its model directly, because the
gateway does not carry that traffic.

OpenCode does not store the context that recall adds to a user message, so a session started with recall on can
continue behind the gateway. A `pc_prepare_context` result is stored, however: start a new OpenCode session instead of
continuing one in which the model called that tool.

With recall off, prompt-time context comes only from the gateway. The gateway prepares and captures in the Scope set
by `PC_GATEWAY_DEFAULT_SCOPE`, not in the Scope this plugin resolves for the Session or workspace. Set it to the same
Scope.

OpenCode sends its session ID in the `x-opencode-session-id` header of every model request. A gateway connected to
the Core over UDS therefore prepares and injects each OpenCode turn by default; a standalone gateway needs
`PC_GATEWAY_INJECTION=on`. See [Gateway context injection](../operate/deploy-server.md#gateway-context-injection).

OpenCode always streams. Configure the OpenCode provider that points at the gateway's `/gateway/v1` with
`@ai-sdk/openai`, which uses the Responses route. The gateway requests a Responses result from the upstream model
without streaming and replays the released result as a stream, so streaming does not stop these turns or their
capture in any Core [privacy mode](../operate/inference-protection.md#configure-a-supported-target). An enforcing
Core still applies its other request and response rules. `@ai-sdk/openai-compatible` uses the Chat Completions route.
An enforcing Core withholds streamed Chat Completions responses, and the gateway returns HTTP 400. With a
non-enforcing Core, including the default `disabled` mode, the gateway releases the stream unchanged. With
`PC_GATEWAY_SEDIMENT=on`, it also assembles the assistant's text from a terminal stream and captures the completed turn.

Core also needs a target for the profile that `PC_GATEWAY_UPSTREAM_PROFILE` names (`default` unless set), in every
privacy mode, including `disabled`. Without one, Core withholds each request with `target_unsupported`, and the gateway
returns HTTP 400 for every turn on either route, not only for streamed Chat Completions turns. The `reason_codes` field
of the gateway's response body names the reason and tells the two cases apart. See
[Configure a supported target](../operate/inference-protection.md#configure-a-supported-target).

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
