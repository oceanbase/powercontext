---
title: Manage Prompts
description: Customize operation instructions, review demonstrations, and restore Prompt revisions in one Scope.
---

# Manage Prompts

Prompt customization is available on current `master`. It changes operation guidance within one Scope; it cannot
change output schemas, tool availability, or permissions. Captured user prompts are [Source evidence](sources.md),
which is a separate workflow.

## Edit and verify

1. Open the Server Dashboard, select **Prompts**, and choose the intended Scope and operation.
2. Check the displayed capability. Customization requires a configured provider and an enabled operation. An injected
   component may manage its own prompts and report customization as unsupported.
3. Select **Custom**, edit the instructions, and add complete JSON input/output demonstrations if needed.
   Generated demonstrations are suggestions; inspect them before saving.
4. Choose **Save new revision**. A successful save creates an immutable Prompt Revision. If the head changed,
   reload the current revision and reconcile your edits.
5. Run the intended operation again and inspect its output. Saving a Prompt does not reprocess historical Sources
   or rerun previous operations.

Select **Auto** and save to use the currently deployed built-in guidance. **Restore as new revision** restores selected
historical content by creating a new head; it does not remove intervening history. Restoring an Auto revision resolves
the current built-in guidance, not a frozen copy of an older deployment.

In enforced mode, creation, replacement, switching to Auto, and restoration require current `scope.admin` authority.
Owning a Prompt Artifact does not permit changes after that Scope role is revoked.

To reuse instructions in another Scope, create or replace its Prompt using a registered `prompt_key`.
Generic Artifact publication rejects Prompts with `422 / artifact_publication_unsupported` and leaves the target Scope unchanged.

For applications, read effective configuration through `GET /v1/scopes/{scope_id}/prompts/{prompt_key}` and use the
[Artifact API](artifacts.md) with conditional writes to save revisions. See the [HTTP API](../develop/http-api.md)
for supported keys, request shapes, and demonstration generation.
