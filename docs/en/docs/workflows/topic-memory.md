---
title: Use Topic Memory
description: Search topic summaries produced from long-running Sources and read exact revisions on demand.
---

# Use Topic Memory

Topic Memory is a read-oriented Artifact family for long-running topics. It turns accumulated Sources in one Scope into a
`title`, `summary`, and progressively disclosed `detail`, so an Agent can locate a topic first and read the full detail only
when needed. It does not replace Memory, Experience, Skill, or Handoff.

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
should offer vector or hybrid retrieval. The Server exposes the selected retrieval mode in each search response; callers
do not choose arbitrary retrieval controls.

The schedule is an admission interval, not a completion deadline. Topic generation also has provider and Worker
timeouts. An unset schedule disables new automatic admission but does not discard already accepted work. For SQLite deployments, Topic Workers
with a Generation model require a persistent, file-backed database rather than an in-memory database. See
[Configure models and full memory](../get-started/configure-models.md) and
[Configuration options](../operate/configuration.md) for the complete settings and deployment constraints.

## Verify a real topic

Use the [Quick Start Topic Memory check](../get-started/quickstart.md#4-verify-topic-memory-with-ordinary-conversation)
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

## Search current topics

Search requires `scope.read` access. Send a non-empty `scope_id` and `query`, with an optional `limit` from 1 to 20
(default 10):

```http
POST /v1/topic-memory/search
Content-Type: application/json

{"scope_id":"project-a","query":"release process","limit":5}
```

The response reports the deployment's actual `mode` (`fts` or `hybrid`) and `hits`. Each hit contains an exact `artifact`
reference, `title`, `summary`, an optional `snippet`, `score`, and `matched_by`. Search sees only current Topic Memory
heads in the current Scope; it does not search across Scopes or accept a caller-selected retrieval mode.

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

- There is no generic Topic Memory create, update, delete, or retire endpoint; topics are generated from Sources and stored as immutable Revisions.
- Topic Memory is not in the current Taggable Artifact family list, so Memory, Experience, Skill, and Handoff tag APIs do not apply.
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
