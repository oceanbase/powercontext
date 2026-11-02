---
title: Scopes and access
description: Separate project context, bind Agents to Scopes, and authorize access.
---

# Scopes and access

A Scope selects a project context boundary. Its opaque ID identifies data; it does not authenticate the caller or
grant permission to access that data.

## Separate projects and bind an Agent

1. Read the default Scope with `GET /v1/scopes/default`, or create an independent Scope with `POST /v1/scopes`.
   Keep the returned `scope_id`; do not derive it from a directory, repository name, or Agent session ID.
2. Select that Scope through the host's explicit Scope setting or supported persistent binding operation.
   [Host guides](../integrations/index.md) document each setting. Without a binding or explicit selection, hosts can
   share the Server default; changing project directories alone does not establish isolation.
3. Inspect the resolved Scope in host diagnostics or API responses before saving project information.

Parent relationships organize Scopes; explicit context references describe reuse. Neither relationship grants access.
Exact, subtree, and all-Scope views change the selection being inspected, not the caller's permissions.

Binding resolution returns `404 scope_not_found` when no binding is available or an explicitly supplied Scope ID
does not exist. If a durable binding or default pointer exists but its target Scope is missing, the Server returns
`409 scope_binding_target_missing` with the original target ID in `error.details.scope_id`.
Resolution stops at that binding without falling back to later bindings or the default Scope. Client `Ensure()`
implementations must propagate this conflict so an operator can investigate the data loss, restore the original
Scope, or explicitly repair the binding. They must not automatically create a replacement Scope and overwrite
the binding. Direct lookups of missing Scopes still return 404 because an ID alone cannot establish whether the
Scope previously existed.
With enforced access, subject-source writes require contribution access to the bound target before returning this conflict;
callers without that permission receive 403 without the target ID.

## Enable access control

For shared or remote use, follow [deployment authentication](../operate/deploy-server.md#enable-authentication) and
set `POWERCONTEXT_SERVER_ACCESS_MODE=enforced`. The built-in static bearer token represents one administrator
Principal; it does not distinguish individual users. A multi-user deployment must supply an Authentication Provider
and an appropriate AccessControlService as described in [Configuration](../operate/configuration.md).

Inspect `GET /v1/access/me` for the active identity and provider capabilities. Roles and Bindings govern Server,
Scope, and Artifact actions. Scope creation requires `server.admin`; Prompt changes require current `scope.admin`.
The ability to read review candidates does not grant permission to approve them.

To let an Agent submit new Experience or Skill Candidates while a separate reviewer decides, assign
`scope.contributor` to the Agent and `scope.reviewer` to the reviewer using distinct authenticated identities.
Keep reviewer credentials out of the Agent's environment. A Principal holding both permissions can approve its own
Candidate. Follow [Separate Agent submission from review](review-candidates.md#separate-agent-submission-from-review)
for role-assignment examples, rejection and resubmission, and existing-Artifact permission limits.

Scheduled processing needs an authorized background Principal. Tag queries require `scope.read`, while editing a
target's tags follows that target's write permission. Use [Tags](manage-artifact-tags.md) and
[Scope profiles](use-profiles.md) for the corresponding workflows.

## Team access

Teams use the API, MCP, or host integrations with their own identities and permissions. The Dashboard is limited to
personal and demonstration deployments, with optional static token authentication. It is not a team RBAC interface; leave
`POWERCONTEXT_SERVER_DASHBOARD_ENABLED=false` in team deployments.

## Recover external ownership writes

When `create_server_app` composes the built-in Runtime with relationship/audit stores outside its business transaction,
supported creation hooks persist ownership intents in `pc_access_owner_recovery`. A failed delivery returns 503 even
when the domain object has committed. Keep the original database: the Server retries pending intents after restart.
Do not repeat a non-idempotent create blindly, assign the retrying user as owner, or edit an intent's payload.

An operator with access to the embedded application can inspect metadata and explicitly retry an accepted intent:

```python
recovery = app.state.owner_recovery
if recovery is not None:
    pending = await recovery.pending(scope_id=scope_id, limit=100)
    for operation in pending:
        await recovery.replay(operation.operation_id)

    after = None
    while True:
        page = await recovery.inventory_gaps(scope_id, kind="artifact", after=after, limit=100)
        # Export page.items only to the trusted operator's destination.
        after = page.after
        if after is None:
            break
```

`pending` also accepts an `after` operation ID for pagination. Use `kind="candidate"` to inventory proposed-owner
attestations. Inventory returns identity metadata, never Artifact or Candidate bodies. Its `after` cursor follows
scanned identities, so an empty `items` page may still have a continuation. `unknown` means no trusted intent exists;
`delivered_relation_missing` means a completed receipt no longer has its relation; `legacy_selector_required` means a
Memory collection needs an explicit entry inventory. These require an operator policy based on trusted original
evidence; replay does not accept a new owner or restore completed receipts whose external relation was deleted.
An existing independent read grant remains valid and does not fill the ownership gap.

External Artifact recovery requires event-idempotent `append_audit_once`; missing capability fails before the domain
commit. Relationship writers must enforce their existing immutable owner/key and exact Candidate target contracts.
Conflicts remain pending for investigation. Completed receipts are retained. Keep them with the business database's
backup and retention policy. No public recovery HTTP/MCP endpoint or CLI is provided.

The recovery tests use two separate real SQLite databases with injected delivery/acknowledgement failures, process
lifecycle restart, and concurrent replay. They do not establish acceptance for a remote relationship service,
OceanBase/MySQL, multiple Server nodes, or custom Runtime assemblies without commit hooks.
