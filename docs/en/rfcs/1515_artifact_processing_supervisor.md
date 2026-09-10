- Proposal Name: `artifact_processing_supervisor`
- Start Date: 2026-09-08
- Status: Proposed
- Amends: the Artifact Processing Supervisor scheduling and recovery contract in [Topic Memory](1417_topic_memory.md)
- Implementation baseline: [Topic Memory PR #1490](https://github.com/oceanbase/powercontext/pull/1490)
- Related RFCs: [Product Definition](0001_product_definition_and_vision.md), [Core SDK](0002_core_sdk_product_model.md),
  [Local Source-to-Memory Runtime](0019_local_source_memory_runtime.md),
  [Experience and Skill](0051_experience_skill_artifact_families.md), [Candidate and Review](0050_artifact_candidate_review_inbox.md),
  [Profile](1485_profile_artifact.md), [Handoff](0048_handoff_artifact.md),
  [Scope Organization](1345_scope_organization_and_agent_integration.md), [Access Control](1396_handoff_access_control.md),
  [Observability](0046_observability_foundations.md)

# Summary

The [Topic Memory RFC](1417_topic_memory.md) first defined `ArtifactProcessingSupervisor` to schedule Topic Memory's
Source-driven background processing. This RFC does not introduce another Supervisor. It extends the existing Supervisor
into shared scheduling infrastructure for every Artifact Family with background processing capabilities and revises its
general responsibilities. The Supervisor is responsible only for scheduling: it first checks whether an artifact type
is eligible, then discovers eligible Scopes of that type, and finally dispatches a Scope invocation to the corresponding
processor. Input selection, continuous Source consumption, generation, retrieval, Review, publication, and business
progress belong to the artifact processor.

The only operating modes are `global` and `dedicated`. The default, `global`, uses one logical Supervisor for all
artifact types; `dedicated` organizes a separate Supervisor for each artifact type. Both modes use the same processor
interface, persistence protocol, and per-artifact Worker budgets. Custom groups and mixed arrangements are unsupported.

Each artifact type independently configures its scheduling policy, Worker concurrency budget, and timeout for one Scope
invocation. Built-in Memory, Topic Memory, and Experience use intervals; the existing Profile processor retains its cron
and timezone. A failed Scope releases its Worker and enters an in-memory backoff queue without blocking other Scopes.
Accepted invocation intent is durable, while the processor persists business progress; recovery requires neither Job
history nor stage checkpoints. Topic Memory has shipped under its original RFC. This document defines the subsequent
Supervisor amendments and the target contract after other artifact types migrate.

# Motivation

Memory, Topic Memory, Experience, and Skill have different business processes, but background processing faces the same
operational questions: when to check for work, how to allocate Workers, how to handle failures, and how to recover after
restart or leadership change. Separate scheduling systems for each artifact type duplicate these mechanisms.

Topic Memory PR #1490 implements the original RFC's reusable scheduling together with Topic's Source-driven processing
substrate. That common
substrate reads Source Cursors, selects Source Windows, and makes automatic-wave completion depend on every frozen Scope.
The earlier design already allows other Scopes to run and catch up while one Scope fails. A persistently failing target
still prevents the old wave from completing, however, leaving the shared scheduling contract dependent on Source Windows
and whole-wave state. This RFC preserves its Pending, Worker, term, fencing, failure-isolation, and backoff foundations,
leaves Source Windows and business progress explicitly with processors, and admits Scopes by artifact type.

Shared scheduling must not become a shared consumption pipeline. Source Windows suit some Source-driven processors, but
must not be a prerequisite for connecting Handoff, Skill import, or future artifact types to the Supervisor. Dispatch
decisions must not require the Supervisor to call a model, parse Sources, or read complete Artifact content.

A shared Supervisor must prevent two forms of blocking: long tasks of one artifact type must not exhaust another type's
Workers, and failed Scopes of one type must not stop subsequent work in healthy Scopes. Automatic scheduling cannot
depend on every Scope of an artifact type completing successfully.

# Guide-level explanation

## Relationship to the Topic Memory RFC

This document supplements and amends the [Topic Memory RFC](1417_topic_memory.md); it does not replace that RFC's Topic
Memory product or processor design. When both RFCs apply, the Topic Memory RFC continues to govern Topic content,
Source ordering, Windows, evidence, generation, retrieval, atomic publication, and APIs. This document governs the
Supervisor's responsibilities, scheduling unit, resource budgets, automatic admission, and operating modes.

The following Supervisor foundations from the Topic Memory RFC remain unchanged:

- `ArtifactProcessingSupervisor` is a reusable execution layer for multiple Artifact Families, with Topic Memory as its
  first user.
- The Supervisor manages leadership, queues, fairness, Workers, timeouts, and backoff; artifact content and processing
  logic belong to Processors.
- Source writes remain decoupled from long processing, and explicit requests persist intent before returning.
- The same processing key is single-flight, while different Scopes may run concurrently.
- Automatic processing intervals are independent by binding; a busy binding does not reset another binding's interval.
- When one Scope fails, its Cursor does not advance and that Scope retries while other Scopes continue.
- Workers run in separate child processes; the Supervisor manages launch, timeout, termination, and retry.
- SQLite uses a single-process term, while OceanBase uses Leases, a Leader, and fencing.
- Business commits from an old-term Worker fail atomically, and a new term recovers from durable facts.
- Backoff remains in Supervisor memory, without persistent Jobs, run history, or stage checkpoints.

Topic Memory PR #1490 has implemented these foundations as concrete compatibility surfaces, including
`ArtifactProcessingBinding`, `ArtifactProcessingWorkAssignment`, `pc_artifact_processing_pending`, binding state,
automatic-wave targets, and the global Lease. The amendments in this document require an explicit migration; deployed
data and configuration cannot be reinterpreted as though the earlier contract had never shipped.

This document promotes the earlier RFC's independent binding intervals into uniform per-artifact scheduling policies. It also
adds multi-artifact capabilities that the earlier RFC explicitly did not implement: shared processor registration,
artifact-type-first Scope discovery, per-artifact Worker budgets and timeouts, the deployment-wide `global` and
`dedicated` modes, and compatibility and acceptance rules for incremental artifact migration. Topic Memory was the only
initial user, and that RFC explicitly did not migrate Memory, Experience, or Skill. Profile later shipped with an
APScheduler cron, so it also belongs to this document's compatibility scope.

This document revises the following first-version rules:

| Topic Memory RFC first-version rule | Revision in this document | Reason |
|---|---|---|
| All bindings share one global Worker pool and timeout | Every artifact has an independent Worker budget and timeout in both modes | Long-running work must not exhaust another artifact's execution capacity |
| An automatic wave freezes all Pending Scopes; failures do not block same-wave work or catch-up, but completion time advances only after every target succeeds | An interval or cron fire first admits an artifact type and then discovers Scopes with a finite scan; scan completion does not wait for all Workers | This removes the cross-Scope wave-completion barrier completely, extending failure isolation to later scheduling opportunities |
| The common substrate recovers work through `source_through`, Cursors, and automatic-wave targets | Scheduling persists only generic dirty state, invocation requests, and scan generations; processors retain Source watermarks and business progress | This preserves durable invocation intent while allowing non-Source-driven artifacts to participate without synthetic Source state |
| A Worker may execute only one final business transaction | A processor may execute one or more fenced short transactions and uses its own progress to make retry safe | Artifact types have different atomic-publication boundaries; the Supervisor can require only idempotency and completion acknowledgment |
| The current mode is fixed to `global`, with arbitrary named groups as the future direction | Only the default `global` mode or the all-artifacts-separate `dedicated` mode is supported | There is no established use case for selective splitting or mixed groups |

These revisions change the shared scheduling contract. They do not change Topic Memory's business guarantees: Source
ordering, no skipping past a failed position, and atomic publication of a Revision and its retrieval projections. When
Topic Memory adopts the revised Supervisor, its existing Pending, Cursor, flush-generation, and automatic-wave state
must be mapped using this document's compatibility and migration rules.

## Two deployment-wide operating modes

| Mode | Supervisor organization | Worker budget |
|---|---|---|
| `global` | One logical Supervisor handles all registered artifact types | Independent for each type |
| `dedicated` | Each registered artifact type has its own dedicated Supervisor | Independent for each type |

Users select only the mode. Runtime organizes Supervisors from the artifact registry; users need not create or name
groups or bind individual artifacts to them. `dedicated` does not promise separate hosts, separate databases, or model
resource isolation. It separates scheduling state, leadership terms, and lifecycles by artifact type.

`global` still has one Supervisor, one leadership boundary, one Lease, one valid term, and one scheduling loop. Within
that loop, the Supervisor maintains a separate due time, queue, and Worker budget for each artifact type. These are
scheduling partitions inside one Supervisor and do not create additional Leases. The earlier RFC's `global` mode
likewise maintained independent intervals for
multiple bindings inside one Supervisor, but those bindings shared a Worker pool and timeout. Only `dedicated` splits
leadership, Leases, terms, and scheduling loops by artifact type: every registered artifact type has one Supervisor and
therefore one Lease.

On OceanBase, a logical Supervisor may have multiple candidate instances, but only one valid Leader at a time. SQLite
uses one Runtime host process; `dedicated` organizes multiple Supervisors within that process without adding support
for multiprocess SQLite deployments.

## Artifact type first, then Scope

Each scheduling check follows this sequence:

~~~text
Check an artifact type
  -> The type has a registered, available processor and belongs to the current valid Supervisor
  -> Its interval or cron is due, or it has accepted requests or due retries
  -> The type has available Worker and queue capacity
  -> Discover eligible Scopes of this type with bounded queries
  -> Dispatch a Scope to its processor
~~~

An ineligible type does not block later types. The Supervisor does not enumerate every Scope first and then check every
artifact type for each Scope. The scheduling policy belongs to the artifact type; there is no separate configured or
maintained schedule for each Scope. A type may disable automatic admission or register one interval or cron policy, but
not both. The Supervisor uses database time for due checks and does not use business-processing completion as the next
check's baseline.

For example, Topic Memory checks every 5 minutes and Experience every 15 minutes. Even when all Topic Memory Workers are
busy, Experience can run according to its own interval and budget. Profile may retain its daily
`02:00 Asia/Shanghai` cron. The scheduling policy controls when work is checked and admitted; actual start time also
depends on capacity for that type.

## Meaning of one invocation

The scheduling key remains `(binding_name, scope_id)`, not an Artifact ID, Source, Source Window, or model request.
`binding_name` is a globally unique processor-registration identifier that is opaque to the Supervisor. It lets a
`global` Supervisor select the processor directly without requiring the Supervisor to understand how that binding
processes work. In the current contract, each Artifact Family registers exactly one binding with the Supervisor, so
`binding_name` and `artifact_family` have a one-to-one relationship. At most one Worker may run for a key within a valid
leadership term.

The key identifies dispatch and single-flight only; it does not determine the number of Supervisors. The same key
applies in both `global` and `dedicated` modes. In either mode, a Worker receives one Scope processor invocation rather
than a Source Window selected by the Supervisor.

After receiving a Scope, the processor decides which inputs to handle, how much to process, and how to commit. One
invocation may produce Artifacts, create pending Review Candidates, or determine that no change is needed. A successful
return means only that the work selected for this invocation is complete. It does not mean that the entire Scope has no
backlog or that a Candidate has been approved.

~~~text
Scope A: A1 succeeds -> A2 succeeds -> A3 awaits the next scheduling opportunity
Scope B: B1 fails -> retry B1 after backoff; B2 waits for B1 to succeed
~~~

A3 does not wait for B1. For artifact types that consume inputs in order, the processor must preserve continuous
progress; it must not consume B2 first and return to B1 later.

## Configuration example

~~~dotenv
POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_SUPERVISOR_MODE=global

POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SCHEDULE_SECONDS=300
POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_MAX_WORKERS=4
POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_WORKER_TIMEOUT_SECONDS=600

POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS=900
POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_MAX_WORKERS=2
POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_WORKER_TIMEOUT_SECONDS=1800

POWERCONTEXT_SERVER_RUNTIME_PROFILE_SCHEDULE_ENABLED=true
POWERCONTEXT_SERVER_RUNTIME_PROFILE_CRON="0 2 * * *"
POWERCONTEXT_SERVER_RUNTIME_PROFILE_TIMEZONE=Asia/Shanghai
POWERCONTEXT_SERVER_RUNTIME_PROFILE_MAX_WORKERS=4
POWERCONTEXT_SERVER_RUNTIME_PROFILE_WORKER_TIMEOUT_SECONDS=600
~~~

Switching to `dedicated` changes only Supervisor organization, not these scheduling policies, budgets, or timeouts.
Disabling a type's automatic schedule disables automatic admission while retaining the explicit triggers supported by
that type and recovery of already accepted invocations.

## Integration boundaries for all artifact types

All Artifact Families use the same registration and scheduling contract. Each Family defines whether it offers periodic
processing, how it generates content, and how that content is consumed.

| Type or capability | Work that may use the Supervisor | Business boundary that must remain intact |
|---|---|---|
| Memory | Incremental extraction and maintenance within a Scope | Preserve direct remember; Memory owns Source progress, deduplication, and retrieval |
| Topic Memory | Topic generation and evolution within a Scope | The processor owns continuous input, context budgets, Probes, evolution, index readiness, and atomic publication |
| Experience | Incubation, evidence-based updates, and defined background maintenance | Success may mean persisting a Candidate; waiting for Review occupies no Worker |
| Skill | Defined background steps for generation, import, fork, usage evolution, and related work | Skill owns exact inputs and targets, package validation, Review, and publication policy; inputs are not restricted to Experience |
| Profile | Scanning Policy-enabled Scopes and generating Source-window profiles | Preserve cron/timezone, startup catch-up, Policy, Cursor, single-pending-Candidate, and trusted background identity semantics |
| Handoff | Preparation work with business authorization that is suitable for background execution | An interval does not authorize automatic milestone commits, snapshot selection on the user's behalf, or Continue execution |
| Routine, Procedure, SOP, and other planned Families | The same scheduling interface after their domain contracts and processors are registered | Scheduling integration does not grant permission to perform external actions periodically |
| Preferences, constraints, Task Outcome, small tools, and other product concepts | Integration through their eventual Family | The appearance of a product concept alone does not justify registering an empty processor without a domain contract |

Candidate, Prepared Context, Prepared Handoff, External Skill Registry, and Handoff Report do not thereby become new
Artifact Families. They retain their roles as proposals, results, or projections; background computation remains with
their owning artifact processor or existing service. Reads, retrieval, Review, and synchronous domain operations need
not pass through the Supervisor. This RFC does not silently change existing synchronous APIs to asynchronous accepted
responses.

# Reference-level explanation

## 1. Responsibilities and registration

| Component | Responsible for | Not responsible for |
|---|---|---|
| Runtime | Registering processors, reading deployment configuration, organizing Supervisors and resource lifecycles | Automatically installing operating-system services or enabling artifact business capabilities on the user's behalf |
| Supervisor | Type admission, Scope discovery, fair queuing, Worker lifecycle, retry, and leadership terms | Source selection, model calls, consumption rules, Review, and business publication |
| Artifact pending-work provider | Bounded queries for pending Scopes of its type and domain change markers | Generation or lengthy business computation during discovery |
| Artifact processor and its storage adapters | Bounded input selection, computation, commits, progress recovery, and completion acknowledgment | Reimplementing leader election, Worker pools, or backoff waiting |

Registration includes at least a globally unique and stable `binding_name`, a stable `artifact_family`, a unique
configuration prefix, a pending-work provider, a processor entry point that can start in a child process, and exactly
one scheduling policy: disabled, interval, or cron. A cron policy also declares an IANA timezone. A Family uses its
canonical domain registry name, such as `topic-memory`, with the configuration prefix `TOPIC_MEMORY`. Name collisions,
duplicate registrations, simultaneous interval and cron configuration, and explicit configuration for missing
capabilities are rejected at startup rather than silently ignored.

The first version allows each Artifact Family to expose only one binding to the Supervisor. `binding_name` supports
dispatch and persistence compatibility; `artifact_family` selects the interval, Worker budget, and `dedicated`
ownership. A processor may internally compose multiple phases or business steps, but it does not expose them as
additional bindings to the Supervisor. Existing binding names and business Cursors are not renamed or merged by
scheduling unification. Specific phases, targets, and request parameters reside in the artifact type's own persistent
inputs; the Supervisor does not interpret these fields.

The logical Worker invocation is:

~~~text
process_scope(scope_id, execution_context)

execution_context:
  binding_name
  artifact_family
  claimed_request_generation
  supervisor_key, holder_id, supervisor_generation
  worker_id
~~~

The processor reads work from its own storage. The interface contains no `source_after`, `source_through`, Window limit,
Prompt, or business result. The control context supports correlation and commit validation; it does not select business
inputs. A normal processor return means success; an exception or abnormal process exit means failure. The first version
does not add a third scheduling result for yielding a Worker and continuing the same invocation later.

Pending-work discovery must use short, cancellable queries with finite time limits. A provider exception postpones only
that artifact type's discovery and records an error; initialization or processing failure for one Scope follows only
that key's failure path. Neither may masquerade as global leadership loss, clear other types' queues, or block renewal.

## 2. Fair scheduling at two levels and independent budgets

Type checks wake on interval or cron deadlines, explicit request notifications, retry deadlines, Worker exits, and
necessary backend polling. A due schedule opens an admission opportunity for that type; it does not immediately invoke
processors for every Scope.

The Supervisor checks types in rotation. Only after a type satisfies registration, leadership, trigger, and capacity
conditions does it query that type's Scopes. Each type has an independent ready queue, pagination position, backoff
index, and capacity limit. Individual queries and memory caches have fixed bounds and must not materialize the entire
backlog.

- Worker usage includes child processes that are starting or running. A terminating process whose exit has not been
  confirmed still occupies capacity.
- Queuing and backoff do not occupy Workers. Keys in backoff must not fill the ready queue or prevent discovery of
  later pages.
- For one key, ready, running, and retry-wait are mutually exclusive in-memory states, not a durable Job state machine.
- When a type exhausts its Worker or ready capacity, skip it and continue with other types; resume unfinished discovery
  when capacity becomes available.
- Dispatch Scopes fairly within a type. Explicit requests and due retries have no unlimited priority and must not
  indefinitely starve ordinary eligible work.
- The first version neither borrows another type's idle capacity nor imposes a hidden global Worker limit below the sum
  of the per-type budgets.

In `global` mode, the normal dispatch concurrency limit is the sum of all registered types' budgets. In `dedicated` mode,
only the valid Leader for a type may dispatch, so each candidate replica does not receive its own copy of that budget.
The limit governs dispatch under a valid term; during failover, leftover processes may briefly continue computation,
while database fencing prevents old-term commits. Worker budgets are not hard quotas for model tokens, CPU, memory, or
database connections.

## 3. Persistence: scheduling intent and domain progress are recorded separately

The target implementation adds `pc_artifact_processing_intents`, with one coalescible scheduling-intent record for each
`(binding_name, scope_id)`. It must not repurpose the existing `pc_artifact_processing_pending` in place. Topic Memory
PR #1490 has already created that table, and its non-null `source_through` and flush generations have Topic Source
processing semantics. The Topic processor continues to own those fields after migration; other artifact types neither
write nor fabricate them.

~~~text
pc_artifact_processing_intents

binding_name, scope_id                   PRIMARY KEY
pending_sequence                        UNIQUE, monotonically increasing, assigned on first registration and never reused
dirty_generation                        DEFAULT 0
clean_generation                        DEFAULT 0
requested_generation                    DEFAULT 0
handled_generation                      DEFAULT 0
last_auto_scan_generation                DEFAULT 0

0 <= clean_generation <= dirty_generation
0 <= handled_generation <= requested_generation
~~~

The two generation pairs are independent and must not be compared across pairs:

- `dirty_generation / clean_generation`: whether the artifact type has ordinary pending work. Domain input changes
  advance dirty; only the processor may advance clean after confirming that no work remains for the corresponding
  version. These versions are not Source journal positions.
- `requested_generation / handled_generation`: whether accepted Scope invocations have completed. An explicit trigger
  or automatic admission advances requested; Worker success acknowledges only the requested generation captured at
  startup.
- `last_auto_scan_generation`: deduplicates admission of this Scope by the same automatic scan; it does not denote
  business completion.

Domain input and its generic dirty marker must commit consistently. Large external objects may be prepared first, but the
consumable input reference and dirty marker must be published in the same database transaction. Explicit requests obey
the same rule for input references and requested updates. An adapter unable to commit them in one transaction must
provide an existing recoverable delivery mechanism rather than depend on a post-commit in-memory callback. Processors
without Sources use their own domain change versions and need not fabricate a Source Journal.

Generic dirty state is only a coarse notification that the processor may have work. Topic `source_through`, Profile
Policy, Memory and Experience Cursors, and other domain facts still determine what can actually be processed. During a
temporary mismatch, the processor must safely return NOOP or rebuild dirty state from bounded domain facts. The
Supervisor cannot infer consumption of business input from the generic generation.

A successful invocation may handle only the portion of input selected by the artifact type. It may therefore advance
handled while leaving dirty > clean. Remaining work awaits the next automatic admission or explicit trigger. The
Supervisor must not set clean = dirty merely because the invocation returned successfully.

At the start of processing, the processor reads a domain snapshot and its dirty generation. Even if it confirms that
no work remains in that snapshot, it may acknowledge only that version; it must not overwrite updates that arrive
during execution. Business results, business progress, and their completion acknowledgment commit consistently in
processor-defined short transactions. Counter rows are retained to prevent generation reuse after deletion and
recreation; cleanup on Scope deletion must ensure that no valid invocation or scan refers to the records. These
records contain no per-run history, model intermediate results, or checkpoints.

## 4. Automatic check timing and pagination recovery

Each binding persists minimal check state in the existing `pc_artifact_processing_binding_states`. This table evolves
from Topic Memory's implemented `last_auto_wave_completed_at` and retains the binding primary key:

~~~text
binding_name                            PRIMARY KEY
last_schedule_checkpoint_at             NULLABLE TIMESTAMP
scan_generation                         DEFAULT 0
scan_in_progress                        DEFAULT false
scan_upper_pending_sequence              NULLABLE INTEGER
~~~

For interval T, pending work permits an immediate first check when no check has started. Thereafter a new automatic scan
is allowed when database time reaches `last_schedule_checkpoint_at + T`. For cron, this field stores the admitted cron
fire time. Multiple fires missed during downtime coalesce into one catch-up that advances through the latest fire before
the current database time. Profile retains its existing immediate first catch-up after enablement, followed by cron.
A type without capacity remains due and neither advances its checkpoint nor pretends that a check ran.

The short transaction that starts a scan increments the scan generation, records the interval's database start time or
the cron fire, sets scan-in-progress, and saves the type's current maximum pending sequence. These commit together. If
the process then exits, its successor knows that discovery is incomplete and does not defer already-due work merely
because the checkpoint advanced.

The scan paginates in stable pending-sequence order, visiting only Scopes with dirty > clean whose registration sequence
does not exceed this scan's upper bound. Scopes registered later wait for the next automatic scan; explicit requests are discovered
independently. This bounds the membership of each scan so that continuous Scope registration cannot prevent completion.
The registration sequence identifies only the Scope's pending record; it is not a Source watermark and does not define
the processor's consumption range. Scanning follows these rules:

1. Automatically admit only keys without a ready, running, or retry occupant. Busy keys are not awaited and do not block
   later pages.
2. For a key not yet visited in this scan generation, advance its requested generation and record its last-auto-scan
   generation in a short transaction with term validation before putting work in the in-memory queue.
3. Reuse an existing unacknowledged request rather than creating a duplicate automatic request; still record the
   deduplication marker for this scan.
4. Capacity limits may pause pagination, but must not mark an unfinished scan as complete.
5. Clear scan-in-progress after all discovery pages finish, without waiting for any Worker, failed retry, or business
   Cursor.

Recovery reuses the unfinished scan's generation. Restarting from the first page must remain idempotent. The
last-auto-scan marker prevents already-successful Scopes on earlier pages from being automatically admitted twice in
the same scan. A record that becomes visible late, with a registration sequence before the current pagination position,
waits for the next automatic check; explicit triggers are discovered independently of that position. New dirty state
for an existing Scope does not reset the current scan's admission deduplication marker.

Interval T is the time between check starts, not a rest period after all business processing completes. If the interval
becomes due again during a scan, it coalesces into one pending check opportunity rather than stacking scans; cron fires
coalesce the same way. A scan that finishes after the next due time may be followed by another check, subject to type
capacity and fairness. Restart does not create duplicate work for every missed schedule occurrence.

Disabling automatic processing stops admission of ordinary dirty Scopes that have not yet been admitted, while
restoring existing invocations with requested > handled. An unfinished automatic scan may end after the disabled
configuration is confirmed; requests already admitted durably are not withdrawn. Re-enabling configuration resumes
checks according to persisted timing.

## 5. Explicit triggers, coalescing, and completion

The artifact API validates Scope, permissions, parameters, and business capability for an explicit trigger. The short
acceptance transaction publishes references to required inputs and advances requested generation together, and must
commit before returning accepted. The artifact's public API defines whether a request with no business work returns idle.

The scheduling layer holds only the Scope and request generation, not business parameters, and does not synchronously
wait for model processing. Topic Memory may retain its existing `HTTP 200 {"status":"accepted"}` /
`{"status":"idle"}` behavior. This RFC adds no generic task-query API and changes neither existing synchronous
response contracts nor MCP exposure for other artifact types.

At dispatch, read the latest requested generation as G:

- Multiple requests received while queued coalesce into one invocation carrying the latest G.
- Requests received during execution continue advancing requested. The current invocation acknowledges only G and
  therefore cannot swallow later requests.
- If requested > handled after success, retain at most one successor invocation; merge the latest requests again when
  it actually starts.
- Failure does not acknowledge G and retains retry responsibility for that Scope. New input, ordinary automatic checks,
  and flush do not clear existing backoff.
- Ordinary input changes advance only dirty, not an explicit request; they await the next eligible automatic check or
  explicit trigger.

If dispatch or processing discovers that handled generation has already reached the carried G, acknowledge that the
invocation is complete without consuming new business input. Later requests are dispatched separately with a new G;
retrying a completed invocation must not advance business processing further. A Worker that exits normally without the
corresponding durable completion acknowledgment has violated the processing protocol; its request must not be discarded
from memory based on the exit code alone.

The processor determines and validates Source freeze positions, window sizes, and the inputs that a particular flush
must cover. The Supervisor neither compares Source watermarks nor continues running merely because the entire Scope
still has Sources.

## 6. Workers, timeouts, and business commits

A Worker is a managed child process that runs one Scope invocation. Input selection, token estimation, model calls,
retrieval, and other potentially lengthy business preparation all run inside the Worker rather than the Supervisor's
dispatch path. Child processes reconstruct necessary resources from configuration; they cannot depend on model objects,
connections, or closures that exist only in parent memory and cannot be recreated.

Timeout starts when the Worker starts and covers the entire Scope invocation, excluding prior queuing and backoff.
It is independent of the timeout for one model request. The first version has neither Worker progress heartbeats nor
indefinite extension while progress continues.

On timeout, stop the Worker: first allow a bounded graceful termination period, then force termination if necessary.
Release capacity and schedule retry only after exit is confirmed. If exit cannot be confirmed, do not start a second
Worker for that key in the same term. A term change must invalidate old commits before restoring dispatch. A transaction
that already committed successfully is not undone by a later timeout; `deadline_at` is not a business commit
correctness condition.

Processors compute outside database transactions and commit business results in short transactions. Every business
commit validates the active term and the processor's own version and progress conditions; final success acknowledgment
is consistent with the related business commits. If an invocation contains multiple legitimate short transactions,
failure may retain committed business progress but must not acknowledge the unfinished invocation. Retry resumes from
durable business progress without replaying side effects that have already taken effect.

NOOP can successfully acknowledge an invocation, as can generating a Candidate that then awaits Review. The Supervisor
does not receive complete model output or publish on the processor's behalf. Lost transport or a process exit after
commit may cause duplicate invocations, so exactly-once execution is not promised. Processors must protect repeated
invocations with atomic progress, version CAS, or business idempotency keys.

The Supervisor does not perform business authorization for a processor. An artifact API completes permission checks
before accepting explicit intent. For automatic work, the registered processor wrapper restores the deployment's trusted
background Principal and performs the domain's authorization and audit for the actual Scope. Existing trusted Runtime
service identity for Profile and scheduled access-runner semantics for Memory and Experience remain intact. An
authorization denial is a failure for that Scope; it does not turn into loss of the entire `global` Supervisor.

## 7. Failure and backoff

For each `(binding, scope)`, the Supervisor keeps consecutive-failures and next-retry-at in memory. After a failed exit,
the key moves to the delayed queue. When due and its type has capacity, it returns to the fair ready queue.

- Use exponential backoff with slight jitter: about 30 seconds, 1 minute, 2 minutes, up to a cap of about 30 minutes.
- There is no retry-count limit, automatic skipping of failed input, or new failed/manual-recovery task state.
- New input or explicit triggers for the same Scope do not reset backoff; success clears the failure count.
- Actual failures, including model, validation, retrieval, Embedding, database writes, Worker crashes, and timeouts, use
  the same policy and record their causes.
- Processor progress or Head CAS conflicts use a fixed short redispatch delay without increasing the ordinary failure
  count or immediately spinning.
- Leadership loss is a control event: stop dispatch and commits for that Supervisor and let a new valid term recover.

Backoff remains in memory. Restart or leadership change may retry an accepted failed invocation immediately, then
rebuild the backoff sequence; it never skips the failed business position. Permanent errors may continue consuming
invocation resources and must be found through observability; the first version does not discard them automatically.

## 8. Leadership terms, backends, and process roles

One Lease corresponds to one logical Supervisor, not to a binding, Scope, Worker, or Worker-budget partition. Because
`global` has one Supervisor, all artifact types share and contend for the single `global` Lease; a queue or processor
failure for one artifact type does not create another Lease. Under `dedicated`, every registered artifact type has one
Supervisor and therefore one `artifact:<canonical Family name>` Lease. Multiple background replicas are candidates for
the same Supervisor and compete for the same key; candidate count does not increase the Lease count.

The internal Lease key is determined by the mode: `global` or `artifact:<canonical Family name>`. It is not a
user-configured route name. A Lease stores at least the supervisor key, a random holder ID, a monotonically increasing
supervisor generation, and nullable expires-at. Generation prevents ABA when the same holder loses and later regains
leadership; a process UUID alone is insufficient.

### OceanBase

Multiple candidates compete atomically for an expired Lease. Takeover increments generation; ordinary renewal does
not. The Supervisor renews on a short interval and discovers requests from other processes; failed renewal immediately
stops dispatch and attempts to terminate Workers. Database time is authoritative for Leases and durable automatic
check timing.

Every Worker business write transaction and every Supervisor scheduling-control write transaction must lock and
validate the Lease within that transaction, checking holder, generation, and unexpired status and holding the lock
through commit. Scheduling control includes starting/ending scans, admitting requests, and cleaning control state;
domain APIs need not be Leaders to publish dirty state or accept explicit requests. They must not check the term in a
separate transaction and then write business data or scheduling state unconditionally.

### SQLite

A single Runtime host runs with role `all`. At legitimate startup, each internal Supervisor key updates its holder and
increments generation, with expires-at NULL. There is no election, lease expiration, or renewal. `dedicated` adds only
controllers organized by type within that host.

Term replacement and Worker commits must be serialized by real SQLite write transactions, such as `BEGIN IMMEDIATE` or
equivalent conditional writes and locking. Fencing cannot rely on `FOR UPDATE`, which SQLite ignores, or on an unlocked
read-term-then-write sequence.
Supervisor write transactions that start/end scans, admit requests, or clean control state must likewise validate
holder/generation and be protected by real write transactions.

SQLite scheduling wakes on explicit request notifications, automatic deadlines, retry deadlines, and Worker exits; it
adds no periodic database polling while idle. A scan records the notification sequence at its start. New notifications
during a scan or a capacity-limited interruption retain the obligation to rescan. If the sequence has changed when the
last page completes, restart discovery from the beginning before deciding to sleep, preventing a later flush for an
already-scanned Scope from remaining stranded indefinitely.

### Roles and recovery

Preserve `runtime.artifact_processing_role`: `all` runs API and background processing; `api` serves only the API;
`background` runs only background processing. OceanBase supports all three roles; SQLite supports only `all`. Background
replicas automatically organize candidate Supervisors according to the mode. Standby is healthy and does not repeatedly
restart merely because it has not been elected. API acceptance does not imply that background processing is currently
alive; health and backlog metrics express background availability.

API and background instances must agree on mode, binding-to-Family mappings, and triggerable capabilities. Worker
budgets, timeouts, models, and other execution-only configuration may be present only on background candidates; an API
does not pretend to be a processor merely because it can accept intent. In `global`, a discovery or processing error for
one artifact type degrades only that type while leadership and other artifact types continue. Failure of the shared Lease
itself changes the state of the whole global controller.

Recovery establishes a valid term, loads artifact check state, restores invocations with requested > handled and
unfinished scans, then checks ordinary dirty work according to deadlines. It does not inherit an old process's ready
queue, backoff times, or intermediate business results. If a Worker commits successfully but its notification is lost,
durable completion acknowledgment and the processor's own progress determine whether work remains.

## 9. Shutdown and mode changes

Normal shutdown stops discovery and new dispatch and performs bounded cleanup of running Workers. Accepted requests
without completion acknowledgment remain pending; clearing a queue does not complete them. Invalidate the current term
if necessary to prevent remaining Workers from committing.

Changing between `global` and `dedicated` uses a coordinated shutdown:

1. Stop every old background candidate and its Workers, and prevent automatic restart with the old configuration.
   Pause new writes and explicit triggers during migration maintenance.
2. Invalidate every active term of the old mode in the database while preserving and advancing generation. Creating
   Lease keys for the new mode alone is insufficient.
3. Give every replica the same new mode, registry, and artifact configuration, then start new terms and leave maintenance.

The same procedure applies when returning to `global`. In particular, an old permanent SQLite term does not become
invalid merely because a new key exists. A mode change does not reset request counters, ordinary pending markers,
artifact check times, or processor Cursors. The first version does not support rolling coexistence of modes, online
routing tables, dynamic lease ownership, or mixed groups for individual artifact types.

## 10. Configuration contract

Environment variables share the prefix `POWERCONTEXT_SERVER_RUNTIME_`; the Runtime model uses corresponding lowercase
fields.

| Suffix | Runtime field | Default and validation |
|---|---|---|
| `ARTIFACT_PROCESSING_SUPERVISOR_MODE` | `artifact_processing_supervisor_mode` | `global`; only `global` and `dedicated` are valid |
| `ARTIFACT_PROCESSING_ROLE` | `artifact_processing_role` | `all`; backend support is defined above |
| `<ARTIFACT>_SCHEDULE_SECONDS` | `<artifact>_schedule_seconds` | Unset/None disables automatic admission; a configured value must be greater than 0 |
| `<ARTIFACT>_SCHEDULE_ENABLED` | `<artifact>_schedule_enabled` | Explicit switch for cron-based artifacts; Profile is the first user |
| `<ARTIFACT>_CRON` | `<artifact>_cron` | Five-field cron; must parse with a valid IANA timezone |
| `<ARTIFACT>_TIMEZONE` | `<artifact>_timezone` | Cron timezone; Profile retains `Asia/Shanghai` by default |
| `<ARTIFACT>_MAX_WORKERS` | `<artifact>_max_workers` | Must be a positive integer; built-in compatibility defaults appear below |
| `<ARTIFACT>_WORKER_TIMEOUT_SECONDS` | `<artifact>_worker_timeout_seconds` | Must be greater than 0; Topic and other migrating types default to 600 seconds |

| Family | Default Worker budget | Compatibility basis |
|---|---:|---|
| Memory | 1 | Current APScheduler activation and shared processor lock execute serially |
| Topic Memory | 10 | Current `ARTIFACT_PROCESSING_MAX_WORKERS=10` |
| Experience | 1 | Current APScheduler activation and shared processor lock execute serially |
| Profile | 4 | Current `PROFILE_MAX_CONCURRENCY=4` |
| Later Families | 1 | Their integration RFC may change this after workload validation |

Canonical built-in prefixes include MEMORY, TOPIC_MEMORY, EXPERIENCE, PROFILE, SKILL, and HANDOFF. Configuration may be
enabled only when the corresponding schedulable processor and business capability exist. Future Families declare a
unique prefix at registration. Setting a schedule does not create a processor, choose a default model, or permit
external actions.

Configuration takes effect at startup. All OceanBase instances must agree on mode, binding-to-Family mappings, and
trigger capability declarations; background candidates must additionally agree on schedules, budgets, and timeouts.
Explicitly enabling a capability without its processor or dependencies returns a clear configuration error. Generation
capabilities that are not enabled do not prevent reads and Review of existing artifacts.

The default Worker count is a per-artifact budget, not a deployment total. Before enabling multiple artifact types,
provision deployment resources for the sum of their budgets. Models, context windows, Source windows, retrieval shapes,
and Review policies retain their own configuration rather than moving into Supervisor mode.

## 11. Compatibility and integration order

This RFC revises the existing Supervisor's boundary between shared scheduling and domain processing. The Topic Memory
RFC continues to define Topic content, Source ordering, Windows, generation, retrieval, and publication. This document
supersedes its rules that make the Supervisor select Windows, use one shared global Worker budget, wait for all Scopes
before advancing an automatic interval, or evolve toward arbitrary named groups. Other related RFCs' Source, Artifact,
Review, Scope, and retrieval contracts remain valid; this document governs scheduling organization, admission, the unit
of Worker execution, and recovery.

The current-main implementation baseline is Topic Memory PR #1490. `ArtifactProcessingBinding` still contains
`source_window_limit`, `window_selector`, and a Window launcher;
`ArtifactProcessingWorkAssignment` still carries a Source range; and the Supervisor directly accesses Source Cursors,
Pending, binding state, and automatic-wave targets. The target registration adds Family, scheduling policy, Scope
provider, per-artifact capacity, and a Scope processor. Topic keeps its existing binding name while Source selection and
domain-table access move into its processor. This refactor requires a behavior-preserving adapter before the old
Supervisor path is removed.

Configuration migration follows deterministic rules:

- Existing Topic Memory and Experience interval keys retain their names and disabling semantics.
- `SCHEDULE_SECONDS` is a compatibility alias for `MEMORY_SCHEDULE_SECONDS`; explicitly configuring both with different
  values fails startup.
- Profile retains `PROFILE_SCHEDULE_ENABLED`, `PROFILE_CRON`, and `PROFILE_TIMEZONE`. Existing
  `PROFILE_MAX_CONCURRENCY` is a compatibility alias for `PROFILE_MAX_WORKERS`; explicitly configuring both with
  different values fails startup.
- Existing `ARTIFACT_PROCESSING_MAX_WORKERS` and `ARTIFACT_PROCESSING_WORKER_TIMEOUT_SECONDS` are compatibility aliases
  only for the corresponding new Topic Memory fields, because Topic Memory is the first processor that uses that
  Supervisor. The former must not continue to mean a shared budget across all types, nor may either custom value be
  silently copied to every artifact type. Explicit old and new values that disagree fail startup.
- Effective configuration logs identify compatibility aliases in use. The 600-second default covers one Scope
  invocation. Topic migration must verify that internally selected work fits that budget, rather than silently
  extending timeouts or changing Source selection to hide a mismatch.

Before enabling the shared Supervisor for a type, disable its old background entry points. APScheduler jobs, old flush
executors, and new Workers must not simultaneously process the same domain progress. Memory and Experience's shared
processor lock and Profile's in-process semaphore do not provide commit idempotency across replicas. Remove their old
role restrictions only after each type passes commit and recovery acceptance. Types that have not migrated do not
register fictitious background capabilities. Migration may proceed one artifact at a time: Memory, Experience, and
Profile continue using the existing APScheduler sidecar until migrated, but are not reported as Supervisor-managed.

Storage migration runs during shutdown maintenance:

- Create the generic intent table. Do not make existing `pc_artifact_processing_pending.source_through` a nullable
  generic field, and do not make non-Topic artifacts write that table.
- Explicitly map old processing bindings to Families while preserving independent business Cursors, Topic Pending, and
  evidence refs. Backfill generic dirty state for old uncovered Source pending work. Backfill accepted requests for
  unacknowledged flushes as well, rather than making them wait for an automatic interval.
- When domain progress shows remaining work but no old Pending record exists, the artifact provider reconstructs
  ordinary dirty state with bounded discovery. Newly registered or re-enabled processors perform the same reconciliation
  rather than waiting only for future input events. Reconstruction neither bypasses disabled automatic processing nor
  accepts domain actions on its own.
- Convert started but unfinished Topic automatic targets into accepted invocations with recovery responsibility. Do not
  simply drop old automatic target tables and copy only completion timestamps.
- For a type with no ongoing work, its old automatic completion time may provide the check-time baseline, preserving
  the remaining wait. If completion of old automatic work cannot be proven, conservatively retain invocation intent
  and let processor progress deduplicate it.
- Preserve Profile's immediate catch-up on first migration. Its latest cron fire, Policy, Cursor, and pending Candidate
  jointly determine whether work exists; changing schedulers must neither create a duplicate Candidate nor skip pending
  Review state.
- Disable Topic's old scheduling fields and temporary automatic target tables only after validating consistency among
  new intent, domain pending state, check state, and business progress.

Every type ultimately integrates through the same registration contract, but domain pipelines may ship incrementally.
Adding an empty Family or changing consumption behavior does not count as completing integration.

## 12. Observability and acceptance

Startup logs record mode, role, registered bindings and Families, effective schedules, budgets, timeouts, and candidate
terms. Scheduling failure logs include binding, family, scope, trigger reason, request generation, Worker ID, term,
stage, error code, exception type, retry count, and delay. Processors log domain fields such as Source ranges. Logs must
not contain Source bodies, Prompts, complete model outputs, credentials, or full database connection strings.

Expose available/used capacity, ready count, retry-wait count, unacknowledged requests, discovery latency, invocation
duration, and failure/timeout statistics by artifact type. Unbounded identifiers such as Scope, Worker, and request IDs
are not metric labels. Use existing logging, metrics, tracing, and health interfaces rather than adding a task console
or a per-run query API. Preserve the aggregate `artifact_processing_supervisor` readiness check and add per-Family
status. When the `global` Lease remains valid but one Family provider fails, aggregate readiness reports partial
degradation while other Families remain schedulable.

Behavioral acceptance must cover:

1. No concurrent execution for the same binding key; independent Scopes and types run concurrently within their own
   budgets.
2. Long tasks fill one type's capacity while other types still discover and dispatch work; large earlier pages or keys
   in backoff cannot starve later pages.
3. B2 never overtakes a persistently failing B1, while A3 arriving after A completes remains eligible in a later cycle.
4. A processor successfully handles only part of its business input, retaining ordinary remaining work for the next
   interval.
5. Artifact scheduling policies are independent; full capacity does not incorrectly advance the checkpoint, and scan
   completion does not await Worker success.
6. Crashes after automatic admission but before in-memory queuing, and during automatic pagination, lose no requests
   and do not duplicate same-scan admission on earlier pages.
   Continuous registration of new Scopes still permits finite scan completion, while new dirty state for an existing
   Scope can be admitted in a later check.
7. With automatic processing disabled, ordinary dirty work does not run, while explicit triggers and accepted requests
   can still recover.
8. Multiple flushes while queued or running coalesce; success acknowledgment swallows no new trigger, new dirty state,
   or later notification for an already-scanned Scope.
9. Lost post-commit notification, Worker crash, timeout, and failure after partial legitimate business commits all
   recover safely from business progress.
   Duplicate invocations for an acknowledged G consume no new input; a crash between input reference and intent
   publication cannot leave undiscoverable work.
10. Term competition, reelection of the same holder, surviving old Workers, and races between old and new commits never
    permit old-term writes; SQLite tests verify real transaction locking.
11. Switching in both directions between `global` and `dedicated` preserves progress and invalidates old-mode terms;
    SQLite does not accidentally enable multiprocess mode.
12. After Topic Memory migrates from PR #1490's Source Window assignments, Pending, and automatic-wave targets, existing
    uncovered Sources, unacknowledged flushes, running waves, Cursors, and `last_auto_wave_completed_at` are all
    preserved, as is existing API behavior.
13. Profile preserves cron/timezone, startup catch-up, Policy, Cursor, pending Candidate, and background Principal.
    Migration neither generates twice for one cron fire nor loses existing Sources when the APScheduler sidecar stops.
14. Each artifact type separately verifies Source ordering or its business idempotency, Candidate Review boundaries,
    and Handoff authorization boundaries; scheduler tests do not substitute for these checks.
15. Old/new configuration aliases, invalid numbers, unknown modes, interval/cron conflicts, missing capabilities, and
    prefix collisions produce diagnosable startup outcomes.

# Drawbacks

- Independent artifact budgets leave some capacity idle; simultaneous execution across types may consume more total
  resources than the old shared pool.
- `dedicated` adds controllers and Leases without automatically isolating shared database, model, or host failures.
- Generic intent duplicates a small amount of coarse pending information already present in Topic Pending, Profile
  Policy, and other domain state. It exists for scheduling recovery and deduplication and must not become a second
  authority for business consumption progress.
- Unlimited backoff retries do not resolve permanent errors automatically, and uncommitted computation may repeat
  without stage checkpoints.
- Every processor must demonstrate consistency among commit idempotency, completion acknowledgment, and pending markers;
  migration requires more than replacing a scheduler.

# Rationale and alternatives

- The `global` default reduces operational cost. `dedicated` accurately describes scheduling dedicated to each artifact,
  unlike `isolated`, which implies complete resource isolation.
- Two deployment-wide modes avoid arbitrary grouping, per-artifact routes, and online ownership migration without a
  demonstrated use case.
- Scope invocations preserve processor autonomy. Making Source Windows the shared scheduling unit would exclude
  non-Source work and make the Supervisor responsible for domain input selection.
- Type admission before Scope discovery applies independent budgets to discovery, queuing, and execution, rather than
  merely counting Workers at the final launch step.
- Measuring intervals from check starts, recording cron fires as checkpoints, and ending scans independently of business
  completion prevent one failed Scope from obstructing the entire artifact type.
- Persisting coalescible requests and ordinary dirty state while keeping execution queues and backoff in memory provides
  recovery facts without creating a generic Job platform.
- Acknowledging processor success separately from clearing ordinary backlog avoids forcing every invocation to drain
  all input or incorrectly discarding unselected work.

# Prior art

PowerContext's [Topic Memory RFC](1417_topic_memory.md) first defined `ArtifactProcessingSupervisor` and supplies the
foundations of Pending, independent business
Cursors, Worker child processes, SQLite/OceanBase terms, short-transaction publication, flush generations, and in-memory
backoff. [RFC 0019](0019_local_source_memory_runtime.md) provides the Source-driven local processing model;
[RFC 0051](0051_experience_skill_artifact_families.md) and [RFC 0050](0050_artifact_candidate_review_inbox.md) define exact
evidence, Candidates, Review, and version commits for different artifact types. The
[Profile RFC](1485_profile_artifact.md) supplies existing cron, startup catch-up, per-Scope Cursor, Policy, and Candidate
boundaries as another background-processing baseline whose behavior must be preserved.

This document uses those existing domain and storage contracts to unify scheduling entry points. User-facing mounting,
retrieval, Continue, and external actions continue to follow their domain contracts. Artifact Processing Supervisor
also does not take over the operating-system service lifecycle defined by
[RFC 1299](1299_local_server_availability_and_service_installation.md).

# Unresolved questions

There are no product semantics within this scope left to arbitrary implementation choice. Implementation determines
concrete Python registration types, internal page sizes, polling durations, and storage migration scripts, subject to
the boundedness, recovery, and backend acceptance requirements above.

The following are explicitly excluded: per-Scope scheduling overrides, runtime configuration changes, arbitrary groups,
Worker budget borrowing, priority preemption, durable retry history, Job queries/cancellation, model stage checkpoints,
online mode migration, a unified Source consumption algorithm, and a unified artifact Review policy.

# Future possibilities

New Artifact Families with existing domain contracts can register processors directly. Planned concepts without
processors must first establish their own domain designs. Shared model resource budgets, online mode changes, runtime
configuration, and operational controls require separate review when a concrete need arises; implementation convenience
must not add them implicitly to this scheduling contract.
