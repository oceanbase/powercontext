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
| Processing records | Durable target requests, stage/results, retries, leases, and recoverable indexing/cleanup work |

Reuse Artifact, head, lineage, tag, and processing facilities where their semantics match. Do not add duplicate
knowledge-base/document/revision master tables or mix document rows into family-specific Topic Memory chunk tables.
Physical names and backend-specific auxiliary indexes are implementation decisions.

Each revision initially stores a **complete chunk snapshot**. A 100-chunk document has one manifest and 100 detail
rows; editing one chunk creates another manifest and a complete 100-row snapshot. Validate counts, unique ordering,
structure references, and a digest covering effective text, order, and evidence mappings. Batched staging remains
invisible until the complete revision is committed. This deliberately accepts storage amplification for simpler
revision reads and recovery; shared chunk bodies across revisions are deferred.

The main Artifact content must not contain an unbounded full-text/chunk/vector array. Search accesses chunk indexes
directly, then batch-loads exact hits and same-revision neighbors. Generic Artifact reads return the manifest;
detail pagination fixes revision, view, and ordering in the cursor. Distinguish original parsed evidence from
effective edited chunks. Overlapping chunks cannot simply be concatenated into an edited full-document export.

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
| Snapshot preparation | Persist bytes before acceptance; failed acceptance may leave an unreferenced snapshot for cleanup |
| Import acceptance | Source acceptance/reuse, validated target request, and durable scheduling intent |
| Content commit | Complete manifest/details, provenance, validated membership, content receipt, and index work; manual edits also create their system Source here |
| Index publication | Verify complete indexes, exact content target, active configuration, execution ownership, and lifecycle; switch the searchable selection and visibility generation |
| Source revocation | Persist lifecycle tombstone, advance visibility generation, and record cascading cleanup before acknowledging deletion |

Run OCR, embeddings, DDL, and bulk index construction outside these short transactions. Every commit rechecks
registration/lease fences, target state, and input/configuration/deletion generations. A newer edit or import
supersedes old publication work. Durable work records are authoritative; process-local notifications only wake workers.

Content head and searchable revision are distinct. Index failure preserves the previous published revision while
showing the new content and error. A `rag` rebuild uses a new generation and catches up accepted changes to a verified
cutover watermark before switching; a partially built generation cannot replace the active one. Rebuilding one
knowledge base cannot switch another's generation. Filesystem/database backup and restore must verify referenced
snapshots and index consistency before serving restored retrieval.

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

Deleting an original Source is a Scope-authorized operation that revokes every dependent document revision,
including manually edited revisions and dependencies in other `rag` Artifacts in that Scope. Membership references
do not prevent deletion; `rag` configuration remains. Revocation immediately gates search, caches, original reads,
and ordinary/historical Artifact and chunk reads, while physical cleanup is asynchronous and retryable. Returning
an in-flight result requires a visibility recheck; responses already delivered cannot be recalled.

Cleanup includes dependent text, OCR/parse caches, indexes, and edit-Source/audit payloads containing that text.
Minimal non-content receipts may remain. An original Source's payload is never rewritten by ordinary editing;
explicit erasure follows its separate lifecycle. Removing one knowledge base does not delete a shared Source.
Deleting an old Source does not revoke revisions derived solely from its replacement, and a revoked old revision
cannot remain as a search fallback. Never delete the user's original file; reclaim managed snapshots only when
no live reference needs them. Late workers must fail the same deletion fence used by revocation.

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

Final URLs and schemas belong in `openapi/powercontext.yaml` during implementation, followed by generated clients
and contract checks. This RFC specifies operation semantics rather than presenting unimplemented curl examples.
New families and detail tables require versioned migrations on all three backends; existing Sources and Artifact
families retain their contracts and are not automatically converted or enrolled in document processing.

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
- Source revocation across shared dependencies, historical/cache/original-read enforcement, retryable cleanup,
  and preservation of unrelated documents, replacement-Source revisions, and `rag` configurations.

Use existing issues for delivery: #2441 covers foundations/ingestion, #2442 processing/content/index lifecycle,
#2443 retrieval/interfaces, and #2444 operational visibility and cross-backend acceptance. The implementation can
use one integration branch and PR with these four review stages. This RFC PR completes none of those implementation
issues. Publish supported formats, model versions, resource limits, and recovery instructions with implementation;
do not infer production capacity from functional fixtures.

# Drawbacks

Complete snapshots amplify storage and write cost even for a one-chunk edit. Separating content and searchable
revisions complicates reads and diagnostics. Dedicated consumer restrictions, target work, family writers, and
revocation need framework integration beyond a plugin declaration. Three backends add migration and index testing
cost. RapidOCR model packaging and platform compatibility also require explicit maintenance.

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

- Exact family capability/writer extensions, API schemas, migration names, and backend index layouts.
- Limits for snapshots, decoded image pixels, manifests, chunks, pages, worker concurrency, timeouts, and retries.
- Pinned RapidOCR/ONNX/model versions, supported image formats/platforms, and default chunk/embedding profiles.
- Historical revision retention, staging/snapshot garbage collection, and resulting expired-reference responses.

Full edited-document export needs an explicit overlap and edit reconciliation contract. Independent per-`rag` ACLs,
cross-deployment federation, and shared document ownership require separate design work rather than implicit support.

# Future possibilities

Additional parsers, storage providers, automatic collection, alternative chunkers, rerankers, and bounded agentic
retrieval can use the declared component boundaries. Measured storage pressure may justify shared immutable chunk
bodies across revisions. These extensions must preserve exact evidence, target isolation, and publication/deletion
semantics; they are not prerequisites for the initial knowledge-base workflow.
