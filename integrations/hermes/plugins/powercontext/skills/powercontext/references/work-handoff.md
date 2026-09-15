# Work Handoff

## Current-work Handoff input

Use `powercontext_handoff_current_work` with a unique `source_id` and a `handoff` object
containing `schema: "powercontext.current-work-handoff.v1"`, `trust: "untrusted_input"`,
`objective`, `state`, `disposition`, `next_action`, and `omissions`. Both state
items and a non-null next action are WorkClaims: `{text, basis, evidence}`.
Use `basis: "declared"` and `evidence: []` for facts inspected in the current
conversation or repository without an existing exact PowerContext citation.
Do not use `citations` in a WorkClaim, invent evidence for the new `source_id`,
or call a fact `verified` merely because the user checked it. Preserve the
returned carrier unchanged, including its Server-created evidence references.

## Continuity

For work that may cross sessions, use a Work Contract or Handoff operation with
structured, evidence-backed objects:

1. Describe the objective, facts, scope, exclusions, completion criteria, and
   authorization notes in a Work Contract.
2. Use the Handoff prepare/activate flow to create an inspectable draft from
   exact evidence.
3. Finalize only after inspecting the draft. Return the complete temporary carrier.
   Commit only when the user explicitly requests a durable milestone; ordinary
   handoff requests and temporary transfers do not authorize a commit.
4. On receipt, use continue or acknowledge after checking the selected evidence
   and current capabilities.
5. Record a Task Outcome when the work completes, is blocked, or is cancelled.

Do not claim that a task is complete merely because a Handoff or Outcome was
written.

For a preview, use inspected current facts without capture, prepare, or commit. Preserve the complete returned carrier, including required nullable fields and generation receipts.
