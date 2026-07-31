- Proposal Name: `artifact_candidate_review_inbox`
- Start Date: 2026-07-29
- RFC PR: [oceanbase/powercontext#50](https://github.com/oceanbase/powercontext/pull/50)
- Related RFCs: [RFC 0001](0001_product_definition_and_vision.md), [RFC 0014](0014_memory_layer_design.md),
  [RFC 0019](0019_local_source_memory_runtime.md), [RFC 0028](0028_context_pack.md)

# Summary

This RFC defines Artifact Candidate and Review Inbox for Artifact Families that require explicit Review confirmation.

Whether content enters Review is not chosen by the user and does not depend on whether an LLM is involved. It is fixed by
the Artifact Family: Memory continues to write directly to an Artifact, while Experience and Skill must first become a
pending Candidate and can form an Artifact Revision only after approval. Task Outcome is not yet defined, so this RFC does
not assign it a Review policy. Handoff is a downstream result composed from these sources and is not treated as a peer
Family in this RFC's Review policy.

A Candidate is a persisted, untrusted proposal, not an Artifact. It does not enter search or PreparedContext. Review Inbox
is a query view of pending Candidates in the current scope. The first implementation ships with the Experience Artifact
Family, and Skill reuses it later. This RFC does not introduce a generic workflow framework without a real consumer.

This review must confirm four decisions:

1. Review policy is fixed on the Artifact Family; the user does not provide a mode.
2. Memory writes directly, while Experience and Skill require Review.
3. Pending and rejected Candidates are fully isolated from Artifact retrieval and PreparedContext.
4. Review operations are exposed consistently through HTTP, the Python Client, the CLI, and MCP; MCP is not a separate
   approval-policy boundary.

# Motivation

Different Artifacts carry different risks.

Memory stores facts, conventions, and preferences. It is written frequently and can retain exact evidence. Requiring
separate Review confirmation for every Memory write would turn Review Inbox into a routine operational burden.

Experience and Skill are higher-order Artifacts. Experience derives a situation, action, outcome, and lesson from
multiple results. Skill distills Experience into reusable steps. Incorrect generalization, semantic merging, or operating
steps can affect many later tasks, so these Artifacts require explicit Review.

PowerMem has demonstrated the value of Experience and Skill distillation, deduplication, and merging. It also shows the
governance risk of a direct `distill -> merge -> store` path. PowerContext preserves automatic generation while separating
the generation and confirmation of higher-order Artifacts:

```text
Memory -> Artifact Revision

task result evidence -> Experience Candidate -> Review Inbox -> Experience Revision
Experience           -> Skill Candidate      -> Review Inbox -> Skill Revision

Memory + optional Task Outcome + approved Experience/Skill -> handoff context
```

The existing Source-to-Memory path remains unchanged:

```text
Source window -> CandidatePipeline.extract() -> Memory Revision
```

# Guide-level explanation

## Which Artifacts require Review

The initial version uses a fixed policy. It does not provide user configuration or a generic rules engine:

| Artifact Family | Review policy | Result |
| --- | --- | --- |
| Memory | `direct` | Create a Revision through the existing Memory write path |
| Experience | `review` | Enter Review Inbox first and create a Revision after approval |
| Skill | `review` | Enter Review Inbox first and create a Revision after approval |

`direct` does not mean creating an automatically approved Candidate. It means no Candidate is created. `review` means
that creation and modification for the Family must enter Review Inbox before an Artifact Revision can be committed. Each
new Artifact Family must declare its policy in its own RFC. The Runtime does not guess a default for an unknown Family.

## Example 1: write ordinary Memory directly

A user asks Codex to remember a project convention:

```text
"Run contract tests after changing OpenAPI."
```

Codex calls `remember_memory`, and the Runtime creates a Memory Revision through the existing path. The user does not
provide a mode and does not need to approve the write again in Review Inbox. `POST /v1/memory/flush` also retains its
existing behavior of writing Memory directly from a Source window.

## Example 2: Experience requires Review

A task system provides bounded result evidence, including the goal, changes, and test results. The Runtime derives this
proposal:

```text
Candidate: cand_exp_123@1
Family: experience
Proposal: "After changing OpenAPI, regenerate the Client and run contract tests."
Evidence: source:task-result/run_42
Status: pending
```

This is cross-task Experience. While pending, it does not enter Artifact search or PreparedContext. The Runtime commits an
Experience Revision only after a reviewer approves it.

## Example 3: revise Experience before approval

The model proposes an overgeneralized Experience:

```text
cand_exp_124@1: "Run contract tests after changing any YAML file."
```

The reviewer calls `revise` with a complete replacement proposal:

```text
cand_exp_124@2: "Run contract tests after changing openapi/powercontext.yaml."
```

Version 1 remains immutable, and version 2 remains pending. The reviewer approves version 2 after confirming it, ensuring
that the committed content exactly matches the reviewed content.

## Example 4: reject a Skill

The Runtime distills a Skill from approved Experience, but the proposal has no failure handling:

```text
Candidate: cand_skill_7@1
Family: skill
Steps: modify spec -> generate client -> run tests
Failure handling: missing
Status: pending
```

The reviewer rejects the Candidate. It does not form a Skill Artifact and cannot be published, mounted, or included in
Context Pack. Even after a Skill is approved, execution and mounting remain subject to later Skill governance. Artifact
approval is not execution authorization.

# Reference-level explanation

## Family review policy

Review policy is a built-in Runtime Family contract, not a parameter on each request:

```text
direct families: memory
review families: experience, skill
```

- A `direct` Family retains its own validation, identity, Revision, and CAS semantics.
- Public create and modify operations for a `review` Family produce a Candidate instead of committing an Artifact.
- Only Review approval can call the internal Artifact commit of a `review` Family.
- Policy does not vary by caller, MCP, HTTP, CLI, LLM, or rule.
- The initial version adds no policy DSL, dynamic registry, tenant-level switch, or per-request override.

The first implementation ships with the Experience Family. Skill reuses the same Candidate envelope and lifecycle when it
is introduced. Experience and Skill content schemas are defined by their respective RFCs; this RFC does not predefine an
arbitrary JSON payload. Task Outcome's schema, lifecycle, and Review policy, as well as Handoff composition and
persistence semantics, are left to later RFCs.

## Candidate model

A Candidate consists of a family-neutral envelope and a family-owned typed proposal:

| Field | Meaning |
| --- | --- |
| `scope_id + candidate_id + version` | Exact optimistic-concurrency reference for the Candidate |
| `family` | `experience` or `skill` |
| `status` | `pending`, `approved`, or `rejected` |
| `proposal` | Complete, strongly typed proposal defined by the corresponding Family |
| `sources/artifacts` | Exact evidence supporting the proposal; at least one reference is required |
| `target` | Exact active ArtifactRef when modifying an existing Artifact |
| `reason` | Untrusted explanation that does not affect authorization or ranking |
| `result_artifact` | Exact Artifact Revision produced after approval |
| `decision_reason` | Reviewer-supplied rejection reason; absent for other states |

A Candidate reference is not an `ArtifactRef`. A Candidate has no Artifact identity and cannot be read by the Artifact
catalog, a Family search index, or the Context builder.

## Lifecycle and concurrency

```text
create version 1 -> pending
pending --revise--> pending version N+1
pending --approve-> approved
pending --reject--> rejected
```

`approve` confirms only the current Candidate version and cannot modify the proposal at the same time. To change content,
the reviewer first calls `revise`, creates a complete new version, and then approves that version. `approved` and
`rejected` are terminal states; the initial version does not support reopening them.

A Candidate that modifies an existing Artifact must also include its exact `target` in `artifacts` evidence. This keeps
the direct predecessor in the approved Revision's lineage instead of relying only on a temporary target field in the
Candidate envelope.

Every Review write requires `expected_version`. The Runtime performs two concurrency checks:

1. Candidate version CAS prevents a reviewer from acting on a stale proposal.
2. Family target CAS prevents a proposal from being committed against an Artifact head that has changed.

A stale Candidate returns `candidate_conflict`, and a stale target returns `artifact_conflict`. The Runtime does not
perform an automatic three-way merge.

## Persistence and transactions

Builtin Runtime uses two logical tables:

- `artifact_candidate_heads` stores the current version, status, and approval result.
- `artifact_candidate_versions` stores each immutable proposal version.

Review Inbox queries pending heads and their current versions. It adds no queue, assignment, or search-index table. Model
generation runs outside the database transaction, and a Candidate appears in the Inbox only after its write completes.

During approval, the Family Artifact commit and the Candidate's `approved` status must be committed in the same database
transaction. Any failure rolls back the entire operation and leaves the Candidate pending. SQLite and OceanBase must pass
the same lifecycle, CAS, rollback, and isolation contract tests.

## API and compatibility

OpenAPI remains the source of truth for the HTTP contract. Review Inbox adds:

| operationId | Purpose |
| --- | --- |
| `list_artifact_candidates` | Page by scope, status, and optional family; list only pending items by default |
| `get_artifact_candidate` | Read the current Candidate head |
| `approve_artifact_candidate` | Approve by expected version without accepting content changes |
| `reject_artifact_candidate` | Reject by expected version and reason |
| `revise_artifact_candidate` | Submit a complete replacement proposal by expected version |

The initial Experience vertical slice also exposes `propose_experience` and `get_experience`. The former creates only a
pending Candidate; the latter reads an approved Experience Revision only by exact `ArtifactRef`. Their HTTP paths are
`/v1/experience/propose` and `/v1/experience/get`. The five Review paths are under `/v1/artifact-candidates/`.

The generated Python Client exposes the same operations, the CLI provides
`candidate list/show/approve/reject/revise`, and MCP projects all five Review operations as tools. Transport does not
change Candidate validation, `expected_version` checks, or the atomic approval transaction. PowerContext does not treat
MCP visibility as an authorization boundary; deployments that require reviewer separation must control access to the
MCP endpoint.

This RFC does not modify the existing Memory contract:

- `POST /v1/memory/flush` continues to process a Source window into Memory.
- `remember_memory`, `revise_memory_entry`, and `retire_memory_entry` remain unchanged.
- `MemoryRememberMode` and its existing behavior remain unchanged. It controls only how Memory content is generated and
  is unrelated to Review.
- The Codex Hook, existing Memory MCP tools, and `prepare_context` contract remain unchanged; Review adds five MCP tools.

Candidate and Review code ships with the first Experience vertical slice. The project does not first deliver
infrastructure consisting only of tables and unused APIs.

## Retrieval and trust boundary

The following invariants must hold:

- Pending or rejected Experience and Skill Candidates do not enter Artifact or Family search.
- Pending or rejected Candidates do not enter `prepare_context`.
- Only Experience or Skill Artifacts produced by successful approval may become future Context contributors.
- Candidate status is not a retrieval-ranking signal.
- Candidate content is always displayed as untrusted data; body, reason, and evidence previews are not logged.
- `scope_id` remains a business partition, not authentication or ACL.

PreparedContext currently reads only active Memory. Whether Experience and Skill enter Context Pack is decided by a later
multi-Artifact Context Profile RFC. Candidate approval does not automatically expand Context Pack.

## Acceptance

| Scenario | Passing condition |
| --- | --- |
| Family routing | Memory writes directly to an Artifact; Experience and Skill produce only Candidates |
| Inspect | Client and CLI can show family, proposal, version, and exact evidence |
| Gate | Pending and rejected Candidates do not enter Artifact search or PreparedContext |
| Revise | Create an immutable next Candidate version that remains pending |
| Approve | An expected version succeeds only once; Artifact commit and approved status are atomic |
| Conflict | A stale Candidate or Artifact target returns a typed conflict without automatic merge |
| Reject | Write no Artifact; a terminal Candidate cannot be approved or revised again |
| Compatibility | Existing Memory flush, HTTP, MCP, Hook, and PreparedContext behavior remains unchanged |
| MCP parity | MCP can list, read, revise, approve, and reject Candidates with the same lifecycle and CAS rules |

# Drawbacks

- Memory does not go through Review, so incorrect content must be corrected through its existing revision semantics.
- Experience and Skill are not available immediately after generation; they must wait for explicit Review.
- Candidates may accumulate without assignment, notifications, or bulk operations.
- A fixed policy is simple, but every new Family must explicitly choose `direct` or `review`.
- Candidate heads and versions add storage and migration costs.

# Rationale and alternatives

| Option | Decision |
| --- | --- |
| Review every Artifact | Rejected; it would create too much review work for high-volume Memory and task records |
| Let the user select a mode for each request | Rejected; it is easy to choose incorrectly and lets callers bypass Family governance |
| Decide based on whether an LLM is involved | Rejected; the Runtime cannot reliably determine whether a model exists behind the call chain |
| Fix policy by Artifact Family | **Adopted**; the rule is stable and explainable and adds no user parameter |
| Build a generic policy engine now | Rejected; only two `review` Families exist and there is no need for dynamic rules |
| Make Candidate an Artifact Family | Rejected; unreviewed content would receive Artifact identity too early |

# Prior art

- RFC 0014 treats Memory pipeline output as an untrusted candidate but still commits it directly after Memory validation;
  this RFC does not change that path.
- RFC 0019 defines the Source window, cursor, and Memory flush; this RFC preserves their public behavior.
- RFC 0028 specifies that PreparedContext currently reads only active Memory; pending Candidates remain isolated from it.
- PowerMem's Experience and Skill distillation, deduplication, and merging provide a reference for generation, but
  automatic content review is not Artifact approval.

# Limits and deferred work

The initial implementation uses these fixed limits:

- A Candidate may contain at most 32 exact evidence references across `sources` and `artifacts`.
- `reason` and the rejection-only `decision_reason` are limited to 2,000 characters each.
- Inbox pages contain 50 items by default. Callers may choose a `limit` from 1 through 100 and continue with
  `next_cursor`.
- Each Family's typed schema limits its proposal payload. Each of the four initial Experience fields is limited to
  8,000 characters.

Experience and Skill generation rules and Family write semantics are defined by their own RFCs. Candidate retention,
reviewer identity, RBAC, notifications, bulk review, and multi-IDE UI remain future work.

# Future possibilities

The later dependency chain is:

```text
task result evidence -> Experience Candidate -> approved Experience
approved Experience  -> Skill Candidate      -> approved Skill

Memory + optional Task Outcome + approved Experience/Skill
  -> multi-Artifact Context Profile
  -> PreparedContext (ephemeral) / Handoff Artifact (persisted when replay or audit is required)
  -> retrieval quality evaluation
```

- Semantic Experience merging can produce only a new Candidate; it cannot overwrite an approved Artifact directly.
- Skill approval does not automatically grant execution, publication, or mounting permission.
- Context Pack expands its contributor, budget, and provenance contract only after Experience or Skill becomes a real
  source.
- Retrieval quality is measured against the existing FTS/vector/RRF baseline for freshness, conflict, and diversity.
  Graph, sparse retrieval, and reranking are implemented only after they demonstrate an improvement.
