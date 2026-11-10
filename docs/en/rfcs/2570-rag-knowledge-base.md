---
title: "RFC 2570: Document knowledge bases with rag Artifacts"
description: "A proposal for plugin-based knowledge bases with immutable document Sources, versioned chunks, hybrid retrieval, and recoverable processing."
---

- Proposal Name: `rag_knowledge_base`
- Start Date: 2026-11-10
- RFC PR: [#2570](https://github.com/oceanbase/powercontext/pull/2570)
- Tracking Issue: [#2440](https://github.com/oceanbase/powercontext/issues/2440)
- Related Issues: [#2441](https://github.com/oceanbase/powercontext/issues/2441), [#2442](https://github.com/oceanbase/powercontext/issues/2442), [#2443](https://github.com/oceanbase/powercontext/issues/2443), [#2444](https://github.com/oceanbase/powercontext/issues/2444)

# Summary

Add document knowledge bases through one document Source plugin and two Artifact families: `document` and `rag`.
A Scope can contain multiple user-created `rag` Artifacts. Users import files into a selected `rag`; background work
parses immutable input, produces a whole-document Artifact with separately stored chunks, associates it with that
knowledge base, and publishes full-text/vector indexes. This proposal defines design intent, not released APIs.

# Motivation

Business descriptions and task SOPs need passage retrieval with exact evidence, explicit updates, and deletion.
Extracted memories complement this use case but do not preserve all document content. A knowledge base must keep
original input, processed content, and searchable versions distinct so that failed processing cannot corrupt an
existing result and manual corrections do not masquerade as original evidence.

Reuse PowerContext's Source definitions, Artifact management, plugin registration, Scope authorization, processing
supervision, and persistence. Add the document-specific contracts they do not yet provide instead of introducing
a parallel knowledge-base object system or a general workflow engine.

# Guide-level explanation

A Scope remains a project or workspace and the initial authorization boundary. A knowledge base is a `rag` Artifact
within that Scope, with its own name and processing/retrieval configuration. Renaming or deleting it does not rename
or delete the Scope. Knowledge bases in the same Scope are not separate confidentiality boundaries.

For example, a team creates `Product docs` and `Operations SOPs` in one Scope:

1. Create both empty `rag` Artifacts through Artifact management; no document input is required.
2. Import Markdown into `Product docs` and a standalone text image into `Operations SOPs`.
3. Receive durable import receipts and inspect parsing, indexing, and failure status.
4. Search either knowledge base, or explicitly select both. Documents appear automatically after processing and
   become searchable after index publication; users do not manually attach generated documents.
5. Replace an original file or edit an existing chunk. A new document revision becomes searchable only after its
   indexes are ready. A failed update leaves the previous published revision available unless revoked or removed.

The initial release supports Markdown and independently imported images using RapidOCR with ONNX Runtime.
Markdown image references are preserved without fetching or OCR-processing their targets. SQLite, SeekDB, and
OceanBase must each support the complete functional workflow; one deployment uses one selected backend.

Plain TXT, PDF, Office, automatic synchronization, production reranking, agentic retrieval, a complete management
UI, and a tens-of-thousands-of-documents benchmark are not initial acceptance requirements. Task status and recovery
APIs must nevertheless support a future UI. Applications remain responsible for answering questions from results.

# Reference-level explanation

## 1. Identity, ownership, and provenance

```text
Scope
  +-- rag K1 -- membership --> document D1 -- revisions --> chunks
  +-- rag K2 -- membership --> document D2 -- revisions --> chunks
                                 ^
immutable document Source -------+  content provenance
```

| Object | Identity and responsibility |
| --- | --- |
| `rag` Artifact | User-created configuration and collection identity; members and processing progress do not create configuration revisions |
| Document Source | Immutable original input descriptor: original path, managed snapshot reference, digest, media type, and captured metadata |
| `document` Artifact | One whole logical document, with a stable ID and increasing content revisions; initially belongs to exactly one `rag` |
| Chunk | Internal content and retrieval unit of a document revision, not a Source or independent Artifact |
| Index generation | A `rag`-scoped index configuration/build, separate from content revisions and plugin registration generations |

Logical document identity is `(scope, rag, source_namespace, external_key)`. Reimporting that identity updates the
same document; importing the same path into another `rag` creates a separate document. Content digests do not merge
logical identities. An immutable Source may be explicitly reused by multiple knowledge bases in the same Scope;
this does not create cross-Scope Source identity or shared document ownership.

Source lineage, `rag` membership, and the searchable revision selection are separate relationships. Tags cannot
substitute for ownership. Original paths aid provenance; reads use the captured immutable snapshot rather than
whatever currently exists at that path. Raw Markdown bytes and original images live in an OpenDAL-managed local
directory, using the existing OpenDAL/`opendalfs` integration where suitable.

Define a dedicated document Source type, provisionally `knowledge-document-input`. Only the document pipeline may
directly consume it. Enforce this at input selection, execution, and commit validation, not just through a naming
convention. Do not automatically expose it through generic evidence feeds consumed by other Artifact families.
Parsing output, OCR text, chunks, index pointers, and progress never mutate the Source payload.

## 2. Plugin responsibilities and framework integration

| Logical plugin | Owned behavior |
| --- | --- |
| Document Source | File validation, immutable snapshots, Source acceptance, and authorized original-content reads |
| `document` Artifact | Parsing, OCR, chunking, content revisions, chunk reads, manual edits, and evidence mappings |
| `rag` Artifact | Configuration, membership, index generations, publication, hybrid retrieval, and selected multi-base queries |

These are three logical plugins, not three required processes or distributions. Parser/OCR/chunker implementations
are replaceable components within `document`; index and retrieval adapters belong to `rag`, using shared database
and model facilities. Defaults may be referenced by `rag`, while component schemas are validated by their owner.

Reuse Source and Artifact plugin registration and existing management/read conventions. The current generation
model must be extended for explicit `rag` creation, domain detail storage, and target-specific work: an empty `rag`
is a managed object, not the output of Source consumption. Do not create a fake input Source to schedule its creation
or claim the generic plugin manifest alone supplies these capabilities. Existing system-generated management
provenance may still be recorded without triggering document processing.

A knowledge-base application service coordinates the use case through registered family capabilities. It is not
a fourth domain plugin. Each writer owns its records and participates in the caller's shared database transaction;
plugins do not write each other's private tables or commit independently. This uses one deployment database, not
a distributed transaction between plugin services. The document parser cannot choose the target `rag`.

## 3. Parsing and chunk storage

Markdown processing uses `markdown-it-py`, retaining headings, paragraphs, lists, code, tables, and links.
Map parser locations back to original UTF-8 bytes; do not fabricate inline precision from block-level line ranges.
RapidOCR with ONNX Runtime processes standalone images with pinned model identities, image-size limits, and
coordinate mapping back to the original image after preprocessing. Preserve actual OCR text, confidence, and
regions. Distinguish unsupported input, invalid encoding, damaged images, no recognized text, missing models,
and timeouts. Do not use generation models to fill in or rewrite OCR output.

Both paths produce validated blocks, sections, and source locators for a configurable heading/paragraph/length
chunker. Preserve ordering, overlap/continuation, neighboring chunks, and potentially multiple source spans or
image regions. A locator describes actual evidence; OCR confidence is an engine score, not a promised accuracy.

| Storage | Proposed representation |
| --- | --- |
| Existing Artifact records and heads | One bounded manifest per document revision: title/summary, original Source reference, configuration identifiers, schema version, chunk count, and collection digest |
| Document detail records | One row per chunk keyed by `(scope, document_id, revision, chunk_id)`, plus section/block structure and evidence/edit metadata |
| Membership and target state | `rag` ownership, logical document key, desired input/configuration, and lifecycle/concurrency generations |
| Retrieval projections | Chunk-level full-text/vector entries and exact revision/profile/generation mappings |
| Processing records | Durable target requests, stage/results, retries, leases, and recoverable indexing work and deletion receipts |

Reuse Artifact, head, lineage, tag, and processing facilities where their semantics match. Do not add duplicate
knowledge-base/document/revision master tables or mix document rows into family-specific Topic Memory chunk tables.
The proposed schema below fixes ownership, keys, and minimum fields. Backend-specific DDL and auxiliary indexes
are finalized through migrations, not runtime table creation.

Each revision initially stores a **complete chunk snapshot**. A 100-chunk document has one manifest and 100 detail
rows; editing one chunk creates another manifest and a complete 100-row snapshot. Validate counts, unique ordering,
structure references, and a digest covering effective text, order, and evidence mappings. Batched staging remains
invisible until the complete revision is committed. This deliberately accepts storage amplification for simpler
revision reads and recovery; shared chunk bodies across revisions are deferred.

The main Artifact content must not contain an unbounded full-text/chunk/vector array. Search accesses chunk indexes
directly, then batch-loads exact hits and same-revision neighbors. Generic Artifact reads return the manifest;
detail pagination fixes revision, view, and ordering in the cursor. Distinguish original parsed evidence from
effective edited chunks. Overlapping chunks cannot simply be concatenated into an edited full-document export.

### 3.1. Existing tables to reuse

| Existing table / facility | Use and required adaptation |
| --- | --- |
| `pc_sources`, `pc_source_journal_heads` | Keep canonical payloads and journal order immutable; reference original snapshots, not derived chunks |
| `pc_artifacts`, `pc_artifact_heads` | Store rag configuration and document manifests/revisions; keep family in every Artifact key; head is not a retrieval-publication pointer |
| `pc_artifact_lineage_sources`, `pc_artifact_source_locators` | Preserve root input and edit Sources; add/verify reverse lookup on `(scope_id, source_type, source_id)` |
| `pc_artifact_lineage_artifacts` and tags | Exact base-revision evidence and tagging, not rag membership |
| `pc_artifact_processing_intents`, leases, binding states | Reuse wakeup, ownership, and registration fences; store per-document work separately |
| `pc_scope_settings` | Reserve a system-owned Source-visibility epoch, advanced atomically on Source deletion; not user-editable |

Do not reuse `pc_artifact_publications` for searchable revisions: it represents cross-Scope publication. Generic
search state is keyed by Scope/family and cannot select independent generations for two rags without extension.
Reuse vector-space definitions and adapter code where applicable, but keep rag publication explicit. The generic
head's `searchable_text` is not the passage index.

### 3.2. Proposed relational schema

These are proposed names, not installed schema. The baseline has seven domain tables, one shared Source lifecycle
table, and one chunk-index projection. It adds no duplicate Source/Artifact/revision master, separate edit-audit
table, or separate request-receipt table. Equivalent existing facilities may be consolidated only if these keys,
atomicity, and reads remain intact. Backend search structures may add physical tables.

Every key/join carries `scope_id`. IDs use byte-exact comparison, revisions are positive integers, counters are
nonnegative big integers, and timestamps use UTC. `PK` means primary key; `UQ` means unique key. Common timestamps
are omitted. JSON contains bounded metadata/configuration, never a whole-document chunk array.

| Proposed table / owner | Keys | Minimum fields and purpose |
| --- | --- | --- |
| `pc_rag_runtime` / rag | PK `(scope_id, rag_id)` | `state` (`active/disabled/deleted`), `version`, `effective_config_revision`, `active_generation_id`, `building_generation_id`, `change_seq`, `visibility_epoch`, `deleted_at`; operational state separate from content |
| `pc_rag_documents` / rag | PK `(scope_id, document_id)`; UQ `(scope_id, rag_id, namespace, external_key_hash)` | `rag_id`, full `external_key`, `state` (`active/deleted`), `target_source_type/id`, `target_content_generation`, `target_config_revision`, `last_change_seq`, `version`, `deleted_at`; identity reservation and membership |
| `pc_document_nodes` / document | PK `(scope_id, document_id, revision, node_id)` | `kind` (`section/block`), `parent_node_id`, `ordinal`, `title/text`, `source_locators`, `parser_metadata`; immutable evidence and hierarchy |
| `pc_document_chunks` / document | PK `(scope_id, document_id, revision, chunk_id)`; UQ `(scope_id, document_id, revision, ordinal)` | `section_id`, `text`, `content_digest`, `source_locators`, `origin`, `base_revision`, `edit_source_type/id`, `metadata`; complete effective snapshot |
| `pc_rag_index_generations` / rag | PK `(scope_id, rag_id, generation_id)` | `state` (`building/ready/active/retired/failed`), `config_revision`, immutable `profile_snapshot`, `start_seq`, `applied_seq`, `cutover_seq`, `build_fence`, `error`; independent profile builds |
| `pc_rag_document_publications` / rag | PK `(scope_id, rag_id, generation_id, document_id)` | `document_revision`, `target_content_generation`, `chunk_count`, `index_digest`, `version`; selects entirely indexed content, not membership or head |
| `pc_rag_jobs` / application service | PK `(scope_id, job_id)`; UQ `(scope_id, request_scope_key, operation, idempotency_key)` | `rag_id/document_id` where applicable, `binding_name`, `parent_job_id`, exact Source, `request_digest`, `profile_snapshot`, `target_content_generation`, `generation_id`, `change_seq`, `stage/state`, `attempt`, `lease_token/expires_at`, `next_attempt_at`, `receipt`, sanitized `error`, `actor`; receipts, item work, and index-change work |
| `pc_source_lifecycle` / shared Source service | PK `(scope_id, source_type, source_id)` | `state` (`active/deleted`), `delete_generation`, `deleted_at`, `deleted_by`, `reason`, `delete_operation_id`; initially used for document input and its edit Sources |
| `pc_rag_chunk_index` / rag adapter | PK `entry_id`; UQ `(scope_id, rag_id, generation_id, document_id, revision, chunk_id)` | Complete target key, `searchable_text`, `embedding_input_digest`, `vector_space_id`, `embedding`, `source_digest`; derived exact-chunk entries |

`request_scope_key` is a server-generated, non-null canonical key: Scope+operation for rag creation, target rag
for its commands, or exact Source for deletion. Internal jobs use deterministic keys; nullable `rag_id` must not
weaken idempotency. The request digest covers operation, target, normalized input descriptor, and preconditions.
An accepted snapshot is pinned: retries return its receipt even if the original path changes. A new file version
requires a new request key.

Store full external keys alongside fixed-size identity hashes; compare full values on collision and never silently
merge them. Import acceptance reserves the document ID and ownership row; the Artifact is created only after
processing succeeds, so a pending reservation cannot require a document-head foreign key. Content/node/chunk
references resolve to `(scope, family=document, document_id, revision)` and rag references to `family=rag`.
Use composite constraints or transactional validation for same-rag relationships. Public deletion has no SQL cascade.

Manifests include root SourceRef, parser/OCR/chunker snapshot identifiers, `chunk_count`, `chunks_digest`, and schema
version. Chapter/block parent links stay in the same revision and are acyclic. Chunk locators refer to those blocks;
IDs and order are validated before publication. Edit Source payloads provide audit details without another master table.

### 3.3. Indexes and backend mapping

| Access path | Required index / invariant |
| --- | --- |
| List rag documents | `(scope_id, rag_id, state, document_id)` on membership; no full Scope Artifact scan |
| Read revision chunks | Unique ordinal index; keyset cursor fixes revision and last ordinal/chunk ID |
| Resolve Source dependencies | Reverse lineage index; retain root dependencies after every edit |
| Claim work / recover leases | `(binding_name, state, next_attempt_at, job_id)` and `(state, lease_expires_at)` |
| Catch up rebuild | `(scope_id, rag_id, change_seq)` on durable change jobs; retain receipts while builds need them |
| Search published content | Generation/publication keys and chunk-entry UQ; enforce exact revision before ranking |

SQLite uses FTS5 with stable entry/row-ID mapping and a validated sqlite-vec adapter or another explicitly supported
exact-vector implementation. Missing vector capability must not silently downgrade hybrid requests. SeekDB and
OceanBase use separately verified full-text/vector adapters. `embedding` is a logical column: physical
dimension-specific tables or vector structures may differ, but map to the same exact chunk key. Check index-length
limits without truncating identities. Model/profile changes create generations rather than overwrite old vectors.

A manual edit transaction writes its system Source, new manifest, complete nodes/chunks snapshot, lineage, target
generation, and index job while preserving membership. An index worker builds invisible entries, validates coverage
and digest, then compare-and-swaps the publication row. A full rebuild captures `change_seq` and applies durable
changes through cutover; inserts, updates, removals, and target changes advance it in their owning transaction.
Source deletion additionally checks the Scope visibility epoch before any per-rag fan-out finishes.

Cancelled/superseded builds cannot become visible. First-phase retention includes deleted records and indexes;
storage therefore grows with revisions and rebuilds. Expose retained/visible counts and capacity failures.
Restoration, garbage collection, and permanent erasure are deferred, not implicit side effects of DELETE.

## 4. Targeted asynchronous work and publication

An import request fixes the Scope, target `rag`, exact SourceRef, logical document identity, processing configuration
snapshot, and input/request generation. Accepting or reusing a Source and recording target work must be atomic.
Source journal replay alone is insufficient: reuse in another `rag`, retries, and configuration rebuilds may append
no Source entry and must explicitly request processing.

Reuse Scope/binding supervision, leases, timeouts, and execution fences. Workers claim persisted target work;
they do not broadcast every Source to every `rag`. The stages are parse/OCR, chunk/content commit, index build,
and publication. Each work item follows these transitions, with bounded retry policy:

```text
queued -> running -> succeeded
             +--> retry_wait -> queued
             +--> failed
queued / running / retry_wait -> cancelled or superseded
failed -> queued through an authorized explicit retry
```

Expose stage, failure reason, attempt, and both latest-content and searchable revision. A first failure may have
no document Artifact yet. Retry the failed durable stage without duplicating content revisions or publication.
Replaying an idempotency key with a different request digest is a conflict.

| Boundary | Atomic records / required checks |
| --- | --- |
| Snapshot preparation | Persist bytes before acceptance; failed acceptance may leave an unreferenced snapshot retained for a future GC policy |
| Import acceptance | Source acceptance/reuse, validated target request, and durable scheduling intent |
| Content commit | Complete manifest/details, provenance, validated membership, content receipt, and index work; manual edits also create their system Source here |
| Index publication | Verify complete indexes, exact content target, active configuration, execution ownership, and lifecycle; switch the searchable selection and visibility generation |
| Soft deletion | Persist owning lifecycle marker, advance visibility generation, and save idempotent receipt before acknowledgment; no physical cleanup |

Run OCR, embeddings, DDL, and bulk index construction outside these short transactions. Every commit rechecks
registration/lease fences, target state, and input/configuration/deletion generations. A newer edit or import
supersedes old publication work. Durable work records are authoritative; process-local notifications only wake workers.

Content head and searchable revision are distinct. Index failure preserves the previous published revision while
showing the new content and error. A `rag` rebuild uses a new generation and catches up accepted changes to a verified
cutover watermark before switching; a partially built generation cannot replace the active one. Rebuilding one
knowledge base cannot switch another's generation. Filesystem/database backup and restore must verify referenced
snapshots and index consistency before serving restored retrieval.

Existing `pc_artifact_processing_pending.source_through` requires a positive Source position. Target-only work
uses requested intent generations and durable jobs without inventing a Source position or advancing a Source cursor.
Each claim captures its lease token plus Supervisor/binding fences; checkpoints compare all of them. Expired
ownership permits recovery but never authorizes a late worker to commit.

## 5. Manual editing, reprocessing, and deletion

Initially allow replacement of one existing chunk's text, guarded by the document head ETag. Do not expose chunk
insertion, deletion, merging, or reordering. Reject oversized edits rather than silently rechunking. Editing one
chunk does not silently edit overlapping text in another chunk.

Follow Artifact management's provenance pattern: in the content transaction create a system Content Source with
`role=lineage_only` and `operation=artifact_replace`, then commit the new document revision and index work. Record
the base revision, edit, actor/reason, and exact target. This Source is not parsed again. Explicitly preserve the
original input Source dependency as well as the edit Source and base revision; generic replacement provenance
must not be assumed to retain the root input automatically.

Manual replacement preserves logical chunk IDs, but citations always include revision. Reparse/rechunk generates
new chunk IDs; ordinal position is not identity. Edited results carry `origin=manual_edit` and `verbatim=false`:
their original locator is supporting evidence, not a claim that the edited words appear there.

Updates refresh full-text entries, vector mappings, publication selection, and visibility-sensitive caches.
Embedding computation may be reused only when the complete actual input and model/profile identity match;
even then, new revision addresses must be indexed. Changing only embedding/index configuration preserves edited
content. Reprocessing manually edited content requires explicit `manual_edits_policy=discard`, otherwise a conflict.
Editing while a Source replacement/reparse is pending is rejected; editing committed content still being indexed
is allowed and supersedes older index work. Check these conditions atomically with target state.

**The first release implements soft deletion only.** Keep Source payloads, original snapshots, document revisions,
chunks, edit evidence, and indexes physically stored. No restore or permanent-erasure endpoint is provided.
Deletion removes availability through supported application interfaces, not from backups or administrator storage
access. Retained tombstones prevent identity reuse and accidental resurrection.

| Delete target | Authoritative marker | Immediate effect; retained objects |
| --- | --- | --- |
| Document Source | `pc_source_lifecycle.state=deleted` and Scope visibility epoch | All revisions rooted in it become unavailable, including manual edits across rags in that Scope; rag configuration remains |
| Document | `pc_rag_documents.state=deleted` and rag visibility/change counters | All its revisions/chunks become unavailable via membership, generic reads, and retrieval; Source and other documents remain |
| rag | `pc_rag_runtime.state=deleted` and rag visibility epoch | The knowledge base and its documents become unavailable; Scope, shared Sources, and other rags remain |

Visibility is a conjunction, not a copied boolean on every row: ordinary content reads require a non-deleted,
enabled rag, active document membership, and non-deleted root Source. Search also requires active generation and
exact published revision. Historical reads can select older revisions but must check their parent/Source markers.
Resolve the root input from the requested revision, not the membership row's newest target Source. Historical
base-revision links are not transitive content dependencies: a fully reparsed replacement declares its own root.
Disabled rag configuration/status remain readable to authorized users; content retrieval is disabled. Re-enabling
a disabled rag does not restore a deleted rag.

Source deletion needs no synchronous updates to every dependent chunk/membership row: the root marker and epoch
immediately reject reads. Administrative status derives the effective reason. Direct reads of system edit Sources
carrying derived text are gated through their exact document target and immutable root Source reference. Ordinary
Source, generic Artifact, history, chunk, raw-image, and cached reads cannot bypass these checks.
An original Source has its own Scope/lifecycle authority: deleting only a rag or document does not revoke that
Source or its direct original-file read. Edit Sources instead carry derived document text and must check their
document/rag target. Public Source DELETE targets original document inputs; edit Sources are system-managed.

Keep lineage foreign keys and `RESTRICT` constraints: public DELETE physically deletes no Source/Artifact rows.
Repeated DELETE returns the accepted receipt without advancing counters again. Import into a deleted identity
returns `410`; a new identity must be explicitly chosen. No phase-one operation clears a deletion marker.
In-flight reads recheck visibility before returning; late workers fail commit/publication checks. Already delivered
responses cannot be recalled.

Deleting an old Source does not revoke revisions derived solely from its replacement. A published old revision
rooted in the deleted Source becomes unavailable immediately, even while its replacement is indexing. Do not use
it as fallback, silently publish an unindexed replacement, or exempt manually edited content from Source checks.

## 6. Retrieval and public interfaces

Queries select complete `rag` references, in one Scope or across authorized Scopes. A Scope alone must not silently
select the first or all knowledge bases. Authorize every target and apply membership, metadata, lifecycle,
published-revision, and generation filters within each full-text/vector channel. Do not retrieve Scope-wide top-k
and filter afterward. If a backend cannot apply eligible-candidate filtering with ANN, use a verified filtered
exact-vector path rather than silently changing the retrieval contract.

Use bounded candidate counts and deterministic rank fusion. Different embedding profiles require compatible
query embeddings; incompatible raw scores cannot be compared across knowledge bases. Deduplication preserves
distinct provenance. Return text, Scope/`rag`, document/revision/chunk, SourceRef, locator, edit markers, and actual
index profile/generation. Report per-target failures separately from no matches; all targets failing is a failure.
Parser, chunker, retrieval strategy, and optional reranking hooks must preserve these budgets and evidence contracts.

Reuse generic Artifact create/read/replace/revision/tag shapes, complete references, pagination, errors, and
conditional writes. Add domain capabilities for targeted import, task inspection/retry, revision-pinned chunk
reads, chunk editing, retrieval, and Source revocation. Do not expose unrestricted generic document replacement
that can bypass provenance and ownership validation. Keep HTTP, Python client, and necessary MCP capabilities
consistent, with explicit knowledge-base context selection and unchanged Memory retrieval defaults.

The following routes and payloads are proposed contracts. Implement them in `openapi/powercontext.yaml`,
generate clients, and run contract checks; they are not already available endpoints.
New families and detail tables require versioned migrations on all three backends; existing Sources and Artifact
families retain their contracts and are not automatically converted or enrolled in document processing.

### 6.1. API surface

Let `R = /v1/scopes/{scope_id}/artifacts/rag/{rag_id}` and
`D = /v1/scopes/{scope_id}/artifacts/document/{document_id}`. Routes below are proposed, not released.
Reuse current `ArtifactReference`, `ArtifactAddress`, `SourceTypeReference`, `ETag`, `Location`, pagination,
`ErrorResponse`, and Scope permissions. No parallel generic Artifact CRUD protocol is introduced.

| Operation | Route / contract | Permission / response |
| --- | --- | --- |
| Create rag | Existing POST `/v1/scopes/{s}/artifacts`, `family=rag`, typed `content`; extend this command with optional `idempotency_key` | `scope.contribute`; `201 ArtifactCreated`, ETag, Location |
| List/read/configure | Existing family list, GET/PUT `R`, revisions/tags; config PUT replaces complete content with current Artifact `If-Match` | Read: `scope.read`; config: `scope.contribute`; `200`, ETag |
| Operational state | New GET/PUT `R/runtime`; PUT accepts `enabled` and `expected_version`, without creating config revisions | Read: `scope.read`; control: `scope.admin`; `200` runtime version/state |
| Import batch | New POST `R/imports`; `idempotency_key`, items with unique `item_key`, namespace/key, and input descriptor | `scope.contribute`; `202` parent job and per-item receipts |
| Browse members | New GET `R/documents`; cursor/state filters, reserved targets, membership version, effective availability, head/published revisions | `scope.read`; `200 items/next_cursor` |
| Document/history | Existing GET `D` and revisions; manifest/provenance with lifecycle checks | `scope.read`; `200`, ETag |
| Chunk/evidence detail | New GET `D/revisions/{rev}/chunks` and `/nodes`; effective chunks versus original evidence; cursor/limit | `scope.read`; `200 items/next_cursor` pinned to revision/view |
| Edit chunk | New PATCH `D/chunks/{chunk_id}`; document `If-Match`, text/reason/idempotency key | `scope.contribute`; `202` new revision and index job |
| Reprocess document | New POST `D/reprocess`; document `If-Match`, idempotency key, explicit `manual_edits_policy=discard` when needed | `scope.contribute`; `202` job |
| Rebuild rag | New POST `R/rebuilds`; `config_revision`, `mode=index_only/reprocess`, idempotency key, edit-discard policy when needed | `scope.admin`; `202` generation/job |
| Inspect/retry work | New GET `/v1/scopes/{s}/rag-jobs/{job_id}`; POST `.../{job_id}/retry` with new idempotency key, same accepted target/input | Read: `scope.read`; retry: `scope.contribute`; `200` status / `202` receipt |
| Query selected rags | New POST `/v1/rag/query`; explicit targets, query, top_k, typed filters and bounded options | `scope.read` for every target; `200` hits and target outcomes |
| Original input | Existing Source GET returns descriptor; new GET `/v1/scopes/{s}/sources/{type}/{id}/content` streams the snapshot | `scope.read`; `200`, media type; no storage credentials |
| Soft-delete document | New DELETE `R/documents/{document_id}`, `expected_version` from membership | `scope.contribute`; `200` receipt |
| Soft-delete rag | New DELETE `R`, `expected_version` from runtime | `scope.admin`; `200` receipt |
| Soft-delete Source | New DELETE `/v1/scopes/{s}/sources/{type}/{id}`; restricted to document Source lifecycle initially | `scope.admin`; `200` receipt |

DELETE carries `Idempotency-Key` and, where specified, an `expected_version` query parameter. Receipts contain
`operation_id`, target, `state=deleted`, `deleted_at`, and `data_retained=true`, without a cleanup job. Repeated
DELETE returns the original receipt. Administrative jobs can report deleted status without retained text;
normal content GET returns `410` after authorization. There is no `include_deleted` content bypass.

Creating an empty rag atomically creates initial runtime/generation metadata without scheduling parsing. Config
PUT stores desired settings. Imports use effective settings; config changes do not silently reparse edited content.
Explicit rebuild activates the desired snapshot after verified cutover; `index_only` rejects parser/OCR/chunker
changes. Concurrent imports/edits remain durable and are caught up before switching. Expose desired and effective
revisions separately. Failed rebuilds leave the old effective configuration and indexes.
During ordinary document updates, a valid published revision need not equal the latest head or target generation.
That difference is intentional while indexing; only a successful publication switches it. Lifecycle revocation
always overrides this availability fallback.

File input uses an authorized Source binding plus relative path, or an exact same-Scope `SourceTypeReference`.
Arbitrary client server paths, URLs, or snapshot locators confer no authority. The plugin captures bytes and validates
media/digest. Reusing a Source still schedules target work. Validate permission and batch shape before acceptance;
per-item capture failures may coexist with successes, but every accepted item atomically persists Source, identity
reservation, job, and intent. Report partial acceptance explicitly. Batch retries return the same item receipts;
failed capture without pinned input needs a new key, while worker retry uses the accepted snapshot.

### 6.2. Representative payloads

**Create** uses the existing `family + content` shape. Profiles below are illustrative configured bindings, not
hard-coded defaults. The rag content schema rejects unknown fields and contains no secrets or member arrays.

```json
{
  "family": "rag",
  "idempotency_key": "create-ops-01",
  "content": {
    "schema_version": "1",
    "title": "Operations SOPs",
    "summary": "Runbooks and screenshots",
    "profiles": {
      "parser": "markdown-v1",
      "ocr": "rapidocr-local-v1",
      "chunker": "heading-paragraph-v1",
      "embedding": "docs-embedding-v1",
      "retrieval": "hybrid-v1"
    }
  }
}
```

**Import** into an existing rag uses exactly one input alternative per item:

```json
{
  "idempotency_key": "import-ops-01",
  "items": [
    {"item_key": "a", "namespace": "oceanbase-docs", "external_key": "README.md",
     "input": {"kind": "file", "binding_name": "local-documents", "path": "README.md"}},
    {"item_key": "b", "namespace": "ops-images", "external_key": "restart.png",
     "input": {"kind": "source", "source": {"source_type": "knowledge-document-input", "source_id": "image-42"}}}
  ]
}
```

The `202` result contains a stable parent job, per-item Source references and reserved document IDs, with
`document=null` until content exists. Parent counts distinguish acceptance from success. Item receipts expose
`stage=accepted/parse/chunk/index/publish`, state, attempt, latest-content/searchable revisions, and error.
Parent aggregation may report `partial_failure` without adding it to individual work-item states.

**Edit** PATCH `D/chunks/chunk-3` with the document GET's ETag in `If-Match`:

```json
{"idempotency_key": "edit-03", "text": "Restart the follower before the leader.", "reason": "Correct OCR text"}
```

```json
{
  "document": {"family": "document", "artifact_id": "doc-1", "revision": 2},
  "index_job_id": "job-12",
  "latest_content_revision": 2,
  "searchable_revision": 1,
  "chunk": {"chunk_id": "chunk-3", "origin": "manual_edit", "verbatim": false}
}
```

**Query** reuses `ArtifactAddress` for each rag. Target revision pins the config head seen by the caller; a stale
head returns conflict. The response separately identifies effective index/configuration, which may lag desired settings.

```json
{
  "targets": [
    {"scope_id": "project-p", "artifact": {"family": "rag", "artifact_id": "ops", "revision": 1}},
    {"scope_id": "project-q", "artifact": {"family": "rag", "artifact_id": "product", "revision": 3}}
  ],
  "query": "How should I restart a follower?",
  "top_k": 10,
  "filters": {"media_types": ["text/markdown", "image/png"]},
  "retrieval": {"mode": "hybrid", "candidate_limit": 100}
}
```

Each hit carries Scope, rag/document ArtifactReferences, chunk ID/text/rank/score, SourceTypeReference, supported
locator data, origin/verbatim, and index generation. Return target statuses/errors and `partial`. Authorization
failure rejects the entire request before retrieval; an authorized target's operational failure may yield explicit
partial results. All-target failure remains an error.

### 6.3. Errors, concurrency, and activation

| Condition | Contract |
| --- | --- |
| Same key/digest | Replay stable receipt after current authorization/lifecycle checks; deletion replays its tombstone receipt |
| Same key, different digest | `409 idempotency_conflict`, no new work |
| Missing/stale content or config ETag | Existing `428` / `412` precondition responses |
| Stale runtime/membership version, rag config, or pending reparse | `409` with machine-readable reason; no partial commit |
| Deleted target/evidence | `410 resource_deleted` after authorization; no text in error details |
| Unsupported input, invalid profile, oversized edit | `422`; transport byte-limit rejection may use `413` before acceptance |
| Missing backend/model capability | `503`, never an empty match list |
| Accepted asynchronous failure | Job becomes failed/retry_wait; `202` never means processing succeeded |

Keep the existing error envelope:

```json
{"error": {"code": "resource_deleted", "message": "The requested document is unavailable.", "details": null}}
```

Content writes compare the document head; index writes compare exact target/publication versions. Both update
state and durable work atomically. Replay lookup precedes rechecking an old write precondition but never bypasses
current authorization/deletion. Failed commands roll back their receipts and writes. Staged content is invisible
until complete, and replay responses must not leak text from subsequently deleted targets.

Migrations create schemas/indexes/capability declarations before activation. New document Sources get lifecycle
rows during acceptance; missing required lifecycle/ownership is an integrity failure, not permission to read.
Existing unrelated families remain unchanged. Reject activation without required backend/profile capability.
Validate upgrade/restart, duplicate requests, read-after-write, publication races, and deletion filters through
public APIs rather than direct table writes.

## 7. Acceptance and delivery

Use Markdown from a pinned `oceanbase/oceanbase` commit and standalone OCR fixtures with checked text/locators.
On SQLite, SeekDB, and OceanBase, verify:

- Multiple empty knowledge bases per Scope, automatic document membership, exact citations, single-base filtering,
  and explicitly authorized same-Scope/cross-Scope queries.
- Existing-Source reuse and idempotent retries, first-import failure without a document, process restart, atomic
  content/membership/work commit, and failed indexing with an old searchable revision.
- Real RapidOCR execution, malformed/unsupported input and no-text outcomes, preserved Markdown image references,
  and full-text/vector retrieval over document chunks without whole-document JSON scans.
- Manual edit provenance, complete snapshots, revision-pinned pagination, updated full-text/vector mappings,
  reparse conflicts, stale-worker rejection, and generation cutover during concurrent updates.
- Source/document/rag soft deletion, historical/cache/original/edit-Source read enforcement, retained physical rows,
  idempotent deletion, and preservation of unrelated documents, replacement-Source revisions, and rag configurations.

Use existing issues for delivery: #2441 covers foundations/ingestion, #2442 processing/content/index lifecycle,
#2443 retrieval/interfaces, and #2444 operational visibility and cross-backend acceptance. The implementation can
use one integration branch and PR with these four review stages. This RFC PR completes none of those implementation
issues. Publish supported formats, model versions, resource limits, and recovery instructions with implementation;
do not infer production capacity from functional fixtures.

# Drawbacks

Complete snapshots amplify storage and write cost even for a one-chunk edit. Separating content and searchable
revisions complicates reads and diagnostics. Dedicated consumer restrictions, target work, family writers, and
revocation need framework integration beyond a plugin declaration. Three backends add migration and index testing
cost. RapidOCR model packaging and platform compatibility also require explicit maintenance. Soft deletion retains
all data and indexes, so it is not an erasure feature and requires storage-capacity monitoring.

# Rationale and alternatives

- **A Scope per knowledge base:** provides existing isolation but couples collection organization to authorization
  and project identity. Multiple `rag` Artifacts keep those concerns separate; use separate Scopes when isolation is required.
- **Chunks stored in mutable Sources:** mixes original evidence with derived content and conflicts with Source immutability.
- **One Artifact per chunk:** turns a whole-document update into many independent Artifact lifecycles and obscures atomic revisions.
- **All chunks in one Artifact JSON field:** simplifies writes but makes passage reads and indexing depend on large payloads.
- **Independent knowledge-base master tables:** duplicate existing Artifact identity, version, and management facilities.
- **Source-journal-only generation:** cannot reliably express empty `rag` creation or work triggered by existing-Source reuse.

The selected design preserves whole-document identity while giving chunks efficient physical storage and explicit
publication. Doing nothing leaves applications to assemble inconsistent ingestion, provenance, and deletion workflows.

# Prior art

This proposal builds on the repository's [Source plugin design](2012-source-plugin-system.md),
[Artifact plugin design](2175-artifact-plugin-system.md), [Source model](1400_source_definition_and_observation_model.md),
[Artifact processing supervisor](1515_artifact_processing_supervisor.md),
[Source/Artifact API](1437_source_artifact_rest_api.md), and [Topic Memory](1417_topic_memory.md).
It reuses their extension, provenance, scheduling, and read boundaries while adding explicit collection management
and document-specific storage/publication. Topic Memory's family-specific detail/index schema is not a shared document schema.

# Unresolved questions

The following must be fixed and tested in the implementing changes; they do not defer identity, atomicity,
consumer restrictions, or revocation semantics:

- Exact Python capability/writer signatures, migration DDL, and backend layouts implementing the contracts above.
- Limits for snapshots, decoded image pixels, manifests, chunks, pages, worker concurrency, timeouts, and retries.
- Pinned RapidOCR/ONNX/model versions, supported image formats/platforms, and default chunk/embedding profiles.
- Future retention/erasure policies and expired-reference responses; phase one retains historical and soft-deleted data.

Full edited-document export needs an explicit overlap and edit reconciliation contract. Independent per-`rag` ACLs,
cross-deployment federation, and shared document ownership require separate design work rather than implicit support. Restoration and permanent
erasure also need explicit dependency, authorization, and audit rules before exposure.

# Future possibilities

Additional parsers, storage providers, automatic collection, alternative chunkers, rerankers, and bounded agentic
retrieval can use the declared component boundaries. Measured storage pressure may justify shared immutable chunk
bodies across revisions. These extensions must preserve exact evidence, target isolation, and publication/deletion
semantics; they are not prerequisites for the initial knowledge-base workflow.
