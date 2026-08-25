---
title: Create and review an Experience
description: Generate an Experience Candidate from exact evidence, review it, and verify the approved Revision.
---

# Create and review an Experience

Generate an Experience Candidate when exact task evidence contains a reusable situation, action, outcome, and lesson.
After approval, the current Experience Revision can participate in `PreparedContext` recall for the same scope.

## Before you begin

Start the Server with a generation model and confirm that Experience generation is enabled:

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
powercontext server run
```

In another terminal:

```bash
powercontext capabilities
```

The output should report `Experience generation: enabled`. You also need at least one exact Source or Artifact
reference in the target scope. Provider credentials come from the selected inference provider.

## 1. Generate a Candidate

Pass the exact evidence that supports the proposed Experience:

```bash
powercontext experience generate \
  --scope-id project:example \
  --source-ref content/SOURCE_ID \
  --reason "Extract a reusable lesson from the completed task."
```

Repeat `--source-ref` or `--artifact-ref` when the proposal needs more evidence. The response is one of:

- `status: pending`, with a Candidate to review;
- `status: no_op`, with no Candidate because the model found nothing to propose.

Generation does not approve or recall the proposal.

## 2. Inspect and review the Candidate

For a pending result, copy its `candidate_id` and `version`, then inspect it:

```bash
powercontext candidate show --scope-id project:example CANDIDATE_ID
```

Check all four Experience fields and their exact evidence. Approve only the version you inspected:

```bash
powercontext candidate approve \
  --scope-id project:example \
  --expected-version 1 \
  CANDIDATE_ID
```

The response should have `status: approved` and an exact Experience `result_artifact`. To revise or reject instead,
follow [Review Candidates](review-candidates.md).

## 3. Verify the approved Revision

Read the Candidate again and record `result_artifact`:

```bash
powercontext candidate show --scope-id project:example CANDIDATE_ID
```

The approved current head is now eligible for same-scope `PreparedContext` recall. Eligibility does not guarantee
selection because the Runtime applies the query and shared output budget. Exact Experience reads are available through
the Python Client and HTTP API.

## Replace an existing Experience

Generate a replacement against the exact current Revision and cite the evidence for the change:

```bash
powercontext experience generate \
  --scope-id project:example \
  --target experience/EXPERIENCE_ID@REVISION \
  --source-ref content/NEW_SOURCE_ID \
  --reason "Update the lesson with the verified follow-up result."
```

The CLI includes the target in Artifact evidence automatically. Review the new Candidate as usual. If another approval
has already advanced the Artifact head, approval reports an Artifact conflict and leaves this Candidate pending.

## Incubate Experiences on a schedule

To inspect completed task outcomes periodically, configure a separate interval and restart the Server:

```bash
export POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS=30
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
powercontext server run
```

The job processes bounded Content Source windows and considers only Sources whose metadata contains
`"kind": "task-outcome"`. Each activation inspects at most 32 Sources. Ordinary prompt Sources are ignored.

Scheduled incubation stops after creating pending Experience Candidates. It never approves them or puts pending
content into `PreparedContext`. Use the Review Inbox to make each decision.

For configuration defaults and provider settings, see [Configuration](../reference/configuration.md).
