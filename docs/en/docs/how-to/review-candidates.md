---
title: Review Candidates
description: Inspect, revise, approve, or reject pending Experience and Skill Candidates.
---

# Review Candidates

Use the Review Inbox to decide whether a generated or submitted Experience or managed Skill should become an Artifact
Revision. Approval writes an immutable Revision. Rejection closes the Candidate without writing an Artifact.

## Before you begin

Start the Server and confirm that it is ready:

```bash
powercontext ready
```

You need the `scope_id` that contains the Candidate. This guide starts after an Experience or Skill operation has
created a pending Candidate.

## 1. List pending Candidates

```bash
powercontext candidate list --scope-id project:example
```

The default Review Inbox contains only `pending` Candidate heads. Filter it by family when needed:

```bash
powercontext candidate list --scope-id project:example --family experience
powercontext candidate list --scope-id project:example --family skill
```

The response contains each `candidate_id` and current `version`. If it returns `next_cursor`, pass that value through
`--cursor` to read the next page. `--limit` accepts values from 1 to 100 and defaults to 50.

## 2. Inspect one Candidate

```bash
powercontext candidate show --scope-id project:example CANDIDATE_ID
```

Before deciding, inspect:

- `proposal`, including every Experience field or the complete Skill instructions and validation checks;
- `source_refs` and `artifact_refs`, and the evidence identified by those exact references;
- `target`, when the proposal would replace an existing Artifact Revision;
- `reason`, `family`, `status`, and the current `version`.

Do not approve a claim that its evidence does not support. Review Skill instructions as content that may later be
exported, and reject any proposal that contains secrets or unsafe instructions. Approval itself does not install or
execute a Skill.

## 3. Approve, reject, or revise

Use the `version` you inspected as `--expected-version`.

### Approve the exact version

```bash
powercontext candidate approve \
  --scope-id project:example \
  --expected-version 1 \
  CANDIDATE_ID
```

The response has `status: approved` and an exact `result_artifact`. Approval commits the proposal and marks the
Candidate approved in one transaction. The Candidate is then terminal.

### Reject the exact version

```bash
powercontext candidate reject \
  --scope-id project:example \
  --expected-version 1 \
  --reason "The evidence does not support the proposed lesson." \
  CANDIDATE_ID
```

The response has `status: rejected`, preserves the reason in `decision_reason`, and has no `result_artifact`.
Rejection is terminal.

### Revise an Experience Candidate

A revision is a complete replacement proposal, not a patch. Include the evidence that should belong to the new
version:

```bash
powercontext candidate revise experience \
  --scope-id project:example \
  --expected-version 1 \
  --situation "Only one storage backend was tested." \
  --action "Run the same acceptance scenario on both backends." \
  --outcome "Both backends passed." \
  --lesson "Keep acceptance behavior backend-neutral." \
  --source-ref content/SOURCE_ID \
  CANDIDATE_ID
```

The response remains `pending` and has a higher `version`. Inspect that version before making another decision.

### Revise a Skill Candidate

Put longer instructions in a UTF-8 file. Supply either `--instructions` or `--instructions-file`, but not both.
Repeat `--validation` for each check:

```bash
powercontext candidate revise skill \
  --scope-id project:example \
  --expected-version 1 \
  --name backend-validation \
  --description "Validate storage backends consistently." \
  --instructions-file instructions.md \
  --validation "SQLite passes." \
  --validation "OceanBase passes." \
  --artifact-ref experience/EXPERIENCE_ID@REVISION \
  CANDIDATE_ID
```

Use `--target FAMILY/ID@REVISION` only for a replacement. The CLI adds the target to the Candidate's Artifact evidence
automatically.

## 4. Verify the result

Read the Candidate again:

```bash
powercontext candidate show --scope-id project:example CANDIDATE_ID
```

For an approved Candidate, record the exact `result_artifact`. For a rejected Candidate, confirm `decision_reason`.
The default Inbox no longer lists either terminal state; audit them explicitly when needed:

```bash
powercontext candidate list --scope-id project:example --status approved
powercontext candidate list --scope-id project:example --status rejected
```

If a write reports that the Candidate version is stale, show the Candidate again and review the new version. Do not
change `--expected-version` without inspecting the replacement. A terminal Candidate cannot be approved, rejected, or
revised again.

The HTTP API, Python Client, and MCP expose the same five Review operations and concurrency rules. See
[Interfaces](../reference/interfaces.md) for their contract and availability.
