- Proposal Name: `decision_policy_governance`
- Start Date: 2026-09-28
- RFC PR: [oceanbase/powercontext#1770](https://github.com/oceanbase/powercontext/pull/1770)
- Tracking Issue: [oceanbase/powercontext#1649](https://github.com/oceanbase/powercontext/issues/1649)
- Related RFCs: [RFC 0046](0046_observability_foundations.md), [RFC 0050](0050_artifact_candidate_review_inbox.md),
  [RFC 0080](0080_memory_search_reranking.md), [RFC 1223](1223_human_agent_work_continuity.md),
  [RFC 1560](1560_recall_sufficiency_gate.md), [RFC 1652](1652_memory_quality_and_lifecycle.md),
  [RFC 1745](1745-decision-model-rerank-seam.md)
- Related work: [#1643](https://github.com/oceanbase/powercontext/issues/1643),
  [#1644](https://github.com/oceanbase/powercontext/issues/1644),
  [#1645](https://github.com/oceanbase/powercontext/issues/1645),
  [#1647](https://github.com/oceanbase/powercontext/issues/1647),
  [#1648](https://github.com/oceanbase/powercontext/issues/1648),
  [#1739](https://github.com/oceanbase/powercontext/pull/1739),
  [#1740](https://github.com/oceanbase/powercontext/pull/1740),
  [#1742](https://github.com/oceanbase/powercontext/pull/1742),
  and [#1745](https://github.com/oceanbase/powercontext/pull/1745)

# Summary

This RFC defines a shared governance layer for narrow decision gates in PowerContext. A decision gate is a deterministic
Runtime consumer that asks one bounded question through the existing provider-neutral `DecisionModel` port, combines the
answer with local rules, and returns a typed assessment that a domain service may observe, flag, review, hold, or ignore.
The governance layer supplies policy manifests, shadow/advisory/enforcing modes, audit observations, replay/rescore
rules, privacy boundaries, and promotion criteria. It does not add a new model provider, expose the decision model as a
tool, change public HTTP/OpenAPI contracts, or claim that Jev, Laya, or any other backend improves PowerContext quality.
It establishes the contract that later Memory, Handoff, Experience, Skill, and safety-gate consumers must satisfy before
they can move from experiments to runtime behaviour.

# Motivation

PowerContext now has a provider-neutral decision role through #1739 and #1740. Several follow-up issues ask whether a
cheap decision backend can help with narrow judgements:

- #1643: Memory reranking;
- #1644: semantic relevance and evidence coverage;
- #1645: Handoff and Task Outcome evidence checks;
- #1647: Experience and Skill applicability;
- #1648: duplicate and conflicting Memory candidates.

Those consumers have different domain effects, but they share the same risks:

1. A model probability can be mistaken for authorization.
2. A provider outage can be hidden as a clean pass.
3. Hosted backends can receive material that a deployment did not intend to send.
4. Thresholds can be copied from another system even though the loss function is different.
5. A shadow experiment can be promoted without replay evidence.
6. A failed or abstaining decision can be recorded as if it had actually judged the content.

The project already has concrete pressure on this boundary. #1742 proposes a Memory write-time evidence gate. #1745
proposes a decision-model reranker and explicitly separates rerank fail-closed behaviour from advisory fail-open
behaviour. RFC 1652 reserves Memory consolidation, supersession, and lifecycle effects for review-gated flows. Without a
shared policy and observation contract, each consumer will reinvent its own threshold, fallback, privacy, and audit
rules.

The expected outcome is a small shared layer that lets implementation PRs proceed in a consistent order:

1. define a policy in shadow mode;
2. collect observations without changing domain behaviour;
3. replay and rescore the observations under candidate thresholds;
4. promote only the narrow action a policy has earned;
5. keep domain authority, review, persistence, and access control in the owning service.

This is an enablement RFC, not an adoption recommendation for a specific backend.

# Guide-level explanation

## What a decision policy is

A decision policy is a versioned description of one narrow judgement. It says:

- which deterministic Runtime consumer may call it;
- what bounded question is being asked;
- which subject and evidence fields may be sent;
- which local rules should decide before any model request;
- which backend failure policy applies;
- whether the policy is disabled, shadow-only, advisory, or allowed to affect the domain action;
- what to record for audit and replay;
- which promotion criteria must be met before changing behaviour.

A policy is not a prompt registry for agents. It is called by PowerContext code, not by the host model. A host model
never sees a `decision_evaluate` tool and never decides when to call the decision backend.

## What a narrow gate is

A narrow gate is a consumer of a decision policy. It receives a domain event, builds a policy-shaped request, and returns
a typed assessment.

Examples:

- a Memory write gate asks whether the cited evidence covers the candidate Memory claim;
- a Handoff consult asks whether a claim should be treated as requiring human verification;
- an Experience/Skill applicability gate asks whether an exact approved revision applies to the current task;
- a duplicate/conflict gate asks whether a candidate Memory entry appears equivalent, conflicting, complementary, or
  unknown relative to exact existing entries.

The gate may use local rules before the model. For example, a Memory write gate can accept content that is too short to
judge, decline to send content that contains sensitive material, or avoid repeated calls during a cooldown. A local rule
that actually judges the content is an adjudication. A local rule that only refuses to send content to a hosted backend
is not an adjudication; it is a fallback observation.

## Runtime modes

Every decision policy starts disabled or shadow-only.

`disabled`:
The consumer is absent. It makes no decision call and produces no observation.

`shadow`:
The consumer records what it would have decided, but the domain action is exactly the same as if the gate did not exist.
Shadow mode is the default for new policies that have not been measured on PowerContext data.

`advisory`:
The consumer may annotate the domain result, add a warning, or create a review suggestion. It still cannot block, approve,
merge, retire, or execute anything by itself.

`enforcing`:
The consumer may take one explicitly allowed domain action, such as holding a Memory write or failing a configured
rerank stage. Enforcing mode is available only when the owning domain contract defines that action, the policy declares
the exact effect, and the promotion criteria have been satisfied. Enforcing does not grant approval authority.

The default progression is:

```text
disabled -> shadow -> advisory -> enforcing
```

A policy may stop permanently at any mode. Some consumers should never become enforcing.

## What users and operators should expect

Decision assistance is opt-in. A normal PowerContext installation does not need Jev, Laya, or any other decision
provider.

When a hosted provider is used, the deployment must explicitly choose what content may leave the process. The policy
records that content boundary. If a gate cannot send enough material because of privacy rules, the observation is marked
as fallback or unknown. It must not be recorded as "checked and clean".

When a policy is in shadow mode, the user sees no behaviour change. Operators may inspect aggregate observations and run
replay jobs. Replay can answer questions such as:

- What would the write gate have held at threshold X?
- How many extra human reviews would advisory mode create?
- Which reasons account for most fallbacks?
- Did a candidate threshold produce any known false holds?

Promotion is based on replay and labeled evidence, not on provider claims.

## Boundaries that users should not expect

This RFC does not add a general decision workflow engine. It does not let a model approve Review candidates, accept a
Handoff, verify task completion, deactivate Memory, install a Skill, or select tools for an agent. Those remain domain
actions owned by existing PowerContext services and their review or authorization contracts.

This RFC also does not change RFC 1745. Decision reranking is a special non-advisory consumer whose failure policy is
fail-closed under RFC 0080. The shared policy layer may describe that role, but it must not turn reranking into a
fail-open advisory gate.

# Reference-level explanation

## Named concepts

### `DecisionPolicy`

A `DecisionPolicy` is a versioned, low-cardinality runtime policy. Its stable identity is independent of the backend
model version.

Required fields:

| Field | Meaning |
| --- | --- |
| `policy_id` | Stable identifier such as `memory.write.evidence_sufficiency.v1`. |
| `decision_kind` | Low-cardinality selector passed to `DecisionRequest.decision_kind`. |
| `version` | Policy schema/question version, separate from backend model version. |
| `consumer` | Owning consumer, such as `memory_write_gate`, `handoff_evidence_consult`, or `experience_applicability`. |
| `mode` | `disabled`, `shadow`, `advisory`, or `enforcing`. |
| `failure_policy` | `fail_open` or `fail_closed`, resolved per consumer role. |
| `privacy_boundary` | Whether subject/evidence content may be sent to local-only, hosted-redacted, or no external backend. |
| `local_rules` | Deterministic pre-rules and their reason codes. |
| `question` | The bounded question template. |
| `subject_selector` | The domain value judged by the question. |
| `evidence_selector` | Exact references or excerpts allowed as evidence. |
| `outcome_mapping` | How `yes`, `no`, `abstain`, fallback, and local-rule results map to a gate assessment. |
| `promotion_criteria` | Minimum evidence before advancing modes. |

Policy files or in-code policy constants must be reviewable. A policy cannot silently change the question text or
threshold while keeping the same `policy_id` and version.

### `DecisionAssessment`

A gate returns a domain-neutral assessment before the owning service maps it to a domain action.

```text
DecisionAssessment:
  policy_id
  policy_version
  mode
  coverage          # adjudicated | unadjudicated
  verdict           # allow | deny | review | unknown
  source            # local_rule | decision_model | none
  reason
  confidence
  used_fallback
  usage
  latency_ms
```

`coverage` is the first truth axis:

- `adjudicated`: a local rule or backend made a substantive judgement about the content.
- `unadjudicated`: the gate did not judge the content; any domain pass-through is fallback behaviour.

`used_fallback=true` implies `coverage=unadjudicated`. A policy must not record `adjudicated+allow` when the only thing
that happened was "content was too sensitive to send", "no backend is configured", "cooldown is active", or "the backend
failed". Those cases may pass through, but they are not evidence that the candidate is clean.

### `DecisionObservation`

A `DecisionObservation` is the audit/replay record for one gate evaluation. It is intentionally distinct from the
domain object. It may be emitted to logs, an evaluation artifact, or a future internal ledger; the first implementation
does not need a public HTTP or OpenAPI surface.

Required observation fields:

- operation identity and scope;
- consumer and policy identity;
- mode;
- sanitized subject/evidence references;
- redaction and privacy-boundary outcome;
- model provider id and backend model id when a backend was called;
- model/policy versions;
- assessment;
- final domain action actually taken;
- usage and latency;
- fallback reason, if any.

Raw subject/evidence text is not stored by default. Replay should prefer exact PowerContext references and re-resolve
them under the same authorization context. If a deployment chooses to retain raw snippets for offline evaluation, that
retention must be explicit and separately documented.

## Consumer classes

### Advisory consumers

Most gates are advisory by default. Advisory consumers use fail-open runtime semantics: backend failure becomes
`unknown` or pass-through, and the owning domain behaviour proceeds unchanged unless the policy has been explicitly
promoted to a stronger mode.

Examples:

- Handoff evidence consult;
- Experience/Skill applicability recommendation;
- duplicate/conflict observation before Memory lifecycle review;
- hosted-provider safety screens whose result can suggest review but cannot authorize.

Advisory consumers may annotate, warn, or create a pending review suggestion where the domain already has such a
concept. They must not approve, accept, execute, publish, deactivate, supersede, or merge.

### Enforcing consumers

An enforcing consumer can change behaviour only inside an explicit domain contract.

Allowed examples:

- A Memory write evidence gate may hold a write if the Memory service defines a visible, structured refusal and the
  policy has been promoted.
- A reranker may fail closed because RFC 0080 and RFC 1745 define reranking as non-advisory.

Disallowed examples:

- A duplicate/conflict gate cannot deactivate or supersede Memory by itself; RFC 1652 reserves those effects for review
  proposals and approved lifecycle operations.
- A Handoff consult cannot mark work complete or accept a Handoff.
- A Skill applicability gate cannot install, publish, or execute a Skill.

## Local rules and code-first decisions

Policies may run local rules before model calls. Local rules are valuable when code can answer a cheaper, more reliable
question:

- the candidate is empty or outside supported length bounds;
- the action is outside the gate's scope;
- a sensitive key was detected and the hosted privacy boundary forbids sending;
- the same content was already adjudicated under the same policy version;
- a deterministic citation or revision check already fails.

Local rules must return reason codes. They also must distinguish content judgements from transport/privacy decisions.

Examples:

| Local result | Coverage | Reason |
| --- | --- | --- |
| Candidate below minimum useful length and policy defines it as pass-through | `adjudicated` | `too_short` |
| Candidate contains a sensitive value and hosted sending is disabled | `unadjudicated` | `sensitive_not_sent` |
| No backend credentials | `unadjudicated` | `no_backend` |
| Policy cooldown active after provider failure | `unadjudicated` | `cooldown` |
| Citation reference cannot resolve | Domain error before gate | `invalid_evidence_reference` |

## Shadow, audit, and replay

Shadow mode is required before a policy can change production behaviour unless maintainers explicitly accept a narrower
fast path in the implementation PR.

Shadow mode requirements:

1. The final domain output is byte-for-byte or semantically identical to the no-gate path.
2. Every gate attempt emits a `DecisionObservation`.
3. Observations distinguish no-call, local-rule, successful backend, backend fallback, privacy fallback, and parse
   failure.
4. The policy can be replayed or rescored without re-running the whole workload when enough exact references remain
   resolvable.

Replay has two forms:

- **Rescore:** reuse stored backend outputs and apply a new threshold or outcome mapping.
- **Re-evaluate:** re-resolve exact references and call a backend under a new policy or model version.

Both forms must report their input coverage. A replay result that cannot resolve enough original material is not a
negative result for the policy; it is an incomplete replay.

## Promotion criteria

A policy must declare promotion criteria before moving beyond shadow.

Minimum criteria:

- a labeled calibration set separate from held-out evaluation;
- a held-out result covering English and Chinese cases when the consumer can see both;
- no known high-severity false action for the enforcing action being promoted;
- measured fallback rate by reason;
- measured added latency and request cost;
- a friction budget for human-review or hold outcomes;
- a rollback path to the prior mode.

The exact numeric threshold belongs to the policy and the domain loss function. A Memory write gate that tries not to
lose true observations and a duplicate/conflict gate that tries not to merge history have different acceptable errors.
Thresholds from Hermes, OpenClaw, Jev cookbooks, Laya reports, or any other prior art are hypotheses, not defaults.

## Privacy boundary

Every policy declares one of these content boundaries:

| Boundary | Meaning |
| --- | --- |
| `local_only` | Subject/evidence content may be sent only to an in-process or loopback backend. |
| `hosted_redacted` | Content may be sent to a hosted backend only after PowerContext redaction/sanitization. |
| `references_only` | Hosted calls may receive identifiers, metadata, or bounded non-secret labels, but not raw content. |
| `no_external_call` | The policy can use only local rules. |

The implementation must use PowerContext's existing sanitization primitives where applicable rather than inventing a
parallel redaction scheme. If sanitization removes material required to answer the question, the observation is
`unadjudicated`; it is not `allow`.

## Interaction with existing RFCs

### RFC 0080 and RFC 1745

Decision reranking is covered by RFC 1745. This RFC does not change its fail-closed stance. The shared policy layer can
record observations and policy metadata for a reranker, but it must preserve RFC 0080's startup validation and runtime
failure semantics.

### RFC 1560

The recall sufficiency gate is model-free and remains the default recall expansion policy. Decision policies may add
semantic observations around retrieval quality, but they do not replace the deterministic sufficiency gate unless a
future RFC explicitly changes that contract.

### RFC 1652

Memory quality and lifecycle effects remain review-oriented and evidence-preserving. A duplicate/conflict gate may
produce observations or proposals that feed RFC 1652 evaluation, but it cannot directly merge, deactivate, supersede, or
rewrite Memory.

### RFC 0050

Review Inbox remains the place for review-owned families and explicit review proposals. A gate may create a pending
Candidate only where the owning family already supports that flow, and the model cannot approve the Candidate it
creates.

## Compatibility, persistence, and API impact

This RFC adds no public HTTP endpoint, no OpenAPI field, no required database table, and no migration by itself.

Implementation PRs may add internal data structures, structured logs, evaluation artifacts, or optional private ledgers.
Any public diagnostic API, persisted audit table, or externally visible error contract must be proposed in that
implementation PR or a follow-up RFC.

Existing installations continue to work without a decision backend. Existing Memory, Handoff, Experience, Skill,
Review, and PreparedContext contracts are unchanged by accepting this RFC.

# Drawbacks

- The policy layer adds concepts before all consumers exist. That can feel heavy for the first gate.
- Shadow and replay delay visible product behaviour.
- Observation design can become a second telemetry system if it is not kept aligned with RFC 0046.
- Privacy-safe replay is harder than storing raw snippets, and some replay attempts will be incomplete.
- A shared layer can overfit to Jev-shaped yes/no decisions if reviewers do not keep provider neutrality explicit.

# Rationale and alternatives

## Why a shared policy layer

The consumers are narrow, but their risk controls are the same. A shared layer lets each implementation PR focus on its
domain question while reusing the same vocabulary for fallback, audit, shadow, replay, privacy, and promotion.

## Alternative: each consumer defines its own gate

This is faster for the first PR, but it creates incompatible meanings for "fallback", "unknown", "checked", "shadow",
and "promoted". It also makes cross-consumer evaluation impossible.

## Alternative: make every decision model call fail-open

This is safe for advisory consumers but wrong for non-advisory roles such as RFC 1745 reranking. Failure policy belongs
to the consumer role, not the backend.

## Alternative: use Review Inbox for every uncertain result

This would turn uncertainty into user friction. Some uncertainty should be counted and observed rather than sent to a
human. A policy must justify when `review` is the correct verdict.

## Alternative: store complete raw prompts for replay

This makes replay easy but violates the privacy boundary that motivated explicit hosted-provider opt-in. Exact
PowerContext references and optional deployment-controlled retention are a safer default.

# Prior art

PowerContext already has the building blocks:

- #1739 and #1740 add the provider-neutral DecisionModel role.
- RFC 1745 defines a decision-model rerank seam and the fail-closed distinction for reranking.
- RFC 1560 defines a model-free recall sufficiency gate with bounded expansion.
- RFC 1652 defines evidence-preserving Memory quality and lifecycle boundaries.
- RFC 0050 defines the Review Inbox and the rule that generated candidates cannot approve themselves.
- RFC 0046 defines observability foundations that the observation stream should reuse rather than bypass.

Adjacent systems provide cautionary examples. Hermes-style Jev plugins show the value of cheap code-invoked gates,
shadow modes, and local rules, but their thresholds and host-specific hooks are not PowerContext defaults. OpenClaw-style
decision roles show why decision models should be runtime collaborators rather than model-callable tools. Jev and Laya
are candidate backends or reference points, not policy owners.

# Unresolved questions

- What is the first concrete storage target for `DecisionObservation`: structured logs, evaluation artifacts, or a
  private internal ledger?
- Which subset of observations should be retained by default, and for how long?
- Should policy definitions live as Python constants, JSON/YAML resources, or both?
- Which numeric promotion criteria should #1742 use if it is revised to conform to this RFC?
- Do hosted-provider privacy boundaries need a user-facing configuration document before the first hosted backend is
  enabled?
- How should replay jobs report authorization changes when an exact reference was visible during shadow collection but
  is no longer visible during replay?

# Future possibilities

- A policy registry page in the dashboard showing active policies, modes, fallback rates, and last replay results.
- A CLI command that replays one policy against recent observations and prints candidate thresholds.
- A standard evaluation bundle for decision policies, shared by Jev, Laya, local rules, and future backends.
- A private decision-observation ledger with retention controls and export for offline evaluation.
- Policy-aware Review Inbox grouping, where a review item links back to the policy observation that suggested it.
- Local-only decision backends for air-gapped deployments, using the same policy and observation contract.
