---
title: Manage Artifacts
description: Read current and historical revisions, then choose the write workflow for each Artifact family.
---

# Manage Artifacts

Artifacts preserve versioned results. Memory, Experience, Skill, Handoff, and Prompt have family-specific write rules;
sharing a REST envelope does not make those workflows interchangeable.

## Inspect current and historical content

Use the scoped HTTP routes in the [API contract](../develop/http-api.md):

| Operation | Route |
| --- | --- |
| List a family | `GET /v1/scopes/{scope_id}/artifacts/{family}` |
| Read the current head | `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}` |
| List revisions | `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions` |
| Read exact evidence | `GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}` |

Keep the family, Artifact ID, and exact Revision when citing a result or handing it to another Agent.
The current head can advance while a historical Revision remains unchanged.

## Change content through its workflow

- [Memory](memory-and-context.md): explicitly write, revise, or retire entries.
- [Experience and Skill](experience-and-skill-lifecycle.md): inspect and approve Candidates before publication or export.
- [Handoff](handoff-with-codex.md): inspect and commit the current work boundary.
- [Prompt](manage-prompts.md): customize operation guidance within one Scope.
- [Tags](manage-artifact-tags.md): organize logical Artifacts and individual Memory entries without rewriting content.

For direct REST replacement, read the current `ETag` and send it in `If-Match`. Missing preconditions return `428`;
a stale head returns `412`. Reload and reconcile the content before retrying. Candidate review uses its own
`expected_version` contract instead. Access checks apply to the selected target in both cases.
