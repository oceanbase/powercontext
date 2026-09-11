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

## Lifecycle

```text
Source → background cursor → flush request → new or updated immutable Topic Memory Revision
      → search current heads → get an exact Revision
```

`flush` persists a processing request without waiting for background work. `accepted` means the request was accepted and
`idle` means the Source cursor is already current; neither means that a particular topic has been generated.

## Request processing

The caller needs `scope.contribute` access to the target Scope:

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
Topic Memory Revision. See [Prepare context text](prepare-context-text.md) for grouped Markdown rules.

## Current boundaries

- There is no generic Topic Memory create, update, delete, or retire endpoint; topics are generated from Sources and stored as immutable Revisions.
- Topic Memory is not in the current Taggable Artifact family list, so Memory, Experience, Skill, and Handoff tag APIs do not apply.
- Source capture does not synchronously generate a topic; background processing and the required generation capability are needed.
- Backups, recovery, worker availability, and retrieval failures belong to [deployment and operations](../operate/index.md), not this lifecycle.
