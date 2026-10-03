---
status: community
title: Dify
description: Connect legacy Workflow Agent and Agent V2 to PowerContext external memory.
---

# Dify

`community` · `experimental`

PowerContext supplies external memory through a Dify tool plugin and a separate Function Calling strategy plugin.
Agent V2 additionally needs the native external-memory layer in the Dify `feat/agent-v2-mem` source branch. An unmodified
Dify release cannot acquire this native layer by installing a Marketplace plugin.

## Install and configure

Build the two package directories under `integrations/dify/` with the official Dify plugin CLI and install the resulting
packages from Dify's plugin management page. The tool package depends on an immutable PowerContext source commit;
run a compatible Server. The repository files `integrations/dify/README.md` and `ACCEPTANCE.md` contain development commands,
the callback protocol and deployed acceptance steps.

The PowerContext provider credential contains the Server URL, bearer token and a stable namespace unique to the Dify
workspace/deployment. HTTPS is the default transport requirement outside loopback. An explicit private-network HTTP option
is available. Remember that loopback inside a plugin container points to that container.

Leave the optional Scope ID empty for per-identity bindings. By default, identity is the Dify runtime app and user ID.
A configured business identifier intentionally shares memory between invocations within that app. The bridge hashes
`[namespace, app_id, subject_kind, subject_id]` as compact UTF-8 JSON to form a `dify` Scope binding key. Pre-create the Scope
and binding through the admin API or `integrations/dify/provision_scope.py`.

Resolution always sets `allow_default=false`. Missing bindings degrade recall/capture and never select the default Scope.
An explicit Scope ID shares one existing Scope for all uses of that credential. Scope selection is separate from access
control: use appropriate PC authorization or separate credentials/instances across trust boundaries.

## Legacy Workflow Agent

Install both packages. Select **PowerContext Function Calling**, then add `prepare_context`, `capture_event` and business
tools. Configure each callback's PC credential, the model, query and instructions. Set `context_window` when the model
provider does not report it. The strategy removes the two callbacks from model tool selection.

Recall runs automatically at the start. Visible user/model text and tool calls/results are captured during execution.
The strategy reserves output space, clears older tool results and summarizes older complete interactions while retaining
the first user request. Text and schema UTF-8 byte counts provide a conservative input estimate. Inputs that still cannot
fit are rejected before calling the model. Image/audio token costs are not estimated by this text-oriented strategy.

## Agent V2 and standalone Agent apps

Build the Dify API, Web and `dify-agent` runtime with the external-memory changes. Add and authorize the memory tools under
**Tools**, then configure **Advanced settings → External memory**. Select recall/capture operations, user or business
identity, byte budgets and whether to capture history. The shared Agent configuration works for both entrypoints.

Configuration stores tool and credential references, not secrets. The API resolves them through the existing tenant-scoped
tool owner at execution time. Recall enters a transient instruction field that participates in Dify's existing compaction
budget and is cleared when history is saved. Native history and deferred-tool resume state remain owned by Dify.

## Memory lifecycle and limits

`capture_event` writes ContentSource evidence. The PC extraction pipeline and scheduler, or an explicit `flush_memory`
checkpoint, turn accepted evidence into searchable Memory. Capture does not promise immediate recall. The plugin also
provides `search_memory`, `remember_memory`, `get_memory_entry`, `revise_memory_entry` and `retire_memory_entry`.

Recall defaults to 8,000 bytes and each event to 8,192 bytes, configurable between 512 and 32,768. A known input budget
further restricts recall. Callback timeouts are 10 seconds; failed callbacks are suppressed for the rest of the run and
retried on the next run. Memory failures do not intentionally stop business execution. Dify logs degraded-memory outcomes.

Capture redacts credential-like fields and the PC token, drops arbitrary metadata and truncates oversized events. It
excludes hidden reasoning and does not intentionally collect binary attachments. Free-text data can still contain personal
or sensitive material. Review the plugin privacy policy and select capture settings appropriate to the application.

The durable boundary is successful Server acceptance. Deterministic source IDs support stable retries, but there is no
persistent client outbox or exactly-once guarantee. Cancellation or process termination can lose unacknowledged events.

Marketplace release and a Dify release containing the native layer remain separate delivery steps. Source tests cover SDK
registration, runtime execution, API/UI configuration and the actual PC HTTP/SQLite capture-to-recall chain; they do not
replace acceptance against a running CE deployment, daemon and real model plugin.
