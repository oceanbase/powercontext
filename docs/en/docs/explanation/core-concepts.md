---
title: Core concepts
description: Understand how PowerContext organizes evidence, durable context, reviewed Artifacts, and work continuity.
---

# Core concepts

PowerContext uses a small set of domain values to organize project evidence and reusable context. Sources preserve
what happened. Revisioned Artifacts preserve selected results, and `PreparedContext` supplies a bounded view for one
Agent turn. Every value belongs to a scope.

## Scope is the isolation boundary

Every content operation uses a `scope_id`. The Scope selects an isolated Source journal, Memory lifecycle, Candidate
inbox, Handoff history, and related runtime state. Scope IDs are opaque Server identifiers. Integrations resolve an
explicit Scope, a durable binding, or the Server default; repository, path, session, and Agent identities are binding
inputs rather than Scope IDs.

A Scope ID selects data. It does not prove user identity, grant tool access, or authorize execution.

## Sources preserve evidence

A Source describes evidence that PowerContext can read. Captured Sources store content in PowerContext; referenced
Sources point to material owned by another adapter. A `SourceRef` identifies one Source by its type and ID.

Capturing a Source does not automatically create Memory, an Experience, or a Skill. A configured pipeline may process
eligible Sources later. Work Contracts and Task Outcomes are also captured as exact Source evidence.

## Artifacts have immutable Revisions

An Artifact is one immutable revision of reusable output. Its exact reference contains a family, Artifact ID, and
Revision:

```text
FAMILY/ARTIFACT_ID@REVISION
```

The Artifact ID remains stable while approved replacements create later Revisions. Lineage records the exact Source
and Artifact references used to produce each Revision. Reading an exact reference returns that historical snapshot,
even after the family head advances.

## Memory stores durable project knowledge

Memory is a revisioned Artifact family for reusable decisions, constraints, facts, state, and next steps. Entries can
be active or inactive; retiring an entry removes it from active recall without deleting its history.

Explicit Memory writes do not require a model. Source-based extraction does require a configured generation pipeline.
Memory is durable and searchable, unlike the temporary context prepared for one Agent turn. See
[Memory and Handoff](memory-and-handoff.md) for the boundary between durable knowledge and task transfer.

## Candidates separate proposals from approved Artifacts

Experience and managed Skill proposals enter a scope-local Review Inbox as `pending` Candidates. A Candidate contains
one current proposal version and its exact evidence. Review writes use `expected_version` so a decision cannot silently
apply after another writer changes the proposal.

Approval writes an immutable Artifact Revision and returns its exact `result_artifact`. Rejection records a decision
reason without creating an Artifact. Both decisions are terminal. See
[Review Candidates](../how-to/review-candidates.md) for the procedure.

## Experience and Skill have different availability

An Experience records a situation, action, observed outcome, and reusable lesson. The approved current head can take
part in same-scope `PreparedContext` recall. Pending, rejected, and historical Experience Revisions are excluded.

A managed Skill contains a name, discovery description, instructions, validation checks, and lineage. Approval does
not install or execute it. An exact approved Revision must be exported explicitly before an Agent host can discover
the host-local projection. See [Experience and Skill lifecycle](experience-and-skill-lifecycle.md) for the full
review and availability model.

## PreparedContext is temporary

`PreparedContext` is the final bounded value for one Agent turn. The Runtime selects active Memory and approved
Experience heads for the request query, applies a shared byte budget, and returns either `ready` content or `empty`.
The result is not a new durable record.

Recalled content is history, not an instruction authority. The receiving Agent must still follow current user and
system instructions, inspect live workspace state, and check its actual capabilities.

## Work continuity records task boundaries

The high-level work loop uses four durable or transferable values:

```text
Work Contract → Prepared Handoff → Acknowledgement → Task Outcome
```

A Work Contract captures the objective and completion boundary as Source evidence. `handoff_current_work` captures an
inspected boundary and returns a temporary Prepared Handoff. Committing a Handoff creates a durable Revision only when
the user wants a milestone. The receiver resolves the Handoff and records an Acknowledgement; a Task Outcome preserves
the final status and checks as Source evidence.

The [Handoff Report](../how-to/use-handoff-report.md) projects the latest Handoff Revision in each selected Scope for
inspection and export. It is read-only and does not rewrite Memory or the underlying Handoff history.

## Interfaces expose different parts of the same Server

HTTP is the complete remote application contract. The Python Client provides typed access to that contract. MCP is a
curated Agent-facing projection, while the CLI covers setup, diagnostics, Server operation, and human review tasks.
Core protocols support applications that assemble their own Source, Artifact, and Trigger implementations.

Model generation, human Review, and execution authority remain separate. A model can propose content, Review can
approve an Artifact Revision, and export can create a host-local copy. None of those steps grants an Agent permission
to execute instructions.

Use [Interfaces](../reference/interfaces.md) for current surface availability and
[Configuration](../reference/configuration.md) for exact settings and defaults. RFCs record design decisions and may
not describe the current implementation.
