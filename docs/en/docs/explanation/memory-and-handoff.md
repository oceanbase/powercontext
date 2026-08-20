---
title: Understand Memory and Handoff
description: Learn the different purposes and boundaries of durable project Memory and a temporary work Handoff.
---

# Understand Memory and Handoff

PowerContext provides both durable project Memory and a temporary Handoff. Both help later work continue, but they
solve different problems.

## Memory: durable project knowledge

Memory stores information that later work may need and can understand independently, such as a decision, constraint,
current state, or next step. It belongs to a project scope, can be searched, and can be revised or retired. Revision
and retirement preserve history rather than silently overwriting an earlier record.

Codex should write Memory only when the user explicitly asks to save it. The prompt Hook captures prompts as Source
evidence, but capture is not the same as automatically creating Memory. It should not create an extra Memory merely to
duplicate the current prompt.

## Handoff: temporary transfer of work

A Handoff organizes a task's current objective, verified progress, blockers, next action, and evidence into temporary
content for a receiver. It must be explicitly prepared, inspected, and finalized. The receiver gets the complete
Prepared Handoff and checks it against current code and instructions.

Drafts and Prepared Handoffs are not durable project knowledge by default. Commit a Handoff only when the user
explicitly asks to retain a milestone.

## Choose the right one

| Your need | Use |
| --- | --- |
| A later project task needs a decision, constraint, or next step | Memory |
| Transfer the complete current task to another task, session, or model | Handoff |
| Record the current user prompt as processing evidence | Let the prompt Hook capture a Source |
| Retain a verified Handoff milestone for long-term reuse | Commit the Handoff on user request, or save it as Memory |

Never store secrets, access tokens, or other sensitive information in either. For the Handoff procedure, see
[Hand off work in Codex](../how-to/handoff-with-codex.md).
