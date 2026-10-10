# Scope and Memory

## Resolve scope

Before the first memory tool call, run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_scope.py" --cwd "$PWD"
```

Reuse that exact `scope_id` for the task.

The Server resolves an explicit Scope first, then durable session and workspace
bindings, and finally its default Scope. When the user explicitly asks to bind
the current checkout to a known Scope, run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/workspace_scope.py" \
  --cwd "$PWD" --bind-scope "SCOPE_ID"
```

Then run the normal resolver command again and verify the same Scope. The
binding is stored by PowerContext; the plugin never derives a Scope ID from a
Git remote or directory. Never infer a Scope when multiple candidates remain
consequential.

Before a durable one-turn Handoff or a `latest` Continue, resolve the intended
Scope explicitly. If the current binding is not the intended boundary, ask the
user or host for the exact Scope ID, bind it, and verify the resolver result
before any Handoff write. Never infer a Scope from a report view.

## Read

- Use `search_memory` with a focused query, `mode: "auto"`, and at most eight results.
  Current hits contain `memory.artifact`, text, state, and `state_version`; they do not contain legacy entry citations.
- Use `list_atomic_memories` for requested inventories, explicit state filters, and `next_cursor` pagination.
  Default to active memories. Include forgotten, merged, or retired memories only for an explicit audit.
- Use `get_artifact_revision` with the exact `atomic-memory` ArtifactRef to inspect immutable content and lineage.
  Use `get_artifact` for current content and `get_atomic_memory_state` for current lifecycle state.
- `get_memory_entry` only adapts a legacy `target` with `type: "memory_entry"`, `family: "memory"`,
  the collection's `artifact_id`, and `entry_id` to the migrated Atomic's current content.
  Legacy citations and collection history are unsupported. Use `get_artifact_revision` with an Atomic ArtifactRef for exact history.

## Write only on request

Call `remember_memory` only when the user explicitly asks to persist context.
Store concise, self-contained entries such as a decision, constraint,
current-state, task-outcome, or next-step. Never store secrets or credentials,
and never claim success until the tool returns successfully.

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
