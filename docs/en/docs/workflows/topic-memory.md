---
title: Use Topic Memory
description: Search topic summaries produced from long-running Sources and read exact revisions on demand.
---

# Use Topic Memory

Topic Memory is an Artifact family for long-running topics. It turns accumulated Sources in one Scope into a `title`,
`summary`, and progressively disclosed `detail`, so an Agent can locate a topic first and read the full detail only
when needed; callers can also submit complete topic content directly through the generic interfaces in
[Manage Artifacts](artifacts.md). It does not replace Memory, Experience, Skill, or Handoff.

Topic Memory is Scope-local. Capturing a Source does not synchronously create a topic; configured background processing
must advance it.

It is useful for questions such as “what have we decided about Aurora's deployment?” when the answer has changed across
several conversations. Topic Memory is historical evidence. It does not override the current prompt, live project state,
authorization, or the Agent's other instructions.

## How it differs from other context

| Surface | Stores | Typical use |
| --- | --- | --- |
| Source | Captured input and other evidence | Trace where information came from |
| Memory | Curated, durable decisions, constraints, or facts | Recall one stable piece of knowledge |
| Topic Memory | A title, summary, detail, and Source references for an evolving topic | Follow a project thread across related inputs |
| Prepared Context | A bounded response assembled for one request | Give an Agent relevant historical context |

Topic Memory is not created by an explicit Memory write. A meaningful Source window, supported Generation settings,
and enabled Topic processing are required. The model can revise an existing topic, merge evidence, or decide that no
new topic revision is warranted.

Registered remote Source observations can share a processing window with built-in Sources. Topic processing uses the
stored standard text-evidence projection when available, or the captured payload otherwise; it does not require a
local adapter for the remote Source. Published revisions retain the original Source references for provenance.

## Lifecycle

```text
Source → background cursor → flush request → new or updated immutable Topic Memory Revision
      → search current heads → get an exact Revision
```

`flush` persists a processing request without waiting for background work. `accepted` means the request was accepted and
`idle` means the Source cursor is already current; neither means that a particular topic has been generated.

## Enable automatic Topic Memory

Use the configuration wizard to select full memory, or configure the deployment directly. Topic Memory needs:

- a supported Generation model and valid provider credentials;
- a positive `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SCHEDULE_SECONDS` value to admit new automatic work;
- meaningful Source evidence in the Scope.

Embedding is optional for Topic Memory itself. Configure a compatible Embedding model and dimension when the deployment
should offer vector or hybrid retrieval. The dedicated Topic search exposes the selected retrieval mode in each
response and uses the deployment default. The [unified Artifact search](search-artifacts.md) also accepts explicit
modes and supported search controls.

The schedule is an admission interval, not a completion deadline. Topic generation also has provider and Worker
timeouts. An unset schedule disables new automatic admission but does not discard already accepted work. For SQLite deployments, Topic Workers
with a Generation model require a persistent, file-backed database rather than an in-memory database. See
[Configure models and full memory](../get-started/configure-models.md) and
[Configuration options](../operate/configuration.md) for the complete settings and deployment constraints.

## Inspect and retry blocked processing

After processing exhausts its allowance, the same Scope's later Sources remain pending. The Supervisor stops launching
Workers for that frontier and periodically checks metadata. Other Scopes can continue. A flush or restart does not
grant more allowance.

Inspect the current Cursor, Source head, target and block with `scope.read` permission:

```bash
powercontext --json topic-memory status --scope-id "$SCOPE_ID"
```

The state is `idle`, `pending`, or `blocked`. A block contains its exact `block_id`, attempted range, retained target,
attempt/request/token counters, terminal `error_code`, `last_error_code`, and UTC `blocked_at`. An active third attempt
remains pending because it can still finish. For legacy budgets, `blocked_at` is the first observation time and an
unknown last error is null.
`source_through` is the exact selected end, or null when unknown; `budget_source_through` separately preserves cumulative
allowance coverage. Imported historical blocks without their original budget may have null cumulative coverage.
`legacy_source_through` preserves an imported attempts-table value whose range semantics cannot be proven; it is null
for new blocks. Successful and superseded attempts leave no retained history. Block snapshots and accepted receipts
remain available indefinitely, without automatic expiry.

Existing databases require the explicit [Topic recovery migration](../operate/database-migrations.md#topic-memory-recovery-bundle).
Normal startup checks the format before business writes and does not migrate it.

Correct the cause, then copy the returned block ID into `BLOCK_ID`. A Scope administrator can explicitly grant one
new bounded allowance and request processing:

```bash
powercontext --json topic-memory retry --scope-id "$SCOPE_ID" --block-id "$BLOCK_ID"
powercontext --json topic-memory block --scope-id "$SCOPE_ID" --block-id "$BLOCK_ID"
```

Retry requires `scope.admin`. Its `accepted` response means the request committed; it does not promise generation
success. These reads require `scope.read`, which the `scope.admin` role does not include. Repeating the same ID returns
the original acceptance even after processing is disabled, and cannot reset a later attempt. An unretried block
that no longer owns the current frontier returns HTTP 409. Cursor, target and Sources are retained; the original block
and its retry timestamp remain readable after progress resumes. Normal flushes and restarts retain their existing
budget behavior. Each explicitly accepted retry grants the same three-attempt and provider ceilings.

The equivalent Python Client SDK methods are `get_topic_memory_processing_status`,
`get_topic_memory_processing_block`, and `retry_topic_memory_processing`. Their HTTP routes are
`POST /v1/topic-memory/processing/status`, `POST /v1/topic-memory/processing/blocks/get`, and
`POST /v1/topic-memory/processing/retry`. These administrative operations are not MCP tools.

## Verify a real topic

Use the [Quick Start Topic Memory check](../get-started/quickstart.md#4-verify-memory-in-your-selected-mode)
for a full Agent and Dashboard acceptance flow:

1. Send a concrete project decision in a new session.
2. Confirm that the input appears as a Source in the same Scope.
3. Wait for the configured inspection interval and model processing, then confirm a related Topic Memory exists.
4. Send a related update and check the topic content or revision history.
5. Start a new session with the same Scope and retrieve the topic with citations.

Do not treat `doctor codex`, a readiness response, or the passage of one minute as proof that the business flow works.
Those checks cover installation or service state, not Source capture, generation, publication, and new-session retrieval.

## Request processing

The caller needs `scope.contribute` access to the target Scope. For the HTTP examples below, use the Server URL and
credentials from your trusted environment and include an `Authorization: Bearer <token>` header when Access is enabled.
Replace `project-a` with the same real Scope ID for every request.

Send a flush request:

```http
POST /v1/topic-memory/flush
Content-Type: application/json

{"scope_id":"project-a"}
```

The response contains only `status`. Source capture, topic generation, and index updates run asynchronously through the
configured worker. Without a generation model or background processing capability, `flush` cannot create topics by itself.
Whether vector or hybrid retrieval is available is a deployment choice; the caller does not select it in this request.

## Generated content admission

A Source must contain durable information to justify a new Topic or revision. A vague request such as “check the
dashboard” does not establish a result. When there is no durable addition or correction, processing may complete
without publishing a Topic and advance the Source cursor normally.

Before secondary retrieval and after reconciliation, the worker rejects recognizable numbered placeholder bodies
and content consisting only of internal identity-coordination boilerplate. It checks combinations of fields rather
than banning identifiers: a genuine protocol named `T-19` or an investigation quoting `candidate-0001` remains valid.
Ordinary identity-preservation rules need no literal match to evidence. Template detection additionally requires
generated section labels or supplied internal IDs within the bounded boilerplate vocabulary; coordination language
alone is insufficient. Common sentence endings, paired emphasis, whole-field inline code, and block quotes do not exempt numbered
placeholder templates. Quote support first checks the original field, then permits removal of these outer display
wrappers without deleting punctuation or joining evidence fields. Known stage IDs, including work, component, and
temporary proposal IDs, remain available to admission after compaction and reconciliation. For quoted templates, summary and detail may match different cited Sources or the selected
exact history. Titles need no special keywords; uncited Sources and unselected history cannot supply quote support.
Pure numbered placeholder bodies remain rejected.
These conservative checks do not verify every assertion or guarantee semantic quality.

A rejected proposal fails the entire Window. Existing heads, search projections and the cursor remain unchanged;
the worker records `invalid_topic_placeholder` or `invalid_topic_coordination_content` and may retry within the
existing attempt and provider budgets. Exhaustion retains the Sources and pending frontier for operator remediation.
Repeated flushes and restarts do not replenish that budget. Rejection is not a successful no-op.

Admission applies to automatic generation, including custom Prompts. Direct generic Artifact writes retain their
structural validation. Existing revisions are not scanned, deleted or rewritten automatically; inspect exact
revisions before restoring useful content through the generic replacement interface.

Custom demonstration checks use only explicit evidence bodies and the selected historical Topic as support for
quoted templates. Intermediate proposals carry references, not Source evidence. In `reduce`, `reconcile`, or an
`evolve` example without sufficient explicit evidence or selected history, a template quotation is rejected even
when it repeats intermediate input. Use concrete explanatory content instead, or supply supporting evidence where
the stage input permits it. Ordinary business content remains allowed. Saved custom Prompts are checked again when
resolved; unsupported examples fail with `prompt_definition_incompatible`. Example JSON and stage schemas are unchanged.

## Search current topics

Search requires `scope.read` access. Send a non-empty `scope_id` and `query`, with an optional `limit` from 1 to 20
(default 10):

```http
POST /v1/topic-memory/search
Content-Type: application/json

{"scope_id":"project-a","query":"release process","limit":5}
```

The response reports the deployment's actual `mode` (`fts` or `hybrid`) and `hits`. Each hit contains an exact `artifact`
reference, `title`, `summary`, an optional `snippet`, `score`, and `matched_by`. This dedicated search sees only current
Topic Memory heads in the current Scope; it does not search across Scopes or accept a caller-selected retrieval mode.

For complete Artifact results, explicit text/vector/hybrid modes, admission, score thresholds, or channel scores, use
[Search Artifacts](search-artifacts.md). Topic fusion controls are documented in
[Fusion algorithms and parameters](search-fusion.md). The dedicated route keeps its existing summary response and
`0`–`100` score scale.

Explicit vector/hybrid search uses the configured query embedding provider even in an empty Scope; provider errors and
fallback depend on the requested mode. SQLite ranks exact nearest Topics across both title/summary and detail vectors.
It first checks a bounded exact neighbor prefix. If that prefix contains enough eligible distinct Topics or is exhausted,
it returns; otherwise one full exact query replaces the prefix. Worst-case latency grows with eligible chunks. At exactly equal
distances, boundary Topic membership and the choice of an equally near chunk need not follow global identity or
ordinal order. These choices can change fused candidates and scores; the final fused-score tie rules still apply.
seekdb/OceanBase use approximate title/summary retrieval. Detail search ranks current chunks by each Topic's minimum
distance before the candidate limit and payload reads. This scans eligible chunks and has no constant-latency guarantee.
The native fused result remains approximate because its title channel uses ANN; measure recall and tail latency for
the deployment. Startup leaves existing detail HNSW indexes intact and does not create a detail graph for new databases.
Detail queries do not use that graph; an existing graph still incurs database maintenance costs.

## Read exact detail

Do not reconstruct the latest version from a title. Pass the `artifact` from a search hit (`family`, `artifact_id`, and
`revision`) unchanged to `get` to read an immutable snapshot and its direct Source evidence:

```http
POST /v1/topic-memory/get
Content-Type: application/json

{
  "scope_id":"project-a",
  "artifact":{
    "family":"topic-memory",
    "artifact_id":"topic-release",
    "revision":3
  }
}
```

The response contains `title`, `summary`, full `detail`, and `source_refs`. Even after the current topic head advances, the
exact reference resolves to the same historical Revision for audit, citation, and progressive disclosure.

## Write through the generic Artifact interfaces

Besides Source processing, Topic Memory also uses the generic interfaces in [Manage Artifacts](artifacts.md):

| Operation | Route |
| --- | --- |
| Create | `POST /v1/scopes/{scope_id}/artifacts` (`family` is `topic-memory`) |
| Replace | `PUT /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` |
| List heads | `GET /v1/scopes/{scope_id}/artifacts/{family}` |
| Read a head, list revisions, read an exact revision | `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}`, `.../revisions`, `.../revisions/{revision}` |
| Read, replace, and query tags | `GET`/`PUT /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/tags`, `POST /v1/scopes/{scope_id}/artifact-tags/query` |

Creation requires `scope.contribute` on the target Scope, and the request body carries the complete `title`, `summary`,
and `detail`. The content skips semantic generation: topic content, the current head, chunks, and the retrieval indexes
enabled by the deployment are committed in one operation, so generic reads and the dedicated search see the same topic
version. A replace requires current `scope.admin` access, creates the next immutable Revision, and must carry the current
head's `If-Match`.

Manage tags as described in [Organize with tags](manage-artifact-tags.md); whole-Artifact Topic Memory tags are
read with `scope.read` and modified with `scope.admin`.

Publish one exact Revision into another Scope with `POST /v1/artifact-publications`, which requires `scope.admin` in
both the source and target Scopes. Publication creates an independent identity in the target Scope and carries its
retrieval indexes; tags and direct Sources are not copied.

## Assemble into PreparedContext

To inject topic summaries into one Agent turn, add `topic-memory` explicitly to `assembly` in
`POST /v1/context/prepare`, for example:

```json
{
  "scope_id": "project-a",
  "query": "release process",
  "assembly": {
    "sections": [
      {"family": "topic-memory", "limit": 2}
    ]
  }
}
```

When `assembly` is omitted, the Runtime may retain the default Topic Memory recall when the deployment and data support it.
An empty `sections` array disables candidate artifact recall. `PreparedContext` is still temporary and does not create a new
Topic Memory Revision. An explicit `assembly: {}` uses the Memory and Experience defaults and excludes Topic Memory.
The prepared result includes bounded title, summary, optional matching snippet, Scope, and exact revision citations;
read the exact revision to retrieve full Topic detail. See [Prepare context text](prepare-context-text.md) for grouped
Markdown rules.

## Search and read through MCP

MCP exposes the read-only `search_topic_memory` and `get_topic_memory` tools. The Agent should search with a focused
query and pass the complete returned Artifact reference unchanged to the exact-read operation. The flush operation is
HTTP-only and is not exposed as an MCP tool.

## Current boundaries

- The generic interfaces do not accept Topic Memory delete or retire operations; topics are stored as immutable Revisions, and background processing or an explicit write advances the current head.
- Source capture does not synchronously generate a topic; background processing and the required generation capability are needed.
- Backups, recovery, worker availability, and retrieval failures belong to [deployment and operations](../operate/index.md), not this lifecycle.

## Diagnose missing or stale topics

| Symptom | Check first |
| --- | --- |
| No Source exists | Agent hook or connector, Server URL, token, and Scope binding |
| Source exists but no topic appears | Generation readiness, positive Topic schedule, Worker errors, and whether the evidence is meaningful |
| Search returns no hit | Current Scope, query text, retrieval capability, and whether processing has completed |
| Search is `fts` instead of `hybrid` | Embedding endpoint, model, dimensions, and the configured retrieval Profile |
| Topic is stale after a flush | Flush admission is asynchronous; inspect Worker state and search again after completion |
| New session cannot recall it | Same Scope, context-assembly policy, exact citations, and Agent integration diagnostics |

For service and model failures, see [Troubleshoot](../operate/troubleshoot.md). For the processing cursor, retries,
budgets, and deployment roles, see [Configuration options](../operate/configuration.md) and the
[Topic Memory RFC](../../rfcs/1417_topic_memory.md).
