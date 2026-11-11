---
title: "RFC 2570: Document knowledge bases with rag Artifacts"
description: "OpenDAL ingestion, versioned document chunks, and knowledge-base retrieval using Source journals, shared Workers, and Source lifecycle metadata."
---

- Proposal Name: `rag_knowledge_base`
- Start Date: 2026-11-10
- RFC PR: [#2570](https://github.com/oceanbase/powercontext/pull/2570)
- Tracking Issue: [#2440](https://github.com/oceanbase/powercontext/issues/2440)
- Related Issues: [#2441](https://github.com/oceanbase/powercontext/issues/2441), [#2442](https://github.com/oceanbase/powercontext/issues/2442), [#2443](https://github.com/oceanbase/powercontext/issues/2443), [#2444](https://github.com/oceanbase/powercontext/issues/2444)

# Summary

Provide document knowledge bases through a Source plugin and the `document` and `rag` Artifact families. A Scope
contains multiple user-created rags; each document Source and whole-document Artifact belongs to exactly one rag.
OpenDAL captures immutable originals. Markdown and image OCR share simple chunking. Asynchronous operation requests
are immutable Sources consumed through the existing journal, cursor, and Worker framework. Commit each document
revision, its chunks, lineage, indexes, and consumption cursor atomically.

Add one document chunk table and generic soft-deletion fields to `pc_sources`. Reuse Artifact storage and processing
schemas without a separate import/task/state platform. Expose content availability rather than durable per-file
execution status. This RFC proposes a contract; it does not describe released functionality.

# Motivation

Business descriptions and SOPs need passage retrieval over complete documents, exact provenance, explicit updates,
and predictable deletion. Memory extraction does not preserve every passage. The first phase should cover that
workflow with existing Source/Artifact infrastructure and explicit failure semantics.

Source snapshots, accepted operation requests, and successful Artifact revisions serve different purposes. Keep
those distinctions without mutable task state in Source payloads. Retrieval is derived from committed chunks and
isolated from Memory indexes and configuration. Reuse requires actual family, read, projection, and Worker integration.

# Guide-level explanation

1. Create an empty `rag` through standard Artifact creation in an existing Scope.
2. Select the rag and a local directory/file through the configured OpenDAL Source plugin.
3. View whether a usable document exists: generated, update needed, or not yet generated. No result does not prove failure.
4. Search selected authorized rags. A failed update leaves the previous committed content searchable.
5. Import changed files or explicitly reparse. Parsing restarts from a fixed original and commits a whole new revision.
6. Edit or remove a chunk asynchronously. The request is accepted first; a successful new revision retains old history.

Users need not supply file hashes, namespaces, or document IDs. Scope remains the authorization boundary. Importing
the same file into two rags creates different logical Sources/documents, with optional physical snapshot deduplication.
Support Markdown and independently imported text images using RapidOCR with ONNX Runtime. Markdown image references
are retained without fetching or OCR. SQLite, SeekDB, and OceanBase must support the workflow, one selected backend
per deployment.

PDF, Office, plain TXT ingestion, semantic/LLM chunking, complex layout reconstruction, scheduled directory sync,
per-rag embedding spaces, zero-downtime index cutovers, a task-management UI, durable per-file failure details,
restoration/permanent erasure, and large-corpus performance acceptance are outside the first phase.

# Reference-level explanation

## 1. Ownership and plugin configuration

```text
Scope
  rag R1
    original Source S1 -> operation Source Q1 (import) -> document D@1 -> chunks
    operation Source Q2 (edit D@1) -> document D@2 + successful edit evidence E2
    original Source S2 -> operation Source Q3 (reparse) -> document D@3
  rag R2
    separate original Sources, requests, and documents

Source plugin: OpenDAL capture, snapshot access, original and trusted operation Source types
Document plugin: Markdown / OCR, chunks, revisions, operation consumption, provenance and chunk index lifecycle
rag plugin: collection configuration and retrieval orchestration over document chunks
Shared facilities: Source/Artifact APIs, Supervisor/Worker, cursor, transactions, database/model adapters
```

The three logical plugins may ship in one package. The application service coordinates them; it is not a fourth
plugin. A rag can exist without Sources. Its configuration uses Artifact revisions, and deletion uses head governance.
The knowledge-base Scope enables only document business: document Sources, document Artifacts, and pre-created rag
Artifacts. Operation and edit-evidence Sources are internal records of that same pipeline, not inputs to other families.
Scope admission and consumer bindings enforce this restriction; no new Scope table is required.
There is no rag runtime table. Ownership is explicit in immutable Source payloads and document manifests, not inferred
from tags or evidence lineage. Each original, request, and document has one owner rag.

The package owns independent embedding/index configuration. Explicit plugin settings win; installation may initialize
compatible missing values from the main path and persist the resolved profile and credential references. Do not copy
secrets into Artifacts or follow subsequent main-path changes. Parser/OCR/chunker configuration may vary by rag;
the initial embedding/index profile is plugin-level, shared by the document family within each Scope. Isolate indexes
by family, definition, and vector-space identity. Model/dimension changes use the existing family rebuild mechanism.

Only the document pipeline may directly consume document originals; only its operation processor may execute
operation Sources. Enforce eligibility during selection, execution, and commit. Registering a new Source type alone
does not exclude it from existing generic consumers. Requests are neither ordinary generation input nor searchable text.

## 2. OpenDAL ingestion, identity, and duplicate detection

Use OpenDAL/`opendalfs` for local listing/reads and immutable snapshots in a managed directory. The plugin instance
must run where the selected path exists. Fix root, relative-path, and symlink normalization; reject escapes. Capture
before acceptance, hash the exact saved bytes, and parse that snapshot, not a later read of a changing file. mtime/size
are hints, not proof of equality. Preserve the first canonical capture metadata when reusing an existing Source.

The first phase supports only locally accessible directories, using the OpenDAL `fs` backend. The importer, Server,
and Worker must access the same managed snapshot directory, directly or through local container volume mappings.
Check storage configuration/access before accepting processing requests. Store `original_path` for provenance and
`snapshot_ref={store_id, object_key, digest}` for actual reads; resolve the storage alias to each process's configured
local root. Worker reads the captured snapshot even if the user later edits or removes the original file. Remote
client uploads, cross-machine file transfer, object-storage originals, and remote shared storage are outside this phase.

```text
file_key       = (scope_id, rag_id, collection_id, normalized_relative_path)
document_id    = stable_hash(identity_version, file_key)
content_digest = SHA256(captured raw bytes)
source_key     = stable_hash(source_schema_version, file_key, content_digest)
processing_key = hash(parser/OCR/model/normalizer/chunker versions and settings)
index_key      = hash(complete embedding inputs, plugin profile and projection versions)
```

Derive collection from the registered storage instance/binding and normalized root, not a scan token. Verify full
identity fields on hash matches; never truncate paths or merge documents by digest alone. Original payloads store
owner rag, file identity, original path, document ID, media type, digest, and snapshot reference. The same document ID
is available before an Artifact exists; no reservation row is required.

| Observation | Required behavior |
| --- | --- |
| Same file/content/processing profile, usable successful result | Reuse Source/document; skip parsing, check index readiness separately |
| Equivalent request still unconsumed | Coalesce when found in the unconsumed journal; racing duplicate imports recheck success before writing |
| Same input, no successful result | An explicit retry may create a new request; a capture checkpoint is not processing success |
| Same path, changed bytes | New original Source, same document ID, whole-document revision on success |
| Parser/OCR/chunker changes | Reuse original Source; explicitly request reprocessing |
| Only embedding/index changes | Reindex committed chunks without OCR |
| Different path/rag, same bytes | Different logical Source/document; optional physical snapshot reuse |
| Unchanged original after manual edits/removal | Preserve the manual revision; do not compare its edited text hash as if it were the original hash |
| Reprocessing an edited document | Require explicit `manual_edits_policy=discard`; otherwise retain current content and reject the conflict |

Reverting to old bytes can reuse an older Source, so desired input follows the latest accepted import/reprocess request,
not the largest original Source journal position. Finding a Source is not proof that the current document matches it.
Renames create new identities initially; absent files do not imply deletion. Deleted Source identities and retired
document heads must not be silently resurrected.

Parser/chunker choice is not part of document identity. Explicit reprocessing of the same Source with a new splitting
method keeps the same `document_id` and `rag_id`, and commits the next revision only after content/index preparation
succeeds. It replaces the current effective content/indexes, not historical rows. Failure leaves the old head usable;
manual modifications still require explicit discard authorization. Rechunking may assign new chunk IDs.

Reuse Source `collect` discovery/add/status/control. A client-generated `scan_token` in plugin input identifies one
explicit scan; retrying that input reuses its collection target. A new explicit scan has a new token but the same
file identities. Scan once per token; do not imply periodic sync or that repeated add restarts a stopped target.
Collection status describes acquisition only. Scans consult successful document results as well as capture checkpoints.

## 3. Markdown normalization and chunking

```text
Markdown bytes -> markdown-it-py structure / byte mapping ----+
                                                             +-> rule-based chunks
Image -> RapidOCR text/regions -> normalized Markdown --------+
```

OCR preserves actual text, order, confidence, and original-image regions, including preprocessing transforms.
Normalization escapes Markdown syntax and groups lines/paragraphs; it does not invent headings, use LLM correction,
or reconstruct complex tables. Map normalized spans back to OCR lines and image regions, stating locator precision.
Derived Markdown never replaces the original image Source.

Use headings, paragraphs, lists, and code blocks. Merge short blocks up to `max_chunk_chars`; split oversized blocks
by sentence/length, optionally using `overlap_chars`. Preserve heading paths, language, and continuation flags.
Overlap must be smaller than the maximum. Example values 1200/100 need validation; actual embedding token limits,
including heading/prefix context, must be enforced without silent truncation.

Validate formats, UTF-8, image decoding/pixel limits, OCR models, timeouts, and no-text outcomes. Pin/test real RapidOCR,
ONNX, and model versions. Empty OCR is not successful empty-document ingestion. An explicit removal of the last chunk
is allowed. No separate section-tree, parsed-node table, or complete Markdown cache is required. Store bounded source
mappings in chunk metadata; keep vectors out of canonical chunks and full text out of the Artifact manifest.

## 4. Persistence: one detail table and Source lifecycle fields

Do not add `pc_document_imports` or `pc_document_source_states`. Source payloads carry immutable identities/requests;
Artifacts carry successful revisions; existing processing tables coordinate consumption. Do not replace the removed
tables with unbounded maps in rag content, Scope settings, checkpoints, or cursors.

| Reuse | Responsibility and structural change |
| --- | --- |
| `pc_sources` | Originals, operation requests, successful edit evidence; add generic lifecycle fields below |
| Source journal/heads, connector checkpoints | Acceptance order and acquisition progress; unchanged schema |
| `pc_source_cursors` | Document binding's contiguous scan position and CAS generation; unchanged schema |
| Processing intents, leases, binding state | Scope/binding wakeup, invocation generations, execution fences; unchanged schema, not per-file tasks |
| `pc_artifacts`, `pc_artifact_heads` | rag configuration, document manifests, current revision and retirement/governance; unchanged schema |
| Source/Artifact lineage, locators, tags | Original input, request, base revision, successful edit evidence; existing schema |
| Artifact search states/coverage/changes/text/vectors | Current-revision fragment indexes and build coordination; existing facilities plus a chunk projection adapter |

Add these columns to `pc_sources`:

| Column | Type/default | Meaning |
| --- | --- | --- |
| `lifecycle_state` | Short string, NOT NULL, default `active` | `active` or `deleted`, one-way transition initially |
| `deleted_at` | Nullable UTC timestamp | First deletion time; repeat deletion does not refresh it |

Backfill existing Sources as active. Payload, identity, and journal position remain immutable. Lifecycle metadata is
excluded from content hashing. Use row locking/conditional updates and consistent lock ordering against document
commits; no separate Source deletion-generation column is required initially. Adapt add/get/list, evidence and generation
access, and family reads. Duplicate add of a deleted identity must not return usable content or reactivate it. Internal
migration/cascade scans may include deleted rows; public reads cannot bypass deletion. This phase exposes deletion for the knowledge-base pipeline's originals and
internal records only, not unrelated Source types or cross-family cascades. Verify additive migrations on
SQLite, SeekDB, and OceanBase rather than treating Artifact retirement as existing Source soft-deletion support.

The only new domain table is `pc_document_chunks`:

| Constraint/fields | Design |
| --- | --- |
| Primary key | `(scope_id, document_id, revision, chunk_id)` |
| Unique order | `(scope_id, document_id, revision, ordinal)` |
| Content | `text`, `content_digest` |
| Structure | Heading path, continuation/overlap metadata |
| Provenance | Normalized spans, original byte/image locators, origin/verbatim, base/edit references |

Use backend-safe keys and bounded metadata. Each revision is a complete immutable chunk snapshot: removing one of
100 chunks produces a new manifest and 99 rows, preserving the original 100. The existing Artifact envelope identifies
the document/revision; its bounded manifest contains title, owner rag, file identity, root SourceRef, processing
fingerprint, chunk count/digest, manual-change flag, and `applied_request_ref`. A client derives the chunk-directory
route from the exact document reference. Do not embed chunk bodies or an unbounded list of chunk IDs in the manifest.
History uses exact revisions, not today's head. Chunk rows also supply `text_length`, measured in Unicode code points,
for metadata-only directory reads without returning body text.

Initially, membership and availability may require paginated Scope/type scans and payload decoding plus exact
Artifact reads by deterministic document ID. Do not assume canonical payloads already have queryable JSON owner/path
indexes. Filtered pagination tracks the scan position without losing later matches. Validate ownership and full identity
at writes/reads. These scans have costs; no large-scale performance claim is made. Additional projections require measured
need, not an implicit reintroduction of a task table.

## 5. Operation Sources, Workers, and transaction boundaries

A trusted application service creates a dedicated operation Source type, illustrated as `document_operation`;
originals are illustrated as `document_file`. These are plugin registrations, not new storage tables. A request stores
owner rag, document ID, operation, root SourceRef, fixed processing configuration/fingerprint, request ID, base revision,
and bounded edit/discard parameters. Initial import may have no base; edit requires an existing base. Do not store
mutable state, attempts, errors, or a future output revision in its payload. Ordinary user metadata cannot forge commands.

Import/reprocess regenerates the whole document from a fixed original. Edit supports `replace_text` and `remove`.
Existing `ContentSourceInternal` binds successful `artifact_create`/`artifact_replace` evidence to an exact revision;
it cannot represent an unexecuted command. Preserve that successful-result contract and use the separate request type.

Accept/reuse the original, insert the request, and call the document binding's `processing_intents.request()` in one
transaction. Only a newly accepted request schedules new work. Extend Source/binding notification integration explicitly;
the current SourceRepository does not automatically discover this proposed processor.

Request identity is deterministic within Scope/rag/document/operation and client `request_id`. Identical canonical
payload replays return the accepted request; different payload under the same identity conflicts. Replaying a consumed
request never reruns it. An explicit retry uses a new request ID, possibly the same original Source. Incrementing an
invocation counter alone cannot rerun an input already passed by the cursor.

```text
accept request Source + processing intent atomically
                       |
existing Worker scans the document binding's journal window in order
                       |
prepare chunks/embeddings outside the transaction
                       |
commit document + chunks + lineage + indexes + cursor atomically
```

Process sequentially within a Scope/document binding, using bounded windows and existing fences/cursor CAS. Ignore
unrelated Sources while advancing the contiguous scan boundary. No per-file queue or latest-request-wins cancellation
is promised. Requests pin their base; if an earlier operation changes the head, a later request may conflict instead
of silently rebasing. Users refresh and submit a new request. Source acceptance during execution does not mutate the
running request. Scope invocation acknowledgement follows existing `ScopeInvocation` rules; partial work must not
mark an entire window clean.

The cursor records consumption, including skips, not success. Successful revisions identify their request through
`applied_request_ref` and lineage. There is no durable per-request failure receipt.

| Outcome | Cursor and recovery |
| --- | --- |
| Input-specific parse/OCR/configuration failure or base conflict | Log sanitized identifiers/error, advance past that request under the execution fence, retain old content, continue other inputs |
| Transient request-local error | Optional bounded in-process retry, then the same failure/skip rule; no durable attempt counter |
| Database failure, index rebuild, or system-wide model unavailability | Do not advance this request; reuse Supervisor backoff/recovery and report binding-level unavailability |
| Process exits before commit | Cursor has not advanced; re-execute the fixed request after recovery |
| Process exits after successful commit | Content and cursor are already committed; do not create another revision |
| Crash between failure logging and skip commit | The request may run again; duplicate diagnostics are allowed |

Logs follow existing observability contracts and exclude content, credentials, and sensitive paths. Logs are not a
recovery dependency. A consumed failed request requires a new explicit request to retry. Runtime task status or a
request timeout cannot establish permanent failure.

| Boundary | Work |
| --- | --- |
| Outside transaction | Snapshot capture; parsing/OCR; complete chunk snapshot; embedding and index projection preparation |
| Acceptance transaction | Authorization, ownership/lifecycle/base checks, Source/request acceptance, processing intent |
| Successful content transaction | Recheck Worker/cursor/base/owner/root/index fences; commit manifest, chunks, lineage, successful edit evidence when needed, head, indexes, and cursor |
| Failure-skip transaction | Revalidate execution/cursor fences and advance the contiguous boundary; no document write or successful edit evidence |
| Deletion transaction | Mark Source lifecycle or retire Artifact head; parent/root checks block concurrent publication |

Models and DDL run outside the content transaction. Chunk projections are prepared from chunks, not full-text JSON
inside the manifest. Validate snapshot and projection fingerprints. New content and indexes become visible together;
failed updates retain the previous head/indexes. During family rebuild defer new document commits, not the generic
content-only path. Rebuild committed chunks without OCR, with explicit unavailability rather than Memory/full-text
fallback or a zero-downtime promise. Verify real transaction behavior on all backends, including SQLite write locking;
ANN limitations need filtered exact retrieval or a clear capability failure.

The document plugin owns one revision-aware chunk projection boundary used by initial writes, revision replacement,
rebuild, coverage verification, deletion filtering, and retrieval. Rebuild must load the exact committed chunk rows,
not expand the bounded manifest as full text. Coverage compares the manifest count/digest with that revision's chunks
and projections. A zero-chunk revision has valid zero-fragment coverage. Retrieval retains exact document revision
and chunk IDs through scoring/fusion so later chunk reads resolve the actual hit. Generic projection/rebuild code
needs this integration; adapting only the initial write path is insufficient. The rag service reuses these projections
without changing generic Artifact search result contracts.

## 6. Editing, chunk removal, and deletion

Validate edit `If-Match` both at acceptance and commit. A successful edit writes a whole new snapshot. Unchanged chunks
keep logical IDs; rechunking assigns new ones; citations always include revision. Removing the last chunk leaves a
readable zero-chunk manifest and no current index fragments. No removal physically deletes old chunk rows.

At success, create a system Content Source with `role=lineage_only`, `operation=artifact_replace`, and the exact new
revision, recording request/base/root provenance. Preserve the original Source dependency explicitly. Mark edited
text `origin=manual_edit`, `verbatim=false`; old image coordinates are edit evidence, not verbatim proof. Removal
also counts as manual modification. Explicit reparse must authorize discarding manual changes. Unchanged complete
embedding inputs may reuse computation, but index mappings must identify the new current revision.

| Delete operation | Behavior |
| --- | --- |
| Original Source | Set Source lifecycle deleted; retire a document still rooted in it and revoke dependent history. A revision fully reparsed from another root survives |
| Document | Retire its head; block all revisions/chunks and future commands, leaving original Sources active |
| rag | Retire its head, immediately block all owned content, then recoverably mark all owned Sources (including requests/edit evidence) and document heads deleted/retired |
| Chunk | Commit a new revision without that chunk, retaining history |

Reuse Artifact `lifecycle_state=retired` and governance CAS. Original Source payloads/snapshots and historical business
content remain stored; user files are untouched. No restore or permanent erase initially. An imported file without a
head is deleted through its original Source; document DELETE returns not found rather than manufacturing a placeholder
Artifact. A deleted pending replacement Source must not retire an older committed version based on a different root.

Check owner rag, document head, and the exact revision's root on search and generic/exact/history/chunk reads.
Historical base links do not transitively revoke a full reparse from a replacement root. Original Source reads check
its state/owner; deleting just a document does not hide its original. Request/edit evidence reads also check the target
document to prevent retained manual text from bypassing deletion. Deleting a request blocks unconsumed execution,
not an already committed edit: content validity is governed by root/document/rag, not a retroactive cancellation.

Generic retirement alone may still allow exact reads; add family guards. Large rag cascades rediscover remaining work
from the retired parent and immutable ownership, including deleted rows internally, without a cascade job table.
Exclude invalid rows before ranking even while fan-out is incomplete. Derived indexes retain existing replacement,
removal, and rebuild semantics; preserving business history does not promise every historical index entry stays intact.

## 7. Availability, Artifact APIs, and chunk access

| Display | Derivation |
| --- | --- |
| Generated | A usable committed revision matches the selected original/configuration; valid manual changes are preserved |
| Update needed | A usable older revision exists but desired original/configuration differs; it may still be searchable |
| Not yet generated | Source accepted, no usable successful revision; could be unstarted, running, or failed |
| Deleted | Source or owning Artifact lifecycle invalid; no retained text, normally excluded from lists |

Desired input uses the latest accepted import/reprocess request, or the accepted original if none exists. Manual edits
are not raw-file mismatches. Rag configuration changes affect future requests and may indicate reprocessing is needed;
they do not automatically enqueue it. Do not expose per-file stages, percentages, attempt counts, durable errors, or
infer success from collection completion/cursor advancement. Binding diagnostics and index availability stay separate.

Reuse existing permissions, errors, references, pagination, ETags, and SDK/MCP operations. Implement necessary contract
changes in `openapi/powercontext.yaml` and regenerate bindings. The current generic create-Source accepts built-in
content; document files go through the Source plugin protocol. Let `R=/v1/scopes/{s}/artifacts/rag/{r}` and
`D=/v1/scopes/{s}/artifacts/document/{d}`.

| Operation | Existing route or necessary extension |
| --- | --- |
| Create/configure rag | Existing POST `/v1/scopes/{s}/artifacts`, GET/PUT `R`, revisions/tags; full replacement and existing preconditions |
| Import/control | Existing POST `/v1/source-management/add`, collect capability/instance, existing status/target/instance controls |
| List files and available results | Existing Source list/get and Artifact list/get/history, validated owner-rag filters, helper join by document ID; no `R/processing` task list |
| Explicit reparse | POST `D/processing`, `source_ref`, `action=reprocess`, `request_id`, optional configuration/discard; 202 request acceptance |
| Edit/remove chunk | POST `D/edits`, current `If-Match`, typed bounded operation and request ID; 202 request acceptance |
| Get document | Existing Artifact GET/history; title, exact revision, owner, chunk count and original reference, no chunk bodies |
| Chunk directory | GET `D/revisions/{rev}/chunks`; paginated ID/order/heading-path/text-length metadata only |
| Get one chunk | GET `D/revisions/{rev}/chunks/{chunk_id}`; body, provenance and manual-edit markers |
| Get chunks in a batch | POST `D/revisions/{rev}/chunks/batch-get`; bodies for an explicit bounded list of chunk IDs |
| Knowledge-base retrieval | New POST `/v1/scopes/{s}/rag/retrieve`; explicit `rag_ids`, query/mode/limit, chunk-reference results |
| Soft-delete | DELETE on existing exact Source/Artifact routes; Artifact governance `expected_generation`, one-way Source lifecycle |

Reparse with an existing head requires `If-Match`; without a head, use the deterministic document ID and
`If-None-Match: *`. Validate that Source ownership/file identity resolves to that ID, and recheck the base at execution.
Do not return Scope invocation generations as document target generations. Reuse `scope.contribute` for document
operations and `scope.admin` for rag/Source deletion, with existing read authorization.

Create rag:

```json
{"family":"rag","content":{"title":"Operations SOPs","parser":"markdown-v1","ocr":"rapidocr-local-v1","chunker":{"max_chunk_chars":1200,"overlap_chars":100}}}
```

Directory collection; capability/instance names are illustrative deployment-discovered values:

```json
{"scope_id":"project-p","capability":"knowledge-documents.collect","capability_version":"1","instance_id":"local-documents","input":{"rag":{"family":"rag","artifact_id":"ops","revision":1},"root":"/work/docs","scan_token":"scan-01"}}
```

Remove a chunk with POST `D/edits` and the current document ETag:

```json
{"request_id":"edit-03","operation":{"kind":"remove","chunk_id":"chunk-3"},"reason":"Remove an obsolete instruction"}
```

The 202 receipt states durable acceptance, not pending status or a future revision:

```json
{"accepted":true,"document_id":"doc-1","request_source":{"source_type":"document_operation","source_id":"request-03"},"current_revision_at_accept":2}
```

`current_revision_at_accept` is an observation, not the output. Confirm success through a revision's
`applied_request_ref`; no matching revision does not prove failure. For a new execution, refresh preconditions and
use a new request ID. Identical replay returns the original acceptance fact, not a fresh execution result.

Explicit reparse, with `If-Match` for an existing document and authorization to discard manual changes:

```json
{"action":"reprocess","request_id":"reparse-04","source_ref":{"source_type":"document_file","source_id":"source-1"},"manual_edits_policy":"discard"}
```

Document GET and exact history retain the existing Artifact envelope with the bounded manifest. The directory
returns `items/next_cursor`, ordered by ordinal, with no body or excerpt. Its cursor binds Scope/document/revision;
concurrent head changes cannot mix revisions. Single and batch reads use that same exact revision, never today's head.
All three routes enforce owner/document/root lifecycle checks, including historical reads. A zero-chunk revision has
an empty directory. Single-item output includes text, source reference/locator, origin/verbatim and edit provenance.

Batch-get accepts a nonempty unique `chunk_ids` list, returns items in request order, and does not accept mixed
revisions. Enforce configured ID-count and response-byte limits; reject oversized batches without truncating text.
Authorize the parent first, then validate every ID in that revision. A missing ID fails the batch with 404 rather
than silently omitting it; deleted resources follow the existing authorized 410 rule. These are read operations,
not a general bulk mutation API.

Example directory response for one exact revision:

```json
{"document_ref":{"family":"document","artifact_id":"doc-1","revision":2},"items":[{"chunk_id":"chunk-1","ordinal":0,"heading_path":["Operations","Restart"],"text_length":128}],"next_cursor":null}
```

Batch request to POST `D/revisions/2/chunks/batch-get`:

```json
{"chunk_ids":["chunk-1","chunk-2"]}
```

Knowledge-base retrieval uses POST `/v1/scopes/{s}/rag/retrieve`, not the generic Artifact search response:

```json
{"rag_ids":["ops"],"query":"How should I restart a follower?","mode":"hybrid","limit":10}
```

Return bounded chunk hits with rag ID, exact document reference, chunk ID, score, and source locator; optional snippets
are bounded and do not replace the full-body chunk API. Here `limit` counts chunk hits. For example:

```json
{"hits":[{"rag_id":"ops","document_ref":{"family":"document","artifact_id":"doc-1","revision":2},"chunk_id":"chunk-1","score":0.82,"source_ref":{"source_type":"document_file","source_id":"source-1"},"locator":{"heading_path":["Operations","Restart"]}}]}
```

The generic Artifact GET/history and search contracts remain intact: Artifact searches return Artifacts, not arbitrary
chunks or another family's documents. A document-specific internal handler can reuse index/model facilities; the
separate rag retrieval route avoids adding knowledge-base matches to every generic search item. Require nonempty
validated rag selection and filter ownership/lifecycle before ranking. Unsupported filters/rerank options fail clearly.
Cross-Scope helpers authorize explicit `(scope, rag)` targets, call the same per-Scope rag retrieval route, rank-fuse,
and retain target errors/identities. Do not compare incompatible raw scores. HTTP, Python and MCP expose equivalent
metadata, chunk-read and retrieval operations, reusing common authorization and errors.

At acceptance use existing 428/412 preconditions, 409 payload/discard conflicts, authorized 410 deletion, 422 validation
(or transport 413), and 503 service unavailability with the existing `error.code/message/details` envelope. After 202,
request-local failures/conflicts follow the log-and-consume rule; there is no durable error-status endpoint.

## 8. Implementation and acceptance

Reuse requires Source lifecycle migrations/read guards, trusted request admission, consumer eligibility, document
binding notification, journal selection/cursor transactions, chunk projection, and ownership filtering before top-k.
It does not imply a zero-change framework or existing support for all proposed routes.

Run the same public workflow against SQLite, SeekDB, and OceanBase:

- Multiple rags and Scopes, exclusive file ownership, authorized selection and no cross-rag results.
- Markdown from a pinned `oceanbase/oceanbase` commit and real OCR fixtures with accurate provenance.
- Source migration backfill, immutable payload, deleted replay rejection, and exact/history/evidence read guards.
- Successful duplicate skipping, failed-input retry with a new request, configuration reparse, embedding-only rebuild.
- Atomic request/intent admission, restart recovery, atomic content/cursor commit, no rerun on request replay.
- Input-local failure consumption versus system-wide failure retention; cursor advancement never means file success.
- Async base conflicts/late Workers, no successful edit evidence on failure, manual-edit protection.
- Versioned chunk removal including the last chunk, complete history and correct current indexes.
- Source/document/rag deletion races, recoverable cascade, replacement-root independence.
- Independent plugin configuration, index isolation, rebuild unavailability, pagination, contract generation and SDK/MCP parity.
- Metadata-only document/directory reads, exact-revision single/batch chunk bodies, byte/count bounds and missing-ID behavior.
- Same-Source rechunking keeps document/rag identity while replacing the current revision and retaining history.
- Chunk projection/rebuild/coverage parity, including zero chunks and exact retrieval-hit resolution.
- Local OpenDAL snapshots accessible to importer/Worker, including container mappings; no remote-path acceptance.

Mocks do not establish real backend/OCR support. Use #2441 for ingestion/ownership/Source lifecycle, #2442 for processing,
requests/chunks/atomic writes, #2443 for retrieval/interfaces, and #2444 for diagnostics/acceptance. One implementation
branch/PR may deliver these stages. This RFC does not close implementation issues or establish large-corpus capacity.

# Drawbacks

Operation Sources grow the journal; complete chunk snapshots and retained deleted data consume storage. Sequential
consumption can cause head-of-line waiting. Availability lacks durable per-request failures, and initial owner/request
filtering may scan payloads. Family rebuilds can interrupt several rags. These are explicit first-phase tradeoffs;
Source lifecycle and consumer/projection integration still require meaningful implementation work.

# Rationale and alternatives

- Multiple rag Artifacts preserve project-level Scopes without coupling collections to authorization.
- Source journals persist accepted operations without a new mutable task table; in-memory-only commands lose work on restart.
- Request Sources preserve intent; exact successful edit Sources preserve results. Reusing the latter before success misstates provenance.
- Source lifecycle columns express generic deletion while payloads remain immutable; a document-only state sidecar duplicates this responsibility.
- Whole-document manifests plus chunk rows avoid giant content JSON and per-chunk Artifact identities.
- Immutable revision replacement preserves old counts/digests/citations; direct chunk deletion does not.
- Separate task/publication systems could provide richer status/cutovers, but are outside the accepted scope.

# Prior art

Build on [Source plugins](2012-source-plugin-system.md), [Artifact plugins](2175-artifact-plugin-system.md),
[Source identity](1400_source_definition_and_observation_model.md), [processing supervision](1515_artifact_processing_supervisor.md),
and [Source/Artifact APIs](1437_source_artifact_rest_api.md). Existing OpenDAL hashing, Source cursor CAS, Artifact
head governance, and fragment indexes provide foundations, not a complete knowledge-base implementation. Existing
Atomic Memory retirement is not already a Source-to-document cascade contract.

# Unresolved questions

Fix exact Source/capability signatures, supported image/model versions, limits/chunker defaults, bounded retry error
classification, and backend migration/index transaction behavior during implementation. Additional query projections
must be justified by measured costs. Durable per-request outcomes, restore/erasure/GC, rename identity, per-rag vector
spaces, and full edited-Markdown export need separate contracts; overlapping chunks cannot simply reconstruct a full edit.

# Future possibilities

Extend OpenDAL providers, parsers, chunkers, rerankers, and bounded agentic retrieval through component boundaries.
Measure before adding shared immutable chunk bodies, richer task state, or collection-scale projections. None is
required for the first phase's ownership, recoverable requests, versioned content, and exact evidence.
