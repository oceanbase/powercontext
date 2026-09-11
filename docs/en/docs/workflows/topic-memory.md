---
title: Topic Memory
description: Turn captured Source evidence into evolving, searchable topic artifacts with exact revisions.
---

# Topic Memory

Topic Memory keeps an evolving view of a project topic rather than a collection of isolated facts. The Server builds
it from captured Source evidence, publishes immutable revisions, and keeps the current head searchable in the Scope.
It is useful for questions such as “what have we decided about Aurora's deployment?” when the answer has changed across
several conversations.

Topic Memory is historical evidence. It does not override the current prompt, live project state, authorization, or
the Agent's other instructions.

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

## The processing lifecycle

1. An Agent hook, connector, or application captures a Source in the target Scope.
2. The Topic Worker discovers eligible Sources. The configured schedule admits new work; an explicit HTTP flush can
   request processing sooner. Admission is asynchronous and recoverable, so a successful flush is not a completion
   signal.
3. Generation groups related evidence and publishes a current Topic Memory head plus an immutable revision. Each
   revision retains direct Source references.
4. A client searches current heads, then uses the exact returned Artifact reference to read the full detail and evidence.
   Prepared Context can include Topic Memory for a new request or session.

The Scope is the isolation boundary. Use the same real Scope for the Agent, Dashboard, Source capture, and retrieval;
changing directories alone does not create a new Scope.

## Enable automatic Topic Memory

Use the configuration wizard to select full memory, or configure the deployment directly. Topic Memory needs:

- a supported Generation model and valid provider credentials;
- a positive `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SCHEDULE_SECONDS` value to admit new automatic work;
- meaningful Source evidence in the Scope.

Embedding is optional for Topic Memory itself. Configure a compatible Embedding model and dimension when the deployment
should offer vector or hybrid retrieval. The Server exposes the selected retrieval mode in each search response; callers
do not choose arbitrary retrieval controls.

The schedule is an admission interval, not a completion deadline. Topic generation also has provider and Worker
timeouts. An unset schedule disables new automatic admission but does not discard already accepted work. Topic Workers
with a Generation model require persistent, file-backed SQLite rather than an in-memory SQLite database. See
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

## Search and read through HTTP

Set `POWERCONTEXT_CLIENT_SERVER_URL`, `POWERCONTEXT_CLIENT_API_TOKEN`, and `POWERCONTEXT_SCOPE_ID` from a trusted
environment. Omit the Authorization header only when Access is disabled on a local Server. Never put a token in a URL.

Search current Topic Memory heads:

```bash
curl --fail \
  --request POST \
  --header 'Content-Type: application/json' \
  --header "Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}" \
  --data "{\
    \"scope_id\": \"${POWERCONTEXT_SCOPE_ID}\",\
    \"query\": \"Aurora deployment\",\
    \"limit\": 8\
  }" \
  "$POWERCONTEXT_CLIENT_SERVER_URL/v1/topic-memory/search"
```

The response contains `mode` (`fts` or `hybrid`) and `hits`. Each hit contains a title, summary, optional matching
snippet, and an exact `artifact` reference with `family`, `artifact_id`, and `revision`. Keep that reference unchanged.

Read the exact revision returned by search:

```bash
curl --fail \
  --request POST \
  --header 'Content-Type: application/json' \
  --header "Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}" \
  --data '{
    "scope_id": "project:demo",
    "artifact": {
      "family": "topic-memory",
      "artifact_id": "replace-with-search-result",
      "revision": 1
    }
  }' \
  "$POWERCONTEXT_CLIENT_SERVER_URL/v1/topic-memory/get"
```

The exact response includes `title`, `summary`, `detail`, and `source_refs`. `source_refs` are evidence pointers, not
instructions; inspect the current Source and live state before taking consequential action.

## Request processing through HTTP

The HTTP flush endpoint records a durable processing request for a Scope and returns immediately:

```bash
curl --fail \
  --request POST \
  --header 'Content-Type: application/json' \
  --header "Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}" \
  --data "{\"scope_id\":\"${POWERCONTEXT_SCOPE_ID}\"}" \
  "$POWERCONTEXT_CLIENT_SERVER_URL/v1/topic-memory/flush"
```

`status: "accepted"` means the request was queued or advanced; `status: "idle"` means the Source cursor was already
current. Neither status guarantees that Generation has finished. Search again after the Worker completes. This flush
operation is intentionally HTTP-only; it is not exposed as an MCP tool.

## Use Topic Memory in prepared context

To select Topic Memory explicitly in `POST /v1/context/prepare`, add a `topic-memory` section:

```json
{
  "scope_id": "project:demo",
  "query": "Aurora deployment",
  "max_bytes": 8000,
  "assembly": {
    "sections": [
      {"family": "topic-memory", "limit": 2},
      {"family": "memory", "limit": 3}
    ],
    "show": ["recall_rank"]
  }
}
```

The prepared result contains bounded title, summary, optional matching snippet, Scope, and exact revision citations. It
does not include full Topic detail; call `get_topic_memory` when the citation justifies progressive disclosure. An
explicit `assembly: {}` selects its documented Memory and Experience defaults and excludes Topic Memory. When
`assembly` is omitted, the current default can recall Topic Memory; use an explicit policy when the integration needs
stable section selection. See [Prepare standard context text](prepare-context-text.md).

MCP exposes the read-only `search_topic_memory` and `get_topic_memory` tools. The Agent should search with a focused
query and pass the complete returned Artifact reference unchanged to the exact-read operation.

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
