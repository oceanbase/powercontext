---
title: Manage Prompts
description: Customize operation instructions, review demonstrations, and restore Prompt revisions in one Scope.
---

# Manage Prompts

Prompt customization is available on current `master`. It changes operation guidance within one Scope; it cannot
change output schemas, tool availability, or permissions. Captured user prompts are [Source evidence](sources.md),
which is a separate workflow.

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
