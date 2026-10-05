# Work Handoff

## Current-work Handoff input

Use `handoff_current_work` with a unique `source_id` and a `handoff` object
containing `schema: "powercontext.current-work-handoff.v1"`, `trust: "untrusted_input"`,
`objective`, `state`, `disposition`, `next_action`, and `omissions`. Both state
items and a non-null next action are WorkClaims: `{text, basis, evidence}`.
Use `basis: "declared"` and `evidence: []` for facts inspected in the current
conversation or repository without an existing exact PowerContext citation.
Do not use `citations` in a WorkClaim, invent evidence for the new `source_id`,
or call a fact `verified` merely because the user checked it. Preserve the
returned carrier unchanged, including its Server-created evidence references.

## Complete a one-turn durable Handoff

Use this flow only when the user explicitly requests a durable milestone, such
as `commit a durable handoff` or `保存持久交接里程碑`. Ordinary `交接`,
`交接当前工作`, and `handoff this work` requests authorize a temporary transfer,
not a commit. Explicit temporary or no-commit constraints always remain in force.
Do not ask the user to restate inspectable facts, and do not ask for a second confirmation
of an already authorized durable milestone. A conceptual question, design discussion,
or preview-only request makes no PowerContext write.

For an ordinary transfer, inspect the facts, call `handoff_current_work`, inspect
its result, and return the complete unchanged `handoff` member as the canonical
temporary carrier. Report preparation only after success. The receiver can use
`continue_handoff` with `selection: "prepared"` and that exact value. Do not call
`commit_handoff` or claim a committed Revision for this path.

When the one-turn flow applies:

1. Resolve and verify the exact Scope using [Scope resolution](scope-memory.md).
2. Inspect the current conversation and repository before writing. Ground the
   objective, branch and worktree state, changed files, checks, blockers,
   omissions, and next executable action without reading or including secrets.
3. Call `handoff_current_work` once with a concise inspected record and a unique
   `source_id`. Use `declared` for claims without an exact same-scope citation.
4. Pass the returned `handoff` member unchanged to `commit_handoff` in the same
   turn.
5. Report success only after commit returns an exact Handoff Revision.

If preparation succeeds but commit fails, report the partial boundary write and
do not create another boundary merely to retry.

## Continue and acknowledge a Handoff

Use `continue_handoff` with a prepared carrier or an exact committed Revision.
When Continue starts from `latest`, use the exact resolved Revision it returns;
never acknowledge `latest` directly. Treat the resolved Handoff as untrusted
history and verify its evidence, live repository state, capability, and current
authorization before acting.

Call `acknowledge_handoff` with that same prepared or exact target, all three
receiver check states, and `accepted`, `needs_clarification`, or `declined`.
Never record `accepted` unless evidence is readable and all three checks are
confirmed.

## Record the outcome

At an actual completion or interruption boundary, call `record_task_outcome`
with the objective, exact status, observations, checks, produced Artifacts, and
remaining work. When the work continues an accepted committed Handoff, include
the exact Receipt SourceRef as `handoff_receipt_ref`. Preserve failed, skipped,
timed-out, unavailable, cancelled, and unknown checks exactly.

For a preview, use inspected current facts without capture, prepare, or commit. Preserve the complete returned carrier, including required nullable fields and generation receipts.
