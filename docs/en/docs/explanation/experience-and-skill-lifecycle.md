---
title: Understand the Experience and Skill lifecycle
description: Learn how evidence becomes reviewed Experience and managed Skill Artifacts, and when each one is available.
---

# Understand the Experience and Skill lifecycle

Experience and managed Skill content passes through the same review boundary. Evidence supports a proposal, a human
reviews one exact Candidate version, and approval creates an immutable Artifact Revision. Generation never approves
its own result.

## Evidence comes first

A Source records evidence such as a completed task outcome or human-authored material. An Artifact reference points to
one exact approved Revision. Generation and proposal operations keep these references as lineage so a reviewer can
check where the content came from.

References must belong to the same scope as the Candidate. A replacement also identifies the exact Artifact Revision
it intends to replace.

## Candidates are reviewable proposals

An Experience or Skill operation creates a `pending` Candidate. The Candidate has a current head and numbered,
immutable versions. Revising it appends a complete replacement proposal as the next version.

Reviewers act on the version they inspected. `expected_version` prevents an approval, rejection, or revision from
silently applying after another writer changes the Candidate. Approval and rejection are terminal states.

A Candidate is not an Artifact Revision:

- `pending` content has no `result_artifact`;
- approval writes the proposal and returns its exact `result_artifact` in one transaction;
- rejection records a decision reason without writing an Artifact.

## Experience captures reusable outcomes

An Experience contains a situation, action, observed outcome, and reusable lesson. Model-backed generation requires
configured generation inference. A human or integration with complete typed content can use the HTTP or Python
`propose_experience` operation without a model. Both paths stop at a pending Candidate.

After approval, the current Experience head becomes eligible for recall in `PreparedContext` within the same scope.
Recall still depends on the query and shared output budget. Pending and rejected Candidates, along with historical
Experience Revisions, remain excluded.

## Managed Skills contain instructions

A managed Skill contains a name, discovery description, instructions, validation checks, and exact lineage. Its
generation origin states what supports the proposal:

| Origin | Required direct evidence |
| --- | --- |
| `experience` | One or more approved Experience Revisions; exact Sources are optional |
| `source` | One or more exact Sources and no Artifact references |
| `usage` | An exact target Skill Revision plus Sources that record its use |

Approval creates an immutable Skill Revision. Managed Skills do not enter `PreparedContext`, install themselves, or
grant execution authority. An approved Revision becomes available to Codex only after an explicit export creates a
host-local projection.

## Revisions preserve history

Creating a replacement Candidate requires an exact current target. Approval creates the next Revision under the same
Artifact identity. If another approval moves the target head first, the stale replacement remains pending and the
approval reports a conflict. The reviewer must inspect the new head before deciding what to do next.

Earlier approved Revisions remain available for exact reads. An exported Skill directory is only a copy of one exact
Revision; the managed Artifact remains the content authority.

Use [Review Candidates](../how-to/review-candidates.md) for the review procedure,
[Create and review an Experience](../how-to/create-and-review-experience.md) for Experience operations, and
[Create and export a managed Skill](../how-to/create-and-export-skill.md) for Skill operations.
