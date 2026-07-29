---
title: Configure Codex
description: Install the PowerContext Codex plugin and control its local behavior.
---

# Configure Codex

## Install or refresh the plugin

Run:

```bash
powercontext setup codex --source oceanbase/powercontext --ref main
```

The command adds the repository as a Codex marketplace, installs the PowerContext plugin, and creates the user data
directory. It is safe to run again. Pass the same `--ref` used to install the PowerContext tool.

Open a new Codex session after setup. Use `/hooks` to inspect and, when prompted, trust the PowerContext
`UserPromptSubmit` hook.

## Understand what the plugin does

The plugin has two paths to the same Server:

- a prompt hook asks the Runtime to prepare one final, bounded context value, then independently captures the
  user's prompt as Source evidence;
- MCP gives Codex explicit tools to remember, search, revise, retire, and audit Memory.

Memory scope comes from the normalized Git remote when one is available, or from the project path otherwise. A later
Codex session opened in the same project resolves the same scope. Set `POWERCONTEXT_CODEX_SCOPE_ID` only when you need
an explicit scope that is independent of both.

The Hook calls `POST /v1/context/prepare` once before Codex analyzes the prompt. It requests an 8000-byte total budget,
strictly validates `powercontext.prepared-context.v1`, and injects the returned content unchanged. The Runtime labels
Memory-derived items as untrusted history, preserves exact citations, and owns final selection and rendering. Explicit
search remains available through the Client and MCP; it is not a second automatic recall step.

## Control prompt capture

Prompt capture is enabled by default. Disable it before starting Codex when the current work must not be recorded:

```bash
export POWERCONTEXT_CODEX_CAPTURE_PROMPTS=false
codex
```

Captured prompts become Source evidence. Turning capture on does not guarantee automatic Memory extraction; that
requires a configured generation model. Explicit `remember_memory` calls do not require a model.

For testing only, make the hook wait for captured Source processing:

```bash
export POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE=true
```

This adds inference latency to each prompt and is not the normal interactive setting.

If the Server is unavailable, hook recall and capture fail open. Codex work continues, and explicit Memory tools
report that the service is unavailable.

For a normal empty result or recall failure, the Hook writes a content-free JSON diagnostic to stderr. Outcomes include
`empty`, `version_mismatch`, `server_unavailable`, and `invalid_response`. The event never contains the query, scope,
prepared content, citation, or response body.
