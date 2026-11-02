---
title: Manage Prompts
description: Customize operation instructions, review demonstrations, and restore Prompt revisions in one Scope.
---

# Manage Prompts

Prompt customization is available on current `master`. It changes operation guidance within one Scope; it cannot
change output schemas, tool availability, or permissions. Captured user prompts are [Source evidence](sources.md),
which is a separate workflow.

`profile.generate` customizes Profile generation during automatic or manual flushes. Custom content affects only
future processing windows; evidence boundaries, person attribution, sensitive-attribute safeguards, and the output
contract remain fixed by the server.

`atomic_memory.reconcile` customizes how an extracted Memory candidate is compared with related memories and earlier
candidates from the same window. When `atomic_memory.extract` and `atomic_memory.reconcile` both use Auto, a candidate
with nothing to compare is kept as extracted without a reconciliation request. When either Prompt is Custom, every
candidate is reconciled. Extraction decides which facts are worth keeping. Built-in reconciliation can also drop a
candidate it judges unworthy, but only while comparing it, so the first candidate of a window that has no related
memory does not get that second check.

## Edit and verify

1. Read current configuration, built-in instructions, and operation status through
   `GET /v1/scopes/{scope_id}/prompts/{prompt_key}`. Disabled operations remain readable; saving configuration does not
   enable them. Externally managed components may not expose built-in instructions.
2. Set Custom instructions and complete JSON input/output demonstrations using the Prompt content contract.
   Inspect generated demonstrations before saving them.
3. Create or conditionally replace the Prompt through the Artifact API. A successful save creates an immutable Revision.
   Read the current head again and reconcile edits if a version conflict occurs.
4. Run the intended operation and inspect its output. Saving a Prompt does not reprocess historical Sources or rerun operations.

Save Auto mode to use the current deployment's built-in guidance. Restore historical content as a new Revision,
preserving intervening history; restoring Auto uses current built-in instructions. The Dashboard has no Prompt editor.

In enforced mode, creation, replacement, switching to Auto, and restoration require current `scope.admin` authority.
Owning a Prompt Artifact does not permit changes after that Scope role is revoked.

To reuse instructions in another Scope, create or replace its Prompt using a registered `prompt_key`.
Generic Artifact publication rejects Prompts with `422 / artifact_publication_unsupported` and leaves the target Scope unchanged.

For applications, read effective configuration through `GET /v1/scopes/{scope_id}/prompts/{prompt_key}` and use the
[Artifact API](artifacts.md) with conditional writes to save revisions. See the [HTTP API](../develop/http-api.md)
for supported keys, request shapes, and demonstration generation.

## Topic Memory content requirements

Topic Memory's built-in stage instructions use version `v2`. Auto and custom generation must produce concrete
business content supported by supplied material, preserve still-valid history on updates, and keep opaque IDs in
routing fields. Counts are upper bounds, not quotas. Probe/global may return empty results and evolve may return
`proposal: null`; temporary and reduction stages retain their existing non-empty evidence-preservation contracts.

Custom demonstrations that teach recognizable placeholder or coordination-only output are rejected with
`prompt_definition_incompatible`, including when an existing custom revision is resolved for inference. Stored Prompt
revisions remain unchanged. A custom Prompt cannot disable runtime content admission. Instruction-only custom
Prompts remain supported, but their generated output passes the same admission checks as Auto output.
