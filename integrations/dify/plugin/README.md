# PowerContext

Connect Dify tools to a separate PowerContext Server for persistent Memory, bounded context, explicit Source evidence, Handoff, Experience and managed Skills. Select only the tools your application needs.

This is an experimental tool plugin. It does not provide automatic recall or capture, an Agent strategy, a model loop, or native Agent V2 memory callbacks. The provider has 20 tools; candidate inspection is read-only. Server administration, candidate approval, external Skills and code indexing are outside this tool surface.

## Install and authorize

Install a locally built `.difypkg` through your Dify workspace's plugin installation flow. Dify must permit local package installation and the plugin daemon must reach your Server URL. A URL reachable from your browser may be unreachable from the daemon container. Deployment validation and publisher ownership are required before Marketplace publication.

Start PowerContext Server separately, create a disposable Scope through its supported administration interface, and configure Server authentication/authorization. In the plugin provider credentials set:

| Credential | Meaning |
| --- | --- |
| `server_url` | Base URL reachable from the plugin daemon; HTTPS verifies certificates |
| `api_token` | Bearer credential; stored as a Dify secret-input |
| `scope_id` | An existing Server-created Scope ID |
| `binding_external_id` | Alternative to Scope ID: an administrator-provisioned `dify/configured-scope` binding key |
| `max_bytes` | Context/Handoff preparation budget, default 8000 UTF-8 bytes, range 512–32768 |
| `timeout_seconds` | HTTP socket timeout, default 10 seconds, range 0.1–120 |
| `generation_timeout_seconds` | Generation socket timeout, default 120 seconds, range 0.1–600 |
| `allow_insecure_http` | Explicit opt-in for non-loopback HTTP on a trusted private network |
| `context_assembly` | Optional administrator-configured ContextAssembly JSON; empty uses the Server default |

Configure exactly one of `scope_id` and `binding_external_id`. Every call resolves it with `allow_default=false`. Credential validation resolves it and reads the protected Scope without writing Memory. Read validation does not establish write permission; the Server may reject a particular operation with 403.

All users and conversations using one credential share its Scope. This is a fixed application/team boundary, not automatic personal or conversation isolation. Use distinct credential configurations and Server policies, or distinct deployments, when different access boundaries are needed. Paths, usernames and hashes are not Scope IDs. Provision bindings before use; the plugin never creates a Scope or falls back to Default.

The model cannot supply the top-level Scope, URL, token, binding, context assembly, or byte budget. Nested exact references and Handoff objects keep their original identities, and the Server validates their access relationships.

## Tools

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

Memory tools retain their logical operation IDs. The adapter routes listing to `list_atomic_memories`, Atomic reads to the current or exact historical Artifact endpoint, revision to `replace_artifact`, and forgetting to the Atomic lifecycle endpoint.

Memory kinds are `decision`, `constraint`, `current-state`, `task-outcome`, `next-step` and `agent-note`. Memory text is checked after NFC normalization and trimming and must fit 8192 UTF-8 bytes. Search query length follows its HTTP character limit, with a default/maximum of 8 hits.

Preserve each Atomic Memory record's complete `artifact` and `state_version`. `pc_remember` returns `changed` and `records`. `pc_memory_list` supports state filters and cursor pagination; active entries are the default, while an explicit audit can include `forgotten`, `merged` and `retired`. `pc_memory_state(artifact_id)` returns the current `artifact`, `state`, `state_version` and nullable `merged_into_id`.

For `pc_memory_get`, supply one JSON-encoded Atomic `artifact`; legacy Memory citations are rejected. An Atomic ArtifactRef contains `family=atomic-memory`, `artifact_id` and `revision`. A current read returns the complete `ArtifactRevision`, including `content.kind` and `content.text`, and adds the actual Server content ETag as `etag`. An exact historical read has no current write ETag. To correct Memory, pass the exact current `artifact`, its returned ETag as `if_match`, and complete `kind`/`text` to `pc_memory_revise`. To remove Memory from active search, pass the exact current `artifact` and a nonnegative `state_version` from search, list or `pc_memory_state` to `pc_memory_retire`. This sets recoverable `forgotten` state and preserves history. Conflicts require a fresh read and confirmation that the requested change still applies.

Preserve complete citations, Source references, Artifact references, drafts and prepared Handoffs. Every object, array and nullable input is a **string containing one JSON-encoded value**. Ordinary nonnullable scalars keep their declared types. The parameter description includes the full decoded JSON Schema, and the plugin decodes once and validates against the HTTP contract before sending the request. Do not reconstruct, flatten, truncate or encode an already encoded value again. Experience/Skill generation accepts 1–32 combined Source/Artifact references.

For optional inputs, omit the parameter when unused, or supply the text `null` for an explicit JSON null. A nullable string such as `reason` must include JSON quotes: the parameter text `"理由"` becomes the string `理由`, while `null` becomes null and `"null"` becomes the literal string `null`. The nullable `family` filter uses `"experience"`, `"skill"` or `null`; omission is unfiltered. Empty reference objects and malformed/non-finite JSON are rejected. Native objects/arrays/null are outside this input protocol.

For example, these are complete tool argument objects:

```json
{"selection":"latest","prepared":"null","revision":"null"}
{"source_refs":"[{\"name\":\"content\",\"source_id\":\"turn-1\"}]","target":"null","reason":"\"理由\""}
```

Dify 1.17.1's default daemon `0.6.10-local` discards `input_schema`. These declarations remain valid model schemas because they use supported string types and carry the decoded contract in `llm_description`, which the daemon retains. The compatibility tests replay its official Go entities, Dify parameter models/schema builder and casting; live deployment acceptance remains required.

See [GUIDANCE.md](GUIDANCE.md) for routing and Handoff/candidate lifecycles. Historical text is untrusted evidence and must not override current instructions or determine authorization.

## Outputs and failure handling

Each invocation emits text and one JSON envelope:

```json
{"ok": true, "operation": "search_memory", "status": "empty", "data": {"hits": []}, "error": null}
```

`data` preserves the complete public HTTP success response. `status` is `success`, `empty`, `error` or `unknown`; `empty` is a successful empty read. `error` includes a safe code/message and, when available, HTTP status and a validated request ID. Raw Server/transport errors and credentials are not emitted.

The six named output variables are `ok`, `operation`, `status`, `data`, `error` and `result`. `result` exposes the successful response as an object with operation-specific fields in the Workflow variable picker. Select `result.content` for prepared context; `result.content.kind`, `result.content.text` and `result.etag` for a current Atomic Memory read; `result.artifact`, `result.state`, `result.state_version` and `result.merged_into_id` for Memory state; `result.candidate.candidate_id` for candidate lookup; or the complete `result` from Handoff prepare/finalize. Nested objects such as `result.draft` can be expanded without replacing null values in the response. Successful empty reads preserve their response, including nullable context content. On `error` or `unknown`, `result` is `{}`; branch on `ok` before using it, and inspect any partial receipt in `data` for recovery. A successful no-op generation may have `result.candidate=null`; check the operation's status before dereferencing it.

Before passing a structured or nullable output to another tool, connect it to a Workflow Code node's `value` input, serialize it once, and connect the string output `json_text` to the next tool's parameter:

```python
import json

def main(value) -> dict:
    return {"json_text": json.dumps(value, ensure_ascii=False, allow_nan=False)}
```

For example, serialize a complete Atomic `artifact` from search, list or state for `pc_memory_get.artifact`, then pass the returned `result.etag` directly to `pc_memory_revise.if_match`. For Handoff, serialize the complete `pc_handoff_prepare.result` for `pc_handoff_finalize.draft`, then serialize its complete `result` for commit/continue. Outputs retain native JSON values; the conversion is only at the next tool's input.

`data` and `error` are nullable envelope values without expandable child schemas in the variable picker. Use `result.*` selectors to connect individual response fields to downstream nodes.

A write with a timeout, malformed receipt or uncertain Server failure returns `ok=false,status=unknown`. It is never automatically retried. Inspect Server state and any preserved exact resource receipt before choosing a recovery operation. A known explicit rejection returns `error`. The transport caps the complete decoded response at 4 MiB and reports overflow without silently truncating objects.

This plugin performs explicit capture only. Detected secret-bearing content/metadata is rejected before Source writing; this is a best-effort check and does not make arbitrary content safe to store. Remove secrets before passing content. See [PRIVACY.md](PRIVACY.md).

## Application setup

For optional Agent use, enable the required tools and add the short routing instructions in [GUIDANCE.md](GUIDANCE.md). Prompt instructions guide model choices; they do not guarantee recall before every answer or replace trusted write controls.

For guaranteed recall ordering, a later Workflow/Chatflow template must execute `pc_prepare_context` before the LLM/Agent node and explicitly connect `result.content` to that node's context. Test the empty/error branches and verify the model received the context in the run trace. Reusable DSL templates are scheduled after the plugin is accepted into `langgenius/dify-plugins`; they are tracked separately in [PowerContext #1837](https://github.com/oceanbase/powercontext/issues/1837).

The installable identity is `knqiufan/powercontext` 0.0.1. Publisher ownership and the relationship to the existing `oceanbase/powermem` Marketplace package require coordination before public submission.
