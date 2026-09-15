# Scope and Memory

## Memory

- Use `powercontext_search_memory` for an explicit search or when relevant history is missing from current context.
- Use powercontext_remember only when the user explicitly asks for durable
  memory.
- Use the exact citation returned by search or list for reads, revisions, and
  retirement.
- Use powercontext_revise_memory_entry for a correction and
  powercontext_retire_memory when an entry is no longer valid.
- Treat inactive entries and change history as audit data.

Automatic hooks attempt bounded context and Source capture; neither substitutes for an explicit Memory save.
