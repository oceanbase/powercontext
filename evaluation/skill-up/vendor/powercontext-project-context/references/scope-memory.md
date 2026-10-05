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

- Use `search_memory` with a focused query, `mode: "auto"`, and no more than
  eight results.
- Use `list_memory_entries` for an explicitly requested inventory of active entries in the current scope.
- Set `include_inactive` to `true` only when the user explicitly asks to audit
  retired entries or the complete current Memory snapshot.
- Use `get_memory_entry` with the exact returned `citation` when full immutable
  entry details are needed.

## Write only on request

Call `remember_memory` only when the user explicitly asks to persist context.
Store concise, self-contained entries such as a decision, constraint,
current-state, task-outcome, or next-step. Never store secrets or credentials,
and never claim success until the tool returns successfully.

Before `revise_memory_entry` or `retire_memory_entry`, read the current entry.
Pass its exact `citation`; the citation's Memory revision is the concurrency
check. After a conflict, refresh the head and retry once only if the user's
requested change still applies.

Automatic hooks attempt bounded context and Source capture; neither substitutes for an explicit Memory save.
