- Proposal Name: `recurring_failure_repair`
- Start Date: 2026-09-10
- Status: Proposed
- RFC PR: [oceanbase/powercontext#1557](https://github.com/oceanbase/powercontext/pull/1557)
- Tracking Issue: [oceanbase/powercontext#1554](https://github.com/oceanbase/powercontext/issues/1554)
- Related RFCs: [Product Definition](0001_product_definition_and_vision.md), [Memory Layer Design](0014_memory_layer_design.md),
  [Context Pack](0028_context_pack.md), [Handoff Artifact](0048_handoff_artifact.md),
  [Artifact Candidate and Review Inbox](0050_artifact_candidate_review_inbox.md),
  [Experience and Skill Artifact Families](0051_experience_skill_artifact_families.md),
  [Scoped Statistics and Usage](0072_scoped_statistics_and_usage.md),
  [Memory Search Reranking](0080_memory_search_reranking.md),
  [End-to-End Evaluation Architecture](0081_end_to_end_evaluation_architecture.md),
  [Source Definition and Observation Model](1400_source_definition_and_observation_model.md),
  [Prepared Context Text Assembly](1489_prepared_context_text_assembly.md)

# Summary

An Experience answers "in what situation did an action produce an outcome, and what did we learn?". Nothing in
PowerContext answers the follow-up question: "the situation came back — did what we learned actually help?"

This RFC gives recurring failures a machine-matchable identity, an attribution that names which PowerContext layer a
repair must touch, and an outcome ledger that counts whether a published record was ever selected, ever recurred, and
ever actually worked — the last scored only on positive evidence, never on a task that stayed quiet. Four statements
summarize the design:

1. **Recurrence needs identity, and free text is not an identity.** An Experience gains an optional structured
   `failure` block whose signature is the match key. Without it, a re-worded description of the same failure is a new
   Experience, so recurrence is uncountable and a `lesson` cannot be falsified.
2. **Attribution routes a repair; it is not a causal claim.** A required `repair_surface` names the layer a fix must
   touch, so "the record is right but recall never fires it" becomes an expressible diagnosis instead of an invisible
   defect.
3. **Admission is evidence-gated; degradation is Review-gated.** There is no failure record without a cited failing
   observation, and no automatic retirement, decay, or importance score — the boundary [RFC 0051](0051_experience_skill_artifact_families.md)
   already recorded stays intact.
4. **The ledger is derived from evidence already on the write path, never from `prepare_context`.** Selection is
   reconstructed from Handoff citations; recurrence is decided while consolidating Task Outcome Sources. The read path
   stays read-only. A `selected` event therefore covers only observations with a complete Handoff/Task Outcome link;
   missing linkage is an evidence gap, not proof that recall did not happen.

# Motivation

## Recurrence is currently uncountable

`LLMExperienceCandidatePipeline.incubate` consolidates `task-outcome` Sources into Experience candidates and deduplicates
them by exact content equality plus source identity — `key = (candidate.proposal.model_dump_json(),
tuple((source.source_type, source.source_id) for source in selected))` in
`src/powercontext/builtin/artifacts/experience/incubation.py`. That `seen` set lives inside a single `incubate()` call,
and the call is bounded by `EXPERIENCE_INCUBATION_WINDOW_LIMIT = 32`. There is no comparison against candidates produced
in an earlier window, and none against already published revisions, so the same failure observed in two windows is two
Experiences — and a failure described in different words is two Experiences even inside one window. Two consequences
follow:

- **A lesson cannot be proven wrong.** `ReviewService` validates evidence, content, and revision consistency *before*
  publication. Nothing observes *after* publication whether the recorded knowledge did anything.
- **A recurring failure looks like progress.** The consolidation pipeline reads failures faithfully — its instructions
  already forbid turning failed, timed-out, or cancelled checks into success (`artifacts/experience/prompts.py`) — and
  then emits a positive Experience from each one. The fourth occurrence of the same defect produces a fourth
  well-formed lesson, which reads like accumulating knowledge.

## The missing concept is already named

[RFC 0014](0014_memory_layer_design.md) lists "validated pitfalls" among the content Memory should prefer, requires that
a durable entry "will change the judgment or action of a future agent", and stores `decision` and `constraint` as first
class kinds. It does not define what *validated* means. RFC 0014 also fixes the discipline this RFC must respect: "only
explicit revision evidence can revise an entry; deactivation still requires an explicit `forget()`".

Meanwhile [RFC 0051](0051_experience_skill_artifact_families.md) lists "retirement, ranking, and usage attribution for
Experience and Skill" as future work and states that the current Artifact contract "has no retirement semantics, so this
RFC adds no automatic retirement or time decay". Usage attribution is the missing half of this proposal.

## Concrete scenario

A coding agent fixes a flaky integration test three times over two weeks. Each time, a well-formed Experience is
proposed; each time a human approves it. At the end of the week the scope holds three near-identical Experiences, and
nothing distinguishes "this lesson worked" from "this lesson was never in context" from "this lesson was in context
three times and the failure happened anyway".

The three desired answers are: was the record ever selected, did the failure recur anyway, and — when it recurred — which
layer was actually broken. Today none of the three is representable. The flow below is the smallest case that separates
them.

## What this RFC is not

It is not the same proposal as [#1508](https://github.com/oceanbase/powercontext/issues/1508), which connects recurring
Task Outcomes to an existing Experience and gates Skill revisions under a paired comparison. #1508 consolidates; it has
no match key, so it cannot count recurrence, and no repair typing, so "the recall policy is broken" can only be written
as prose. It is also not the Dream workflows from [#1510](https://github.com/oceanbase/powercontext/pull/1510), which
decide *which artifact to propose* and assume the input concept already exists. This RFC supplies the negative-knowledge
type those two mechanisms can consolidate and route.

It also deliberately does **not** record `candidate_not_selected`. `prepare` remains entirely read-only, so a candidate
omitted because of a byte budget or ordering is not a negative outcome, and a later incomplete provenance chain is an
evidence gap. In particular, missing `selected` evidence must not be used to infer that `recall_policy` failed.

# Guide-level explanation

## The flow this RFC is written around

The case that motivated this proposal: an agent repeatedly edits `openapi/powercontext.yaml` and forgets to regenerate
the generated sources, so the checked-in code drifts from the contract.

1. The first two occurrences are ordinary Experience proposals, and nothing links them. With the `failure` block from
   this RFC, the second is recognized as a **recurrence** of the first: the signature matches, the ledger records
   `recurred`, and because a record already exists, no third lesson is written.
2. A later task touches the same file. The record is recalled into `prepare_context` and the agent is told to
   regenerate. If the resulting Handoff and Task Outcome preserve the required citations, that linked observation yields
   a `selected` event. It is reconstructed from the Handoff citation rather than instrumented on the read path (see
   *Ledger write path* below); a prepare with no later linkage remains unobserved.
3. The task reports a Task Outcome, and whether that outcome counts for anything is decided by evidence rather than by
   the agent saying so. The record's `verification` names the check "the generated sources are in sync with the
   contract":
   - the check **ran and passed** → `avoided`;
   - the check **ran and failed** → `recurred`; this is recurrence evidence, not a causal diagnosis. The Review route is
     determined by the record's already Review-confirmed `repair_surface`, not inferred from this one result;
   - the check **did not run** → `unknown`, and no event is written. A task that succeeded without ever exercising the
     check is not evidence that the record helped.
4. If the signature accumulates recurrences without ever reaching `avoided`, the revision is surfaced for review. When
   its `repair_surface` is `recall_policy`, the review question can include "why did recall fail in the observed linked
   cases". Missing `selected` evidence alone cannot establish that recall never fired, because an Handoff or Task Outcome
   may not have been recorded.

Every step uses a mechanism that already exists: Experience revisions, recall of an artifact into `prepare_context`,
Handoff citations, Task Outcome Sources with embedded `TaskCheck` results, and the Review Inbox. The new parts are the match key on the
record, the `repair_surface` enum, the `verification` binding, and the ledger.

## Three new concepts

**Failure signature.** An `ExperienceContent` may carry an optional `failure` block. Its `signature` has two parts: a
`recall_cue` (the situation in which this record should be recalled) and an optional `symptom` (the observable shape of
the failure). The cue is the match key: it is what makes "this happened before" a checkable statement. The rest of the
Experience keeps its existing shape — `situation` / `action` / `outcome` / `lesson` still carry the human-readable
judgment.

**Repair surface.** A required enum naming the layer that must change for the failure to stop:

| Value | The fix must change |
| --- | --- |
| `experience_content` | The current Experience revision's `situation` / `action` / `outcome` / `lesson` / `failure` content |
| `working_state` | Handoff `objective` / `state[]` / `next_action`, or which Task Outcome fields are recorded |
| `recall_policy` | Scope recall configuration, how the `prepare` query is constructed, or `assembly.sections` selection |
| `acceptance_check` | Handoff `disposition` / acceptance criteria, or a verification instruction attached to an Experience or Handoff |

The enum exists to route the repair. A record whose fix belongs in `recall_policy` should not produce another lesson —
it should produce a signal about retrieval. `repair_surface` is proposed by generation and confirmed at Review; it is not
inferred and then treated as fact.

**Outcome ledger.** Three evidence events per published Experience revision, derived from evidence rather than from
instrumenting the read path:

- `selected` — the revision was cited by a Handoff whose exact Receipt is referenced by a later Task Outcome;
- `recurred` — a later Task Outcome reported a failure matching this signature;
- `avoided` — a later Task Outcome showed the risky situation recurring *and* the check bound to the record passing.

`avoided` is evidence-gated, and it requires all four of the following. Selection into prepared context is not use, and
a Task Outcome that merely fails to mention the failure is not avoidance:

1. the revision was cited by a Handoff, and the Task Outcome's `handoff_receipt_ref` resolves to a Handoff Receipt for
   that exact Handoff;
2. `condition_ref` resolves to an `observations[]` item in that same Task Outcome with `basis="verified"` and non-empty
   exact evidence, proving that the trigger condition occurred **and whose normalized `WorkClaim.text` equals the
   record's `verification.condition`**; `check_ref` resolves to a `checks[]` item in that same Task Outcome with
   `basis="verified"`, non-empty exact evidence, **and normalized `TaskCheck.name` equal to
   `verification.check_subject`**, proving that the bound check **ran**. A declared check, a missing or non-matching
   item, or a check that did not run leaves the verdict `unknown`;
3. that check **passed**;
4. no `recurred` event was recorded for this signature under the same Task Outcome.

A completed Task Outcome on its own writes no event. Where the trigger condition cannot be shown to have occurred, or
where the bound check produced no result, the verdict stays **`unknown`** and no event is written — absence of evidence
is never scored as success, and an evidential gap must not be silently converted into a positive counter.

`unknown` is a *derived verdict, not a ledger event*. Writing a row for every unobserved case would fill an append-only
ledger with information-free rows and would require capture on paths that must stay write-free. It is computed only for
linked observations, as the gap between a revision's `selected` events and its linked events with a resolved verdict;
unlinked prepares are outside the denominator and must not be interpreted as non-selection.

`avoided` remains a proxy, and the RFC does not claim otherwise. A passing check shows the bound outcome was right; it does
not show that the record caused it. The condition and exact check item references limit unrelated tasks from being counted, but
they still cannot establish causation.

## How a contributor should think about it

Treat a failure record as an Experience that can be **falsified and routed**, not as a second kind of knowledge store.
If a record's evidence is weak, do not write it — an absent record is better than a wrong one. If a record keeps being
selected and the failure keeps happening, the answer is not to write another record: it is to check `repair_surface`,
because the failure may not live in the content at all.

Rejected-approach notes and API pitfalls remain ordinary Experience or Memory content. They are decision knowledge with
no recurrence to count. Only a *recurring* failure needs a match key and a ledger. Conflating the two — as the reference
implementation cited below does — forces every rejected approach to carry counters it will never use.

## Worked example

An agent hits `pytest` failing with a port already bound in a sandbox. Outcome status `failed`, check status `failed`.

1. Consolidation matches the failure against the signatures already present in the scope. It returns the cue of an
   existing Experience whose `repair_surface` is `experience_content`, and cites the failing check as its evidence.
2. The ledger records `recurred` for that revision, with the Task Outcome as provenance.
3. This is the record's third recurrence with no `avoided` in between, so it is marked as needing review.
4. Because the surface is `experience_content`, Review receives a revision candidate whose `reason` states the
   recurrence count and whose evidence is the same failing observation. A human decides whether to sharpen the record or
   change its `repair_surface`.

Had the surface been `recall_policy`, step 4 would not happen at all. The pipeline would record the recurrence, surface
it in statistics, and propose no artifact change, because the bug is in retrieval, not in the text.

This example exercises the `recurred` path. The OpenAPI flow above is what exercises `avoided` and `unknown`; between
them the two cases cover every event the ledger can hold.

## Minimum checkable evidence case

The following source graph is the smallest implementation and test fixture that makes the accounting boundary explicit:

1. Experience revision `E7` has a failure signature and a verification binding.
2. Handoff `H12` cites `E7`. Handoff Receipt Source `R12` has `status = "accepted"`, `selection = "exact"`,
   `selected_revision = H12`, and `evidence_status = "available"`. Each of the distinct Task Outcome Sources
   `O12-pass`, `O12-recurred`, and `O12-unknown` has `handoff_receipt_ref = R12`; each complete chain writes one
   `selected` observation for `E7`.
3. `E7.failure.verification.condition` normalizes exactly to `O12-pass.observations[0].text`, and
   `E7.failure.verification.check_subject` normalizes exactly to `O12-pass.checks[0].name`.
   `O12-pass.observations[0]` is a `basis="verified"` condition claim with exact evidence, and
   `O12-pass.checks[0]` is the bound `basis="verified"` TaskCheck with exact evidence and status `passed`.
   `condition_ref` and `check_ref` both carry `task_outcome_ref = O12-pass`; when their digests resolve, that linked
   observation writes `avoided`.
4. `O12-recurred.checks[0]` is a `basis="verified"` failed check with exact evidence, and its normalized `name` equals
   `E7.failure.signature.recall_cue`. Its one immutable `RecurrenceMatch` uses `candidate_set_mode = "handoff_citations"`,
   contains `E7` as the only eligible candidate, and records the exact target
   `(E7, normalized signature key)`. The match's `failure_ref` resolves to that check; its digest is the event's
   `recurrence_match_digest`. That linked observation writes `recurred`, not `avoided`; a replay resolves this match and
   cannot ask the generator to choose again.
5. In `O12-unknown`, the bound TaskCheck did not run, or it is only `basis="declared"`; no verdict event is written and
   the linked selection is counted as `unknown`. No `avoided` event can be written without both verified, same-Outcome
   item references.
6. When a prepare has no Handoff/Task Outcome chain, no `selected` observation is written and it stays outside the
   `unknown` denominator. A Handoff citation with no joinable Task Outcome is reported only as missing provenance
   coverage; a prepare with no Handoff emits no telemetry. Neither case can be read as a failed recall or a
   `candidate_not_selected` result.

7. After a Review publishes `E7` revision 2 with the same cue, a new unlinked failure window snapshots only revision 2
   under `candidate_set_mode = "scope_heads"`; the historical `E7` revision-1 match remains unchanged. A recurrence
   streak does not cross that revision boundary.

Implementations must also demonstrate that replaying any one of these source windows produces no duplicate match or event,
while two separate Handoff/Task Outcome chains for `E7` remain two observations.

# Reference-level explanation

## Data model

The optional block is added to the existing content model, not to a new Artifact family:

```python
class FailureSignature(_ExperienceValue):
    recall_cue: Annotated[str, Field(min_length=1, max_length=MAX_FAILURE_CUE_LENGTH)]
    symptom: ExperienceText | None = None

class FailureVerification(_ExperienceValue):
    condition: ExperienceText  # normalized exact binding to the verified WorkClaim.text
    check_subject: Annotated[str, Field(min_length=1, max_length=MAX_FAILURE_CUE_LENGTH)]
    # normalized exact binding to the verified TaskCheck.name

class FailureRecord(_ExperienceValue):
    signature: FailureSignature
    repair_surface: RepairSurface
    verification: FailureVerification
    @model_validator(mode="after")
    def reject_blank_cue(self) -> FailureRecord: ...

class ExperienceContent(_ExperienceValue):
    situation: ExperienceText
    action: ExperienceText
    outcome: ExperienceText
    lesson: ExperienceText
    failure: FailureRecord | None = None
```

`RepairSurface = Literal["experience_content", "working_state", "recall_policy", "acceptance_check"]`.
`MAX_FAILURE_CUE_LENGTH` is a proposed new constant (512) because a match key should not be 8000 characters; the exact
value is an implementation decision, not a design one.

`verification` is required *inside* `FailureRecord`, and it is what makes the ledger able to say anything beyond "this
failed again". `condition` is an exact normalized binding to the `WorkClaim.text` that proves the risky situation
occurred; `check_subject` is an exact normalized binding to the `TaskCheck.name` that evidences it. These are deliberately
not semantic matches: semantic matching could be proposed by a generator, but its result would need a separately
persisted and Reviewable assertion before it could write the ledger. A record without a check can only ever accumulate
`recurred` events, so requiring the field is what keeps `avoided` from degrading into "nothing was reported".

**Backwards compatibility.** Artifact content is persisted as JSON and re-validated through the registered content type
on load, so an optional field is load-compatible with every existing revision. No `schema_version` is introduced: the
Artifact families do not carry one today, and adding one for a single optional field would create a second versioning
scheme.

**Two deliberate implementation requirements.** `experience_search_text` currently returns "only user-authored fields so
renderer labels cannot cause matches" — the signature must be added to that projection explicitly, or the cue will not
participate in retrieval. `render_experience` feeds bounded context delivery, so the cue and symptom need a rendered
form; otherwise the record can be selected but never recognized by the agent reading it.

## Admission rules

A failure record is admitted only when all of the following hold. Rules 1 and 2 are the confidence floor: an
unverifiable record is dropped rather than stored.

1. **A cited failing observation.** The candidate must cite at least one Source whose content records a failure —
   a Task Outcome with status `failed` or `blocked`, or an embedded `TaskCheck` with status `failed`, `timed_out`, or
   `unavailable` in a cited Task Outcome. A recurrence event must additionally retain a `failure_ref` to the exact
   embedded observation or check that supports its signature. An observation is failure evidence only when its parent
   Task Outcome is `failed` or `blocked`, and the `WorkClaim` itself is `basis="verified"` with non-empty exact evidence.
   A check is failure evidence only when it is `basis="verified"`, has non-empty exact evidence, and has status
   `failed`, `timed_out`, or `unavailable`; `skipped`, `cancelled`, and `unknown` never write `recurred`. The existing
   Review invariant (at least one exact citation) is necessary but not sufficient, because the cited content must
   specifically evidence the failure.
2. **A single, self-contained cue.** The cue must name a recognisable situation, not a restatement of the outcome field.
3. **A `repair_surface`.** The record must state which layer a fix must touch.
4. **A check that can be run later.** The record must carry a `verification` whose `condition` and `check_subject` are
   normalized exact bindings for the future `WorkClaim.text` and `TaskCheck.name`. A record with no check can only ever
   be observed failing again, which is the state this RFC exists to get out of.
5. **No silent near-twin.** If the normalized cue is a near-duplicate of an existing record's cue, the proposal is
   returned with a warning that names the existing record, so the author can revise that record instead. The proposal is
   not rejected automatically.
6. **Provenance.** Reuse the existing Review evidence model unchanged; do not add a second evidence mechanism.

Review rejection remains auditable through the persisted Candidate status `rejected` and its `decision_reason`. A
near-duplicate is a candidate-generation or Review UI suggestion, not a recurrence-ledger event. Version 1 creates no
new immutable Source kind for either case; doing so would require a separately specified write point, access model, and
compatibility surface.

## Matching policy

Matching is the load-bearing mechanism, so it is specified conservatively.

- **Normalization.** Unicode NFKC, case folding, whitespace collapsing, and stripping of leading and trailing
  punctuation produce the comparison key. Normalization is a comparison aid, not a stored identity.
- **The exact matching input is the failure item itself.** For a `failure_ref` whose `item_kind` is `observation`,
  use the resolved `WorkClaim.text`; for `item_kind = "check"`, use the resolved `TaskCheck.name`. A candidate is
  eligible only when that normalized value equals the candidate revision's normalized `signature.recall_cue`.
  `TaskCheck.details`, the optional `symptom`, and surrounding Task Outcome prose are not match inputs. The eligible
  set is therefore computed deterministically before any generator call: zero candidates produces `unmatched`, one
  produces `matched`, and more than one produces `ambiguous`.
- **Freeze the candidate set before matching.** A complete Handoff/Task Outcome chain uses only the exact Experience
  revisions cited by that Handoff. Without that chain, the candidate set contains only the current head revision of each
  Experience Artifact in the scope, sorted by `ArtifactRef`; superseded revisions are excluded. The selected mode and
  complete ordered candidate refs are persisted, so a later revision cannot redirect a historical recurrence. A recurrence
  streak is always per exact revision and never transfers to a replacement revision.
- **Persist one replayable match decision.** Before any `recurred` event, consolidation writes one immutable
  `RecurrenceMatch` for the exact Task Outcome and `failure_ref`. It stores the outcome ref and journal position, the
  failure locator and digest, candidate-set mode and digest, every candidate ref, and either one exact target
  `(artifact_ref, signature_key)` or the terminal result `unmatched` / `ambiguous`. The target is selected by the
  deterministic eligibility rule above; a generator may provide explanatory text, but it must not choose or override
  the result. Record validation must reject a target absent from the frozen set or whose normalized key is not the
  target revision's `recall_cue`. Reprocessing first resolves this record; it must not call the generator again or make
  a new choice for the same `(task_outcome_ref, failure_ref)`.
- **Only a frozen exact target links; fuzzy similarity only suggests.** The target's normalized key is copied verbatim
  from its stored `recall_cue`; a token-bigram overlap at or above 0.8 produces a *suggestion* only, mirroring the
  reference implementation's threshold, and never writes a counter. Fuzzy matching must not silently increment a
  recurrence count, because a wrong link silently corrupts the signal the feature exists to produce.
- **Ambiguity resolves to no verdict.** If the frozen candidate set has two possible targets, the persisted decision is
  `ambiguous`; no ledger event is written and the conflict is surfaced.
- **The signature is not a global identity.** The identity of a record remains `(artifact_id, revision)`. The reference
  implementation keys its cards by a mutable content-derived id so that re-storing edits in place; that is incompatible
  with immutable revisions, and changing a record must stay an explicit revision.

## Ledger write path

The ledger is written only by the consolidation pipeline that already consumes `task-outcome` Sources. It is never
written from `prepare_context` or from search.

This is not a preference, it is what the existing contracts require. [RFC 0028](0028_context_pack.md) states that
Context Pack "writes no database or file, enters no Source journal or Memory evidence, starts no scheduler work, and is
not persisted as telemetry", and that normal logging "must not record scope, query, snippets, entry IDs, entry version
IDs, or response bodies". [RFC 1489](1489_prepared_context_text_assembly.md) likewise keeps model calls out of assembly.
Instrumenting selection on the read path would violate all three.

Selection is instead reconstructed from provenance that already exists:

```
TaskOutcome.handoff_receipt_ref  ->  HandoffReceipt Source
                                 ->  HandoffReceipt.selected_revision  ->  Handoff Revision
                                                                     ->  HandoffArtifactCitation[]  ->  Experience revisions in context
                                                                     ->  HandoffMemoryCitation[]
```

`HandoffResolution` already carries `selection`, `selected_revision`, `current_revision`, and `evidence_checks`, and
Handoff activation evidence is already bounded by `MAX_HANDOFF_CITATIONS`. The reconstruction is therefore a read of
existing data, not a new capture path. Its trust level is `untrusted_history`, and the ledger records that: a citation is
evidence that the agent's context named the record, not proof that the agent read or obeyed it.

The match decision precedes one ledger event per observation:

```python
class RecurrenceMatch(_ArtifactValue):
    scope_id: str
    task_outcome_ref: SourceRef
    task_outcome_position: int
    failure_ref: TaskOutcomeItemRef
    candidate_set_mode: Literal["handoff_citations", "scope_heads"]
    candidate_refs: tuple[ArtifactRef, ...]          # sorted, exact snapshot
    candidate_set_digest: str
    result: Literal["matched", "unmatched", "ambiguous"]
    artifact_ref: ArtifactRef | None = None           # required only for matched
    signature_key: str | None = None                  # required only for matched


class RecurrenceObservation(_ArtifactValue):
    observation_id: str                              # stable idempotency key for one source observation
    scope_id: str
    artifact_ref: ArtifactRef                      # the exact Experience revision
    signature_key: str                             # the normalized cue that matched
    event: Literal["selected", "recurred", "avoided"]
    match_basis: Literal["exact"]
    task_outcome_ref: SourceRef                     # required for every event; joins selected to its Handoff
    task_outcome_position: int                      # matching immutable Source journal position; canonical event order
    handoff_receipt_ref: SourceRef | None = None   # required for selected/avoided; resolves the exact Handoff
    handoff_ref: ArtifactRef | None = None         # how selection was derived
    condition_ref: TaskOutcomeItemRef | None = None  # required for avoided: observation proving the risky condition
    check_ref: TaskOutcomeItemRef | None = None      # required for avoided: check that ran and passed
    failure_ref: TaskOutcomeItemRef | None = None    # required for recurred: observation or check proving the failure
    recurrence_match_digest: str | None = None       # required for recurred: exact frozen RecurrenceMatch


class TaskOutcomeItemRef(_ArtifactValue):
    task_outcome_ref: SourceRef
    item_kind: Literal["observation", "check"]
    item_index: Annotated[int, Field(ge=0)]
    item_digest: str  # digest of the item's canonical serialized content
```

Record validation rejects any event that violates this matrix before it reaches persistence:

| Event | Required evidence | Rejected combination |
| --- | --- | --- |
| `selected` | `task_outcome_ref` and its exact positive `task_outcome_position`; an accepted/exact `handoff_receipt_ref`; and the matching `handoff_ref` that cites the revision | no receipt, a non-accepted/non-exact receipt, wrong journal position, a Handoff that does not cite the revision, or a second `selected` event for the same `(artifact_ref, signature_key, task_outcome_ref)` |
| `avoided` | all `selected` evidence; a verified `condition_ref` to an observation whose normalized `text` equals `verification.condition`; a verified passing `check_ref` to a check whose normalized `name` equals `verification.check_subject`; both locators on `task_outcome_ref` | declared, unreferenced, non-matching, or ambiguous items; a wrong item kind or Outcome; a non-passing check; or any terminal verdict already recorded for this signature and Outcome |
| `recurred` | `task_outcome_ref` and its exact positive `task_outcome_position`; a `recurrence_match_digest` for a `matched` `RecurrenceMatch`; and its unique `failure_ref`. The match's scope, Outcome, failure locator, target `artifact_ref`, and `signature_key` must equal the event's. An observation ref requires a verified claim with exact evidence and a parent Outcome status of `failed` or `blocked`; a check ref requires a verified check with exact evidence and status `failed`, `timed_out`, or `unavailable` | no matching decision, a decision with a wrong scope, Outcome, failure locator, frozen set, or target, a failed item outside those status rules, a wrong journal position, an ambiguous/unmatched decision, a locator whose digest does not resolve, or any terminal verdict already recorded for this signature and Outcome |

`RecurrenceMatch` and events are append-only and are committed in one transaction. The match key is unique for
`(scope_id, task_outcome_ref, failure_ref)`; its `failure_ref.task_outcome_ref` must equal `task_outcome_ref`, and its
candidate snapshot is canonicalized before its digest is calculated. A `matched` result requires both target fields;
`unmatched` and `ambiguous` require them to be absent.
`observation_id` is unique and is derived from the exact event evidence: the event type, referenced Task Outcome and its
immutable journal position, Handoff/Receipt, artifact revision, normalized signature key, the canonical match digest when
applicable, and every applicable item locator (`condition_ref`, `check_ref`, or `failure_ref`, including its digest).
Replaying the same Source window therefore reuses its recorded match decision and is idempotent while distinct observations
for one revision remain appendable. A **linked** source window
writes one `selected` event and at most one terminal verdict (`recurred` or `avoided`) for each
`(scope_id, artifact_ref, signature_key, task_outcome_ref)`. An unlinked Source window can write only one `recurred`
verdict when its unique `failure_ref` supports the match; it cannot write `avoided`. Multiple candidate evidence items
make a verdict ambiguous and leave it `unknown`, rather than allowing one Task Outcome to inflate a streak.
`(scope_id, artifact_ref, signature_key)` is an aggregation index, not a uniqueness constraint. Nothing is updated in
place, so the history of a record's yield is inspectable even after it is revised.

`TaskOutcomeItemRef` is a ledger-local locator, not an invented `TaskCheck` Source identity: it must resolve against the
immutable Task Outcome content at `item_index`, and `item_digest` must match that exact item's canonical serialized
content. The validation matrix above makes `condition_ref.item_kind == "observation"` and `check_ref.item_kind == "check"`
enforceable, requires their exact Outcome and verified evidence, requires their normalized content to equal the
`FailureVerification` bindings, and requires a passing check. `recurred` retains the unique failure item and its frozen
`RecurrenceMatch` through `failure_ref`; an observation requires a `failed`/`blocked` parent Outcome, while a check must
be verified, carry exact evidence, and be `failed`, `timed_out`, or `unavailable`. `selected` carries `handoff_ref` and the
Task Outcome that used that Handoff. `task_outcome_position` must equal the source journal entry resolved by
`task_outcome_ref`; it is the sole ordering key for verdicts and ties cannot occur in one scope. An observation with no
resolved verdict writes no row at all. A
revision's `unknown` count is therefore derived only over linked observations: its `selected` events with a recorded Task
Outcome, minus those that acquired a `recurred` or `avoided` verdict under the same Task Outcome. A missing Handoff or
Outcome is reported as missing provenance, not as a zero-use or recall-policy result.

## Degradation and the Review interaction

A revision is marked **needing review** when it accumulates a recurrence streak — proposed default 3 consecutive
terminal `recurred` verdicts with no intervening terminal `avoided` verdict — on the same revision. Verdicts are sorted
strictly by their immutable `task_outcome_position`; replay, delayed processing, and wall-clock time cannot alter the
streak. The consequence depends on `repair_surface`:

- `experience_content` — the pipeline proposes an Experience revision candidate through the existing `CandidateRepository`,
  with the recurrence count in `reason` and the failing observation as evidence. Approval produces a new immutable
  revision. This reuses the Dream pattern from #1510: candidates are generated automatically and human decisions are
  mandatory.
- `working_state`, `recall_policy`, `acceptance_check` — no artifact candidate is proposed. The recurrence is recorded
  and surfaced in statistics, because the repair is not a content change.
- **No automatic retirement, decay, importance score, or deactivation.** [RFC 0051](0051_experience_skill_artifact_families.md)
  forbids it ("this RFC adds no automatic retirement or time decay"), Artifacts have no `state` field at all, and the
  Experience family has no `active`/`inactive` concept — unlike Memory entries, which do. A low-yield record is made
  *visible*, not made *inactive*. Real retirement semantics need their own RFC.
- An `avoided` event clears the streak but changes no artifact state; it returns a record to normal recall, which is the
  only automatic transition this RFC introduces.
- A revision whose bound check never runs accumulates neither `recurred` nor `avoided`, so it never reaches the streak
  threshold. That is not silence: it surfaces as a growing `unknown` count, which is the signal that the `verification`
  binding is wrong — not that the record is fine.

## Read surface

`ScopeStatistics` ([RFC 0072](0072_scoped_statistics_and_usage.md)) gains a `recurrence` block: per-scope counts of
`selected` / `recurred` / `avoided` and of linked selections still `unknown`, plus the number of Experience revisions
needing review.
The block also reports Handoff citations that cannot be joined to a Task Outcome; those citations are provenance-coverage
gaps, not selected events. Missing linkage is an evidence-coverage signal, not a recall-policy diagnosis. Because the existing statistics layer has no
per-artifact usage view, the RFC proposes one bounded read: the top-N revisions by recurrence streak in a scope, returned
by the existing statistics operation. No new MCP tool is introduced.

```python
class RecurrenceStreak(BaseModel):
    artifact_ref: ArtifactRef
    signature_key: str
    terminal_recurred_streak: int  # non-negative; derived in task_outcome_position order


class RecurrenceStatistics(BaseModel):
    selected: int
    recurred: int
    avoided: int
    unknown: int                         # linked selections without a terminal verdict
    unlinked_handoff_citations: int      # provenance coverage only
    needing_review: int
    top_revisions: tuple[RecurrenceStreak, ...]  # deployment-bounded N
```

`ScopeStats.recurrence` is required in the public statistics response. `top_revisions` is sorted by descending
`terminal_recurred_streak`, then `(artifact_ref.family, artifact_ref.artifact_id, artifact_ref.revision, signature_key)`;
the API's deployment-bounded N is applied after that ordering. A multi-scope `ScopedStats` response exposes one such
block through each `by_scope` entry rather than merging independent scopes into a single streak.

## Compatibility and blast radius

| Surface | Impact |
| --- | --- |
| `openapi/powercontext.yaml` | `ExperienceProposal` gains one optional object. `ScopeStats` gains the required `RecurrenceStatistics` block, including bounded `RecurrenceStreak` rows; the existing statistics operation returns it through each `by_scope` entry. Update generated Python models and all generated clients, then run `make api-generate` and `make contract-test` |
| Persistence | No Artifact schema version; the ledger adds append-only `RecurrenceMatch` and `RecurrenceObservation` records with immutable `TaskOutcomeItemRef` locators. Their match/event uniqueness constraints and transactional write are part of the persistence migration |
| Task Outcome / Handoff | Existing public contracts stay unchanged: the ledger replays immutable Source content by accepted/exact receipt, index, digest, and existing verified evidence. If implementation instead introduces stable per-item IDs, that is an OpenAPI/model/generated-contract change and must be specified separately |
| Review | Unchanged contract. #1508-style consolidation continues to work; the recurrence candidate uses the existing `propose_experience` shape |
| Retrieval (`prepare`) | Read-only and unchanged. The cue is indexed because it becomes part of the content projection |
| Tags, authorization profiles, artifact resource discovery | Untouched, because no Artifact family is added |
| Evaluation | Adds an outcome category this feature can be measured by; [#1422](https://github.com/oceanbase/powercontext/issues/1422) is not implemented yet, so the metric is defined here in terms that do not depend on it |

# Drawbacks

- **The cue is model-authored free text, and a bad cue decays the whole mechanism.** A vague cue matches nothing, so
  recurrence silently undercounts. This failure mode is chosen deliberately: undercounting produces a quiet record,
  while overcounting produces a confident wrong signal. Neither is free.
- **The ledger adds persisted rows to a system that deliberately keeps its read path write-free.** Storage growth is
  proportional to consolidation events, not to requests, but it is real.
- **`avoided` rests on a binding that generation proposes.** The `verification` must be bound to the signature's
  trigger condition. A loose binding scores `avoided` on tasks where the risky situation never arose, which overcounts; a
  binding that never fires leaves the counter `unknown` indefinitely, which undercounts. Undercounting is preferred, on
  the same reasoning as the cue above — but it makes this counter only as good as the model-authored check it names, and
  it means some records can never be scored at all.
- **Review load increases.** A noisy consolidation pipeline can now flood the Review Inbox with recurrence candidates.
  The streak threshold is the only brake proposed here.
- **Mixing negative knowledge into `ExperienceContent` widens the family's shape.** RFC 0051 defines Experience as
  reusable judgment; a failure record is still judgment, but a reader who expects four prose fields now has to know when
  to read two more.

# Rationale and alternatives

**Placement: this lands in Experience, and a separate family is deferred.** The design above extends
`ExperienceContent` rather than adding a family, following the guidance on the tracking issue to use the existing
Experience and Task Outcome paths and to settle family separation once a concrete case works. Adding a family is the
larger change — repository tuples, the candidate repository, `BaseArtifactFamily`, tags, authorization profiles,
artifact resource discovery, the Review service's family branches, OpenAPI, the JS integration, docs, and tests — for a
record whose shape is Experience-shaped (situation, action, outcome, lesson) plus a match key.

Deferring is not the same as deciding, and the reason to revisit it is concrete: if the `failure` block turns out to be
carried by only a minority of Experience revisions, then Experience is the wrong container and the cost above becomes
the right price. The three pieces — signature, `repair_surface`, ledger — move together with no design change; placement
is the only open part, and it is open deliberately.

**Alternative: a reserved Memory `kind`.** Rejected. Memory entries already have `active`/`inactive` states and
`MemoryChangeOp`, and RFC 0014 gives them a different admission contract built around statements that change future
judgment. The reviewed unit for failure records should be the Experience revision, and the ledger keys on a revision.

**Alternative: adopt the reference implementation's card verbatim.** Rejected on two points that the immutable-revision
model makes non-negotiable: automatic deactivation after 5 non-avoided hits (contradicts RFC 0051 and the absence of
artifact state) and a mutable content-derived card id (contradicts immutable revisions). Its confidence floor — drop
rather than store — and its near-duplicate *suggestion* semantics are adopted.

**Alternative: do nothing until #1422 lands.** Reasonable sequencing, and the reason the tracking issue was filed first.
But the metric this feature needs must be requested from #1422, otherwise it will not exist when the loop is built.

**Impact of not doing this.** Recurrence stays uncountable, the recall-policy failure class stays unwritable, and no
record can ever be shown to have failed. Experience accumulates monotonically and unfalsifiably.

# Prior art

**Recuris** — Zhaochen Yu, Yingcheng Wu, Zhenfei Yin, Kaiyuan Chen, Zhe Zhao, Mengdi Wang, Shuicheng Yan, and Ling Yang,
*Recursive Experiential-Working Memory Evolution for Long-Horizon Agent Harnesses*, arXiv:2608.24876v1, 25 August 2026
([paper](https://arxiv.org/abs/2608.24876), [code](https://github.com/Gen-Verse/Recuris), Apache-2.0). It models the
harness memory-control layer as `M_k = (E_k, W_k, rho_k, C_k)` and defines that tuple as the **patch space**: a failure is
localized onto a component rather than merely summarized. Three elements are carried over: localization instead of
summarization; the framing of attribution as *"a repair decision rather than a claim of causal identification"*, which
matches PowerContext's evidence discipline; and validation-gated admission.

Its limits bound what can be copied. **It has no per-failure recurrence counter** — reuse and avoidance are measured
through aggregate proxies only (`Reach`, held-out success gains, dev-set regression rate). Admission also assumes a
held-out development set that a production recall loop does not have. The authors' reported gains (+17.8 points on
tau-bench for one model, up to 80% fewer long-horizon failures) are self-reported single-paper numbers and are not
reproduced here. This RFC therefore extends past the published baseline — the ledger is new work, not a reproduction.

**claw-mem v7.6.0** (`Error Pattern Card`) is a small Apache-2.0 community plugin that operationalizes the `E`-adjacent
diagnosis. Its card format is a useful starting point: a `{trigger, symptom}` signature, a
`skill-defect | state-defect | invocation-timing | transition-judgment` root-cause enum described in its source as
mapping onto "the layer the fix must touch", a minimum resolution length, near-duplicate trigger detection at 0.8
overlap that only suggests an edit, and per-card `hitCount` / `avoidedCount` / `lastHitAt`. Two components are adopted;
the deactivation rule and the mutable card id are not. Its published benchmark numbers are not treated as a baseline: its
README claims 100% on LoCoMo, ConvoMem, and LongMemEval simultaneously and a "subagent memory merge" that does not exist
in its source. Only the constants verifiable in its code are cited.

**PowerContext prior art.** [RFC 0028](0028_context_pack.md) permits aggregate selection counts as telemetry but forbids
per-entry logging; [RFC 0072](0072_scoped_statistics_and_usage.md) already persists recall measurements
(`preparations`, `baseline_tokens`, `recalled_tokens`, `token_reduction`) and per-kind memory counts, which is the
natural home for a recurrence block. [RFC 0081](0081_end_to_end_evaluation_architecture.md) defines the evaluation
architecture this metric should feed.

# Unresolved questions

1. **Placement.** This RFC lands the record in `ExperienceContent` and defers the question of a separate Artifact family
   until the flow above works on a concrete case, per the tracking issue. Revisit it if the `failure` block turns out to
   be carried by only a minority of Experience revisions. Everything else in the RFC is placement-independent.
2. **Where does `repair_surface` live** — in the artifact, or as a Review annotation that leaves the artifact content
   untouched? In the artifact it is durable and queryable; in Review it keeps the content immutable and the judgment
   auditable.
3. **Thresholds.** The recurrence streak that triggers Review, `MAX_FAILURE_CUE_LENGTH`, and the 0.8 near-duplicate
   overlap are proposed values, not measured ones. The streak threshold in particular interacts with how often
   consolidation runs per scope.
4. **Should a `recall_policy` recurrence feed a retrieval-evaluation loop** rather than a statistics view, and does that
   make `repair_surface` the routing key between two downstream loops?
5. **Ledger home.** A new append-only persistence record, or an extension of the statistics layer? RFC 0072's repository
   is already writable and scope-scoped, which argues for extension; per-artifact granularity argues for its own record.
6. **Naming.** RFC 0001 and RFC 0002 reserve `Trigger` as a product-level concept and leave its public contract
   undefined. This RFC deliberately avoids that word (`recall_cue`, `FailureSignature`); confirm the choice before the
   public schema is generated.
7. **Does `avoided` belong in #1422** as a generic per-artifact outcome signal rather than a feature-specific counter?

# Future possibilities

- **Cross-scope failure patterns.** A signature that recurs in many scopes is a candidate for promotion from personal to
  team assets, which is the flow RFC 0001 already describes.
- **Signature-driven retrieval.** Today `SearchMemoryRequest` filters by tag only, with no family or kind filter. A
  `recall_policy` diagnosis would be more actionable if recall could be evaluated against a signature directly.
- **Feeding Skill validation.** A record whose surface is `acceptance_check` is a natural source for the `validation`
  items a `SkillContent` already carries.
- **Handoff integration.** The most relevant signatures could be carried into `HandoffContent.state` or `omissions` so a
  successor agent inherits the failure to avoid, not only the work to continue.
- **Verified retirement.** Once this ledger exists, a future RFC can define retirement semantics on top of measured
  yield instead of guessing, which is the order RFC 0051 implies.
