- Proposal Name: `topic_memory`
- Start Date: 2026-09-01
- RFC PR: [oceanbase/powercontext#1417](https://github.com/oceanbase/powercontext/pull/1417)
- Related RFCs: [RFC 0014](0014_memory_layer_design.md), [RFC 0019](0019_local_source_memory_runtime.md),
  [RFC 0051](0051_experience_skill_artifact_families.md), [RFC 0080](0080_memory_search_reranking.md), and
  [RFC 0081](0081_end_to_end_evaluation_architecture.md)

# Summary

This RFC adds the `topic-memory` Artifact Family to PowerContext. A Topic Memory Artifact represents one long-lived
topic and contains a title, summary, and detailed body. The title, summary, and detail all participate in retrieval;
automatic recall returns only the title, summary, an optional detail snippet, and an exact ArtifactRef. The Agent reads
the complete detail only when it determines that expansion is necessary, providing progressive disclosure.

Topic Memory is generated incrementally from each Scope's immutable Source Journal. The processor first creates
lightweight Probes from a bounded Source Window and retrieves the current Topic Heads. It then selects global direct
evolution, Work Item evolution, or a temporary Topic fallback according to context size. Newly created results undergo
a second historical retrieval and related-group reconciliation before publication. The final operations are limited to
CREATE, UPDATE, and NOOP. The model supplies only `title`, `summary`, `detail`, and `evidence_ids`; the server controls
the target Revision, Artifact identity, operation type, and publication state.

Generation, retrieval, reconciliation, chunking, and, in vector-enabled deployments, Embedding run in background
Workers. A general-purpose
`ArtifactProcessingSupervisor` uses a persistent Pending dirty set to discover Scopes that require processing and an
independent Source Cursor to record completed progress. Supervisor fencing, Cursor CAS, and Artifact Head CAS prevent
multiple replicas, duplicate Workers, or late Workers from committing stale results. A new Revision replaces the old
searchable Revision in one short transaction only after every retrieval channel enabled for the deployment is ready.

# Motivation

## Organizing long-lived topics

Existing Memory is well suited to independently retrievable facts, preferences, decisions, constraints, and work
notes. A long-lived topic, however, needs to organize evidence from multiple sessions and tasks into one continuously
evolving whole. For example:

~~~text
Title: PowerContext background artifact processing architecture
Summary: Source-driven artifacts use independent Cursors; the global Supervisor manages the queue, Workers, and
         multi-replica leader election.
Detail:
  - Source Journal, Window, Cursor, and Pending
  - Supervisor modes for SQLite and OceanBase
  - Worker fencing, atomic publication, and failure recovery
~~~

Splitting this material into many Memory Entries loses the topic structure, while loading the complete topic body on
every request wastes Agent context. Topic Memory gives a topic an independent identity and separates discovery,
relevance judgment, and complete reading.

## Topics need to evolve with new evidence

A new Source may supplement, correct, or change a historical topic. Append-only records accumulate duplication and
conflicts, while similarity-based replacement alone can incorrectly merge related but distinct topics. The system must
retrieve historical Topics first, then use a constrained evolution process that combines exact Sources with exact
historical Revisions to decide whether to create, update, or do nothing.

## Long-running work must not block interactive requests

A Topic Window may require multiple generation calls, multi-channel retrieval, detail chunking, batch Embedding in a
vector-enabled deployment, and an atomic index switch. Neither the Source write transaction nor an explicit flush
should wait for the entire pipeline.
Background execution must preserve the same business semantics for a single-node SQLite deployment and a
multi-replica OceanBase deployment, and it must recover from process crashes, timeouts, duplicate dispatch, and leader
failover.

# Guide-level explanation

## One Artifact represents one topic

Topic Memory does not store multiple Topic Entries in one Artifact. Each topic has a stable, opaque `artifact_id`; its
title may evolve with the content and is not a unique key. Topic content consists of:

- `title`: a short, distinctive topic title;
- `summary`: a synopsis that helps an Agent judge relevance quickly;
- `detail`: the complete topic body.

Updating the same topic preserves its `artifact_id` and creates a new immutable Revision. An `ArtifactRef` identifies
`family + artifact_id + revision` exactly; complete addressing also requires the caller to provide `scope_id`. After a
search, expansion must continue to use the exact ArtifactRef returned by that search. It must not read the latest Head
by `artifact_id`, because a concurrent update between search and read could change the content.

Topic Memory coexists with Memory, Experience, Skill, and Handoff. It does not replace another Family, nor does it
change the requirement that a user must explicitly select a Handoff and choose to continue from it.

## Source, Window, Cursor, and Pending

Every Source add creates a new Source with a stable SourceRef. A Source may encapsulate one message, one conversation
turn, multiple turns, or a document segment, so the Source count is not the raw message count.

Each Scope has a Source Journal whose journal positions increase monotonically. Different processing bindings maintain
independent Cursors over the same Journal:

~~~text
Scope A Source Journal: 1 2 3 ... 20

Memory Cursor:       18  -> pending 19..20
Topic Memory Cursor: 15  -> pending 16..20
Experience Cursor:   10  -> pending 11..20
~~~

A Source Window is a contiguous interval `(after, through]` selected at runtime. It is not a persistent entity, has no
ID, and does not create a new Source. Pending identifies which `(binding_name, scope_id)` pairs may be behind; the
Cursor identifies how far that binding has published atomically; and the Window identifies the contiguous Sources
processed in the current run.

## Topic evidence and Source lineage

The Source Window only bounds the input that the model may read; it is not the evidence for every Topic. The server
creates operation-local evidence IDs for Sources in the Window. For each CREATE or UPDATE, the model returns the
`evidence_ids` it actually used. The server maps them back to exact SourceRefs and rejects references that do not exist
or fall outside the Window.

For example:

~~~text
Window = Source 1..3

Topic A evidence_ids = [s1, s2]
Topic B evidence_ids = [s3]
~~~

Topic A's direct lineage ultimately stores only Sources 1 and 2, while Topic B stores only Source 3. A new UPDATE
Revision also references the exact old ArtifactRef held by the server. The Window itself is neither stored as evidence
nor converted into a Source archive.

## Progressive retrieval and expansion

Topic Memory automatically participates in retrieval within the current Scope. The title, summary, and all Detail
chunks participate from the first retrieval, while automatically returned content stays compact:

1. Search returns an exact ArtifactRef, title, summary, and optional detail snippet.
2. The Agent uses that information to decide whether the Topic is worth expanding.
3. The Agent uses the exact ArtifactRef to read the complete Detail and Source lineage.

Progressive disclosure controls how much content is returned to the Agent, not which fields participate in retrieval.

## Automatic processing and explicit flush

A Source write only updates Pending; it does not generate a Topic synchronously. Automatic Topic Memory processing is
disabled by default, and deployers may configure an automatic processing interval. A user may also call
`POST /v1/topic-memory/flush` to ask the background system to start a processing wave against the latest Source
snapshot as soon as possible.

After persisting the processing intent, flush immediately returns HTTP 200:

~~~json
{"status": "accepted"}
~~~

If the Topic Memory Cursor already covers the Source Head at call time, it returns:

~~~json
{"status": "idle"}
~~~

`accepted` does not mean generation has completed and does not create a queryable one-off task. Concurrent flush calls
coalesce into the same Pending record. Flushes received while a wave is running trigger at most one successor wave.

# Reference-level explanation

## Family, binding, and processing scope

An Artifact Family describes a persistent content type; a processing binding describes how Sources are consumed to
create or evolve Artifacts. This RFC registers the `topic-memory` Family and the `topic-memory-source-window` binding.

The first release limits retrieval, generation, and exact reads to one `scope_id`. It does not accept `scope_ids` or
perform cross-Scope retrieval. Topic Memory is published automatically and does not enter the Review Inbox used by
Experience and Skill. The first release also provides no API for users to create, update, delete, or retire Topics
manually.

This RFC extracts a minimal Source-driven Artifact processing substrate responsible for:

~~~text
discover Pending
  -> read the independent Cursor
  -> select a bounded Window
  -> create and validate evidence IDs
  -> execute the Processor outside the transaction
  -> publish with fencing, Cursor CAS, and Head CAS
  -> retry from the same Cursor after failure
~~~

Topic Memory is the first consumer. This RFC does not migrate the existing Memory, Experience, or Skill processors.

## Window selection and context budgets

In Journal order, the Topic Window Policy selects the largest contiguous prefix after the Cursor that satisfies both
limits:

- no more than `runtime.topic_memory_source_window_limit` Sources, with a default of 10;
- estimated Source tokens no greater than 80% of the generation model's context window.

`inference.generation_model_context_window_tokens` defaults to 125,000, so the default Source Window limit is 100,000
tokens. Every actual generation request must still satisfy:

~~~text
system prompt
+ stage instructions
+ Sources
+ historical Topics
+ structured output schema
+ output reservation
<= generation_model_context_window_tokens
~~~

If adding the next Source would exceed the budget, that Source remains for the next Window. If the first Source after
the Cursor exceeds the Window token budget by itself, the policy still selects it as a single-Source Window so the
Cursor can make progress, and attempts to process its original content. The first release does not internally chunk,
truncate, or reject one oversized Source. If it exceeds the model's actual capability, processing fails and retains the
Cursor until a dedicated design addresses the case.

## Probe and historical Topic selection

The Worker first reads the current Source Window and generates zero or more lightweight Probes. A Probe is a semantic
query sentence or set of keywords with evidence IDs. It contains no Topic body and does not decide CREATE, UPDATE, or
NOOP.

Each Probe recalls the currently searchable Revisions from the current Scope through the Topic retrieval channels
enabled for the current deployment.
Channel results are first collapsed by Topic and then fused with RRF. Historical candidate selection uses three
deployment settings:

~~~text
history_max_candidates = 20
history_rrf_threshold = 70
history_min_candidates = 5
~~~

RRF scores are normalized to 0..100. Candidates meeting the threshold are selected first, up to 20. If fewer than 5
qualify, the remaining candidates are added in fused-rank order until the set reaches 5. If fewer than 5 candidates
exist in total, all available candidates are used.

## Layered evolution flow

All intermediate results exist only in Worker memory. They are not Artifacts, Sources, Jobs, or searchable records.

### Global direct evolution

The server first estimates:

~~~text
all original Source Window content
+ all selected historical Topic bodies
+ prompts, schema, and output reservation
~~~

If the total fits within the context budget and the server can bind outputs to operation targets deterministically, it
skips the Work Planner and lets the Global Topic Evolver produce the final content directly.

The Global Evolver is an optimization path, not a correctness mechanism. If one output could correspond to multiple
UPDATEs, cannot be bound to one historical Topic, or remains target-ambiguous after structural validation, the server
discards the unpublished in-memory result and falls back to the Work Planner. Model timeouts, network errors, and
database errors are processing failures handled by normal retries, not semantic fallbacks.

### Work Planner and direct Work Items

The Work Planner runs only when the global material exceeds the budget or the global target cannot be bound
deterministically. The Planner reads only:

~~~text
Probe
+ SourceRef associated with the Probe
+ title, summary, and snippet of historical Topics
~~~

The Planner does not read complete Sources or all historical Detail. It divides the material into Work Items under the
following rules:

- each Probe belongs to exactly one Work Item;
- Probes that hit the same historical ArtifactRef must enter the same Work Item;
- one historical Topic Head is owned by only one Work Item in a wave;
- the same SourceRef may belong to Work Items for different topics;
- an UpdateWorkItem binds exactly one target ArtifactRef held by the server;
- a CreateWorkItem binds no historical target.

Each Work Item then loads its own original Source content and at most one complete historical Topic body. If the
material fits within the context budget, the Item Evolver produces final Topic content directly.

### Oversized Work Items and temporary Topics

If the original Source content plus the historical Topic still exceeds the budget for one Work Item, only that Work
Item uses the temporary Topic path:

~~~text
Work Item Sources
  -> split into Source Batches at Source boundaries
  -> generate temporary Topics for each Batch without loading the historical Topic
  -> all relevant temporary Topics + one historical Topic or an empty target
  -> final CREATE / UPDATE / NOOP
~~~

A temporary Topic contains only the new information contributed to the current topic by that Source Batch and retains
its evidence IDs. Final lineage is the union of SourceRefs referenced by the temporary Topics that actually contribute
to the result. A temporary Topic has no identity, is not written to the database, does not participate in retrieval,
and is discarded when the Worker ends.

If all temporary Topics plus one historical Topic still exceed the model context, the first release does not perform
recursive compression, split the historical Topic, or split the topic automatically. This is a known but explicitly
excluded extreme input, consistent with the boundary for a single Source that exceeds model capacity.

## Second retrieval and related-group reconciliation

Before publication, CREATE content produced by any path searches historical Topics a second time using the complete
`title + summary + detail`. This second retrieval compensates for historical Topics that a Probe failed to recall.

The server forms bounded related groups from the second retrieval. For example:

~~~text
CREATE B from this wave + CREATE C from this wave + historical Topic D
  -> one UPDATE of Topic D
~~~

The Planner should eliminate obvious duplication first; related-group reconciliation is only a safeguard. It may
combine multiple CREATEs from the current wave into one CREATE, or fold multiple CREATEs from the current wave into one
UPDATE of the same historical Topic. It must not merge two existing historical Artifact identities. If there is no
CREATE, the second retrieval and reconciliation are skipped.

## Evolution operations and model output

Background evolution produces only three internal operations:

- CREATE: the server allocates a new `artifact_id` and creates Revision 1;
- UPDATE: the `artifact_id` is preserved, `title + summary + detail` is replaced in full, and the next Revision is
  created;
- NOOP: no Artifact or index is created, but the Window succeeds and the Cursor advances normally.

UPDATE is a complete content rewrite, not a patch, and retrieval chunks are not update units. Supplementation,
correction, temporal change, and scope narrowing are all expressed through UPDATE. Old Revisions remain available for
audit but do not participate in default retrieval.

The first release does not define MERGE, SPLIT, RETIRE, or DELETE. If new evidence contains an independent topic, the
system may CREATE it. If the original Topic also needs correction or narrowing, the system performs a separate UPDATE.
Two historical Topics are never merged automatically.

During Topic content generation, the model may provide only these business fields:

~~~text
title
summary
detail
evidence_ids
~~~

The server holds the target ArtifactRef for an UpdateWorkItem and generates the new Artifact ID for a CreateWorkItem.
The server also determines the operation type, Revision, lineage, and publication state. NOOP stores neither an entity
nor a reason; logs may record only the code-known `no_change` and processing context.

## Detail chunks and deployment-enabled retrieval channels

Public TopicMemoryContent stores only `title`, `summary`, and `detail`. A Detail chunk is a rebuildable internal
retrieval projection. It has no business identity, is not part of the public data model, and cannot be an UPDATE unit.

The first release supports two deployment shapes. An FTS-only deployment maintains two full-text channels. A deployment
that starts with a matching Embedding model and vector infrastructure also maintains two vector channels:

~~~text
full-text(title + summary)
embed(title + summary)
full-text(detail chunk)
embed(detail chunk)
~~~

A Detail embedding does not prepend the global Topic title, preventing the title from dominating a short chunk. Local
Markdown headings within the Detail may remain part of the body. An FTS-only deployment does not generate either
embedding projection and does not advertise vector or hybrid capability.

Chunk policy:

1. First form indivisible semantic blocks at Markdown heading, paragraph, list, and sentence boundaries.
2. Pack adjacent semantic blocks to an internal target size.
3. Prefer merging an undersized tail block into the preceding chunk.
4. Use limited overlap only when one semantic block exceeds the maximum length and must be split with a fixed window.
5. After a search hit, expand the snippet dynamically around the best matching position.
6. Exact get always returns the complete Detail.

The concrete chunk length, tail threshold, and overlap ratio are internal constants associated with a policy version,
not public configuration. Changing the Chunk policy requires rebuilding the corresponding retrieval projections.

Each retrieval channel first collapses by Topic. When multiple Detail chunks from one Topic match, only the best
position is retained for the snippet. The two or four channels enabled for the current deployment are fused by RRF
rather than by comparing raw full-text scores with raw vector distances. A Topic occupies at most one final result
position, and `matched_by` records which channels matched it.

## Storage, Head, and atomic activation

Topic content, identity, Revision, Head, and lineage reuse the shared Artifact storage:

- `pc_artifacts` stores immutable TopicMemoryContent Revisions;
- `pc_artifact_heads` stores the current Topic Head;
- `pc_artifact_lineage_sources` stores direct Source evidence;
- `pc_artifact_lineage_artifacts` stores the exact old Revision on which an UPDATE was based.

Topic-specific retrieval storage maintains two kinds of active projection records:

- Topic-level active record: exact ArtifactRef, title, summary, full-text field, and, in a vector-enabled deployment,
  title/summary vector;
- Detail-chunk active records: exact ArtifactRef, chunk ordinal, body position, snippet text, full-text field, and detail
  vector in a vector-enabled deployment.

Database adapters may implement these logical records with the existing SQLite FTS/vector virtual table or OceanBase
full-text/vector index patterns. Search, however, may query only the currently complete and searchable active records;
it must not first read every Revision and construct a large `IN` query.

Topic Content, chunks, full-text fields, and all Embeddings required by a vector-enabled deployment are prepared outside
the transaction. When an existing Topic is updated, the old active Revision continues serving requests; a new Topic
remains unsearchable until its first Revision is complete. Only after all channels enabled for the deployment are ready
does the Worker execute one short transaction:

~~~text
validate the Supervisor term
-> Cursor CAS
-> Artifact Head CAS for every UPDATE
-> write all Topic Revisions and lineage
-> write and switch all active projections
-> advance the Cursor
-> update Pending
-> commit
~~~

All CREATEs and UPDATEs for one Window commit atomically with the Cursor. Any validation or CAS failure rolls the whole
batch back and causes reprocessing against the latest state. The system never exposes a Revision with only some
searchable Detail chunks. A vector-enabled deployment also never exposes a Revision with missing or incomplete vectors;
a complete Revision in an FTS-only deployment requires no vector projection.

## Pending dirty set

`pc_artifact_processing_pending` is not a Job queue and does not store
`queued/running/retry_wait/failed/completed`. Its schema is:

~~~text
binding_name                NOT NULL
scope_id                    NOT NULL
source_through              BIGINT NOT NULL
flush_generation            BIGINT NOT NULL DEFAULT 0
handled_flush_generation    BIGINT NOT NULL DEFAULT 0

PRIMARY KEY (binding_name, scope_id)

CHECK source_through >= 1
CHECK flush_generation >= 0
CHECK handled_flush_generation >= 0
CHECK handled_flush_generation <= flush_generation
~~~

A Source write transaction executes:

~~~text
source_through = max(existing, new_source_position)
~~~

Both flush generations remain unchanged. An explicit flush executes in a short transaction:

~~~text
source_through = max(existing, current_source_head)
flush_generation = flush_generation + 1
~~~

When the Supervisor starts a wave, it freezes `wave_target = source_through` and
`claimed_flush_generation = flush_generation`. A wave may contain multiple Window-bounded Worker jobs. Only after the
last Window successfully covers the wave target does the system execute:

~~~text
handled_flush_generation =
    max(current, claimed_flush_generation)
~~~

If another flush occurs while the wave is running, `flush_generation` continues increasing. When the current wave
finishes, it immediately starts at most one successor wave from the new snapshot. Ordinary Source additions only raise
`source_through` and remain for the next automatic or explicit wave.

Pending may be deleted only when both conditions hold:

~~~text
cursor >= source_through
and handled_flush_generation == flush_generation
~~~

Ordinary explicit processing may delete one Pending row under those conditions in the same transaction that advances
its Cursor. A Worker in an automatic wave advances its Cursor but leaves Pending in place. After every frozen target in
that wave completes, the binding's automatic-wave completion transaction performs cleanup so Pending deletion and
`last_auto_wave_completed_at` commit atomically.

A Worker failure does not advance the handled generation. After restart, the Supervisor can recover from Cursor and
Pending without Job history or stage checkpoints.

## Artifact Processing Supervisor

The first release provides a general-purpose `ArtifactProcessingSupervisor` with a fixed Supervisor group of `global`.
The group uses one set of:

- `pc_artifact_processing_pending`;
- `pc_source_cursors`;
- `pc_artifact_processing_leases`;
- `pc_artifact_processing_binding_states`;
- an in-memory fair queue and global Worker pool.

This RFC does not add `pc_artifact_processing_routes`. Persistent routing and a routing generation become necessary
only if a future release needs to move a binding online from `global` to an independent group without downtime.

### Process roles

`runtime.artifact_processing_role` supports:

- `all`: API + global Supervisor;
- `api`: API only;
- `background`: global Supervisor only.

OceanBase supports all three roles and multiple candidate Supervisor replicas. The first SQLite release supports only
single-process `all`; it does not support deploying the API and Supervisor in separate processes.

### Leader, term, and fencing

`pc_artifact_processing_leases` uses `supervisor_group` as its primary key. The only current group is `global`. It
stores at least:

~~~text
supervisor_group
holder_id
supervisor_generation
lease_expires_at
~~~

Each Supervisor process generates a UUIDv4 `holder_id` at startup. `supervisor_generation` is a monotonically
increasing leadership term that prevents ABA when the same holder loses and later regains leadership.

OceanBase permits multiple candidates but only one valid `global` Leader. Acquiring an expired Lease increments the
generation; renewal does not. A Worker's final transaction must validate `holder_id + supervisor_generation` and
confirm that the Lease has not expired.

SQLite performs neither leader election nor renewal. At startup, the Supervisor overwrites the holder, increments the
generation, and sets `lease_expires_at = NULL`; that term lasts until the next legitimate startup. The Worker follows
the same business flow as OceanBase but validates only the holder and generation. Even if an old Supervisor crashes and
leaves an orphan Worker alive, the generation produced by the new startup rejects the stale commit.

### Supervisor failover and recovery

When an OceanBase Leader fails to renew, it immediately stops dispatching and makes a best effort to terminate local
Workers. Other candidate Supervisors periodically try to acquire the Lease. After the Lease expires, the candidate that
takes over through an atomic update increments `supervisor_generation` and becomes the new Leader.

The new Leader receives no in-memory queue, wave target, or retry state from the old Leader. It rediscovers work from
persistent Pending, flush generations, Cursors, and binding scheduling state. An unfinished explicit flush resumes
immediately. Whether ordinary Pending immediately forms an automatic recovery wave is determined by the binding's
`last_auto_wave_completed_at` and automatic processing interval. If the old Leader exits before an automatic wave
completes, it does not advance that time, so the new Leader immediately resumes a wave that is due but unfinished. A
recently completed automatic wave waits only its remaining interval and does not run early merely because leadership
changed. In-memory backoff resets on takeover, so a failed Scope may receive one immediate extra retry. Even if an old
Leader or orphan Worker keeps running, its final transaction rolls back on holder, generation, or Lease validation and
cannot change Artifacts, projections, Cursors, Pending, or binding scheduling state.

SQLite provides no automatic multi-replica failover; process restart is its recovery path. A new Supervisor increments
the generation at startup, removing publication authority from old orphan Workers, then recovers work through the same
Pending, flush-generation, and Cursor rules.

### In-memory queue and Workers

The Leader maintains an in-memory fair queue keyed by `(binding_name, scope_id)`. At most one Worker runs concurrently
for the same key, while different keys may run in parallel up to `artifact_processing_max_workers`. A Worker processes
only one Window at a time. A key with more work in the current wave returns to the back of the queue so a hot Scope
cannot starve other Scopes.

A Worker is a child process managed by the Supervisor and sends no progress heartbeat. The Supervisor only determines
whether it finishes within `artifact_processing_worker_timeout_seconds`; on timeout, it terminates and redispatches
the Worker. A Worker may access the database but may execute only one final short transaction guarded by fencing,
Cursor CAS, and Head CAS. `deadline_at` is not a database correctness condition and does not appear in WorkAssignment.

### Automatic scheduling

The automatic processing interval is calculated per binding rather than from global Supervisor activity. Scheduling
progress is persisted in:

~~~text
pc_artifact_processing_binding_states

binding_name                    PRIMARY KEY
last_auto_wave_completed_at     TIMESTAMP NULL
~~~

This table stores only the minimum state needed to recover automatic scheduling across Supervisor terms. The binding's
family, automatic processing interval, and Window limit remain startup registration configuration; this is neither a
routing table nor a task table. Bindings update their completion times independently, so activity in one Artifact
Family cannot reset or postpone another binding's automatic wave.

To avoid materializing a large Pending set in Leader memory, the current term persists each automatic wave's frozen
targets in `pc_artifact_processing_auto_wave_targets`:

~~~text
wave_id            VARCHAR(36)
binding_name       VARCHAR(128)
scope_id           VARCHAR(256)
source_through     BIGINT NOT NULL
completed          BOOLEAN NOT NULL DEFAULT FALSE

PRIMARY KEY (wave_id, binding_name, scope_id)
~~~

This table is bounded scheduling state for the current term, not a recoverable job queue; Pending, Cursors, and binding
completion time remain authoritative after takeover. A new term clears stale targets and freezes again from Pending. At
wave start, one set-based statement in the same fenced database transaction freezes every current Pending member and its
`source_through` for that binding. Scheduling then reads bounded pages only from the target table and never adds live
Pending rows to the old wave. Final cleanup is set-based, so ready/running memory and each scheduling scan stay bounded
while the completion time and whole-wave cleanup still commit in one short transaction.

Per-key single-flight still applies across explicit and automatic work. A frozen target already owned by an explicit
attempt or retry is deferred while the Supervisor continues through later frozen pages; an automatic target that fails
is deferred in the same way. Other Scopes can therefore make progress, but the binding completion time cannot advance
until every deferred target is eventually covered.

If a deferred target keeps an old wave open, a newly arriving Scope that is absent from the frozen target table may
still run without joining that wave: explicit flushes retain their explicit semantics, while ordinary Pending may make
an automatic catch-up attempt that advances only its Cursor and leaves Pending in place. Neither path changes the old
wave's immutable membership or completion time.

When a binding has automatic scheduling enabled and ordinary Pending exists, the Leader may start an automatic wave if
`last_auto_wave_completed_at` is `NULL` or database time has reached
`last_auto_wave_completed_at + automatic_processing_interval`. At wave start it freezes the current `source_through`
for every Pending Scope in that binding and uses multiple Windows to process through those targets. Sources added during
the wave remain for the next wave.

Workers in an automatic wave commit only Artifacts, projections, and Cursors; they do not delete Pending. Only after
every frozen target completes does the Supervisor execute one short transaction with the same backend-specific
fencing: OceanBase validates the holder, generation, and unexpired Lease, while SQLite validates the holder and
generation. The transaction reconfirms that every frozen target is covered by its Cursor, updates the binding's
`last_auto_wave_completed_at` from database time, and then deletes Pending rows that satisfy the existing Cursor and
flush-generation conditions and have not been raised by newer Sources. The completion time and Pending cleanup commit
atomically.

Failure, timeout, leadership loss, or partial completion neither updates the time nor cleans up automatic-wave Pending.
An explicit flush does not update the time either. If the Supervisor exits after all Workers complete but before the
completion transaction, the replacement still sees Pending and recovers the due work as a new wave; Cursors prevent it
from publishing already committed Windows twice. The binding state row remains when no Pending exists so leadership
changes do not lose the automatic scheduling baseline.

SQLite uses an in-process flush signal and automatic timer to wake the in-process Supervisor. The OceanBase Leader
uses a short-period event loop for Lease renewal, discovery of persisted flush generations, and automatic deadlines.
An in-process signal only reduces latency; database state provides correctness.

When automatic scheduling is disabled, ordinary Pending records wait for an explicit flush and
`last_auto_wave_completed_at` does not participate in scheduling. An unhandled flush generation must still recover
after restart. When automatic scheduling is enabled, a restarted or replacement Supervisor restores each binding's
deadline from its persistent completion time.

### Retry and observability

A processing failure neither advances the Cursor nor deletes Pending. It blocks later Sources for the same
binding/scope but does not block other keys. The Supervisor keeps this state in memory:

~~~text
retry_states[(binding_name, scope_id)] = {
  consecutive_failures,
  next_retry_at
}
~~~

Retries use jittered exponential backoff at approximately 30 seconds, 1 minute, and 2 minutes, up to a cap of about 30
minutes. There is no maximum retry count, and the system never skips a Source automatically. Leader failover or process
restart loses the backoff state and permits one immediate extra retry.

Actual errors—including model calls, output validation, retrieval, enabled Embedding, database commit, Worker crash, and
timeout—use the same backoff strategy but must produce structured logs by `stage` and `error_code`. Cursor/Head CAS
conflicts and leadership loss are control signals and do not increase the ordinary failure count.
Cursor and Head conflicts use a fixed short retry deadline rather than immediate redispatch. The conflicting target
releases its current page while delayed, so frozen suffix targets continue to make progress without a hot loop.

Logs include at least the binding, Scope, Window range, stage, error code, exception type, failure count, retry delay,
Supervisor generation, Worker ID, and traceback. They must not contain original Source content, Prompts, complete model
outputs, or secrets.

## HTTP, MCP, and Prepared Context

### HTTP API

The first release exposes three HTTP operations.

`POST /v1/topic-memory/flush`:

~~~json
{
  "scope_id": "project:powercontext"
}
~~~

It returns `{"status":"accepted"}` or `{"status":"idle"}`, both with HTTP 200. It persists only Pending and the flush
generation and does not wait for background completion. Authentication, request validation, dependency unavailability,
and internal errors retain the existing 401, 422, 503, and 500 semantics.

`POST /v1/topic-memory/search`:

~~~json
{
  "scope_id": "project:powercontext",
  "query": "How does the Supervisor recover from failures?",
  "limit": 10
}
~~~

Each hit returns an exact ArtifactRef, title, summary, nullable snippet, fused score, and `matched_by`. Multiple matching
chunks from the same Topic produce only one result.

`POST /v1/topic-memory/get`:

~~~json
{
  "scope_id": "project:powercontext",
  "artifact": {
    "family": "topic-memory",
    "artifact_id": "topic-a",
    "revision": 3
  }
}
~~~

It returns the title, summary, complete detail, and SourceRefs for that exact Revision without exposing internal chunks.

### MCP

MCP projects only the read-only operations intended for Agents:

- `search_topic_memory`;
- `get_topic_memory`.

`flush_topic_memory` is not projected as an MCP tool, preserving the HTTP-only boundary used by the existing Memory
flush and Experience/Skill generation operations.

### Prepared Context

`POST /v1/context/prepare` retrieves Topic Memory automatically and includes only:

~~~text
title + summary + optional snippet + exact ArtifactRef
~~~

It does not include complete Detail automatically or inject tool-invocation control instructions into historical
content. MCP/API tool descriptions tell the Agent that it can expand an exact ArtifactRef.

The candidate limits per Family are:

~~~text
Memory:       8
Topic Memory: 8
Experience:   2
~~~

The runtime no longer uses one fixed limit of eight candidates across all Families. It preserves each Family's own
ranking, does not compare raw scores across Families, and interleaves their results. The request's `max_bytes` is the
shared final-output constraint. A Topic-only request can return all eight compact hits.

## Configuration

The first release adds or uses these deployment-level settings:

| Setting | Default | Semantics |
|---|---:|---|
| `runtime.topic_memory_schedule_seconds` | `None` | Disable automatic waves when unset; use as the interval when greater than 0; treat values less than or equal to 0 as configuration errors |
| `runtime.topic_memory_source_window_limit` | `10` | Maximum number of Sources in one Window |
| `runtime.topic_memory_history_max_candidates` | `20` | Maximum number of historical Topic candidates |
| `runtime.topic_memory_history_rrf_threshold` | `70` | Acceptance threshold for RRF normalized to 0..100 |
| `runtime.topic_memory_history_min_candidates` | `5` | Minimum recall count when too few candidates meet the threshold |
| `runtime.artifact_processing_max_workers` | `10` | Total concurrency of the global Worker pool |
| `runtime.artifact_processing_worker_timeout_seconds` | `600` | Local timeout in seconds for one Window Worker |
| `runtime.artifact_processing_role` | `all` | `all / api / background` |
| `inference.generation_model_context_window_tokens` | `125000` | Total context window for one generation-model request, including input and output reservation |

The Source Window token limit is fixed at 80% of the generation context window; the total Topic request budget is
100%. Neither ratio is public configuration in the first release.

Topic Memory reuses the existing generation model, generation timeout, and generation max requests. A vector-enabled
deployment also reuses the existing Embedding model, Embedding profile, dimension, normalization, timeout, and batch
size. Probe, Planner, Evolver, and Reconciler use the same generation model. Per-stage model selection is deferred to a
later RFC.

The retrieval shape is fixed when a deployment is initialized. A new deployment may select FTS-only, or it may enable
FTS, vector, and hybrid by starting with complete and matching Embedding and vector infrastructure. The first release
does not support converting an existing FTS-only deployment with Topic Heads in place. Adding Embedding configuration
later neither backfills existing Heads nor permits the deployment to advertise vector or hybrid. That conversion
requires a separately designed procedure for quiescing writes, creating projections, backfilling every current Head,
validating completeness, and restoring capabilities. Until that procedure exists, an operator must retain the original
FTS-only configuration or create a new vector-enabled deployment.

When the Runtime opens existing Topic data with vector configuration, it must verify before readiness that every current
Head already has complete Topic-level and Detail-chunk vector projections matching the configured profile. If any Head
has only full-text projections, lacks any vector, or uses a different profile, initialization fails with a typed
configuration error. The Runtime neither creates nor backfills those historical vectors and does not continue in a
degraded state while advertising vector or hybrid. Removing the newly added vector configuration still allows the
original deployment to open as FTS-only. A new deployment with no existing Topic Head may initialize directly with
vector support.

Configuration is read at process startup. The `all/background` candidates in a multi-replica OceanBase deployment
should use consistent settings and record effective values in startup logs.

# Drawbacks

- Topic Memory adds multiple generation calls, two or four logical retrieval channels, background processes, and index
  storage, making it substantially more expensive than existing Memory.
- Automatic evolution may create or update a topic incorrectly. Immutable Revisions and lineage support auditability
  but cannot guarantee semantic quality automatically.
- Requiring complete indexes before activating a Revision increases write latency.
- The Pending dirty set adds write amplification to Source write transactions.
- Avoiding persistent Jobs, checkpoints, and retry state simplifies the system, but a failure requires recomputing the
  whole Window and the progress of an individual task cannot be queried.
- The `global` Supervisor centralizes resource control, but it may become a bottleneck if several heavyweight Families
  share the Worker pool in the future.
- A single oversized Source and the case where temporary Topics plus a historical Topic still exceed context are
  explicitly accepted, uncovered extreme inputs in the first release.

# Rationale and alternatives

## A separate Family rather than extending Memory Entry

A Topic has an independent identity, complete Detail, and progressive expansion semantics. Putting it inside a Memory
Entry would mix two granularities and make versioning, retrieval, and evaluation difficult to distinguish. Topic Memory
therefore coexists with Memory.

## Probe-first rather than fixed candidate-first

Generating complete candidate Topics first for every Window would add first-round cost to all processing. A Probe is
sufficient for low-cost historical recall, and the second retrieval using complete new Topics compensates for misses.
Temporary Topics are used only for oversized Work Items, not as a fixed step for every Window.

## Layered fallback rather than hard truncation

The system evolves material directly when the global set fits, groups it by Work Item when it does not, and generates
temporary Topics only when one Work Item is still too large. The normal path therefore preserves the full original
content, while only the oversized path pays the compression cost. An ordinary multi-Source Window never silently drops
tail Sources because of a budget.

## Pending + Cursor rather than a persistent Job state machine

The Cursor already expresses completed progress authoritatively, so Pending only needs to identify keys that may be
behind. One Job per Window would duplicate progress and introduce state cleanup, stage recovery, and task history. This
RFC does not need public task queries, cancellation, or checkpoints, so it uses a coalescing dirty set.

## One global Supervisor rather than one background system per Family

Only Topic Memory uses the new substrate in the first release. One global Leader can control Worker and model
concurrency without prematurely introducing a routing system. Future group separation can reuse the same Pending,
Cursor, Lease, and Worker protocols.

## Activate after the current deployment's indexes are complete rather than fuse incomplete Revisions

An FTS-only deployment activates a Revision after both full-text channels are complete. In a vector-enabled deployment,
allowing a new Revision without vectors to participate in full-text retrieval would give different Revisions different
channel counts and make fused rankings incomparable, so the old active Revision remains until all four channels of the
new Revision are ready. Both deployment shapes query only complete active projections, avoiding large Revision `IN`
filters and temporary score compensation.

# Prior art

- [OpenViking](https://github.com/volcengine/OpenViking) uses hierarchical context, Session commit, persistent queues,
  and asynchronous memory extraction. It demonstrates the value of separating compact discovery information from
  complete-content reads.
- [Hindsight](https://github.com/vectorize-io/hindsight) Mental Models summarize accumulated Memory into refreshable
  topic views and support event-driven or scheduled refresh.
- [TencentDB Agent Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory) uses asynchronous queues,
  distributed coordination, and layered Memory processing, demonstrating why an explicit flush should not wait for a
  subsequent long-running pipeline.
- [Infini Memory](https://github.com/infinigence/Infini-Memory) uses structured body boundaries, local retrieval, and
  expansion around hits, supporting this RFC's structure-first chunks and dynamic snippets.
- PowerContext RFC 0051 already defines exact evidence, target Revision, Review, and Head CAS for Experience and Skill.
  Existing Experience incubation also provides an independent Cursor, bounded Window, operation-local evidence IDs,
  and generation outside the transaction. This RFC reuses those principles while adding automatic historical
  retrieval, automatic publication, a complete-index barrier, and a multi-replica Supervisor.

# Unresolved questions

The current scope has no unresolved design questions that block acceptance of this RFC.

The following boundaries are explicitly excluded rather than left as open choices for implementers:

- chunking, truncation, or rejection when a single Source exceeds the generation model's actual context;
- recursive compression or splitting when temporary Topic content plus one historical Topic still exceeds context;
- automatic merging of two existing Topic identities;
- cross-Scope Topic retrieval;
- user-facing APIs to create, update, delete, or retire Topics manually;
- queryable background tasks, cancellation, checkpoints, or persistent retry state;
- in-place conversion of existing FTS-only Topic Heads to a vector-enabled deployment and offline backfill of their
  vector projections;
- an independent Topic Supervisor group and online routing migration.

# Future possibilities

- A separate Artifact configuration RFC that lets deployments or users select enabled Families and override scheduling
  and budgets by Scope.
- A multi-model RFC that lets Probe, Planner, Evolver, Reconciler, Embedding, and rerank use different models.
- Migrate Experience incubation and Skill usage evolution to the Artifact Processing Supervisor.
- Add `topic`, `experience`, or `skill` groups after `global` becomes a bottleneck; add persistent routing only when
  online migration without downtime is required.
- Design dedicated lossy or lossless fallbacks for one oversized Source, an oversized historical Topic, and recursive
  reconciliation.
- Add manual Topic correction, rollback, retire, history visualization, and evaluation annotation.
- Add an offline migration procedure that quiesces writes, backfills vector projections for existing FTS-only Topic
  Heads, validates completeness, and restores capabilities.
- Design and run LoCoMo comparison evaluation and tuning only after Topic Memory development passes functional
  acceptance; do not make that evaluation an implementation acceptance condition for this RFC.
