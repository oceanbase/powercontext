---
title: Antigravity
description: Connect Antigravity to PowerContext for scoped recall, optional prompt capture, and MCP tools.
---

# Antigravity

Install the native PowerContext plugin with the same source ref as your running Server. The hooks use Python 3.9 or newer. Run the PowerContext CLI in the same user account as Antigravity.

```bash
powercontext setup antigravity --server-url http://127.0.0.1:8000
powercontext doctor antigravity
```

Setup copies the bundle to `~/.gemini/config/plugins/powercontext/` and configures the Streamable HTTP MCP endpoint. Restart Antigravity and enable PowerContext in its plugin manager if needed. Doctor checks the local bundle, hook registration, and Server connection. The [Antigravity plugin manager](https://antigravity.google/docs/plugins?tab=cli) shows whether the host has enabled the plugin.

Use `powercontext config agent antigravity --server-url <url>` to change the endpoint. If your Server requires a bearer token, supply it as `POWERCONTEXT_ANTIGRAVITY_AUTHORIZATION` during setup or configuration. PowerContext writes the header to the user-local MCP configuration and reuses it for hook requests to that endpoint. A token is preserved on reconfiguration of the same endpoint and removed when switching endpoints unless you supply a new token.

## Automatic context and capture

- At `SessionStart`, the plugin resolves and persists the conversation's Scope and injects available bootstrap context.
- At `PreInvocation`, the plugin uses the latest user text to recall context. Repeated invocations with identical text in the same conversation are coalesced until the text changes. Antigravity's hook payload supplies an invocation number but no user-turn ID, so consecutive identical submissions are coalesced too.
- Capture is off by default. Enable it with `powercontext config agent antigravity --capture-prompts` and disable it with `--no-capture-prompts`. The shared privacy filter scrubs recognized credentials from recall queries and skips capture for those prompts.

If the PowerContext Gateway handles the host’s model requests, launch Antigravity with `POWERCONTEXT_ANTIGRAVITY_PROMPT_RECALL=false` and keep prompt capture disabled so the Gateway owns both operations during the turn.

The bundled `powercontext-project-context` Skill uses the plugin's MCP tools for explicit memory and handoff operations. See the [Antigravity hook contract](https://antigravity.google/docs/hooks) for the native event payloads.
