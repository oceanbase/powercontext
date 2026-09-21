- Proposal Name: `memory_quality_and_lifecycle`
- Start Date: 2026-09-18
- Tracking Issue: [oceanbase/powercontext#1590](https://github.com/oceanbase/powercontext/issues/1590)
- Related RFCs: [RFC 0014](/en/rfcs/0014_memory_layer_design), [RFC 0019](/en/rfcs/0019_local_source_memory_runtime),
  [RFC 0028](/en/rfcs/0028_context_pack), [RFC 0050](/en/rfcs/0050_artifact_candidate_review_inbox),
  [RFC 0080](/en/rfcs/0080_memory_search_reranking), [RFC 1229](/en/rfcs/1229_unified_workloads_and_long_horizon_memory_evaluation),
  and [RFC 1560](/en/rfcs/1560_recall_sufficiency_gate)
- Related work: [#1425](https://github.com/oceanbase/powercontext/issues/1425),
  [#1321](https://github.com/oceanbase/powercontext/issues/1321),
  [#1556](https://github.com/oceanbase/powercontext/issues/1556),
  [#1586](https://github.com/oceanbase/powercontext/pull/1586), and
  [#1596](https://github.com/oceanbase/powercontext/pull/1596)

# Summary

This RFC defines an opt-in, evidence-preserving Memory quality and lifecycle policy. It adds four independent derived
dimensions—provenance/verification, importance, novelty, and current-state status—to help PowerContext keep a healthy
active Memory retrieval surface without changing authoritative entry bodies, Artifact Revisions, citations, or public
RRF scores.

The initial quality policy is deliberately narrow. It filters records whose lifecycle invalidity is already known,
then applies at most a stable `±2`-position reorder *inside* the fixed RRF coarse pool. It neither changes channel
admission nor brings a pool-external entry to the reranker. Near-duplicate detection produces relationships and review
proposals rather than destructive merging. Low-value entries may later be reversibly deactivated by a disabled-by-
default, dry-run-first L1 policy. Higher-risk consolidation, supersession, and promotion belong to an L2 review-gated
extension.

The design distinguishes recoverable deactivation from rollback of automatic side effects. It also separates this
logical Memory lifecycle from physical erasure, compliance retention, and cross-family cleanup, which remain in the
scope of #1425 and follow-up RFCs.

# Motivation

Memory already provides immutable entry versions, manifest state, exact citations, FTS/vector admission, RRF fusion,
and optional listwise reranking. Its current active retrieval surface nevertheless has three gaps:

1. A durable, evidence-backed constraint and a one-off weak working note have no explicit quality distinction.
2. Byte-identical deduplication cannot control active paraphrases of the same instruction or evolving fact.
3. A known inactive or superseded record has no explicit temporal-validity annotation that the retrieval path can use
   independently of relevance ranking or reranker availability.

These are different problems. Quality is not a write-admission policy; semantic similarity is not proof that two
records can be merged; and lifecycle validity is not a soft ranking preference. Conflating them makes a low-quality
entry disappear from history, lets a reranker see revoked evidence, or makes arbitrary recency override a durable
constraint.

The target policy is therefore permissive writing with bounded, inspectable retrieval and maintenance:

```text
write grounded Memory directly
  -> retain immutable entry authority and evidence
  -> derive bounded lifecycle/quality projections
  -> enforce known validity before retrieval ranking
  -> reorder only a fixed RRF pool
  -> retire only explicitly eligible low-value entries, reversibly
  -> review semantic consolidation, supersession, and promotion
```

# Guide-level explanation

## Normal Memory writes remain normal

This RFC does not make an ordinary `remember` call wait for Review. A directly grounded statement such as
“Run `make docs-test` after changing documentation” remains a normal Memory write, with its exact evidence and
immutable entry version.

With every lifecycle option disabled, current write, search, rerank, Context Pack, and citation behavior is unchanged.
The new policy is not a permission to rewrite entry text, silently delete history, or alter the public Memory search
score.

## Current-state recall is different from historical recall

Suppose a project first records:

```text
Documentation changes must run make test.
```

Later direct evidence establishes:

```text
Documentation changes must run make docs-test.
```

After a reviewed, explicit supersession relation is recorded, ordinary current-state recall returns the latter record.
The older record is not merely ranked lower: it is withheld before RRF and before optional reranking. An exact
historical read can still resolve the preserved old entry and its supersession reason.

If the two records merely look contradictory but PowerContext lacks reliable evidence of their relation or comparable
time, it must retain both as `unresolved_conflict`. The Context Pack labels them as unresolved evidence rather than
asking an agent to infer chronology from two plain texts or from retrieval rank.

## Quality helps only within evidence already retrieved

For one search, PowerContext first performs normal channel retrieval, admission, lifecycle-validity filtering, RRF,
and coarse-pool truncation. Only then may quality make a small stable adjustment within that exact pool:

```text
baseline RRF members:  A  B  C  D  E  F
quality-adjusted order: A  C  B  D  E  F
```

No new member appears. A record that did not survive RRF cannot be promoted into the reranker’s input by quality.
This keeps quality a conservative selection aid rather than a hidden second recall mechanism.

When the RFC 1560 recall gate is enabled, each issued search round retains the current ordering boundary: quality
reordering occurs within that round's fixed coarse pool, the existing optional listwise reranker runs, and the gate
receives the resulting `result.hits`. This RFC does not defer reranking or substitute a baseline-only gate input.
With quality disabled or neutral, the gate receives the same reranked results, makes the same decisions, and retains the
same cost accounting as current behavior.

The public hit `score` remains the baseline RRF score. A proposed in-process `MemoryRankingTrace` explains the
bounded reorder for diagnostics and evaluation; HTTP search output does not gain that trace in the first phase.

## Forgetting means retiring from the active surface, not erasing history

An optional L1 maintenance job can identify a low-importance, expired, unprotected entry for deactivation. It first
runs in dry-run mode. Once enabled, it emits a normal Memory deactivate Revision with a stable reason such as
`auto_decay:v1`.

The entry body, old revisions, evidence, and exact citations remain available. `reactivate()` restores the same entry
version to the active projection and starts a new lifecycle interval. This is **recoverable deactivation**, not a
promise that every downstream effect that once used the entry can be undone.

## Semantic maintenance is reviewed work

Two paraphrased entries may deserve alignment, and recurring working notes may eventually deserve promotion. These are
not automatic mutations. An L2 task creates a proposal with exact affected entry versions, evidence, proposed
relations, and all intended effects. Only approval may create a Memory Revision or change a lifecycle relation.

Ordinary Memory remains direct-write. L2 is a limited Memory-specific extension of RFC 0050’s review mechanism, not a
change to the review policy for all Memory writes.

# Reference-level explanation

## Design invariants

1. **Immutable authority.** Entry body text, entry content hashes, Artifact Revisions, evidence citations, and exact
   Handoff citation identity remain authoritative and immutable.
2. **No-regression defaults.** Every lifecycle option is disabled or neutral by default. Old data receives neutral
   derived values; when the required derivation is unavailable, the feature remains disabled rather than guessing.
3. **Validity precedes quality.** Known `inactive` and explicitly `superseded` records are filtered before RRF and
   before optional reranking for ordinary current-state recall. This does not depend on a quality policy, freshness
   configuration, or model availability.
4. **Quality is not admission.** Importance, novelty, provenance, and current-state metadata do not decide whether a
   record can be written or pass lexical/vector admission. They only influence bounded order or maintenance eligibility.
5. **Uncertain time fails safe.** A conflict without a reliable lifecycle relation, or with unknown/incomparable time,
   remains visible as `unresolved_conflict`; relevance and RRF order are never used as temporal evidence.
6. **Reversibility is named precisely.** L1 promises recoverable deactivation. L2 must record its operation and define
   compensating behavior; `reactivate()` alone is not a rollback of consolidation, promotion, or derived views.
7. **Backend parity.** SQLite and OceanBase expose the same validity filtering, bounded ordering, lifecycle reasons,
   rebuild behavior, and disabled-mode results.

## Authoritative and derived state

The existing Memory manifest remains the authority for active/inactive entry state. This RFC introduces a rebuildable
**Memory lifecycle projection**, adjacent to current-head search projections. It is keyed by exact Memory artifact,
entry, and entry-version identity and may be implemented as an extension of head storage or as a separate projection
table.

The projection contains no replacement body text. Its logical shape is:

```text
entry identity: memory_artifact_id, entry_id, entry_version_id
quality:        importance, evidence_strength, provenance class, novelty relation summary
validity:       current | inactive | superseded | unresolved_conflict
lineage:        validity_reason, successor identity when known
time:           recorded_at/effective_at plus an explicit unknown state
lifecycle:      tier, protected/pinned state, lifecycle interval, automatic-action reason
observability:  bounded source/artifact reference counts and rule-version identifiers
```

The projection is derived from authoritative Memory Revisions, their exact evidence references, declared Source
attributes, and bounded lifecycle/operation records. It must be rebuildable. It must not affect entry content hashes
or substitute a second content authority.

Time values require an explicit persistence rule. A `recorded_at` value is written with the lifecycle or write operation
that first observes an entry; an `effective_at` value is accepted only when the evidence explicitly supplies one. The
operation record is keyed to the exact Memory revision and entry-version identity, so a projection rebuild does not
depend on the wall clock or on backend-specific row timestamps. Existing entries without such a record remain
`unknown`; they are neutral for freshness and are ineligible for age-based L1 deactivation until a later authoritative
event supplies a usable time. A derived `created_at` or `revised_at` field cannot by itself make an old entry age-eligible.

`MemoryHit` continues to carry only its existing identity, text, public RRF score, and matched channels. The read path
may attach an internal `MemoryContextAnnotation` containing validity, reason/successor, time-known state, and bounded
provenance summary. Context rendering uses that annotation to label current, historical, or unresolved evidence.

## Source authority and verification prerequisite

Current `Source` carries `name`, `definition_version`, `materialization`, and `description`; `SourceDefinition` carries
its version and projections. Neither carries an authority or verification declaration. `SourceRef` is only an identity
and must never be used to guess trust.

Before stage-1 quality ranking can be enabled, Source registration gains a reconstructible adapter contract,
provisionally named `MemoryEvidenceDeclaration`:

```text
SourceDefinition.memory_evidence:
  authority: untrusted | user_asserted | repository_attested | system_attested
  verification: unknown | verified | not_verified
  declaration_version: stable contract version
```

The precise enum labels may be refined during the Source-contract implementation, but these properties are required:

- the adapter or trusted registration configuration declares them explicitly;
- the declaration is versioned and reconstructible for historical Source materialization;
- content text, a `SourceRef`, or a model cannot manufacture a stronger declaration; and
- absent declarations resolve to `unknown`/untrusted and do not enable stage-1 quality ranking.

When a Memory entry is written, the declaration version and its resolved authority/verification state are snapshotted
in the bounded lifecycle evidence for that exact Source materialization. Rebuilds therefore use the historical
declaration that was in force, rather than the current registry result. If the snapshot is absent or cannot be
validated, the entry remains `unknown`/untrusted and the ranking policy stays disabled for that deployment.

This is a Source/SourceDefinition adapter-surface change and lands before, not alongside, the ranking feature. The
first ranking policy does not use a `verified_source_bonus` or model-generated authority score.

For deployments that accept materially untrusted Sources, a later evaluation may compare a bounded
source-class-occupancy policy with neutral and calibrated provenance-ranking baselines. It may cap the share of a fixed
candidate pool or delivered Context Pack occupied by a lower-authority class, but must retain an explicit route for
legitimate answer-bearing evidence. This is an opt-in experiment, not a stage-1 default or a substitute for the Source
declaration contract.

## Independent quality dimensions

The policy keeps these dimensions separate:

| Dimension | Derived from | Stage-1 use | Forbidden shortcut |
| --- | --- | --- | --- |
| Provenance/verification | declared Source contract and exact evidence | protects authority-sensitive records; may support a bounded positive ordering signal after calibration | infer from `SourceRef`, prose, or model output |
| Importance | deterministic entry-version features | bounded ordering and L1 eligibility | model free-form score |
| Novelty | normalized equality and bounded relation checks | relation/proposal and density observability | automatic importance downgrade or semantic deletion |
| Current-state | explicit entry classification and temporal query intent | freshness eligibility only | generic “newer is better” ranking |

Stage 1 emits no `importance_band`, `importance_reason`, or model ranking bonus. The deterministic importance seed is:

```text
base(kind):       fact=1, preference=1, decision=2, constraint=2, working_note=0, unknown=1
updates_state:    +1 when an evidence-backed revise changes current state
thin_penalty:     0 in stage 1; a future versioned content-density rule may add -1 only after calibration

importance = clamp(base + updates_state - thin_penalty, 0..3)

caps: weak evidence, session tier, and uncorroborated working notes do not exceed normal.
```

These values are calibration seeds, not defaults to enable. The score is recalculated on add or evidence-backed revise,
not by a periodic model regrade. Unknown kinds use the neutral `normal` base. Stage 1 does not infer a thin-content
penalty from prose; any future content-density rule must be deterministic, versioned, and disabled when its required
inputs are unavailable. The score may change through an explicit revise, corroborating evidence, or explicit user
override. Validity and novelty are not inferred from importance.

## Validity and temporal behavior

For ordinary current-state recall, the lifecycle projection enforces this policy after channel admission and before
RRF:

| Projected validity | Ordinary current-state recall | Exact/history read |
| --- | --- | --- |
| `current` | eligible | eligible |
| `inactive` | filtered | available with deactivation reason |
| `superseded` | filtered | available with successor/reason |
| `unresolved_conflict` | eligible and labeled | eligible and labeled |

No generic HTTP historical-search parameter is introduced in the first implementation phase. Existing exact Artifact
and entry-version resolution remains the historical path. A later public temporal query API must make its requested
view explicit; it may not silently use ordinary current-state search semantics for historical questions.

An inferred semantic relation is insufficient to set `superseded`. L2 approval or direct authoritative lifecycle
evidence is required. Unknown and incomparable time values remain explicit `unknown`, never an ordering key.

## Retrieval algorithm and fixed membership

For query `q`, requested limit `k`, and current search configuration, stage 1 is:

```text
per-channel candidates
  -> existing FTS/vector admission
  -> validity filtering for the requested temporal view
  -> baseline RRF and coarse-pool truncation
  -> bounded stable quality reorder inside that round's fixed pool
  -> existing optional listwise reranker over the same member identities
  -> `result.hits`
  -> [recall gate enabled] existing sufficiency assessment and bounded next-round decision
  -> final limit and existing Context Pack candidate and byte budgets
```

`fuse_rankings()` currently truncates to its `limit`; `MemoryService` supplies `coarse_limit`, which is `k` without a
reranker and at least the configured reranker candidate limit with one. Quality runs only after that truncation.
It cannot extend fusion, lower admission thresholds, or alter the reranker’s member identities.

When the recall gate is disabled, this sequence has one post-RRF pool. When it is enabled, the same sequence runs for
each issued round: the gate receives the `result.hits` produced after the current optional reranker, exactly as it does
today. Quality does not create a new gate-input mode or defer reranking. The reorder is a deterministic
`bounded_stable_reorder`:

1. Start from the fixed RRF sequence.
2. Compute a policy-versioned proposed rank adjustment from the independent dimensions and query intent.
3. Produce a stable order in which every member moves no more than two positions from its baseline rank; ties retain
   baseline RRF order.
4. Assert that the before/after identity sets are identical.

The former `0.5..2.0` multiplicative scheme is rejected. With RRF constant 60, the rank-1/rank-10 single-channel
spread is only about 1.15x, rank-1/rank-30 about 1.48x, and rank-1/rank-64 about 2.03x. A 4x multiplier is therefore a
first-class ranking term, not a bounded tiebreaker.

Freshness is a separate, query-time hint:

```text
freshness(age) = alpha + (1 - alpha) * 2^(-age / half_life)
```

`alpha` and `half_life` are calibration inputs. Freshness is eligible only for explicitly current-state records and
explicit current/temporal query intent. It remains neutral for historical facts and completed decisions; unknown time
is neutral. It is not multiplied by importance, and it cannot demote a high-authority or critical record. The first
stage uses revision age only as a proxy, not an implementation of usage-frequency memory.

## Ranking trace

`MemoryRankingTrace` is a proposed in-process diagnostic value, separate from RFC 0080’s `MemoryRerankTrace` and not
present in `master` at the time of this RFC. Its bounded, content-free decision data includes:

```text
policy identifier and parameter version
baseline fixed-pool identities and baseline ranks
quality-adjusted order and bounded displacement
dimension/rule codes used for each adjustment
validity-filter count and unresolved-conflict count
membership_changed = false
```

It is available whether or not reranking is enabled. It does not alter the public RRF `score`, add an HTTP field, or
reuse a reranker trace whose absence when reranking is disabled would hide quality behavior.

## Compatibility with recall sufficiency (#1556 / #1596)

The disabled-by-default recall sufficiency gate in [RFC 1560](https://github.com/oceanbase/powercontext/blob/master/docs/en/rfcs/1560_recall_sufficiency_gate.md)
and PR #1596 may issue additional bounded searches with a relaxed `AdmissionFloor`. That expansion is a new recall
round, not a quality-driven pool-membership change.

For each issued round, this RFC applies validity filtering after that round’s channel admission and before baseline RRF,
then applies a bounded reorder within that round’s fixed coarse pool before the existing optional listwise reranker.
The gate receives the reranker-produced `result.hits`, preserving RFC 1560’s current gate-input semantics. This RFC
does not defer reranking, substitute baseline-order candidates, or change the gate algorithm, configured maximum rounds,
admission floors, query-embedding reuse, cost-trace semantics, or the in-process `RecallEffort` sink. With quality
disabled or neutral, behavior is identical to RFC 1560/#1596. When quality is enabled, its bounded reordering can be
observed through the existing reranked gate input; evaluation must report any resulting expansion decision, final-pool,
and cost differences. `MemoryRankingTrace` and the gate’s aggregate effort trace remain separate. Evaluation must also
confirm that every quality reorder preserves the member identities passed to that round’s reranker.

## Near-duplicate alignment

Near-duplicate alignment is a bounded write-side candidate procedure, not a destructive deduper:

```text
normalized content equality
  -> bounded lexical candidates
  -> optional top-k semantic neighbors within the target Memory
  -> relation/evidence-increment record or review proposal
```

Exact normalized equality retains the existing no-op behavior. For semantic neighbors, thresholds such as
`tau_same` and `tau_similar` are calibration inputs only. A semantic relation never automatically lowers importance to
`low`, deactivates an entry, merges bodies, or assigns `superseded`.

Deployments without embeddings may collect deterministic lexical evidence and create a review proposal, but must not
automatically merge or discard paraphrases. Direct explicit writes and deterministic adapters may retain their existing
idempotency paths. Bulk imports may defer the bounded check to an offline health pass.

## Recall feedback

Ordinary search remains read-only. It does not lock, persist a recall event, or update `last_recalled_at` in stage 1.
The first policy uses revision age as a limited activity proxy.

A later opt-in capability may asynchronously record bounded recall events and fold them into a projection. ACT-R
base-level learning is motivation for that later work: its activation is a power-law sum over multiple practice times,
not this RFC’s single revision-age proxy. Frequency must be introduced together with provenance protection because a
low-authority entry can otherwise gain influence merely by being accessed often.

## L1: reversible automated deactivation

L1 is disabled by default and must support dry-run output. It may deactivate an entry only when all conditions hold:

1. the authoritative manifest and lifecycle projection identify it as active/current;
2. its importance is at or below the configured floor, initially only `low`;
3. its applicable retention interval has expired;
4. it is not pinned, protected, or explicitly retired by the user;
5. it is not a session-tier entry awaiting its explicit session/task boundary; and
6. it has no unresolved inbound relation or unhandled derived view that would break evidence lineage.

L1 emits `forget(..., reason="auto_decay:v1")` and records a bounded action reason. It is idempotent and uses normal
scope-lock/head-CAS discipline. `reactivate()` restores the original entry version without generating a new body
version. User actions win: an explicit restore starts a new lifecycle interval, and a user revise refreshes derived
time/quality data.

`session_end` is a separate deterministic lifecycle event, not an age/importance L1 decision. When the caller closes a
session or task, a `session`-tier entry may be deactivated with `reason="session_end"` without waiting for retention or
importance thresholds, subject to the same protection and concurrency checks. It remains recoverable. The L1 age path
does not process session-tier entries; this prevents the tier table from conflicting with the low-importance L1 floor.

Tiers are derived lifecycle metadata, not transcript ownership:

| Tier | Intended use | Lifecycle behavior |
| --- | --- | --- |
| `session` | caller-marked task/session note | deactivate at the explicit boundary with `session_end`; recoverable |
| `short` | temporary working note | shorter configured retention window |
| `long` | durable constraint, decision, or historical fact | normal protection and retention path |

Capacity pressure creates a bounded cleanup **proposal**, not automatic compaction. Values such as “50 new low entries”
or “60% inactive with 1,000 entries” are seeds only. Calibration binds pressure to the existing Context Pack shape—16
Memory candidates, at most 8 injected items, caller byte budget—and observed manifest size and write latency.

## L2: reviewed semantic maintenance

L2 is a restricted Memory-specific extension of RFC 0050. Ordinary Memory writes still commit directly. Only the
following maintenance-generated proposals enter review:

- **consolidation:** an evidence-preserving revision plus redundant-entry deactivation;
- **explicit supersession:** retain both entries, attach successor/reason, and make the predecessor invalid for
  ordinary current-state recall;
- **promotion:** promote recurring working-note evidence across independent windows to a durable Memory kind.

Each proposal includes exact pre-state identities, source/artifact evidence, relation or contradiction evidence,
proposed changes, derived-view handling, and a policy version. Approval is the only path that creates the resulting
Memory Revision. Inferred relations require review; consolidation preserves the evidence union and specific names,
numbers, and dates. Repeated body “polishing” without new evidence is forbidden.

L2 adds a Memory maintenance operation record with operation identity, approved proposal identity, affected entries,
pre-state, created revisions/relations, derived-view effects, and declared compensation behavior. It must explicitly
list effects that cannot be rolled back. This is distinct from L1 recovery and from PR #1586’s Experience-specific
recurrence ledger: Memory L2 must not reuse or overload that ledger, its event semantics, or its Experience review
routing. Any Candidate/API extension for Memory is staged after #1586 and preserves existing Experience/Skill contracts.

## Compression and physical retention boundary

This RFC does not compress or summarize authoritative Memory entry bodies. They are self-contained, citation-bearing
records; rewriting them risks losing names, dates, quantities, and auditability. It distinguishes:

1. authoritative entry bodies, which this RFC does not compact;
2. discardable derived views, which a later design may regenerate or retire; and
3. Topic Memory summaries, which are a separate organization-layer concern.

Physical tombstone/manifest compaction, legal retention, external erasure, and cross-artifact cleanup are out of
scope. #1425 owns that broader policy boundary. Inventory observations may report bounded active/inactive counts,
manifest growth, and automatic-action categories, but they do not authorize deletion.

## Compatibility, persistence, and API impact

- Existing Memory body, revision, manifest, evidence, search identity, Handoff citation, and public RRF score contracts
  remain unchanged.
- New lifecycle and quality data is rebuildable projection data or bounded action evidence, never entry-body content.
- First-stage HTTP, MCP, CLI, and OpenAPI shapes do not change. The Source adapter contract is an implementation
  prerequisite, not an implicit `SourceRef` interpretation.
- Project instruction files such as `CLAUDE.md` and `AGENTS.md` remain source-authoritative, file-backed durable
  context and are outside automatic Memory lifecycle control; see the [file-backed memory discussion](https://github.com/vitoworleone/claude-code-handbook/blob/main/docs/manual/part-04-context/ch-10-memory-system.md).
- The first release exposes no ranking trace or lifecycle annotation over HTTP. In-process traces must not persist
  Memory content merely for observability.
- SQLite and OceanBase migrations/rebuilds must produce equivalent projection state and order.
- #1321’s append-write amplification, storage layout, manifest splitting, and physical compaction remain outside this
  RFC; this RFC only supplies the Memory logical lifecycle and retrieval-quality boundary.
- Lifecycle inventory must not silently add fields to the public `ScopeStats` contract introduced by #1586; any public
  statistics API extension requires its own compatible contract update.

## Evaluation and calibration

All parameters are seeds. No non-neutral default is enabled without evidence from the following matrix:

1. **Quality benefit:** compare baseline and fixed-pool quality order under the same Context Pack byte budget, using
   Recall@k, MRR, answer/task outcomes, latency, and cost.
2. **Temporal validity:** verify a same-`entry_id` revise returns only the current successor for ordinary recall while
   exact historical resolution returns the predecessor and lifecycle reason.
3. **Conflict safety:** verify known inactive/superseded entries cannot reach a reranker or tool-using Context Pack,
   while unknown/incomparable conflicts remain present and labeled.
4. **Candidate density:** inject near-duplicates and low-value records while holding required evidence fixed; measure
   top-k degradation and use it to calibrate relation thresholds and bounded rank displacement.
5. **L1 dry run:** measure false retirement, protection, restore, action idempotency, and inbound-lineage handling.
6. **Longitudinal regression:** run Ledger-QA-shaped sequences of revisions and score both current-state and
   “what was true at time/revision T?” answers by final task/world state as well as retrieval metrics.
7. **Safety:** include provenance poisoning and low-authority/high-frequency-access cases separately from relevance.
8. **Source-class occupancy:** for deployments accepting materially untrusted Sources, compare the neutral baseline and
   any calibrated additive provenance-ranking baseline with an opt-in, bounded cap on the lower-authority share of the
   fixed candidate pool or delivered Context Pack. Preserve a route for legitimate answer-bearing evidence and report
   attack success, trusted and untrusted evidence recall, citation correctness, abstention, and false exclusion. This is
   not a default policy without a concrete threat model and held-out benefit.
9. **Recall-gate composition:** with FTS/vector/hybrid, reranker on/off, and recall gate on/off, confirm that a disabled
   or neutral policy gives the gate the same reranked `result.hits`, expansion decisions, final pool, and effort trace as
   RFC 1560/#1596. With quality enabled, confirm each reorder preserves the member identities of that round’s reranker
   pool and report any changes in gate decisions, final membership, reranker calls, and `added_generation_calls`.
10. **Backend parity:** run conformance and rebuild cases on SQLite and OceanBase; report retrieval, task, safety, and
   cost separately.
11. **Full-context reference:** report a full-material reference independently, so model reading failures are not
    misattributed to retrieval or lifecycle policy.

The evaluation follows RFC 0080 and RFC 1229 boundaries: PowerContext exposes its actual behavior and traces; workload
data, judge policy, and acceptance criteria remain explicit. Results from prior work are hypotheses, not product
acceptance thresholds.

## Delivery sequence

1. **Source prerequisite and neutral projection:** add the versioned Source evidence declaration, lifecycle projection
   rebuild, internal annotation shape, and conformance fixtures. Ranking remains disabled.
2. **Stage-1 validity and fixed-pool ranking:** add current-state validity filtering, `MemoryRankingTrace`, and the
   disabled-by-default `±2` reorder. Preserve #1596’s per-round rerank-then-gate input semantics and RFC 0080
   reranker behavior.
3. **Alignment and L1:** add bounded near-duplicate relationships/proposals, inventory, tiers, and dry-run L1.
4. **L2 review:** separately stage Memory Candidate/review integration, operation records, consolidation, explicit
   supersession, and promotion. Do not couple it to PR #1586’s Experience recurrence work.
5. **Physical retention:** propose a separate RFC only if inventory and evaluation demonstrate a storage problem.

## Acceptance criteria for a future implementation

- With every lifecycle option disabled, write semantics, ranking, public contracts, and backend conformance are identical to
  the current behavior.
- Importance, evidence strength, lifecycle observations, and Source declarations are rebuildable from authoritative revisions
  plus exact, bounded operation evidence; none change entry content hashes.
- Low-importance entries remain eligible for ordinary recall. Only explicit or policy deactivation removes an entry from the
  active candidate surface, and `reactivate()` restores the original version without a new body version.
- L1 deactivation affects only eligible low-tier, expired, unprotected entries; it records `auto_decay:v1`, supports dry-run,
  is auditable and idempotent, and is recoverable. Session-tier deactivation records `session_end` and is recoverable.
- No L2 proposal changes authority before approval. Approved consolidation preserves evidence lineage; explicit supersession
  preserves old and new states; unresolved conflicts remain visible without inferred time order.
- Near-duplicate handling never performs unverified semantic merging or discarding, and similarity findings never silently
  lower importance into the deactivation floor.
- Known inactive/superseded records are filtered before RRF and reranking for ordinary current-state recall; historical reads
  preserve exact originals, while unknown conflicts remain marked.
- Quality ordering never changes channel admission, the fixed RRF-pool membership passed to a round’s reranker, the gate
  algorithm/configuration, or public RRF score semantics. Each round still reranks before the gate receives `result.hits`;
  disabled or neutral quality preserves RFC 1560/#1596 behavior. `MemoryRankingTrace` makes validity and membership
  invariance auditable.
- The evaluation plan reports retrieval, task, safety, cost, and backend-parity results for SQLite and OceanBase, including the
  joint #1556/#1596 recall-gate matrix.

# Drawbacks

- A Source authority/verification declaration is a new adapter-contract responsibility and must be migrated carefully.
- Derived projections, validity filtering, and backend parity add implementation and conformance complexity.
- A bounded reorder can still harm a relevant baseline rank if calibration is poor; it is intentionally small and
  disabled by default for that reason.
- L1 can wrongly retire a rarely used but valuable low-importance entry; recovery reduces but does not eliminate cost.
- L2 requires reviewer attention and explicit compensation design, so proposals may accumulate.
- Near-duplicate analysis costs search/index work and cannot safely automate semantic decisions without embeddings.

# Rationale and alternatives

- **Multiplicative importance/decay weighting** was rejected: RRF’s actual score range makes a 4x multiplier dominate
  the coarse order, and multiplying freshness by importance can demote a durable constraint.
- **Quality before coarse truncation** was rejected for stage 1: it changes reranker membership and needs independent
  experiments, trace semantics, and a broader recall policy.
- **Model importance scores** were rejected: provider/time drift makes a quality value difficult to reproduce and test.
- **Automatic semantic deduplication or supersession** was rejected: similarity and relevance are not sufficient
  evidence for destructive lifecycle change.
- **A temporal knowledge graph** was rejected for now: it creates a second authority and schema beyond revisioned
  Memory.
- **Body compaction** was rejected: it harms exact citation without solving active-pool interference.
- **Pure ACT-R decay** was rejected: its usage-event, power-law mechanism is not represented by a single timestamp and
  would be unsafe as a generic historical-fact policy.

# Prior art

- [CoALA](https://arxiv.org/abs/2309.02427) distinguishes working, episodic, semantic, and procedural concerns; this
  RFC adopts separation of operations rather than a new cognitive architecture.
- [MemGPT / Letta](https://www.letta.com/) demonstrates bounded in-context memory and background consolidation; this
  RFC places consolidation outside the critical read path.
- [mem0 memory types](https://github.com/mem0ai/mem0/blob/main/docs/core-concepts/memory-types.mdx) informs the
  session/short/long time-scale distinction, while its [recency discussion](https://mem0.ai/blog/memory-decay-for-long-running-agents-how-recency-aware-ranking-fixes-retrieval-staleness)
  is background for a query-gated freshness experiment, not an automatic deletion policy.
- [Generative Agents](https://arxiv.org/abs/2304.03442) motivates keeping relevance, recency, and importance distinct;
  it does not justify importing free-form LLM scores or creation-time recency as usage history.
- [ACT-R base-level learning](https://doi.org/10.1037/0033-295X.111.4.1036) motivates future recall-event evaluation,
  not the first-stage revision-age proxy.
- [VoiceMem](https://arxiv.org/abs/2608.26005) supports the candidate-density hypothesis only; it is not evidence for
  this RFC’s freshness formula.
- [Revoked](https://arxiv.org/abs/2609.08258) motivates deterministic retrieval-time enforcement of known validity.
- [Utility Under Attack](https://arxiv.org/abs/2608.21230) motivates source-poisoning and non-default source-class-
  occupancy evaluation; it does not justify inferring source authority from content or making a cap the default.
- [Selective Memory](https://arxiv.org/abs/2603.15994) and [ProMem](https://arxiv.org/abs/2601.04463) motivate
  reversible retirement and later alignment/verification work, not unreviewed semantic deletion.
- [Retain or Consolidate?](https://arxiv.org/abs/2607.17545) reinforces preserving raw authority when evidence already
  fits the budget, rather than introducing automatic body compaction.
- [Rate–Distortion Theory for Agent Memory Compaction](https://arxiv.org/abs/2607.08032) motivates measuring
  irreversible, query-unknown pre-query discarding under a shared budget; it does not authorize body compaction here.
- Additional proposer-supplied background is collected in [1](https://mp.weixin.qq.com/s/UDkGQvutJn-OQg0KunOqzw),
  [2](https://mp.weixin.qq.com/s/eijn2Cg3TSQqrqy2UU4fog), and
  [3](https://mp.weixin.qq.com/s/sQyuqmnl5EHMb-l356bFVQ). These links provide context for memory organization and
  lifecycle discussion only; they are not sources for numeric defaults or acceptance thresholds.

# Unresolved questions

- What exact `MemoryEvidenceDeclaration` enum and registration mechanism best fit built-in and third-party Source
  adapters while remaining reconstructible?
- Which explicit field or caller contract classifies a Memory entry as current-state material?
- What public historical-read contract, if any, should follow the initial exact-revision path?
- Which bounded rank-displacement mapping is stable and beneficial across calibrated workloads?
- Should L2 Memory proposals extend the generic Candidate family or use a dedicated Memory maintenance proposal type?
- What compensation actions are feasible for each approved L2 operation and each derived view?
- When inventory is available, which physical-retention measurements warrant a separate compaction RFC?

# Future possibilities

- An asynchronous, privacy-bounded recall-event projection with authority-aware ACT-R-inspired features.
- User-visible pin/protection controls and explicit lifecycle inspection.
- A public, redacted historical-read and lifecycle annotation API.
- A separately reviewed temporal relation model with explicit effective-time intervals.
- Topic-level derived summaries and a physical manifest archival/compaction RFC if measurements justify them.
