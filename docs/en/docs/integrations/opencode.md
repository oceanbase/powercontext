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
for each prompt.

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

With recall off, the plugin sends no preparation request and adds no context to the user message. It still captures
prompts while `POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS` is `true`; with both off, it sends no request for a prompt. The
Session Scope, `pc_*` tools, and the TUI keep working. Keep the default where OpenCode calls its model directly,
because the gateway does not carry that traffic.

With recall off, prompt-time context comes only from the gateway. The gateway prepares and captures in the Scope set
by `PC_GATEWAY_DEFAULT_SCOPE`, not in the Scope this plugin resolves for the Session or workspace. Set it to the same
Scope.

OpenCode always streams, and the gateway completes streamed turns only on its Responses route. Configure the OpenCode
provider that points at the gateway's `/gateway/v1` with `@ai-sdk/openai`. On the Chat Completions route
(`@ai-sdk/openai-compatible`), Core withholds streamed responses and the turn fails.

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
