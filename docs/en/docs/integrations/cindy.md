---
title: Cindy
description: Use scoped recall and automatic capture in Cindy 0.1.115, with native MCP for the full tool catalog.
---

# Cindy

`experimental`

Cindy's `powercontext-hooks` Ghost shares one Server connection, credential, and Scope resolver between automatic
hooks and explicit recall tools. A separate native custom MCP connection provides the full Server tool catalog.
The compatibility target is [Cindy 0.1.115](https://github.com/makecindy/cindy/releases/tag/v0.1.115).

## Connect the Server

Follow [Install and run](../get-started/install-and-run.md) to install PowerContext and
[Deploy the Server](../operate/deploy-server.md) to configure and verify it. On supported personal macOS/Linux:

```bash
powercontext service install --env-file /path/to/powercontext.env
powercontext service status
```

Load the corresponding client connection settings, then run `powercontext ready` and `powercontext capabilities`.
Import `powercontext-cindy-hooks-0.1.0.cindy`, open plugin settings, and set the Server URL and optional Bearer token.
Credentials are stored with their Server URL in Cindy's secret store and injected only into the Node worker.

For explicit writes, Handoffs, reviews, and other Server tools, also add a native custom MCP connection:

| Field | Value |
| --- | --- |
| Name | `PowerContext` |
| Transport | `http` (Streamable HTTP) |
| URL | `http://127.0.0.1:8000/mcp`, with your actual port |
| Bearer token | Your Server token, without the `Bearer ` prefix; empty for anonymous local use |
| Additional headers | Empty for ordinary Bearer authentication |

Select the connection for the task and inspect its discovered tools. Use Streamable HTTP; legacy SSE is not available
through Cindy's Codex/Pi configuration path. For a Server on another machine, use a reachable HTTPS URL. Desktop
loopback is not the remote Agent's loopback; the SSH Pi path connects to custom MCP endpoints directly.
See [remote connections](../operate/connect-remote-server.md).

## Select a Scope and use explicit tools

In plugin settings, select an explicit Server-issued Scope ID, or leave it empty to use the current Cindy session's
registered binding. The host injects the trusted session identity through `sessionContext`; the model cannot supply
a different Scope or connection to plugin recall tools. Missing bindings fail the read without selecting a default.

| Plugin tool | Behavior |
| --- | --- |
| `status` | Current session ID and latest hook observation; no Server request |
| `resolve_scope` | Check the current binding against the Server |
| `prepare_context` | Return bounded, validated PreparedContext as historical evidence |
| `search_memory` | Search Memory within the same resolved Scope |

These read tools also work with automatic memory disabled. Each invocation resolves the binding again; they do not
reuse a stale Scope from an earlier message. They never capture the query or perform durable writes.

For the separate native MCP connection:

Inspect existing Scopes through `list_scopes` and `get_scope`. Create one only when requested.
Call `resolve_scope_binding` with the selected Server-issued `explicit_scope_id` and `allow_default: false`, then
reuse the returned `scope_id`. A missing or invalid binding stops the memory operation. Do not guess a Scope from
a directory or fall back to a global Scope.

Use the actual discovered namespace for `search_memory`, `remember_memory`, and `capture_content_source`.
Memory saves require user intent and result verification. Source capture accepts original material and retains the
same `source_id` for retries; it does not prove Memory extraction has completed. Handoff and candidate-review
operations use the same Server tools and approval boundaries as other MCP hosts.

The native MCP connection does not provide PowerContext with a trusted Cindy session identity or workspace path.
Its explicit tool calls must use the selected Scope, which can be checked with the plugin's `resolve_scope` tool.
The plugin cannot enforce Scope selection on the separate native MCP connection. Keep its Server URL and Scope
aligned with plugin settings. A workspace binding is not inferred from Agent-authored text.

Retrieved history does not override current instructions. On failure, report the failed operation and continue
ordinary work from available context. Inspect Server state before retrying an uncertain write.

## Enable automatic recall and prompt capture

Enable the plugin and select **Enable automatic memory** in its settings. Automatic hooks use the same connection
and Scope resolver as plugin recall tools. Users do not need DSH or additional runtime packages.

Select an explicit Scope in settings, or leave it empty and have an operator register the Host-issued session ID
through `set_scope_binding` with `integration: "cindy"` and `kind: "session"`. The plugin `status` tool reports the
current session ID and its latest observed hook outcome. Missing bindings allow the original message without memory operations;
they do not trigger Scope creation or default-Scope fallback.

The `will-user-message` hook recalls bounded PreparedContext and independently captures the original prompt as a
Source before dispatch. Capture is separately selectable and does not prove extraction or Memory production.
The captured text is the input to this adapter before its rewrite; an earlier Ghost may already have rewritten it.
Cindy does not supply a separate raw-draft field.
Explicit writes, Handoffs, and review use the native MCP tools.

A ready snapshot is appended as untrusted historical evidence. Cindy's rewrite updates the message displayed,
stored, and sent to the Agent. Failed or empty recall leaves the original message
available; late replies cannot rewrite it. Context obeys its UTF-8 budget and the complete message must fit Cindy's
16,000-character rewrite limit. The adapter sends an allow verdict after a local timeout; Cindy independently
enforces its three-second deadline for the complete hook dispatch.

The same delivery reuses its Source ID; a newly submitted message has a new Host hook ID. Edited messages carrying
the adapter's historical markers are allowed without recapture or reinjection. Flagged credentials are skipped.
Uncertain writes are reported without automatic retry. Local user-message dispatch and steering are covered;
remote, worker, scheduled, goal-continuation, and pre-compaction flows are not qualified. Use the CLI for Server readiness checks.

## Runtime and built-in memory

Ghost `main.js` runs in a browser sandbox. Cindy starts the bundled Node worker on demand and reuses it; its official
Node broker owns process management and JSON-RPC forwarding. The worker calls the same PowerContext HTTP operations
and PreparedContext validator as DSH. No additional bridge server is deployed. `cindy.fetch` requires HTTPS without
a custom port, so it cannot replace the worker for the default local HTTP Server.

Automatic context uses the supported `will-user-message` rewrite carrier. Plugin recall returns tool results.
Neither mechanism installs a system prompt or changes Cindy's internal memory. There is no public hook API for
reusing the separately configured native MCP connection.

Cindy's shared memory and the selected harness's native memory are separate settings. For a PowerContext-only
evaluation, disable Cindy shared memory and the relevant Claude Code/Codex native-memory switch in Cindy, then use
a new session. Turning shared memory off alone can restore the harness's native memory. The plugin does not change
these settings or delete existing memory; previously injected history may remain in existing sessions.

## Experimental support

Cindy 0.1.115 provider and hook gateway tests cover MCP tools, session isolation, prompt persistence, message rewrites,
and plugin recall against a real Server. Native MCP transport checks cover the provider's direct compatibility path;
Desktop's default MCP Apps facade is a separate boundary.

An opt-in live check runs the upstream `maker-core` Codex app-server host with an authenticated local Codex binary.
It checks answers from injected history and an explicit recall tool, then reads captured original prompts back from
the Server. See the [integration README](https://github.com/oceanbase/powercontext/blob/master/integrations/cindy/README.md) for commands. Host credential/message
bridges are simulated; Electron installation, secret-store UI, and complete Desktop harness assembly remain unverified.
Cindy is outside the qualified host catalog.
