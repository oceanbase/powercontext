---
title: Use Atomic Memory
description: Create, search, revise and restore independent memories, and adapt legacy Memory API clients.
---

Atomic Memory stores each fact, preference or decision as an independent `atomic-memory` Artifact. Content revisions
are immutable; the current lifecycle is stored separately. Explicit creation and replacement need no generation model.
With Embedding configured, publication also prepares the current search vector. Existing collection databases require
[offline migration](../operate/atomic-memory-migration.md) first.

## Create, read and replace

In these HTTP examples, `S` is an existing Scope ID and `M` is the Artifact ID returned by creation. Send
`Content-Type: application/json` and the deployment's Bearer token when authentication is enabled.

Submit to `POST /v1/scopes/S/artifacts`:

```json
{
  "family": "atomic-memory",
  "content": {
    "schema": "powercontext.atomic-memory.v1",
    "kind": "decision",
    "text": "Keep the public API asynchronous."
  }
}
```

The server assigns the identity, creates revision 1 and returns the content `ETag`. `kind` is an application-defined
name of at most 128 characters. New writes normalize `text` to Unicode NFC and trim surrounding whitespace; the
result must be nonempty and fit within 8192 UTF-8 bytes. Existing content and historical revisions remain unchanged
when read. Restoring a revision preserves its original text without applying the normalization rules for new writes.
Ordinary writes do not accept merge metadata named `creation`.

`GET /v1/scopes/S/artifacts/atomic-memory/M` reads the current content and `ETag`.
`GET /v1/scopes/S/artifacts/atomic-memory/M/revisions/1` always reads that exact historical revision.
Use `/v1/scopes/S/artifacts/atomic-memory/M/revisions` to list content history.

Submit complete content to `PUT /v1/scopes/S/artifacts/atomic-memory/M`, sending the content `ETag` just read in `If-Match`:

```json
{
  "content": {
    "kind": "decision",
    "text": "Keep the public API asynchronous and provide a separate internal synchronous adapter."
  }
}
```

Success creates the next content revision. Missing `If-Match` returns `428 precondition_required`; a stale value
returns `412 revision_conflict`. Read and reconcile the content before submitting again.

## Current state and forgetting

`GET /v1/scopes/S/artifacts/atomic-memory/M/state` returns the current exact `artifact` reference, `state`,
`state_version` and `merged_into_id`. Its ETag includes both content revision and state version and supports
`If-None-Match` with `304`. Content replacement still uses the ETag from the content read.

| State | Meaning |
| --- | --- |
| `active` | Included in ordinary search and context recall |
| `forgotten` | Content and history remain available; it can be restored |
| `merged` | Frozen as a merge input; `merged_into_id` identifies the result |
| `retired` | No longer available for current use; editing and restoration are rejected. Create a new Artifact to adopt its content again |

Forget through `POST /v1/atomic-memory/lifecycle`, copying the exact reference and `state_version` from the state read:

```json
{
  "scope_id": "S",
  "target": {
    "artifact": {"family": "atomic-memory", "artifact_id": "M", "revision": 2},
    "state_version": 0
  },
  "state": "forgotten"
}
```

Replace the example revision and state_version with the values actually read. Forgetting does not advance the content
revision. Ordinary Replace can edit active or forgotten content and preserves its state. Merged and retired content
cannot be edited directly. The lifecycle endpoint currently accepts only `forgotten`.

## Search and administrative listing

Submit to `POST /v1/atomic-memory/search`:

```json
{"scope_id": "S", "query": "public API", "mode": "text", "limit": 10}
```

`mode` accepts `text`, `vector` or `hybrid` and defaults to `text`. Vector and hybrid modes require an available,
matching Embedding profile. Search returns active memories only. Each `hits[].memory` contains the content, exact
Artifact reference and state version; the hit also has `score` and `matched_by`. Preserve `memory.artifact` for citations.
Ordinary vector search applies eligibility first, computes exact L2, then ranks and limits results. This path does not
use ANN and does not establish production backend performance or acceptance.

For complete Artifact results and advanced retrieval controls, use
`POST /v1/scopes/S/artifacts/atomic-memory/search`. This unified route requires `scope.read`, defaults to text,
and accepts `filters`, admission, RRF rank constant/weights, `min_score`, and optional score metadata.
Its default limit is 10 and maximum is 100. It returns `results` with immutable `content` and `lineage`, rather than
the dedicated search's current-state `hits[].memory`. Retrieval scores are normalized to `[0, 1]`; channel metadata
contains actual BM25/MATCH relevance or L2 distance. Configured reranking stays active; no request `rerank` option is
exposed. See [Search Artifacts](search-artifacts.md) and [Fusion algorithms and parameters](search-fusion.md).
Callers with an Artifact-only read grant can continue to use the dedicated Atomic search route.

Submit to `POST /v1/atomic-memory/list`:

```json
{
  "scope_id": "S",
  "states": ["active", "forgotten", "merged", "retired"],
  "kind": "decision",
  "limit": 50
}
```

Listing accepts no semantic query. Omitting `states` selects active only. `items` contain current content and state.
When `next_cursor` is present, continue with that cursor and the same Scope, principal and filters. Search and list
both accept `kind`, `tags` and `tag_match: "all" | "any"`, with limits of 1–100. Use the
[Artifact tag API](manage-artifact-tags.md) with `{type: "artifact", family: "atomic-memory", artifact_id: "M"}`
for new targets.

## Merge and restore

`POST /v1/atomic-memory/merges` requires at least two exact active input references and state versions, plus result content:

```json
{
  "scope_id": "S",
  "inputs": [
    {"artifact": {"family": "atomic-memory", "artifact_id": "A", "revision": 1}, "state_version": 0},
    {"artifact": {"family": "atomic-memory", "artifact_id": "B", "revision": 3}, "state_version": 2}
  ],
  "content": {"kind": "decision", "text": "The public API is asynchronous; internal adapters may be synchronous."}
}
```

The server creates result C and marks A and B merged. Supply additional evidence through `source_refs` and
`artifact_refs`; the inputs themselves become exact Artifact evidence. Merge and restore authorize every actual
target. The current principal must own each write target.

Restore directly through `POST /v1/atomic-memory/restorations`:

```json
{"scope_id": "S", "target": {"artifact_id": "B"}}
```

Restoring forgotten content makes it active. Restoring an already active memory without a revision returns unchanged.
Restoring a merged input undoes the later merges that froze it. For A+B→C followed by C+D→E, restoring B makes
A, B and D active and retires C and E. Ordinary downstream Artifacts and Source cursors are not rolled back.
Supplying `target.revision` saves that historical content as a new revision of the target.

Inspect the impact first through `POST /v1/atomic-memory/restoration-previews`:

```json
{"scope_id": "S", "operation": "restore", "target": {"artifact_id": "B", "revision": 3}}
```

The response contains `preview_token`, `expires_at`, the current chain `endpoint`, proposed `restore` items, exact
`retire` references and `undo_merge_results`. A preview holds no locks and creates no pending approval record.
After inspecting it, send the original token to restorations with the same principal, Scope, operation and target:

```json
{
  "scope_id": "S",
  "operation": "restore",
  "target": {"artifact_id": "B", "revision": 3},
  "preview_token": "copy the original preview_token here"
}
```

Success returns `changed`, exact resulting `restored` references, `retired` and `undo_merge_results`.
To undo the merge that created C, use `operation: "undo_merge"` and `target: {"artifact_id": "C"}`.
That operation cannot also select a content revision.

| Error | HTTP status | Action |
| --- | --- | --- |
| `invalid_preview` | 422 | Check the token, principal, Scope, operation, target and shared signing configuration |
| `preview_expired` | 409 | Preview again |
| `preview_stale` | 409 | Read the impact and preview again |
| `invalid_memory_state` | 409 | Do not edit merged content directly or restore retired content |
| `atomic_memory_changed` | 409 | Read the changed content/state before deciding to retry |
| `invalid_memory_relation` | 409 | Investigate inconsistent stored relationships |

Restore and merge have no `idempotency_key` or durable receipt for replaying an earlier response. A direct restoration
interprets the relationships current on each invocation; repeating it after another merge can undo that newer merge.
Replaying a successful request with a token can return `preview_stale`. If the connection fails during commit, inspect
current states before deciding to preview again. The Python SDK does not blindly retry writes with an unknown outcome.
See [configuration](../operate/configuration.md#atomic-memory) for signing keys, TTL and direct restoration retries.

## Legacy Memory API compatibility

Retaining a route does not retain its old response model. New memories and revisions produce real `atomic-memory`
ArtifactRefs only, with no new collection revision, entry_version_id or MemoryCitation.

| Legacy call | Behavior and response after upgrade |
| --- | --- |
| `entries/get` with an old `citation` | Returns the old `MemoryEntry`, validating membership and the exact entry version in that collection revision |
| `entries/get` with an old `target` | Returns a current `AtomicMemoryRecord`; see the two read modes below |
| Exact revision read of an old collection Artifact | Retains the old body, manifest and changes; the frozen head does not describe current memories after upgrade |
| `search` | Keeps request modes `auto/fts/vector/hybrid` and tag_filter; returns `mode` and `hits`, whose `memory` is an AtomicMemoryRecord |
| `entries/list` | Returns `entries: AtomicMemoryRecord[]` and `next_cursor`; `include_inactive=true` includes all four states |
| `remember` with omitted or null `expected_revision` | Creates independent memories; returns `changed` and `records: AtomicMemoryRecord[]` |
| `flush` | Runs Atomic Source processing, retaining status/cursors/counts; `memory` is null, held_count is 0 and hold_codes is empty |
| Tags GET/PUT for a valid old entry target | Maps to new Artifact tags, preserving the old target response and tag ETag concurrency checks; compacted targets return 404 |
| `remember` with a non-null collection `expected_revision` | Rejected before writes; reconstruct the intended concurrency contract with the new API |
| `entries/revise` or `entries/retire` with an old citation | Rejected before writes; use Atomic Replace or the forgotten lifecycle respectively |
| `changes`, `capacity` or collection compact | No continuous changes stream across migration, collection capacity or compaction; read historical changes from exact retained revisions |
| `family=memory` Create/Replace or collection rollback | Rejected; use independent Artifact writes and restoration |

Unsupported legacy collection operations return HTTP `422` with `error.code: "legacy_memory_operation_unsupported"`.
`error.details` contains `operation`, replacement `alternatives` routes and `instruction`, plus `kind` and `name`.
Alternatives are empty when no replacement operation exists. Clients must not automatically drop collection CAS
preconditions and retry.

Read the current memory by its old logical identity through `POST /v1/memory/entries/get`:

```json
{
  "scope_id": "S",
  "target": {"type": "memory_entry", "family": "memory", "artifact_id": "OLD", "entry_id": "E"}
}
```

The server validates the retained legacy identity, maps it to a new Artifact and reads its current head/state.
If the original memory is merged, it returns that object's frozen content, merged state and merged_into_id;
it does not follow the result automatically.

Exact historical reads still use the original citation:

```json
{
  "scope_id": "S",
  "citation": {
    "memory_ref": {"family": "memory", "artifact_id": "OLD", "revision": 7},
    "entry_id": "E",
    "entry_version_id": "V3"
  }
}
```

Supply exactly one mode. A citation never discards its revision to read latest. Newly created memories use their new
Artifact IDs directly.

Python Client methods retain the old names, but `remember_memory` returns `.records`, `search_memory` hits use
`.memory.artifact`, and `list_memory_entries` returns `.entries` and `.next_cursor`. `get_memory_entry` directly returns
`MemoryEntry | AtomicMemoryRecord`: citation mode has `.citation`; target mode has `.artifact`. Upgrade the SDK and
handle the actual model. Old collection `.memory` and citation fields do not apply to new results. Inspect unsupported
errors through `ServerResponseError.status_code`, `.code` and `.details`.
New methods include `get_atomic_memory_state`, `list_atomic_memories`, `search_atomic_memory`, `merge_atomic_memories`,
`change_atomic_memory_lifecycle`, `preview_atomic_memory_restoration` and `restore_atomic_memory`. Use generic Artifact
Client methods for creation, replacement and exact historical reads.
