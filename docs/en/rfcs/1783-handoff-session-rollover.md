- Proposal Name: `handoff_session_rollover`
- Start Date: 2026-09-29
- Status: Draft
- RFC PR: [oceanbase/powercontext#1783](https://github.com/oceanbase/powercontext/pull/1783)
- Tracking Issue: Not assigned
- Related RFCs: [RFC 0001](0001_product_definition_and_vision.md), [RFC 0014](0014_memory_layer_design.md), [RFC 0019](0019_local_source_memory_runtime.md), [RFC 0028](0028_context_pack.md), [RFC 0048](0048_handoff_artifact.md), [RFC 0082](0082_handoff_report.md), and [RFC 1489](1489_prepared_context_text_assembly.md)

# Summary

This RFC defines session rollover as a Handoff lifecycle event. When a long Agent session approaches context limits or starts to accumulate stale assumptions, the host or user can ask PowerContext to prepare a Rollover Handoff: a complete, evidence-backed work checkpoint for continuing the same scope in a fresh session. A fresh session then continues from the Rollover Handoff and bounded PreparedContext instead of inheriting the old transcript or a lossy conversation summary.

PowerContext remains an Agent memory and work-continuity system. It does not become an Agent runtime, a transcript store, or a provider-specific context-window manager. Rollover Handoff reuses the RFC 0048 Handoff content contract and commit semantics, adds advisory rollover reasons to preparation, and keeps long-term Memory promotion explicit.

# Motivation

Long Agent sessions fail in a different way from ordinary missing context. Even when the model still has enough tokens, a session can accumulate stale assumptions, outdated plans, redundant summaries, noisy tool output, and hidden dependency on earlier wording. Repeated compaction can preserve a session's apparent continuity while degrading the exact facts needed to continue safely.

PowerContext already has the right durable boundary for this problem: Handoff. A Handoff captures objective, state, disposition, next action, evidence, and omissions. That is exactly the minimum state a fresh session needs to continue work without inheriting the whole old session.

Without a rollover concept, hosts tend to choose one of two weak strategies:

- keep extending the current session until the model or host must compact it;
- start a new session and rely on Memory, old prompts, or an informal summary to recover state.

Both blur the distinction between current work state, long-term project knowledge, and original evidence. This RFC defines a narrower path: turn session rollover into an explicit Handoff checkpoint. The checkpoint can be inspected, committed, and later used by Continue like any other Handoff, while Source and Memory keep their existing boundaries.

## Review focus

Reviewers should focus on these decisions:

- whether session rollover belongs in Handoff semantics rather than Memory, PreparedContext, or a new Notes family;
- whether the proposal preserves RFC 0048's distinction between temporary Prepared Handoff and committed Handoff Revision;
- whether the rollover quality requirements are sufficient to prevent vague summaries such as "continue the previous work";
- whether automatic draft preparation can be allowed while automatic commit remains a separate policy decision;
- whether the proposed relationship with PreparedContext is narrow enough to avoid making Context Pack a Handoff replacement;
- whether Source capture is sufficient prior art for optional transcript evidence without making PowerContext a full transcript store.

# Guide-level explanation

## Rollover Handoff

A Rollover Handoff is a Handoff prepared because the current Agent session should not be the primary carrier of work state anymore.

Typical triggers include:

- the user asks to continue in a new session;
- the host observes that a session is long or near its context budget;
- the Agent reports that the current context is noisy, stale, or too compressed;
- a host-specific compaction event happened and the next session needs a clean continuation point;
- a human wants to checkpoint the current state before delegating to another Agent.

These triggers are recorded as rollover reasons supplied by the caller or host. PowerContext itself does not detect context pressure; detection belongs to the host trigger policy defined below.

Rollover does not mean that the work is complete. It means the next participant should continue from a verified checkpoint rather than from the old conversation.

The Handoff content still answers the RFC 0048 questions:

| Field | Rollover requirement |
| --- | --- |
| Objective | The workstream objective being continued |
| State | The current work state that a fresh session must understand |
| Disposition | Usually `continuable`, but may be `blocked` or `complete` |
| Next action | The first action the fresh session should consider |
| Evidence | Exact references for state and next action |
| Omissions | Missing checks, unverifiable assumptions, excluded materials, or host context that was not captured |

A Rollover Handoff is not a transcript summary. It should omit details that do not change how the next session continues.

## Example: move a coding task to a fresh session

Session A has been implementing a feature for several hours. It has read many files, tried two designs, fixed tests, and now the model starts repeating an older plan. The user says:

```text
Prepare a rollover handoff so I can continue this in a new session.
```

PowerContext prepares a Handoff draft:

```text
Objective:
  Add session rollover support to Handoff without changing Memory semantics.

State:
  - The RFC draft exists in English and Chinese. [evidence: exact Artifact revision]
  - The design deliberately treats rollover as a Handoff lifecycle event. [evidence: user prompt Source]
  - No runtime implementation has been started. [evidence: Git diff Source]

Disposition:
  continuable

Next action:
  Review the RFC for consistency with RFC 0048 and RFC 0028, then open the RFC PR.

Omissions:
  - No full repository test suite has been run because this is a documentation-only change.
  - Host-specific token-budget signals are not specified beyond an advisory input.
```

The user can inspect and correct the draft. If it is only being copied into another session, the Prepared Handoff can be transferred directly. If it is meant to become the latest workstream checkpoint, the caller explicitly commits it as a Handoff Revision.

## Continue from rollover

Preparing a Handoff does not open the new session. Opening, resetting, or closing the model window is a host action; PowerContext only prepares, validates, and stores the checkpoint. The host owns the following sequence:

```text
prepare -> open/reset -> deliver -> Continue
```

1. `prepare`: the originating session prepares the Rollover Handoff and the caller inspects it. If the checkpoint must survive a failed delivery, the caller commits it as a Handoff Revision.
2. `open/reset`: the host opens the fresh session and binds it to the Handoff's originating scope.
3. `deliver`: the host transfers the exact Handoff into the fresh session — the Prepared Handoff value or the exact committed Revision reference. This is the explicit transfer of RFC 0048, not transcript inheritance.
4. `Continue`: the fresh session verifies the transfer (see "Receiver verification and recovery"), then continues from the Handoff under the current request.

The scope binding matters. Continue resolves the latest committed Handoff only in the Handoff's originating scope. If the fresh session binds to a different scope, the host must deliver the exact Prepared value or exact Revision, and Continue must not treat a latest lookup as current work.

A fresh session should not receive the full old transcript by default. After delivery, the fresh session receives:

1. the Handoff content as untrusted historical work state;
2. exact evidence references or evidence-check results;
3. bounded related Memory, Experience, Profile, or Topic Memory when requested by the context assembly;
4. omissions explaining what was not carried forward.

The fresh session still treats current instructions, the current request, repository rules, live workspace state, and tool results as higher priority than the Handoff.

## Relationship to Memory

Rollover Handoff does not automatically create or update Memory.

During rollover, information falls into different stores:

| Information | Destination |
| --- | --- |
| Current objective, progress, next action, blockers | Handoff |
| Original prompts, tool outputs, file snapshots, test output | Source, if the host captured them |
| Long-term project decisions or constraints | Memory, only through explicit Memory flows |
| Reusable troubleshooting lessons | Experience, only through the existing review lifecycle |
| Summarized domain knowledge | Topic Memory, through its own processing lifecycle |

This keeps a temporary work checkpoint from polluting long-term project Memory. A Rollover Handoff may cite Memory, and later review may promote a durable decision from the Handoff into Memory, but promotion is explicit and reviewable.

# Reference-level explanation

## Scope

This RFC defines:

- Rollover Handoff as a named use of the existing Handoff lifecycle;
- rollover reasons, the host trigger policy, and host signals;
- additional quality requirements for rollover content;
- the host sequence that opens or resets a session window around a Rollover Handoff;
- receiver acknowledgement and recovery when a rollover fails;
- how a fresh session receives continuation context;
- the boundary between Handoff, Source, Memory, Experience, Topic Memory, and PreparedContext.

This RFC does not define:

- provider-specific APIs for opening a new model session or context window;
- full transcript storage in PowerContext;
- automatic Memory creation from every rollover;
- a new durable Notes family;
- automatic execution of the Handoff next action;
- implementation priority or rollout policy.

## Product model

Rollover Handoff is not a new Artifact family. It is a Handoff prepared with advisory rollover reasons supplied by a host or user. The shared content contract remains the RFC 0048 Handoff contract.

Prepared and committed forms retain their existing lifecycle:

```text
Draft -> Prepared Rollover Handoff -> Transfer
                                  -> Commit -> Handoff Revision
```

The rollover reasons are part of the preparation context. They guide draft generation and inspection, but they are not part of Handoff content identity in the initial implementation. A committed Rollover Handoff is still a Handoff Revision in the scope's linear Handoff history. Reading the latest Handoff does not require special handling.

## Rollover reasons

A preparation request may include one or more advisory reasons:

| Reason | Meaning |
| --- | --- |
| `user_requested` | The user explicitly requested a new session or checkpoint |
| `host_context_budget` | The host estimates that the session is near a context limit |
| `host_compaction` | The host reports that compaction already occurred or is imminent |
| `context_quality` | The Agent or host reports stale, noisy, or over-compressed context |
| `delegation` | Work is being transferred to another Agent or human |
| `manual_checkpoint` | The caller wants a checkpoint without claiming the session is unhealthy |

Reasons are advisory. They help generation focus on a fresh-session checkpoint, but they do not authorize commit or execution and do not create a distinct durable Artifact kind.

## Host trigger policy

Rollover reasons record a caller's decision; they do not detect context pressure. Detection belongs to the host. A host that supports the Advisory or Automatic draft level defines its trigger policy explicitly:

- which observations it uses, such as token estimates, provider usage reports, compaction events, or session age;
- the thresholds or events that turn an observation into a suggestion or a draft;
- when preparation runs, so drafts are prepared at safe boundaries rather than in the middle of an action;
- what the policy may and may not do: suggestions and drafts never commit, and they never close or reset the window by themselves.

PowerContext supplies the checkpoint contract, not the detection logic, and treats host observations as untrusted inputs (see "Host integration").

When usage or compaction signals are unavailable, the trigger policy degrades to the Manual level: only `user_requested`, `delegation`, or `manual_checkpoint` triggers apply. The host must not fabricate or infer signals it does not have, and missing signals never block an explicit user request. A host that cannot detect budget pressure can still support full rollover through user-initiated preparation.

## Quality requirements

A Rollover Handoff must be self-contained enough for a fresh session to begin safely. In addition to RFC 0048 validation, finalization should reject or flag content that lacks:

- a nonempty objective;
- at least one current state statement;
- a disposition;
- either a next action or a disposition that explains why no next action exists;
- evidence for state statements and next action, or an omission explaining why evidence is unavailable;
- explicit omissions for known unverified checks, missing host state, or relevant excluded material.

The following content is invalid or should require correction:

- "continue the previous work";
- "see the conversation above";
- a next action without evidence or an omission;
- a claim that tests passed without a cited test result or current verification;
- an objective rewritten by generation instead of supplied by the caller.

These checks may start as deterministic validation over the Handoff content model. A future generation pipeline may use a model to draft content, but model output cannot satisfy the evidence requirement by itself.

## Host integration

A host integration can support rollover in three levels:

| Level | Behavior |
| --- | --- |
| Manual | The user explicitly asks to prepare or commit a Rollover Handoff |
| Advisory | The host detects a long session or budget pressure and suggests preparing a Rollover Handoff |
| Automatic draft | The host prepares a draft at a safe boundary, but commit still follows configured policy |

Automatic commit is outside this RFC. It requires a separate policy decision because committing advances the scope's Handoff history.

Hosts may provide context-budget observations, such as model name, estimated remaining tokens, compaction count, or session age. PowerContext treats them as untrusted host observations unless backed by Source evidence. They can influence drafting and display, but they are not proof that a provider will actually open or preserve a session.

## Continue and PreparedContext

Continue remains the primary operation for acting on a Handoff. PreparedContext remains a bounded context package for one Agent turn. The two can work together without merging their contracts.

A continuation-oriented `prepare_context` profile may select:

1. the latest Handoff for the current scope, if the caller asked for Handoff-backed continuation;
2. exact evidence references or evidence check summaries from that Handoff;
3. related Memory and Experience under the existing assembly budget;
4. optional Profile or Topic Memory sections when explicitly requested.

This RFC does not require RFC 1489 to add `handoff` as an assembly family. It defines the semantic boundary so a later implementation PR or RFC can add that family without changing Handoff meaning.

PreparedContext must continue to mark historical material as untrusted. It must not make a historical objective current, execute a next action, or hide evidence omissions.

## Source and transcript boundary

Rollover can cite captured Source records, including host prompts, selected tool outputs, test results, or human-authored notes. This does not require PowerContext to ingest or retain the complete session transcript.

Hosts decide what they are allowed to capture. If relevant transcript material was not captured, the Handoff records an omission rather than pretending the material is available. A host may include a transcript location or digest as evidence only when it is readable through an authorized Source adapter.

The generator only sees the evidence a caller supplies. Work state that exists only in the current agent context — plans, partial reasoning, or decisions not yet written to any file, tool output, or Source — is invisible to preparation until it is captured. Hosts and Agents should capture that state explicitly with `handoff_current_work` (RFC 1223): the Agent declares its inspected facts with `basis="declared"` against a captured boundary Source, and the operation finalizes the Prepared Handoff deterministically in one step. This declared boundary is the default capture path for hosts that do not retain transcripts. State that is neither captured as Source nor declared through the boundary remains an omission.

## Concurrency and idempotency

Committed Rollover Handoffs use the same CAS behavior as RFC 0048. If the scope's Handoff head advanced after draft preparation, commit reports a conflict. The caller must read the new head and prepare a complete replacement or transfer the prepared value without committing it.

Repeated commit attempts for the same finalized content must remain idempotent under the existing Handoff commit rules. A rollover reason alone must not create a new Revision when the content is otherwise identical to the current head.

## Access and trust

Rollover does not weaken access control. A recipient must be allowed to read the Handoff scope and the cited evidence. Missing evidence degrades only the claims that depend on it.

All Rollover Handoff content delivered to an Agent is untrusted history. The current user request, developer and system instructions, repository instructions, live workspace, and current tool results take precedence.

## Receiver verification and recovery

The fresh session verifies the transfer before planning, reusing the existing acknowledgement semantics (`acknowledge_handoff`). The receiver re-resolves the exact prepared or committed Handoff, checks that cited evidence is readable, compares the claims with live state, capabilities, and authorization, and records the decision as a durable receiver acknowledgement. The receiver must not acknowledge an unresolved latest selector, and must not report acceptance while required checks are unknown. Acknowledgement does not execute the next action.

If window creation, delivery, or validation fails, the host retains a usable recovery point:

- The originating session remains usable until the receiver acknowledges; the host does not close or destroy it on the strength of an unacknowledged transfer.
- A committed Rollover Handoff is durable: delivery can be retried from the exact Revision in the scope's Handoff history.
- A Prepared-only Handoff has no durable identity. If its carrier is lost before acknowledgement, the host re-prepares from the still-usable originating session.
- A failed acknowledgement degrades only the claims that depend on unavailable evidence, per RFC 0048. The remaining validated content stays usable for retry.

# Drawbacks

This design gives Handoff another job. Reviewers and implementers must keep "work checkpoint" separate from "project milestone" so the Handoff history does not become noisy.

Rollover preparation can produce a false sense of safety if content quality checks are weak. A bad Handoff may be worse than no Handoff because it looks deliberate.

Hosts that do not expose context-budget or compaction signals can only support manual rollover. That is acceptable, but it limits automation.

Adding Handoff to continuation-oriented context assembly may increase injected bytes unless hosts select tight budgets.

# Rationale and alternatives

## Why Handoff

Handoff already describes continuable work state with evidence and omissions. Session rollover is a work-continuity event, not a knowledge-ingestion event. Reusing Handoff preserves the existing trust, evidence, and CAS semantics.

## Alternative: store rollover notes in Memory

This would pollute long-term Memory with temporary progress, stale next actions, and incomplete validation. Memory remains appropriate for durable decisions and constraints, but not for every session checkpoint.

## Alternative: add a Notes family

A Notes family could model session-local scratch state, but it would overlap with Handoff for this use case and introduce a second work-continuity object. This RFC leaves room for future Notes if a different use case emerges, but does not need one for rollover.

## Alternative: store complete transcripts

Complete transcripts are useful evidence in some hosts, but they are expensive, sensitive, and provider-specific. PowerContext can cite captured Source records without becoming a transcript archive.

## Alternative: provider-specific `new_context`

Some hosts may offer a model-callable tool that opens a fresh context window. PowerContext should not standardize that provider action. It should standardize the checkpoint that lets a fresh session continue safely.

# Prior art

RFC 0048 defines Handoff and Continue as the existing work-transfer boundary. RFC 0028 and RFC 1489 define bounded PreparedContext delivery with trust wrappers, budgets, and exact citations. RFC 0014 defines Memory as durable project knowledge rather than session state.

OpenAI Codex introduced context-management work around token budgeting, a model-requestable fresh context window, history and notes tools, and lightweight history-note hints. The portable idea is not the provider-specific tool call, but the separation between current working context, explicit working state, and evidence retrieved on demand.

Developer workflows already use manual handoff documents before starting fresh sessions. This RFC makes that pattern part of PowerContext's Handoff semantics, with evidence and scope boundaries.

# Unresolved questions

- Should later implementations persist rollover reason observations separately for diagnostics, without changing Handoff content identity?
- Should committed rollover Handoffs be visually distinguished in Handoff Report through separate observations, or should reasons remain preparation-only?
- What minimum deterministic validation should be mandatory before a Prepared Rollover Handoff can be committed?
- Should continuation-oriented PreparedContext include Handoff in RFC 1489 assembly, or should Continue remain a separate host step?
- Which host observations are safe to capture as Source by default, and which require explicit user or workspace policy?

# Future possibilities

Future work may add host-specific adapters that detect budget pressure and suggest rollover automatically. Those adapters can remain optional and fail open.

PowerContext may later support a continuation profile for `prepare_context` that includes the latest Handoff, related Memory, Experience, Profile, and Topic Memory in a fixed order with one output budget.

Handoff Report could show rollover density, stale workstreams, or long-running sessions that checkpointed without later continuation.

Evaluation can compare long coding tasks continued through Rollover Handoff against tasks continued through transcript compaction or informal summaries, measuring task success and injected bytes separately.
