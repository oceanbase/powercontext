---
title: Create and export a managed Skill
description: Generate a managed Skill Candidate from exact evidence, approve it, and export one Revision to Codex.
---

# Create and export a managed Skill

Generate a managed Skill when reviewed evidence can support reusable instructions and validation checks. Approval
creates an immutable Skill Revision. A separate export makes one exact Revision available to Codex.

## Before you begin

Start the Server with a generation model and confirm that managed Skill generation is enabled:

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
powercontext server run
```

In another terminal:

```bash
powercontext capabilities
```

The output should report `Managed Skill generation: enabled`. Set `POWERCONTEXT_SCOPE_ID` to an existing ID returned
by `create_scope`, then choose one provenance origin before generating:

| Origin | Use it when |
| --- | --- |
| `experience` | One or more approved Experience Revisions support a new Skill |
| `source` | Exact official or human-authored Sources directly support a new Skill |
| `usage` | Usage Sources support an update to an exact existing Skill Revision |

## 1. Generate a Candidate

From an approved Experience:

```bash
powercontext skill generate \
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
  --origin experience \
  --artifact-ref experience/EXPERIENCE_ID@REVISION \
  --reason "Turn the reviewed lesson into reusable instructions."
```

From exact Sources instead:

```bash
powercontext skill generate \
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
  --origin source \
  --source-ref content/SOURCE_ID \
  --reason "Create a Skill from the approved operating procedure."
```

The response has either `status: pending` with a Candidate or `status: no_op` with no Candidate. Generation does not
approve, install, export, or execute the proposal.

## 2. Review and approve the Candidate

Inspect the returned Candidate:

```bash
powercontext candidate show --scope-id "$POWERCONTEXT_SCOPE_ID" CANDIDATE_ID
```

Check its name, discovery description, complete instructions, validation checks, and exact lineage. Then approve the
version you inspected:

```bash
powercontext candidate approve \
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
  --expected-version 1 \
  CANDIDATE_ID
```

The response should have `status: approved` and an exact Skill `result_artifact`. Approval does not install the Skill
or grant execution authority. To revise or reject the proposal, follow [Review Candidates](review-candidates.md).

## 3. Read the exact Skill Revision

Use the Artifact ID and Revision from `result_artifact`:

```bash
powercontext skill show \
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
  --revision 1 \
  SKILL_ID
```

The response contains the approved content and its exact Source and Artifact lineage. Managed Skills do not enter
`PreparedContext`; they remain available through exact reads and explicit export.

## 4. Export the Revision to Codex

For Codex export, the Skill name must contain at most 64 lowercase letters, digits, and single hyphens. Its description
must contain at most 1,024 characters and no angle brackets. The destination directory name must match the Skill
`name`. For a repository-local Codex Skill:

```bash
powercontext skill export \
  --target codex \
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
  --revision 1 \
  --destination .agents/skills/backend-validation \
  SKILL_ID
```

The command creates a new directory containing:

```text
.agents/skills/backend-validation/
├── SKILL.md
└── powercontext.json
```

`powercontext.json` records the exact Artifact reference and rendered-content hash. The command refuses to replace an
existing destination. The managed Skill Revision remains authoritative; the directory is a host-local projection.

Codex detects the exported repository-local Skill automatically. If it does not appear, restart Codex.

## Evolve a Skill from usage

Use the `usage` origin with the exact current Skill Revision and Sources that record how it performed:

```bash
powercontext skill generate \
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
  --origin usage \
  --target skill/SKILL_ID@REVISION \
  --source-ref content/USAGE_SOURCE_ID \
  --reason "Update the validation steps from observed usage."
```

The CLI adds the target to Artifact evidence. Review and approve the replacement Candidate before exporting its new
Revision. If an export destination already exists, inspect and handle that projection explicitly before exporting
again; the command never overwrites it.

For external Agent-native Skill discovery and import contracts, see [Interfaces](../reference/interfaces.md).
