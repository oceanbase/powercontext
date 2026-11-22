---
title: "RFC 2570: Scope-based Knowledge Base document plugin"
description: "Plugin-local space Scopes organize knowledge-base Scopes for cross-base retrieval, a Dashboard, three database backends, synchronous editing and native soft deletion."
---

- Proposal Name: `knowledge_base_plugin`
- Start Date: 2026-11-10
- RFC PR: [#2570](https://github.com/oceanbase/powercontext/pull/2570)
- Tracking Issue: [#2440](https://github.com/oceanbase/powercontext/issues/2440)
- Related Issues: [#2441](https://github.com/oceanbase/powercontext/issues/2441), [#2442](https://github.com/oceanbase/powercontext/issues/2442), [#2443](https://github.com/oceanbase/powercontext/issues/2443), [#2444](https://github.com/oceanbase/powercontext/issues/2444)

# Summary

One `knowledge-base` business plugin provides local ingestion, parsing, chunking, versioning, and hybrid retrieval. Each
ordinary Scope is one knowledge base. The plugin declares only the `document` family, with one Artifact per complete file.
A plugin-local space Scope groups knowledge-base Scopes. Space identities, membership, and user defaults live only in plugin
storage: core stores neither the space object nor these parent-child relationships. A knowledge base is not an Artifact. The plugin supplies original
and processing-request Source definitions and reuses framework generate, Supervisor, and consumption cursors rather than
implementing an independent task scheduler.

The first phase supports SQLite, SeekDB, OceanBase, Markdown, standalone-image RapidOCR, rule-based chunking, synchronous
chunk edits/removals, document forget, a Dashboard, browser uploads, and cross-base retrieval within a space. Spaces organize
query scope; they do not introduce tenant isolation or team authorization. This is a target design dependent on framework contracts, not a claim that the
current implementation PR provides every capability.

# Motivation

Business descriptions and SOPs need searchable passages, original provenance, and manual changes that preserve history.
Scope provides the authorization and organizational boundary, Artifacts manage whole-document revisions, and plugin storage
holds bodies and indexes. Existing scheduling, revision preconditions, and lifecycle rules reduce domain-specific machinery.

# Guide-level explanation

1. Select a plugin-local space; if the UI has no selection, read the caller's default first and pin space_scope_id for creation.
   An administrator creates the knowledge-base Scope there and saves parsing/chunking configuration.
2. Upload files through the Dashboard or import a Server-readable directory; select multiple knowledge bases within the space for retrieval.
3. The plugin saves immutable snapshots, registers Sources, and submits processing-request Sources. Acceptance is not parsing success.
4. Framework generate asynchronously processes Markdown/images, prepares complete chunks and indexes, then publishes a document.
5. Changed content and explicit reprocessing retain the document ID. Failures preserve existing revisions. Status distinguishes
   pending, latest failure, published, and conflict/skipped.
6. Synchronous chunk edits/removals increment the document revision; core records a lineage_only success Source in the same transaction.
7. Whole-document soft deletion uses generic forget: deprecated, excluded from normal retrieval, with no new revision. Bodies,
   originals, and history remain stored.

The first phase does not promise in-flight cancellation, Source deletion, knowledge-base cascades, physical erasure, an
independent queue, online model switching, cross-space aggregation, generated answers, nested/movable/deletable spaces, team
membership or permission inheritance, continuous directory sync, or remote runners.
PDF/Office, scene-image understanding, OCR of embedded Markdown images, semantic/LLM chunking, and large-corpus benchmarks
are out of scope. Restore/merge methods are not implemented initially; generic routes report unsupported. Import is not restoration.

# Reference-level explanation

## 1. Plugin, Scope, and framework dependencies

Follow [#2549](https://github.com/oceanbase/powercontext/pull/2549) for single-family plugins, Source definitions, writers,
models, and storage, and [#2579](https://github.com/oceanbase/powercontext/pull/2579) for routes and Scope management.
The required framework contract is specified by [#2659](https://github.com/oceanbase/powercontext/pull/2659) and implemented by
[#2660](https://github.com/oceanbase/powercontext/pull/2660). Merge those dependencies before the knowledge-base implementation;
an older master is not sufficient. Framework storage provisioning, original edit-payload evidence, transaction-bound authorization,
governance concurrency checks, and generic Dashboard Artifact forwarding belong to those framework changes, not this plugin PR.
The plugin consumes public contracts and owns its tables/indexes, snapshots, parsing, retrieval, routes, and pages. Initialize through
setup without depending on a framework upgrade hook. [#2617](https://github.com/oceanbase/powercontext/pull/2617)
is a future collection integration, not a prerequisite for explicit local imports.

| Object | Model |
| --- | --- |
| Space Scope | Plugin-local logical Scope, space_scope_id; no core Scope record |
| Knowledge-base Scope | Ordinary core Scope, scope_id; belongs to exactly one space initially |
| Plugin | knowledge-base, one installed directory, one document family |
| Original | knowledge-base-file Source, pinned snapshot and original path |
| Processing request | knowledge-base-process Source, pinned original reference, configuration, base, and request identity |
| Document | document Artifact, bounded whole-document manifest |
| Chunk | Immutable result in plugin storage, not a separate Artifact |

```mermaid
flowchart TD
  P[Plugin-local space Scope] --> M[Plugin-only membership]
  M --> S[Core Scope: knowledge base]
  S --> C[Plugin configuration]
  S --> O[Original Source and immutable snapshot]
  O --> R[Processing request Source]
  R --> G[Framework generate and cursor]
  G --> I[Immutable chunks and indexes]
  I --> D[document Artifact revision]
  U[Synchronous chunk edit] --> D
  D --> Q[Retrieval and exact chunk reads]
```

Create through ctx.scopes.create with server.admin; a key derived from the same full creation identity returns the same Scope.
The framework's plugin external reference identifies creator ownership. Scope fields hold title/summary; plugin tables hold
business configuration. Retry configuration after a successful Scope creation if its separate write fails; reject imports
until configuration is ready. Reuse Scope read/contribute, Artifact write, and framework preconditions. Plugin routes must not
treat Scope contribution as permission to modify every document.

Plugin inputs subscribe only to processing requests, so originals do not independently trigger this plugin again. Deployment
reserves knowledge-base Scopes for this use and does not enable other content consumers. A Source type name is not framework
enforced exclusive admission; isolation from arbitrary other consumers is not a guarantee of this plugin.

### 1.1 Space Scopes and default selection

space_scope_id and core scope_id are separate namespaces. Never pass a space ID to core Scope APIs. The UI calls it a space;
Workspace may be a display label, not another Artifact family or core domain model. Initially spaces are one level deep and
one knowledge base has one parent. Store membership as indexed space_scope_id on the plugin's scope configuration row, never
as core parent_scope_id or context_references.

Defaults use the authenticated ctx.caller.principal_id. First default resolution idempotently creates a personal space and
its default mapping; concurrent callers resolve the same space. This creates no core Scope or contribution grant. Users may
select a visible space as their default. A space records a personal owner or team kind; team is an organizational label only.
Explicit space creation/renaming and knowledge-base creation/renaming require server.admin. Implicit personal-space registration
and changing one's own default do not require permission to create core Scopes.

Personal owners can see their spaces; a caller who can read a contained knowledge base can see its space; administrators can
manage empty spaces. Lists/counts include only readable knowledge bases. Membership grants no Scope read/contribute or Artifact
write, permission inheritance, tenant isolation, or cascade deletion. An inaccessible default causes implicit knowledge-base lists
and retrieval to fail closed. GET /me/default-space still returns the caller's own saved ID/version with available=false, and
GET /spaces independently lists accessible spaces. The caller explicitly selects a visible space and uses the saved version to
change the default; neither endpoint silently selects another space. A space without readable bases returns no hits, never a global
fallback. Moving knowledge bases or deleting spaces is not supported initially, avoiding implicit data migrations.

The knowledge-base creation POST requires an explicit nonempty space_scope_id; missing/empty values return 422. The server
never resolves the default during creation or retry. Dashboard/other callers first read the default or use an explicit selection,
then pin the space ID, request key, and creation parameters. Automatic retries preserve them even if the user's default changes.

The full creation identity is (ctx.caller.principal_id, space_scope_id, idempotency_key). Derive a key using versioned canonical
encoding and a digest of that tuple for ctx.scopes.create; core adds the plugin-name prefix itself. Never pass only the client's
raw request key or put space membership into core Scope fields. The same full identity returns the same Scope and must match
existing plugin membership. Different callers/spaces with the same raw key are different creations, never reuse or relocation.

After validating the space and permissions, idempotently create the core Scope, then commit unique membership/configuration
in plugin storage before reporting readiness. Retry incomplete registration using the pinned space and full creation identity;
a Scope missing membership/configuration cannot receive imports or enter space queries. Queries and knowledge-base lists may
still omit the space and resolve the current default; those read rules never change a creation's pinned target.

## 2. Local storage, identity, and request acceptance

OpenDAL fs manages snapshot directories accessible to the importer, Server, and Workers. Original paths are provenance;
parsing reads snapshots only. After checking canonical-root containment, open each directory component from the filesystem root
through directory descriptors with O_NOFOLLOW, including ancestors of the allowed root; open the final file relative to that bound
directory. Resolving then reopening an absolute root is insufficient because an ancestor can be replaced by a symlink between
validation and open. Save bytes and compute SHA256 before Source registration; mtime/size cannot replace the content hash.

```text
file_key    = (scope_id, collection_id, normalized_relative_path)
document_id = stable_hash(identity_version, file_key)
source_key  = stable_hash(source_schema_version, file_key, raw_content_digest)
request_key = (scope_id, idempotency_key)
result_id   = digest(canonical complete chunk result and provenance)
```

For Server directories, collection derives from the storage alias and normalized root. Browser uploads use a stable collection
within the knowledge base, never a new collection per batch. Browsers provide filenames/relative paths, not trusted client
absolute paths. Record origin_kind, collection, and relative_path; OpenDAL fs saves uploaded bytes in the shared Server-local
snapshot directory. Equal names from the two origin types are independent by default; the same collection/path retains identity.
Use bounded binary requests and request keys, normalize relative paths, reject traversal, and keep file bodies out of JSON Sources.
Compare full identities on hash matches. Files in different knowledge-base Scopes remain independent; space IDs are not part of
file_key. Same path/bytes reuse the original Source; changed bytes create a new Source
but retain the document ID. Renames are new files; disappearance does not automatically forget a document.

Each processing request pins original_source_ref, snapshot_ref, processing settings, independent model fingerprint,
base_revision, manual_edits_policy, and request digest. Existing documents pin the accepted revision; first publication has an
explicit no-head precondition. Ordinary duplicate imports reuse available matching content/configuration. Explicit reprocessing
and user retries use new request keys, producing new request Sources even for unchanged originals. Ordinary imports preserve
manual edits; discarding them requires explicit discard authorization.

First persist bounded immutable acceptance parameters in a plugin resource row, then idempotently register the original and
request Sources, and finally record their actual SourceRefs. Framework generate can start before the plugin acknowledgement
fields are saved: execution uses the committed request Source and pinned acceptance parameters, not another ready flag.
Return accepted only after the request Source is durably confirmed. After a lost response, replay the same key/parameters to
complete registration; a key reused with different parameters conflicts. This register is not a queue: requests without a
successfully registered Source have no background-execution promise and callers can retry. Unreferenced snapshots are not searchable.

Directory ingestion uses bounded file batches through this same acceptance path and returns per-item receipts. Enumeration
completion does not mean parsing completion. No independent directory-scan job is persisted initially; clients submit large
directories in batches, and repeated enumeration deduplicates by file identities and request keys.

## 3. Parsing, chunking, and original provenance

Markdown uses markdown-it-py for headings, blocks, and source mappings. Standalone images use RapidOCR with ONNX Runtime,
retaining boxes, order, confidence, and coordinate transforms; normalized Markdown enters the same chunker. Do not infer
complex tables or flowcharts. Embedded Markdown images remain references.

Split by heading/content blocks, recursively splitting oversized blocks at paragraphs/sentences. Preserve code, tables, and
SOP steps where possible; split oversized tables with repeated headers. Overlap applies to split long paragraphs. Configure
max_chunk_chars/overlap_chars; 1200/100 is an initial test value. Validate the complete embedding input, including heading text,
against model token limits without silent truncation. Report invalid UTF-8, image dimensions/decoding, and OCR with no text.

Parsers return Markdown, locators, and warnings, not cursor or Artifact changes. New formats use an internal parser registry,
not additional plugin types.

A request records the original SourceRef and snapshot. Generate directly references only request Sources in its current window;
a previously consumed original may be outside that window and cannot bypass writer restrictions. The document manifest retains
original provenance, providing a verifiable document → request Source → original Source chain without claiming that the original
is always direct lineage. Synchronous edits preserve current lineage and original locators and add core's success Source.

## 4. Plugin storage and models

ctx.storage follows #2660: a dedicated SQLite file or the SeekDB/OceanBase database powercontext_plugin_knowledge_base, precreated by the deployer
when database-creation permission is unavailable. The plugin selects migrations and full-text/vector implementations by backend,
without changing existing core schemas. Originals stay in local OpenDAL directories rather than SQL body columns.

| Plugin domain table | Fields and constraints |
| --- | --- |
| pc_knowledge_base_spaces | Primary key space_scope_id; title, kind, personal_owner_id, created_by, creation_key, request_digest, version; unique personal owner and creation key |
| pc_knowledge_base_space_defaults | Primary key principal_id; space_scope_id, version; one default mapping per authenticated user |
| pc_knowledge_base_resources | Primary key (scope_id, resource_kind, resource_key); fixed scope/document/request kinds; one scope configuration row with indexed space_scope_id; bounded payload, request_digest, source_ref, document_id, latest observations and timestamps |
| pc_knowledge_base_chunks | Primary key (scope_id, document_id, result_id, chunk_id); ordinal unique within a result; text, text_length, content_digest, heading_path, source_locator, origin, edit_metadata |

There are four plugin domain tables; spaces/defaults do not invent core scope_id values. Validate space references in plugin
transactions and use version preconditions for default updates. Register the deterministic personal space before its default
mapping; unique keys and idempotent retries repair interruption between these writes without replacing an existing default. Explicit space-creation keys are scoped to their creator; changed parameters conflict. Reuse the scope
resource row for membership without an additional relationship table or core schema changes.

Resources is a fixed-purpose register, not a generic task framework: scope rows hold configuration/version, document rows stable
file identities, and request rows immutable accepted inputs, receipts, and latest attempt observations. No leases, execution
tokens, claiming queue, or cleanup cursors; do not copy the Artifact publication pointer or lifecycle. Request results may refer
to exact revisions for reconciliation, but core determines success. Full-text/vector auxiliary structures are derived indexes,
not additional domain tables.

Document content holds only title, original SourceRef, file identity, result_id, chunk count/digest, configuration fingerprints,
manual-edit markers, and applied_request_id. Neither bodies nor unbounded chunk-ID lists belong in the manifest. Result IDs
derive from canonical complete results, never from a predicted uncommitted revision number.

The plugin independently configures and creates model clients. Deployment supplies credentials; Sources/Artifacts do not store
them. Do not automatically follow ctx.models or Server model changes. Initially one fixed embedding profile serves the plugin,
including model identity/version, dimensions, complete input rules, and normalization version. Equal dimensions do not establish
model compatibility. If existing indexes disagree with deployment configuration, reject vector queries and new publication and
require controlled migration; do not silently replace configuration or mix spaces. Online model switching is not included.
Same-profile index repair reads committed chunks. Parsing/chunking configuration may vary by Scope and is pinned in each request.

The framework SQL namespace is plugin-name based, not core-database based. Independent deployments sharing a SQL tenant must
isolate their plugin databases/accounts; changing only the core database does not isolate this plugin's tables.

## 5. Framework consumption, status, and failure handling

Declare on_sources and processing-request inputs; reuse framework generate/Supervisor, Scope bindings, consumption cursors,
lease fencing, and failure backoff. No independent jobs table or plugin Worker scheduler. Each invocation opens at most one
writer.transaction; core atomically commits Artifacts, the cursor, and invocation acknowledgement. Use a small max_sources and
bounded file sizes to keep OCR/model windows within limits; arbitrary files are not guaranteed to fit framework timeouts.

| Event | Behavior |
| --- | --- |
| Request submitted without a result | Report pending, without invented percentages |
| Deterministic input error such as invalid format or no OCR text | Persist a bounded failure observation and skip that input; normal window acknowledgement permits subsequent requests |
| Database, model service, or durable index failure | Raise; core rolls back and the cursor does not advance; framework retries with backoff |
| Pinned base changed or document no longer active | Record conflict/skipped, never automatically replace the base; caller submits a new request |
| Core publication completed | Derive published state and exact revision from manifest/history applied_request_id |
| Core succeeded but plugin status acknowledgement failed | Reconcile core, not failure or duplicate publication inferred from a missing status |

Status reads first reconcile the pinned base+1 revision and current manifest with applied_request_id. If no publication belongs to
the request and the current revision has advanced past its pinned base or the document is inactive, pending/processing resolves to
failed with a conflict reason. This also repairs status when the cursor committed but the later conflict observation failed; it
requires no redelivery, new table, or automatic rebasing. Restart and same-key receipt replay preserve that outcome.

Display the currently usable revision separately from the latest request status: failed reprocessing does not make an older
committed revision unusable. Failure observations describe the latest attempt, not proof of cursor acknowledgement. Plugin status cannot advance consumption
or justify skipping unproven core writes. Persistent infrastructure failure may block subsequent windows in the Scope; independent
unlimited per-file concurrency and a dead-letter queue are not promised. User retries of deterministic failures use new requests;
old Sources remain unchanged. Processing requests express intent, not lineage_only success.

Require no head for first publication. Updates validate the pinned base, original, manual-edit policy, and lifecycle. Framework
retries cannot rewrite the request's base or discard policy. If an edit/forget occurs after preparation, publication uses framework
revision/governance concurrency checks and must not reactivate deprecated documents. The framework tracks governance observed by
writer reads and checks it during publication; the plugin passes expected_revision to put and does not invent an
expected_state_version writer argument or a local lock. Lifecycle-race guarantees are an acceptance requirement for #2660.

## 6. Publication and index consistency

1. Parse and calculate vectors outside the core transaction; durably store immutable chunks and their full-text/vector indexes,
   validating counts, digests, and readability.
2. Validate preconditions and put a manifest referencing result_id and index fingerprints in writer.transaction. Generate commits
   its cursor with the revision; user edits commit success evidence with the revision.
3. A failed core commit leaves prepared results invisible and the previous manifest/index usable. Retries do not overwrite old results.
4. Retrieval candidates must match the active current document's result_id/profile. Refill after filtering; unpublished results
   must not consume the effective top-k.

Isolate indexes by result rather than overwriting current candidates under document_id alone. Model/file computation stays outside
short transactions. No additional core Scope-row gate or distributed transaction protocol is introduced. Exact historical chunks
resolve through their revision's manifest, independently of current search indexes. Unreferenced prepared data does not change
visible results; permanent cleanup is a separate contract.

## 7. Synchronous chunk editing and soft deletion

Generic Artifact PUT invokes update_artifact synchronously for chunk edits/removals; these are not plugin-route writes or queued
commands. The plugin editing payload is a bounded chunk-change description containing a chunk ID/new text or removal action.
The service reads the base revision and composes the complete result. Only the service creates manifests; reject caller-supplied
arbitrary result IDs or original ownership. Core permits plugin normalization of submitted content; arbitrary manifest replacement
is not exposed.

Require If-Match, prepare the new result/indexes, then perform exactly one put(expected_revision=...). Core stores the submitted
edit payload as a lineage_only Content Source in the same transaction and increments the document revision. Edits retain logical
chunk identity and original locators, marking non-verbatim manual text. Removing the final chunk permits a zero-chunk revision.
Edit evidence can contain the new text; existing permissions apply, and forget does not erase that evidence.

Model calls obey synchronous time and edit-size limits; failures publish no partial result. After an uncertain response, read
current revision/lineage before retrying. An old If-Match cannot be reused blindly. Generic PUT does not add an idempotency-key
or exactly-once HTTP guarantee.

Whole-document soft deletion uses generic forget, invoking forget_artifact with exactly one set_lifecycle(deprecated). This is
only a lifecycle transition: no new content revision, no plugin-specific deletion evidence, and no physical removal of bodies,
indexes, or snapshots. Normal retrieval and Prepared Context exclude deprecated documents. Generic get/history/evidence retain
framework visibility; authorized exact chunk reads and rendering can show retained versions. Do not promise all content becomes
unreadable. Core owns lifecycle; no duplicate document tombstone in plugin storage. Index cleanup is not a prerequisite for forget.

Reimport, reprocessing, and late work never automatically restore forgotten documents; synchronous edits require active documents.
Before a document exists there is no forget target. In-flight import cancellation is not supported; do not create empty Artifacts
or deletion revisions. No Source deletion or knowledge-base cascade API; physical Scope deletion is not soft deletion. Repeated
forget follows framework preconditions/no-op behavior without scheduling cleanup work.

## 8. APIs, retrieval, and authorization

Let `P=/v1/plugins/knowledge-base/api` and `D=/v1/scopes/{s}/artifacts/document/{d}`. Declare plugin routes through PluginApi/Route
using supported response facilities. Accepted below is a business state, not an assumption that custom HTTP 202 is available;
default successful JSON responses may use 200.

| Operation | API and response |
| --- | --- |
| List/create/rename spaces | GET/POST `P/spaces`, PATCH `P/spaces/{space_scope_id}`; plugin-local spaces, writes require idempotency keys or version preconditions |
| My default space | GET/PUT `P/me/default-space`; current caller's space_scope_id and version; GET adds available; no caller-supplied principal_id for other users |
| List knowledge bases | GET `P/knowledge-bases`; optional space_scope_id, otherwise use the current default |
| Create knowledge base | POST `P/knowledge-bases`; required space_scope_id and idempotency_key; idempotency uses caller + space + request key, returning both IDs |
| Rename/configure knowledge base | PATCH `P/scopes/{s}/settings`; ctx.scopes.update for title/summary, plugin storage for chunking, separate version preconditions |
| Import files | POST `P/scopes/{s}/imports`, bounded paths and idempotency_key; per-file request SourceRefs |
| Browser upload | POST `P/scopes/{s}/uploads`, binary body; collection, relative_path, idempotency_key in query; same acceptance path |
| Document list | GET `P/scopes/{s}/documents`, paginated published summaries and unpublished request observations; core determines revision/lifecycle |
| Reprocess/retry | POST `P/scopes/{s}/documents/{d}/reprocess`, base, pinned configuration, original reference, new request key |
| Processing status | GET `P/scopes/{s}/imports/{request_id}`, acceptance, latest attempt, and verifiable publication |
| Document overview/history | Generic Artifact GET/revisions, bounded manifest |
| Edit/remove chunk | PUT `D`, If-Match, synchronous new document revision |
| Soft-delete document | POST `D/forget`, revision and state_version; framework response |
| Chunk directory/body | GET `P/scopes/{s}/documents/{d}/revisions/{rev}/chunks`; append `/{chunk_id}` for body |
| Batch chunks | POST directory path plus `/batch-get`, bounded chunk_ids |
| Chunk hybrid retrieval | POST `P/scopes/{s}/retrieve`, exact revision, chunk ID, score, provenance |
| Cross-base retrieval within a space | POST `P/retrieve`, optional space_scope_id/scope_ids; resolve as below, hits carry space and knowledge-base IDs |
| Rendering/generic search | render_artifact/search_artifacts; generic search and Prepared Context integration |

Examples for knowledge-base creation, import, editing, forget, and retrieval:

```json
{"space_scope_id":"space-team","title":"Operations SOPs","summary":"Runbooks","idempotency_key":"kb-01","chunker":{"max_chunk_chars":1200,"overlap_chars":100}}
```

```json
{"root":"/work/docs","paths":["restart.md"],"idempotency_key":"import-01"}
```

```json
{"scope_id":"scope-ops","items":[{"document_id":"doc-1","request_ref":{"source_type":"knowledge-base-process","source_id":"request-01"},"status":"accepted"}]}
```

```json
{"content":{"operation":"edit_chunk","chunk_id":"chunk-1","text":"Check replica health before restarting."}}
```

```json
{"content":{"operation":"remove_chunk","chunk_id":"chunk-2"}}
```

```json
{"revision":3,"state_version":0}
```

```json
{"query":"How should I restart a follower?","mode":"hybrid","limit":10}
```

```json
{"hits":[{"space_scope_id":"space-team","scope_id":"scope-ops","document_ref":{"family":"document","artifact_id":"doc-1","revision":3},"chunk_id":"chunk-1","score":0.82,"source_ref":{"source_type":"knowledge-base-file","source_id":"original-1"},"locator":{"heading_path":["Operations","Restart"]}}]}
```

Chunk APIs read the requested exact revision without current-version fallback. Directory pagination omits bodies; single/batch
reads return bounded bodies and provenance. Reject duplicate/missing IDs and excessive responses without silent truncation.
Check Scope/document permissions and ownership first; plugin storage spans Scopes, so chunk IDs alone never authorize reads.

Generic search retains the framework's Artifact-reference response; plugin retrieve returns chunk structures. Prepared Context
uses SearchHit text/locators within its byte budget. Normal search excludes non-active, stale, and unauthorized candidates before
refilling. Unsupported filter/rerank parameters fail explicitly. Python/HTTP share business semantics. Generic search/Prepared
Context retain their Scope contract; plugin retrieve provides space aggregation.

### 8.1 Space selection and cross-base retrieval

1. Omitted space_scope_id resolves the caller's default; an explicit value must be visible. Every explicit scope_id must belong
   to that space; selecting bases never implicitly switches the resolved space.
2. Omitted scope_ids selects readable bases in that space; an explicit empty list selects none. An explicitly inaccessible or
   out-of-space base fails the whole request rather than being silently omitted. Bound base count, request size, and total hits;
   require complete defaults/membership/configuration and never broaden to global search.
3. Retrieve full-text/vector candidates in the selected range and fuse them consistently. Reuse the query vector for the fixed
   embedding profile. Use comparable scoring/common candidate ranking, not raw BM25 scores from separate per-base corpora.
   Constrain candidates by Scope, then verify core access, active lifecycle, current result_id/profile, and refill after filtering.
4. Hits carry space_scope_id, scope_id, document_ref including revision, chunk_id, score, and provenance. Deduplicate with full
   ownership/result identity; importing the same file into different bases does not merge its evidence. Exact chunk reads still
   resolve by knowledge-base Scope and revision.

```json
{"space_scope_id":"space-team","scope_ids":["scope-ops","scope-dev"],"query":"How should I restart after a release?","mode":"hybrid","limit":10}
```

A query alone searches readable bases in the default space; space_scope_id alone selects readable bases in that space.
Aggregation reuses single-base authorization, lifecycle, and revision rules without nested HTTP requests. Backend failures are
explicit; partial success and a consistent snapshot across bases are not first-phase promises.

## 9. Dashboard

PluginPages provides space/default selection, knowledge-base management, Markdown/standalone-image drag-and-drop upload and
local-directory import, document/request status, retry/reprocess, paginated chunk metadata/bodies, history viewing, synchronous
edit/removal, document forget, and multi-base retrieval within a space. Filter lists by permissions and retain operation-specific
access checks: creation/configuration management requires server.admin, imports require scope.contribute, and edit/forget retain
artifact.write. Pin the selected/default space before submitting creation; retries preserve space_scope_id and the request key.
Changing the creation target is a new user operation. Resolve valid chunking configuration before initial import; no separate
post-upload pending-configuration queue. Capture the space, knowledge-base Scope, document/revision, and view generation for
asynchronous reads; discard stale results and errors after navigation. Status requests must retain their originating Scope, and
old lists/chunks must not replace the newly selected view. An unavailable default leaves the selector usable for explicit recovery.

Display the usable revision separately from the latest request. A failure does not hide previously committed content. Successful
edits show the new revision; conflicts require re-reading, and uncertain responses require reconciliation. Removing the final
chunk is valid. Label forget as removal from retrieval with history retained; omit cancellation, restoration, whole-base deletion,
and generated-answer controls.

The sandboxed iframe calls declared plugin routes through the framework client; reads can wrap ctx.reads. Generic document
GET/PUT/forget uses #2660 `client.artifacts(method, path, {scopeId, ifMatch, json})` and the host's
`/dashboard/plugins/{name}/artifacts/{scope_id}/{artifact_id}` route. PluginPages does not declare artifact_actions, and the plugin
must not add an /api/_artifacts proxy. The framework restricts operations to the owning family and retains caller authorization,
If-Match/revision/state_version; plugin hooks retain knowledge-base ownership checks. Do not provide
arbitrary URL forwarding or session credentials. Invoke the generic Artifact operation and its atomic evidence path; an ordinary
plugin writer or asynchronous edit Source is not a substitute. Upload files separately within binary request limits, enforce
server-side path/size bounds, and do not present uploaded-file counts as completed parsing progress.

## 10. Initialization, implementation, and acceptance

setup invokes plugin-owned idempotent initialization and schema-version checks. Deployment runs plugin migrations with
coordination in plugin storage across instances. Preserve historical manifest/Source decoding and reject incompatible versions.
The first phase restarts for deployment and does not depend on an upgrade(ctx) hook or hot reload.
Backups must include core, plugin storage, and OpenDAL snapshots; core backups do not automatically cover plugin data.

#2659/#2660 supply framework storage, synchronous edit evidence, transaction-bound authorization, governance checks, and the
Dashboard Artifact bridge. The knowledge-base PR contains no core runtime, core schema, framework SDK, host, writer, or Dashboard
bridge changes. Repository integration is limited to plugin files, optional dependencies, plugin tests/CI, and usage documentation.
Do not introduce knowledge-base lock tables, read guards, or an independent queue. Verify against the merged framework contracts;
until those dependencies merge, report integration results against the tested framework commit rather than master compatibility.

Validate on SQLite, SeekDB, and OceanBase:

- Concurrent plugin-local space/default initialization, default-update conflicts, revoked-default recovery without implicit fallback, unique membership, no core space hierarchy.
- Allowed-root and ancestor symlink replacement after validation; delayed Dashboard list/status responses after switching bases.
- Conflict observation failure after cursor commit, including restart and replay; status must not remain processing forever.
- Default/explicit space queries, empty selections, inaccessible/out-of-space rejection, filtered counts, global top-k, scoped citations.
- Sandboxed Dashboard generic-operation forwarding, preserved If-Match, zero-chunk edits, separate statuses and usable old revisions.
- Administrator-only Scope creation, membership/configuration retry, multi-Scope isolation, snapshots, stable identities/path bounds for both origins.
- Missing creation space returns 422; after core success/plugin failure, changing the default does not redirect the retry; raw keys cannot collide across callers/spaces.
- Retries across import acceptance boundaries, key conflicts, content deduplication, repeated handling, and deterministic failures
  that do not block later files.
- Window rollback/redelivery, failure observations versus committed core success, and pinned-base conflicts without automatic rebasing.
- Markdown, real RapidOCR, oversized blocks, complete embedding-input limits, provenance, and manual-text markers.
- Synchronous edit/removal revisions and lineage_only evidence, zero chunks, model failures, and lost responses.
- Edit/forget racing generate without overwriting edits or restoring deprecated documents; retained originals/history.
- Core rollback after index preparation while old retrieval remains usable, candidate filtering, exact reads, generic search, and Prepared Context.
- Explicit model-fingerprint mismatch failures; Server model changes do not alter the plugin vector space.

Use Markdown from a pinned oceanbase/oceanbase commit and real text images. Mocks cannot establish real OCR or backend support.
#2441 covers spaces/defaults/ingestion/configuration; #2442 framework consumption/parsing/publication; #2443 cross-base retrieval,
editing, and Dashboard; #2444 operation/acceptance; #2440 tracks the whole feature. Implementation may use one PR with staged commits and validation. No new issues.

# Drawbacks

Persistent infrastructure failures can block a Scope window; synchronous editing has request time limits. The plugin owns
three-backend indexes, migrations, and backups. Immutable results/history consume storage. Forget removes normal retrieval,
not history access or stored evidence; Sources remain immutable and readable.

# Rationale and alternatives

- Plugin-local space Scopes organize knowledge-base Scopes; membership/defaults stay in plugin tables without core hierarchy or implied grants.
- A core Scope is a knowledge base, matching a single-family plugin without another Artifact or cross-family writes.
- Framework generate/cursor atomically commits Artifacts and consumption progress; the plugin records acceptance/observations
  rather than duplicating scheduling.
- Generic synchronous PUT supplies preconditions and success evidence; plugin-route writes do not automatically have those semantics.
- Original and request Sources separate immutable facts from reprocessing; direct lineage and original provenance have explicit roles.
- Immutable chunk references preserve history and failure recovery; in-place body replacement cannot implement versioned editing.
- Forget follows framework lifecycle without Source tombstones, deletion revisions, or global read interception.

# Prior art

Builds on [plugin interfaces #2549](https://github.com/oceanbase/powercontext/pull/2549),
[plugin APIs #2579](https://github.com/oceanbase/powercontext/pull/2579),
[Source identity](1400_source_definition_and_observation_model.md),
[processing supervision](1515_artifact_processing_supervisor.md), and [Source/Artifact APIs](1437_source_artifact_rest_api.md).
Future collection can integrate [#2617](https://github.com/oceanbase/powercontext/pull/2617). Collection checkpoints describe
acceptance, not completed parsing; deleting a collection target does not delete its Sources or documents.

# Unresolved questions

Implementation fixes SQL/index details, OCR model versions, window/file sizes, synchronous editing limits, and API byte bounds.
Validate revision/lifecycle races on real backends. Check gaps between framework RFCs and implementations, including dedicated
storage, edit evidence, and Dashboard forwarding under #2659/#2660; proposals are not released capabilities. These choices do not change append-only Sources, forget without
new revisions, synchronous edits, pinned request bases, or independent models.

# Future possibilities

Word/PDF parsing, continuous collection, cross-space queries, generated answers, rerank, adjacent context, in-flight cancellation, and controlled model
migration. Complete Source deletion, revoking historical access, knowledge-base cascades, and independent long tasks require
separate contracts rather than implicit first-phase promises.
