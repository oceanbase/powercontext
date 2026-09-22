- Proposal Name: `multi_artifact_dreaming`
- Start Date: 2026-09-22
- Related RFCs: [Artifact Dreaming](1510-artifact-dreaming.md), [Candidate and Review Inbox](0050_artifact_candidate_review_inbox.md), [Profile](1485_profile_artifact.md), [Topic Memory](1417_topic_memory.md), [Prompt management](1468_scope_owned_prompt_management.md), [Tags](1467_artifact_tags.md), and [Processing Supervisor](1515_artifact_processing_supervisor.md)

# Summary

Extend explicit Artifact Dreaming beyond `refine_experience` and `derive_skill` with bounded revisions of Memory entries, Profile, Topic Memory, Handoff, managed Skill, and Prompt, plus a separate proposal for Tag changes. Every new Dream operation produces a reviewable candidate or an explicit `no_change`/`needs_evidence` result. A Dream candidate cannot change the current Artifact or Tag set before an authorized reviewer approves its exact version against the current target. Existing automatic Source-window generation retains its current activation and review policy; adding Dream to a Family does not turn automatic generation into a review flow or let an automatic policy bypass Dream review.

Each run addresses one selected target using exact, same-Scope evidence. The existing Dream run, Candidate, Review, Family writer, and Processing Supervisor machinery remains the foundation. A0/A1 introduce no new business tables; later evaluation and Tag storage is justified and introduced only when those stages ship. Existing `/v1` interfaces are extended in place, with coordinated Client upgrades for new closed enum and proposal types. No periodic discovery or unattended publication is required.

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
| `revise_skill` | Existing instruction-only managed Skill | Commit a validated standard package after trusted evaluation |
| `revise_prompt` | Existing registered custom-capable Prompt key | Publish a new configuration revision after trusted evaluation |
| `revise_tags` | One TagTarget with an ETag and content basis | Replace the Tag set conditionally without an Artifact Revision |

New revision operations require an existing target. They do not create empty Profiles, Topics, Handoffs, or Prompt keys. They do not merge Memory entries, split Topics, run business tools, perform package scripts, or change cross-Scope permissions. An event can justify submitting a request, but the event itself is not evidence; the request carries inspectable citations. Periodic evidence discovery is a separate future policy.

## Outcomes and review

The run lifecycle remains `queued/running/succeeded/failed`; a successful run returns `proposed`, `no_change`, or `needs_evidence`. Missing or revoked evidence, target conflict, and exhausted budget are failures, not no-change. A `proposed` result points to an exact Candidate ID and version. The reviewer can inspect, revise, approve, or reject that version. A substantive candidate edit invalidates its old evaluation records.

# Reference-level explanation

## Operation registry and admission

Add a server-owned `DreamOperationSpec` registry declaring an operation name and version, target kind, allowed evidence roles, proposal schema and validator, processing Family and canonical binding, review and commit adapters, effect, and evaluation policy. Requests cannot name Python paths, tools, SQL, or arbitrary code. Expose an operation only when resolution, generation, review, commit, and worker execution are all implemented and configured. Family read support alone is insufficient.

The current worker dispatch assumes a Skill run or an Experience run. Replace that binary assumption with a binding-to-operation registry and claim one accepted run in deterministic request-generation, acceptance-time, and run-ID order. Reuse one Supervisor binding per Family. Do not create another scheduler or clear ordinary Source progress when a Dream run finishes. Keep explicit work from starving ordinary Source work: after at most four consecutive Dream attempts on one binding, perform one eligible Source pass before continuing, with the counter durable across worker restarts in a new `consecutive_dream_attempts` column on the existing intent table. A completed Source pass resets the counter and preserves queued Dream requests; no new table is required. Apply the additive column migration before processing schema validation on startup, preserving pending work in existing databases.

## API and Client compatibility

Extend the existing `POST/GET /v1/scopes/{scope_id}/dream` and `GET /v1/scopes/{scope_id}/dream/{run_id}` operations with exact Artifact targets, new operations, and corresponding run results. Extend `POST /v1/artifact-candidates/list`, `get`, `revise`, `approve`, and `reject` for new Artifact proposal types while retaining `candidate_id` and `expected_version`. Extend `GET /v1/capabilities` with available Dream operations, output kinds, and effects. No parallel Dream or Artifact Review path is needed.

Generated Clients currently use closed Dream operation enums and Candidate proposal unions. Deploy Server, generated Python Client, CLI, integrations, and Dashboard coherently before advertising new operations. An old Client must receive an explicit upgrade signal or only a compatibility-filtered list that it can decode; it must not silently reinterpret a new proposal as an old type. Define and test the minimum supported Client release. Tag proposals have a different result type and ETag transaction; add dedicated Tag candidate interfaces only in stage C, once that different resource lifecycle is implemented. Trusted evaluation registration belongs to stage B1, not A0/A1.

For Artifact operations, `target` remains an exact ArtifactRef and must also appear in `artifacts`. Memory entry identities and versions are supplied through `memory_citations`, all belonging to that target Memory. References are deduplicated and counted against the evidence budget; a target cannot independently support its own claims. For Profile, Topic Memory, and Handoff, validate the target permission, existence, and exact head separately: retired historical dependencies must not prevent correction using new valid evidence. Recursively validate all selected supporting evidence without granting it the target exemption. Explicit Sources and references must belong to the same Scope. Stage C expresses TagTarget, expected ETag, and the exact content basis through its separate Catalog Change request, without changing the Artifact Dream target type.

The existing idempotency identity remains `(scope_id, principal_id, idempotency_key)`, stored using the current principal key digest. The same normalized request returns its original run; changed input under the same key conflicts. Different principals may use the same key without sharing a run, ownership, or worker identity. Capture Profile policy at admission and supply its snapshot to generation; reject changes during queueing, generation, or approval, while allowing stale candidates to be rejected. On first execution freeze the operation adapter, model, and server prompt version for retries. Existing HTTP 202/200 replay semantics remain.

## Evidence, model output, and trust boundary

Resolve the target and evidence under the requester's current permission, within the same Scope, at admission and again during execution and approval. Freeze exact revisions and content digests; never substitute search snippets for Memory Entry text. Attribute projection nodes as target content, supporting evidence, counterevidence, configuration under review, or lineage only. Derived copies of one root Source count as one root observation; unknown independence remains unknown. Lineage-only administrative text cannot become business-result evidence. Untrusted content, including Prompt instructions under review, never becomes the Dream system prompt or changes tools, trust rules, budgets, or policy.

The model returns a typed complete proposal, a concise reason, intent, and only evidence IDs supplied by the resolver. Deterministic validators reject forged IDs, invalid shape, target-external changes, and unsafe package material. `no_change` and `needs_evidence` carry no proposal or selected evidence IDs. Prompt and Skill improvements require trusted evaluation bound to the exact candidate version and content digest; a self-reported `passed=true` Source does not satisfy that gate. Independent later task results, not candidate count or usage frequency, measure actual benefit.

## Family commit contracts

**Memory.** Revise at most 20 active logical entries in one owning Memory Artifact. The proposal contains its base Memory Ref, each expected Entry Version, replacement kind/text, evidence IDs, and a before/after explanation. Do not create, merge, forget, deactivate, restore, or rename entry IDs in this operation. Approval locks the Memory head, validates all expected versions and evidence, and atomically creates new Entry Versions, manifest, changes, formal Revision, Candidate decision, and retrieval projection. A stale head conflicts; no hidden rebase. `forget()` stays independently authorized.

**Profile.** Generate a complete Markdown snapshot with per-change reason and subject evidence. Introduce a Dream-specific candidate origin and generation mode without fabricating `source_window`. Existing Source-window candidates retain the Policy pending pointer and cursor semantics. Dream candidates neither read/clear that pointer nor consume the cursor or ordinary dirty flag. Both routes conditionally write against the same Profile head. `activation_mode=automatic` continues to govern only the existing automatic flow; Dream always requires review. Use the established Profile lock order: Policy, Candidate, Source Cursor only for Source-window decisions, then head validation and conditional write. All HTTP and SDK decision paths follow that order.

**Topic Memory.** Revise one Topic identity with complete title, summary, and detail; preserve the distinction between confirmed results, hypotheses, ruled-out causes, open questions, and next steps. Prepare the complete FTS, configured vector, and detail-chunk projection for the exact candidate version and digest outside the publishing transaction. On approval, recheck candidate, head, evidence, permission, and retrieval configuration; atomically publish the new Revision, head, full active projection, publication time, and Candidate decision through the existing Topic publication contract. A failed embedding or changed candidate leaves the old complete head and projection in place. Do not introduce a “new head, index pending” state or bypass startup consistency checks. Dream never advances the Topic Source cursor.

**Handoff.** Generation validation, Candidate edits, and final commit must preserve the target objective; changing tasks requires a separate explicit operation. Require the exact current revision and evidence for every claimed change in completed, pending, blocked, or next work. Reuse existing prepare/commit validation through a transaction-safe writer, but approval only commits a non-activated revision. Existing activation tokens, live-state, capability, authorization, and receiver acknowledgement remain separate. A plan does not prove its action happened.

**Skill.** `revise_skill` targets a managed Skill with an exact package snapshot. A package with only root `SKILL.md` is an instruction-only Skill and is supported, including existing generated Skills. Legacy instruction content without a package follows the existing one-file package normalization. A package containing additional assets, scripts, or dependencies is rejected as an unsupported target in this stage. Keep authorized metadata and frontmatter, produce a new valid standard package, and bind evaluation to the exact target and candidate; never edit cached instructions while retaining the old package digest. Approval does not install, run, or automatically distribute the Skill.

**Prompt.** Target only an existing registered, custom-capable key. Propose full `mode/instructions/demonstrations` content after comparing verified errors and their original evidence with human-labelled or otherwise trusted expected outputs. The Dream system prompts and review/evaluation rules cannot be targets. Validate demonstrations against that key's schemas. Keep training roots separate from held-out evaluation roots. Approval requires review and Prompt write authority plus a trusted, current evaluation record; it publishes a new Prompt Revision for future inferences. In-flight inference keeps its frozen prior revision; rollback writes a higher revision.

**Tag.** Tag is catalog metadata, not an Artifact Family. A Catalog Change Candidate captures one current TagTarget, expected ETag, exact Artifact or Memory Entry content basis, before/after complete Tag sets, evidence, and reason. Review checks both ETag and current content basis. Approval atomically replaces the Tag set and records its decision, without creating an Artifact Revision or changing its content digest or embeddings. It must not be squeezed into the Artifact Candidate result shape, where approved implies `result_artifact`.

## Review transaction, storage, and implementation stages

A common review path validates current authority, reads the candidate to choose the server-registered Family/origin adapter, performs any external preparation, then lets that adapter acquire all locks in the Family's established order. The common layer must not lock Candidate before the Profile adapter locks Policy. Inside the transaction, revalidate exact candidate version and digest, target baseline, evidence availability, policy, and required evaluation. The Family writer and Candidate decision commit together. Stale or invalid candidates remain pending with an actionable conflict; rejected Dream candidates never alter formal content or a Source cursor.

A0/A1 reuse `pc_dream_runs`, `pc_artifact_candidate_heads`, `pc_artifact_candidate_versions`, and Family storage. Prefer existing typed payloads for operation version, target, origin, proposal, and result; add columns or indexes only for proven query, uniqueness, or transactional needs. No new A0/A1 business table, generic index-outbox table, or second Dream scheduler is needed. Preserve the current `(scope_id, principal_key, idempotency_key)` constraint. Stage B1 may add a dedicated trusted evaluation table if existing trusted storage cannot bind evaluator identity, candidate version/digest, test set and policy versions, result, and usage. Stage C may add Tag candidate and candidate-version storage justified by its distinct ETag and result lifecycle. Neither is a prerequisite for A0/A1 startup or migration. Apply any schema change to SQLite and OceanBase.

| Stage | Deliverable | Enablement gate |
| --- | --- | --- |
| A0 | Operation registry, typed targets/evidence roles, `/v1` schema extension, shared review adapters | Current operations regressions pass; Client compatibility and lock ordering are tested; incomplete operations stay disabled |
| A1 | Explicit Memory, Profile, and Topic Memory revision | Atomic Memory write, Profile cursor isolation, and Topic complete-projection publication pass on SQLite and OceanBase |
| B1 | Handoff refresh, one-file Skill revision, trusted evaluation | Non-activation boundary and version-bound evaluation gate pass |
| B2 | Prompt revision | Same-key schemas, held-out tests, Prompt write authority, and rollback pass |
| C | Single-target Tag governance | Both ETag and content baseline, distinct result type, and decision history pass |

Per-run defaults remain at most 20 explicitly selected target/artifact/entry references, at most 32 projected evidence items including Sources, 64 KiB model-visible evidence, two model calls, 4,096 output tokens per call, and 120 seconds from first execution. A Scope has at most 32 pending/running runs. Root traversal retains its 128-node, 256-edge, 8-level budget. Complete snapshots that exceed budget fail; do not truncate them and claim a complete revision.

The idempotency key handles request replay. Candidate suppression may additionally fingerprint operation, complete target baseline, root evidence digest, and policy within one Scope and one principal. Before reusing a pending Candidate, verify its current version, actual content and evidence, and permission; editing the proposal invalidates an outdated fingerprint. Do not reuse across principals in the first release. A rejected proposal with unchanged evidence can yield `no_change` and a readable reference to the earlier decision, but cannot conceal new counterevidence or a changed target.

Unregistered operation or invalid target yields a stable validation error; unavailable capability rejects before creating a run; stale target and Tag ETag have distinct conflicts; permission or evidence revocation blocks generation or approval; invalid model output and exhausted budget fail the run. Approval without current trusted evaluation returns an actionable conflict. Record bounded operation/family timings, outcome, usage, candidate reuse, review conflicts, and Topic projection preparation/publication time without recording raw evidence or identifiers in low-cardinality metrics.

## Validation

Acceptance scenarios cover temporary travel versus an explicit move; a single Memory entry revision without changing other entries; revoked or superseded evidence; simultaneous Dream and Source-window Profile candidates; Topic embedding failure, worker restart, stale projection and competing fusion; Handoff plans without execution evidence; supported one-file versus unsupported multi-file Skill packages; stale evaluation after candidate edit; Prompt held-out-set leakage and rollback; Tag content-basis changes while ETag stays fixed; same-root evidence copies; per-principal idempotency; a shared lock order across HTTP and SDK review paths; worker lease takeover; and preservation of ordinary Source progress while Dream runs execute. Use an isolated real model chain for quality evaluation and compare no Dream, normal regeneration, and this proposal without presupposing an improvement percentage.

# Drawbacks

Each Family still needs its own validator and transaction-safe writer. Full Memory-head CAS may conflict in active Scopes. Complete Profile/Topic snapshots can exceed model budgets. Human review and trusted evaluation add latency and operational cost. Closed Client types require coordinated upgrades. Tag's distinct lifecycle may ultimately justify its own candidate storage and interfaces.

# Rationale and alternatives

A generic `replace` writer would be smaller superficially but would bypass Memory Entry Versions, Profile cursor policy, Topic projection invariants, Handoff activation, and Tag ETags. Converting every result into an Experience would leave the underlying inaccurate content in place. One Dream service per Family duplicates idempotency, budgeting, evidence, and recovery. One common run with Family-owned validation and commits preserves the proven runtime while respecting each write boundary. A universal nightly scan and automatic overwrite is postponed because discovery quality, cost, and rollback evidence are not established. In-place `/v1` extension avoids a parallel route tree; it requires a deliberate minimum Client version and compatibility strategy.

# Prior art

This proposal builds on [Artifact Dreaming](1510-artifact-dreaming.md) for exact evidence and bounded runs, [Candidate and Review Inbox](0050_artifact_candidate_review_inbox.md) for immutable proposal versions, [Memory](0014_memory_layer_design.md) for Entry Version writes, [Profile](1485_profile_artifact.md) for Source-window policy, [Topic Memory](1417_topic_memory.md) for complete current projections, [Handoff](0048_handoff_artifact.md) for explicit commit and receiver checks, and [Prompt](1468_scope_owned_prompt_management.md) and [Tag](1467_artifact_tags.md) for their distinct configuration and catalog lifecycles.

# Unresolved questions

1. Define the minimum coordinated Client release and explicit upgrade or compatibility-filtering behavior for old closed enums and proposal unions.
2. Define the first trusted held-out evaluation sets, versioned quality thresholds, and owners for Skill and each supported Prompt key.
3. Extract the transaction-safe, non-activating Handoff writer without bypassing existing tokens or receiver checks.
4. Choose the Stage C Tag Inbox pagination strategy while preserving its separate lifecycle.
5. Decide which application observes post-publication quality and requests explicit rollback; no automatic causal attribution is claimed.

Stage A0/A1 can be developed independently; an operation in a later stage remains unavailable until its own contract, storage, evaluation, and concurrency gate is complete.

# Future possibilities

A later discovery policy could select new root evidence, counterexamples, and corrections with quiet periods and candidate deduplication. Future governance may merge Memory entries, split or merge Topics, support signed multi-file Skill package diffs, or enable separately authorized cross-Scope sharing. Automatic approval and rollback would need another RFC establishing reversibility, evidence thresholds, auditability, and independent task-outcome evaluation.
