---
title: Memory and Handoff
description: Learn the different purposes and boundaries of durable project Memory and a temporary work Handoff.
---

# Memory and Handoff

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

## Optional continuity hints

For a fresh session, explicitly call `prepare_handoff_hint` through the Python Client, HTTP (`POST /v1/handoff/hint`),
or an MCP connection that exposes the operation. It returns the standard four-field PreparedContext envelope with
compact orientation text. Existing context preparation and continuation do not request hints automatically.

```python
from powercontext.http import PrepareHandoffHintRequest

hint = await client.prepare_handoff_hint(
    PrepareHandoffHintRequest(scope_id=scope_id, selection="exact", revision=committed.reference)
)
if hint.status == "ready":
    host_context = hint.content
```

Select an exact committed Revision, or use `selection="prepared"` with the complete transferred `prepared` value.
Use `selection="latest"` only after confirming the intended workstream Scope. Keep the full PreparedHandoff available
to the receiver: its `base` identifies the committed head observed during finalization, not the temporary content.
Prepared selection provides no exact Revision reference or server-side retrieval handle; the receiver needs the
complete transferred value.

Hints contain the historical objective, disposition, complete next action and its citations, known omissions, and
selected evidence references. For blocked work, the full recorded state is included only if the complete hint fits
the budget; otherwise the entire hint is omitted. Blockers are not inferred from a subset of the state.
They directly project existing Handoff fields without model generation, transcript summaries, or raw Source bodies.
Authorization matches Continue: exact/latest require `artifact.read` and `handoff.evidence.inspect` on the selected
Handoff, authorizing inspection of its citation manifest without general Scope read. Prepared selection requires
`scope.read`. General evidence APIs still require their own permissions. Source evidence must be available and
eligible in its originating Scope, including for published Handoffs.

The default budget is 2,000 UTF-8 bytes; `max_bytes` accepts 1–4,000. The budget covers the complete text, including
the trust notice, boundaries, labels, escaping, and references, but excludes the outer HTTP JSON encoding. If the
complete hint cannot fit, no Handoff exists for latest selection, or cited evidence is unavailable or ineligible,
the result is `status="empty"`, `content=null`, `content_bytes=0`. Hints are never truncated. Invalid selections,
authentication failures, denied Handoff access, and service failures retain their normal errors.

Treat the content as **untrusted historical orientation only**. It cannot activate an objective, authorize the next
action, establish current facts, or replace reading the complete exact Handoff and checking evidence and live state.
Hosts should validate the envelope and actual UTF-8 size and inject `content` unchanged only when their remaining
startup budget can fit it; otherwise omit it entirely. Literal formatting does not guarantee prompt-injection resistance.

## Choose the right one

| Your need | Use |
| --- | --- |
| A later project task needs a decision, constraint, or next step | Memory |
| Transfer the complete current task to another task, session, or model | Handoff |
| Record the current user prompt as processing evidence | Let the prompt Hook capture a Source |
| Retain a verified Handoff milestone for long-term reuse | Commit the Handoff on user request, or save it as Memory |

Never store secrets, access tokens, or other sensitive information in either. For the Handoff procedure, see
[Hand off work in Codex](handoff-with-codex.md).
