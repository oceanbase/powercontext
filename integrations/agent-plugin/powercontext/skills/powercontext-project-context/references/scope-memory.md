# Scope and Memory

## Resolve Scope

Before the first data-plane call, use `resolve_scope_binding` to obtain one
Server-owned Scope. Supply an explicit Scope only when the host or user selected
an existing one; otherwise let the Server use its durable binding or default.
Reuse the returned `scope_id` for the task. Never derive or invent a Scope ID
from a repository, directory, branch, Agent, or prompt.

When the user explicitly establishes an independent result boundary, inspect
the current Scope, call `create_scope` with the established Parent and
references, then bind the host identity when the integration supports durable
binding. Creating or switching a Scope is a host action, not an implicit result
of an ordinary Memory or Handoff call.

## Read Memory

- Use `search_memory` with a focused query, `mode: "auto"`, and no more than
  eight results.
- Use `list_memory_entries` for an explicitly requested inventory of active entries in the current scope.
- Set `include_inactive` to `true` only when the user explicitly asks to audit
  retired entries or the complete Memory snapshot.
- Use `get_memory_entry` with the exact returned `citation` when immutable entry
  details are needed.

## Write Memory Only On Request

Call `remember_memory` only when the user explicitly asks to persist reusable
project context.

Store concise, self-contained entries such as a decision, constraint,
current-state, task-outcome, or next-step. Never store secrets, credentials,
private tokens, or transient logs.

Before `revise_memory_entry` or `retire_memory_entry`, read the current entry.
Pass its exact `citation`; the citation's Memory revision is the concurrency
check. After a conflict, refresh the current entry and retry once only if the
user's requested change still applies.

Automatic hooks attempt bounded context and Source capture; neither substitutes for an explicit Memory save.
