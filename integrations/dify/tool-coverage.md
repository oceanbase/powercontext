# Tool coverage

The provider declares exactly the following 20 tools. `generate_contract.py --check` checks declarations against the selected public OpenAPI closure; the integration manifest probe reads provider-listed declarations and their actual Python operation bindings. The SDK loading test checks that the resulting classes are loadable.

| Tool | Tool operation ID | Behavior |
| --- | --- | --- |
| `pc_search` | `search_memory` | Memory search; default and maximum 8 hits |
| `pc_memory_list` | `list_memory_entries` | Page through Atomic Memory, optionally including inactive states |
| `pc_memory_get` | `get_memory_entry` | Read an exact Atomic ArtifactRef |
| `pc_memory_state` | `get_atomic_memory_state` | Read the current Atomic reference, lifecycle and state version |
| `pc_remember` | `remember_memory` | Save explicitly chosen Atomic Memory; return `changed` and `records` |
| `pc_memory_revise` | `revise_memory_entry` | Replace exact current Atomic content using its content ETag |
| `pc_memory_retire` | `retire_memory_entry` | Set guarded, recoverable forgotten state for Atomic Memory |
| `pc_prepare_context` | `prepare_context` | Prepare bounded context |
| `pc_capture_source` | `capture_content_source` | Capture an explicit Source |
| `pc_handoff_activate` | `activate_handoff` | Activate from a boundary Source |
| `pc_handoff_prepare` | `prepare_handoff` | Generate an evidence-backed draft |
| `pc_handoff_finalize` | `finalize_handoff` | Finalize an inspected draft |
| `pc_handoff_commit` | `commit_handoff` | Persist a complete prepared Handoff |
| `pc_handoff_continue` | `continue_handoff` | Read prepared / exact / latest Handoff |
| `pc_experience_generate` | `generate_experience` | Generate an Experience candidate |
| `pc_experience_get` | `get_experience` | Read an exact Experience artifact |
| `pc_skill_generate` | `generate_skill` | Generate a managed Skill candidate |
| `pc_skill_get` | `get_skill` | Read an exact managed Skill artifact |
| `pc_review_list` | `list_artifact_candidates` | List candidates; explicit family experience / skill |
| `pc_review_get` | `get_artifact_candidate` | Read a candidate |

## Parameter and output differences

Memory tools retain their logical operation IDs. The adapter routes `pc_memory_list` to `list_atomic_memories`, Atomic reads to `get_artifact` or `get_artifact_revision`, revisions to `replace_artifact`, and forgetting to `change_atomic_memory_lifecycle`.

The administrator supplies Scope/binding, Server URL/token, context assembly and byte budget. The model supplies only operation-specific fields. Search limit is 8 rather than the HTTP default of 10 and maximum of 50. Memory kind is restricted to the six documented kinds; normalized Memory text is bounded by 8192 UTF-8 bytes. Search query keeps the HTTP character limit. Code indexing and tag filters are outside this provider surface.

Objects, arrays and nullable inputs use strings containing one JSON value. The model-facing parameter description retains the decoded schema through the default daemon's discovery path; the plugin decodes once and validates the public HTTP contract. Nullable strings need JSON quotes, explicit null is the text `null`, and optional unused parameters may be omitted. Unknown fields, malformed/non-finite JSON, empty reference objects and malformed references fail before the operation request. Generation permits a combined 1–32 Source/Artifact references; empty array defaults are encoded as `[]`. Review's explicit family uses the text `"experience"`/`"skill"`; an omitted or JSON-null filter is unfiltered. See [Workflow serialization examples](plugin/README.md).

`pc_memory_get` accepts one `artifact`, encoded as one complete JSON value. An Atomic `artifact` has `family=atomic-memory`, `artifact_id` and `revision`. A current read returns the complete `ArtifactRevision`, including `content.kind` and `content.text`, and exposes the actual Server content ETag as `etag`. An exact historical read has no current write ETag. Legacy Memory citations are rejected before any request is sent. `pc_memory_revise` requires the exact current `artifact`, that returned ETag as `if_match`, and complete `kind`/`text`. `pc_memory_retire` requires the exact current `artifact` and a nonnegative `state_version` from search, list or `pc_memory_state`; it sets `forgotten` and preserves history. Conflicts require rereading current state and checking that the requested change still applies.

`pc_memory_state(artifact_id)` returns `artifact`, `state`, `state_version` and nullable `merged_into_id`. States are `active`, `forgotten`, `merged` and `retired`. `pc_remember` returns `changed` and `records`; Atomic records carry their exact `artifact` and `state_version`.

Success preserves the entire public response, including pagination, citations, statuses, nullable nested objects and exact revision references. Each SDK invocation emits the same envelope as text, JSON and five corresponding named outputs. A sixth named output, `result`, exposes the successful response with traversable operation-specific properties for Workflow selection, including Atomic `content.kind`/`content.text` and `etag`, state `artifact`/`state_version`, candidate IDs and Handoff drafts; it is `{}` on error/unknown, while `data` retains any partial recovery receipt. Branch on `ok` and the operation's status before dereferencing nullable fields. Empty reads are success; uncertain writes are unknown and are not retried. Responses over the 4 MiB transport cap fail without truncation.

## Evidence

The recorded 2026-10-07 acceptance covers the 19-tool baseline against a real PowerContext HTTP/SQLite Server, with deterministic Handoff/Experience/Skill generators. It covers Source references, complete temporary and committed Handoff readback, candidate inspection, administrator approval outside the plugin, exact Experience/Skill readback, SDK concurrency across two Scopes, and official Dify casting and output-selector helpers. Its Memory checks use legacy citations. See [ACCEPTANCE.md](ACCEPTANCE.md) for the exact counts and package checksum; that record does not establish acceptance for the current 20-tool Atomic Memory surface.

Dify daemon dispatch, UI credential storage/restart, live generation, Workflow node object wiring, trusted write controls and clean package installation require the deployment acceptance recorded in [ACCEPTANCE.md](ACCEPTANCE.md).
