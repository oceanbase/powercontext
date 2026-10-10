# Tool coverage

The provider declares exactly the following 19 tools. `generate_contract.py --check` checks declarations against the selected public OpenAPI closure; the integration manifest probe reads provider-listed declarations and their actual Python operation bindings. The SDK loading test checks that the resulting classes are loadable.

| Tool | HTTP operationId | Behavior |
| --- | --- | --- |
| `pc_search` | `search_memory` | Memory search; default and maximum 8 hits |
| `pc_memory_list` | `list_memory_entries` | List Memory entries, optionally including inactive entries |
| `pc_memory_get` | `get_memory_entry` | Read an exact Memory citation |
| `pc_remember` | `remember_memory` | Save an explicitly chosen Memory entry |
| `pc_memory_revise` | `revise_memory_entry` | Revise an exact citation |
| `pc_memory_retire` | `retire_memory_entry` | Retire an exact citation |
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
| `pc_review_list` | `list_candidates` | List candidates; explicit family experience / skill |
| `pc_review_get` | `get_candidate` | Read a candidate |

## Parameter and output differences

The administrator supplies Scope/binding, Server URL/token, context assembly and byte budget. The model supplies only operation-specific fields. Search limit is 8 rather than the HTTP default of 10 and maximum of 50. Memory kind is restricted to the six documented kinds; normalized Memory text is bounded by 8192 UTF-8 bytes. Search query keeps the HTTP character limit. Code indexing and tag filters are outside this provider surface.

Objects, arrays and nullable inputs use strings containing one JSON value. The model-facing parameter description retains the decoded schema through the default daemon's discovery path; the plugin decodes once and validates the public HTTP contract. Nullable strings need JSON quotes, explicit null is the text `null`, and optional unused parameters may be omitted. Unknown fields, malformed/non-finite JSON, empty reference objects and malformed references fail before the operation request. Generation permits a combined 1–32 Source/Artifact references; empty array defaults are encoded as `[]`. Review's explicit family uses the text `"experience"`/`"skill"`; an omitted or JSON-null filter is unfiltered. See [Workflow serialization examples](plugin/README.md).

Success preserves the entire public response, including pagination, citations, statuses, nullable nested objects and exact revision references. Each SDK invocation emits the same envelope as text, JSON and five corresponding named outputs. A sixth named output, `result`, exposes the successful response with traversable operation-specific properties for Workflow selection, including candidate IDs and Handoff drafts; it is `{}` on error/unknown, while `data` retains any partial recovery receipt. Branch on `ok` and the operation's status before dereferencing nullable fields. Empty reads are success; uncertain writes are unknown and are not retried. Responses over the 4 MiB transport cap fail without truncation.

## Evidence

The HTTP acceptance scenario calls all 19 registered SDK entries against a real PowerContext HTTP/SQLite Server, with deterministic Handoff/Experience/Skill generators. It validates exact Memory read/revise/retire, Source references, complete temporary and committed Handoff readback, candidate inspection, administrator approval outside the plugin, and exact Experience/Skill readback. A separate scenario overlaps SDK greenlets with two Scope configurations. A third applies Dify 1.17.1's actual casting before SDK/HTTP invocation and verifies nullable Handoff/generation inputs and candidate readback. Official frontend helpers resolve 15 context, citation, Handoff and candidate selectors from the generated metadata.

This establishes SDK and HTTP behavior. Dify daemon dispatch, UI credential storage/restart, live generation, Workflow node object wiring, trusted write controls and clean package installation require the deployment acceptance recorded in [ACCEPTANCE.md](ACCEPTANCE.md).
