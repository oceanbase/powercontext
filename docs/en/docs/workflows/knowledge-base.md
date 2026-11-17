---
title: Knowledge-base plugin
description: Import Markdown and images, maintain document revisions, and search across knowledge-base Scopes.
---

# Knowledge-base plugin

The `knowledge-base` plugin implements [RFC #2570](https://github.com/oceanbase/powercontext/pull/2570).
It owns the `document` Artifact family and supports SQLite, embedded SeekDB and OceanBase MySQL tenants.
The plugin provides ingestion, parsing, immutable chunk results, hybrid retrieval and Dashboard pages.

Requires a Server version containing the framework contracts from RFC [#2659](https://github.com/oceanbase/powercontext/pull/2659)
and implementation [#2660](https://github.com/oceanbase/powercontext/pull/2660). The plugin contains no core/framework patches.
Dashboard document operations use the framework `client.artifacts` bridge.

## Install and start

Run from a repository checkout. These commands create a separate local instance, install optional model/OCR dependencies,
and bind the Server to loopback. The first embedding call downloads the pinned public model; provision its cache before
using an offline deployment. Server and Worker must use the same Python environment, plugin code and local data directory.

```bash
uv sync --locked --extra knowledge-base --extra server --extra cli
mkdir -p "$PWD/.powercontext/kb-demo/plugins"
cp -R plugins/knowledge-base "$PWD/.powercontext/kb-demo/plugins/knowledge-base"
export POWERCONTEXT_KB_DATA_DIR="$PWD/.powercontext/kb-demo/data"
export POWERCONTEXT_KB_IMPORT_ROOTS="$PWD/docs"
export POWERCONTEXT_SERVER_PLUGINS_DIR="$PWD/.powercontext/kb-demo/plugins"
export POWERCONTEXT_SERVER_WORKSPACE="$PWD/.powercontext/kb-demo"
export POWERCONTEXT_SERVER_DATABASE='{"kind":"sqlite","url":"sqlite+aiosqlite:///.powercontext/kb-demo/core.db"}'
export POWERCONTEXT_SERVER_RUNTIME='{"artifact_processing_families":[]}'
export POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true
uv run --no-sync powercontext server run --host 127.0.0.1 --port 8765
```

Use the deployment's normal authentication and authorization configuration. Open `/dashboard/plugins/knowledge-base`
The Dashboard opens the knowledge base selected by the host Scope picker in its owning space, without changing the
user's default space. An ordinary or inaccessible knowledge-base Scope shows a notice and lets the user choose a
knowledge base from the default space or switch spaces.

after signing in. Space creation/renaming, knowledge-base creation and processing configuration require `server.admin`;
imports require Scope contribution and searches require Scope read. Editing and forgetting also enforce Artifact authority.
New replacement requests for an existing document, including changed-file uploads, retries and explicit reprocessing, also
require its Artifact write authority. Replaying an already accepted request retains its original fixed base and receipt.
Plugin pages receive no session credentials; the Dashboard bridge forwards only declared plugin routes and explicitly
enabled operations for this plugin's own Artifact family, preserving caller identity and version preconditions.

For SeekDB, also install `--extra seekdb` and configure the core SeekDB database. For OceanBase, provision a MySQL user tenant
and core database according to the Server deployment guide. The plugin uses the same backend/account but a separate database,
`powercontext_plugin_knowledge_base`. An administrator may pre-provision it if the
application account cannot create databases; the startup error reports the exact database. No core table columns are added.
SQLite stores a separate plugin database in the framework-managed plugin storage directory. SQL namespaces depend on the plugin
name, not the core database: isolate plugin databases/accounts for independent deployments sharing a SQL tenant.

`POWERCONTEXT_KB_IMPORT_ROOTS` contains allowed local roots separated by the operating system's path separator. An empty value
disables Server-directory imports, while browser uploads remain available. File size is limited to 16 MiB, images to 25 million
pixels, imports to 100 explicit paths per request, and parsed documents to 2,000 chunks. Server/Worker must share the same local
filesystem. Remote object stores, PDF/Office files, restoration, permanent cleanup and generated answers are outside this release.

## Spaces, creation and retries

A space is a plugin-local organizational Scope, not a core parent Scope or an authorization tenant. Each knowledge-base Scope
belongs to one space. A personal space is initialized on first default resolution. Defaults are keyed by authenticated principal;
changing a default requires its current version. Space visibility does not grant access to its knowledge bases.

Let `P=/v1/plugins/knowledge-base/api`. The principal's default comes from `GET P/me/default-space`. If access was revoked, this endpoint still returns the caller's
saved ID/version with `available=false`, and `GET P/spaces` lists other visible spaces. Select a space explicitly and update the
default with its saved version; implicit lists/searches fail until a visible default is selected, without automatic fallback.
Create a knowledge base with an explicit space and a stable request key:

```json
{"space_scope_id":"<space ID>","title":"Operations","idempotency_key":"operations-create-1"}
```

Send this to `POST P/knowledge-bases`. The response includes `scope_id`, plugin settings and their `version`.
The creation identity includes principal, explicit space and request key. Retry the **same body/key**, even after changing the
default space: an interrupted core-Scope/plugin-membership registration repairs the same knowledge base. Missing space is 422;
reusing an identity with different creation parameters is a conflict. Unregistered Scopes cannot accept imports or enter search.

`GET/POST P/spaces` lists/creates spaces; `PATCH P/spaces/{space_id}` renames with `{title,version}`.
`PUT P/me/default-space` accepts `{space_scope_id,version}`. `GET P/knowledge-bases?space_scope_id=...` returns only readable bases.

## Import and process

Choose a space and knowledge base from the plugin’s top navigation. Create a knowledge base beside the selectors;
its documents, retrieval tests and settings share the full content area. The host Dashboard navigation is unchanged.

The Dashboard supports selecting or dragging files, and explicit Server-directory imports. Markdown references to embedded images
remain references; independently imported PNG/JPEG/WebP images use RapidOCR with ONNX Runtime. OCR produces Markdown text blocks
with original polygons, reading order and confidence. Both paths use heading-aware block packing followed by bounded sentence/line
splits. Default limits are 360 characters and 40-character overlap; complete embedding input is checked against the tokenizer limit.
Markdown HTML blocks, including HTML tables, retain their literal content and source lines in chunks.

For browser uploads, send file bytes to:

```text
POST P/scopes/{scope_id}/uploads?relative_path=runbooks/restart.md&idempotency_key=upload-1
Content-Type: application/octet-stream
```

For local import, send this to `POST P/scopes/{scope_id}/imports`:

```json
{"root":"/allowed/runbooks","paths":["restart.md","checklist.png"],"idempotency_key":"batch-1"}
```

Directory import returns one item per path. A deterministic file error, request conflict or missing document write authority
sets that item's `accepted` to false; conflicts and permission failures include `status` and `error`. Other files continue,
and successful items retain their request/Source receipts. Infrastructure failures still fail the request.

The local importer rejects traversal and symlink replacement of the file, allowed root, or its ancestors; directory-descriptor
traversal binds file access after validation. OpenDAL manages immutable, content-addressed local snapshots; Workers
verify their digest before reading. Browser and directory collections have separate stable identities. Same collection/path means
the same document, changed bytes create a new original Source, and renames create a new document. Unchanged bytes/configuration
can reuse the published document without another parse. Reusing a request key with different bytes/explicit parameters is a conflict. A retry retains its initially pinned settings.

Acceptance pins the snapshot, original Source, processing settings, embedding profile and expected document revision. Only
`knowledge-base-process` Sources enter the framework cursor/Supervisor; immutable `knowledge-base-file` Sources are evidence.
No independent plugin task queue or scheduler is added. Deterministic input failures are recorded without blocking later files;
infrastructure failures use the framework's window retry/backoff and can block that Scope until repaired.

`GET P/scopes/{s}/imports/{request_id}` reports pending/processing/failed or a publication verified against core history, separately
from the usable revision. `POST .../imports/{request_id}/retry` accepts a **new** `idempotency_key` and creates a new request.
The recent requests returned by `GET P/scopes/{s}/documents` use the same publication check, so committed work is reported as
completed even if its processing observation still says processing.
`POST P/scopes/{s}/documents/{d}/reprocess` reparses with current settings. Its optional `manual_edits_policy:"replace"` explicitly
permits replacing manual edits; otherwise conflicting reprocessing fails. Optional `expected_revision` rejects a new request
with HTTP 409 if the document has changed since selection; the Dashboard always supplies it. Reprocessing pins the original snapshot and its
document revision together. A concurrent upload, edit or forget prevents stale publication, including changes while the snapshot
is being read; submit a new reprocessing request to use the latest original.
Replaying the same reprocessing key and explicit document/policy/revision condition returns the accepted request's receipt, even if the document's
original file or processing settings have since changed. Interrupted Source registration resumes from the original pinned input
and base; it never rebases onto the current revision. Reusing the key for another document, policy, revision condition or operation is a conflict.
Synchronous chunk edits also check the governance version at publication, so forgetting during embedding preparation rejects the edit.

`PATCH P/scopes/{s}/settings` accepts `{version,chunking:{max_chars,overlap}}`; existing documents change only after reprocessing.
If reference links cannot fit within the chunk size, parsing fails without replacing the published document. Increase `max_chars` and retry the failed processing request.
The deployment owns an independent, pinned `BAAI/bge-small-zh-v1.5` 512-dimensional embedding profile. It does not inherit core models.
This release does not switch models online; a mismatched profile fails closed. Additional parsers can implement the internal parser
contract and register extensions in `pc_knowledge_base/content.py` without adding another Artifact plugin.

## Read, edit and forget

Generic Artifact GET/history returns a compact manifest, including title, result identity, chunk count, original reference and
processing profile. Chunk text lives in separate immutable rows and is fetched through these plugin routes:

| Operation | Route under `P/scopes/{s}/documents/{d}/revisions/{rev}` |
| --- | --- |
| Chunk directory | `GET /chunks?offset=0&limit=50` (metadata only, at most 100) |
| One chunk | `GET /chunks/{chunk_id}` |
| Batch text | `POST /chunks/batch-get` with `{"chunk_ids":["..."]}` (at most 100) |

An exact revision never falls back to the current revision. To edit, use the generic
`PUT /v1/scopes/{s}/artifacts/document/{d}` with `If-Match: "revision:3"`:

```json
{"content":{"operation":"edit_chunk","chunk_id":"<ID>","text":"Verify the latest backup before restarting."}}
```

Use `operation:"remove_chunk"` and omit `text` to remove a chunk. Edits and removals are synchronous and create one new document
revision plus a lineage-only Source containing the original command. Manual text replacement preserves the logical chunk ID;
rechunking generates new IDs. Removing the last chunk creates a valid empty revision. Historical chunks and their indexes remain.
For bulk removal, use `{"content":{"operation":"remove_chunks","chunk_ids":["<ID1>","<ID2>"]}}`
with the same revision precondition. A request accepts 1–100 unique chunk IDs belonging to that revision; an invalid ID or
revision conflict rejects the whole operation. One successful batch creates one document revision and one edit evidence Source.
The document overview supports multi-selection for batch forget and reprocessing. Select-all includes only active documents
matching the current filter among loaded rows. Changing the space, library or filter clears selection; changed revisions are
removed from selection. Confirmation captures each document's Scope and revision. Operations run sequentially through the
single-document APIs and report per-document results; a failure does not roll back successful items. Reprocessing protects
manual edits unless explicitly allowed in the confirmation dialog. A submitted request is not a completed parse: follow its
final state in processing records. Refresh after errors before selecting items again; batches are not automatically retried.

The Dashboard supports selection across loaded chunk pages, clears selection when switching revisions, and disables editing
of historical versions. Use **Settings** to change chunking parameters, then **Document → More → Reparse** to rebuild chunks
from the saved original snapshot. This also rebuilds the new result's search projections; manual edits require replacement confirmation.

New results/indexes are durable before the manifest is committed. Failed publication leaves only invisible, unreferenced results.

`POST /v1/scopes/{s}/artifacts/document/{d}/forget` takes `{revision,state_version}`. Forget changes lifecycle to deprecated,
without adding a document revision or edit Source. Ordinary retrieval immediately excludes it; authorized exact history remains
available. Original Sources and snapshots are retained. Source deletion, cascade deletion and restoration are not implemented.

## Search and storage

```json
{"space_scope_id":"<space ID>","scope_ids":["<operations Scope>","<development Scope>"],"query":"restart prerequisites","mode":"hybrid","limit":10}
```

Send this to `POST P/retrieve`. Omitting the space resolves the current default. Omitting `scope_ids` selects readable knowledge
bases in that space; an explicit empty list selects none. Explicit unreadable/out-of-space IDs fail the whole request. Select at most
20 bases and 50 hits. `POST P/scopes/{s}/retrieve` selects one base independently of the default space. Generic document search and
Prepared Context use the same current-revision filtering.
The plugin retrieval endpoints return individual chunks. The generic Artifact search adapter recalls at most 50 ranked chunks,
groups them by document revision and applies its limit to documents; each document's score is its best matching chunk's score.
Prepared Context receives the grouped passages with chunk IDs, headings, original Source references, locations and manual-edit
markers in the text, so Artifact-level deduplication retains distinct matches from the same document. Its output byte budget still applies.

SQLite uses FTS5 and sqlite-vec cosine distance; SeekDB/OceanBase use native FULLTEXT and VECTOR storage/distance. Retrieval fuses
lexical/vector ranks, then verifies current active document/result/profile and refills candidates. It never compares raw per-library
BM25 scores. Results include Scope, exact document revision, chunk ID, original Source and location. Candidate work is bounded at
5,000 per channel; many retained historical results can reduce recall, so this is not a large-corpus performance guarantee.

The four plugin domain tables are `pc_knowledge_base_spaces`, `pc_knowledge_base_space_defaults`, `pc_knowledge_base_resources`
and `pc_knowledge_base_chunks`. Fulltext/vector projections are derived structures. Core owns Artifact revisions, lifecycle,
lineage-only edit evidence, Sources, cursors, leases and retries. Keep the core database, plugin database and snapshot directory
together when backing up an instance.

## Validation

```bash
uv run --locked --extra knowledge-base pytest tests/knowledge_base tests/e2e/test_knowledge_base.py
```

The default end-to-end test uses deterministic embeddings only in its temporary plugin copy; the installed plugin has no mock
embedding fallback. Set `POWERCONTEXT_TEST_KB_MODELS` to a prepared local data directory to also run real embedding acceptance.
Set `POWERCONTEXT_TEST_KB_SQL` to an isolated OceanBase/SeekDB configuration for native projection tests, and
`POWERCONTEXT_TEST_KB_SERVER_DATABASE` to an isolated core database configuration for the full Server/Worker workflow.

## Scope navigation and processing retries

`GET P/scopes/{s}/settings` requires Scope read permission and returns the knowledge base's owning space,
configuration and configuration version. Processing status responses and the document list's `requests` include
`relative_path`, so a file remains identifiable even when it has no published document revision.

The Dashboard's processing retry dialog uses the original snapshot saved for that request and a new idempotency key.
Manual edits are protected by default. Only explicit confirmation sends `manual_edits_policy:"replace"`, including
when uploading new file bytes conflicts with an existing manual edit. Historical revisions remain available.

For `POST P/scopes/{s}/retrieve`, an optional `scope_ids` must contain only the Scope in the path. An empty list or
another knowledge base returns 422 rather than silently ignoring the caller's selection.
