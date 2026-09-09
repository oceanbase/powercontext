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

## Enable access control

For shared or remote use, follow [deployment authentication](../operate/deploy-server.md#enable-authentication) and
set `POWERCONTEXT_SERVER_ACCESS_MODE=enforced`. The built-in static bearer token represents one administrator
Principal; it does not distinguish individual users. A multi-user deployment must supply an Authentication Provider
and an appropriate AccessControlService as described in [Configuration](../operate/configuration.md).

Inspect `GET /v1/access/me` for the active identity and provider capabilities. Roles and Bindings govern Server,
Scope, and Artifact actions. Scope creation requires `server.admin`; Prompt changes require current `scope.admin`.
The ability to read review candidates does not grant permission to approve them.

Scheduled processing needs an authorized background Principal. Tag queries require `scope.read`, while editing a
target's tags follows that target's write permission. Use [Tags](manage-artifact-tags.md) and
[Scope profiles](use-profiles.md) for the corresponding workflows.

## Team access

Teams use the API, MCP, or host integrations with their own identities and permissions. The Dashboard is limited to
personal and demonstration deployments using a static token. It is not a team RBAC interface; leave
`POWERCONTEXT_SERVER_DASHBOARD_ENABLED=false` in team deployments.
