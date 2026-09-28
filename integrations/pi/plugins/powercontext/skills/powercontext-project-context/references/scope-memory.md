# Scope and Memory

## Read context

- Use `pc_search` with a focused query, `mode: "auto"`, and no more than eight results.
- Use `pc_memory_list` for an explicitly requested inventory of active entries in the current Scope.
- Use `pc_memory_get` only with an exact citation returned by search or list.
- Use `pc_prepare_context` when one bounded, query-specific context value is more useful than raw search hits.

## Write only on request

- Call `pc_remember` only when the user explicitly asks to persist a concise decision, constraint, current state,
  task outcome, next step, or agent note.
- Read the current entry and use its exact citation before `pc_memory_revise` or `pc_memory_retire`.
- Never submit secrets or credentials.
- Pi asks for confirmation before an explicit durable mutation and refuses it without an interactive UI.

Automatic hooks attempt bounded context and Source capture; neither substitutes for an explicit Memory save.
