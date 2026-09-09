# Scope and memory

## Resolve and discover Scopes

`resolve_scope_binding` accepts `explicit_scope_id`, `binding_keys`, and `allow_default`. Binding keys must come from identities supplied by the host. Do not derive Scope IDs from repositories, branches, directories, or prompts. Continue only after resolution returns a Scope successfully.

When the user asks to find an existing project, use `list_scopes` and `get_scope` to read descriptors. `list_scopes` supports `query`, `query_field`, and relationship filters. Queries use literal substring matching, not regular expressions or wildcards. Keep the same filters and use the returned cursor when paging. Discovering a Scope does not authorize switching the current write target.

Use `create_scope` only when the user needs a separate boundary for work results. Supply `title`, `summary`, a stable `idempotency_key`, and any confirmed parent Scope or context references. `set_scope_binding` and `clear_scope_binding` change persistent bindings and require a requested binding change. Reading memory does not require changing a binding. Parent relationships, context references, and access permissions are separate concepts.

## Search and inventory

- `search_memory`: provide the resolved `scope_id` and a focused `query`. Usually use `mode: "auto"` and `limit: 8`. Use returned hits; a missing hit does not prove the information never existed. Supported modes are `auto`, `fts`, `vector`, and `hybrid`, subject to the current tool schema and deployment capabilities.
- `list_memory_entries`: use for explicit inventory or audit requests. Read active entries by default; set `include_inactive: true` only when the user asks for retired history.
- `get_memory_entry`: supply the complete `citation` to read an exact historical version. A citation contains `memory_ref`, `entry_id`, and `entry_version_id`; `memory_ref` contains `family`, `artifact_id`, and `revision`.
- `search_topic_memory`: search topic summaries in the current Scope and use returned Artifact references for detailed reads.
- `get_topic_memory`: pass the exact Artifact reference in `artifact`, not the Memory `citation` field.

Preserve an empty search result. If keywords need refinement, keep the same question and Scope. Do not automatically search other Scopes or enumerate all stored entries.

## Explicit memory maintenance

`remember_memory` requires `scope_id`, `kind`, and `text`, with an optional `reason`. Express the requested decision, constraint, current state, or next step so it makes sense independently. Normalized entry text must not exceed 8192 UTF-8 bytes. Do not copy an entire conversation into memory.

Confirm a save only after reading the write response and preserving its reference. Do not substitute `capture_content_source` to obtain a successful result. Supply `expected_revision` only when an exact version is available and a concurrency check is needed; do not guess the current Revision.

To correct or retire an entry:

1. Locate it through search or a reference supplied by the user, then read it with `get_memory_entry`.
2. For `revise_memory_entry`, pass `scope_id`, the original `citation`, and the new `kind` and `text`. For `retire_memory_entry`, pass `scope_id` and the original `citation`. Add `reason` as needed.
3. On a version conflict, find the current entry and read its new reference. Reading the old citation again still returns the old version and cannot resolve the conflict.
4. Retry once with the new reference only if the user's original intent still applies. Otherwise, explain the conflict. Do not add a duplicate entry to bypass concurrency checks.

See [examples.json](examples.json) for request examples. Its Scope values are test placeholders; use server-returned values for actual operations.
