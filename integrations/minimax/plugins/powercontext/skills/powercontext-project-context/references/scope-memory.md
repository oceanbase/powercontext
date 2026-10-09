# Scope and memory

## Resolve and discover Scopes

`resolve_scope_binding` accepts `explicit_scope_id`, `binding_keys`, and `allow_default`. Binding keys must come from identities supplied by the host. Do not derive Scope IDs from repositories, branches, directories, or prompts. Continue only after resolution returns a Scope successfully.

When the user asks to find an existing project, use `list_scopes` and `get_scope` to read descriptors. `list_scopes` supports `query`, `query_field`, and relationship filters. Queries use literal substring matching, not regular expressions or wildcards. Keep the same filters and use the returned cursor when paging. Discovering a Scope does not authorize switching the current write target.

Use `create_scope` only when the user needs a separate boundary for work results. Supply `title`, `summary`, a stable `idempotency_key`, and any confirmed parent Scope or context references. `set_scope_binding` and `clear_scope_binding` change persistent bindings and require a requested binding change. Reading memory does not require changing a binding. Parent relationships, context references, and access permissions are separate concepts.

## Search and inventory

- Use `search_memory` with a focused query, `mode: "auto"`, and at most eight results.
  Current hits contain `memory.artifact`, text, state, and `state_version`; they do not contain legacy entry citations.
- Use `list_atomic_memories` for requested inventories, explicit state filters, and `next_cursor` pagination.
  Default to active memories. Include forgotten, merged, or retired memories only for an explicit audit.
- Use `get_artifact_revision` with the exact `atomic-memory` ArtifactRef to inspect immutable content and lineage.
  Use `get_artifact` for current content and `get_atomic_memory_state` for current lifecycle state.
- `get_memory_entry` reads retained legacy history using a complete old citation, or resolves a migrated logical target.
  Never manufacture a legacy citation from a new ArtifactRef.

Use `search_topic_memory` and `get_topic_memory` for Topic Memory. Preserve empty search results; an empty result
does not authorize a Scope change or full inventory. Check the actual tool catalog before calling any operation.

## Explicit memory maintenance

Call `remember_memory` only for an explicit durable save. Keep each memory self-contained and at most 8192 UTF-8 bytes.
Do not store secrets or whole transcripts. Confirm a write only from its successful response.

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

See [examples.json](examples.json) for request examples. Replace placeholder Scope values with server-returned IDs.
