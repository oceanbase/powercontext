- Proposal Name: `replay_guided_artifact_evolution`
- Start Date: 2026-09-27
- Tracking Issue: [#1635](https://github.com/oceanbase/powercontext/issues/1635)
- Related RFCs: [Experience and Skill](0051_experience_skill_artifact_families.md), [Artifact Dreaming](1510-artifact-dreaming.md), [Artifact Processing Supervisor](1515_artifact_processing_supervisor.md), and [Recurring Failure Repair](1557_recurring_failure_repair.md)

# Summary

This RFC explores adapting the core Dream-RSI mechanism of using realized exploration history as a replay environment to PowerContext's Experience and Skill evolution. It adopts the idea, not the implementation: PowerContext will not depend on, integrate, fork, vendor, or execute Dream-RSI code, packages, schemas, protocols, data formats, or scripts.

The RFC answers two questions:

1. self-evolution should first improve Experience and Skill generation, not the `prepare_context` hot path; and
2. PowerContext's existing explicit Dream mechanism should be enriched with observable proposal attempts, faithful replay, bounded comparison, and downstream evidence without weakening Candidate Review or Artifact authority.

The near-term scope is deliberately narrow:

- **P0:** document current boundaries and protect them with behavior-equivalence tests;
- **P1:** define an evaluation-owned recording contract and faithfully replay realized history; and
- **P2 spike:** test one small, opt-in, Experience-only extension to explicit `refine_experience` Dream runs.

P3 Experience-to-Skill and usage-driven Skill replacement are conditional follow-ons. P4 cross-policy improvement is a research track. They are not short-term delivery commitments.

# Motivation

## Why not `prepare_context`

`prepare_context` is called on the online path and assembles bounded, citable context under caller configuration, Scope, retrieval results, and latency constraints. In most products, the available sections and selection strategy are business decisions rather than an open-ended search performed for every request. Adding multi-attempt exploration there would directly increase serving latency and make behavior less predictable.

Approved Experiences may still supply downstream observations about recall, task outcomes, runtime, token use, turns, and tool calls. Those observations can inform Artifact evolution without making `prepare_context` the first self-evolution boundary. Any future context-assembly policy optimization requires a separate RFC.

## Why Experience and Skill generation

Experience and Skill already have the lifecycle needed for controlled improvement: exact evidence, typed generation, pending Candidates, human Review, immutable Revisions, lineage, and later outcome evidence. Generation can run asynchronously, and failure to find an improvement is a valid result. This makes Artifact generation a more natural place to test exploration and replay while keeping the serving path unchanged.

PowerContext also already has an explicit Artifact Dreaming contract. The proposal enriches that contract rather than creating a generic self-modifying policy platform.

# Terminology and current boundaries

Three objects must remain distinct.

| Object | Current or proposed role | Candidate semantics |
| --- | --- | --- |
| Experience incubation | Existing automatic processing over a bounded `task-outcome` Source window. One generation call yields `0..N` proposals and advances its own cursor atomically with accepted processing results. | Each valid proposal may become a pending Candidate under current behavior. |
| Explicit `DreamRun` | Existing asynchronous run over caller-selected exact Memory entry versions, Experience Revisions, and optional Sources. Operations are `refine_experience` and `derive_skill`; one successful run produces at most one Candidate. | The authoritative live business object for the proposed spike. |
| Artifact Evolution Policy / `ProposalAttempt` | The evaluation-owned policy chooses a finite action and budget; each realized isolated action is recorded as a `ProposalAttempt` with its result. Neither is a Runtime Artifact or a second run API. | A `ProposalAttempt` never enters the Review Inbox directly. At most one nominated result may create the existing Candidate for a `DreamRun`, subject to a separate contract change. |

Experience incubation and explicit Dream solve related generation problems, but they are not the same implementation object. This RFC does not merge their cursors, storage, APIs, or Candidate-count rules.

The public Dream budget allows `max_model_calls` in `1..2` and primarily allows retry after a recoverable inference failure. Such a retry continues one logical generation attempt. It is not evidence that Dream already explores two proposal branches. A P2 additional attempt is a separately recorded proposal action with its own output, validation, and cost.

Likewise, Experience incubation may return multiple proposals from one model call. That is multi-output generation, not multi-attempt exploration.

The evaluation-level `objective` is derived, not a new Runtime field: `(scope_id, operation, target_artifact_ref_or_none, expected_head_or_none, input_manifest_digest, experiment_id)`. Only attempts with the same tuple are compared or deduplicated. This identity gives the experiment a stable grouping boundary without changing `CreateDreamRunRequest`.

# Design decisions

## Preserve `DreamRun` as the live authority boundary

This RFC does not add a public `EvolutionRun` beside `DreamRun`. The existing `DreamRun` remains authoritative for live status, exact inputs, operation, result, cost, and its optional Candidate. P1 stores `ProposalAttempt` and replay data in an evaluation-owned bundle first, without a database migration or public API.

This separation avoids prematurely making experimental search-tree nodes part of the Runtime contract. It also preserves the existing guarantee that one successful explicit Dream run creates at most one Candidate.

## Preserve Review and Artifact authority

An exploration layer may generate, validate, compare, prune, stop, or nominate. It cannot approve a Candidate, write an Artifact Revision directly, publish or install a Skill, execute a Skill, grant permissions, forge Source evidence, or cross Scope boundaries. Human Review remains the only route from a pending Candidate to an immutable Revision.

## Enrich Dream incrementally

The conceptual bounded flow is:

```text
explicit refine_experience Dream objective
  -> baseline proposal attempt
  -> deterministic validation
  -> optional additional attempt under an explicit small budget
  -> deterministic comparison with auditable reasons
  -> nominate zero or one proposal
  -> existing pending ArtifactCandidate
  -> human Review
  -> approved immutable Experience Revision
```

The P2 spike implements the additional attempt as an evaluation-owned shadow action: it records validation and comparison evidence but does not nominate the shadow result or mutate the Candidate/Review path. Nomination of an additional branch remains a follow-up contract change. Multi-attempt behavior is opt-in, asynchronous, and experimental; it is not enabled for all Dream runs and does not change scheduled Experience incubation.

# Near-term scope

## P0: baseline boundaries and equivalence

P0 introduces no behavior change. It documents and tests three baselines independently:

- automatic Experience incubation over Source windows;
- explicit `refine_experience` Dream; and
- explicit `derive_skill` Dream.

Equivalence tests use public behavior and pinned deterministic collaborators where needed. For the same resolved input and collaborator result, they verify the same:

- `proposed`, `no_change`, `needs_evidence`, or execution-error outcome;
- Candidate count, family, operation, target, and pending status;
- exact evidence and lineage refs;
- Experience-incubation Source-cursor advancement or non-advancement (explicit Dream never advances or clears the ordinary Source cursor);
- retry and model-call accounting; and
- Review and authorization behavior.

P0 adds no migration, new public API, default configuration, additional Candidate, or extra model call. Its exit gate is that current behavior is explicit and protected well enough for later experiments to measure a real delta.

## P1: recording contract and faithful replay

P1 defines a versioned, evaluation-owned replay bundle. It follows RFC 1229's workload and evaluation conventions where applicable and may reference typed `powercontext.e2e-evidence/v1` records; it is not a second workload or replay harness. Ordinary production capture is off by default and does not retain complete prompts, task bodies, or evidence bodies unless a controlled evaluation explicitly supplies reviewed or synthetic material.

P1 is intentionally split into an MVP and a deferred P4 extension. The MVP contains only the records required for the Experience-only spike and read-only faithful replay. Cross-policy replay metadata is useful, but it is not an MVP implementation blocker.

### Evaluation ownership and contract adapter

P1 and P2 are owned by a Dream Evolution Evaluation Harness under `evaluation/`. It reuses existing components for Scope isolation, workload selection, retention, and report rendering through one Dream execution adapter. The adapter owns the exact Dream input fixture, baseline/shadow allocation, attempt recording, replay lookup, and result projection; it does not turn the Dream bundle into a Bub workload or a SWE-Pro run.

The only new versioned artifact required by this RFC is `powercontext.artifact-evolution/v1`. Its `execution.type` is `dream`, and its workload section records the `refine_experience` operation, exact Memory/Experience/Source refs, target and expected head, fixture digest, treatment, and declared budgets. A `powercontext.e2e-task/v1` entry may be referenced as upstream task provenance when one exists, but Dream inputs do not need to fit that catalog. When an underlying run produces reusable typed evidence, the bundle may reference `powercontext.e2e-evidence/v1` through the adapter; the adapter adds Dream-specific refs, action signatures, nomination state, and validator results without changing the shared evidence contract.

The existing Bub/Harbor catalog and independent SWE-Pro patch/grading path remain separate owners. Their workload or result artifacts are not passed through unchanged. The evaluation report is a projection of `powercontext.artifact-evolution/v1`, outside Runtime tables and the public run API. The adapter rejects a bundle whose schema, operation, fixture digest, or evidence reference does not match the declared contract.

### Run and decision fields

Each recorded run contains at least:

| Field group | P1-MVP required information |
| --- | --- |
| Identity | Bundle schema version, run id, exact `DreamRun` ref when applicable, timestamp, Scope-safe workload identity |
| Inputs | Exact input manifest, immutable refs, content/snapshot digests, evidence roles, operation, target and expected head |
| Generation and validation | Policy/generator/model/schema/runtime identities, canonical chosen-action signature, typed output or terminal result, validator/scorer identities and individual results |
| Outcome links | Exact `DreamRun`, input, and Candidate refs when they exist; Review, Revision, and downstream outcome joins are optional follow-up links |
| Cost | Proposal attempts, model calls and retries, input/output tokens, wall time, concurrency, and validation failures |

The following P4 extension fields are recorded only when the evaluation harness already exposes them; they are not required to ship P1-MVP:

- the complete action set available at each decision step;
- logging-policy version and selection probability, or deterministic selection reason and tie-break rule;
- explicit seed identity when one exists;
- RFC 1229 split/holdout identity and arm-scoped run identity;
- support and coverage state; and
- a sealed post-decision outcome record for asynchronous Review, Revision, Source, or recurrence joins.

The P1-MVP decision record contains:

- the observations revealed before the decision;
- the chosen canonical action signature; and
- the decision parent and ordering information.

Every `ProposalAttempt` records:

- its parent decision and canonical action signature;
- exact evidence and target refs used by that action;
- typed proposal, `no_change`, `needs_evidence`, or error result;
- output digest and optional controlled payload;
- validator/scorer identities and versions with individual results;
- incurred cost and retry information; execution/retry `attempt_count` is distinct from `proposal_attempt_id`; and
- nomination status and reason.

When the P4 extension is available, selection probabilities make the logging policy auditable; they do not manufacture action overlap. A deterministic logger will commonly have poor support for a different policy. Any post-decision links are written asynchronously with an idempotency key such as `(bundle_id, event_type, exact_ref)` and never rewrite the sealed pre-decision observations.

### Faithful replay semantics

This faithful replay mode is a read-only realized-attempt mode inside `powercontext.artifact-evolution/v1`, using the typed evidence references declared by its adapter. It may reference RFC 1229 workload provenance, but it is not the repository's script that replays requests through a live public API. Replay is a read-only lookup over realized records. Given the same revealed observations and canonical action signature, it returns the saved result and saved cost. It never calls a generator or evaluator that could create a new branch.

If the exact action signature is absent, replay returns `out_of_support` with a reason. It must not:

- use the most similar recorded attempt;
- infer an unobserved evidence grouping or target;
- rerun the model to fill a missing branch;
- fabricate a reward or zero-cost result; or
- allow future observations, future evaluation labels, Review decisions, or downstream outcomes to influence an earlier decision.

A replay reuses the recorded validator/scorer identities and results. Re-running a deterministic validator is an independent diagnostic and cannot replace the recorded result or nomination. P1 must report basic exact-replay coverage: supported requested decisions divided by all requested decisions, and complete supported trajectories divided by all requested trajectories. Unsupported decisions and trajectories stay in the denominator and are reported by workload, policy, operation, and environment where relevant. These ratios measure whether the realized records can be replayed exactly; they do not claim that another policy had sufficient action support.

### P1 validation

Pinned fixtures must demonstrate:

- record round-trip and schema-version rejection behavior;
- exact baseline action sequence, outcome, nomination, and cost reproduction;
- deterministic `out_of_support` for absent or mismatched actions;
- no model call, Candidate creation, Review mutation, or Runtime write during replay;
- no future-information leakage; and
- redaction/retention behavior for optional controlled payloads.

P1-MVP does not claim to support statistically complete cross-policy replay or to improve future live runs. P1 still requires the basic exact-replay coverage described above. Split/holdout isolation, cross-policy action support/coverage, and policy-level replay comparisons belong to the deferred P4 extension.

## P2: small Experience-only spike

Here, a spike means a time-boxed feasibility experiment with explicit inputs, budgets, and an exit gate. It is not a new production API or a default behavior change.

P2 is limited to an asynchronous, explicitly enabled `refine_experience` experiment and uses the workload section of `powercontext.artifact-evolution/v1` through the Dream Evolution Evaluation Harness. The existing `OFF`/`ON` arms belong to the evaluation service's treatment switch (currently plugin disabled/enabled); they are not Dream policy variants. The harness may reuse their Scope isolation, workload selection, and reporting primitives through an adapter, but it must define a Dream-specific treatment and result projection. It does not route Dream attempts through the Bub/Harbor catalog or the independent SWE-Pro runner. The spike does not introduce a new arm, holdout, or statistical-allocation subsystem. Scheduled Experience incubation and `derive_skill` remain unchanged. The near-term spike has two separate analyses.

The baseline is the current Dream behavior: one run addresses one caller-selected question and produces one result; recoverable execution failures may retry with the same inputs. For a fair comparison, use a pre-registered, comparable, matched workload population and fix the baseline/experimental allocation before outcomes are known. Random allocation can be added later if the workload size warrants it. Triggering candidate behavior only after baseline failure answers a rescue question, not whether one versus two attempts is better overall. Rescue-after-failure results are therefore reported separately.

The experimental policy may request a bounded additional proposal attempt only when:

- deterministic validation finds no eligible proposal;
- exact inputs reveal a structural duplicate, target, or conflict condition covered by the experimental action space; or
- a preregistered experimental decision rule requests the additional attempt within budget.

The current public `DreamBudget.max_model_calls` is `1..2`, and the second execution attempt is already part of retry semantics. The spike does not raise this limit, consume retry budget, or put two independent branches into one existing `DreamRun`. Additional proposal attempts first use an evaluation-owned shadow budget. Shadow attempts create no Candidate; the actual Candidate path remains the current Dream behavior. Routing multiple branches directly into one Candidate requires a separate Dream budget/API contract and migration discussion.

The spike must distinguish proposal attempts from recoverable inference retries. Both count toward total model-call and wall-time cost, but only proposal attempts create independent comparison branches.

Before execution, the experiment fixes maximum proposal attempts, total model calls including retries, input/output tokens, wall time, and concurrency. It reports live `DreamRun.usage` as the authoritative live generation cost and shadow evaluation cost separately, so costs are never counted twice. The RFC intentionally does not standardize numeric defaults before workload measurement. A run that reaches any limit stops and records the reason.

### Pre-registered comparison rule

For each matched workload `W`, the harness uses the same immutable inputs to run the current Dream action `B` (baseline) and, when the decision rule permits it, one additional shadow action `S`. `B` remains the only result eligible for the existing Candidate/Review path. The primary P2 quality comparison scores `B` and `S` as separate proposals on the fixed workload; it does not use `max(B, S)` as a best-of-two production result and it does not treat `S` as a nominated Candidate.

The following fixture makes the rule concrete:

The scores below are illustrative; the actual Review scale and minimum effect are fixed before the workload is run.

| Workload | Baseline `B` | Shadow `S` | Quality comparison | Candidate and cost treatment |
| --- | --- | --- | --- | --- |
| `W1` | Eligible, blinded Review score 3 | Eligible, score 4 | Paired difference `S - B = +1`; both scores are reported | `B` remains the only Candidate result; charge `B` and `S` execution, retries, and validation costs |
| `W2` | Eligible, score 3 | Ineligible by deterministic validation | Score `B`; report `S` as an eligibility failure with no imputed quality score | `B` remains the only Candidate result; charge both attempts and all validation/retry costs |
| `W3` | Ineligible | Ineligible | Record zero eligible proposals; do not assign a synthetic quality score | No Candidate; charge all incurred execution, retry, and validation costs |
| `W4` | Eligible, score 4 | Eligible, score 4 | Record a tie and a zero quality delta | `B` remains the only Candidate result; charge both attempts and all validation/retry costs |

If `B` fails execution and `S` succeeds, the workload is reported in a separate rescue-after-failure stratum and is excluded from the primary one-versus-two-attempt comparison. Human Review effort is reported separately; the evolution cost for every workload includes baseline and shadow model calls, retries, wall time, tokens, deterministic validation, and failed or discarded attempts. Shared setup cost is charged once to the workload and its allocation is recorded. No missing score or failed attempt is silently removed from a denominator.

Before the spike starts, the team preregisters the minimum paired quality effect or cost reduction required to continue. A tie is zero improvement, and a zero-eligible workload is an eligibility failure rather than a quality gain. The exit gate can pass only when the pre-registered quality/cost condition is met and the Scope, lineage, target, fabrication, and Review-bypass guardrails remain clean.

### Initial validators

The spike uses deterministic validators only:

- output-schema completeness;
- exact evidence and citation resolution;
- Scope consistency;
- create/replacement target and current-head correctness;
- lineage completeness;
- exact or deterministically detectable duplication;
- deterministically detectable conflicts;
- budget and cost-accounting compliance; and
- invariants against fabricated evidence, outcome, approval, publication, installation, or execution claims.

These checks establish eligibility, not semantic superiority. Semantic proposal quality is assessed through blinded human Review on the fixed workload and then fresh live Dream validation. No LLM evaluator is part of the first spike.

### Nomination and exit gate

All attempts remain in the evaluation bundle. In this first shadow spike, no shadow branch is nominated; only the existing live Dream result can enter the Review Inbox. A later contract change may nominate zero or one branch, but that is outside this RFC's current P2 behavior. Discarded branches never become Candidates.

The spike reports the quality/cost Pareto frontier rather than one blended score. It proceeds only if, under comparable budgets, it improves grounded reusable-proposal quality or reduces cost without increasing Scope, lineage, target, fabrication, or Review-bypass violations. Otherwise, multi-attempt work stops; P0 and P1 may remain useful independently.

# Evaluation and cost model

Evaluation is mandatory but proportional to the stage:

| Stage | Required evidence |
| --- | --- |
| P0 | Behavior equivalence and invariant preservation |
| P1 | Replay fidelity, record completeness, basic exact-replay coverage, leakage checks, and replay cost |
| P2 | Deterministic eligibility, blinded Review disposition/reason, separate proposal-attempt and execution/retry/model-call/token/wall-time cost, and fresh live validation |
| Conditional later work | Approved-Revision recall or usage, recurrence or Skill validation, task outcome, runtime, tokens, turns, and tool calls |

Candidate count or approval rate cannot be the sole objective. A comparison reports separately:

- proposal validity and semantic quality;
- Review load and reviewer disagreement;
- evolution cost, including failed and discarded attempts; and
- delayed downstream effect when exact attribution exists.

Runtime, token, turn, and tool-call improvements are legitimate downstream measures, but they may appear only after an approved Revision is exercised on comparable tasks. They are not guaranteed effects of installing this mechanism and must be reported alongside the added evolution cost.

# Separation from recurring-failure outcomes

The recurring-failure-repair ledger and replay bundle answer different questions:

```text
ProposalAttempt / replay record
  = what a Dream generation strategy explored

recurrence ledger
  = what later happened to an approved Experience Revision in real tasks
```

The recurrence ledger owns evidence-derived `selected`, `recurred`, and `avoided` events. The replay bundle owns proposal actions, outputs, validation, nomination, and exploration cost. They remain separate semantic stores and may be joined only through exact Candidate, Artifact Revision, DreamRun, and Source refs.

An unapproved attempt cannot receive a recurrence outcome. Missing `selected`, `avoided`, Skill invocation, or task outcome evidence is unknown, not a negative observation. Replay must preserve that distinction when deriving any delayed metric.

# Conditional follow-ons

## P3a: Experience-to-Skill

After the Experience-only spike passes its exit gate, a separate scoped change may apply the same bounded attempt and nomination mechanism to the existing explicit `derive_skill` Dream operation. It must retain one Candidate maximum per run, standard Skill validation, human Review, and explicit publication/installation authority.

## P3b: usage-driven Skill replacement

PowerContext currently has a `skill-usage` evidence model and recording entry point, and the generation lineage contract contains `SkillGenerationOrigin.USAGE`. These are prerequisites, not an automatic usage-driven replacement producer.

Such a producer should be designed only after coverage and quality gates exist for selection, invocation, validation, task outcome, environment fingerprint, and missing-data rates. Non-invocation and absent outcomes must never be counted as success. P3b requires its own detailed proposal and validation workload.

## P4: cross-policy replay research

P4 may investigate alternative attempt-allocation, pruning, stopping, and nomination policies over realized histories. It is not currently scheduled engineering work. Research is meaningful only when logging-policy data provides adequate cross-policy action support and coverage, RFC 1229 workload manifests and arm assignments are stable, and promotion gates are preregistered.

Replay results screen candidates; they do not authorize deployment. A policy with a better supported replay result must still pass fresh live Dream runs, fresh workload evaluation, human Review, and code/configuration review. Arbitrary model-generated executable policy code is out of scope.

# Privacy, retention, and security

- Ordinary production does not save full evidence bodies or prompts for replay by default.
- Evaluation bundles prefer synthetic, reviewed, or redacted evidence and carry explicit retention and access policy.
- Digests establish identity but do not make sensitive content safe to disclose.
- Exact Scope and authorization checks run on live inputs; replay cannot be used to recover inaccessible bodies.
- Logs and reports must not expose evidence content, credentials, model secrets, or cross-Scope identifiers.
- Replay is read-only and has no Candidate, Review, Artifact, publication, installation, or execution capability.

# Compatibility and rollout

P0 changes no API, storage schema, migration, default, or observed Candidate behavior. P1 begins as a versioned evaluation format outside the public Runtime contract. P2 is available only in explicit evaluation or behind an opt-in experimental control; disabling it restores current explicit Dream behavior.

Any proposal to persist attempts in Runtime storage, expose them through HTTP, change `DreamRun` schemas, or enable production capture requires a follow-up contract review and migration plan. This RFC does not authorize those changes.

# Drawbacks and alternatives

The design adds instrumentation and evaluation work before visible generation improvement. Strict replay support may also make early datasets too sparse to compare policies. That is an intended limitation: filling gaps with inferred generative results would make the replay claim unsound.

Keeping the current Dream behavior unchanged is cheaper and remains the baseline. Directly adding multiple default attempts would be simpler to implement, but would raise cost and Review pressure before demonstrating value. Optimizing `prepare_context` first would expose the online hot path to the wrong experimental risk. Using an LLM evaluator could provide richer rankings, but would introduce another non-deterministic policy, cost, and calibration problem before the record contract is trustworthy.

# Unresolved questions

1. Which canonical action vocabulary is the smallest useful one for the P2 `refine_experience` spike?
2. Which existing evaluation isolation and reporting components should the Dream adapter reuse, and which adapter fields must be fixed before the first fixture is recorded?
3. Which conditions should trigger an additional proposal attempt, and what budget is fair to the baseline while `max_model_calls <= 2` remains unchanged?
4. Should a controlled replay bundle store encrypted payloads, or only manifests/digests plus separately managed fixture content?
5. Which exact refs and retention windows are sufficient to join approved results to the recurrence ledger without coupling the stores?
6. What blinded Review protocol and minimum workload size are credible for the spike?
7. What usage coverage, missing-outcome rate, and environment stratification would justify starting P3b?

# References

- [Dream-RSI paper](https://arxiv.org/abs/2609.14858)
- [Chinese overview](https://mp.weixin.qq.com/s/VRjsoQLqHx80NZpeS5aqkA?scene=1)


The paper's reported improvements depend on its tasks, models, budgets, and evaluators. They are not expected PowerContext gains.
