---
title: "RFC 2857: PowerContext Organizations, Teams, and Projects"
description: "Define how organizations, teams, and projects relate, and describe the data structures, interfaces, and permissions needed to manage an organization."
---

- Proposal Name: `organization_team_and_project`
- Start Date: 2026-11-26
- RFC PR: [oceanbase/powercontext#2857](https://github.com/oceanbase/powercontext/pull/2857)

# Summary

This RFC defines the PowerContext model for organizations, teams, members, and projects. Organizations directly own teams
and projects. Teams manage members, and a member can participate in multiple projects within the same organization. In
the first version, each shared database serves one organization and can be used by multiple Servers. People using
PowerContext independently manage their own memory through the same Server and Scope interfaces.

# Motivation

Organizations need to manage membership and team rosters in one place. Team and project boundaries support different
working relationships. Moving to another team should leave a member's existing project participation intact. An
organization should be able to scale by adding Server instances while keeping membership, projects, and authorization
consistent.

# Guide-level explanation

## Deploy PowerContext for yourself

When Mei is the first person to sign up in an empty deployment, PowerContext creates an organization if she has no
organization invitation, joins her as an administrator, and makes it her default organization. She can organize notes
in the personal Scope assigned when she joins, and add teams and projects as needed. Scopes in existing deployments can
still be used as before.

## Join an organization

The organization gives administrators a place to admit users and manage membership. An administrator invites Mei by
email. If Mei already has an account, she can accept the pending invitation. If she has yet to create an account, she
can sign up with an invitation code. The pending invitation appears in the roster. Mei can begin working in the
organization once her membership becomes active.

## Create teams and assign project members

An administrator creates the Platform team and two projects, Search and Import. The team roster records which team Mei
belongs to. The organization owns both projects directly, and the administrator gives Mei access to each one separately.
Chen can join the same Platform team and participate only in Search.

```text
Organization
  +-- Platform team ---- roster: Mei, Chen
  +-- Search project --- participants: Mei, Chen
  +-- Import project --- participant: Mei
```

Mei can open either project, and Chen can open Search with the permissions he received. When Mei looks up or records a
decision in Search, project memory is one of the use cases. The detailed design describes the corresponding Scopes and
access rules.

## Move teams, leave a project, and remove a member

If Mei moves from Platform to another team, the administrator only needs to update her team assignment. She loses
access to Platform's team memory and keeps her existing project permissions. If Mei finishes her work on Import, the
administrator can revoke her access to that project separately. Removing Mei from the organization ends her access to its
teams and projects. Existing project materials stay in the original projects.

| Administrator action | Mei's team assignment | Mei's project access |
| --- | --- | --- |
| Move Mei to another team | Switches to the new team | Keeps access to Search and Import |
| Revoke Mei's access to Import | Keeps her current team | Keeps access to Search |
| Remove Mei from the organization | Clears her team assignment | Ends access to projects in the organization |

## Serve one organization from multiple Servers

An operator can add a second Server to serve the same organization. Both Servers share one OceanBase logical database.
Users see the same roster, projects, and authorization decisions from either Server. An administrator can issue an
invitation on one Server, and Mei can accept it on the other.

# Reference-level explanation

## Registration, default organization, and initial Scope

Registration and invitation acceptance use the existing account identity. An account with a valid organization
invitation joins the organization named in the invitation. When a new account registers without an organization
invitation and the logical database has no organization, the Server creates one and makes the registrant its first
administrator. If an organization already exists, registration returns `409` and requires an invitation from that
organization. An invalid, expired, or revoked organization invitation is rejected before organization creation can be
considered.

```text
Registration with an organization invitation --> join invited organization
Registration without an organization invitation
  +-- empty logical database -----------------> create organization + admin
  +-- organization already exists ------------> invitation required

Join or create --> activate membership + personal Scope --> first session
```

Both successful paths share the member activation flow. The membership, personal Scope, and Access binding are published
together, and retries reuse the same result. The system makes the organization just joined or created the account's
default organization. The first version has one organization per database, so the default is derived from the active
membership record without storing an additional field on the account. The client selects the member's personal Scope
in their first organization session. The deployment default Scope, existing bindings, and data are preserved.

Subsequent Scope selection follows the order in [RFC 1345](1345_scope_organization_and_agent_integration.md) of
explicit selection, persisted binding, and configured default Scope. Resolving the default organization or selecting a
Scope leaves read and write permissions subject to authorization.

## Organization boundary and shared database

The first version supports at most one organization per **logical database**. Multiple Servers can connect to the same
logical database on an OceanBase instance and serve the organization together. Server replicas share the deployment
identity, organization record, members, teams, projects, Scopes, and Access bindings. Servers connected to different
logical databases on the same physical instance use the configuration and data of their respective logical databases.

```text
Server A ----+
            +--> OceanBase logical database --> Organization
Server B ----+                                  +-- Teams
                                                +-- Projects
```

The first approved registrant without an organization invitation creates the organization in the shared database.
Concurrent initialization must return the same organization. Initialization fails if configuration differs.
Replicas read organization and role state from the shared database and use the same Access deployment identifier and
identity mapping. The identity of a replica process is excluded from organization, member, and Scope keys. A request
from the same account uses the same organization member record through either replica. Invitation code consumption,
member changes, and Scope allocation rely on shared database transactions and constraints. Replicas must use current
state to authorize requests. Once a member or role revocation commits, subsequent authorization on any replica must
see it. Migration is coordinated once per logical database using the existing
[shared database migration process](../docs/operate/database-migrations.md).

The account's default organization is resolved from its sole active membership in the deployment. The first version
derives this value and needs no switch endpoint. The organization slug in a route must identify the deployment's
organization. Persistence keys, queries, and authorization still use `org_id` explicitly. Teams and projects are
resolved within the organization, and members are identified by `(org_id, account_id)`. Account identity, the Access
deployment identifier, and the organization ID are stored separately. A future version can resolve the organization per
request and verify the account's membership in that organization. Existing organization IDs, records, and Scope IDs
keep their identities. Before serving multiple organizations, existing standalone Scopes, database queries, background
jobs, and other shared state need organization isolation. Existing standalone Scopes keep their current access paths in
the first version. Access to an organization-managed Scope must be denied if its ownership cannot be verified.

Shared database acceptance uses two Servers with the same deployment identifier. After creating a team and project on A,
B should discover the same IDs. Accepting an invitation through either replica should produce one member record and one
personal Scope. After A revokes permissions, the next request on B should be denied. After restarting B, it should
still resolve the same organization and Scopes.

## Use case for team, project, and personal memory

| Level | Scope allocation | Purpose |
| --- | --- | --- |
| Organization | No organization-level Scope in the first version | Manage accounts, members, teams, and projects. |
| Team | Allocate a Scope for each team | Store memory needed for the team's ongoing work. |
| Project | Allocate a Scope for each project | Store project memory for members of the organization who participate in the project. |
| Personal | Allocate a personal Scope for each member | Store personal memory. Read access is authorized separately from team or project relationships. |

```text
PowerContext Server
  +-- Organization
      +-- Platform team  ------> Team Scope
      +-- Search project ------> Project Scope
      +-- Mei ----------------> Personal Scope
```

Team, project, and personal Scopes each own their memory. Read and write permissions are checked independently of team
assignment, project participation, or Scope selection. Organization and member state determine whether the subject is
eligible to access this deployment. The target Scope and requested operation still require authorization.

## Data structures and constraints

The table below defines the first version's logical SQL schema. Each backend can map the physical types. IDs are
opaque strings, timestamps use UTC, and fields are non-null by default. A `?` marks a nullable field. Account IDs
come from the existing Server `PrincipalRef`. Account and credential storage stay with the existing identity system.

| Table | Columns | Keys and constraints |
| --- | --- | --- |
| `pc_organizations` | `org_id, slug, name, state, creator_id, created_at, updated_at` | Primary key `org_id`. Unique `slug`. Created by the first approved registrant without an organization invitation in an empty deployment. States are `initializing / active / removed`. |
| `pc_organization_members` | `member_id, org_id, account_id?, invite_email, kind, state, invited_at?, created_at, updated_at, team_id?, personal_scope_id?` | Primary key `member_id` and foreign key `org_id`. Unique `(org_id, account_id)` once the account is bound. Indexes on `account_id` and `(org_id, invite_email)`. An active member must have an account and a personal Scope. |
| `pc_invitation_codes` | `invitation_id, code, email, org_id, member_kind, expire_seconds, created_at, used_at?, deleted_at?` | Primary key `invitation_id`, unique `code`, and foreign key `org_id`. An organization registration code is valid only while unused, unrevoked, and unexpired. |
| `pc_teams` | `org_id, team_id, name, scope_id` | Primary key `(org_id, team_id)` and foreign key `org_id`. Unique `scope_id`. |
| `pc_projects` | `org_id, project_id, name, scope_id` | Primary key `(org_id, project_id)` and foreign key `org_id`. Unique `scope_id`. |

A member's `kind` is `admin` or `collaborator`, and their `state` is `invited / active / rejected / expired`.
The first version uses a nullable `team_id` on the member row for the primary team. The composite foreign key
`(org_id, team_id)` keeps the assignment within the organization. Project records use only `org_id` for organizational
ownership. In the first memory use case, existing direct user role bindings on a project's Scope provide the entire
project membership model.

Each team, project, and active member has its own Scope allocated through the existing Scope creation flow.
`personal_scope_id` is unique in the member table. All three types of binding reference existing Scope records.
The first version allocates new Scopes to these entities, and a Scope can only be assigned to one entity.
The Scope and its ownership record are published together. A Scope whose creation is incomplete stays out of all
discovery endpoints.

## Organization initialization and membership flows

The first registration without an organization invitation creates the organization in the initializing state and
establishes an administrator membership for the creator. Registration with an invitation and acceptance by an existing
account use the same member activation flow. Initial organization creation must satisfy the deployment's existing
bootstrap authorization.

```text
Invite a registered account --> pending membership --> accept / reject
Invite an unregistered email -> pending membership + signup code
                                                      |
                                                      v
                                           signup joins organization
```

The organization becomes active once the creator's member record and personal Scope are ready. Existing accounts only
act on pending records bound to their own identity. Acceptance makes the member active, and rejection marks the record
as rejected. A registration invitation code is valid for seven days, and the registering identity must match the invited
email. Consuming the code and activating the membership must succeed together. Retries and concurrent acceptance must
avoid duplicate members and personal Scopes. Removing a member deletes the membership. Removing an unregistered invitee
also revokes registration codes for that organization and email.

## HTTP API

The following paths are relative to `/v1` and all require authentication. The path parameter `slug` is the
organization slug, and `member_id` is the ID of a member record in the organization. JSON uses lower snake case field
names. A successful object or array response returns `200`. An empty array indicates no records, and `204` has no
response body. A `?` marks an optional request field.

| Method and path | JSON request body | Success response | Behavior |
| --- | --- | --- | --- |
| `GET /orgs/{slug}` | `—` | `Organization` | Active members read organization details. |
| `POST /orgs/{slug}/edit` | `{name?, slug?}` | `204` | Administrators update the supplied fields. |
| `GET /orgs/{slug}/members` | `—` | `Member[]` | Active members list the roster and member states. |
| `GET /orgs/{slug}/member-kinds` | `—` | `MemberKind[]` | Returns the fixed role options. |
| `POST /orgs/{slug}/members/invite` | `{email, org_member_kind}` | `204` | Administrators invite an existing account or unregistered email. |
| `POST /orgs/{slug}/members/{member_id}/edit` | `{member_kind}` | `204` | Administrators change another member's role. |
| `DELETE /orgs/{slug}/members/{member_id}` | `—` | `204` | Administrators remove another member or cancel an invitation. |
| `GET /my/orgs` | `—` | `Member[]` | Returns up to one current member record for the caller. |
| `GET /my/org-membership-requests` | `—` | `MembershipRequest[]` | Lists pending invitations addressed to the caller. |
| `POST /my/org-membership-requests/{slug}/accept` | `—` | `204` | Accepts the caller's own pending invitation. |
| `POST /my/org-membership-requests/{slug}/reject` | `—` | `204` | Rejects the caller's own pending invitation. |

Timestamps in the response structures below are RFC 3339 strings. IDs, names, emails, roles, and states are strings.
A `null` value for `account_id` indicates an invitee whose account has yet to be bound.

| Type | JSON fields |
| --- | --- |
| `Organization` | `name, member_count, state, created_at, updated_at`. The integer `member_count` counts the organization's member records, including pending invitations. |
| `Member` | `id, account_id, account_name, account_email, account_avatar_url, org_slug, org_name, org_state, member_kind, state, created_at, updated_at`. |
| `MembershipRequest` | `org_name, org_slug, account_id, state, created_at, updated_at`. |
| `MemberKind` | `value`, either `admin` or `collaborator`. |

Invalid parameters return `400`, missing authentication returns `401`, and insufficient permissions return `403`.
A missing target organization, member, or pending invitation addressed to the caller returns `404`. Inviting an
active member again returns `409`. Errors use the existing PowerContext HTTP error format.
The first registration flow creates the organization. Registration without an invitation also returns `409` when an
organization already exists. The first version needs no organization switch endpoint. Memory operations use the
existing Scope API.

### Team and project management interfaces

These interfaces manage only assignments and discovery. Memory for projects, teams, and individuals still uses the
existing Scope API. The `team_id` and `project_id` values used in these routes are opaque IDs within the deployment's
organization.

| Method and path | JSON request body | Success response | Behavior |
| --- | --- | --- | --- |
| `POST /orgs/{slug}/teams` | `{name}` | `201 Team` | Administrators create a team and its dedicated Scope. |
| `GET /orgs/{slug}/teams` | — | `Team[]` | Administrators list teams to manage rosters. Other active members discover only their own team. |
| `PUT /orgs/{slug}/members/{member_id}/team` | `{team_id: ID or null}` | `204` | Administrators set an active member's team. `null` clears the assignment. An unknown team or one from another organization returns `404`. |
| `POST /orgs/{slug}/projects` | `{name}` | `201 Project` | Administrators create a project and its dedicated Scope. |
| `GET /orgs/{slug}/projects` | — | `Project[]` | Administrators can manage project metadata. Other active members discover only the projects they have permission to read. |

`Team` returns `team_id, name, scope_id`, and `Project` returns `project_id, name, scope_id`.
All of those fields are non-null strings. The `Member` response adds a nullable `team_id`. Members can discover
Scopes they are authorized to access, including their organization-assigned personal Scope, through the existing
`POST /v1/access/resources/list` endpoint. A caller who can manage project metadata must still pass the data-plane
check to read the project's memory.

## RBAC, attribute checks, and existing Scope permissions

Active collaborators and administrators can read organization details. Editing the organization requires an active
administrator. The role determines the available operations. Authorization checks the account, target organization, and
member state. The slug in the route resolves the target organization.

```text
Authenticated account + organization slug
                    |
                    v
          Resolve organization ID
                    |
                    v
     Find membership for account + org
                    |
             active membership?
              /             \
            no               yes
           deny         read allowed
                              |
                        admin role?
                              |
                        yes: edit allowed
```

Server Access still evaluates existing PowerContext operations such as `scope.read` and `scope.contribute`.
Sources and Artifact revisions still belong to their Scope. The product organization manages membership. The Scope
`Organization Parent` organizes Scopes, and the parent relationship leaves read access subject to authorization.
[RFC 1345](1345_scope_organization_and_agent_integration.md)

Organization-managed Scopes use existing Access endpoints to create, replace, and revoke bindings.
Organization administrators can grant team Scope roles only to active members of the current team and project Scope
roles only to active members of the current organization. Grants must also satisfy existing Access management
permissions. An administrator who creates a team or project receives `scope.admin` for its Scope. Reading still
requires `scope.read`. When the initial member or a new member is activated, existing Access grants that member
`scope.contributor` for their personal Scope and publishes the binding with the member record.

## Access behavior for memory

- **Team.** An administrator creates a team and allocates a team Scope. Administrators can add active members to a
  team, remove them, or move them to another team. Moving teams updates only the team field on the organization
  member row. Existing project bindings are preserved. Team memory requires current team assignment and permission
  for the requested operation on its Scope.
- **Project.** An administrator creates a project and its Scope in the current organization. Eligible organization
  members can receive roles on multiple project Scopes through existing Access. Each memory operation uses a
  single, explicitly selected project. Scope authorization is evaluated for that operation and target.
- **Personal.** The initial administrator and each approved member receive a personal Scope, published when their
  membership becomes active. The data plane allows only the member to perform operations that Access authorizes in
  their personal Scope.
- **Member removal.** Revoke the member's Scope bindings in the organization before deleting the roster record.
  Once deletion takes effect, the membership check immediately denies subsequent access even if other resource bindings
  exist. Memory stays in its original Scope, and existing Scope ownership is preserved. A member who later rejoins
  needs new project permissions.
- **Access boundary.** The organization and member must both be active, and the operation must be authorized on the
  Scope. This check applies to reads, writes, discovery, and the Scope candidates considered by Prepare Context.
  Context References still require authorization. Managing the organization or roster leaves memory reads subject to
  the same permission check.

# Drawbacks

Hosting multiple organizations in the first version requires separate logical databases and Server deployment
contexts, which increases operational cost. A single organization can still add Server replicas.

# Rationale and alternatives

One organization per shared database supports multiple teams and projects within an organization and simplifies how
accounts select an organization. Hosting multiple organizations in one service requires resolving the organization on
each request, isolating member and discovery operations by organization, and checking that the target team, project,
personal Scope, and referenced resources all belong to that organization. Personal Scopes are identified by organization
and member, so the same person can have a separate personal Scope in each organization. Whether to allocate an
organization-level Scope depends on whether organization-wide memory is needed.

# Prior art

[RFC 1345](1345_scope_organization_and_agent_integration.md) defines Scope ownership of persistent memory.
Server Access already evaluates operation permissions on Scope resources.

# Unresolved questions

- Claiming existing standalone Scopes and handling retained personal Scopes after someone leaves the organization need
  separate lifecycle rules.
- The need for team management roles beyond administrators is left to future work.

# Future possibilities

A single service could host multiple organizations by reusing tables keyed by organization and stable IDs, and adding
request-level organization resolution and data isolation.
