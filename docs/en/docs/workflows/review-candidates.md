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

Set `POWERCONTEXT_SCOPE_ID` to the existing Scope ID that contains the Candidate. This guide starts after an
Experience or Skill operation has created a pending Candidate.

## Start a Dream from existing evidence

Dreaming turns selected Memory entries or Experience revisions into one proposal for review. It runs in the background;
it can also finish with `no_change` or `needs_evidence` without creating a Candidate. Confirm that the Server reports
`artifact_dreaming: true` in `powercontext capabilities`. The built-in model adapter currently supports OpenAI-compatible
and Anthropic generation configurations; SDK retries are disabled so the durable run controls the call budget.

Save an exact Memory citation returned by the Memory API in `dream.json`:

```json
{
  "operation": "refine_experience",
  "memory_citations": [{
    "memory_ref": {"family": "memory", "artifact_id": "memory", "revision": 3},
    "entry_id": "ENTRY_ID",
    "entry_version_id": "ENTRY_VERSION_ID"
  }],
  "idempotency_key": "review-write-retry-evidence"
}
```

Replace all identifiers with references from the same Scope, then submit and inspect the returned run ID:

```bash
powercontext dream run --scope-id "$POWERCONTEXT_SCOPE_ID" --request-file dream.json
powercontext dream show --scope-id "$POWERCONTEXT_SCOPE_ID" RUN_ID
powercontext dream list --scope-id "$POWERCONTEXT_SCOPE_ID" --status succeeded
```

The HTTP resource is `/v1/scopes/{scope_id}/dream`: POST accepts a run, GET lists runs, and
GET `/v1/scopes/{scope_id}/dream/{run_id}` reads one. POST returns 202 for active work and 200 when replaying a finished
request. Reusing a key with the same normalized selection returns the same run; changing its selection returns 409.
Use the Candidate ID in a `proposed` result with the review commands below. A run does not approve or install its result.

To produce a Skill, set `operation` to `derive_skill` and place approved Experience references in `artifacts`.
Direct Memory citations are accepted only by `refine_experience`. To propose a replacement Experience, include its
current exact reference in both `artifacts` and `target`.

A selection contains 1–20 Memory citations and Experience references after deduplication, with at most 32 references
including supplementary `sources`. Source objects use `source_type` and `source_id`. Default limits are 32 projected
items, 64 KiB of model input, two model calls, 4,096 output tokens per call, and 120 seconds from first execution.
Each Scope admits at most 32 queued or running requests by default. Restarted workers resume persisted requests under
a lease; they keep the same evidence snapshot and execution deadline.

Use the Candidate commands below or `POST /v1/artifact-candidates/get` to read the current version and its
`memory_citations`, then inspect reference bodies through `POST /v1/memory/entries/get` and exact Source/Artifact reads.
The Dream run's `input_manifest` retains generation-time root Source groups and independence annotations. Repeated
citations to one root do not count as independent observations; unknown independence remains unknown. Review revalidates
evidence against the current Candidate version and reviewer permissions; an unavailable or retired entry blocks approval.
Omitting `memory_citations` or setting it to null on revision retains them; the HTTP request
can explicitly replace them, and `[]` clears them. Approved Experience revisions preserve these citations in their lineage.

Dashboard is an opt-in personal content viewer using static Bearer authentication. It displays approved Experiences and
Skills, with exact references linking to historical Memory entries. Dream creation, Run inspection, and Candidate review
use the CLI, Client, or HTTP API. See [Install and run](../get-started/install-and-run.md) to enable personal access.

The Runtime creates `pc_dream_runs` and adds nullable `memory_citations` columns to `pc_artifacts` and
`pc_artifact_candidate_versions` on startup. Existing rows read as empty citations. No copy of Memory entry bodies is stored
in the run table. Back up existing databases before deploying a schema change.

## 1. List pending Candidates

```bash
powercontext candidate list --scope-id "$POWERCONTEXT_SCOPE_ID"
```

The default Review Inbox contains only `pending` Candidate heads. Filter it by family when needed:

```bash
powercontext candidate list --scope-id "$POWERCONTEXT_SCOPE_ID" --family experience
powercontext candidate list --scope-id "$POWERCONTEXT_SCOPE_ID" --family skill
```

The response contains each `candidate_id` and current `version`. If it returns `next_cursor`, pass that value through
`--cursor` to read the next page. `--limit` accepts values from 1 to 100 and defaults to 50.

## 2. Inspect one Candidate

```bash
powercontext candidate show --scope-id "$POWERCONTEXT_SCOPE_ID" CANDIDATE_ID
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
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
  --expected-version 1 \
  CANDIDATE_ID
```

The response has `status: approved` and an exact `result_artifact`. Approval commits the proposal and marks the
Candidate approved in one transaction. The Candidate is then terminal.

### Reject the exact version

```bash
powercontext candidate reject \
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
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
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
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
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
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
powercontext candidate show --scope-id "$POWERCONTEXT_SCOPE_ID" CANDIDATE_ID
```

For an approved Candidate, record the exact `result_artifact`. For a rejected Candidate, confirm `decision_reason`.
The default Inbox no longer lists either terminal state; audit them explicitly when needed:

```bash
powercontext candidate list --scope-id "$POWERCONTEXT_SCOPE_ID" --status approved
powercontext candidate list --scope-id "$POWERCONTEXT_SCOPE_ID" --status rejected
```

If a write reports that the Candidate version is stale, show the Candidate again and review the new version. Do not
change `--expected-version` without inspecting the replacement. A terminal Candidate cannot be approved, rejected, or
revised again.

The HTTP API, Python Client, and MCP expose the same five Review operations and concurrency rules. See
[Interfaces](../develop/interfaces.md) for their contract and availability.

## 5. Use Dream experience in a later task

For example, record the actual results of a timed-out write, request replays, and database row-count checks, then
extract Memory entries from those Sources. Multiple entries describing the same check should still show one root
Source in review. Do not count those descriptions as repeated validation. Submit the entries to Dream, verify that
the proposal distinguishes read retries, idempotent writes, and writes with unknown commit status, then approve the
exact version.

At the start of a later retry task, send this request to `POST /v1/context/prepare`:

```json
{
  "scope_id": "SCOPE_ID",
  "query": "How can a retry avoid duplicate records when a write times out with unknown commit status?"
}
```

Check that the returned context includes the exact approved Experience reference before supplying it to the Agent
as historical evidence. Pending or rejected Candidates and Dream run explanations do not enter this context as
experience. The later task must still verify commit status and deduplication, recording new results as Sources.
If an original Memory entry is retired, new Dream submissions and Candidate approvals that cite it are blocked.
