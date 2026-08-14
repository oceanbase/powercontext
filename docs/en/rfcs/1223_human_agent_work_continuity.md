- Proposal Name: `human_agent_work_continuity`
- RFC Number: 1223
- Start Date: 2026-08-13
- Status: Draft
- RFC PR: [oceanbase/powercontext#1223](https://github.com/oceanbase/powercontext/pull/1223)
- Tracking Issue: [oceanbase/powercontext#1224](https://github.com/oceanbase/powercontext/issues/1224)
- Related RFCs: [RFC 0001](0001_product_definition_and_vision.md), [RFC 0048](0048_handoff_artifact.md),
  [RFC 0051](0051_experience_skill_artifact_families.md), and [RFC 0082](0082_handoff_report.md)

# Summary

This RFC defines PowerContext's work-continuity loop. It can be understood as an evidence-backed work relay: agree on
the work before it starts, describe where it stands at transfer time, let the receiver state whether they can safely
take it, and record what actually happened afterward. The same work can then move across human-to-human,
human-to-Agent, and Agent-to-Agent boundaries without relying on verbal follow-up or a copy of the full conversation.
A historical instruction to "continue" also does not grant new authority.

`Work` here means the work in progress, not a new persisted entity. The existing Workstream remains the stable work
line and `scope_id` remains its identity. This RFC adds four continuity record types around a Workstream and one
read-only projection. It does not add a `Work` table, Task system, or Workflow engine.

The loop has four user actions:

```text
Delegation
  -> Work Contract
  -> Execution by a human or Agent
  -> Handoff
  -> Continue + Acknowledge
  -> Task Outcome records what actually happened
  -> Complete, or enter the next Handoff
```

The first version does not create a workflow engine or database parallel to Handoff. Work Contract, Current Work
Handoff, Handoff Receipt, and Task Outcome are stored as typed `ContentSource` values. Temporary and persistent
handoffs continue to use RFC 0048 Prepared Handoffs, immutable Handoff Revisions, evidence checks, and Continue. Task
Outcome retains the existing `metadata.kind="task-outcome"`, so it can enter RFC 0051's existing Experience incubation
and Review path.

The first version adds four high-level operations:

- `create_work_contract` turns human intent into a checkable delegation baseline;
- `handoff_current_work` stores caller-inspected current state and prepares a Handoff;
- `acknowledge_handoff` resolves exact Handoff evidence again and records acceptance, clarification, or refusal;
- `record_task_outcome` records the result and check states at a real completion or interruption boundary.

Existing `commit_handoff` and `continue_handoff` behavior remains unchanged. Publishing a durable milestone is still
explicit. An acknowledgement never grants tool, network, credential, or execution authority.

# Motivation

PowerContext already provides a Handoff lifecycle for one Workstream and a project-level Handoff Report, but ordinary
users still need to understand internal steps such as `capture -> activate -> inspect -> finalize -> commit ->
continue`. More importantly, Handoff alone does not answer:

- how the objective, scope, and completion criteria are fixed when a human delegates new work to an Agent;
- whether the receiver actually obtained and understood a handoff, rather than merely receiving bytes;
- what happened after continuation and whether checks passed, failed, were skipped, or remain unknown;
- when an Agent should continue and when it should return a decision to a human;
- whether a successful or failed attempt can become reviewable Experience evidence.

The three participant relationships need different experiences without becoming incompatible models:

| Relationship | Primary question | Product experience |
| --- | --- | --- |
| Human -> Human | Can the receiver understand the state and assume responsibility? | Human-readable report, exact evidence, explicit acknowledgement |
| Human <-> Agent | Does the Agent understand intent while the human retains trade-off and authorization decisions? | Grounded Delegation, Task Outcome, decision return |
| Agent -> Agent | Can a new Agent continue safely without the original Session? | Canonical JSON, exact selection, evidence gate, capability and authority recheck |

All three share one Workstream, one Handoff content model, and the same exact evidence. Humans, Agents, and audit
systems consume different projections of that common core.

# Guide-level explanation

## Start with the intuition: an evidence-backed work relay

The five core concepts can first be understood in plain terms:

| Concept | Plain-language meaning | Question it answers |
| --- | --- | --- |
| Workstream / `scope_id` | One work line that can continue over time | Which piece of work are we continuing? |
| Work Contract | The baseline agreed before execution | What are we doing, excluding, and treating as complete? |
| Handoff | A transfer note | Where does the work stand, and where does the next participant start? |
| Handoff Receipt | A receiver acknowledgement | Does the receiver understand the transfer and have the conditions and authority to take it? |
| Task Outcome | The result of one attempt | What actually happened, which checks ran, and what remains? |

Their relationship is:

```text
One Workstream (identified by scope_id)
  ├── may begin with a Work Contract
  ├── produces a Handoff when work moves
  ├── receives a Receipt for one exact Handoff
  └── produces a Task Outcome at a completion or interruption boundary
```

This RFC therefore does not introduce a `Work` aggregate root or business table. The `Work` prefix in
`WorkContract`, `WorkClaim`, and `WorkContinuity` means that these records describe the work in progress. Workstream
remains the identity boundary, and Handoff Revision remains the durable transfer milestone.

## End-to-end example: delegate auto-refresh work to a Coding Agent

The following example walks one task through the full loop. Content is abbreviated for readability and does not
define the exact API payload format.

### Step 1: a human states the goal and the system forms a Work Contract

The user tells a Coding Agent:

> Add five-second auto-refresh to Handoff Report. Pause while the user has unsent changes and never overwrite the
> draft. Do not modify Handoff Core. Run the relevant tests and `make docs-test` when finished.

This is a new objective, so it begins as Delegation rather than Handoff. The integration inspects the repository,
existing Handoffs, and repository rules, then creates a Work Contract. Its objective is safe auto-refresh; its scope
includes frontend polling, draft protection, and relevant tests; its exclusions prohibit changing Handoff Core or
adding a general Scheduler; its completion criteria require exact test states and no overwritten draft; and its
authorization covers changes in the current repository but not production deployment.

`declared` means that a participant asserted a claim. `verified` requires same-scope exact evidence. A readable
reference proves only that the reference exists and has the expected identity; it does not prove that the fact is
still current. The Agent must still rely on the live workspace and current tool results.

The human is not asked to repeat facts that the integration can retrieve. For example, the Agent can read the
repository rules to find the test command. If five-second polling creates a material remote Runtime cost, however,
accepting that trade-off can change the result and must be returned to the human.

### Step 2: the Agent stops partway through and prepares a Handoff

Agent A implements polling and draft protection, but a Revision-switching test still fails and the current Session
cannot continue. It calls `handoff_current_work` and prepares a boundary like this:

```yaml
objective: Add safe auto-refresh to Handoff Report
current_state:
  - Automatic polling is implemented
  - Refresh pauses when there are unsent changes
  - The frozen-selection switching test still fails
disposition: continue
next_action: Fix the comparison between the frozen selection and the current Report Revision
omissions:
  - Remote CI is unavailable in the current environment
```

Every current-state item and the next action cite the newly stored boundary Source. Original exact citations for
verified claims are retained. There is only one `next_action`: Handoff tells the next participant where to start; it
does not create another task queue.

If Agent B will continue immediately in another Session, the integration can transfer the Prepared Handoff directly.
If the team needs a durable milestone, the user or integration explicitly calls `commit_handoff` to create an
immutable Handoff Revision. `handoff_current_work` does not commit automatically.

### Step 3: the receiver checks the transfer and records a Receipt

Agent B cannot modify code merely because the Handoff says to fix a test. It first calls Continue and checks:

- whether the cited evidence is still readable;
- whether the current checkout belongs to this Workstream;
- whether it has the capability to run and modify the relevant code;
- whether the current request grants the required file, tool, network, or other authority.

Agent B then records exactly one of three receipts:

| Receipt | Situation in this example |
| --- | --- |
| `accepted` | Evidence is readable, the workspace matches, and capability and repository-write authority are confirmed |
| `needs_clarification` | The cited failure log is unavailable, so the failing assertion cannot be identified |
| `declined` | The next action actually requires a production deployment that the receiver cannot or may not perform |

A Receipt means only that the transfer can or cannot be taken. It is not task completion. Even after `accepted`, the
loop still needs a Task Outcome or another Handoff.

### Step 4: why the Receipt must identify an exact Revision

Suppose Agent B inspected Handoff Revision 7. Before B accepts it, Agent A commits Revision 8 with a new instruction:
"Stop changing the code and wait for a rollback decision." If B could acknowledge `latest`, the system could record
that B accepted Revision 8 even though B never inspected it.

The receiver therefore resolves a Prepared Handoff or exact Revision with Continue and acknowledges that same exact
selection. When Report preflight finds that the displayed Revision is stale, the page requires a refresh instead of
allowing the user to accept a newer value that was never displayed or inspected.

### Step 5: record the actual result instead of inferring success

Agent B fixes the test, but `make docs-test` times out because of an environment problem. Its Task Outcome can be:

```yaml
status: partial
summary: Auto-refresh and draft protection are complete, but the full documentation build has no final result
handoff_receipt_ref: The accepted Receipt for Handoff Revision 7
checks:
  - name: focused pytest
    status: passed
  - name: make docs-test
    status: timed_out
  - name: remote CI
    status: unavailable
remaining_work:
  - Run make docs-test again when the documentation-build environment is healthy
```

The absence of a failure does not imply a pass: a check that did not run is `skipped`, a timeout is `timed_out`, an
inaccessible environment is `unavailable`, and an indeterminate result is `unknown`. Because this Outcome exactly
references the Receipt accepted by Agent B, the transfer outcome state can move from `awaiting_outcome` to `covered`.
`covered` means only that the result was recorded; a `failed` result can also be covered.

Task Outcome records what happened in this attempt. Whether "background refresh must preserve unsent drafts" should
become reusable Experience for future tasks still requires Candidate generation, Review, and approval. One successful
attempt does not enter PreparedContext automatically.

## Detailed rule: Delegation is not Handoff

A new objective that has not yet been started is Delegation, not Handoff. Codex or another integration inspects the
current repository, existing Handoffs, project constraints, and the user's input; PowerContext validates and stores
the resulting Work Contract:

```text
Human intent
  -> retrieve current facts
  -> ask only consequential goal, trade-off, or authorization questions
  -> Work Contract
  -> Agent execution
```

A Work Contract contains at least:

- `objective`: the result to achieve;
- `facts`: known facts, distinguished as `declared` or `verified`;
- `in_scope` and `exclusions`: the work boundary;
- `completion_criteria`: what completion means;
- `authorization_notes`: authority already granted or explicitly missing;
- `open_questions`: consequential questions that could still change the result.

Facts that can be retrieved from the current environment are not requested from the human again. Only questions that
change the objective, risk acceptance, or authorization should be asked. A Work Contract is `untrusted_input`; it
cannot override current system or developer instructions, repository rules, or a later user request.

## Human to human

The sender calls `handoff_current_work` with the checked objective, current state, work disposition, one next action,
and known gaps. PowerContext stores the complete boundary as a Source, cites it directly from every Handoff statement,
and returns a Prepared Handoff.

For an ordinary temporary transfer, the Prepared Handoff is passed directly. For a team milestone, the sender
explicitly calls `commit_handoff`. The receiver selects a Workstream in Handoff Report, reads the human Markdown view,
and calls Continue with that same exact selection.

The receiver then records one acknowledgement:

- `accepted`: the handoff is understood, every citation is readable, and live workspace, capability, and authorization
  have each been confirmed;
- `needs_clarification`: facts, evidence, or a necessary decision are missing;
- `declined`: scope, capability, or authorization does not match.

The receipt records the receiver's observation, not task completion. A later Task Outcome or another Handoff closes
the loop.

For example, before taking leave, one developer submits a Handoff saying: "The memory growth after bulk import is
reproduced, the fix branch has an initial change, and the next action is a 100,000-row validation. Production logs are
omitted because I cannot access them." The receiver can accept it or choose `needs_clarification` because reproduction
data is missing. Sending a document does not prove that responsibility transferred; the Receipt records that
difference.

## Human and Agent

The shortest Human-to-Agent path is:

```text
create_work_contract
  -> Agent executes under current instructions
  -> record_task_outcome
  -> human review
  -> complete or handoff_current_work
```

An Agent cannot treat an ordinary prompt, SessionEnd, or Stop event as proof of completion. Only a completion-aware
integration that can distinguish succeeded, partial, blocked, failed, cancelled, or unknown boundaries should call
`record_task_outcome`.

When the Agent needs a human value judgement or new authority, it prepares a `blocked` Handoff containing the question,
options, impact, and evidence. It does not guess the answer or treat `authorization_notes` as an authority token.

## Agent to Agent

The sending Agent calls `handoff_current_work` and obtains a canonical Prepared Handoff. The host transports that
structure unchanged through MCP, A2A, or provider metadata. The receiver does not parse human Markdown or depend on a
copy of the full Session transcript.

The receiving flow is:

```text
receive an exact Prepared Handoff or Revision
  -> continue_handoff
  -> inspect trust and evidence checks
  -> compare the current request and live workspace
  -> check capabilities and authorization
  -> acknowledge_handoff
  -> execute one applicable next action
  -> record_task_outcome or create the next Handoff
```

`acknowledge_handoff(status="accepted")` resolves the same prepared or exact selection again. It does not accept
`latest`: the receiver first resolves and inspects with Continue, then acknowledges the inspected exact Revision. The
Server rejects `accepted` when any statement or next-action evidence is unavailable or when live workspace,
capability, and authorization are not all `confirmed`. These confirmations remain `untrusted_observation`; they are
not authentication or an ACL.

## One-screen Handoff workspace

Handoff Report provides one workspace for each Workstream instead of splitting transfer across a wizard:

- the left side answers **What am I handing off?** by editing and sending the objective, state, one next action, and
  missing material;
- the right side answers **Can I take it?** with exact-evidence preflight and live workspace, capability, and
  authorization checks;
- the footer answers **What happened afterward?** with Contract, Handoff, Receipt, and Outcome in stable order.

The detailed behavior is:

- the sender card is prefilled from the current committed Handoff, and its objective, state, disposition, next action,
  and omissions remain editable until sending;
- **Send Handoff** is the explicit persistence action: it calls `handoff_current_work` to capture the inspected
  boundary and then `commit_handoff` to publish an immutable Revision. Browser-only editing writes neither a Source
  nor a Handoff;
- the page retains a stable `source_id` and Prepared Handoff for one send attempt. If preparation succeeds but commit
  is not confirmed, it exposes that partial success and retries the same Prepared Handoff without creating another
  boundary Source;
- the receiver side runs Continue automatically against the latest committed Handoff and its exact evidence. The
  returned exact Revision must match the card currently shown by Report; otherwise the receiver refreshes before
  making a choice and never acknowledges an unseen newer value;
- the receiver has exactly three choices: `accepted`, `needs_clarification`, and `declined`. Unavailable evidence
  disables acceptance; acceptance also requires all three receiver checks, while clarification and decline require a
  reason;
- automatic preflight presents citation readability separately from the receiver's live checks and never uses a green
  Evidence state to imply that a claim is currently verified;
- after acceptance, an uncovered result exposes a Task Outcome form linked to that exact accepted Receipt.
- while authenticated and visible, the page reloads the same Project every five seconds. It pauses while the card has
  unsent edits or a Handoff action is running. Background refresh neither disables page controls nor overwrites an
  unsent draft in the current or another selected Workstream;
- the workspace shows the current Workstream's Handoff Revision trail. Report returns the Revision count through its
  frozen selection and the latest 20 bounded summaries. The page displays them latest-first and identifies the current
  exact Revision. A newer concurrent Revision does not leak into an older report;
- Codex can bind the current Git workspace once to that Workstream's `scope_id`. Git-private state takes precedence in
  later sessions, so a one-line Handoff creates the next Revision in the same Artifact lifecycle. Explicit configuration
  still wins, and the integration never chooses silently between multiple candidates.

The workspace footer projects Delegation, Handoff, Receipt, and Task Outcome in Source journal position order.
Positions express stable sequence and are not presented as timestamps. The projection reports transfer state as
`not_applicable/awaiting_receipt/needs_clarification/declined/accepted` and outcome state as
`not_expected/awaiting_outcome/covered`. Outcome becomes `covered` only when its `handoff_receipt_ref` exactly identifies
the active accepted Receipt. A later unlinked Outcome in the same scope does not close the transfer.

## Task Outcome

A Task Outcome records what happened during one attempt. It is not a reusable conclusion by itself:

| Field | Meaning |
| --- | --- |
| `objective` | Objective for this attempt |
| `status` | `succeeded/partial/blocked/failed/cancelled/unknown` |
| `summary` | Bounded result summary |
| `handoff_receipt_ref` | Optional exact SourceRef for the accepted committed Handoff Receipt this result covers |
| `observations` | Observations distinguished as declared or verified |
| `checks` | Check name, exact state, basis, and evidence |
| `produced_artifacts` | Exact Artifact Revisions produced |
| `remaining_work` | Work that remains incomplete |

Check status is one of `passed/failed/skipped/timed_out/unavailable/cancelled/unknown`. The absence of a failure does
not imply a pass, and a producer's pass claim is not upgraded to verified automatically. Task Outcome is stored as a
Source. An Experience still requires a generated Candidate, Review, and approval before entering PreparedContext.

The easiest boundaries to confuse are: `accepted` is not completion, `covered` is not success, and Task Outcome is not
reusable Experience. They mean, respectively, that the receiver confirmed it can take the work, that the transfer has
an exactly linked result record, and that the record describes what happened in one attempt.

# Scope

The first version includes:

- versioned models for Work Contract, Current Work Handoff, Handoff Receipt, and Task Outcome;
- four HTTP, Python Client, and MCP operations;
- same-scope exact evidence validation;
- deterministic conversion from current work to a Prepared Handoff;
- evidence-gated acknowledgement;
- compatibility between Task Outcome and existing Experience incubation;
- a low-intrusion Codex `project-context` skill flow.
- an editable one-screen Handoff Report card, automatic receiver preflight, three receiver choices, and a read-only
  continuity timeline.
- Handoff Report auto-refresh and Revision history, plus a Git-private Codex workspace binding to a Workstream scope.

The first version does not include:

- a general Task, Workflow, Queue, Scheduler, or Agent orchestrator;
- Session, Agent, model, Git branch, or Issue as Workstream identity;
- automatic commit of every Handoff;
- mandatory Outcome or Handoff creation from SessionEnd or Stop;
- storage of complete prompts, transcripts, tool stdout or stderr, or credentials;
- automatic authorization or execution of a historical next action;
- automatic Project or Handoff-history replication across Runtimes;
- new per-scope ACLs or cross-trust-domain authorization.

# Reference-level explanation

## Identity and records

The stable Workstream identity remains `scope_id`. Agent, Session, and human labels are untrusted attribution only;
they do not become work identity, an ACL, or a compare-and-swap key.

The records map to existing Content Sources:

| Record | `metadata.kind` | Persistent role |
| --- | --- | --- |
| Work Contract | `work-contract` | Objective and boundary baseline at delegation |
| Current Work Handoff | `handoff-boundary` | Direct Source evidence for a Prepared Handoff |
| Handoff Receipt | `handoff-receipt` | Receiver observation about an exact selection |
| Task Outcome | `task-outcome` | Completion-aware evidence for one attempt |

Each Source uses the caller's stable `source_id` and existing Source conflict semantics. `WorkSourceReceipt` returns a
SourceRef, journal position, and canonical content digest. The feature adds no business table and does not change the
Artifact or Handoff persistence schema.

## Claim evidence

`WorkClaim.basis` has two values:

- `declared`: asserted by the producer, with no exact evidence;
- `verified`: supported by at least one same-scope exact Handoff Citation.

A caller cannot attach evidence to a declared claim while still presenting it as declared, or label a claim verified
without evidence. The Runtime validates exact citations before storing a verified claim, verified check, or produced
Artifact. Validation proves that the reference is readable and has the expected identity; it does not prove freshness.

## Operation contract

| operationId | Path | Behavior |
| --- | --- | --- |
| `create_work_contract` | `POST /v1/work/contracts/create` | Validate exact evidence and store a Work Contract Source |
| `handoff_current_work` | `POST /v1/work/handoffs/prepare-current` | Store a boundary Source and deterministically finalize a Prepared Handoff |
| `acknowledge_handoff` | `POST /v1/work/handoffs/acknowledge` | Continue an exact selection, check evidence, and store a Receipt Source |
| `record_task_outcome` | `POST /v1/work/outcomes/record` | Store a completion-aware Task Outcome Source |

`handoff_current_work` does not call a generation model. Every state item and next action cites the boundary Source;
original exact evidence from verified claims is retained as additional citations. The operation does not commit.
`commit_handoff` remains the only operation that publishes a durable milestone.

`acknowledge_handoff` accepts only `prepared/exact`, never `latest`. A prepared target is identified by canonical
digest; an exact target stores the resolved Handoff Revision. The receipt records evidence availability, unavailable
citations, and the receiver's live-state, capability, and authorization checks. `accepted` requires readable Evidence
and `confirmed` for all three checks.

A Prepared Receipt can show that a temporary carrier was observed, but in the first version only a Receipt for a
committed exact Handoff can be referenced by Task Outcome and participate in result coverage.

## Continuity projection

`WorkContinuity` is a dynamic read-only projection from the same scope's Source journal and creates no business table.
It parses only the four Work record kinds with a valid versioned schema. Ordinary Content Sources are ignored. A
Source that declares a Work kind but fails the corresponding schema increments `invalid_record_count` and is excluded
from valid history.

The projection returns complete record counts and at most the latest 64 timeline events. When that display limit is
exceeded, `truncated=true`, while `coverage` still uses every valid Work record read for the projection. The last
Receipt for the current exact Handoff determines transfer state. Only an accepted Receipt enters `awaiting_outcome`,
and only a Task Outcome whose `handoff_receipt_ref` identifies that Receipt changes the state to `covered`. Another
Revision, an earlier Receipt, or an unlinked Outcome cannot cover the current selection. Outcome status does not
change whether an exact result record exists.

## Consistency and failure

- Source capture retains stable `source_id` idempotency and conflict behavior;
- the one-screen workspace retains the same `source_id` and Prepared Handoff while retrying one send attempt;
- a Prepared Handoff `base` remains the committed head observed during finalization;
- Handoff commit retains RFC 0048 compare-and-swap behavior;
- acknowledgement runs Continue again and cannot reuse a caller-forged evidence result;
- storing a Work record does not claim that scheduling, Experience generation, Review, or execution occurred;
- a failure does not roll back an earlier explicit Source capture, whose receipt identifies the completed boundary.

## Trust and authorization

Work Contract is `untrusted_input`; Current Work Handoff is `untrusted_input`; Handoff Resolution is
`untrusted_history`; Receipt and Task Outcome are `untrusted_observation`.

They cannot override:

- system or developer instructions and the current user request;
- repository rules such as AGENTS.md;
- the current workspace and live tool results;
- the host's tool, network, secret, and write authorization;
- access policy outside the Project or scope.

`scope_id`, Project membership, MCP tool visibility, receiver labels, and authorization notes are not ACLs.

## Integration rules

Integrations call these operations only at explicit work boundaries:

- create a Work Contract when a new delegation needs a stable baseline;
- prepare a Handoff when the user requests transfer or work must move;
- acknowledge after the receiver checks live state, capability, and authorization;
- record a Task Outcome only when the integration can identify real completion or interruption semantics.

A hook may capture lightweight evidence quickly and fail open, but SessionEnd or Stop cannot be the sole completion
signal. Session ID may be attribution metadata, never Work or Handoff identity.

# Success metrics

Initial dogfood measures:

- `continuation_success_rate`: proportion of transfers whose first correct action needs no full-context restatement;
- `time_to_first_verified_action`: time from receipt to the first verified action;
- `clarification_rate`: proportion returned because facts or evidence are missing;
- `evidence_availability_rate`: proportion of Handoff claims still resolvable at receipt time;
- `handoff_result_coverage_rate`: proportion with an accepted exact Receipt and an Outcome that references it;
- `unauthorized_action_count`: actions executed only because historical Handoff text implied authority; the target is zero.

Metrics cannot treat `accepted` as completion or a producer-declared `succeeded` value as independent verification.

# Acceptance

| Scenario | Pass condition |
| --- | --- |
| Human -> Agent | Work Contract preserves objective, scope, completion criteria, and authority notes without overriding current instructions |
| Human -> Human | A committed Handoff is readable from Report and produces an acknowledgement for its exact Revision |
| Agent -> Agent | A Prepared Handoff is transported unchanged and can Continue without the original Session |
| High-level handoff | One operation stores the boundary Source and returns an uncommitted Prepared Handoff |
| Evidence gate | Unavailable Handoff evidence prevents an `accepted` receipt |
| Clarification | Unavailable evidence can produce `needs_clarification` with a reason |
| Editable card | A locale change or report refresh in the current tab for the same Handoff Revision does not overwrite unsent edits |
| Automatic refresh | The page reloads the same Project every five seconds, pauses for unsent edits or active actions, and never locks controls for a background refresh |
| Revision history | Report returns the total Revisions through the frozen selection and the latest 20 summaries, with the current exact Revision identified |
| Workstream binding | After a Git workspace is bound once to a fixed `scope_id`, later Codex sessions and one-line Handoffs continue the same Artifact lifecycle |
| Retryable send | When prepare succeeds but commit is unconfirmed, retry reuses the same `source_id` and Prepared Handoff |
| Exact preflight | Automatic preflight resolves latest; mismatch with the Report card blocks all choices, otherwise they bind exact |
| Three choices | The receiver sees all three actions; accepted requires readable Evidence and all receiver checks, while the other two require a reason |
| Continuity | Report shows all four Work record kinds in journal position order without presenting position as time |
| Result coverage | Acceptance enters awaiting_outcome; only an Outcome that references that Receipt changes it to covered |
| Outcome status | `failed/skipped/timed_out/unavailable/cancelled/unknown` never upgrades to passed |
| Experience boundary | Only `task-outcome` Sources enter existing incubation, and the result remains a pending Candidate |
| Persistence | Work records reuse the Source journal; Handoff retains immutable Revisions and compare-and-swap |
| Compatibility | Historical Work records remain readable; new accepted requests use prepared/exact and include receiver checks |
| MCP | Four high-level operations are available to Agents, but tool visibility grants no execution authority |
| Codex | The skill no longer requires the user or Agent to assemble capture/activate/finalize manually |

# Rollout

1. Enable the four operations for SQLite and Codex dogfood and validate one complete Workstream loop.
2. Connect a completion-aware Task Outcome producer at a stable public integration boundary.
3. Dogfood sending, exact preflight, all three receipts, and Outcome coverage through the one-screen Handoff Report
   workspace without changing Handoff Core.
4. Collect success metrics on real multi-human and multi-Agent work before supporting other Agent providers.

# Drawbacks

- Four Work record types add product vocabulary and must be taught through high-level actions, not field lists.
- Acknowledgement validates PowerContext evidence only; availability does not prove current live state.
- Source-backed records have no dedicated query index, so the first version consumes them through exact evidence,
  Handoff, and a later Report projection.
- A completion-aware integration must understand real host task boundaries and cannot rely only on a generic Stop hook.

# Unresolved questions

- When should Handoff Report add receiver or status filters without turning the primary workspace into an audit console?
- Can different Agent providers expose stable completion signals without reading private Session databases?
- What signing, revocation, and authorization contract is needed for cross-Runtime Prepared Handoff transport?
- When should Work Contract become a separately queryable projection instead of Source-backed evidence?
