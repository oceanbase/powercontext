---
title: "RFC 2570: Document knowledge bases with rag Artifacts"
description: "A proposal for OpenDAL document ingestion, simple Markdown chunking, plugin-owned retrieval, and versioned knowledge bases using existing Source and Artifact facilities."
---

- Proposal Name: `rag_knowledge_base`
- Start Date: 2026-11-10
- RFC PR: [#2570](https://github.com/oceanbase/powercontext/pull/2570)
- Tracking Issue: [#2440](https://github.com/oceanbase/powercontext/issues/2440)
- Related Issues: [#2441](https://github.com/oceanbase/powercontext/issues/2441), [#2442](https://github.com/oceanbase/powercontext/issues/2442), [#2443](https://github.com/oceanbase/powercontext/issues/2443), [#2444](https://github.com/oceanbase/powercontext/issues/2444)

# Summary

Provide knowledge bases through a document Source plugin and the `document` and `rag` Artifact families. A Scope
contains multiple user-created rags; each document Source and document belongs to exactly one rag. OpenDAL captures
local files, Markdown and image OCR share a simple Markdown chunker, and existing Artifact Workers prepare content
and indexes before committing a document revision together. Reuse existing core metadata schemas, CRUD, supervision,
and generic index storage; add only document-specific detail and target records. This is a proposed contract, not
released functionality.

# Motivation

Business descriptions and SOPs need passage retrieval over complete documents, exact provenance, explicit updates,
and predictable deletion. Memory extraction does not preserve every passage. The first implementation should cover
that workflow without a second task platform, a per-rag index-generation system, or duplicate Artifact master tables.

Source snapshots and historical document revisions remain evidence. Knowledge-base retrieval is a derived projection,
maintained independently of existing Memory retrieval data and defaults. Reuse infrastructure without treating the
current plugin declaration as already sufficient for targeted ingestion, separate chunk storage, or deletion checks.

# Guide-level explanation

1. Create a `rag` through standard Artifact creation in an existing Scope.
2. Select the rag and a local directory/file. The client uses a configured local OpenDAL Source plugin; users do not
   supply Source IDs, namespaces, hashes, or document IDs.
3. Inspect the latest per-file processing status. Accepted input is not yet searchable content.
4. Search documents owned by the selected rag, or explicitly select multiple authorized rags.
5. Change a file and import again. Identical successfully processed input is skipped; changed content creates a new
   Source and revision of the same logical document.
6. Edit or remove a chunk. Both create a new document revision; old revisions and their chunk content remain intact.

Scope remains the authorization boundary. Source/document ownership is exclusive to a rag, not cross-rag sharing.
Importing the same file into two rags creates different Sources/documents even if their physical snapshots are deduplicated.
The initial release supports Markdown and independently imported text images using RapidOCR with ONNX Runtime.
Markdown image references are retained without fetching or OCR-processing them. SQLite, SeekDB, and OceanBase must
support the workflow, with one selected persistence backend per deployment.

PDF, Office, plain TXT ingestion, semantic/LLM chunking, complex layout/table reconstruction, scheduled directory
synchronization, per-rag embedding spaces, independent online rag index cutovers, a full management UI, and a
large-corpus performance gate are out of scope. The UI can later use the per-file status contract.

# Reference-level explanation

## 1. Ownership and plugin configuration

```text
Scope
  +-- rag K1 -- owns --> original Sources -- derive --> document revisions -- chunks
  +-- rag K2 -- owns --> different Sources/documents, even for the same local file

Source plugin: OpenDAL capture and immutable snapshot access
Document plugin: Markdown / OCR -> Markdown -> chunks, revisions and provenance
rag plugin: collection management, retrieval policy and index adaptation
Shared facilities: Source/Artifact APIs, Supervisor/Worker, transactions, database/search adapters
```

A rag is configuration and collection identity, not a Source-generated output. It can be empty. Its name/configuration
uses Artifact revisions; deletion uses existing head governance. There is no separate rag runtime table or runtime
status API. Parsing progress belongs to the imported file, not to rag configuration revisions.

`document` represents a whole document. Its stable identity is scoped by `(scope, rag, collection, relative_path)`;
chunks are internal revision details. The server derives `collection` from the registered storage binding/instance
and normalized root, not a fresh scan ID. Source payloads contain immutable owner rag identity, original path,
relative path, media type, content digest, and a managed snapshot reference. Tasks snapshot processing configuration;
mutable task status and parsed text never enter Source payloads. Only the document pipeline directly consumes this
Source type, enforced at selection, execution, and commit.

The three logical plugins may ship in one package. The knowledge-base package owns its embedding/index configuration,
separately from Memory or other main-path configuration. Explicit plugin settings win; otherwise installation may
initialize compatible values from the main path and persist a resolved configuration snapshot. Keep credential
references rather than copying secrets into Artifact content. Later main-path changes do not silently change the
plugin profile. Missing/incompatible settings fail activation. Parser/chunker settings may vary by rag; the initial
embedding/index profile is plugin-level, shared by its document family within each Scope.

Reuse model invocation and SQL adapters, but isolate by family, index definition, and vector-space/profile identity.
No queries implicitly mix Memory rows with document rows. A plugin model/dimension change uses the existing family
rebuild mechanism, with its availability limitations, not a new per-rag publication protocol.

## 2. OpenDAL ingestion and duplicate detection

Use the existing OpenDAL/`opendalfs` integration for local listing and reads. The plugin instance must run where the
selected local path exists; a remote Server cannot interpret an arbitrary client-local path. Resolve relative paths
against the configured root, reject escapes, and keep a stable normalization/symlink policy. Original Markdown bytes
and images are captured into managed immutable snapshots before Source acceptance. Hash the exact bytes saved;
never hash one read and process a later read of a changing file. Read errors or detected unstable capture are retryable.

```text
file_key       = (scope_id, rag_id, collection_id, normalized_relative_path)
content_digest = SHA256(captured raw bytes)
source_key     = hash(source schema version, file_key, content_digest)
processing_key = hash(content_digest, parser/OCR/model versions and settings,
                      Markdown normalizer version, chunker version and settings)
index_key      = hash(complete embedding inputs, plugin profile and projection versions)
```

mtime/size may help select work but are not proof of identical content. Compare full identity fields on hash collision;
never truncate paths or merge them by digest alone. User-visible Source IDs are generated by the Source framework
using the plugin's deterministic identity rules. Reusing an existing immutable Source preserves its first canonical
capture metadata rather than rewriting mtime or annotations.

| Observation | Required behavior |
| --- | --- |
| Same file/content/processing key, successful result | Reuse Source and document; no new revision; check index readiness separately |
| Same target already pending/running | Coalesce; do not launch another parse |
| Same input, previous processing failed | Retain pinned Source and allow bounded retry; a capture checkpoint is not processing success |
| Same path, changed bytes | Accept new Source; process a new revision of the same document |
| Same bytes, changed parser/OCR/chunker | Reuse Source; explicitly reprocess to a new revision |
| Only embedding/index profile changed | Reindex committed chunks without rerunning OCR |
| Different path or different rag, identical bytes | Different logical document and Source; optional physical snapshot reuse only |
| Deleted identity encountered | Reject implicit resurrection; no restore in phase one; require an explicitly new identity |

Returning to previously seen bytes still compares against the current target/result: finding an old Source is not
proof the current document already represents it. Renames are new paths initially; missing files do not imply deletion.
Manual edits/removals must not be overwritten by an unchanged-file scan. Changed input or explicit reprocessing of
an edited document requires `manual_edits_policy=discard`; without it, retain the old committed revision and report
an actionable conflict. The input Source can already be captured while content processing remains blocked.

Use Source `collect` capability discovery and existing add/status/control operations. A plugin-specific `scan_token`
in the capability input identifies one explicit scan target, generated by the client. A retried add with the same
input reuses that target; a new explicit import uses a new token but the same collection/file identities. The plugin
scans once per token and remains available for future targets; it does not periodically rescan. Existing Source
status describes collection, not full document processing or a batch history. New scans must consult document
processing state as well as capture checkpoints, so unchanged capture cannot hide downstream failures.

## 3. Markdown normalization and simple chunking

Both inputs produce a common Markdown view:

```text
Markdown bytes -> markdown-it-py structure / original-byte mapping --+
                                                                  +-> rule-based chunks
Image -> RapidOCR -> text lines/regions -> normalized Markdown -----+
```

For OCR, preserve actual text, order, confidence and original-image regions, accounting for preprocessing transforms.
Markdown conversion escapes syntax and groups lines/paragraphs; it does not invent headings, repair text with an LLM,
or reconstruct complex tables. Keep normalized-text spans mapped to OCR lines and image regions. Normalized Markdown
is derived processing content, never a replacement for the image Source. A locator must state its actual precision.

Use headings, paragraphs, lists, and code blocks; merge short blocks up to `max_chunk_chars`, split oversized blocks
at sentence/length boundaries, and optionally retain `overlap_chars` at split boundaries. Validate overlap is smaller
than the maximum. Preserve heading paths, order and continuation flags; do not require a standalone section-tree
subsystem. Before embedding, enforce the actual model token budget, including any heading/prefix context; do not
silently truncate. Chunker changes that alter boundaries are versioned configuration changes.

Source media validation, UTF-8 errors, bad/unsupported images, no recognized text, missing OCR models, excessive
pixels, and timeouts have explicit outcomes. Pin/test real RapidOCR and ONNX/model versions. Normalized Markdown may
be computed transiently; an independent parsed-document cache is not required. Chunks store effective text and
bounded source mappings, not vectors. The Artifact manifest remains bounded and does not embed all chunks.

## 4. Persistence: unchanged core schemas and three supporting tables

Existing core metadata table definitions are unchanged by this proposal. New family records, declarations, and
indexes use their established schemas. This is a constraint on migrations, not a claim that code changes are unnecessary.

| Reuse | Purpose and required code integration |
| --- | --- |
| `pc_sources`, Source journal, connector checkpoints | Immutable inputs, capture identity and acquisition progress |
| `pc_artifacts`, `pc_artifact_heads` | rag configuration, document revision manifest, active/retired state and governance CAS |
| `pc_artifact_lineage_sources`, locators, Artifact lineage, tags | Root input, system edit Source, base revision and tags; not mutable membership |
| `pc_artifact_processing_intents`, leases, binding state | Durable wakeup, explicit request generations, execution ownership and registration fences |
| `pc_artifact_search_states`, coverage, changes | Existing Scope/family build coordination and current-revision index maintenance |
| `pc_artifact_search_text`, vectors, vector spaces/index definitions | Fragment-level retrieval projections; `family=document`, `fragment_id=chunk_id` |

Do not add `pc_rag_runtime`, per-rag generations/publications, a general jobs/receipts system, or separate nodes/directory
master tables. Do not store state in unrelated Scope settings or change Source payloads to simulate mutable metadata.
Document ownership is explicit, not inferred from tags or evidence lineage.

The three supporting tables below are a proposed implementation baseline. Include UTC timestamps, byte-exact
identity comparison, bounded JSON metadata and backend-safe key lengths. Migrations must check actual layouts on
all three backends; equivalent existing storage may replace a table only with matching uniqueness and recovery semantics.

| Table | Primary/unique keys | Minimum fields and role |
| --- | --- | --- |
| `pc_document_imports` | PK `(scope_id, document_id)`; UQ `(scope_id, rag_id, collection_id, path_hash)` | Full relative path, binding name, current/target SourceRef and digest, processing/configuration snapshot and fingerprint, target generation, operation (`import/reprocess/edit`), bounded pending edit, request ID/digest, state/stage, attempt, retry time, lease token/expiry, result revision and error; combines identity reservation, membership and latest per-file work |
| `pc_document_chunks` | PK `(scope_id, document_id, revision, chunk_id)`; UQ `(scope_id, document_id, revision, ordinal)` | Text, content digest, heading path, continuation/overlap metadata, normalized spans and original byte/image locators, origin, edit/base references; complete immutable revision snapshot |
| `pc_document_source_states` | PK `(scope_id, source_type, source_id)` | Immutable owner `rag_id`, document/file identity, `state=active/deleted`, deletion generation/time; mutable lifecycle alongside the original Source, including its system edit Sources |

Reserve a document ID before parsing without requiring an Artifact head to exist. Once committed, all detail keys
resolve to `family=document` in the same Scope; each Source's owner matches the reservation and immutable payload.
The manifest contains owner rag, root SourceRef, schema/processing fingerprint, chunk count/digest and manual-change
indicator. A revision with 100 chunks has one manifest and 100 rows; removing a chunk creates a new manifest and 99
rows without touching the original 100. Copies of unchanged rows may be prepared efficiently, but incomplete snapshots
must never become visible. Old revisions are addressed exactly, not by interpreting today's head.

Index imports by `(scope, rag, document_id)`, pending work by binding/state/retry time, chunks by revision/ordinal,
and Source state by `(scope, rag, document_id)` for deletion fan-out. Verify existing reverse lineage access paths
for root-source revocation. Hash keys retain and compare full original identities. Source row ownership is immutable;
status/progress never changes it. The imports table preserves the latest target/result, not an unlimited batch or
command history. Errors before a Source is accepted belong to collection diagnostics. A complete durable batch
history would require an explicitly scoped extension, not an unbounded JSON array in a metadata row.

## 5. Existing Workers and atomic content/index commit

The application service coordinates the three plugins; it is not another plugin or service. Source acceptance/reuse,
Source lifecycle initialization, document target reservation/update, and `processing_intents.request()` commit
atomically. A durable target can therefore be recovered even if a wakeup notification is lost. The source-only
submission paths must call this same acceptance capability; they cannot bypass target validation.

Supervisor dispatch remains Scope/binding based. The document processor selects pending import rows rather than
broadcasting every Source to every rag. Explicit requests handle retry/reprocess with no new Source journal entry;
do not fabricate a positive `source_through` or advance a Source cursor for target-only work.

```text
pending -> running -> succeeded
              +--> retry_wait -> pending
              +--> failed / blocked
new target -> increment target generation; an old attempt becomes superseded
rag/document/Source deletion -> affected target cancelled; no further commit
```

Only one active target is processed per logical document. Identical pending input coalesces; a newer accepted file
version can supersede an older parse. Manual edits require no conflicting pending mutation. Persist the operation,
pinned input/configuration, base revision, and latest request token so restart can retry the same work. Replay of the
current token/digest returns current status; different content under that token conflicts. Superseded token versions
cannot recreate work. There is no promise of indefinite historical receipt replay.

| Boundary | Work |
| --- | --- |
| Outside transaction | OpenDAL snapshot capture; parsing/OCR; Markdown/chunk construction; embedding and prepared index projections |
| Acceptance transaction | Validate owner/lifecycle and preconditions; accept/reuse Source and target state; request existing processing intent |
| Content transaction | Recheck target generation, base head, owner/Source lifecycle, plugin profile/build and Worker fences; commit manifest, complete chunks, lineage, any system edit Source, head, prepared indexes and successful target state together |
| Deletion transaction | Retire parent Artifact or mark Source deleted; invalidate the owned targets; record recoverable fan-out state using existing invocation plus target rows |

Models and DDL do not run while holding the content transaction. The chunk projection adapter prepares from the
chunk snapshot rather than expanding the bounded manifest as if it contained full text; validate both snapshot
and projection fingerprints at commit. Existing generic JSON-field projection is not sufficient without this adapter.

A failed normal update leaves the previous committed head and indexes; no new readable-but-unindexed revision is
created. History may be read independently but retrieval uses the current indexed revision. If the shared document
family index is rebuilding or unavailable, defer new document commits and expose unavailability. Do not silently
use the generic content-only-during-rebuild path, fall back to Memory retrieval, or promise zero-downtime cutover.
Rebuild reads committed chunks, not OCR again. SQLite, SeekDB and OceanBase must prove the transaction/read behavior
through their real adapters; native ANN limitations require filtered exact retrieval or an explicit capability failure.

## 6. Editing, chunk removal, and soft deletion

An edit command supports `replace_text` and `remove` for an existing chunk, with document `If-Match`. The worker
constructs a complete new revision, revalidates count/order/digest, prepares changed indexes, and commits atomically.
Deleting the last chunk is allowed: an empty effective document has zero indexed fragments but remains a readable
manifest with provenance. Unchanged chunks keep logical IDs; rechunking assigns new IDs. Every citation includes
revision. A remove operation affects the current snapshot only, never physically deletes historical chunk rows.

At successful commit create the system Content Source with `role=lineage_only`, `operation=artifact_replace`, exact
new revision, base revision and edit/removal command. It records the modification and is not ordinary parser input.
Preserve the root document Source dependency explicitly; generic replacement does not imply this automatically.
Mark changed text `origin=manual_edit`, `verbatim=false`; original coordinates are edit evidence, not proof those
words occur in the image. Removing a chunk also counts as a manual change, protected against silent reparse overwrite.
Unchanged actual embedding input/profile may reuse computation, but new revision mappings still replace old entries.

| Delete operation | Persisted state and consequences |
| --- | --- |
| Source | Mark sidecar state deleted without mutating payload. Retire a document whose current input is that Source; revoke all matching historical revisions and edit-Source reads. A current revision solely derived from a replacement Source survives |
| Document | Retire its Artifact head; block all its revisions/chunks and pending work; original Source is not deleted |
| rag | Retire its head, immediately block all owned content, then durably mark all owned Source versions and document heads deleted/retired, including documents that never finished processing |
| Chunk | Create a new document revision without that chunk; historical content stays intact |

Retirement uses existing `lifecycle_state=retired` and governance CAS; do not add a second rag/document deletion flag.
Source deletion uses the sidecar because `pc_sources.payload` is immutable. Repeated deletion is idempotent.
A reserved document with no head is cancelled in its import row with terminal reason `document_deleted`; do not
create an empty Artifact just to retire it. That identity cannot be silently revived by another scan. Deleting a
pending replacement Source cancels its target but does not retire a committed revision based on a different Source.
Large cascades may finish in bounded batches: the retired rag and persistent ownership make incomplete fan-out
rediscoverable, while parent checks immediately prevent reads/commits. Source ownership is never shared across rags,
so deleting one rag cannot delete another's Source. Keep OpenDAL snapshots and canonical/historical content; do not
delete user files. Restore and permanent erasure are not offered initially.

Search and generic exact/history/chunk reads check owner rag, document head and that revision's root Source.
Historical base-revision links do not make a fully reparsed replacement transitively dependent on an old Source.
System edit Sources also check their target document/rag. Original Source access checks its own state and owning
rag; deleting only a document leaves that original Source readable, deleting its rag does not. Family-specific read
validation must enforce these rules; generic retirement alone can otherwise leave exact reads available.

Search indexes are derived, not historical evidence. Reuse their existing replacement/removal/rebuild maintenance;
soft deletion preserves business data, not a promise that every historical index entry is retained. Retained stale
entries must be excluded before ranking, including during cascade recovery. Parent/Source checks and commit fences
are mandatory regardless of physical index cleanup. Already delivered responses cannot be recalled.

## 7. Unified API contract

All routes below are design proposals or extensions; current schemas do not already support every family/filter.
Implement changes in `openapi/powercontext.yaml` and regenerate bindings. Reuse existing permissions, error envelopes,
Artifact/Source references, pagination, ETags and SDK/MCP operation mappings. Do not route document files through the
current generic create-Source command, whose schema only accepts built-in content; use the Source plugin protocol.

Let `R=/v1/scopes/{s}/artifacts/rag/{r}` and `D=/v1/scopes/{s}/artifacts/document/{d}`.

| Operation | Reuse or narrow extension | Semantics |
| --- | --- | --- |
| Create/configure rag | Existing POST `/v1/scopes/{s}/artifacts`, GET/PUT `R`, revisions/tags | `family=rag`; complete config replacement and existing If-Match; no parser work for empty creation |
| Import directory/file | Existing POST `/v1/source-management/add` with discovered collect capability and instance | Plugin input carries owner rag and local root/file; Source receipt retains its existing collection semantics, not a new rag import/job envelope |
| Collection status/control | Existing source-management status and target/instance controls | Do not claim collect add reschedules a stopped target or status is full batch history |
| Sources/documents | Existing Source list/get; Artifact list/get/history | Add validated owner-rag filtering where missing; document GET returns bounded manifest, not all chunks |
| Current file progress | New GET `R/processing`, cursor and state filter | Latest per-file target, stage, attempts/error, request generation and committed revision, including unprocessed reservations |
| Retry/reprocess | New POST `D/processing`; failed first imports use reserved document ID even without a head | `action`, `expected_target_generation`, `request_id`, optional discard policy; `202` current target receipt |
| Read chunks | New GET `D/revisions/{rev}/chunks` | Existing items/next_cursor convention; exact-revision effective chunks and source locators |
| Edit/remove chunk | New POST `D/edits` | Document If-Match, bounded typed operation and request_id; `202` pending target, new revision only after atomic success |
| Retrieve | Existing POST `/v1/scopes/{s}/artifacts/document/search` | Existing query/mode/limit/fusion; required validated `filters.rag_ids`; extend optional matched fragments without replacing complete Artifact results |
| Soft-delete Artifact/Source | Add DELETE to existing exact Artifact/Source resource routes | Family writer/Source lifecycle service validates ownership; Artifact `expected_generation` (governance CAS); reuse scope.contribute for document, scope.admin for rag/Source |

Rag config stores title/summary and parser/OCR/chunker settings or references, not runtime progress, members, or
main-path embedding configuration. A config change affects subsequent accepted targets; existing documents change
only through explicit reprocessing. Manual change protection still applies.

Create example, using the standard Artifact envelope:

```json
{"family":"rag","content":{"title":"Operations SOPs","summary":"Runbooks and screenshots","parser":"markdown-v1","ocr":"rapidocr-local-v1","chunker":{"max_chunk_chars":1200,"overlap_chars":100}}}
```

Directory collection example; capability/instance names and settings are illustrative discovered deployment values:

```json
{"scope_id":"project-p","capability":"knowledge-documents.collect","capability_version":"1","instance_id":"local-documents","input":{"rag":{"family":"rag","artifact_id":"ops","revision":1},"root":"/work/docs","scan_token":"scan-01"}}
```

The target rag reference is validated at acceptance; ownership persists by rag ID, while processing pins accepted
configuration. A scan token is not a document identity and does not change the Source protocol's top-level schema.
A client retries a scan with the same token; a deliberate rescan uses a new one.

Chunk removal example, POST `D/edits` with the current document ETag:

```json
{"request_id":"edit-03","operation":{"kind":"remove","chunk_id":"chunk-3"},"reason":"Remove an obsolete instruction"}
```

```json
{"document_id":"doc-1","request_id":"edit-03","target_generation":4,"state":"pending","committed_revision":2}
```

The receipt does not invent revision 3 before commit. GET processing later reports success and the committed new
revision, or failure with revision 2 unchanged. For replacement, use `kind=replace_text` plus `text`; do not represent
partial operations as complete content in generic PUT. Repeating the current request ID/digest reuses its target;
conflicting or superseded requests return conflict/precondition errors rather than silently reapply.

Single-Scope retrieval reuses the generic request:

```json
{"query":"How should I restart a follower?","mode":"hybrid","limit":10,"filters":{"rag_ids":["ops"]},"include_scores":true}
```

`results` still contains complete document Artifact manifests and lineage. Add an optional typed `matches` field to
search items for bounded exact chunk hits: chunk ID, text, locator, origin/verbatim, and fragment scores. It is a
response projection, not part of persisted Artifact content. `limit` continues to bound returned Artifacts;
per-document fragment count has a separate capability limit. Validate only advertised document filters/options;
unsupported rerank is an explicit capability error. Neither unknown filters nor omitted rag selection may widen scope.

For cross-Scope queries, the client/helper accepts explicit `(scope, rag)` targets, authorizes all of them, groups
calls to the same existing search operation and fuses ranks. Preserve document/chunk identity and per-target errors;
do not compare incompatible raw scores or silently return no matches on failure. This phase does not add a separate
`/rag/query` wire protocol. HTTP, Python and MCP use the same family operations, with bounded orchestration where needed.

| Condition | Response |
| --- | --- |
| Missing/stale document/config ETag | Existing 428/412 precondition errors |
| Conflicting target generation/request, pending edit or discarded-edit policy missing | 409 with actionable code |
| Deleted resource or evidence | 410 after authorization; no retained text in errors |
| Invalid format/config/oversized input | 422; transport may reject oversized bodies with 413 |
| Index/model unavailable or rebuilding | 503; no silent full-text-only downgrade or alternate-family search |
| Accepted processing fails | Latest target becomes failed/blocked/retry_wait; Source acceptance remains distinct from success |

Use the existing error shape, for example:

```json
{"error":{"code":"resource_deleted","message":"The requested document is unavailable.","details":null}}
```

## 8. Implementation and acceptance

Framework reuse still requires a typed document Source acceptance hook, per-file selection in Workers, a
chunk-backed projection adapter, filtered retrieval before top-k, lifecycle checks on all reads, and same-transaction
family writers. Reuse tables does not justify storing full documents in manifests or treating generic JSON projection
as already sufficient. No changes to core metadata columns are planned; additive domain migrations and OpenAPI
extensions must be reviewed if the implementation discovers a genuine gap.

On each of SQLite, SeekDB and OceanBase, test the following through public operations:

- Create two rags in one Scope and another in a different Scope; same file imports have exclusive owners and no query leakage.
- Capture Markdown from a pinned `oceanbase/oceanbase` commit and real OCR fixtures; preserve image references and accurate locators.
- Identical success skips, failed identical input retries, changed bytes revise, config changes reprocess, and embedding-only changes do not OCR.
- Reject stale workers and conflicting edits; restart from durable current targets; content/index failure exposes no half-published revision.
- Delete a chunk by revision replacement, including the last chunk; old snapshots remain complete and current indexes exclude it.
- Source/document/rag soft deletion, cascading completion after restart, historical/edit-Source read checks, and new-Source revisions surviving old-Source deletion.
- Plugin config initialization from compatible main settings without live coupling; isolated indexes and explicit unavailable rebuild behavior.
- Core schema compatibility, filtered hybrid search, pagination and contract generation; mocks alone do not establish real backend/OCR support.

Use existing #2441 for ownership/ingestion, #2442 for processing/content/index integration, #2443 for retrieval/interfaces,
and #2444 for diagnostics/three-backend acceptance. One implementation branch/PR may deliver these four review stages.
This RFC PR does not close implementation issues. Do not add issues or claim large-corpus capacity from functional fixtures.

# Drawbacks

Complete chunk snapshots amplify storage and writes. Retaining soft-deleted originals/history requires capacity
monitoring. Shared document-family rebuilds can interrupt retrieval across rags in one Scope. Latest per-file state
is simpler than a task center but lacks complete batch/command history. Plugin-specific read/projection/acceptance
integration is still substantive work even with unchanged core schemas. OCR packaging/platform support needs testing.

# Rationale and alternatives

- A Scope per knowledge base couples collection organization to authorization; multiple rag Artifacts preserve project grouping.
- Sharing one logical Source across rags makes deleting a rag affect others; share only physical snapshot bytes if desired.
- Storing chunks in Sources violates immutable evidence; one Artifact per chunk obscures whole-document revision semantics.
- Whole-document JSON arrays simplify serialization but make retrieval/detail reads unnecessarily large.
- Separate rag runtime, publication generations and general job tables support advanced features but are unnecessary for atomic revision/index commits.
- Direct SQL deletion of a chunk breaks old revision counts, digests and citations; revision replacement preserves evidence.
- A live fallback to main-path embedding settings can silently change vector space; plugin-owned resolved configuration avoids it.

# Prior art

Build on [Source plugins](2012-source-plugin-system.md), [Artifact plugins](2175-artifact-plugin-system.md),
[Source identity](1400_source_definition_and_observation_model.md), [processing supervision](1515_artifact_processing_supervisor.md),
and [Source/Artifact APIs](1437_source_artifact_rest_api.md). Existing OpenDAL capture already compares path/content hashes;
Artifact head governance and generic fragment indexes provide reuse foundations, not a complete knowledge-base implementation.
Atomic Memory retirement is a useful lifecycle pattern but is not already a Source-to-document cascade contract.

# Unresolved questions

Implementation must fix precise capability signatures, supported image formats/model versions, resource limits,
backend migrations/transaction behavior, and defaults validated against actual embedding token limits. The three-table
baseline must be verified rather than expanded preemptively. Any core schema change needs a demonstrated gap and review.

Permanent erasure, restoration, retention/GC, renamed-file identity, complete batch history, independent per-rag
embedding/rebuilds and full edited-Markdown export need separate contracts. Overlapping chunks cannot simply be
concatenated into an accurate edited document.

# Future possibilities

Additional OpenDAL providers, parsers, richer chunkers, rerankers and bounded agentic retrieval may extend the component
boundaries. Measured storage pressure may justify shared immutable chunk bodies. None is required for first-phase
ownership, duplicate detection, atomic commits or exact evidence.
