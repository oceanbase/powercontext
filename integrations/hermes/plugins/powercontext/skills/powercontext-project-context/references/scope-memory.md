# Scope and Memory

## Memory

- Use `powercontext_search_memory` for an explicit search or when relevant history is missing from current context.
- Use powercontext_remember only when the user explicitly asks for durable
  memory.
- Search and list return Atomic records. Use the actual `artifact` and `state_version` in a `reference` object for
  reads and forgetting. Revisions take that object in `citation` and use the actual content ETag.
- Genuine legacy MemoryCitation objects support exact historical reads only; never fabricate one from an Atomic ref.
- Use powercontext_revise_memory_entry for a correction and
  powercontext_retire_memory for reversible forgetting when the user requests removal from active use.
- Treat forgotten, merged, and retired records as historical data. Collection change history is unsupported.
- After a conflict, inspect the current record before retrying; never silently refresh a captured write version.

Automatic hooks attempt bounded context and Source capture; neither substitutes for an explicit Memory save.
