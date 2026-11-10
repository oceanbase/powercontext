<!-- POWERCONTEXT-GUIDANCE:START -->
# Scope and Memory

## Resolve Scope

Follow the [host binding](host.md). Prefer the exact current-request Scope or the host's implicitly bound tools. If a tool schema omits Scope, let its native adapter supply it; do not bypass that binding with a Server-default resolver. For filesystem bindings, locate this installed Skill's plugin root, follow the documented resolver with the actual workspace/session identity, and reuse its successful result. A missing resolver or conflicting identity is a failure, not permission to guess a Scope or change a binding. A workspace change does not replace a current Session override.

Use the available Server resolver only when the host binding permits it. Creating, binding or switching a Scope requires the user's corresponding intent; never derive an ID from a repository, branch, directory or prompt. Before durable Handoff or latest continuation, verify that the selected Scope is the intended result boundary.

## Read Memory

- Use `search_memory` with a focused query, `mode: "auto"`, and at most eight results. Current hits contain `memory.artifact`, text, state, and `state_version`; they do not contain legacy entry citations.
- Use `list_atomic_memories` for requested inventories, explicit state filters, and `next_cursor` pagination. Default to active memories. Include forgotten, merged, or retired memories only for an explicit audit.
- Use `get_artifact_revision` with the exact `atomic-memory` ArtifactRef to inspect immutable content and lineage. Use `get_artifact` for current content and `get_atomic_memory_state` for current lifecycle state.

## Save or change Memory on request

Call `remember_memory` only when the user explicitly asks to persist reusable project context.

Choose a `kind` that preserves the user's meaning. The schema accepts an application-defined string; recommended values are `fact`, `preference`, `decision`, `constraint`, and `working_note`. Use `preference` for an explicit preference such as "I prefer uv for Python", and `constraint` for a requirement or restriction. Preserve a kind explicitly supplied by the user. Store concise, self-contained `text`. Never store secrets, credentials, private tokens, or transient logs.

For `remember I prefer uv for Python`, use `kind: "preference"` and `text: "The user prefers uv for Python."` with the exact resolved Scope or the host's implicitly bound tool.

`remember_memory` returns `records` with independent ArtifactRefs. Omit `expected_revision` or pass null; legacy collection revision preconditions are unsupported.

To retry a save whose response was lost, resend the same content with the same `idempotency_key`; the original records are returned and no duplicate is created. Generate a fresh unguessable key, such as a UUID, for each new save, never derive it from the text, and reuse it only for that save's retries. `idempotency_conflict` means the key already names different content: use a new key.

For a requested correction, call `get_artifact`, inspect its `artifact`, and pass its exact `etag` as `replace_artifact`'s `If-Match`. These MCP tools return `{artifact, etag, status_code}`; a conditional 304 has `artifact: null`. Historical `get_artifact_revision` reads return plain Artifact JSON without a current-head ETag. For Atomic content, write `schema`, `kind`, and `text`; `creation` is system-owned merge metadata and must be omitted. Do not replace a stale precondition silently or create a duplicate to bypass it. After a conflict, reread and proceed only if the requested correction still applies.

For a requested removal from normal search, read `get_atomic_memory_state` and call `change_atomic_memory_lifecycle` with the exact ArtifactRef and state_version. This forgets the memory and preserves recoverable history. Use restoration previews/restorations for an explicitly requested recovery; a merged memory can affect its whole merge chain. Legacy `revise_memory_entry` and `retire_memory_entry` are not current MCP operations. Automatic hooks attempt bounded context and Source capture; neither substitutes for an explicit Memory save.
<!-- POWERCONTEXT-GUIDANCE:END -->
