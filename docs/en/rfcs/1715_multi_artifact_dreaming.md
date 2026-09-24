- Proposal Name: `multi_artifact_dreaming`
- Start Date: 2026-09-22
- Related RFCs: [Artifact Dreaming](1510-artifact-dreaming.md), [Candidate and Review Inbox](0050_artifact_candidate_review_inbox.md), [Profile](1485_profile_artifact.md), [Topic Memory](1417_topic_memory.md), [Prompt management](1468_scope_owned_prompt_management.md), [Tags](1467_artifact_tags.md), and [Processing Supervisor](1515_artifact_processing_supervisor.md)

# Summary

Extend explicit Artifact Dreaming beyond `refine_experience` and `derive_skill` with bounded revisions of Memory entries, Profile, Topic Memory, Handoff, managed Skill, and Prompt, plus a separate proposal for Tag changes. Every new Dream operation produces a reviewable candidate or an explicit `no_change`/`needs_evidence` result. A Dream candidate cannot change the current Artifact or Tag set before an authorized reviewer approves its exact version against the current target. Existing automatic Source-window generation retains its current activation and review policy; adding Dream to a Family does not turn automatic generation into a review flow or let an automatic policy bypass Dream review.

Each run addresses one selected target using exact, same-Scope evidence. The existing Dream run, Candidate, Review, Family writer, and Processing Supervisor machinery remains the foundation. A0/A1 introduce no new business tables; Tag storage is justified and introduced with its stage. Existing `/v1` interfaces are extended in place, with coordinated Client upgrades for new closed enum and proposal types. No periodic discovery or unattended publication is required.

All operations extended by this PR—Memory, Profile, Topic Memory, Handoff, Skill, Prompt, and Tag—use the existing Experience refinement and Skill derivation model: evidence-driven proposals, deterministic validation, and human Candidate review. Trusted evaluation is an optional future enhancement, not implemented in this PR and not required for operation enablement or approval. Family-specific commit rules, including Tag ETag/content checks, remain mandatory.

# Motivation

Existing revisions become inaccurate as later observations accumulate: a Memory entry can describe an outdated fact, Profile may mistake a short trip for a permanent move, a Topic may retain a disproved hypothesis, Handoff may omit a completed task, and Skill or Prompt may repeat a known failure. An Experience proposal cannot directly repair every affected Family. Today each such correction requires the application to gather evidence, manage retries and concurrency, choose the correct writer, and retain an auditable decision.

The Families do not share one write model. Memory text lives in Entry Versions and an owning manifest; Profile generation may hold a Source-window cursor and a policy pending pointer; Topic Memory publishes the current revision and complete retrieval projection atomically; Handoff has separate commit, activation, and receiver checks; Prompt is live generation configuration; Tag is mutable catalog metadata rather than an Artifact. A shared run envelope must preserve those distinct commit boundaries. More candidates or longer summaries are not evidence of better task outcomes.

# Guide-level explanation

## User model and improvement evidence

A user or authorized integration selects the current exact target, relevant sources or approved revisions, and one operation. The worker resolves the evidence, asks the model for a complete proposed change, checks the typed output and citations, and returns either `proposed`, `no_change`, or `needs_evidence`. The model must identify what is wrong or missing in the target, the exact evidence supporting the change, the proposed replacement, and any counterevidence or uncertainty. There is no universal improvement score. A successful run is not approval or proof of later task improvement.

`proposed` creates a pending Candidate. Review rechecks permission, exact candidate version, evidence availability, policy, and target baseline, then invokes the Family's transactional writer. Target changes cause a conflict rather than an automatic rebase. Rejection never changes the formal target. Review approval of Skill does not install or execute it; approval of Handoff does not activate it or acknowledge it for the receiver.

For example, a user's Profile says “permanently based in Shanghai.” “In Shenzhen this week, returning on the 15th” is insufficient evidence of a permanent move. A later statement, “I have moved to Shenzhen and will be based here,” can support a bounded Memory revision. The Profile needs a separate run after the Memory result becomes formal; neither operation silently updates the other. The original Source and historical Entry Version remain inspectable.

| Operation | Exact target and output | Review effect |
| --- | --- | --- |
| `refine_experience` | New or replacement Experience Candidate | Existing Experience review |
| `derive_skill` | New instruction Skill Candidate from approved Experience | Existing Skill review and separate use/export |
| `revise_memory` | Current active entries in one Memory Artifact, up to 20 | Atomically create Entry Versions, manifest, and Memory Revision |
| `revise_profile` | Current complete Profile | Replace the head without consuming a Source window |
| `revise_topic_memory` | Current Topic identity and complete content | Publish a new revision with its complete retrieval projection |
| `refresh_handoff` | Current Handoff Revision | Commit a new revision; activation and receiver checks remain explicit |
| `revise_skill` | Existing instruction-only managed Skill | Commit a validated standard package after deterministic validation and human review |
| `revise_prompt` | Existing registered custom-capable Prompt key | Publish a new configuration revision after deterministic validation and human review |
| `revise_tags` | One TagTarget with an ETag and content basis | Replace the Tag set conditionally without an Artifact Revision |

New revision operations require an existing target. They do not create empty Profiles, Topics, Handoffs, or Prompt keys. They do not merge Memory entries, split Topics, run business tools, perform package scripts, or change cross-Scope permissions. An event can justify submitting a request, but the event itself is not evidence; the request carries inspectable citations. Periodic evidence discovery is a separate future policy.

## Outcomes and review

The run lifecycle remains `queued/running/succeeded/failed`; a successful run returns `proposed`, `no_change`, or `needs_evidence`. Missing or revoked evidence, target conflict, and exhausted budget are failures, not no-change. A `proposed` result points to an exact Candidate ID and version. The reviewer can inspect, revise, approve, or reject that version. A substantive candidate edit creates a new version requiring renewed deterministic validation and human review.

# Reference-level explanation

## Operation registry and admission

Add a server-owned `DreamOperationSpec` registry declaring an operation name and version, target kind, allowed evidence roles, proposal schema and validator, processing Family and canonical binding, review and commit adapters, and effect. Requests cannot name Python paths, tools, SQL, or arbitrary code. Expose an operation only when resolution, generation, review, commit, and worker execution are all implemented and configured. Family read support alone is insufficient.

The current worker dispatch assumes a Skill run or an Experience run. Replace that binary assumption with a binding-to-operation registry and claim one accepted run in deterministic request-generation, acceptance-time, and run-ID order. Reuse one Supervisor binding per Family. Do not create another scheduler or clear ordinary Source progress when a Dream run finishes. Keep explicit work from starving ordinary Source work: after at most four consecutive Dream attempts on one binding, perform one eligible Source pass before continuing, with the counter durable across worker restarts in a new `consecutive_dream_attempts` column on the existing intent table. A completed Source pass resets the counter and preserves queued Dream requests; no new table is required. Apply the additive column migration before processing schema validation on startup, preserving pending work in existing databases.

## Unified Candidate API and upgrade boundary

Candidate means a proposed change awaiting review. Replace the Artifact-named review routes with six JSON POST operations: `/v1/candidates/list`, `/v1/candidates/get`, `/v1/candidates/history`, `/v1/candidates/revise`, `/v1/candidates/approve`, and `/v1/candidates/reject`. Both Artifact and Tag changes use these endpoints. Responses discriminate typed proposals and results with `candidate_kind=artifact|tag`; the server derives the immutable kind from the stored candidate for subsequent operations. Retain scope_id, candidate_id, expected_version, evidence, and decision reasons. Revision submits a complete typed proposal and cannot change its kind or target. List supports kind, status, and owning Artifact family filters, one stable ordering, and one cursor; omitting kind includes both types subject to existing authorization. History exposes immutable proposal versions separately from the final decision.

Remove `/v1/artifact-candidates/*` directly, without aliases, redirects, or compatibility filtering. The unpublished `/v1/catalog-change-candidates/*` and `/v1/catalog-candidates/*` are not exposed and require no compatibility layer. This is an intentional breaking API change requiring coordinated Server and Client upgrades. Automatic database migration does not preserve old API clients.

Existing Experience, Skill, import, Profile, and Dream entrypoints create candidates; no generic create endpoint is added. Keep the current Dream submit/list/get routes and capabilities resource, extending their typed operations, targets, and Candidate references. Update the OpenAPI source contract, operation IDs, generated server and Client code, Python SDK, CLI, MCP, integrations, Dashboard, and contract tests together. Update both locales of the website HTTP API, interface overview, review workflow, examples, and generated API reference; do not hand-edit generated site directories. Release notes must identify removed routes and the matching minimum Client version. Trusted evaluation APIs, storage, and gates remain outside this PR.

For Artifact operations, `target` remains an exact ArtifactRef and must also appear in `artifacts`. Memory entry identities and versions are supplied through `memory_citations`, all belonging to that target Memory. References are deduplicated and counted against the evidence budget; a target cannot independently support its own claims. For Profile, Topic Memory, and Handoff, validate the target permission, existence, and exact head separately: retired historical dependencies must not prevent correction using new valid evidence. Recursively validate all selected supporting evidence without granting it the target exemption. Explicit Sources and references must belong to the same Scope. Tag uses tag_target on the same Dream submission route to carry TagTarget, expected ETag, and exact content basis; Artifact operations retain their exact ArtifactRef target.

The existing idempotency identity remains `(scope_id, principal_id, idempotency_key)`, stored using the current principal key digest. The same normalized request returns its original run; changed input under the same key conflicts. Different principals may use the same key without sharing a run, ownership, or worker identity. Capture Profile policy at admission and supply its snapshot to generation; reject changes during queueing, generation, or approval, while allowing stale candidates to be rejected. On first execution freeze the operation adapter, model, and server prompt version for retries. Existing HTTP 202/200 replay semantics remain.

## Evidence, model output, and trust boundary

Resolve the target and evidence under the requester's current permission, within the same Scope, at admission and again during execution and approval. Freeze exact revisions and content digests; never substitute search snippets for Memory Entry text. Attribute projection nodes as target content, supporting evidence, counterevidence, configuration under review, or lineage only. Derived copies of one root Source count as one root observation; unknown independence remains unknown. Lineage-only administrative text cannot become business-result evidence. Untrusted content, including Prompt instructions under review, never becomes the Dream system prompt or changes tools, trust rules, budgets, or policy.

The model returns a typed complete proposal, a concise reason, intent, and only evidence IDs supplied by the resolver. Deterministic validators reject forged IDs, invalid shape, target-external changes, and unsafe package material. `no_change` and `needs_evidence` carry no proposal or selected evidence IDs. All extended operations follow the existing Experience refinement and Skill derivation logic: evidence-driven model proposals, deterministic validation, pending Candidates, and human approval before conditional publication. A self-reported `passed=true` Source does not replace validation or review, and neither a model proposal nor approval proves quality improvement. Independent later task results, not candidate count or usage frequency, measure actual benefit.

## Family commit contracts

**Memory.** Revise at most 20 active logical entries in one owning Memory Artifact. The proposal contains its base Memory Ref, each expected Entry Version, replacement kind/text, evidence IDs, and a before/after explanation. Do not create, merge, forget, deactivate, restore, or rename entry IDs in this operation. Approval locks the Memory head, validates all expected versions and evidence, and atomically creates new Entry Versions, manifest, changes, formal Revision, Candidate decision, and retrieval projection. A stale head conflicts; no hidden rebase. `forget()` stays independently authorized.

**Profile.** Generate a complete Markdown snapshot with per-change reason and subject evidence. Introduce a Dream-specific candidate origin and generation mode without fabricating `source_window`. Existing Source-window candidates retain the Policy pending pointer and cursor semantics. Dream candidates neither read/clear that pointer nor consume the cursor or ordinary dirty flag. Both routes conditionally write against the same Profile head. `activation_mode=automatic` continues to govern only the existing automatic flow; Dream always requires review. Use the complete Profile lock order: Processing Intent, Policy, Candidate, Source Cursor only for Source-window decisions, then head validation and conditional write. Dream admission and Workers lock the canonical binding Intent first; approve/reject and Policy updates must also lock it before Policy, never request Intent while already holding Policy. Candidate-only revisions that do not access Intent start at Policy and preserve the remaining relative order. All HTTP and SDK decision paths follow that order. Concurrent Dream admission with Source-window approve/reject or Policy updates must finish without circular waits; only ordinary decisions consume the Source cursor.

**Topic Memory.** Revise one Topic identity with complete title, summary, and detail; preserve the distinction between confirmed results, hypotheses, ruled-out causes, open questions, and next steps. Prepare the complete FTS, configured vector, and detail-chunk projection for the exact candidate version and digest outside the publishing transaction. On approval, recheck candidate, head, evidence, permission, and retrieval configuration; atomically publish the new Revision, head, full active projection, publication time, and Candidate decision through the existing Topic publication contract. A failed embedding or changed candidate leaves the old complete head and projection in place. Do not introduce a “new head, index pending” state or bypass startup consistency checks. Dream never advances the Topic Source cursor.

**Handoff.** Generation validation, Candidate edits, and final commit must preserve the target objective; changing tasks requires a separate explicit operation. Require the exact current revision and evidence for every claimed change in completed, pending, blocked, or next work. Reuse existing prepare/commit validation through a transaction-safe writer, but approval only commits a non-activated revision. Existing activation tokens, live-state, capability, authorization, and receiver acknowledgement remain separate. A plan does not prove its action happened.

**Skill.** `revise_skill` targets a managed Skill with an exact package snapshot. A package with only root `SKILL.md` is an instruction-only Skill and is supported, including existing generated Skills. Legacy instruction content without a package follows the existing one-file package normalization. A package containing additional assets, scripts, or dependencies is rejected as an unsupported target in this stage. Keep authorized metadata and frontmatter, produce a new valid standard package, and validate its evidence and exact target before human review; never edit cached instructions while retaining the old package digest. Approval does not install, run, or automatically distribute the Skill.

**Prompt.** Target only an existing registered, custom-capable key. Propose full `mode/instructions/demonstrations` content after comparing verified errors and their original evidence with human-labelled or otherwise trusted expected outputs. The Dream system prompts and review/evaluation rules cannot be targets. Validate demonstrations against that key's schemas. No held-out dataset service, automated baseline/candidate scoring, or trusted evaluation gate is implemented in this PR. Approval requires deterministic schema/evidence/version checks, human review, and Prompt write authority; it publishes a new Prompt Revision for future inferences. In-flight inference keeps its frozen prior revision; rollback writes a higher revision.

**Tag.** Tag is catalog metadata, not an Artifact Family. A unified Candidate with candidate_kind=tag captures one current TagTarget, expected ETag, exact Artifact or Memory Entry content basis, before/after complete Tag sets, evidence, and reason. Review checks both ETag and current content basis. Approval atomically replaces the Tag set and records its decision, without creating an Artifact Revision or changing its content digest or embeddings. Its typed approval result contains the target, committed tags, and ETag; result_artifact remains null. Revision cannot alter the original baseline. Retry after successful approval returns the persisted result without applying tags again.

### Tag request and review examples

Create Tag candidates through the existing Dream submission endpoint, not a separate Candidate create API. Read the current tags and ETag and the exact content baseline first. Supply at least one supporting Source, Artifact, or Memory citation within the shared evidence budget. Artifact targets use basis_ref; Memory entry targets use basis_citation. The target and content basis must identify the same resource.

```json
{
  "operation": "revise_tags",
  "tag_target": {
    "target": {"type": "artifact", "family": "experience", "artifact_id": "exp-17"},
    "expected_etag": "<ETag from current tag response>",
    "basis_ref": {"family": "experience", "artifact_id": "exp-17", "revision": 4}
  },
  "sources": [{"source_type": "content", "source_id": "src-42"}],
  "idempotency_key": "dream-tags-exp-17-001"
}
```

For a Memory entry, use a target with type=memory_entry, family=memory, artifact_id, and entry_id, and a basis_citation containing its exact memory_ref, entry_id, and entry_version_id. Use the returned Candidate reference for review.

| POST endpoint | Required request fields | Optional fields or result |
| --- | --- | --- |
| `/v1/candidates/list` | scope_id | candidate_kind=tag, status, cursor, limit (1–100, default 50); returns candidates and next_cursor |
| `/v1/candidates/get` | scope_id, candidate_id | Current proposal, version, evidence, and decision |
| `/v1/candidates/history` | scope_id, candidate_id | Immutable proposal versions |
| `/v1/candidates/revise` | scope_id, candidate_id, expected_version, proposal, reason | sources, artifacts, memory_citations; creates a pending version |
| `/v1/candidates/approve` | scope_id, candidate_id, expected_version | Conditional Tag replacement and stored result |
| `/v1/candidates/reject` | scope_id, candidate_id, expected_version, reason | Terminal rejection without changing tags |

Reads require scope.read; review mutations require scope.review and applicable target permissions. The complete proposal retains the original target, expected_etag, basis, and before_tags. Revision must not change those fields to bypass a conflict. after_tags contains the entire replacement set: 0–32 labels, each 1–64 characters. An empty set clears the tags. Apply NFC/casefold normalization and reject duplicates after normalization.

Example request to `/v1/candidates/revise`:

```json
{
  "scope_id": "scope-1",
  "candidate_id": "cat-candidate-9",
  "expected_version": 1,
  "proposal": {
    "target": {"type": "artifact", "family": "experience", "artifact_id": "exp-17"},
    "expected_etag": "<original candidate ETag>",
    "basis_ref": {"family": "experience", "artifact_id": "exp-17", "revision": 4},
    "before_tags": ["logistics", "gatehouse-delivery"],
    "after_tags": ["logistics", "wrong-address"]
  },
  "reason": "Verified delivery evidence identifies the wrong address",
  "sources": [{"source_type": "content", "source_id": "src-43"}]
}
```

A stale Candidate version returns 409, a changed Tag ETag returns 412, and a changed content baseline returns 409. Read and review a fresh proposal; never automatically rebase. The response includes candidate_kind=tag, candidate_id, version, status, operation=revise_tags, origin, proposal, evidence, reason, and decision. Approval returns the actual target, committed tags, and new tag_digest/ETag without a result_artifact. Successful approval replay checks current authority and request semantics and returns the persisted result without repeating the mutation.

## Review transaction, storage, and implementation stages

A common review path validates current authority, reads the candidate to choose the server-registered Family/origin adapter, performs any external preparation, then lets that adapter acquire all locks in the Family's established order. The common layer must not lock Candidate before the Profile adapter locks Policy. Inside the transaction, revalidate exact candidate version and digest, target baseline, evidence availability, and applicable deterministic policy. The Family writer and Candidate decision commit together. Stale or invalid candidates remain pending with an actionable conflict; rejected Dream candidates never alter formal content or a Source cursor.

Rename the existing `pc_artifact_candidate_heads` and `pc_artifact_candidate_versions` to `pc_candidate_heads` and `pc_candidate_versions`. These are the only two permanent Candidate tables. Do not create Catalog Change tables or a migration journal table. Heads add immutable non-null candidate_kind (artifact/tag, backfilled to artifact for existing rows) and nullable result_payload. Versions inherit kind through candidate identity and reuse proposal, evidence, target, and reason storage. Family remains the owning Artifact family, never tag. Tag proposals store the target, expected ETag, exact content basis, and complete before/after tags.

Approved artifact candidates require a real result_family/result_artifact_id/result_revision foreign key and no Tag result. Approved tag candidates require a valid typed result_payload and null Artifact result columns. Pending and rejected candidates have no success result; rejected candidates retain their reason. Never synthesize revision 0, -1, or 1 for Tag results. Preserve candidate and version primary keys and add only necessary query indexes. Reuse pc_dream_runs, existing Family storage, and the current (scope_id, principal_key, idempotency_key) constraint. Evaluation storage and generic index-outbox tables remain excluded.

### Automatic migration during upgrade

A dedicated startup migration is required; installing the package alone does not change data, and create_all does not rename or upgrade existing tables. Run migration before Candidate create_all, domain reads, API readiness, or Worker startup.

1. Stop old Servers and Workers and back up the database before starting the new release. Mixed old/new binaries and rolling upgrades across this schema change are unsupported. Serialize migration at deployment level; other new instances wait for verified completion.
2. Detect tables, fields, constraints, and foreign keys. Fresh databases receive the unified schema. Existing databases rename the two tables, add fields, backfill kind=artifact, and update result constraints. Fully migrated databases verify and skip completed work.
3. Preserve IDs, current and historical versions, proposals, evidence, statuses, reasons, and real approval results. Update the Profile pending_candidate_id foreign key and preserve Dream references and access ownership. Normalize old payloads through explicit readers or lossless conversion; users must not regenerate or reapprove historical candidates.
4. SQLite may rebuild constraints using temporary tables inside a transaction, verifying copied contents and foreign keys before replacement; only two Candidate tables remain afterward. OceanBase/MySQL DDL is not assumed to be transactionally reversible: inspect actual schema at each idempotent step, resume interrupted work, and block business startup in every incomplete state.
5. Verify rows, contents, candidate/version relations, approval results, Profile links, and historical references before readiness. Fail with an actionable error while preserving recoverable data; never silently initialize empty replacement tables. Ambiguous simultaneous old/new tables require intervention, not guessed overwrites.
6. The unpublished Catalog Change schema has no production migration or compatibility requirement. If detected, report it as an unsupported experimental schema without deleting it. Downgrading requires restoring the pre-upgrade backup; old binaries need not understand the new schema.

Validate SQLite and OceanBase fresh and populated upgrades, all three review states, multiple proposal versions, Profile pointers, Dream references, ownership, repeat startup, interruption/resume, concurrent initialization, and ambiguous dual tables. Check that old pending candidates remain reviewable, old approved results remain readable, and Tag approval creates no content revision. Finish migration and coordinated reader/serializer/Client/Dashboard support before advertising new operations. Existing non-Dream automatic generation policies remain unchanged.

| Stage | Deliverable | Enablement gate |
| --- | --- | --- |
| A0 | Operation registry, typed targets/evidence roles, `/v1` schema extension, shared review adapters | Current operations regressions pass; Client compatibility and lock ordering are tested; incomplete operations stay disabled |
| A1 | Explicit Memory, Profile, and Topic Memory revision | Atomic Memory write, Profile cursor isolation, and Topic complete-projection publication pass on SQLite and OceanBase |
| B1 | Handoff refresh and one-file Skill revision | Non-activation boundary, package/evidence validation, and human review pass |
| B2 | Prompt revision | Same-key schemas, evidence validation, human review, Prompt write authority, and rollback pass |
| C | Single-target Tag governance using the unified Candidate tables, APIs, and Inbox | Both ETag and content baseline, distinct result type, and decision history pass |

Per-run defaults remain at most 20 explicitly selected target/artifact/entry references, at most 32 projected evidence items including Sources, 64 KiB model-visible evidence, two model calls, 4,096 output tokens per call, and 120 seconds from first execution. A Scope has at most 32 pending/running runs. Root traversal retains its 128-node, 256-edge, 8-level budget. Complete snapshots that exceed budget fail; do not truncate them and claim a complete revision.

The idempotency key handles request replay. Candidate suppression may additionally fingerprint operation, complete target baseline, root evidence digest, and policy within one Scope and one principal. Before reusing a pending Candidate, verify its current version, actual content and evidence, and permission; editing the proposal invalidates an outdated fingerprint. Do not reuse across principals in the first release. A rejected proposal with unchanged evidence can yield `no_change` and a readable reference to the earlier decision, but cannot conceal new counterevidence or a changed target.

Unregistered operation or invalid target yields a stable validation error; unavailable capability rejects before creating a run; stale target and Tag ETag have distinct conflicts; permission or evidence revocation blocks generation or approval; invalid model output and exhausted budget fail the run. An unconfigured evaluation service or absent quality score does not prevent implementation, enablement, or approval of any operation in this PR. Record bounded operation/family timings, outcome, usage, candidate reuse, review conflicts, and Topic projection preparation/publication time without recording raw evidence or identifiers in low-cardinality metrics.

## Validation

Acceptance scenarios cover temporary travel versus an explicit move; a single Memory entry revision without changing other entries; revoked or superseded evidence; simultaneous Dream and Source-window Profile candidates; Topic embedding failure, worker restart, stale projection and competing fusion; Handoff plans without execution evidence; supported one-file versus unsupported multi-file Skill packages; stale candidate versions after edits; invalid Prompt demonstration schemas and rollback; successful review without an evaluation service; Tag content-basis changes while ETag stays fixed; same-root evidence copies; per-principal idempotency; a shared lock order across HTTP and SDK review paths; worker lease takeover; and preservation of ordinary Source progress while Dream runs execute. Use isolated end-to-end tests for the evidence-to-review-to-publication flow. Engineering tests verify observable behavior and transaction correctness; they do not introduce a runtime evaluation dependency or prove quality gains.

# Drawbacks

Each Family still needs its own validator and transaction-safe writer. Full Memory-head CAS may conflict in active Scopes. Complete Profile/Topic snapshots can exceed model budgets. Human review adds latency and operational cost; approval alone does not prove quality improvement. Closed Client types require coordinated upgrades. Unified Candidate storage requires kind-specific constraints and commit adapters; direct API replacement and table renaming require a coordinated upgrade.

# Rationale and alternatives

A generic `replace` writer would be smaller superficially but would bypass Memory Entry Versions, Profile cursor policy, Topic projection invariants, Handoff activation, and Tag ETags. Converting every result into an Experience would leave the underlying inaccurate content in place. One Dream service per Family duplicates idempotency, budgeting, evidence, and recovery. One common run with Family-owned validation and commits preserves the proven runtime while respecting each write boundary. A universal nightly scan and automatic overwrite is postponed because discovery quality, cost, and rollback evidence are not established. In-place `/v1` extension avoids a parallel route tree; it requires a deliberate minimum Client version and a coordinated breaking upgrade.

# Prior art

This proposal builds on [Artifact Dreaming](1510-artifact-dreaming.md) for exact evidence and bounded runs, [Candidate and Review Inbox](0050_artifact_candidate_review_inbox.md) for immutable proposal versions, [Memory](0014_memory_layer_design.md) for Entry Version writes, [Profile](1485_profile_artifact.md) for Source-window policy, [Topic Memory](1417_topic_memory.md) for complete current projections, [Handoff](0048_handoff_artifact.md) for explicit commit and receiver checks, and [Prompt](1468_scope_owned_prompt_management.md) and [Tag](1467_artifact_tags.md) for their distinct configuration and catalog lifecycles.

# Unresolved questions

1. Choose the coordinated Server and Client release versions; removed routes have no compatibility layer.
2. Define the Reviewer presentation of evidence, diffs, and configuration-publication effects for Skill and each supported Prompt key.
3. Extract the transaction-safe, non-activating Handoff writer without bypassing existing tokens or receiver checks.
4. Define Tag diff presentation in the unified Inbox; both kinds already share storage, ordering, and pagination.
5. Decide which application observes post-publication quality and requests explicit rollback; no automatic causal attribution is claimed.

Stage A0/A1 can be developed independently; an operation in a later stage remains unavailable until its own contract, storage, deterministic validation, review, and concurrency gate is complete.

# Future possibilities

Trusted evaluation may be added as an optional enhancement in a separate proposal covering evaluator identity, held-out datasets, versioned thresholds, exact-candidate result binding, and isolated execution. This PR implements none of that infrastructure and does not depend on it.

A later discovery policy could select new root evidence, counterexamples, and corrections with quiet periods and candidate deduplication. Future governance may merge Memory entries, split or merge Topics, support signed multi-file Skill package diffs, or enable separately authorized cross-Scope sharing. Automatic approval and rollback would need another RFC establishing reversibility, evidence thresholds, auditability, and independent task-outcome evaluation.
