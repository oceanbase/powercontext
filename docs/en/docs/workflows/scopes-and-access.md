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
3. Inspect the resolved Scope in the Dashboard or host diagnostics before saving project information.

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

## Read team content in the Dashboard

Members sign in to the same Server with their own credentials. Content and source requests use that identity without
borrowing administrator access. Both `GET /v1/scopes` and `GET /v1/scopes/default` currently require `server.observe`.
The selector is not a filtered list of the member's projects. Ordinary project members should use a link supplied by
an administrator: `/dashboard/home?scope=<scope_id>`.

| Existing access | Dashboard access |
| --- | --- |
| `scope.viewer` on one Scope | That Scope's memories, experiences, skills, handoffs, and usage; `server.observer` is not required |
| Only `scope.admin` or `server.admin` | Administration does not automatically grant content reading; the relevant read role is still required |
| A read role on one experience, skill, or handoff Artifact | Its exact detail link, including Scope, Artifact ID, and Revision; this does not grant a Scope directory or other records |
| Read access to a parent Scope only | Child Scope content requires a separate grant |

The Server default Scope is a deployment setting, not a personal default. Without `server.observe`, opening the home
page without a Scope returns an access error; this does not mean the member lacks project reading rights. An explicit
Scope link opens the authorized project, with a notice that the Scope list is unavailable. Observing the default Scope
still does not grant its content permissions. Do not broaden Server observation access just to make the selector work.

The sign-in form always returns to the home page without a Scope. Restricted members must reopen their project link
after signing in. Switching Scopes does not change the Server default, and Usage covers only the selected Scope.
The Dashboard has no per-member default project setting.

Reading a record does not necessarily permit reading its source material. When source access is denied, the record
remains readable and the source area shows an error. Subsequent API requests apply current permissions after a binding
changes. Administrators manage bindings through the existing Access API; the Dashboard has no grant management interface.
