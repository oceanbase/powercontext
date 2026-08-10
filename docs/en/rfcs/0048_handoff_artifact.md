- Proposal Name: `handoff_artifact`
- RFC Number: 0048
- Start Date: 2026-07-29
- Status: Draft
- RFC PR: [oceanbase/powercontext#48](https://github.com/oceanbase/powercontext/pull/48)
- Tracking Issue: Not assigned
- Related RFCs: [RFC 0002](0002_core_sdk_product_model.md), [RFC 0014](0014_memory_layer_design.md),
  [RFC 0019](0019_local_source_memory_runtime.md), and [RFC 0028](0028_context_pack.md)

# Summary

A Handoff is the work description that one participant gives to the next. Continue is the action that uses it.

Every Handoff answers the same questions:

- What is the objective?
- What is the current state?
- Can the work continue, is it complete, or is it blocked?
- What should happen next, if anything?

This RFC defines two forms of Handoff. A Prepared Handoff is a temporary value passed directly between sessions. A
committed Handoff is an immutable Artifact Revision kept as a project milestone. They use the same content and evidence
contract, but only the committed form has durable identity and history.

Continue can use an explicit Handoff or the latest committed Handoff in the current scope. It checks the cited evidence
before presenting the content as untrusted history. Current instructions, the current request, repository rules, and
live state take precedence.

RFC 0002 already defines Handoff as an Artifact Family. This RFC completes its product semantics. It is a design
contract, not a statement that every described action is already available in the current Runtime.

# Motivation

Most session boundaries need a direct transfer to the next participant. They do not need another permanent project
record. Persisting every transfer would make milestone history hard to read and would make storage a requirement for
the Handoff itself.

Some Handoffs do need to last. Teams use them to mark progress, recover work, and review how a project changed. These
milestones must be immutable, discoverable, and safe when more than one participant can update the same scope.

This RFC defines two paths:

```text
Prepare -> Inspect -> Transfer -> Continue

Prepare -> Inspect -> Commit -> Review or Continue
```

The first path is temporary. The second adds the same content to durable history.

This RFC does not define tasks, parallel work, automatic milestone selection, or transport APIs. It defines the Handoff
content, the difference between temporary and committed forms, and the behavior users can rely on.

# Guide-level explanation

## Prepare a Handoff

At a work boundary, PowerContext prepares a draft with the following content:

| Field | Purpose |
| --- | --- |
| Objective | The caller's intended outcome |
| State | The facts needed to understand the current position |
| Disposition | Whether the work can continue, is complete, or is blocked |
| Next action | One action that can start next, or no action |
| Evidence | Exact references that support state and next action |
| Omissions | Relevant material that could not be included or verified |

The caller owns the objective. Generation may draft the remaining content, but it cannot rewrite the objective or
invent evidence.

The user or host can inspect and correct the draft. Finalization validates the content and its references, then
produces a Prepared Handoff whose content no longer changes. The human and Agent views come from that same content.

Preparation reads a bounded set of candidate Sources, Artifacts, and Memory. Every state statement and next action cites
original evidence. If PowerContext knows that relevant material was excluded or could not be checked, it records an
omission. Omissions describe known gaps; they do not claim that the Handoff is complete.

State describes facts believed current when the Handoff is finalized. Disposition says whether the work described by
the Handoff can continue, is complete, or is blocked. It is a milestone claim, not the host's active objective or
execution status. An absent next action means only that the Handoff proposes no further action; disposition
distinguishes completion, blockage, and the absence of a proposed follow-up.

## Transfer work to another session

A Prepared Handoff can be passed directly to another session:

```text
Prepared Handoff -> explicit transfer -> Continue
```

It has no durable Artifact identity and does not enter milestone history. The transport does not change Handoff
semantics.

An explicit transfer must deliver the same finalized content before the recipient starts planning. Copying or
restoring conversation history, or inheriting Memory, does not replace this step because none guarantees that the
Handoff content survives.

The recipient must have access to the Handoff's scope and evidence. A Handoff from another scope may be inspected as
data, but it does not become the current scope's Handoff by itself. Continue requires the Handoff's originating scope;
cross-scope import is a separate operation outside this RFC.

## Commit a milestone

The caller may commit a Prepared Handoff as a durable milestone:

```text
Prepared Handoff -> Commit -> Handoff Revision
```

Commit keeps the content the user inspected and adds an immutable Revision to the scope's Handoff history. A scope has
one linear Handoff history in the first version.

Commit is explicit. Generation may help prepare a Handoff after the caller requests it, but it cannot decide that the
Handoff is a milestone.

If the content matches the current milestone, commit creates no empty Revision. If another writer has advanced the
history, commit reports a conflict instead of overwriting the newer state. Commit also checks that cited evidence is
still readable before publishing the milestone.

## Continue work

Continue can start from:

- an explicit Prepared Handoff;
- an exact committed Revision selected explicitly;
- the latest committed Revision in the current scope.

PowerContext resolves the Handoff, checks that its evidence is still available, and presents the same content the user
could inspect before the Agent starts planning. The Agent then compares its claims with the current request,
repository, workspace, and tool results.

Continue uses these rules:

| Condition | Behavior |
| --- | --- |
| No committed Handoff exists | Report that there is no saved state |
| The Handoff belongs to another scope | Do not treat it as current; require the originating scope |
| The Handoff has no next action | Present the state without inventing work |
| The current objective differs from the Handoff objective | Report the difference; do not adopt the historical objective as current |
| Required evidence is unavailable | Do not rely on the unsupported claim; act only after re-establishing it from current facts |
| Live facts contradict the next action | Report the conflict and do not perform the affected action |
| Evidence and live facts agree | Continue under the current request and authority |

Continue only delivers work context. The Handoff objective is a historical snapshot from preparation time. The caller
or host still owns the current objective and execution authority.

Selecting an old Revision does not make it current. It remains historical input until live validation shows that its
next action still applies.

## Review milestone history

Each committed Revision is a complete Handoff. A reader can understand the latest milestone without replaying earlier
ones. Earlier Revisions remain available through exact references.

A history view may compare two Revisions, but that comparison is a presentation. It does not replace either original
Handoff or create another source of truth.

## End-to-end flows

The following examples demonstrate the contract without prescribing an API or transport.

### Temporary session transfer

1. Session A prepares a Handoff for the objective "Complete parser error handling."
2. The state says that error mapping changed and cites the relevant Source. The next action says to run regression
   tests and cites the changed Artifact. Missing recent test output is recorded as an omission.
3. The user corrects the draft and finalizes a Prepared Handoff.
4. Session A transfers that value to Session B. No Artifact Revision is created.
5. Session B resolves the references, compares the claims with the live workspace, and runs the tests only if the
   next action still applies.

### Durable milestone and later recovery

1. After the tests pass, the user prepares another complete Handoff and chooses to commit it.
2. Commit verifies the evidence and appends one immutable Handoff Revision to the current scope.
3. A later session selects that scope and asks to Continue without supplying a Handoff.
4. PowerContext resolves the latest committed Revision. The Agent validates it against the current environment before
   taking the proposed next action.

### Concurrent milestone commits

1. Sessions A and B prepare different Handoffs from the same committed head.
2. Session A commits first and advances the scope's history.
3. Session B's commit conflicts because its observed head is stale.
4. Session B reads the new milestone and prepares a complete replacement. It cannot overwrite or append blindly from
   the stale state.

# Reference-level explanation

## Product model

Prepared and committed Handoffs share content but have different lifecycle guarantees:

| Contract | Prepared Handoff | Committed Handoff |
| --- | --- | --- |
| Primary use | Direct session transfer | Milestone tracking and recovery |
| Addressing | Explicit value | Exact Revision or current scope |
| Retention | No guarantee | Durable history |
| History | None | One immutable Revision sequence per scope |
| Update | Replace with another Prepared Handoff | Commit a new Revision |

A Prepared Handoff is final enough to transfer, but it is not an Artifact. A committed Handoff is an Artifact Revision.
The act of committing adds durable identity; it does not rewrite the Handoff content.

The first version binds one Handoff Artifact identity to each scope. All committed Revisions in that scope describe its
single current workstream. A later Handoff may restate the caller-owned objective, but doing so does not create a
separate work identity or parallel history.
An integration must not commit unrelated workstreams into the same Handoff scope. If it cannot confirm that the
current scope identifies the intended workstream, Continue must require an exact Revision instead of treating the
latest Revision as current work.

The temporary envelope associates a Prepared Handoff with its originating scope and the committed head observed during
preparation. These routing and concurrency values are not part of the shared Handoff content. A committed Handoff gets
the equivalent association from its Artifact identity and Revision.

Prepared Handoff is distinct from the Prepared Context in RFC 0028. A Prepared Handoff describes work for another
participant. A Prepared Context is a bounded, ephemeral selection of untrusted material for one Agent turn.

## Content contract

The objective, state, disposition, next action, evidence, and omissions form one versioned content contract. Each state
statement and the next action carry one or more citations. The objective does not require a citation.

State contains at least one current fact. Disposition must be supported by state; when work is blocked, state describes
the blocker. State and next action must cite readable evidence. The next action is optional and singular so that
Handoff does not become an action queue.

Omissions record relevant material that was known but unavailable, excluded, or not verified. An omission should retain
its original reference when one exists.

Evidence may cite a Source, an exact Artifact Revision, or an exact Memory entry version. A Memory entry citation
refines an exact Memory Revision; it does not replace that Revision in the committed Handoff's Artifact lineage.

Continue validates evidence per statement. Unavailable evidence affects only the statements and actions that depend on
it; other validated content remains usable.

Each Handoff is self-contained. It does not rely on earlier Handoffs for meaning.

## Lifecycle

```text
Draft -> Prepared Handoff -> Transfer
                          -> Commit -> Handoff Revision
```

Preparing and finalizing a temporary Handoff do not change durable history. Commit is the only transition that creates a
Revision.

A later Prepared Handoff may use an earlier Prepared Handoff as context. It still contains a complete current state, not
an incremental patch.

The first version requires the caller to initiate preparation, Continue, and explicit commit. Copying, restoring, or
inheriting conversation history does not start Continue by itself. A future Trigger may request the same preparation
or Continue path, but automatic preparation and automatic commit require separate policies. A Trigger cannot authorize
commit or execute the next action by itself.

## Responsibilities

| Participant | Responsibility |
| --- | --- |
| Caller or host | Supply the objective, choose temporary transfer or commit, and authorize current work |
| PowerContext | Prepare content, validate references, preserve exact content, and manage durable history |
| Human or Agent | Check natural-language claims against current facts and decide whether to act |

Generation may propose text and evidence selections. It does not establish truth, authorize actions, or commit a
milestone.

## Consistency

One scope has one committed Handoff sequence. A Prepared Handoff is associated with the committed head observed during
preparation, or with the absence of a head for the first commit. Commit succeeds only while that observation remains
current.

A successful commit publishes the new Revision and its history position together. Retrying the same commit does not
create duplicates. Content equal to the current Revision is a no-op.

Temporary transfer does not change committed history. Losing a Prepared Handoff therefore has no effect on saved
milestones.

## Continue and trust

Reference validation and truth validation are separate:

- PowerContext checks that evidence points to readable material in the Handoff's scope.
- The human or Agent decides whether the statement remains correct in the current environment.

Readable evidence proves which material supported a statement. It does not prove that the statement is still true.

Handoff content is untrusted history. It cannot override current system or developer instructions, the current user
request, repository rules, authorization, or live tool results. PowerContext prepares continuation input; it does not
authorize tool use or execute the next action by itself.

## History and compatibility

Committed Revisions remain immutable and readable through exact references. The latest lookup returns only committed
Handoffs.

Handoff content and its temporary envelope are versioned. A consumer that does not understand a version must reject it
before Continue or commit. A new version does not rewrite an old Revision in place.

## Scope

This RFC includes:

- the shared Handoff content contract;
- temporary transfer through a Prepared Handoff;
- durable milestone history through committed Revisions;
- bounded preparation, evidence validation, and omissions;
- exact and latest Continue behavior;
- concurrency, trust, and compatibility guarantees.

The following remain outside this RFC:

- task or work identity;
- parallel Handoff histories and merge;
- automatic milestone commits;
- cross-scope import or evidence copying;
- participant agreements and their lifecycle; Handoff may later consume their independent representation, but this
  RFC does not define it;
- retention policy and authenticated actor identity;
- transport and provider interface schemas.

## Acceptance

| Scenario | Pass condition |
| --- | --- |
| Temporary handoff | Two sessions exchange one Prepared Handoff without creating a Revision |
| Inspection | The recipient reads the same finalized content that the sender inspected before planning |
| Milestone | Explicit commit adds one immutable Revision without changing its Handoff content |
| Concurrency | A stale commit cannot replace a newer milestone, and retries do not duplicate a Revision |
| Continue | Evidence is checked per statement before action; the historical objective is not inherited, and unsupported claims are re-established |
| Workstream selection | Latest is not used implicitly when the scope cannot be confirmed as the current workstream |
| History | The current scope resolves the latest milestone, while exact older Revisions remain readable |

# Drawbacks

- Two Handoff forms add a lifecycle distinction that users and implementers must understand.
- A Prepared Handoff cannot be recovered after its carrier is lost.
- One linear milestone history cannot represent parallel work in the same scope.
- Bounded preparation can miss relevant material.
- Evidence checks add work before Continue.

# Rationale and alternatives

## Persist every Handoff

This would simplify latest lookup, but routine session transfers would fill milestone history and require a durable
write. Temporary transfer remains a separate form.

## Use only temporary Handoffs

This would support direct transfer, but there would be no durable milestone, latest lookup, or recovery after the
temporary value is lost.

## Store incremental Handoffs

This would reduce repeated content, but a recipient would need the earlier chain to understand the current state.
Self-contained Handoffs keep Continue independent of history replay.

## Add work identity now

Work identity would require rules for creation, selection, parallel histories, and merge. The first version supports one
current workstream per scope.

# Prior art

RFC 0002 defines Handoff as an Artifact Family, gives objective ownership to the caller, and defines optimistic Artifact
updates. RFC 0014 defines exact Memory citations and lineage. RFC 0019 defines the scoped Runtime. RFC 0028 defines
bounded, untrusted context preparation.

# Unresolved questions

The first version intentionally has no automatic milestone policy. Selection limits, storage layout, transport schemas,
and the policy for automatic preparation belong to implementation or later interface RFCs.

# Future possibilities

Later RFCs may add automatic milestone policy, actor attribution, labels, retention and export, cross-scope import,
derived scopes, parallel workstreams, and temporary lookup.
