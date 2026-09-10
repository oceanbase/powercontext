---
title: Configure models and full memory
description: Understand Generation, Embedding, schedules, and Artifact triggers, then verify model-driven memory processing.
---

# Configure models and full memory

[Quick Start](quickstart.md) uses the wizard to configure full memory. This page explains the API details you need,
which capabilities use models, and the policies and behavior to check after saving configuration.

## Choose model connections

Run `powercontext config init --language en --output .env` and select full memory or individual capabilities.
The wizard asks only for the connections those capabilities require. An API protocol describes a model service's
interface, not which Coding Agent you use.

| Connection | Purpose | Prepare |
| --- | --- | --- |
| Generation | Memory extraction, Topic Memory, Profile, Experience, and Skill generation | Supported protocol, complete base URL, API key, and generation model name |
| Embedding | Semantic retrieval and vector indexing | Embedding endpoint, credentials, model, and a supported output dimension |
| Rerank (optional) | Reorder retrieval candidates | Reuse generation or configure a separate reranking connection as prompted |

Background capabilities share the configured Generation Provider by default; they do not automatically reuse your
Codex/Claude subscription session or sign-in credentials. Basic memory needs no separate Generation API:
an Agent can explicitly save content it has prepared through MCP, but that does not automatically process ordinary Sources.

OpenAI-compatible Chat Completions, OpenAI Responses, and Anthropic-compatible Messages are different protocols.
Choose the interface your model service actually offers. OpenAI-compatible can also describe a third-party service;
it does not require a particular Agent subscription. The wizard does not test credentials, billing limits, model
availability, or remote connectivity. Readiness and actual processing after startup check those conditions.

Embedding can share an endpoint and credentials with Generation, but usually uses a different model.
Its dimension must match an output size the selected model supports. The Embedding Profile ID names that combination
of model, dimension, and vector conventions; it is not another API key. For a new database, you can use the suggested ID.
With existing vectors, preserve their model/Profile/dimension relationship rather than changing it to suppress an error.

## Full capabilities do not generate every Artifact from every message

| Capability | Conditions for generation |
| --- | --- |
| Memory | Captured Sources, working Generation, and enabled Memory processing |
| Topic Memory | Meaningful Source evidence, Generation settings supported by Topic processing, and enabled Topic scheduling |
| Profile | Working Generation, Profile scheduling, and an enabled generation policy for the target Scope |
| Experience | A `task-outcome` Source with task-result semantics; produces a review candidate without automatic approval |
| Skill | On-demand generation, without a fixed “every chat creates a Skill” schedule |
| Semantic retrieval | Working Embedding and compatibility with the existing vector Profile and dimension |

Memory, Topic Memory, and Experience have separate inspection intervals. Profile uses a separate cron schedule.
The wizard writes the selected recommendations into your configuration; not every Artifact completes once every 60 seconds.
A Generation request timeout, total Worker timeout, and inspection interval control different limits.

Ordinary conversation is not sufficient evidence for every Experience or Skill workflow. Supply task outcomes and
complete review through the [experience workflow](../workflows/create-and-review-experience.md).
Profile review is described in [Scope profiles](../workflows/use-profiles.md).

## Check after startup

In the Server terminal:

```bash
powercontext config validate --env-file .env
powercontext server run --env-file .env
```

In another terminal, load only the client file:

```bash
set -a
. ./.env
set +a
powercontext ready
powercontext capabilities
```

Check both database/Runtime readiness and configured model dependencies. `degraded` does not prove model extraction works.
If search offers only `auto, fts` rather than `vector, hybrid`, check the Embedding connection, Profile, and dimension.
Do not load the Server's model credentials into the Agent terminal; the client file contains its connection settings.

## Enable a Profile policy for the Scope

Create and bind a real Scope following [Quick Start](quickstart.md#3-create-a-scope-and-install-the-codex-plugin),
then reload the client environment. The following example enables review-required generation for the Codex Scope.
For Claude Code, use `POWERCONTEXT_CLAUDE_SCOPE_ID` in the first line.
Read the current policy version, using version 0 only when the policy does not exist (404):

```bash
export POWERCONTEXT_TEST_SCOPE_ID="$POWERCONTEXT_CODEX_SCOPE_ID"
python3 - <<'PY'
import json
import os
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

base = os.environ["POWERCONTEXT_CLIENT_SERVER_URL"].rstrip("/")
scope = quote(os.environ["POWERCONTEXT_TEST_SCOPE_ID"], safe="")
url = f"{base}/v1/scopes/{scope}/profile-policy"
headers = {
    "Authorization": "Bearer " + os.environ["POWERCONTEXT_CLIENT_API_TOKEN"],
    "Content-Type": "application/json",
}
try:
    with urlopen(Request(url, headers=headers), timeout=30) as response:
        version = json.load(response)["version"]
except HTTPError as error:
    if error.code != 404:
        raise
    version = 0
payload = json.dumps({
    "generation_enabled": True,
    "activation_mode": "review_required",
    "expected_version": version,
}).encode()
with urlopen(Request(url, data=payload, headers=headers, method="PUT"), timeout=30) as response:
    print(json.dumps(json.load(response), indent=2))
PY
```

If another operation changes the policy concurrently, read the latest version before retrying a conflicting update.
Do not use a fixed `expected_version: 0` for every update. The configured cron processes eligible Scopes with new Sources.
Review mode creates a Candidate without replacing the active Profile until approval. Enabling the policy does not
immediately generate content. To test existing input immediately, see Profile flush in [Scope profiles](../workflows/use-profiles.md).

## Verify extraction and recall

Follow the primary [Quick Start check](quickstart.md#4-verify-topic-memory-with-ordinary-conversation):

1. Agent input arrives as Source evidence in the bound Scope.
2. Topic Memory contains a related topic and evidence.
3. After a second related Source, topic content or revision history reflects the update.
4. A new session recalls the content from the same Scope with citations.

To diagnose Memory extraction separately, flush the same Scope after real Source evidence has arrived:

```bash
curl -fsS -X POST "$POWERCONTEXT_CLIENT_SERVER_URL/v1/memory/flush" \
  -H "Authorization: Bearer $POWERCONTEXT_CLIENT_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"scope_id\":\"$POWERCONTEXT_CODEX_SCOPE_ID\"}"
```

Check the returned cursor and the Scope's Memory/Source references. `idle` can mean background processing already ran;
it does not necessarily mean no memory exists. Memory flush does not complete all Topic/Profile/Experience processing.
Use `powercontext stats --scope-id "$POWERCONTEXT_CODEX_SCOPE_ID"` to inspect usage.

For a repeatable acceptance record, assign a source identifier before sending the test input and inspect the resulting
entry. The list response exposes `current_cursor` and each entry's `position`, `entry_id`, `source_refs`, and `matched_by`
fields; these let you distinguish captured evidence from a later generated Artifact.

```bash
SOURCE_ID="quickstart-$(date +%s)-$$"
echo "Send the acceptance input with source id: $SOURCE_ID"
curl -fsS "$POWERCONTEXT_CLIENT_SERVER_URL/v1/memory/entries/list?scope_id=$POWERCONTEXT_CODEX_SCOPE_ID" \
  -H "Authorization: Bearer $POWERCONTEXT_CLIENT_API_TOKEN"
```

| Symptom | Check first |
| --- | --- |
| No Source | Hook loading, capture setting, matching URL/token/Scope |
| Source exists but topic stays unchanged | Topic schedule, model errors, Worker status, and whether evidence warrants an update |
| Timeouts | Connectivity, model latency, request/Worker timeouts; changing only the inspection interval is insufficient |
| Dashboard and Agent show different data | Server URL and real Scope ID |
| New session cannot find content | Scope binding, context assembly/explicit search, saved records, and citations |

See [Troubleshoot](../operate/troubleshoot.md) and the [configuration reference](../operate/configuration.md).
