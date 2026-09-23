# Scope and Memory

## Scope binding

Reuse the Scope resolved by the host and Server. Resolution follows the same order everywhere: an explicitly selected
Scope, trusted session bindings, trusted workspace bindings, then the Server default. Native adapters supply identity;
never derive a Scope ID from a repository, directory, branch, Agent, or prompt.

When a tool requires an explicit `scope_id` and the host has not supplied one, use `resolve_scope_binding` with the
trusted binding information provided by the host. Reuse the exact returned Scope for subsequent operations. An
unresolved or ambiguous boundary must not trigger a guessed binding or a switch to find missing history.

Create or change a binding only for an explicitly requested independent result boundary, using the established Parent
and references. Resolve and verify the intended Scope before a durable write or selecting `latest`. Ordinary Memory
and Handoff operations do not implicitly create or switch a Scope.

## Read context

- Use `search_memory` for explicit search or missing relevant history, with a focused query,
  the tool's result limit, and no more than eight results.
- Use `get_memory_entry` with the exact returned `citation` when immutable entry details are needed.
- Use `list_memory_entries` only for an explicitly requested inventory of active entries in the current Scope.
- Set `include_inactive: true` only for an explicit audit of retired entries or the complete Memory snapshot.

## Write only on request

Call `remember_memory` only when the user explicitly asks to persist reusable project context. Store concise,
self-contained decisions, constraints, current state, task outcomes, or next steps. Never store secrets or credentials.

Before `revise_memory_entry` or `retire_memory_entry`, read the current entry and pass its exact `citation`.
The citation's Memory revision is the concurrency check. After a conflict, refresh the entry and retry once only if
the user's requested change still applies. Current instructions and repository state outrank recalled history.

Preserve the host's permissions and exact user intent. A Skill never grants execution authority; never bypass a
missing approval channel or submit secrets. Distinguish empty, rejected, unavailable, and unknown outcomes.

Automatic hooks attempt bounded context and Source capture; neither substitutes for an explicit Memory save.
Report success only from the corresponding result. A timed-out write may have completed; inspect status before retrying.
