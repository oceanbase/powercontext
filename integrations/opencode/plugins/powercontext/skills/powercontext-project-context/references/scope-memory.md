# Scope and Memory

## Read context

- Use `pc_search` with a focused query, `mode: "auto"`, and no more than eight results.
- Use `pc_memory_list` for an explicitly requested inventory of active entries in the current Scope.
- Use `pc_memory_get` with the exact Atomic Memory artifact returned by search or list.
- Follow `next_cursor` for later inventory pages; a single page is not the whole inventory.
- Use `pc_prepare_context` when one bounded value is more useful than raw search hits.

## Write only on request

- Call `pc_remember` only when the user explicitly asks to persist a concise decision, constraint, current state,
  task outcome, next step, or agent note.
- For `pc_memory_revise`, read the current Atomic Memory and copy its exact artifact and real content ETag from `pc_memory_get` into `if_match`.
- For `pc_memory_retire`, use its exact current artifact and `state_version` from search, list or `pc_memory_state`. This sets recoverable `forgotten` state and preserves history.
- An exact read of an older revision does not supply a current write ETag.
- Never submit secrets or credentials.
- OpenCode asks for confirmation before a named PowerContext mutation.

Automatic hooks attempt bounded context and Source capture; neither substitutes for an explicit Memory save.
