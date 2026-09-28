- Proposal Name: `artifact_dreaming`
- Start Date: 2026-09-07
- RFC PR: [#1510](https://github.com/oceanbase/powercontext/pull/1510)
- Tracking Issue: [#1509](https://github.com/oceanbase/powercontext/issues/1509)
- Related RFCs: [Candidate and Review Inbox](0050_artifact_candidate_review_inbox.md),
  [Experience and Skill](0051_experience_skill_artifact_families.md),
  [Standard Skill lifecycle](1351_standard_skill_package_lifecycle.md),
  [Access control](1396_handoff_access_control.md),
  [Source and Artifact REST API](1437_source_artifact_rest_api.md),
  [Topic Memory and background processing](1417_topic_memory.md),
  [Artifact Processing Supervisor](1515_artifact_processing_supervisor.md)

# Summary

This RFC defines Artifact Dreaming: background processing that compares existing artifacts and their evidence across
tasks to derive reusable judgments, refine applicability, or propose reusable capabilities. Dreaming produces Candidates
that require review before they become committed Artifact Revisions.

The first release provides two operations:

| operation | Input | Output |
| --- | --- | --- |
| `refine_experience` | Exact Memory entry versions, committed Experience Revisions, or both; optionally supplemented by exact Sources | One new Experience Candidate or a replacement Candidate for one Experience |
| `derive_skill` | Committed Experience Revisions, optionally supplemented by exact Sources | One new managed Skill Candidate using the existing `experience` generation origin |

Each run addresses one question selected by the caller and produces at most one Candidate. Finding no worthwhile change
is a valid result. A DreamRun records exact inputs, policy versions, results, and cost. Users or integrations explicitly
request runs in the first release; execution happens in the background and reuses the Review Inbox. Periodic discovery,
Memory entry changes, Skill revisions driven by usage feedback, and governance for merging artifacts are future work.

This RFC defines the execution and review contract. The implementation reuses committed artifacts, Candidates, and Family lifecycles. DreamRun is not a
new Artifact Family, and the proposal does not change authorization semantics for basic Artifact management writes.

# Motivation

Existing artifacts preserve useful information, but patterns and counterexamples across tasks remain scattered across
Revisions. Users still have to gather related Memory entries and Experiences, decide whether they address the same problem, identify limits
of an earlier conclusion, and call generation to propose a change.

Memory bodies reside in separate Entry Versions; the top-level Artifact contains a manifest and change records. Existing
generic generation serializes the Artifact without expanding those entries. Dreaming resolves exact entry content so
task records preserved in Memory can become reusable Experiences.

For example, three tasks establish that retries helped a read request, retrying a timed-out write created duplicate
records, and an idempotency key prevented replay duplicates. Keeping each result is useful. Comparing them can also
correct an overly broad recommendation to retry every failure and produce a Skill for reviewing retry policies.

Existing Source-window Experience incubation derives Candidates from new Task Outcomes. Dreaming operates on explicitly
selected Memory entry versions, Experience Revisions, and related evidence, potentially spanning sessions and Source windows. It needs its own run
results and must not reuse or advance the Source Cursors of Memory, Experience incubation, or Topic Memory.

The expected benefit is fewer incorrect generalizations, less repeated analysis in later tasks, and verifiable working
methods. Candidate count alone does not measure quality or task benefit.

# Guide-level explanation

## Deriving an Experience from Memory

Within one Scope, a user selects three memories about connection-pool troubleshooting. Each is pinned to its owning
Memory Revision and Entry Version:

| Entry | Content | Original task result |
| --- | --- | --- |
| `entry_pool_diagnosis@ev_2` | Investigation found that an exception path did not return connections | outcome_pool_a |
| `entry_pool_fix@ev_1` | Restoring connection returns resolved the pool exhaustion | outcome_pool_a, the same task as the preceding entry |
| `entry_pool_counterexample@ev_3` | A separate exhaustion incident came from slow queries and recovered after query optimization | outcome_pool_b |

The user selects Experience refinement. Background processing reads the three entry bodies and their sources, groups
the first two under one root evidence group, and keeps the third as a counterexample under different conditions. It
proposes an Experience about distinguishing connection leaks from slow-query occupancy when a pool is exhausted.

The reviewer can follow exact entry version → original task result and see that three memories represent two tasks,
rather than three independent validations. Approval creates a new Experience while retaining the original Memory entries.
Preferences, plans, or descriptions without action outcomes do not automatically become Experiences.

## One refinement

Within one Scope, a user selects three Experiences:

| Artifact | Result recorded by its evidence |
| --- | --- |
| `exp_read_retry@2` | A read request to an external service encountered a transient error and succeeded after retry |
| `exp_write_timeout@1` | An automatic retry after a write timeout created duplicate records |
| `exp_idempotent_write@1` | Replaying a write with a stable idempotency key did not create duplicate records |

The user selects Experience refinement and chooses `exp_read_retry@2` as the replacement target. The system returns a
run_id. Background processing examines these artifacts and available original evidence, then proposes a scoped revision:

> Retry policies should distinguish reads, idempotent writes, and writes with an unknown result. For an unknown write
> result without a deduplication guarantee, establish whether the write committed before retrying. Replay according to
> the verified policy only when the relevant interface provides an idempotency guarantee.

A reviewer can inspect the target Revision, complete proposal, selected evidence, change rationale, and evidence
limitations, then approve or reject an exact Candidate version. Approval creates the next Revision of the same Artifact.

If another writer has advanced the target, approval returns a conflict. The Candidate remains pending. The reviewer reads
the new head before submitting a new proposal or requesting another refinement. An existing Candidate cannot change its
target across versions, and Dreaming does not automatically apply an old proposal to a newer head.

## Deriving a Skill

After approval, the user can select `derive_skill` in a separate run to propose a retry-policy review Skill. Its Candidate
includes applicability, instructions, and validation requirements, then follows existing standard Skill validation,
review, and explicit publication.

A Candidate produced by a Dream cannot be an Artifact input to that run or another Dream. Skill approval does not mean
installation, loading, execution, or distribution to a Receiver.

## Run completion and review completion

~~~text
Select exact Memory entry versions or Experiences
  -> Create DreamRun
  -> Resolve evidence, generate, and validate in the background
  -> Commit Candidate and DreamRun result atomically
  -> Reviewer inspects a Candidate version
  -> Approval creates an Artifact Revision
~~~

A successful run has one of three outcomes:

- `proposed`: one pending Candidate has been persisted;
- `no_change`: existing content is sufficient and no substantive change is warranted;
- `needs_evidence`: the question is useful, but the available material cannot support a complete proposal.

`succeeded` describes execution, not Candidate approval. Execution errors produce `failed`, never a disguised
`no_change`.

## First-release boundary

The first release resolves Memory entries, Experiences, and ordinary generation-evidence Sources within one Scope. Committed Experiences
include content published through Review and content explicitly written through the basic management API. Committed
identity does not make every statement a verified fact. All content produced by Dreaming must first enter a Candidate.

Memory → Experience uses refine_experience and expands bodies through exact Entry Versions in the manifest. Memory
mutation, Skill package input, and Handoff input are outside the first release. derive_skill still takes only Experiences
as direct Artifact inputs: Memory first becomes an approved Experience, then another run can derive a Skill.

# Reference-level explanation

## Domain objects and run inputs

A DreamRun has public identity `(scope_id, run_id)`, not ArtifactRef. It is excluded from Artifact search, PreparedContext,
and subsequent evidence sets. The model cannot allocate run_id, candidate_id, artifact_id, or a target Revision.

Create accepts the following fields; unknown fields return 422:

| Field | Constraint |
| --- | --- |
| `operation` | Required: `refine_experience` or `derive_skill` |
| `artifacts` | Optional, defaults to an empty array; exact Experience ArtifactRefs, never a whole-Memory Ref |
| `memory_citations` | Optional, defaults to an empty array; existing `{memory_ref, entry_id, entry_version_id}` shape; refine_experience only |
| `sources` | Optional, defaults to an empty array; items are `{source_type, source_id}` |
| `target` | Optional or null; supported only by refine_experience and must be an exact Experience Ref in artifacts |
| `idempotency_key` | Required: a nonempty, trimmed string of 1–128 characters |

The Path supplies scope_id. Every reference resolves within that Scope. The request accepts no latest selector,
cross-Scope address, Candidate reference, custom prompt, model name, or arbitrary tool call. After normalization and
deduplication, artifacts and memory_citations together contain 1–20 items; adding sources must not exceed 32 references.
derive_skill requires empty memory_citations and nonempty artifacts. Source-only Dreams are not accepted.

Each memory_ref must have family memory and an exact revision. Both entry_id and entry_version_id must match an entry
in that Revision's manifest. An entry citation identifies a nested Memory item, not a new Artifact identity.

At admission, target must be the current head. Other inputs may be historical Revisions. The model and reviewer must see
their exact versions and historical nature rather than describe them as current state. derive_skill always has a null
target; the first release does not automatically locate or replace an existing Skill.

## Evidence resolution, snapshots, and admission

Admission validates direct references, entry anchors, Families, Source purposes, and the target baseline. On its first execution, the
worker resolves selected entries and artifacts, their source chains, and explicit supplemental Sources, persists an immutable input
manifest, and only then calls the model. Retries use the versions and projections identified by that manifest.

The manifest contains at least:

- exact requested MemoryCitations, ArtifactRefs, SourceRefs, and optional target;
- the reference, content digest, and operation-local evidence ID of each content item actually read;
- a digest of the actual model evidence projection, completeness information, and source roles;
- transform_version, prompt version, model configuration identity, and effective budget.

Memory items also record entry_content_hash, entry state in the cited Revision, the current version observed during
resolution, paths to root Sources, and deduplication groups. Runtime constructs these fields. The model selects assigned
evidence IDs and cannot invent entry identities or source relationships.

MemoryEntry, Source, and Artifact stores retain authoritative content. The manifest creates no Source or second long-term memory store.
Recovery reads the same references and verifies their digests and availability. If the same input cannot be recovered,
the run fails instead of switching to latest. Lineage traversal must deduplicate, detect cycles, and enforce a bound.
The complete model-visible evidence set has at most 32 items and its total projection must fit the budget. A run visits
at most 128 distinct nodes, 256 reference edges, and eight lineage levels. Exceeding any bound produces
evidence_limit_exceeded. The first release does not silently truncate evidence and claim complete provenance.

### Memory entry resolution and admission

The resolver reuses exact Memory citation validation and adds Dream generation admission:

1. Read the authoritative Revision by memory_ref and validate entry identity, version, and hash against its manifest.
   Search summaries and current projections cannot supply missing content.
2. Read the complete MemoryEntryVersion and verify its owning Memory and the content hash covering its body and sources.
   Reuse validate_citation/expand identity and hash checks instead of serializing only the top-level Memory.
3. The entry must be active in the cited Revision and the logical entry must still exist and be active in the current
   authoritative manifest. Historical versions are allowed but labeled historical; a newer current version never
   replaces the selected body. Search hits and caches do not replace state checks.
4. Project only selected entries' kind, text, versions, and source roles, then follow each Entry Version's sources/artifacts.
   Do not expand unselected sibling entries or attribute whole-Memory lineage to every entry.
5. Follow upstream Memory relations only through exact entry citations. A legacy whole-Memory Ref is an unresolved
   relationship, not a request to traverse all entries or a root observation. Exact Experience lineage can be expanded;
   Families without a resolver similarly retain explicit limitations.

Authorize every entry, upstream artifact, and Source read. A declared exact source that is missing, a hash mismatch, or
revoked access fails instead of becoming no_change. Absent original-source links, lineage_only-only ancestry, or unresolved
relations remain evidence gaps. An Experience proposal using Memory must have available, admissible root Sources that
support the relevant actions and outcomes; otherwise return needs_evidence. Unrelated preferences can return no_change.
Entry text, kind labels, and repetition counts do not establish successful execution.

Deactivation while queued, generating, or awaiting review prevents subsequent generation commit or approval. Reactivation
allows validation again; an old snapshot cannot bypass deactivation.

Sources have two relevant purposes:

| Purpose | Model-readable | Independent task evidence |
| --- | --- | --- |
| Ordinary, authorized Source eligible for generation | Yes | Only when source identity and task semantics support that interpretation |
| System `lineage_only` Source | No | No |

The lineage_only Source saved by basic Artifact Create/Replace describes the management input for that Revision. The
resolver records its provenance role and stops expanding it. Its payload, nested content, and a new evidence ID must not
reach the model. An Artifact reference cannot bypass is_generation_eligible. Explicitly submitting such a Source returns
source_not_eligible.

An exact Prompt Revision in Memory or Experience lineage records generation configuration with the `lineage_only` role.
Its body stays outside the model evidence projection, adds no root observations, and does not itself create an evidence
gap. Prompt references alone cannot support an Experience Candidate.

Artifact content can participate as committed derived knowledge, but committed identity, Review status, retrieval count,
and independent original observation are different concepts. Without independent original evidence, the system may
organize committed instructions; it must not invent validated outcomes, success counts, or universally applicable claims.

## Root evidence deduplication and conflicts

A task result restated by Memory entries, entry versions, or Experiences remains one original evidence item. First
deduplicate bodies by `(scope_id, memory_artifact_id, entry_id, entry_version_id)`, retaining all valid MemoryCitation
anchors; then follow entry-level lineage to group root Sources. One entry version appearing in multiple Memory Revisions
does not increase the observation count.

Identical SourceRefs must be deduplicated. Where trusted task-attempt identity or an explicit replay relationship exists,
the corresponding evidence groups must also be combined. Different SourceRefs, similar text, or different Artifact
identities do not by themselves establish independent observations. Preserve uncertainty when independence is unknown.
Descendants created by Dreaming, restatements in older Revisions, and model confidence do not count as new independent
support.

The Run manifest records citation anchor → entry/Experience → root Source group relationships, with the group IDs and
independence annotations used by the model, for reviewers to inspect through Run Get. Project one body per SourceRef while retaining multiple paths.
Different Source bodies in the same task group remain available; grouping only merges independent-support counts.
Unknown independence must not appear as a verified task count.

The prompt distinguishes creation, corroboration, refinement, and correction, checking version, time, environment, and
applicability:

- corroboration requires independent evidence or a meaningful source relationship, not a restatement;
- one valid new evidence item can support an explicit correction; there is no universal three-occurrence rule;
- a generalization must identify its support and counterexamples rather than turn one outcome into a universal fact;
- related conclusions under different conditions retain their boundaries; unresolved conflicts return needs_evidence;
- ingestion time does not override event time, and plans, recommendations, or declarations do not become observed outcomes.

The program validates references, types, and commit invariants. Reviewers assess whether evidence sufficiently supports
the claims. This RFC does not describe semantic requirements as an accuracy guarantee provable by deterministic checks.

## Generation plans and Candidate mapping

Builtin Runtime provides a fixed internal Dream pipeline, reusing Family-owned typed generation and Review writers.
The first release adds no public planner registry or executable pipeline DSL.

The model input's `target_evidence_id` identifies the exact Experience Revision to replace in the evidence projection.
When a target is set, other Experiences provide context and support; only that target may be replaced. A null value
means creating a new Artifact. Runtime constructs this ID from the request target; the model cannot choose a different
replacement target.

The generator returns exactly one of these typed results:

| outcome | Content |
| --- | --- |
| proposed | A complete Family-specific proposal, used evidence_ids, change intent, and reason |
| no_change | A bounded reason, without a proposal |
| needs_evidence | A bounded reason identifying missing evidence, without a proposal |

Change intent is `create | corroborate | refine | correct | derive`: an Experience without a target uses create, an
Experience with a target uses corroborate/refine/correct, and a Skill uses derive. The reason reuses the existing
2,000-character Candidate limit. Unknown evidence IDs, the wrong Family, an invalid proposal, or an incompatible intent
fail generation.

Runtime maps used evidence_ids to exact references. Candidates retain used entries and artifacts plus their required root
source dependencies, rather than treating every request input as support. The resolver adds each cited entry's admissible
root Sources to sources as provenance dependencies; this does not mean every source supports every proposal claim.
A replacement additionally retains its exact target ArtifactRef. Sources, artifacts, and memory_citations together have
a limit of 32 references. Each entry citation counts once, and root Sources are deduplicated.
When a root Source is used, Runtime also retains the selected MemoryCitation/Experience references that lead to it in
the manifest. Returning only a root Source ID cannot discard its entry provenance. If completing references exceeds the
bound, return evidence_limit_exceeded rather than dropping provenance.

For derive_skill, direct Artifact lineage contains only Experiences and uses SkillGenerationOrigin.EXPERIENCE. The
proposal passes existing Skill canonicalization and standard package validation. Dreaming does not generate arbitrary
file-edit commands or execute scripts from the new package.
MemoryCitations inside Experiences can resolve root evidence, but derive_skill does not put those entries directly into
the Skill's memory_citations. Intermediate entries provide provenance; model evidence remains the selected Experiences
and admissible Sources.

### Entry provenance in Candidates and committed Revisions

The shared Candidate envelope and ArtifactLineage gain memory_citations, an array defaulting to empty whose items reuse
MemoryCitation. Only Experiences may have nonempty values in the first release. These are direct entry references,
not a substitute whole-Memory Ref or a synthetic Source carrying copied entry text.

Ordinary Candidate proposal, revision, and approval validate transitive evidence without applying Dream's generation
depth, graph, or projection budgets. Their 32-reference limit applies to direct references, including required Source
dependencies of explicit Memory citations. Sources reachable only through an Artifact reference remain in that Artifact's
lineage; review does not copy them into the Candidate's direct references. Current authorization and direct/transitive
Memory validity checks still apply, with Memory head locks protecting approval against concurrent deactivation.
Review follows stored local Artifact lineage through historical Skill Revisions and upstream Experiences. Proposal,
revision, and approval recheck indirectly referenced Memory entries and current read permissions. This traversal is
independent of Dream's input Family restrictions and does not add Skill bodies to Dream's evidence projection. Prompts
remain generation configuration, and cross-Scope publication boundaries are not implicitly expanded.

- Experience propose and Candidate revise accept this optional field. On revise, omission or null retains the current set, an
  explicit array replaces it completely, and [] removes it. Re-resolve and validate source dependencies; keeping an entry
  citation cannot remove its required root Sources. No new review endpoint is added.
- Candidate Get returns actual memory_citations and sources/artifacts. Reviewers use existing exact Memory entry,
  Artifact, and Source reads to inspect bodies and historical versions, and Run Get to inspect generation-time evidence
  groups. Revision and approval revalidate evidence against the current Candidate version. Bodies are read under current
  reviewer permissions; Run or Candidate references cannot expand read authority.
- Approval copies the current Candidate version's memory_citations into Experience Revision lineage in the same
  transaction as committed content, sources/artifacts, and the review result. Artifact exact read returns these citations;
  subsequent Dreams can follow them.
- The Run manifest records all inputs and processing, while Candidates/Artifacts retain used entries and necessary
  sources. After review edits, provenance follows the new Candidate version rather than relying only on the original
  DreamRun, free-text reason, or Dashboard caches.

Reviewers continue to use revise/approve/reject. Candidates retain their immutable versions and terminal states. DreamRun
records the candidate_id/version created by the run. Later reviewer revisions do not rewrite the run result or reopen it.
A revised Candidate uses its own complete proposal and evidence, validated against its revised evidence set without
requiring an exact match to the original Run manifest. All authorization and source admission rules still apply.

### Dashboard reading boundary

Dashboard is an opt-in personal content viewer requiring a static Bearer token and enforced access control.
It displays approved Experiences and Skills with clickable exact Artifact references and Memory citations. A Skill can
link through its Experience to the original Memory entry version. Reference navigation reuses existing APIs and current
authorization; it never substitutes the latest body for a historical version. Dream creation, Run inspection, and
Candidate review use the existing HTTP API or Client. Dashboard provides no Dream management or Candidate review page.
Evidence provenance and approval validation are backend contracts independent of Dashboard enablement.

## HTTP, Client, and operation permissions

This RFC adds three operations:

| operationId | Method | Path | Success |
| --- | --- | --- | --- |
| `create_dream_run` | POST | `/v1/scopes/{scope_id}/dream` | 202 on initial admission; idempotent replay returns the same run |
| `get_dream_run` | GET | `/v1/scopes/{scope_id}/dream/{run_id}` | 200 |
| `list_dream_runs` | GET | `/v1/scopes/{scope_id}/dream` | 200, cursor pagination |

List accepts optional status, operation, cursor, and limit. The default limit is 20 and maximum is 100. Pagination uses
stable reverse admission order, with run_id breaking creation-time ties; new runs do not duplicate earlier pages. List
returns summaries; Get returns the complete reference manifest and result. Neither returns evidence bodies, credentials,
or raw model responses. Existing exact-read operations provide authorized content access.
The List envelope is `{"runs": [...], "next_cursor": null}`, with a null next_cursor on the last page. Summaries contain
run_id, scope_id, operation, status, outcome, target, candidate, reason, error, and the three timestamps. Timestamps use
UTC RFC 3339; started_at is null before first execution, and completed_at is null before a terminal state.

Example creation of a new Experience from three Memory entries:

~~~http
POST /v1/scopes/scp_project/dream
Content-Type: application/json
~~~

~~~json
{
  "operation": "refine_experience",
  "artifacts": [],
  "memory_citations": [
    {
      "memory_ref": {"family": "memory", "artifact_id": "mem_project", "revision": 7},
      "entry_id": "entry_pool_diagnosis",
      "entry_version_id": "ev_2"
    },
    {
      "memory_ref": {"family": "memory", "artifact_id": "mem_project", "revision": 7},
      "entry_id": "entry_pool_fix",
      "entry_version_id": "ev_1"
    },
    {
      "memory_ref": {"family": "memory", "artifact_id": "mem_project", "revision": 7},
      "entry_id": "entry_pool_counterexample",
      "entry_version_id": "ev_3"
    }
  ],
  "sources": [],
  "target": null,
  "idempotency_key": "pool-lesson-20260907-01"
}
~~~

The 202 response includes a Location header pointing to the run URI within the same Scope. Example completed Get result:

~~~json
{
  "scope_id": "scp_project",
  "run_id": "dr_01",
  "operation": "refine_experience",
  "status": "succeeded",
  "outcome": "proposed",
  "target": null,
  "candidate": {"candidate_id": "cand_01", "version": 1},
  "reason": "Three memories trace to two tasks and refine connection-pool troubleshooting boundaries.",
  "error": null
}
~~~

This example omits manifest, timestamps, and usage. The full response also includes accepted_at, started_at, completed_at,
attempt_count, input_manifest (null until resolved), usage (unknown values are null), and effective server-owned budgets.
For queued/running, outcome, candidate, and error are null. For failed, outcome/candidate are null and error contains a
stable code. For succeeded, error is null and candidate is non-null only for proposed.

| Operation | First-release authorization |
| --- | --- |
| Create | scope.read and scope.contribute on the Scope; target artifact.write for a replacement |
| Get / List | scope.read on the Scope; no individual Run sharing |
| Candidate revise/approve/reject | Existing Review authorization and expected_version |
| Scheduling and model configuration | Server deployment configuration authority, never granted by a Dream request |

Reuse existing AccessAction and ResourceRef values without adding dream.* roles. An Artifact sharing grant alone cannot
provide Scope-wide source access through DreamRun. Reading a reference does not automatically authorize related sources;
resolution checks each item against existing authorization and generation admission.

Background work stores a stable requester identity, not credentials, and does not expand that identity's permissions by
running as a service. Recheck required permissions before reading input and before committing a Candidate. Revocation
produces access_revoked and stops generation or commit. Queries also check current permissions.

Python Client and Runtime expose matching create/get/list operations. CLI may project them as dream run/show/list. If
exposed through MCP, creation is annotated as non-read-only and queries as read-only; permissions and evidence boundaries
remain identical. Existing artifact-candidates and Skill publication APIs retain review and distribution ownership.

## Idempotency, transactions, and recovery

Admission is unique on `(scope_id, principal_id, idempotency_key)`. The server calculates a request digest after
normalizing references:

- the same key and request return the original run without execution; queued/running return 202 and terminal runs 200;
- the same key with a different request returns 409 idempotency_conflict;
- admission pins policy and budget; first execution pins model configuration identity, which retries and takeover preserve;
- replaying a failed run's key returns that failure; an explicit retry uses a new key;
- the first release does not guarantee semantic deduplication across different keys. Automatic discovery requires a
  stable work key and suppression of previously rejected proposals before release.

After validating identity, request shape, and current Scope read permission, look up idempotency before applying new-Run
admission checks. Return a matching record under current query authorization. An advanced target, removed model
configuration, or full capacity does not prevent replay of the existing result or trigger generation.

The pc_dream_runs table stores normalized request, manifest, generation configuration identity, budgets, status, attempt,
request generation, and result. Admission commits the Run and its Family invocation intent in one transaction. Add a nullable serialized memory_citations column to both pc_artifact_candidate_versions and
pc_artifacts, decoding old rows as empty collections. Candidates and Artifacts retain their existing tables and relations.
No Memory copy table or entry-evidence relation table is needed. Idempotency, status, and results reside in the authoritative
database; committed entry provenance does not depend on Run retention or logs.

Run states are `queued | running | succeeded | failed`. The Supervisor term is the sole execution authority. A monotonic
Run attempt generation protects against stale attempt updates without a separate Run lease or heartbeat. First execution
records an absolute deadline and model configuration identity; retries and takeover reset neither. Before execution,
model_config_id is null. The persisted input manifest remains immutable.

Model calls, content projection, and necessary package preparation happen outside the write transaction. Success uses one
short transaction:

1. Check the Supervisor holder, generation, term validity and request generation in the same transaction, then check
   Run attempt generation, state, current authorization, exact active entries, source availability and target head.
2. Create at most one Candidate through a Review writer bound to the same transaction.
3. Persist Candidate ownership, outcome, exact Candidate reference and completed_at; mark the Run succeeded and
   acknowledge the Scope invocation. Atomically retain or request a successor invocation for remaining Runs.

No change and insufficient evidence also persist reasoned terminal results without creating Candidates. Any failure rolls
back the whole transaction. A Candidate must not exist without a corresponding confirmed run result. If the transaction
committed but its response was lost, recovery reads the terminal state without calling generation again.

Inputs always use manifest versions. A newer non-target Revision neither rewrites the snapshot nor counts as processed;
the Run manifest and exact Candidate references retain the historical baseline. If the target advances during generation, the run
fails with artifact_conflict. If it advances after Candidate persistence, existing approval CAS prevents publication.

A timeout, transient network error, or worker crash allows at most one additional attempt within the total budget.
Deterministic input errors, revocation, target conflict, and invalid model output fail immediately. Supervisor takeover and
internal provider retries count toward the total attempt/call budget. Unconfirmed usage is unknown, not zero cost.

Approval of a Dream-origin Candidate must recheck the availability and admissibility of the evidence actually referenced
by its current version. Implementation may strengthen shared Review evidence validation, but must not add an
auto-approval path. A reviewer-revised Candidate is checked against its revised evidence set.
Memory checks include direct and transitive exact anchors, hashes, current active state, and required root Sources. Evidence read permissions
for approval use the reviewer's current identity. Final state checks and Candidate/Artifact commit share a transaction,
serialized against Memory deactivation through the owning Memory head lock. Later source changes preserve historical
provenance and appear as unavailable during later use; automatic cascading retraction of committed Artifacts is deferred.

## Execution ownership, budgets, and existing processors

Dream executes through the [Artifact Processing Supervisor](1515_artifact_processing_supervisor.md), with no independent
scheduler, polling dispatcher or Run lease. DreamRun is a business request record, not an Artifact Family or generic Job history.

| Dream operation | Output Family | Supervisor binding |
| --- | --- | --- |
| refine_experience | experience | The existing canonical Experience incubation binding |
| derive_skill | skill | skill.dream.v1 |

Each Family retains one binding. The Experience processor first selects the oldest pending Dream within the claimed
request generation and executes one attempt; otherwise it runs its existing Source incubation. A Dream invocation never
advances or clears the Experience Source Cursor. Skill Dream has no automatic Source scan. Neither operation discovers
new artifacts to refine automatically or gains permission to approve Candidates from a schedule.

The API transaction stores exact request inputs, requester identity and Run while advancing the output Family's Scope
request generation. It wakes the local Supervisor after commit. OceanBase uses existing cross-process discovery; SQLite
adds no idle database polling. A rolled-back admission leaves neither a Run nor an orphaned intent. Idempotent replay
never increments the scheduling request generation.

Workers select only Runs accepted at or before their claimed generation. Wakeups may coalesce, while every Run retains
its own inputs, principal, result and budget. Later requests cannot be acknowledged by an earlier invocation. An attempt
commits its terminal result or bounded retry state together with invocation acknowledgement. Remaining Runs atomically
retain or create a successor request, so explicit work completes with automatic scheduling disabled. Ordinary Source
dirty state remains owned by Experience incubation and is not cleared when Dream finishes.

The Supervisor owns subprocesses, per-Family Worker capacity, Scope single-flight, timeouts and takeover. Global mode
shares one controller term; dedicated mode separates terms by Family. Both modes use experience_max_workers and
skill_max_workers, each defaulting to 1, with respective Scope Worker timeouts defaulting to 600 seconds. These execution
limits are separate from each Run's cumulative model, evidence and elapsed-time budgets. The child constructs its Dream
generator using the selected Run's budget. The foreground generation_concurrency semaphore is not a global subprocess quota.

Deterministic errors and budget exhaustion finish the Run as failed and acknowledge the invocation; they never cause
unlimited model retries. The Supervisor recovers incomplete transactions, crashed Workers and lost leadership using the
same Run, fixed input, cumulative attempts and absolute deadline. Candidate, ownership, Run outcome and invocation
acknowledgement commit atomically. Every stale-term write transaction fails in full. Review waiting consumes no Worker,
and terminal Runs never invoke the model again.

OceanBase supports all and split api/background roles. SQLite retains one all-role host with either Supervisor mode.
The API may declare Experience/Skill through artifact_processing_families without configuring a generation model;
background instances supply execution resources. Child generators and authorization adapters must be reconstructable,
without relying on parent-process closures. Dream always checks the original requester's current permissions rather
than borrowing a background service principal's evidence or write access.

The dream_enabled setting controls new admission, and dream_max_pending_per_scope bounds unfinished Runs per Scope.
Missing output-Family capability returns 503 capability_unavailable for new work; existing results remain readable and
replayable under current query authorization. The /v1/scopes/{scope_id}/dream create/get/list operations and existing
Review APIs remain the business interfaces; no generic Job API is added.

Initial server-side budgets may be tightened by the deployment, never relaxed by API callers:

| Budget | First-release limit |
| --- | --- |
| Explicitly selected MemoryCitations and Experiences | 20 combined |
| Model-visible evidence items / Candidate references | At most 32 each |
| Lineage traversal | At most 128 distinct nodes, 256 edges, eight levels; metadata-only nodes also count |
| Total evidence projection in UTF-8 | 65,536 bytes; fixed system instructions are separate and provider context limits still apply |
| Output per model call | 4,096 tokens |
| Total model calls per Run | 2, including retries |
| Total time from first execution | 120 seconds, including retries and takeover |
| Candidates per Run | 1 |

Shared concurrency and pending capacity require deployment limits. Full admission capacity returns 429 with Retry-After.
Joining the shared executor must not bypass foreground or other background budgets. Run usage includes every call, not
only the last successful one.

## Errors and compatibility

| Stage | Result |
| --- | --- |
| Missing identity / insufficient permission | 401 / 403 using the existing error envelope |
| Missing direct reference | 404 following the current resource-visibility policy |
| Invalid type, excessive references, explicit lineage_only Source | 422 with a stable reason |
| Memory anchor/hash mismatch or inactive entry | 422 invalid_memory_citation / memory_entry_inactive |
| Stale target / idempotency conflict at admission | 409 artifact_conflict / idempotency_conflict |
| Missing execution capability / full capacity | 503 / 429 without a Run |
| Evidence expansion exceeds limits or becomes unavailable after admission | Run failed: evidence_limit_exceeded / evidence_unavailable |
| Permission revoked or target changed after admission | Run failed: access_revoked / artifact_conflict |
| Memory entry deactivation or content-integrity failure after admission | Run failed: memory_entry_inactive / evidence_unavailable |
| Inactive entry or unavailable evidence at approval | 422 memory_entry_inactive / evidence_unavailable; Candidate remains pending |
| Inference failure, invalid output, exhausted budget | Run failed: generation_failed / invalid_generation_output / budget_exceeded |

GET of a failed run returns 200 with structured error data; execution failure is not a query failure. Errors and logs
contain stable codes, identities, counts, and usage rather than evidence bodies, full prompts, or credentials. Proposals
and reasons are always rendered as untrusted content.

Implementation adds Dream OpenAPI operations/schemas, generated Python Client bindings, Runtime entry points, a Run
repository, Supervisor Family adapters, Memory resolver, Family generation adapters, Candidate/Artifact entry citations, and Inbox
provenance display. Reuse the existing MemoryCitation schema and extend Experience propose, Candidate revise/get/list,
and Artifact exact read citation contracts. ArtifactDraft/Repository must carry and persist entry citations. Existing
reference parameters retain their semantics; entry citations are stored in the current Candidate version.
Constrained model output becomes a pure generation plan before the Run transaction invokes Review writers. Calling independently committing high-level generate
APIs and recording success afterwards does not satisfy the transaction contract.

A cross-Scope published copy follows existing publication_source provenance to the original Revision's entry citations;
MemoryCitations must not be reinterpreted in the destination Scope. Reading original evidence still requires separate
authorization. First-release Dreaming does not expand evidence bodies across Scopes.

Dream tables are created directly from this design, without migration compatibility for unpublished Dream schemas.
Entry citation columns in existing Candidate/Artifact tables use additive schema updates on SQLite and OceanBase.
Existing data needs no rewrite. Existing Memory flush, Experience incubation,
Handoff, basic Artifact management, PreparedContext, and Skill distribution behavior remains. OpenAPI and generated
bindings are updated together with the implementation.

## Acceptance

| Scenario | Observable result |
| --- | --- |
| Only MemoryCitations, no Experience input | Exact entry bodies produce a new Experience Candidate; approval preserves entry and root Source provenance |
| Wrong entry version, corrupted hash, or whole-Memory Ref | Admission fails without substitution from latest, summaries, or neighboring entries |
| One entry selected from a Memory | Only that entry and admissible sources reach the model; unselected entry bodies are excluded |
| Three memories and one Experience share two task results | Preserve citation paths and only two root groups; unknown task independence is not a validation count |
| Only preferences, plans, or no available action-outcome evidence | no_change or needs_evidence, without invented observations |
| Current entry version advances / entry becomes inactive | Pinned historical body with a version-difference indicator / no commit or approval |
| Reviewer changes or removes MemoryCitations | New Candidate version revalidates sources; approved lineage matches that version |
| Derive a Skill from the approved Experience | Follow MemoryCitations to deduplicate roots; Skill direct Artifact lineage remains Experience and Dream reports never become evidence |
| Cyclic lineage or expansion over budget | Cycle detection terminates traversal; exceeding a bound fails without a partial Candidate |
| Three independent task Experiences refine an earlier judgment | One grounded pending Candidate with target; approval creates the next Revision of the same identity |
| Multiple Experiences restate one root Source | No increase in independent support or invented repeated validation |
| Inputs involve lineage_only or cross-Scope sources | No model disclosure; rejected or marked unavailable according to admission rules |
| Content already sufficient / evidence insufficient | succeeded + no_change / needs_evidence without a Candidate |
| Model invents a reference or returns the wrong Family | failed without Candidate or Artifact writes |
| Replay same key / change request under the same key | Same Run / 409 |
| Takeover after a crash and a late old worker | At most one committed result; old generation cannot write |
| Coalesced wakeups or requests arriving during generation | Each Run executes independently; newer generations survive and successors run without an automatic schedule |
| Dream shares the Experience Family with Source incubation | Dream completion neither advances the Source Cursor nor clears ordinary Source dirty |
| Model-free API with model-configured background processes | OceanBase global/dedicated modes support cross-process admission, generation, and review |
| Failure within Candidate transaction / response lost after commit | Full rollback and retry / recovery of existing terminal state without duplicate output |
| Target advances during generation or before approval | Run conflict / approval conflict without overwriting the new head |
| Authorization revoked or evidence unavailable after queuing | No continued unauthorized read or commit |
| derive_skill succeeds | Pending Skill with existing review and package validation; no automatic execution or distribution |
| Queries match pending, rejected, or DreamRun text | Excluded from Artifact search and PreparedContext |

SQLite and OceanBase share persistence, Supervisor fencing, atomic commit, and review behavior tests. End-to-end acceptance must also
use a real configured model and verify visible errors and usage; mock tests do not establish actual generation quality.

The real acceptance entrypoint reads inference and OceanBase configuration from `.env`, runs local tasks with explicit
fault injection to create Memory evidence, and uses real HTTP, Supervisor subprocesses, and the LLM for Experience/Skill
generation, review, recall, and entry deactivation checks. It cleans up its isolated databases on exit:

```bash
uv run python -m tests.e2e.artifact_dream_real --env-file .env \
  --case sqlite-global --output /tmp/dream-sqlite-global.json
uv run python -m tests.e2e.artifact_dream_real --env-file .env \
  --case oceanbase-split-dedicated --output /tmp/dream-oceanbase-split-dedicated.json
```

The complete matrix also includes `sqlite-dedicated`, `oceanbase-global`, `oceanbase-dedicated`, and
`oceanbase-split-global`. Memory extraction uses a deterministic adapter; Dream generation uses the configured real model.
Capability acceptance does not establish statistical evidence of Candidate quality or later task improvements.

For effectiveness, separate historical inputs from later held-out tasks by time. Compare no dreaming, ordinary summaries,
and this design, recording acceptance, reviewer edits, incorrect generalizations, later task outcomes, and total foreground
and background token cost. Held-out answers must not enter Dream inputs. The first release must include at least one
complete real task evidence → Memory entries → Experience Candidate → review → later task use example, covering repeated
root sources and entry deactivation. Automatic discovery requires acceptable quality and review load before release.

# Drawbacks

- Controlled refinement adds model cost and review effort. Repeated requests without new evidence may produce similar
  Candidates.
- Root evidence and historical context make resolution more complex; reference and input budgets can require a smaller
  selection.
- Human review and type validation cannot guarantee correct generalization. Later tasks must validate utility, and the
  first release promises no specific improvement percentage.
- Entry provenance requires extending existing Candidate/Artifact serialization and review validation. Memory mutation
  and Skill package revision still need dedicated change contracts.

# Rationale and alternatives

Calling existing generate operations on a timer requires little code, but lacks durable results, idempotency, and recovery
confirmation. Independently committed Candidates cannot be atomic with run completion. This design reuses Family
capabilities while adding bounded execution and a shared commit boundary.

Directly overwriting committed artifacts reduces review effort but lets incorrect generalizations immediately affect
later tasks. Candidates and Revisions expose concrete changes and reuse CAS against concurrent updates, so every Dream
output follows Review.

Full nightly summaries can repeat spending and obscure applicability differences. Light/REM/Deep stages or multiple
agents are not prerequisites for the first release's benefit. Validate explicit small selections and later use before
choosing automatic discovery policies.

Without Dreaming, users can still call generation manually but must organize evidence, retries, and tracking themselves.
That option suits occasional work and provides less support for reliable background refinement.

# Prior art

The following mechanisms were inspected in fixed open-source snapshots on 2026-09-07. They inform the design without
promising equivalent runtime outcomes:

| Project | Mechanism informing this RFC | PowerContext boundary |
| --- | --- | --- |
| [ReMe](https://github.com/agentscope-ai/ReMe/blob/354837f9af94cb8f0df13fc66a10380895a0343d/reme/steps/evolve/dream/integrate.yaml) | Cross-artifact abstraction, creation, corroboration, refinement, and correction | Publish through Candidates instead of directly changing committed content |
| [Honcho](https://github.com/plastic-labs/honcho/blob/be54355545b64ddb10203829d323861f52423685/src/utils/agent_tools.py#L1531) | Original observations separated from Dream-derived conclusions | Deduplicate root evidence; descendant counts do not establish independence |
| [Letta Code](https://github.com/letta-ai/letta-code/blob/701f2a5367828847313876c735ade27b9df97689/src/agent/memory-worktree.ts) | Isolated changes, conditional integration, and successful consumption | Exact target, Candidate version, and transactional completion |
| [Hindsight](https://github.com/vectorize-io/hindsight/blob/f19c424e0c5833d5219185c1fa0dcc6a10fc0a81/hindsight-api-slim/hindsight_api/engine/consolidation/consolidator.py) | Atomic generation-plan changes and input progress | Candidate and terminal Run result in one transaction |
| [Cognee](https://github.com/topoteretes/cognee/blob/e93a4f0c76e6af27142c25189f14c200e1749d51/cognee/modules/memify/skill_improvement.py) | Skill improvement proposals driven by usage results | Future extension retaining Revision and package lifecycles |
| [OpenClaw](https://github.com/openclaw/openclaw/blob/233dabe750abe9deb7aa73a67b9b9a28e5cca85e/extensions/memory-core/src/dreaming-consolidation-candidates.ts) | Source admission and derived-content isolation | lineage_only and run reports do not become new facts |
| [EverOS](https://github.com/EverMind-AI/EverOS/blob/8754365c76daa2f13521fcd29a53044bba083403/docs/reflection.md) | Incremental cluster processing and replacement links | Multi-artifact merges require separate atomic governance and recovery |

# Unresolved questions

This RFC fixes the first-release operations, inputs, review, transactions, and permissions; no blocking contract is left
for the model to decide. Automatic discovery thresholds, deployment quotas for shared execution, and cross-Family merge
governance require experiments or separate design and do not block manual refinement.

# Future possibilities

- Periodically select topics with new root evidence, corrections, or usage feedback; use stable work keys and suppress
  repeated no-change or rejected proposals.
- Add dedicated input resolvers for Handoff, complete Skill packages, and Topic Memory; define a separate Memory entry
  mutation contract.
- Revise an exact Skill Revision from real usage Sources through the existing usage origin and package validation.
- Express merges, replacement links, deprecation, and recovery through atomic change groups rather than treating a
  single Candidate as a multi-target transaction.
- Maintain evolving topic documents or rehearse in isolated environments. Synthetic results retain their origin and do
  not become purported real execution evidence.
