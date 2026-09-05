---
title: Organize Artifacts with custom tags
description: Label logical Artifacts and Memory entries, then find them with exact tag filters.
---

# Organize Artifacts with custom tags

Custom tags organize Memory, Experience, Skill, and Handoff Artifacts within one Scope. A Memory Artifact and each
logical entry inside it have independent tag sets. Tags follow these identities across content revisions; they do not
change content, lineage, embeddings, or Context Versions.

## Use the Dashboard

Start the Server and open its Overview page. In **Custom tags**:

1. Select the exact Scope, target kind, family, and Artifact. For a Memory entry, also select its entry ID.
2. Enter one label per line and select **Save tags**. Saving an empty field clears that target's labels.
3. Enter labels under **Find by exact labels**. Choose **All** or **Any**, then select **Find targets**.
4. Select a result to edit its tags. **Include inactive** also returns inactive Memory entries and deprecated or retired
   Artifacts.

If another writer changes the labels, saving displays a conflict and preserves your input. Use **Reload tags** to read
the current state before deciding what to save. Reloading replaces the input field; copy any text you want to retain first.

## Use the Python Client

You need a running Server and an existing Scope and Artifact. Take their IDs from the Dashboard or the corresponding
Scope and Artifact APIs; a title is not an ID. Set these non-secret example variables in your terminal:

```bash
export POWERCONTEXT_TAG_SCOPE='your-existing-scope-id'
export POWERCONTEXT_TAG_FAMILY='skill'
export POWERCONTEXT_TAG_ARTIFACT='your-existing-artifact-id'
```

Run this with `powercontext` installed. If the Server requires authentication, provide its bearer token through
`POWERCONTEXT_SERVER_AUTH_TOKEN`; do not embed it in the script.

```python
import asyncio
import os

from powercontext.client import PowerContextClient
from powercontext.http import QueryArtifactTagsRequest, ReplaceArtifactTagsRequest


async def main():
    scope = os.environ["POWERCONTEXT_TAG_SCOPE"]
    family = os.environ["POWERCONTEXT_TAG_FAMILY"]
    artifact = os.environ["POWERCONTEXT_TAG_ARTIFACT"]
    async with PowerContextClient(
        "http://127.0.0.1:8000", token=os.getenv("POWERCONTEXT_SERVER_AUTH_TOKEN")
    ) as client:
        current = await client.get_artifact_tags(scope, family, artifact)
        if current is None:
            raise RuntimeError("An unconditional read must return the current tag set")
        saved = await client.replace_artifact_tags(
            scope, family, artifact,
            ReplaceArtifactTagsRequest.model_validate({"tags": ["customer-a", "release"]}),
            expected_etag=current.etag,
        )
        print(saved.tag_set.model_dump(mode="json")["tags"])
        matches = await client.query_artifact_tags(
            scope, QueryArtifactTagsRequest.model_validate({"tags": ["CUSTOMER-A"]})
        )
        print([item.target.model_dump(mode="json") for item in matches.items])


asyncio.run(main())
```

The output contains the two saved labels and a matching target. For an entry, use `get_memory_entry_tags` and
`replace_memory_entry_tags` with `(scope_id, artifact_id, entry_id)`. Read the entry ID from the current Memory manifest
or a Memory citation, not from `entry_version_id`. A Scope can hold multiple Memory Artifacts; the existing scoped
Memory list and search operations address the runtime's designated Memory.

## HTTP and retrieval filters

| Method | Path | Purpose |
| --- | --- | --- |
| GET / PUT | `/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/tags` | Read or replace an Artifact's labels |
| GET / PUT | `/v1/scopes/{scope_id}/artifacts/memory/{artifact_id}/entries/{entry_id}/tags` | Read or replace a logical entry's labels |
| POST | `/v1/scopes/{scope_id}/artifact-tags/query` | Find tagged targets across families |

PUT accepts `{"tags":["customer-a","release"]}` and requires the ETag returned by GET in `If-Match`. Missing `If-Match`
returns **428**; stale or wrong-target state returns **412**. An unchanged conditional GET returns **304**.
`tag_digest` describes the canonical tag set, but is not the HTTP mutation precondition.

Artifact listing accepts repeated `tag` parameters and optional `tag_match=all|any`, for example
`/v1/scopes/{scope_id}/artifacts/skill?tag=release&tag=customer-a&tag_match=all`.
Supplying `tag_match` without `tag` is invalid.

Memory entry listing and search accept this optional request field:

```json
{"tag_filter":{"tags":["customer-a","release"],"match":"all"}}
```

Search filters entry tags, not the parent Memory Artifact's tags, and still returns only active entries. Full-text and
vector candidates are filtered in the database before candidate limits, fusion, and reranking. Tagged vector queries
use exact distance ordering over the eligible set on SQLite and OceanBase; this can cost more than an unfiltered
approximate search. A backend without tag-filter support rejects the request instead of silently post-filtering.

Tag queries return exact current Artifact references or Memory citations, ordered by family, target type, Artifact ID,
and target ID. Pass `next_cursor` unchanged with the same filters, Scope, and caller. Cursors expire after one hour;
invalid or mismatched cursors return **400**, expired cursors **410**. Each page is internally consistent, but pagination
does not freeze a snapshot across requests.

## Label rules and storage

- A target holds at most 32 labels; a filter accepts 1–16 labels.
- Each label has 1–64 Unicode code points, no outer whitespace, and no control, surrogate, or unassigned characters.
- Matching uses NFC normalization followed by Unicode case folding. The submitted display spelling is retained.
  Normalized duplicates such as `Straße` and `STRASSE` are rejected atomically. A normalized key may not exceed 128
  code points.
- Tags are Scope-local discovery metadata, not permissions or trusted instructions. They are not added to model prompts,
  Skill package frontmatter, or publication/import payloads. Published copies start without the source target's tags.
- Inactive entries remain taggable while present in the current authoritative manifest. Rebuilding active search
  projections does not remove their labels.

All assignments live in `pc_artifact_tags`, with a foreign key to the owning Artifact head. The table retains the full
normalized key and indexes a 32-byte SHA-256 key fingerprint. This preserves the parent column lengths required by
OceanBase while keeping composite indexes below its 3072-byte limit. Matching checks both fingerprint and complete key.
Identical replacement preserves assignment timestamps. Existing Artifacts need no backfill; their initial tag sets are
empty. Include the table in backups and restore it after Artifact heads.

See the Server's [HTTP API reference](/api) for complete request and response schemas.
