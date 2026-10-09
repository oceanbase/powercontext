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

- Use `search_memory` with a focused query, `mode: "auto"`, and at most eight results.
  Current hits contain `memory.artifact`, text, state, and `state_version`; they do not contain legacy entry citations.
- Use `list_atomic_memories` for requested inventories, explicit state filters, and `next_cursor` pagination.
  Default to active memories. Include forgotten, merged, or retired memories only for an explicit audit.
- Use `get_artifact_revision` with the exact `atomic-memory` ArtifactRef to inspect immutable content and lineage.
  Use `get_artifact` for current content and `get_atomic_memory_state` for current lifecycle state.
- `get_memory_entry` reads retained legacy history using a complete old citation, or resolves a migrated logical target.
  Never manufacture a legacy citation from a new ArtifactRef.

## Write Memory Only On Request

Call `remember_memory` only when the user explicitly asks to persist reusable
project context.

Store concise, self-contained entries such as a decision, constraint,
current-state, task-outcome, or next-step. Never store secrets, credentials,
private tokens, or transient logs.

`remember_memory` returns `records` with independent ArtifactRefs. Omit `expected_revision` or pass null;
legacy collection revision preconditions are unsupported.

For a requested correction, call `get_artifact`, inspect its `artifact`, and pass its exact `etag` as
`replace_artifact`'s `If-Match`. These MCP tools return `{artifact, etag, status_code}`; a conditional 304 has
`artifact: null`. Historical `get_artifact_revision` reads return plain Artifact JSON without a current-head ETag.
For Atomic content, write `schema`, `kind`, and `text`; `creation` is system-owned merge metadata and must be omitted.
Do not replace a stale precondition silently or create a duplicate to bypass it. After a conflict, reread and proceed
only if the requested correction still applies.

For a requested removal from normal search, read `get_atomic_memory_state` and call `change_atomic_memory_lifecycle`
with the exact ArtifactRef and state_version. This forgets the memory and preserves recoverable history.
Use restoration previews/restorations for an explicitly requested recovery; a merged memory can affect its whole merge
chain. Legacy `revise_memory_entry` and `retire_memory_entry` are not current MCP operations.


Automatic hooks attempt bounded context and Source capture; neither substitutes for an explicit Memory save.
