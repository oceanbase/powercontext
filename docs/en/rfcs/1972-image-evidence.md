---
title: Image evidence and shared media contracts
---

- Proposal Name: `image_evidence_artifact_integration`
- Start Date: 2026-10-13
- RFC PR: [oceanbase/powercontext#1972](https://github.com/oceanbase/powercontext/pull/1972)
- Tracking Issue: [#1932](https://github.com/oceanbase/powercontext/issues/1932)
- Experimental implementation: [draft PR #1956](https://github.com/oceanbase/powercontext/pull/1956)
- Related RFCs: [Source definitions](1400_source_definition_and_observation_model.md),
  [Source access](1458_artifact_generation_source_access.md), [Artifact families](1549_artifact_family_unification.md),
  [processing supervisor](1515_artifact_processing_supervisor.md), [migrations](1771-unified-database-migrations.md),
  [Atomic Memory](1809-atomic-memory.md), [Artifact search](1858-artifact-search.md).

# Summary

Add optional image evidence ingestion to the normal PowerContext workflow. Retain submitted PNG/JPEG bytes as an
immutable, Scope-owned Source backed by a managed asset. Interpret the image asynchronously, and store the validated
interpretation in a new `image-analysis` Artifact family using existing revisions, lineage, authorization and read
contracts. Publish a deterministic text-evidence Source for each successful interpretation revision so existing
Source-driven Atomic Memory processing can consume it. Reuse the Artifact Processing Supervisor and propose three
shared media tables for immutable assets, processing requests and capacity. Images are the first supported media type;
the persistence contracts allow later media handlers to reuse these responsibilities without copying a table set per
modality. This replaces the experimental implementation's seven service-side tables.

This RFC proposes architecture and acceptance requirements. It does not release the experimental API or approve its
implementation. Automatic local-model provisioning is a separate future capability.

# Motivation

Agent conversations include screenshots, diagrams and image-based requirements. Text capture misses their contents;
an incidental Agent summary also loses the exact original material. Persisting both the image and a qualified reading
allows a later session to retrieve useful facts and a person to verify the evidence.

The capability crosses storage, background execution, host integration and public APIs. The design must fit the
existing Source/Artifact framework and explain additional persistence before implementation approval. The
[maintainer feedback](https://github.com/oceanbase/powercontext/pull/1956#issuecomment-6140580696) asks specifically for
that evaluation and for justification of every necessary new table.

# Guide-level explanation

## Normal usage

Images are disabled by default. During normal configuration, a user can enable image interpretation and explicitly
choose either a qualified multimodal generation connection or a dedicated vision endpoint. The latter can be a hosted
provider or an already-running local service. The wizard explains which service receives the image and accompanying
text. A model used by the Agent itself does not automatically provide a PowerContext inference connection.

Users start PowerContext normally. Standard host setup owns capture configuration and collector lifecycle. Initial
automatic targets are qualified Codex Desktop/CLI versions; explicit CLI/SDK upload provides an independent entry.
Host-required plugin activation and Hook trust remain first-use requirements. No separate image environment file or
manually started collector is part of normal usage.

For a runbook screenshot containing an active deployment limit, a crossed-out old value and an unconfirmed hypothesis:

1. Authorized capture preserves the submitted image representation and its session/message provenance.
2. Dashboard shows the retained image and processing state immediately after durable acceptance.
3. Vision produces visible text, visual relationships and uncertainty. An interpretation is a machine reading, not an
   approved decision. Crossed-out and uncertain statements retain those qualifications.
4. Existing Memory processing may create zero, one or several Atomic Memories according to its own policies.
5. A later session can retrieve relevant Memory. Its evidence link reaches the exact interpretation revision and,
   subject to authorization, the original submitted image.

Receipt, interpretation, Memory publication and actual fresh-session recall are distinct outcomes. Dashboard reports
them separately; successful interpretation does not imply a Memory was created. Ordinary generation/Memory processing
may remain unconfigured while vision works.

## User controls

Capture consent is persistent and specific to the project binding, Scope and Server destination. Enabling the Server
capability does not authorize reading a host's historical transcripts. Disabling capture stops new collection; retained
evidence remains readable. Re-enabling does not import messages from the disabled period.

Retry repeats a failed request's pinned interpretation profile. Reparse explicitly selects a new profile and adds a
new interpretation revision. Neither changes cited historical evidence. Configuration changes affect new requests;
they never silently substitute a different backend for accepted work. No automatic fallback sends local work to a
hosted provider.

## Initial scope

Qualify static PNG/JPEG and a file-backed SQLite deployment first. Existing text operations on other supported
backends continue normally; image admission rejects unsupported storage before accepting bytes or creating Sources.
Interpretation uses one image per request with bounded accompanying text. Initial automatic capture supports only
host versions and attachment forms exercised through an actual host process.

Video/audio/PDF ingestion, image embeddings, joint reasoning over several images, arbitrary model hosting and a new
Memory approval workflow are outside this RFC. Local weight download and an owned inference runner are future work.

# Reference-level explanation

## Current contracts and required extensions

The code baseline examined is master
[`7f38426e`](https://github.com/oceanbase/powercontext/tree/7f38426ea87ebaf1c9f7bc761b2459f94bda4e76).
The prototype examined is
[`21df99c8`](https://github.com/knqiufan/powercontext/tree/21df99c841b154975e838fff6618a91c8c191350).
RFC descriptions of future capabilities are not evidence that the baseline implements them.

| Responsibility | Existing contract | Proposed extension |
| --- | --- | --- |
| Exact acquired evidence | Source Definition registry, `pc_sources`, Scope journal, identical-write replay/conflict | Typed original-image Definition and asset descriptor; no bytes in Source JSON |
| Interpretation content/history | Artifact content, heads and exact `ArtifactRef` revisions | Registered `image-analysis` family and domain-owned publication |
| Provenance | Source/Artifact lineage and publication records | Original Source → exact analysis revision → text publication → Memory |
| Model input for Memory | Source-only extraction and named text evidence | Deterministic interpretation-publication Definition; raw images excluded from text extraction |
| Memory fact ordering | New evidence exposes its journal position; recalled Memory supplies exact kind/text and Artifact reference | Separate consumption position from original evidence order; provide and preserve statement-specific ordering support on both sides of reconciliation |
| Scheduling | Scope invocation intents, independent Family budgets, leadership/fencing | Image-analysis binding and worker; per-input progress uses the shared media request contract |
| Inspection | Generic Source/Artifact reads and Dashboard evidence traversal | Image/analysis renderers and authorized asset read |
| Inference | Configured generation roles and usage reporting | Explicit vision role, actual multimodal input, validated output and request budget |

The baseline `SourceRef` is `(source_type, source_id)` within an explicit Scope, and native captures reject conflicting
payload reuse. This RFC uses that implemented contract; it does not require adding the full observation identity model
described in RFC 1400. Definition versions and original identities remain available when image processing is disabled.

Important source evidence:

- [Source models](https://github.com/oceanbase/powercontext/blob/7f38426ea87ebaf1c9f7bc761b2459f94bda4e76/src/powercontext/sources/models.py)
  and [repository](https://github.com/oceanbase/powercontext/blob/7f38426ea87ebaf1c9f7bc761b2459f94bda4e76/src/powercontext/builtin/persistence/sources.py).
- [Artifact models](https://github.com/oceanbase/powercontext/blob/7f38426ea87ebaf1c9f7bc761b2459f94bda4e76/src/powercontext/artifacts/models.py)
  and [shared tables](https://github.com/oceanbase/powercontext/blob/7f38426ea87ebaf1c9f7bc761b2459f94bda4e76/src/powercontext/builtin/persistence/tables.py).
- [Processing intents](https://github.com/oceanbase/powercontext/blob/7f38426ea87ebaf1c9f7bc761b2459f94bda4e76/src/powercontext/builtin/persistence/processing_intents.py),
  [Supervisor](https://github.com/oceanbase/powercontext/blob/7f38426ea87ebaf1c9f7bc761b2459f94bda4e76/src/powercontext/builtin/runtime/artifact_processing.py)
  and [Atomic extraction](https://github.com/oceanbase/powercontext/blob/7f38426ea87ebaf1c9f7bc761b2459f94bda4e76/src/powercontext/builtin/artifacts/atomic_memory/extraction.py).

## Evaluate the Artifact boundary

| Option | Benefits | Costs and limitations |
| --- | --- | --- |
| Original and interpretation both remain Sources, as in the prototype | Smallest consumer change; preserves acquired and derived evidence | Interpretation history, task inspection and lineage need image-specific conventions; Artifact content/versioning is used only downstream |
| Image and interpretation both become Artifacts | Shared revisions and catalog throughout | Treats an acquired attachment as generated content, still needs a binary asset boundary and a Source input for current Memory extraction |
| Original Source plus image-analysis Artifact and text publication | Reuses Artifact history/lineage for derived content while preserving acquisition and Source-only Memory contracts | Adds a Family and a deterministic publication adapter; needs atomic publication and cycle prevention |

Recommend the third option. An image-analysis Artifact is not an Atomic Memory: it describes visible material, including
information that is uncertain, obsolete or not worth remembering. Reusing Memory, Profile or an unrelated Family would
mix their domain rules. Registering a new Family reuses storage and infrastructure, not another Family's meaning.

The media service owns asset durability, request identity/progress and capacity transactions. The image handler owns
format validation, the vision profile, bounded provider requests and analysis publication. Transport/host adapters
translate submissions. They do not define alternative authorization, Source identity, Memory governance or another
scheduler.

## Shared media boundary

Shared storage covers immutable captured media and requests to derive evidence from them. It is not a replacement for
all binary repositories, Artifact domain workflows or the Supervisor. Use existing Definition, Family and processing
binding mechanisms to select an explicitly registered handler; this proposal does not add a plugin discovery system.

| Shared media contract | Handler-owned contract |
| --- | --- |
| Scoped byte identity, digest verification, durable storage and authorized reads | Accepted formats, decoding, dimensions/duration limits and typed media metadata |
| Pinned request input/profile, replay, claim/state transitions and bounded attempts | Processing purpose, provider input/output schema, model compatibility and output validation |
| Capacity accounting and transactional acceptance/completion | Modality-specific admission limits and publication through its Source Definition and Artifact Family |
| Exact original and result references in receipts | Interpretation/transcription meaning, uncertainty, evidence locators and text-evidence projection |

`image.original`, `image.interpretation` and `image-analysis` retain their image-specific schemas. Sharing rows does not
require audio transcripts or video analyses to use those schemas. Future handlers may register their own Definitions
and Families while reusing the same persistence contracts. Their declared text evidence must retain exact result
versions and original evidence ordering, including the delayed-processing rules below.

Requests identify a stable `processing_kind` as well as their scoped original Source. At most one nonterminal request
is allowed for `(Scope, original Source, processing_kind)`. The initial image handler has one interpretation kind,
preserving one active interpretation per original image. Model/profile revisions are pinned request inputs, not new
processing kinds that bypass this exclusion. Distinct future purposes, such as transcription and visual analysis of
one video, can have distinct kinds. Replaying or superseding work must check its kind and exact request/state.

The shared lifecycle initially covers one immutable original input and one final Artifact publication, with optional
bounded context. Multi-stage decoding, chunk scheduling and dependency graphs require their own design if needed;
shared names do not promise that every future workflow fits unchanged. Media handlers retain separate Supervisor
bindings and budgets so a future long-running modality does not occupy the image worker pool.

### Audio transcription design walkthrough

This walkthrough checks the extension boundary; audio ingestion is not part of the initial release:

1. An authorized audio handler validates a supported recording and its duration/size limits. Its Source Definition
   records the exact capture and typed provenance; bytes use the shared scoped asset identity.
2. Acceptance writes the original Source, an audio-transcription request with a pinned profile and the applicable
   media capacity counters in one transaction. A distinct capture of identical bytes still has its own Source.
3. An audio processing binding uses the existing Supervisor and shared request lifecycle. The handler owns speech
   model input, transcript validation and any speaker/time-span representation.
4. Success publishes an exact transcript Artifact revision and deterministic text Source, then records completion and
   wakes Memory processing atomically. Memory retains original recording/evidence order rather than completion time.
5. Reads and retries use the common asset/request surfaces with audio authorization and typed schemas. No separate
   audio asset, request or capacity table is needed for this single-input workflow.

Audio capture consent, supported models, retention limits, evidence time-span semantics and actual host/backend
qualification remain separate release requirements. In the initial image release, unregistered media types or
processing kinds are rejected before asset/Source/request writes; changing a MIME label cannot enable another handler.

## Exact evidence and publication

```text
authorized attachment / explicit upload
  -> managed asset + original-image Source + durable processing request
  -> existing Supervisor -> image-analysis worker
  -> image-analysis Artifact revision + interpretation-publication Source
  -> existing Source-driven Atomic Memory extraction and reconciliation

Memory -> publication Source -> exact analysis Artifact revision -> original Source -> asset
```

The proposed original Definition is `image.original`. Its typed payload records the scoped asset descriptor, stable
capture key/attachment position, host/message provenance, actual submitted representation, bounded context snapshot
and any authorized context Source references. Byte identity and event identity are different: identical bytes can
share one scoped asset while distinct submissions retain distinct Sources.

An `image-analysis` Artifact belongs to one original Source. Revision content records that exact Source, schema/prompt
versions, backend/model deployment identity, pinned input/context digests and validated output. Output distinguishes
readability, visible text, visual description, entities, relationships and uncertainty. Secrets are absent from the
profile and operational metadata; neither embedded image instructions nor accompanying text can change execution
policy. Analysis timestamps are not fact effective dates.

The first success creates revision 1. A reparse appends a revision using the existing optimistic head precondition.
Only one nonterminal request per original image and interpretation kind is admitted at a time. Image-analysis writes
are owned by the image domain; generic content replacement is unsupported in the initial release. A stale head or lost
fence prevents the whole publication. It cannot overwrite a newer interpretation or publish a text Source without its
Artifact revision.

For each committed revision, publish one `image.interpretation` Source containing the exact scoped `ArtifactRef`,
original Source reference, validated text snapshot, projection version and content digest. The snapshot is a derived
view; the analysis Artifact is its authority. Verification rejects inconsistent snapshots. Its stable identity is
derived from the scoped Artifact ID/revision. Reading or projecting it never invokes vision or follows a moving head.

The Artifact revision, its lineage, publication Source, request outcome and Memory dirty notification commit together.
Replaying completion returns the same revision and Source. Context Sources explain interpretation but do not establish
image claims. Analysis lineage references original/context Sources, not its own publication Source; this prevents a
cycle. Memory evidence includes the publication Source and exact analysis revision. Evidence traversal checks access
at every node and never substitutes a latest revision.

Raw-image Sources are explicitly excluded from text generation input. Once an interpretation is published it enters a
later Source window, even if Memory already advanced past the original capture. Memory's existing eligibility, write
policy, reconciliation and zero-candidate behavior remain in effect. Reparse time cannot make old visible facts newer;
multiple readings of one image are not independent corroboration or repeated recall weight.
Publication preserves the original recording time when known and its original journal position in typed metadata.
Its new journal position wakes consumers; it is not the fallback age of image facts. With no explicit fact/recording
date, reconciliation preserves original evidence ordering, so delayed interpretation cannot outrank newer text solely
because it finished later. This requires the Atomic Memory consumer and revision extensions defined below; adding
metadata to the publication Source alone does not establish that behavior.

Original-image and interpretation-publication Sources are domain-owned writes. Generic Source/observation submission
must not forge a retained asset descriptor, acceptance receipt or interpretation publication. The capture/publication
boundary validates the asset's exact Scope/state and the referenced Artifact revision/digest before committing them.

## Memory ordering across delayed interpretation

These are shared requirements for delayed derived evidence, exercised here through images. Future media handlers
must supply the same validated original-evidence basis rather than invent a modality-specific ordering policy.

Consider three journal positions in one Scope, with no explicit event/effective dates or Source recording times:

| Position | Evidence or processing result |
| --- | --- |
| 10 | An original image states that the deployment limit is 100; interpretation is pending |
| 20 | A text Source changes the same limit to 200, and Memory processing publishes that current fact |
| 30 | The image interpretation publishes its text Source with the reading "limit is 100" |

Processing position 30 must keep 200 as the current limit. It may preserve 100 as historical information, but cannot
revise the current limit to 100 or create a contradictory current Memory solely because interpretation completed later.
The same result is required whether positions 20 and 30 are processed in one window or separate windows.

At the examined baseline, `project_atomic_memory_evidence` exposes the publication Source's `journal_position`.
`AtomicMemoryArtifactEvidence` supplies only the exact Artifact reference and kind/text for a recalled Memory;
`_load_related_items` does not load that Memory's Source ordering. Giving only the incoming image its original position
10 therefore leaves the comparison without the existing fact's position 20. The required extension is to the shared
Atomic Memory evidence and revision contract, not only the image publisher or its prompt.
See [evidence projection](https://github.com/oceanbase/powercontext/blob/7f38426ea87ebaf1c9f7bc761b2459f94bda4e76/src/powercontext/builtin/artifacts/atomic_memory/extraction.py),
[comparison evidence](https://github.com/oceanbase/powercontext/blob/7f38426ea87ebaf1c9f7bc761b2459f94bda4e76/src/powercontext/builtin/artifacts/atomic_memory/reconciliation.py)
and [related-Memory loading](https://github.com/oceanbase/powercontext/blob/7f38426ea87ebaf1c9f7bc761b2459f94bda4e76/src/powercontext/builtin/runtime/atomic_memory_processing.py).

### Separate progress from fact ordering

| Value | Owner and meaning |
| --- | --- |
| Consumption position | The accepted Source's actual Scope journal position; used for windows, cursors, wakeups and replay. The interpretation above remains at 30 |
| Original evidence anchor | Exact scoped original Source reference, its retained journal position and its own recording time when known. The image publication above resolves to position 10; ordinary text resolves to its own position 20 |
| Statement time and applicability | Effective/event time and conditions supported by the cited statement; they retain priority over comparable recording times and the same-Scope journal-order fallback |

The image domain validates the publication-to-analysis-to-original references and constructs its anchor from retained
records. The Atomic Memory evidence adapter exposes that anchor as typed comparison input alongside the exact
publication Source and analysis revision. It obtains ordinary text anchors from the same Source repository boundary.
Neither a model nor caller-supplied free-form metadata may select a journal position or an unrelated Source as an
ordering authority. A Source recording time comes from its declared provenance contract; image processing, request,
Artifact publication and migration timestamps are never substitutes. Journal positions from different Scopes are not
comparable clocks. Interpretation retries and reparses of one original retain its anchor; their new publication
positions do not make them newer events or independent corroboration.

### Supply both sides and retain the selected support

Atomic Memory owns a versioned ordering-support record bound to each exact Memory revision and its content digest.
It associates each retained statement, including historical values and applicability conditions, with the exact
supporting evidence and its validated anchor or an explicit unknown basis. A single maximum or minimum position over
the entire lineage is not a statement's age. Recalled Memories supply this compact record alongside kind/text and
their exact Artifact reference; incoming candidates supply the same semantics through their selected evidence IDs.
This extends reconciliation input while keeping initial extraction Source-only. Historical Sources are not replayed
through extraction, and the whole lineage is not added to every model request.

The model selects supplied evidence IDs for the statements it retains. The Atomic Memory service validates that
association and constructs ordering support from those supplied records; the model cannot mint anchors or refresh
their times. An unverifiable association follows the existing invalid-output failure path rather than committing
invented ordering. The new Memory revision, its ordering support and lineage, and the window acknowledgement commit
together under the existing head/state and authorization checks. The support record is part of versioned immutable
Memory revision persistence in the shared Artifact storage, not authority held in image request or capacity rows.
Its runtime/serialization/HTTP schema changes require normal contract review and exact readback compatibility.
Readers must accept existing revisions with absent support as unknown and preserve new support through serialization,
exact reads and backup/restore. Malformed or unsupported support versions are rejected explicitly, not silently dropped
or treated as absent. Storage upgrades and consumer readiness follow the normal migration contract before enabling
image consumption. The support format and model input have explicit size budgets; exceeding them cannot silently
truncate statement associations or acknowledge an unpublished Memory result.

Create, revise and merge preserve statement-to-support associations through the in-window workset and subsequent
recalled revisions. Restoration uses the selected historical revision's support; copying, rebuilding a projection or
changing a head cannot assign today's ordering to old content. Manual writes remain supported without an invented
Source anchor. When their ordering is not established by supported evidence, that basis is explicitly unknown.
These shared consumer changes must be delivered and qualified before image publications become eligible for automatic
Memory extraction; an incompatible consumer must not silently consume them as ordinary newly dated text.

### Historical records and bounded resolution

Existing Memory revisions remain readable and searchable without rewriting their content or history. When a revision
lacks ordering support, a bounded, authorized resolution may recover it only from exact retained evidence with an
unambiguous statement association. Persisted support is preferred; lookup has explicit node, byte and depth budgets
and cannot scan all Sources or expand an unbounded revision chain. Missing, inaccessible, ambiguous or over-budget
evidence leaves the affected basis unknown. Rebuilds cannot use the current head or a newly generated interpretation
to fill historical gaps. Recovered support is a derived resolution result, not a rewrite of the immutable historical
revision; a later Memory revision can persist the support it actually uses.

Comparable explicit fact/recording times still take precedence. If conflicting incoming derived evidence has no such
ordering and either side lacks a usable original anchor, automatic reconciliation preserves the existing current assertion
instead of deciding by interpretation arrival, Artifact revision number or the latest lineage timestamp. It must not
publish a second contradictory current assertion as a workaround. Independent facts can still proceed; this adds
neither a conflict-approval queue nor a rewind of consumed Source positions. Existing explicit Memory correction and
restoration operations remain available. This conservative fallback applies to missing comparison evidence, not to
well-supported newer facts, which continue to follow the normal temporal reconciliation policy.

## Storage comparison and proposed shared media tables

The prototype has seven service-side tables. Their responsibilities map as follows; this comparison does not adopt
the prototype's physical schema as a public contract.

| Prototype table | Responsibility | Proposed disposition and reason |
| --- | --- | --- |
| `pc_experimental_image_state` | Format marker, asset/queue totals, worker owner/lease, fairness pointer | Format moves to normal migrations; worker ownership/fairness to Supervisor; capacity to the capacity table |
| `pc_experimental_image_scope_budgets` | Per-Scope pending count | Consolidate global and scoped media admission budgets in the capacity table |
| `pc_experimental_image_assets` | Scoped byte identity, reservation and retained-asset descriptor | Retain an asset table, but store bounded bytes transactionally; file reservation/orphan state is unnecessary |
| `pc_experimental_image_captures` | Durable capture digest and replay receipt | Merge into typed processing-request receipts; Source alone cannot replay the pinned task/response |
| `pc_experimental_image_tasks` | Pinned work, state and publication outcome | Processing-request table; domain progress is distinct from a coalesced Scope invocation |
| `pc_experimental_image_attempts` | Bounded provider attempts and outcomes | Bounded attempt list in each request; no separate table at the proposed maximum of five attempts |
| `pc_experimental_image_operations` | Retry/reparse key-to-request mapping | Typed keys and unique constraints in the same request table |

Proposed physical names require normal migration review. Their ownership is shared media persistence, with image
validation and interpretation implemented by the first handler:

1. **`pc_media_assets`**: `(scope_id, byte_digest)` identity, bounded raw bytes, validated media type, byte size and
   creation metadata. Reuse relational binary encoding and exact digest verification. Handler-specific technical
   metadata, such as image dimensions, uses a bounded, versioned typed schema; identity, size and common lookup fields
   remain explicit columns. Technical metadata must agree with validated bytes. Source/Artifact JSON remains small;
   several acquired Sources may reference one byte object without repeating or recharging its bytes within a Scope.
2. **`pc_media_processing_requests`**: typed capture/retry/reparse identity and request digest; original Source and
   processing kind; pinned handler/schema/profile/context; acceptance receipt; state, attempt budget, bounded attempts,
   claim token and exact published Artifact reference. Unique constraints protect replay and one active request per
   scoped original and kind. Kind/state/due/Scope indexes support bounded discovery without scanning all requests or
   extracting scheduling keys from arbitrary JSON. Modality-specific input is versioned and validated by its handler.
3. **`pc_media_capacity`**: explicitly distinguished global and scoped rows with retained-media-byte and active-request
   counters. Admission locks and changes applicable counters with asset/request insertion. Byte storage and request
   slots have distinct units; image pixels or future audio/video duration are handler admission limits, not values
   added to a common usage number. Future per-kind limits must define their accounting and reconciliation explicitly.

Shared records cannot accept opaque payloads merely because a media type is present. Unsupported kinds/schema versions
fail before acceptance; supported records round-trip through persistence, typed runtime/HTTP reads and recovery.
Disabling a handler's execution preserves the schemas/readers needed for its retained assets, Sources and results.

Original/interpretation Sources, Artifact revisions/heads/lineage and processing leadership use existing tables. The
new capacity table does not store worker ownership or schema versions. Attempts stay operational, bounded data rather
than Artifact revisions; attempt details cannot include credentials, raw prompts, Base64 or unconstrained provider errors.

Existing storage is reused according to its contract:

| Existing facility | Reuse and boundary |
| --- | --- |
| Source storage and journal | Capture identity, immutable evidence and consumption order; not mutable request state or binary payload storage |
| Artifact content, heads and lineage | Handler-specific result revisions and exact provenance; no new result-history table per modality |
| Processing intents and Supervisor | Coalesced Scope wakeups, leadership and dispatch; intents deliberately omit individual request payloads and retry history |
| Scope configuration and model-usage statistics | Limits/configuration and inference reporting; neither is a transactional retained-byte/request-slot ledger |
| Skill package storage | Reuse binary encoding and verification patterns; preserve its package identity and validation contract |

Existing binary storage was evaluated:
[SkillPackageRepository](https://github.com/oceanbase/powercontext/blob/7f38426ea87ebaf1c9f7bc761b2459f94bda4e76/src/powercontext/builtin/persistence/skill_packages.py)
already stores and verifies scoped immutable archive bytes. Its canonical ZIP/tree identity, required `SKILL.md`,
manifest and 4 MiB uncompressed package limit are Skill contracts, not a captured-media contract. The media repository
does not place images in `pc_skill_packages` or migrate unrelated domains merely to reduce table count. A broader binary
substrate would need a separate assessment of identity, lifecycle and migration costs.

### Capacity ledger versus transactional aggregation

A two-table SQLite alternative can be correct: acquire a write transaction, such as `BEGIN IMMEDIATE`, aggregate
retained bytes and nonterminal requests, check limits, and insert before committing. Its safety depends on all
admission/state mutations following the same serialization boundary. A capacity table is not the only way to prevent
over-admission.

| Option | Benefit | Cost to qualify |
| --- | --- | --- |
| Assets + requests, with transactional aggregation | Two tables; no separately maintained usage counters | Repeated aggregation over growing scoped/global records and the resulting write-lock duration |
| Assets + requests + capacity ledger | Indexed counter-row updates for normal admission; one ledger shared across media kinds | Counter consistency, global-row contention and recovery/reconciliation work |

Recommend the three-table design to bound normal admission accounting work, without claiming measured superiority.
Before implementation qualification, compare both options at the proposed storage/request caps and with many small
assets and concurrent Scopes. Report admission latency, rows visited, write-lock time/contention, restart/reconciliation
cost and crash/concurrency correctness. Queries using indexes still incur aggregation work; ledger repair may still
visit all records. If measurements favor two tables, revise this explicit design choice rather than silently changing
the accepted accounting contract.

### Initial storage and resource bounds

Recommend database-backed image bytes for the initial bounded SQLite deployment. Asset, Source, request and capacity
commit in one database transaction, eliminating the prototype's file-publication gap, orphan reservation and paired
DB/file-tree restore requirement. BLOB writes increase database/WAL size and can hold the SQLite write lock longer;
the choice needs measured admission/read latency and growth, not a claim that it is always faster. External managed
files/object storage are a separate alternative if those measurements justify their recovery complexity. Public asset
references identify exact scoped bytes, not a database column or filesystem path. This preserves a stable read identity,
but any future storage backend must separately qualify durable acceptance, integrity, backup and recovery; the SQLite
BLOB choice and current size caps do not establish suitability for large recordings or videos.

Initial caps are 8 MiB/image, 12 MiB JSON request, 20 million decoded pixels, four attachments/message, 1 GiB of retained
media asset payloads globally, 256 active media requests/Scope and 1,024 globally. Only images are enabled initially;
these budgets are not multiplied per modality. Future handlers require explicit resource limits and qualification.
Ingress/decode concurrency is independently bounded; vision concurrency starts at one. Automatic attempts default to
three with a hard maximum of five. Model output is bounded to 64 KiB and validated before publication.
These are proposed protection defaults, not measured performance claims. Operators can lower them.

Admission uses a fixed lock order and database constraints. Duplicate bytes are not charged twice. Decoder/model work
runs outside transactions; the bounded BLOB insertion is part of the acceptance transaction. Startup reconciliation
can repair derived counters from asset/request facts before admission, using bounded batches; it never invents
new Source/Artifact identities. Routine queue/list work uses indexed keyset pages, not full Source scans.
Measure counter contention, row/file growth and recovery cost before making scalability claims.
Repair or restore may visit all asset/request records in indexed batches: bounded pages still mean O(N) total work.
That cost and the admission readiness gate must be measured separately from normal incremental uploads.

The host's local outbox is separate from these three Server tables. Its durable jobs/attachments/watches solve temporary
file survival and incremental collection on the client. Reuse host checkpoint mechanisms where their exact-input
contract fits; a remote Server cannot replace a client staging queue. Document and bound this client storage separately.

## Acceptance transaction and recovery

| Boundary | Durable fact required | Recovery behavior |
| --- | --- | --- |
| Before admission | Authorization, actual format, limits and supported storage checked | Reject without an accepted Source/request |
| Accepted response | Asset bytes, capacity, original Source/journal position, pinned request/receipt and dirty intent committed in one transaction | Rollback leaves no half-accepted asset; a lost response replays the same receipt without selecting configuration again |
| Provider start | Fenced claim and attempt increment committed before the network call | A crash/unknown response consumes that attempt; no free hidden retry |
| Successful interpretation | Revision, exact text publication, outcome and dirty intent committed atomically | Replay returns the same evidence; lost/stale claims cannot publish |
| Failed interpretation | Stable failure and actual attempt count retained | Explicit retry or bounded automatic policy; original remains readable |

Request states are `pending`, `running`, `retry_wait`, `blocked`, and terminal `succeeded`, `failed` or `superseded`.
Active quota counts all four nonterminal states. Claim/attempt updates advance a request state version. Retry is for a
failed request. An explicit reparse may supersede active work only with its exact request/state precondition; otherwise
it returns conflict. Superseding, invalidating the old claim and accepting the replacement are atomic. A late old
response cannot publish, and its already-started provider call remains counted. This permits replacing an unavailable
pinned profile without silently changing accepted work or leaving an image permanently locked.

Capture keys are qualified by Scope and attachment position. Retry/reparse keys are qualified by processing kind,
operation kind and original/parent request identity. Canonical request digests include the selected processing kind,
bytes, provenance and fixed context; equal keys with different requests return conflict. Retry preserves the failed
profile and context; reparse pins the selected current profile at acceptance and checks the caller's expected Artifact
revision/profile digest. Operation replay uses its first receipt even if configuration has changed.

The image binding uses the existing single-flight Scope invocation and Family worker budget. A bounded pending provider
discovers due image requests and marks domain dirtiness; request acceptance also marks it transactionally. Invocation
generations are wakeups, not per-image task IDs. The processor preserves requests deferred until their retry time and
leaves/re-establishes their wakeup. It does not acknowledge unresolved domain work as completed.

Leadership fence and request claim are rechecked at commit. No persistent image-global worker lease or second
Supervisor is introduced. The worker can be constructed from a qualified vision connection without ordinary text
generation. Disabling capture leaves staging intact; disabling interpretation prevents new provider calls without
breaking historical reads. Lost authorization blocks publication/readback; work is not moved to another Scope or
destination. One disabled workspace cannot monopolize eligible client deliveries.

The system promises idempotent persistence, not exactly-once external inference. Provider responses can be lost after
the provider has done work. Actual request accounting, bounded retry and exact committed evidence are the guarantees.
SDK/provider automatic retries are disabled or charged through the same attempt ledger; a formatting-repair model
call is also an attempt.
Durable receipt requires a file-backed database and qualified journal/synchronous settings. A weaker or unsupported
deployment rejects image admission; normal text behavior keeps its existing contract.

## Public operations and inspection

Reuse and extend existing typed catalogs rather than reproduce image content in task-read responses:

| Public need | Proposed surface |
| --- | --- |
| Submit bytes and pin first interpretation | New image capture command, returning durable original Source and processing-request receipt |
| Read exact bytes/thumbnail | Shared authorized media asset read; image thumbnails are derived representations, never the original |
| Read/list operational progress | Shared media request read/list with bounded kind/state pagination; result is an exact Artifact reference |
| Explicit retry/reparse | One analysis-request command with a typed operation, idempotency key and relevant preconditions |
| List/read original material | Existing `list_sources` / `get_source`, with image Definition support |
| List/read analysis/history | Existing `list_artifacts`, `get_artifact`, `get_artifact_revision`, `list_artifact_revisions`, extended for the Family |
| Health and scheduling | Extend existing processing/configuration diagnostics with image binding readiness and backlog |
| Memory evidence | Extend existing Dashboard evidence renderers/traversal for original image and analysis nodes |

This proposes five operations for the image workflow: capture, asset read, request read, request list and analysis
request. Asset/request reads use common media contracts; capture and analysis commands validate the registered kind's
typed input and authorization. No generic endpoint may write assets, receipts or result references without those
checks. Names/routes are selected in the implementation's OpenAPI contract; the experimental seven-operation surface
is not promoted unchanged. The new Family and shared media records require explicit runtime/HTTP schemas,
reader/processor registration and SDK/JS binding generation. Reusable operations do not by themselves enable another
modality or an unknown Family.

Admission needs Scope contribute permission; reads require the relevant existing content permissions. Retry/reparse
also needs access to the original material. An Artifact share does not grant read permission to its source Scope or
raw image. A shared Memory can remain visible while upstream evidence is explicitly unavailable. No public filesystem
path, arbitrary client path, external image URL fetch or unguarded static asset directory is accepted.

Dashboard groups original images with processing status and analysis revisions, using existing catalog/evidence
components. It supports exact original/analysis inspection from Memory. Ordinary Agent recall remains text-based and
Memory-policy governed; image-analysis is not automatically included as a second default recall family. Listing,
opening, thumbnail generation and recall never call vision.

## Configuration and host lifecycle

The initial image handler uses one capability switch and one explicit backend selection, separate from capture consent:

| Choice | Required configuration | Semantics |
| --- | --- | --- |
| Disabled | None | Existing text behavior; retained image evidence still readable |
| `reuse_generation` | Existing connection plus explicitly qualified multimodal capability | Reuse endpoint/model/credentials; no implicit assumption about image support |
| `dedicated_endpoint` | Vision endpoint, exact model and locally held credentials | Independent hosted or user-managed local inference connection |

Shared media storage grants no additional capture consent or inference permission. Future audio/video handlers need
their own explicit enablement, capture authorization and qualified connection; image consent and a vision endpoint
cannot silently authorize recording capture or select its provider. Common setup/doctor and Server lifecycle remain
the integration boundary rather than adding one independently managed daemon per modality.

The wizard and `.env` describe these optional settings; text-only installations do not gain a mandatory image decoder,
Torch/CUDA dependency or model download. Credentials are resolved at execution through a pinned connection identity;
rotating the key is allowed, silently changing model/deployment identity is not. An unavailable pinned connection
produces a visible blocked/error outcome.
Record provider-reported deployment identity when available. If a provider exposes no immutable model revision,
mark that fact as unknown rather than claim its weights are pinned. Requests pin the declared profile; historical
readback relies on stored output, not reproducible inference.

Standard setup/doctor owns Codex capture installation, consent and diagnostics. The collector stages submitted image
blocks promptly, stores stable host/session/message provenance and retries bounded delivery. It captures the bytes the
host actually submitted, which may already be resized; Dashboard labels this representation accurately. New host
formats are rejected/diagnosed until qualified. Setup must preserve ordinary conversation capture and use the existing
service lifecycle; users do not separately manage an image daemon.

## Compatibility, migration and delivery

Use the normal versioned migration mechanism for the three shared media tables and Family registration. Do not add
an isolated schema version/auto-upgrade subsystem. An official release initializes/upgrades supported image storage
explicitly; incompatible storage rejects admission while ordinary text remains usable.

The unmerged experimental format is not a released compatibility promise. Do not initialize the new layout over it.
If experimental data is imported, keep its exact original/analysis Source IDs, bytes, payloads and journal positions;
the two experimental Definitions remain readable. New analysis Artifacts may cite those old Sources, but import must
not recast old machine readings as if a new profile generated them. Import refusal without modifying the database is
an acceptable initial policy until an explicit converter is qualified. The experiment database is kept separately.

Initial backup/restore follows the database's consistent backup boundary and includes the image BLOBs. Restore validates
asset descriptors/digests before admission. Missing/corrupt evidence produces a clear unavailable/integrity result and
never redirects to another asset. Initial storage pressure refuses new work rather than deleting historical evidence.
If external file storage is selected instead, paired DB/assets backup, durable reservation, file publication and
crash-safe orphan reclamation become additional required contracts and acceptance cases.

After RFC acceptance, deliver reviewed increments: (1) shared media persistence, the image handler and Artifact
publication, the shared Memory ordering contract and explicit upload, (2) standard host lifecycle and Dashboard/SDK
integration, (3) real-host/model, resource-cost and recovery qualification. Implementation approval is separate from
RFC acceptance. #1956 remains a draft prototype pending alignment with this design.

## Acceptance criteria

| Area | Required evidence |
| --- | --- |
| Default-off and normal setup | Text-only startup unchanged; optional wizard/env, normal service lifecycle and host diagnostics; vision works with Memory processing unconfigured |
| Actual hosts | Qualified Desktop drag/file-picker/paste and CLI entries; verify host-produced attachment and Hook timing, not just invoke a Hook with synthetic input |
| Durable receipt | Exact uploaded representation survives response loss, temporary-file deletion, process kill and restart |
| Replay/concurrency | Same request returns the same receipt; conflicting reuse rejected; parallel admission respects budgets; one active request/scoped original/kind even across profile changes; stale fence/head publishes nothing |
| Vision failure | Invalid/oversized output, timeout and provider crash obey actual request/attempt budgets; other Scope/Family work progresses |
| Source/Artifact publication | Atomic revision/view/outcome; exact historical readback after reparse; no cycles or model calls during reads |
| Authorization | Cross-Scope denial, revoked permissions and shared Artifact without raw-image access; no hidden byte/metadata disclosure |
| Memory and fresh recall | Real generation produces governed Atomic Memories; a new actual Agent session uses retrieved evidence; zero-memory results are observable; delayed analysis does not make older facts newer |
| Memory ordering | Both comparison sides carry validated statement-specific ordering support; the scenarios below pass through consumption, revision publication and exact readback |
| Client recovery | Offline backlog, capacity exhaustion, consent disable/re-enable, endpoint/Scope changes and collector replacement preserve consent and progress |
| Storage/migration | Normal fresh/upgrade paths; controlled refusal of experimental format; database backup/restore with exact BLOBs, corruption and mid-transaction crash recovery |
| Shared media boundary | Validate typed asset/request readback and kind-qualified identities; reject unsupported types/kinds before writes; review the audio walkthrough without claiming audio support |
| Resource costs | Compare transactional aggregation with the ledger; measure request/row/file growth, quota-lock contention and indexed page/reconciliation cost; verify all proposed caps |

Controlled providers prove request/recovery behavior. Real model execution proves inference compatibility; actual host
execution proves host input/lifecycle support. These are reported separately. Existing prototype results do not qualify
the proposed Artifact redesign, local-model runtime, every host variant or other image storage backends.

Ordering acceptance includes:

1. The undated 10/20/30 scenario keeps 200 current both within one window and after position 20 has already become a
   Memory. Inspect the actual comparison input: both original positions must be present, while the cursor advances
   through publication position 30. A reparse at 40 and a process restart retain the original position 10.
2. An explicit effective/event time overrides contrary ingestion order under the existing temporal policy; different
   applicability conditions remain separate. Tests must not replace the temporal policy with "lower position always loses."
3. A revision or merge retaining statements supported at positions 5 and 20 keeps each association. An image anchored
   at 10 can affect the statement supported at 5 without making the statement supported at 20 older. Neither a lineage
   maximum nor the Memory revision's creation time may stand in for both statements.
4. Historical readback, restoration, restart and projection rebuild preserve the selected support exactly. Legacy or
   manually written Memories without recoverable support follow the unknown-basis rule, including inaccessible and
   over-budget lookup, without blocking independent facts or creating contradictory current duplicates. Absent support
   remains readable; malformed or unsupported versions fail explicitly, and serialization/backup round trips preserve
   valid support without changing historical content.
5. Forged, mismatched or cross-Scope anchor references are rejected before publication. Controlled-provider tests assert
   the evidence supplied and the persisted support; real-model qualification separately exercises the delayed-image
   scenario and verifies the resulting current and historical Memory content.

# Drawbacks

An additional Artifact family and deterministic Source publication add integration work. Binary data enlarges the
database and needs explicit quota ownership; this proposal adds three shared media tables. The common request/schema
boundary adds design work and does not eliminate future modality-specific workflows or prove their storage suitability.
Persisted vision output can be wrong and can contain sensitive text. Cloud interpretation also has cost and latency.
Exact provenance and Memory policy reduce ambiguity but do not guarantee correct recognition or justify every Memory write.
Preserving fact order also extends shared Memory evidence and revision persistence. Bounded historical resolution has
cost, and unknown ordering can conservatively retain an existing assertion until supported evidence or an explicit
correction is available.

# Rationale and alternatives

The comparison above favors the hybrid design because the baseline Memory extractor consumes Sources while Artifact
revisions already represent derived content. Keeping only an Agent summary loses exact visual evidence. OCR-only is a
possible future qualified interpreter but misses diagram relationships. Reprocessing images on every recall repeats
cost and can change historical meaning. Storing raw Base64 in Artifact/Source JSON grows unrelated reads and still
does not solve durable binary reservation/read authorization.

A two-table variant can derive usage under serialized admission, at the aggregation and locking cost discussed above.
The recommended ledger trades one shared table and recovery bookkeeping for bounded incremental accounting work;
performance acceptance must establish whether that trade is justified. Separate attempt/operation tables require a
demonstrated retention/query need beyond bounded request records. Adding a media kind alone does not justify copying
asset/request/capacity tables. Unrelated existing tables and Supervisor intents keep their own contracts.
The prototype's external-file approach keeps large bytes out of the database, but requires paired snapshots and
cross-resource crash recovery. Storing image BLOBs in a separate domain table follows the existing Skill-package binary
pattern without inheriting Skill semantics. This is the initial recommendation; neither storage medium is assumed to
have acceptable cost without measurement.

Without this feature, users continue to transcribe images manually and exact visual material is absent from the
evidence chain. The default-off initial scope limits the impact on installations that do not need it.

# Prior art

The linked PowerContext RFCs establish acquisition/derived-content separation, family-owned behavior, exact provenance,
Source-only Atomic extraction and shared scheduling. #1932 records the user need and backend alternatives. #1956 is
a feasibility prototype with two native Source Definitions, a separate image worker and seven metadata tables; its
code and acceptance records are inputs to this design review, not an approved upstream architecture.

# Unresolved questions

Resolve before merging this RFC:

1. Accept the hybrid `image-analysis` family and deterministic Source publication, or select the Source-only
   alternative with an explicit equivalent history/inspection contract.
2. Accept the shared media asset/request/capacity boundary, database-backed bytes and the capacity comparison criteria,
   or select an alternative with equivalent exact-read, atomic admission and per-input replay guarantees. Any
   consolidation must retain the invariants above; shared table names alone are not the reuse contract.
3. Confirm file-backed SQLite and qualified Codex versions as the initial support scope, including which actual
   Desktop/CLI entry variants must pass before release.

Provider/model qualification, exact public route names and migration revision allocation are implementation review
items within the accepted contracts. They do not authorize scope expansion or weaker acceptance.

# Future possibilities

Managed local vision could add an explicit backend mode with authorized downloads, pinned model/runtime digests,
readiness, disk/resource budgets, interrupted-download recovery and an owned runner integrated with Server lifecycle.
It would use the same Artifact/evidence path. An already-running local endpoint remains `dedicated_endpoint`.
MiniCPM-V-4.7-1B is a candidate, subject to authorized available weights, licensing, runtime compatibility and measured
hardware/recognition quality; private API access does not establish a distributable default. No model download,
runner or implicit cloud fallback is included in this RFC's acceptance.
Local vision runs on the PowerContext Server machine. It does not make remote capture, ordinary generation or
embedding offline; their destinations must remain explicit.

Audio/video handlers, other storage backends, region/time-span evidence locators, OCR-only interpreters and image search
require their own qualification. Shared media persistence avoids a default table set per modality, but does not promise
that multi-stage workflows never need additional records. Material public-contract changes require follow-up review.
