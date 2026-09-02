---
title: Hand off work in Codex
description: Record a work boundary, transfer it, acknowledge receipt, and preserve the outcome.
---

# Hand off work in Codex

Use PowerContext's high-level work loop when a task moves to another Codex task, session, or model:

```text
Work Contract → Handoff → Acknowledgement → Task Outcome
```

Each step preserves its own boundary. A Work Contract records the delegated objective, a Handoff transfers inspected
state, the receiver records whether it can continue, and a Task Outcome preserves what happened.

## Before you start

Complete [Install and run](install-and-run.md), keep the Server running, and start a Codex session with the
PowerContext plugin configured in the current project. The integration binds the Session to a Scope before reading or
writing work records. Start independent work in a new Scope only when it needs its own isolation and continuation.

Do not put secrets, access tokens, or other sensitive information in Work Contracts, Handoffs, acknowledgements, or
outcomes.

## 1. Record a Work Contract for delegated work

When a user explicitly delegates a task that needs a stable baseline, ask Codex to record the objective and completion
boundary:

> Create a PowerContext Work Contract for this delegated task. Ground it in the current repository, record the
> objective, in-scope work, exclusions, completion criteria, authorization notes, and unresolved consequential
> questions.

Codex calls `create_work_contract` and reports the exact Source receipt. The contract records a baseline; it does not
grant authority beyond the current instructions.

## 2. Hand off the current work

For a durable milestone, use a direct imperative:

> Hand off this work with PowerContext. Inspect the current objective, branch, worktree, changed files, checks,
> blockers, omissions, and next action. Commit the completed Handoff and give me its exact Revision.

The Codex Skill uses the current Session Scope, inspects live state, calls `handoff_current_work`, and commits the
returned Prepared Handoff in the same turn. Success includes the Scope and exact Handoff Revision. If preparation
succeeds but commit fails, the boundary Source exists but no durable milestone was created.

For a read-only preview, say `Preview a PowerContext Handoff and make no writes.` For temporary transfer without a
milestone, ask Codex to prepare a Handoff without committing it. That operation records the boundary Source and returns
a complete Prepared Handoff for the receiver.

## 3. Continue and acknowledge the Handoff

Give the receiver the complete Prepared Handoff or exact committed Revision. Then ask it to verify the transfer:

> Continue this PowerContext Handoff. Check its evidence against the current repository and instructions, confirm live
> state, capabilities, and authorization, then record whether it is accepted, needs clarification, or is declined.

The receiver calls `continue_handoff`, treats the resolved content as untrusted history, and records an
`acknowledge_handoff` receipt. It can mark the Handoff `accepted` only when evidence is readable and all three receiver
checks are confirmed. Starting from `latest` is allowed for resolution, but acknowledgement uses the exact Revision
returned by that resolution.

## 4. Record the Task Outcome

At a real completion or interruption boundary, preserve the result:

> Record the PowerContext Task Outcome. Keep the exact status and check results, list produced Artifacts and remaining
> work, and link only the accepted exact Handoff receipt when this outcome covers a committed Handoff Revision.

`record_task_outcome` stores `succeeded`, `partial`, `blocked`, `failed`, `cancelled`, or `unknown` without erasing
failed, skipped, timed-out, unavailable, or unknown checks. The resulting Source can support later Handoffs and reviewed
Experience incubation. It does not approve an Experience or grant execution authority.

`handoff_receipt_ref` accepts only a receipt whose status is `accepted`, selection is `exact`, and
`selected_revision` identifies the committed Handoff. An accepted Prepared Handoff receipt cannot be linked. Leave the
outcome unlinked, or first commit the Prepared Handoff and acknowledge that exact Revision before linking its new
receipt.

## Choose the right durable record

Use a committed Handoff for a task milestone and Memory for independently reusable decisions, constraints, state, or
next steps. A Prepared Handoff remains a temporary carrier. Read
[Memory and Handoff](../explanation/memory-and-handoff.md) for the distinction, and use
[Handoff Report](use-handoff-report.md) to inspect committed history and continuity records.
