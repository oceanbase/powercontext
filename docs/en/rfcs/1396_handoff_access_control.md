- Proposal Name: `handoff_access_control`
- RFC Number: 1396
- Start Date: 2026-08-30
- Status: Draft
- RFC PR: [oceanbase/powercontext#1396](https://github.com/oceanbase/powercontext/pull/1396)
- Tracking Issue: [oceanbase/powercontext#1395](https://github.com/oceanbase/powercontext/issues/1395)
- Related RFCs: [RFC 0011](0011_remote_access_architecture.md), [RFC 0048](0048_handoff_artifact.md),
  [RFC 0050](0050_artifact_candidate_review_inbox.md), [RFC 0051](0051_experience_skill_artifact_families.md),
  [RFC 0082](0082_handoff_report.md), and [RFC 1223](1223_human_agent_work_continuity.md)

# Summary

This RFC defines an independent Access Control boundary, stable Resource Kinds, and an Artifact Family-driven Access
Profile contract for the PowerContext Server. Handoff is the first complete resource-level authorization scenario.
The RFC answers one concrete question—when user A transfers a Handoff to user B, what may B see and do, and how can
that access be revoked and audited—and specifies how later Artifact Families reuse the same Principal, action,
ResourceRef, Binding, PEP/PDP, listing, and audit semantics.

Handoff content does not store users, roles, or ACLs. `scope_id` remains the stable business partition for a
Workstream; it is not a user identity, tenant, role, or security boundary. Authentication and authorization happen at
the Server. Authentication establishes a trusted Principal. A Policy Enforcement Point (PEP) sends that Principal,
the action, and the resource to a replaceable `AuthorizationProvider` before it calls the existing Runtime application
service.

```text
Identity Provider or static credential
                |
                v
        Authenticated Principal
                |
                v
       PowerContext Server PEP
                |
                v
       AuthorizationProvider  <---->  Policy or relationship store
                |
          allow or deny
                |
                v
       Existing application service
```

The first version defines three stable Resource Kinds:

- `server`: the current PowerContext deployment;
- `scope`: one exact Workstream scope;
- `artifact`: an exact Artifact Revision or Family-owned selector interpreted by an Artifact Family Access Profile.

The `artifact` Resource Kind initially registers Artifact Family Access Profiles for `handoff`, `memory`,
`experience`, `skill`, and `prompt`. `ArtifactReference.family` is the only Profile discriminator. A client does not
submit a second content type that could conflict with it.

User A can collaborate in two ways:

- grant a Workstream role to a long-term collaborator; or
- grant B access to one exact persisted or approved resource.

The second option is the least-privilege path in the first version. B may read the shared exact resource and perform
only the actions defined by its Artifact Family Access Profile. An exact Handoff receiver may inspect the evidence
explicitly cited by that Handoff through its resolver and leave a Receipt for the same Revision. An exact Memory,
Artifact, or Prompt grant does
not open the rest of the scope, the current head, later Revisions, search results, or resources referenced by lineage.
Reading a Skill, publishing it to a target, and allowing a host to load or execute it are separate authorization
boundaries. An `accepted` Receipt, Artifact approval, Prompt read, or Skill publication never grants tools, network,
filesystem, model Provider, or credential access.

PowerContext defines a stable authorization request and decision, built-in roles, an Access API, and an OpenAPI
extension without requiring one policy engine. The first version provides a built-in Role Binding Store. Casbin,
OpenFGA, and Policy Decision Points (PDPs) compatible with the OpenID AuthZEN Authorization API can be integrated
through adapters.

# Motivation

PowerContext already has temporary Prepared Handoffs, immutable Handoff Revisions, Continue, Receipts, Task Outcomes,
Memory Entry Versions, approved Experience and managed Skill Revisions, and host-local Skill projections. The current
Server authentication model, however, is an optional global static Bearer token. A valid token can call every protected
operation. The Server cannot express that:

- A administers a Workstream while B can see only one transfer;
- B may acknowledge a transfer but may not publish another milestone;
- a team member may view a Handoff Report but may not approve an Experience or Skill;
- B may read one shared Memory Entry Version but may not search the scope or follow later versions;
- B may read an approved Experience or managed Skill Revision but may not review a Candidate;
- B may use one exact Prompt but may not silently promote it to a host system or developer instruction;
- a publisher may publish one exact managed Skill but cannot thereby modify its source Revision or gain host execution
  authority;
- a revoked receiver may not read later Revisions;
- HTTP, MCP, and the Dashboard make the same decision for the same Principal.

RFC 0048 requires a receiver to be able to read the Handoff's scope and evidence. Adding B to the complete scope meets
that requirement but exposes unrelated Memory, Sources, and history. Copying only the Handoff body to B loses exact
evidence, Receipts, and revocation.

The authorization check in RFC 1223's `acknowledge_handoff` is the receiver's observation about the live environment.
It answers whether the receiver currently appears able to continue. It does not authenticate B and is not an ACL. The
natural-language `receiver`, `authorization_notes`, or an instruction such as “continue this work” cannot be an access
credential either.

Handoff and other shareable resources therefore need an authorization layer independent of their content and the
Runtime domain API. That layer must support least-privilege sharing, team roles, external PDPs, safe listing, audit, and
fail-closed behavior without allowing an Agent, a request body, or `scope_id` to establish authority.

# Guide-level explanation

## Mental model: transfer content and transfer access are different

A Handoff answers “where is the work?” An Access Binding answers “who may do what with this transfer now?” They have
different lifecycles:

```text
Prepared Handoff -> Commit -> immutable Handoff Revision
                                  |
                                  +-> Access Binding for user B
                                           |
                                  read / inspect / acknowledge
                                           |
                                    expire or revoke
```

Committing a new Handoff does not share it automatically. Sharing does not change the Handoff content or Revision.
Revoking a Binding does not delete the Handoff, Receipt, or audit events.

## One Access Plane with Artifact Family-driven Profiles

The Access Control core answers only whether the current Principal may perform an action on an exact resource. A
Resource Kind defines the shape of an authorization object. An Artifact Family Access Profile defines the
authorization semantics for one kind of content:

```text
Protected Resource
├── server
├── scope
├── artifact
│   ├── family=handoff
│   ├── family=memory
│   ├── family=experience
│   ├── family=skill
│   └── family=prompt
```

Each Artifact Family Access Profile must define:

| Family profile contract | Required definition |
| --- | --- |
| share unit | Whether the grant covers an exact Revision or a Family-owned exact selector |
| shareable state | Which lifecycle states, such as committed, approved, or retained, allow Binding creation |
| parent | How scope- or server-level roles imply child-resource actions in one direction |
| actions | Stable actions for reading, using, acknowledging, publishing, and administration |
| grantable roles | Fixed roles that may bind to the resource and who may create those Bindings |
| resolution | Operations that can resolve the resource from a validated request and what they may not read first |
| listing | How an exact grant is discovered and which aggregate lists still require scope or server authority |
| transitivity | Whether reading the resource also reads lineage, citations, or other related resources |

All Families reuse the same `/v1/access/*` API. They do not add parallel authorization endpoints such as
`/memory/share`, `/experience/share`, `/skill/share`, or `/prompt/share`. A new exact-read Family that reuses
`artifact.read` does not require another ResourceRef variant, but it must be registered explicitly. A Family that
introduces a semantic action, selector, or role must update OpenAPI, the fixed action and role vocabulary, Server-owned
resolvers, Provider conformance vectors, and generated transport artifacts together. Unknown Families are not
shareable by default.

Resource visibility, context selection, and external execution authority are separate planes:

```text
Access Plane:      Which exact resource the Principal may read or use
Context Plane:     Which authorized content enters bounded PreparedContext after explicit selection
Execution Plane:   Whether a host installs, loads, or executes a Skill or Prompt and which tools it may use
```

An allow decision does not propagate across planes. An exact Memory, Artifact, or Prompt grant does not place content
in normal scope recall automatically. A receiver first discovers it in a “Shared with me” view, then explicitly reads
it, attaches it to the current task, or forks it into a scope where the receiver may contribute. Shared content remains
`untrusted_history` or untrusted instruction; Context builders and hosts still enforce their own budgets, precedence,
approval, and sandbox policy.

## A transfers one exact Handoff to B

Assume A administers the `project:payments` Workstream and has prepared a transfer. The normal flow is:

1. A inspects and commits the Prepared Handoff, producing an immutable `ArtifactReference`:

   ```json
   {
     "family": "handoff",
     "artifact_id": "project:payments",
     "revision": 12
   }
   ```

2. A explicitly selects B. The Dashboard or integration resolves B through the deployment's identity directory to a
   trusted canonical Principal. Model output, a display name, or email text cannot replace this resolution.
3. The Server checks whether A has `scope.delegate` on `project:payments`.
4. The Server creates an Access Binding with the `handoff.receiver` role for that exact Revision and optionally sets an
   expiration time.
5. B signs in using B's own credential. `resources/list` returns exact Handoffs B may read. B never receives A's token
   or a new bearer share link.
6. B calls Continue with an exact selection. The Server reads the same Revision and resolves only the evidence it
   explicitly cites.
7. After checking the live workspace, capability, and authorization state, B may leave an `accepted`,
   `needs_clarification`, or `declined` Receipt for the same Revision.

An example Binding creation request is:

```json
{
  "subject": {
    "type": "user",
    "issuer": "https://id.example.com/",
    "id": "00u-bob"
  },
  "resource": {
    "type": "artifact",
    "scope_id": "project:payments",
    "reference": {
      "family": "handoff",
      "artifact_id": "project:payments",
      "revision": 12
    }
  },
  "role": "handoff.receiver",
  "expires_at": "2026-09-06T12:00:00Z",
  "reason": "Continue the payment retry investigation",
  "idempotency_key": "transfer-payments-12-to-bob"
}
```

The Server supplies `granted_by`, creation time, and policy revision. The caller cannot assert them.

## What B can see

`handoff.receiver` is an exact-resource role, not a scope role:

| Operation | Result | Reason |
| --- | --- | --- |
| Read Handoff Revision 12 | Allowed | The Binding identifies this exact Revision |
| Inspect the citations of Revision 12 through Continue | Allowed | `handoff.evidence.read` covers only this Revision's citation manifest |
| Acknowledge Revision 12 | Allowed | A receiver may leave a Receipt for the exact Handoff it inspected |
| Request `latest` | Denied | Latest may be a later Revision that was never granted to B |
| Read Revision 11 or 13 | Denied | An exact Binding does not inherit to adjacent Revisions |
| Open the aggregate Handoff Report | Denied | The Report contains scope-level history and statistics |
| Search scope Memory or list Sources | Denied | A Handoff Binding does not grant general scope read |
| Commit a Handoff or record a Task Outcome | Denied | Those operations require `scope.contribute` |
| Approve a Candidate | Denied | Approval requires independent `scope.review` authority |

Least-privilege evidence access does not copy each Source or Memory item, and it does not require an external PDP to
store every citation. The Server first builds the exact Handoff `ArtifactResourceRef` from the validated request and
checks both `artifact.read` and `handoff.evidence.read` for B. Only when both decisions allow access may it read the
immutable Handoff Revision, obtain its citation manifest, and dereference exact citations in that manifest through the
Handoff resolver. B cannot reuse that permission by placing an arbitrary Source ID in a general read API.

If a citation has been deleted, retired, corrupted, or denied by a higher-order policy, Continue marks the
corresponding evidence unavailable. A Handoff Binding does not override retention, legal hold, data classification, or
an explicit deny policy.

## Sharing other Artifact Families

Other Artifact Families use the same exact-share flow without inheriting Handoff evidence or Receipt semantics:

1. A selects an exact persisted resource that can be authorized. Memory uses a complete `MemoryCitation`; Experience,
   managed Skill, and Prompt use an `ArtifactReference` with a positive integer Revision.
2. The Server checks whether A may create the relevant Binding in the resource's scope, then verifies that the resource
   exists and is in a shareable state.
3. B discovers the exact resource through `access/resources/list` and reads or explicitly uses it as B's own Principal.
4. To modify or maintain the content, B explicitly forks or proposes a Candidate in a scope where B has
   `scope.contribute`. The original resource and Binding do not change.

First-version exact grants behave as follows:

| Family role | Allows | Does not allow |
| --- | --- | --- |
| `artifact.viewer` on a `family=memory` selector | Exact get of one `entry_version_id` | Search, list, changes, current head, revise, retire, or another entry/version |
| `artifact.viewer` | Exact get of one approved Experience or managed Skill Revision | Candidate read/review, later Revisions, publication, or lineage bodies |
| `artifact.viewer` on `family=prompt` | Exact get of one approved Prompt Revision | Render/use, later Revisions, or automatic injection |
| `prompt.user` | `artifact.viewer` plus explicit render/use | Changing instruction priority, enabling tools, or reading credentials |

Ordinary user input remains Source evidence; the word “prompt” in its content does not make it a Prompt Artifact. A later
Prompt Artifact lifecycle may define reusable parameterized task templates. Internal prompts for Memory extraction,
Experience or Skill generation, and Handoff generation are Server implementation or configuration managed by
`server.admin`; they are not shared through `family=prompt` Artifact Bindings. Content that tells an Agent when to
apply a capability, how to perform it, and how to validate it should be a managed Skill rather than a duplicate Prompt
Artifact.

An exact resource response may return lineage or citation identities defined by its schema, but the grant does not
propagate to those referenced resources. A general Source, Memory, or Artifact get still requires an independent
decision for the target. A Provider must not create `can_read` inheritance merely because “A references B.”

## Sharing is a read-only snapshot, not collaborative editing

An exact-resource Binding grants only read, explicit use, or a controlled publication operation to a Server-configured
target. It does not transfer content authority over the original resource. The Binding itself cannot authorize the
receiver to revise, retire, replace, commit a later Revision, or overwrite the shared content in place. If the receiver
separately has `scope.contribute` or stronger authority in the original scope, that write authority comes from the
independent scope role, not from the share.

State produced by the receiver remains separate from the shared original:

| Receiver operation | Constraint |
| --- | --- |
| Acknowledge a Handoff | Creates a separate Receipt and does not modify the Handoff Revision |
| Submit feedback or a change request | Creates separate feedback or a change request and does not modify shared content |
| Publish a managed Skill | Writes projection or state to a Server-configured target and does not modify the source Skill Revision |
| Fork, import, or copy | Requires `scope.contribute` on the destination scope; creates a new identity or Candidate with lineage to the original |

Product surfaces should offer actions such as “View,” “Use,” “Acknowledge,” “Request changes,” “Copy to my scope,” or
“Publish to configured target.” They should not present an exact share as “Edit shared content.” Ongoing co-maintenance
requires a separate scope role. For an Artifact Family with Review, a contributor still creates a Candidate and uses
the Review lifecycle to produce a new Revision instead of editing an approved Revision in place. Revocation prevents
later access, but it cannot erase content already seen by the receiver or automatically revoke a Receipt, projection,
or fork that was previously created under independent authority.

## Publishing a managed Skill

Reading Skill content and publishing it to a configured host-local Agent target are different operations. A
publication request accepts only an exact managed Skill `ArtifactReference` and an opaque Server-configured
`target_id`. It does not accept a destination path, Agent home, SSH credential, or arbitrary filesystem locator.
Before it reads the Skill body, resolves `target_id`, inspects target host state, or writes a projection, the Server
must obtain both allow decisions on the same exact Skill Artifact:

```text
artifact.read AND skill.publish on exact family=skill Artifact
```

`skill.publisher` binds only to one exact managed Skill Revision and grants both actions. `target_id` is an opaque
operation parameter configured by `server.admin`, not a `ResourceRef`, Access Binding, or authorization resource in
`/access/resources/list`. Only after authorization may the Server confirm that `target_id` is registered and resolve it
to host-local Agent projection configuration. An unknown or disabled target rejects publication. Host IDs,
destination paths, Agent homes, credential references, and locators do not enter the request, Binding, ordinary audit,
or public errors.

An ordinary publisher selects a target through `POST /v1/skills/publication-targets/list`. The request contains the
`scope_id` and exact Skill `ArtifactReference`, and the Server reuses the two requirements above. It reads the Skill
Repository and target registry only after every decision allows access. The response lists only enabled targets and
their opaque `target_id`, Agent kind, installation scope, and safe capabilities. It does not return desired or applied
state, host paths, Agent homes, credential references, or underlying errors. This operation belongs to the Skill
publication domain contract; it is not Access Resource listing and creates no target Binding.

```json
{
  "scope_id": "project:payments",
  "artifact": {"family": "skill", "artifact_id": "retry-runbook", "revision": 4}
}
```

```json
{
  "artifact": {"family": "skill", "artifact_id": "retry-runbook", "revision": 4},
  "targets": [
    {
      "target_id": "codex-project",
      "agent_kind": "codex",
      "installation_scope": "project",
      "capabilities": ["publish"]
    }
  ]
}
```

The first version does not support per-target delegation. A Principal with `skill.publisher` on an exact Skill may
publish that Revision to any enabled configured target in the current deployment. Only `server.admin` may configure,
change, or remove targets. Target status is operational information protected by `server.observe` or `server.admin`.
If the product must express “B may publish to X but not Y,” a separate distribution RFC introduces a generic
`execution_target` Resource instead of mixing a Skill-specific target into the Artifact sharing model.

Successful publication means only that the configured host-local target projection received the exact Revision. It
does not authorize the host to load or execute the Skill or to access tools, networks, filesystems, or secrets.
External Skill registrations and host-local locators are not cross-host shareable Artifact Family Access Profiles.
Collaboration requires an explicit import or fork into a managed Skill. Remote Receiver distribution is outside the
first version.

## B takes over the Workstream

Seeing a transfer does not grant execution authority. If B will work on the Workstream over time, A or an administrator
must separately grant `scope.contributor`:

```text
handoff.receiver
  = read one exact Handoff + inspect its citations + acknowledge it

scope.contributor
  = read the Workstream + contribute Sources + prepare/commit Handoffs
    + acknowledge Handoffs + record Task Outcomes
```

PowerContext authorization governs only PowerContext resources and operations. The host, operating system, and
external services still govern Git changes, cloud APIs, production access, and credentials. A Handoff, Role Binding,
or Receipt cannot enlarge those permissions.

## Long-term team collaboration

A stable team can receive scope roles instead of a new Binding for each Revision:

- `scope.viewer` reads Handoffs, Memory, approved Artifacts, Prompts, Sources, and read-only projections in the current
  scope and may explicitly use approved Prompts;
- `scope.contributor` writes work evidence, Memory contributions, Handoffs, and Outcomes and proposes Artifact or Prompt
  Candidates in addition to viewer access;
- `scope.reviewer` reviews Artifact Candidates in addition to viewer access;
- `scope.delegator` shares exact Handoffs with receivers in addition to viewer access;
- `scope.admin` administers all roles and policies for the scope.

`scope.delegate` continues to authorize only viewer or receiver Bindings for `family=handoff` Artifacts in this RFC. In
the first version, only `scope.admin` may create exact Bindings for other Artifact Families. An existing Handoff
delegator does not silently gain a wider sharing boundary. A later resource-specific delegation action is an explicit
wire-contract change. `server.admin` manages publication targets through deployment configuration; targets do not
receive Access Bindings.

These fixed roles are wire-contract vocabulary. An external PDP does not have to persist the same role names. It may
map organization roles, teams, or relationships to these actions.

## Revocation and expiration

A, the applicable grant administrator, or a scope administrator can revoke an exact Artifact Binding within its
administration boundary. For a Handoff, after revocation:

- B's later read, Continue, and acknowledge requests return 403;
- B no longer sees the Handoff in `resources/list`;
- the saved Handoff, Receipt, and Access Audit remain intact;
- content already displayed, exported, or copied by B cannot be recalled remotely.

The PDP evaluates expiration against trusted Server time. If an adapter cannot enforce conditions or expiration, it
must reject creation of an expiring Binding instead of silently creating permanent access.

A role change uses revoke + create rather than updating `handoff.viewer` in place to `handoff.receiver`. Revocation
uses `expected_version`; a concurrent change returns 409.

## The authorization service is unavailable

Authorization is a security dependency. In enforced mode:

- a missing or unverifiable identity returns 401;
- a valid identity with insufficient authority returns 403;
- an unavailable PDP, Binding Store, or safe resource filter returns 503;
- the Server does not fall back to a global token, an empty Principal, or allow-all when a PDP fails;
- `/health/live` still reports process liveness while `/health/ready` reports the required authorization dependency as
  not ready.

A 403 response does not distinguish “the resource does not exist” from “the resource exists but is not visible.” The
Repository may return 404 only after authorization succeeds, preventing resource enumeration.

# Reference-level explanation

## Goals and non-goals

This RFC aims to:

- establish one Server PEP in front of HTTP, MCP, and the Dashboard;
- establish a Principal from a credential without allowing the request to override it;
- support scope-level RBAC and exact Handoff receiver Bindings;
- define stable Resource Kinds and an Artifact Family Access Profile contract, with exact authorization for Handoff,
  Memory, Experience, Skill, and Prompt resources;
- resolve evidence cited by an exact Handoff safely without opening the complete scope;
- separate resource reads, context selection, Skill publication, and host execution authority;
- provide a replaceable decision interface and an optional relationship mutation interface;
- provide APIs for self-checks, resource discovery, Binding administration, and audit;
- fail closed for direct reads, lists, pagination, the internal MCP bridge, and background operations;
- preserve the domain purity of the current Runtime, Source, Memory, Handoff, and Work application APIs.

This RFC does not define:

- user registration, passwords, MFA, an OIDC Provider, or token issuance;
- a custom role DSL, wildcard scopes, organization hierarchy, or a group directory;
- anonymous bearer share links or authority embedded in Handoff content;
- authorization for Git, filesystems, tools, networks, model Providers, or credentials;
- redaction, cross-organization export, legal hold, or retention policy;
- approval workflows, temporary elevation, or an Agent requesting more authority automatically;
- PowerContext as a general-purpose IAM product;
- multi-writer collaborative editing of an exact shared resource or ownership transfer through a Binding;
- dynamic subscription sharing for Memory collections, Artifact catalogs, or resources that follow `latest`;
- the Prompt Artifact content schema, variable language, Review lifecycle, or host instruction-precedence policy;
- per-target publication delegation or a general `execution_target` Resource;
- remote managed Skill projection or a Receiver distribution contract; or
- cross-host locators, automatic installation, or package distribution for External Skills.

## Trust model and invariants

An implementation must preserve these invariants:

1. `scope_id` is a business partition value, not proof of authority.
2. A Principal comes only from authentication middleware or trusted internal bridge context.
3. A `receiver`, `subject`, `actor`, role string, or Handoff prose in a request body cannot replace the current
   Principal.
4. Handoff, Memory, Artifact, and Prompt content is `untrusted_history` or untrusted instruction and cannot grant an
   action.
5. `is_internal_bridge()` may skip repeated transport authentication but never authorization.
6. Every protected operation receives a decision before it accesses a Repository or application service.
7. An exact Handoff grant does not allow `latest` and does not cover other Revisions of the same Artifact.
8. An `accepted` Receipt does not create, update, or inherit an Access Binding.
9. A model may suggest a receiver or explain a denial, but it cannot choose a canonical Principal or invoke an
   allow-all fallback.
10. An exact Memory Entry grant consists of an exact `family=memory` `ArtifactReference` and a complete `memory_entry`
    selector. Every other exact Artifact grant contains a positive integer Revision; none allows `latest` or inherits
    to later Revisions. The Server derives the Access Profile only from `ArtifactReference.family`; it rejects an
    independent content profile, an unknown Family, or a selector mismatch.
11. Reading Memory, Artifact, or Prompt content does not grant its lineage or citation targets and does not place it in
    PreparedContext automatically.
12. An exact-resource Binding does not grant revise, retire, replace, commit-next-Revision, or any other mutation of
    shared content. Receipts, feedback, projections, and forks are separate resources or operations that require
    independent authorization and do not modify the original resource identity, content, or Revision.
13. `prompt.use` does not change host instruction precedence. `skill.publish` does not authorize host loading,
    execution, tools, networks, filesystems, or secrets.
14. Skill publication requires both `artifact.read` and `skill.publish` on the exact `family=skill` Artifact before
    resolving `target_id` or performing any host or filesystem inspection. `target_id` is not an authorization
    resource, and the first version resolves only configured host-local targets.
15. Public errors, logs, metrics, and traces do not contain credentials, Handoff, Memory, Artifact, or Prompt content,
    Source bodies, target locators, or raw PDP responses.

## Principal model

`PrincipalRef` uses the stable opaque identity established by an authentication Provider:

```json
{
  "type": "user",
  "issuer": "https://id.example.com/",
  "id": "00u-bob"
}
```

The fields mean:

| Field | Semantics |
| --- | --- |
| `type` | `user`, `service`, or a later registered Principal type |
| `issuer` | The trusted issuer that established the identity; local credentials use a deployment-specific issuer |
| `id` | A stable opaque subject within that issuer, not a display name or email address |

Agent names, hosts, session IDs, and model names are provenance, not Principals by default. When an enterprise token
proves an on-behalf-of actor, an authentication adapter may add that actor to trusted request context; a PDP may then
constrain both subject and actor. A client cannot assert that actor in a JSON body.

The existing Handoff Receipt `receiver` remains record content. The Server separately records the authenticated
Principal that produced the Receipt. If they differ, the Server rejects `accepted` or explicitly records the mismatch
for a non-accepted Receipt. It never treats the free-form `receiver` as a Principal.

## Resource model

Internal authorization requests use structured `ResourceRef` values. This avoids concatenating identifiers that may
contain `:`, `/`, or user data into policy strings:

| Resource Kind | Identity | Parent |
| --- | --- | --- |
| `server` | Deployment identifier | None |
| `scope` | Exact `scope_id` | Server |
| `artifact` | Exact `ArtifactReference`, optional Family-owned selector, and `scope_id` | Scope |

`ResourceRef` is an OpenAPI discriminated union. Each variant uses `additionalProperties: false` and accepts only these
fields:

| `type` | Required identity fields |
| --- | --- |
| `server` | `deployment_id` |
| `scope` | `scope_id` |
| `artifact` | `scope_id`, `reference`, and optional `selector` |

An ordinary Artifact Revision has no selector:

```json
{
  "type": "artifact",
  "scope_id": "project:payments",
  "reference": {"family": "experience", "artifact_id": "exp-retry-budget", "revision": 3}
}
```

Memory Entry uses an exact selector owned by the `memory` Family. The combination of `reference` and `selector` is a
complete `MemoryCitation`:

```json
{
  "type": "artifact",
  "scope_id": "project:payments",
  "reference": {"family": "memory", "artifact_id": "memory", "revision": 18},
  "selector": {
    "type": "memory_entry",
    "entry_id": "retry-policy",
    "entry_version_id": "01K..."
  }
}
```

`ArtifactResourceRef.reference.family` is the only Artifact Family Access Profile discriminator. A request contains no
separate `profile` field. The Server derives the Profile from the validated exact `ArtifactReference`, avoiding
conflicts such as `profile=prompt` with `family=skill`. Each Family declares its selector required, forbidden, or one
specific discriminated-union variant. The first version requires a `memory_entry` selector for `memory` and forbids a
selector for `handoff`, `experience`, `skill`, and `prompt`.

The Family registry is a fixed Server-owned contract, not an administrator-editable policy DSL. Every registration
contains at least:

| Field | Requirement |
| --- | --- |
| `family` | Stable name that exactly matches `ArtifactReference.family` |
| `share_unit` | `revision` or one explicit Family-owned selector type |
| `shareable_states` | Lifecycle states in which a Binding may be created |
| `base_action` | `artifact.read` in the first version |
| `additional_actions` | Family-specific use, acknowledge, or publish actions |
| `grantable_roles` | Fixed exact roles compatible with the Family |
| `parent_implications` | Child actions implied by scope roles in one direction |
| `transitivity` | Whether lineage, citations, or other related resources need separate decisions; the default is none |
| `resolver` | How to resolve the exact resource after authorization and which safe identity to return |

The first-version registry is:

| Artifact Family | Share unit | Shareable state | Exact actions | Grantable exact roles |
| --- | --- | --- | --- | --- |
| `handoff` | Revision | committed | `artifact.read`, `handoff.evidence.read`, `handoff.acknowledge` | `handoff.viewer`, `handoff.receiver` |
| `memory` | `memory_entry` selector | active in the referenced Revision | `artifact.read` | `artifact.viewer` |
| `experience` | Revision | approved | `artifact.read` | `artifact.viewer` |
| `skill` | Revision | approved | `artifact.read`, `skill.publish` | `artifact.viewer`, `skill.publisher` |
| `prompt` | Revision | approved | `artifact.read`, `prompt.use` | `artifact.viewer`, `prompt.user` |

A Prepared Handoff has no persistent identity and cannot receive an exact Access Binding. A least-privilege cross-user
transfer must be committed first. A pending or rejected Candidate likewise cannot receive an Artifact Binding. Even a
new Family that reuses only `artifact.read` must be registered explicitly as shareable. Unknown, disabled, or
selector-incompatible Families are denied by default. `revision=latest`, an `entry_id` alone, a Memory current head, or
a search query is not a stable authorization identity. Later Artifact Revisions and Memory Entry Versions do not
inherit an exact Binding.

Each Resource Kind defines a stable canonical serialization for adapter object IDs. An Artifact key includes
`scope_id`, `family`, `artifact_id`, a positive integer `revision`, and the complete selector. The same business
identity produces the same key over HTTP, MCP, and the Dashboard. Different Families or selectors cannot share a
Binding through string collisions.

An adapter maps a structured ResourceRef to an external PDP object ID. The mapping must be canonical and stable, and
must not write email addresses, tokens, resource content, publication target locators, or other PII into Casbin policy,
OpenFGA tuples, or audit keys.

## Action vocabulary

First-version actions are stable lowercase dotted strings:

| Action | Resource | Meaning |
| --- | --- | --- |
| `server.observe` | server | Read service-level operations and observability data |
| `server.admin` | server | Administer deployment access and publication-target configuration |
| `scope.read` | scope | Read general resources, approved content, and projections in a Workstream |
| `scope.contribute` | scope | Write Sources, Memory contributions, Handoffs/Outcomes, and propose Artifact/Prompt Candidates |
| `scope.review` | scope | Review Artifact Candidates in the scope |
| `scope.delegate` | scope | Create viewer or receiver Bindings for exact Handoffs |
| `scope.admin` | scope | Administer roles, Bindings, and policy for the scope |
| `artifact.read` | exact artifact | Read the exact Revision or selector defined by its Family Profile |
| `handoff.evidence.read` | `family=handoff` artifact | Resolve that Revision's citation manifest through the Handoff resolver |
| `handoff.acknowledge` | `family=handoff` artifact | Create a Handoff Receipt for that Revision |
| `prompt.use` | `family=prompt` artifact | Explicitly render or attach an authorized Prompt without deciding host instruction precedence |
| `skill.publish` | `family=skill` artifact | Discover safe target choices and select one exact managed Skill Revision for publication |

`artifact.read` has one meaning across every Family: read only the exact Revision or selector named by the Binding. It
does not include Handoff evidence, Prompt use, Skill publication, lineage bodies, or any mutation. A Family adds a
semantic action only for an operation with a genuinely different security effect.

Business operations check actions rather than role names. External role and relationship models can therefore evolve
without changing application code.

Policy may make `scope.read` imply `artifact.read` for every registered Family, `handoff.evidence.read` for Handoffs,
and `prompt.use` for Prompts under the scope. `scope.contribute` may imply acknowledge, prepare, commit, Memory
contribution, Artifact or Prompt Candidate proposal, and Outcome writes. The reverse implication never holds: an exact
viewer or user role does not gain `scope.read` or `scope.contribute`.
`scope.read` does not imply `skill.publish`.

## Built-in roles

| Role | Granted actions |
| --- | --- |
| `handoff.viewer` | `artifact.read`, `handoff.evidence.read` on one exact `family=handoff` Artifact |
| `handoff.receiver` | Viewer actions plus `handoff.acknowledge` on one exact Handoff |
| `artifact.viewer` | `artifact.read` on one compatible exact Artifact Revision or selector |
| `prompt.user` | `artifact.read`, `prompt.use` on one exact `family=prompt` Artifact |
| `skill.publisher` | `artifact.read`, `skill.publish` on one exact managed Skill Revision |
| `scope.viewer` | `scope.read` |
| `scope.contributor` | `scope.read`, `scope.contribute` |
| `scope.reviewer` | `scope.read`, `scope.review` |
| `scope.delegator` | `scope.read`, `scope.delegate` |
| `scope.admin` | Every scope and child Artifact Family action, including delegation and Binding administration |
| `server.observer` | `server.observe` |
| `server.admin` | Every server, scope, and Artifact Family action |

Every exact-resource role is read-only with respect to its bound content. `handoff.receiver` adds only the creation of
a separate Receipt. `skill.publisher` adds only a projection write to a Server-configured target. Neither
role may modify the source Handoff or Skill Revision. Mutation of the original resource requires an independent scope
role and the relevant domain lifecycle.

The first version does not allow the public API to create roles or change role-to-action mappings. Fixed roles give
OpenAPI, the Dashboard, and adapter conformance tests stable semantics. An enterprise PDP may map custom organization
roles to the actions externally.

A Principal with `scope.delegate` may create only `handoff.viewer` or `handoff.receiver`, and only for an existing
exact Handoff in that scope. Creating a scope role requires `scope.admin`. Creating `server.admin` requires an existing
`server.admin` and permission from deployment policy. A Principal cannot grant itself authority beyond the caller's
administration boundary.

In the first version, only `scope.admin` may create `artifact.viewer`, `prompt.user`, or `skill.publisher` Bindings in
an administered scope. `artifact.viewer` may bind only to an exact Revision or selector declared compatible by the
Family registry. `prompt.user` and `skill.publisher` may bind only to approved `family=prompt` and `family=skill`
Artifacts, respectively. A role and Artifact Family Access Profile or Resource Kind mismatch returns 422; insufficient
authority returns 403. The
Server must not forward an incompatible role string unchanged to an external RelationshipWriter.

| Resource or Artifact Family Profile | Grantable exact roles | Binding administrator |
| --- | --- | --- |
| `artifact` with `family=handoff` | `handoff.viewer`, `handoff.receiver` | `scope.delegate`, `scope.admin`, or `server.admin` |
| `artifact` with `family=memory` and a `memory_entry` selector | `artifact.viewer` | `scope.admin` or `server.admin` |
| `artifact` with `family=experience` | `artifact.viewer` | `scope.admin` or `server.admin` |
| `artifact` with `family=skill` | `artifact.viewer`, `skill.publisher` | `scope.admin` or `server.admin` |
| `artifact` with `family=prompt` | `artifact.viewer`, `prompt.user` | `scope.admin` or `server.admin` |

## Authorization request and decision

The PowerContext decision model aligns with the subject, action, resource, and context shape of the OpenID AuthZEN
Authorization API, but the Python protocol does not require an HTTP PDP:

```python
class AuthorizationProvider(Protocol):
    async def check(self, request: AccessRequest, /) -> AccessDecision: ...

    async def check_batch(
        self,
        requests: Sequence[AccessRequest],
        /,
    ) -> Sequence[AccessDecision]: ...

    async def resolve_resource_filter(
        self,
        request: ResourceSearchRequest,
        /,
    ) -> AuthorizedResourceFilter: ...
```

A normalized request is:

```json
{
  "subject": {
    "type": "user",
    "issuer": "https://id.example.com/",
    "id": "00u-bob"
  },
  "action": {"name": "artifact.read"},
  "resource": {
    "type": "artifact",
    "scope_id": "project:payments",
    "reference": {
      "family": "handoff",
      "artifact_id": "project:payments",
      "revision": 12
    }
  },
  "context": {
    "request_id": "pc-01K...",
    "transport": "mcp"
  }
}
```

`AccessDecision` contains at least:

```json
{
  "allowed": true,
  "reason_code": "role_binding",
  "policy_revision": "42"
}
```

`reason_code` is a stable, low-sensitivity enum for audit and diagnostics. A business 403 response does not expose a
provider rule, tuple, URL, stack, or raw body. `policy_revision` correlates audit and cache behavior to a defined
policy; it is not an authorization token.

`check_batch` preserves input order and returns one decision for each item. An adapter cannot use one allowed item to
permit a complete batch.

A business operation may resolve to one or more `ResolvedAccessRequirement` values. The first version supports only
the `all` combination. The PEP uses one `check_batch`, or semantically equivalent point checks, and calls no Repository,
application service, target adapter, or filesystem unless every decision allows access. This is not a client-authored
Boolean policy DSL.

For example, managed Skill publication resolves to:

```json
{
  "combination": "all",
  "requirements": [
    {
      "action": {"name": "artifact.read"},
      "resource": {
        "type": "artifact",
        "scope_id": "project:payments",
        "reference": {"family": "skill", "artifact_id": "retry-runbook", "revision": 4}
      }
    },
    {
      "action": {"name": "skill.publish"},
      "resource": {
        "type": "artifact",
        "scope_id": "project:payments",
        "reference": {"family": "skill", "artifact_id": "retry-runbook", "revision": 4}
      }
    }
  ]
}
```

The business request's `target_id` does not enter these requirements. The Server resolves that parameter only after
both decisions allow access.

Alternatives such as “scope role or exact role” do not require an `any` expression. The PEP requests the child-resource
action. A Provider uses a trusted parent relationship to decide whether a scope role implies that action, while an exact
Binding applies directly to the child. Providers therefore do not need an arbitrary nested policy expression language.

`resolve_resource_filter` is required for safe list operations. An `AuthorizedResourceFilter` is specific to the
current Principal and action. It contains bounded canonical resource keys produced by exact Bindings and bounded
server or scope constraints produced by parent roles. A parent constraint means that a Repository may query only
within that parent, requested Resource Kind, and Family; it is not a client-authored wildcard. The filter also carries
the policy revision. The Server validates its structure and bounds, then pushes the union of exact keys and parent
constraints into one Repository query before totals, ordering, or pagination are computed.

The built-in Provider derives exact keys and parent constraints directly from its Binding Store, so it does not mirror
the complete Artifact catalog. An external Provider returns an equivalent authorization filter, or its adapter builds
one from trusted relationship search. A point-check-only Provider that cannot produce this filter must not query all
Artifacts, Projects, or Scopes and filter them afterward. The affected list operation returns 503, or configuration
reports `safe_resource_filtering=false`.

## Relationship administration

AuthZEN defines decision interoperability, not the relationship mutation interface for every PDP. Administration is
therefore separate from decisions:

```python
class RelationshipWriter(Protocol):
    async def create_binding(
        self,
        request: CreateAccessBinding,
        /,
    ) -> AccessBinding: ...

    async def revoke_binding(
        self,
        binding_id: str,
        /,
        *,
        expected_version: int,
    ) -> AccessBinding: ...
```

The built-in Provider and Casbin or OpenFGA adapters may implement both `AuthorizationProvider` and
`RelationshipWriter`. An OPA, Cerbos, or generic AuthZEN adapter may provide decisions only. Its PowerContext Binding
mutation endpoint then returns `relationship_management_unavailable`, and administrators configure relationships in
the external system. The Server must not report a successful grant and then write only a local shadow record.

## Access Binding model

The built-in Binding Store records at least:

| Field | Requirement |
| --- | --- |
| `binding_id` | Server-generated opaque ID |
| `subject` | Canonical `PrincipalRef` |
| `resource` | Canonical exact `ResourceRef` |
| `role` | One fixed role name |
| `granted_by` | Authenticated Principal recorded by the Server |
| `reason` | Optional bounded human explanation |
| `created_at` | Trusted Server time |
| `expires_at` | Optional trusted expiration |
| `state` | `active` or `revoked` |
| `version` | Monotonically increasing CAS version |
| `policy_revision` | Policy version after mutation when available |
| `idempotency_key` | Bounded caller key scoped to grantor and resource |

A role, subject, or resource change revokes the old Binding and creates a new one. A retry with the same grantor,
idempotency key, and payload returns the original Binding. The same key with a different payload returns 409.
Expiration does not delete a record; the decision treats it as denied.

The built-in Binding Repository belongs to a Server access-control component. It is not added to the Runtime
`context`, `source`, `memory`, `artifact`, `handoff`, or `work` application object. It may share a deployment
database with the Server, but it owns an independent schema, migrations, and API.

## Public Access API

The OpenAPI source of truth adds these operations:

| Operation | Purpose | Authorization |
| --- | --- | --- |
| `GET /v1/access/me` | Return the current Principal and access-control capabilities | Authenticated Principal |
| `POST /v1/access/check` | Check one action/resource for the current Principal | Current Principal only |
| `POST /v1/access/check-batch` | Batch checks for the current Principal | Current Principal only |
| `POST /v1/access/resources/list` | List resource identities available to the current Principal | Current Principal only |
| `POST /v1/access/roles/list` | Return fixed roles and action vocabulary | Authenticated Principal |
| `POST /v1/access/bindings/list` | List Bindings the caller may administer | `scope.delegate`, `scope.admin`, or `server.admin` |
| `POST /v1/access/bindings/create` | Create a Family-compatible exact-resource or administrative Binding | Resource-specific administration action |
| `POST /v1/access/bindings/revoke` | Revoke a Binding using CAS | Same administration boundary |
| `POST /v1/access/audit/list` | Query security audit events | `scope.admin` or `server.admin` |

`check`, `check-batch`, and `resources/list` do not accept a client-selected subject. They evaluate only the current
authenticated Principal, preventing ordinary users from using the API as a personnel permission oracle.
Administrator checks for another Principal, subject search, and directory integration are deferred.

`bindings/create` necessarily accepts a recipient subject so A can name B, but the caller can create only fixed roles
on resources it may administer. The Server validates structure and role compatibility through the Resource Kind and
Artifact Family registry, performs the grant-administration check, and only then reads a Repository
to confirm that the resource exists, belongs to the declared parent, and is in an authorizable state. A nonexistent
and an invisible resource both return 403 to an unauthorized caller. A 404 or Family-specific conflict is available
only after the administration decision allows access.

The Access API does not create, modify, fork, render, or publish business resources. Memory, Artifact, Prompt, and
managed Skill publication operations retain their own contracts. Publisher-safe target selection belongs to the Skill
publication contract; target configuration and operator status are Server operations. None enters the Access API or
creates a target Binding. A Binding expresses only who may perform which action on an existing resource.

The public `check` operation may return HTTP 200 with `allowed=false`. The same denial on a business operation returns
403 and does not call the application service. The Access API supports explanation and UI preflight; it never replaces
enforcement when the business request runs.

## Handoff operation requirements

The first-version Handoff mappings are:

| Operation | Required authorization |
| --- | --- |
| `prepare_handoff`, `finalize_handoff`, `handoff_current_work` | `scope.contribute` on request `scope_id` |
| `commit_handoff` | `scope.contribute` on request `scope_id` |
| `continue_handoff(selection=latest)` | `scope.read` on request `scope_id` |
| `continue_handoff(selection=exact)` | `artifact.read` and `handoff.evidence.read` on the exact `family=handoff` Artifact, directly or through parent `scope.read` |
| `continue_handoff(selection=prepared)` | `scope.read` on request `scope_id` |
| `acknowledge_handoff` with an exact Receipt | `scope.contribute` or `handoff.acknowledge` on the exact Revision |
| `record_task_outcome` | `scope.contribute` on request `scope_id` |
| Aggregate Handoff Report queries | Scope-level read; an exact Handoff grant is insufficient |
| Handoff Report administration | `scope.admin` or an appropriate server administration action |

When an exact receiver calls Continue, the request provides `selection=exact` and an exact `ArtifactReference`. The
Server builds the Handoff ArtifactResourceRef and evaluates it before reading the Revision. It cannot resolve latest before
the check or fall back to latest when the exact Revision is absent.

A Prepared Handoff may contain complete caller-supplied content, so the narrow grant path does not accept
`selection=prepared`. Only a Principal with `scope.read` may use a prepared selection to resolve scope evidence.

## Artifact Family operation requirements

Family operations map as follows. “Scope or exact” behavior is implemented by Provider parent relationships, not by a
client-selected bypass path:

| Operation family | Required authorization |
| --- | --- |
| Memory search/list/changes | `scope.read` on request `scope_id`; an exact Memory grant is insufficient |
| Exact Memory get | `artifact.read` on an exact `family=memory` Artifact plus complete `memory_entry` selector, directly or through parent `scope.read` |
| Memory flush/remember/revise/retire | `scope.contribute`; an exact viewer grant is insufficient |
| Approved Experience/managed Skill exact get | `artifact.read` on an exact `ArtifactReference`, directly or through parent `scope.read` |
| Experience/Skill propose or generate | `scope.contribute` |
| Candidate list/get | `scope.read`; an exact Artifact grant does not expose Candidates |
| Candidate revise/approve/reject | `scope.review` |
| Approved Prompt exact get | `artifact.read` on an exact `family=prompt` Artifact, directly or through parent `scope.read` |
| Approved Prompt render/use | `prompt.use`, directly or through parent `scope.read` |
| Prompt propose/revise | Candidate operation defined by the Prompt lifecycle plus `scope.contribute` |
| List enabled publication targets for an exact managed Skill | `artifact.read` **and** `skill.publish` on the same exact `family=skill` Artifact |
| Publish managed Skill | `artifact.read` **and** `skill.publish` on the same exact `family=skill` Artifact |

An exact-get resolver obtains the complete identity directly from a validated request. A Memory `entry_id`, Artifact
`artifact_id`, or Prompt name alone is not an authorization key. Search, current-head selection, aggregate projections,
and the Candidate Inbox remain collection operations; an exact grant cannot enter them.

The Prompt Family Access Profile specifies authorization vocabulary and resolver behavior only. A deployment reports
that Family as enabled only after it registers an immutable approved `family=prompt` Artifact lifecycle and exposes
exact get and use operations consistent with this section. A version without Prompt domain operations may implement
other Families, but it must reject `family=prompt` Bindings and must not claim `prompt.user` is usable in `roles/list`.

`target_id` is a Server-configured publication operation parameter, not an authorization key or Resource. Only
`server.admin` may configure, modify, or remove a target; `server.observe` or `server.admin` protects detailed target
status. An operator status response contains only target ID, Agent kind, capabilities, desired and applied exact
Revisions, a stable state, and a safe reason code. It does not expose host paths, Agent homes, credentials, or raw OS
errors. For publication and publisher target-list requests, the Server must allow both requirements on the exact Skill
before resolving `target_id` or reading the target registry. A standalone operator status request first checks the
server-level action.

## OpenAPI access metadata

Every protected operation declares `x-powercontext-access` in `openapi/powercontext.yaml`. The generator includes the
extension as `Operation.access`; Server `_add_route()` uses it to assemble the PEP wrapper. For example:

```yaml
/v1/handoff/commit:
  post:
    operationId: commit_handoff
    x-powercontext-access:
      action: scope.contribute
      resource:
        type: scope
        scope-id-from: body.scope_id
```

An operation whose policy depends on selection names a registered resolver rather than embedding executable
expressions in YAML:

```yaml
x-powercontext-access:
  resolver: continue_handoff_access
```

A resolver is deterministic, Server-owned, and unit-tested. It builds an AccessRequest only from the validated request
model and route metadata. It cannot read a business Repository before deciding what to authorize.

Operations that need multiple requirements use a resolver. Publisher target selection and publication reuse the same
exact Skill resolver:

```yaml
/v1/skills/publication-targets/list:
  post:
    operationId: list_skill_publication_targets
    x-powercontext-access:
      resolver: publish_managed_skill_access

/v1/skills/publish:
  post:
    operationId: publish_managed_skill
    x-powercontext-access:
      resolver: publish_managed_skill_access
```

Generated `Operation.access` represents either one static requirement or a named resolver. The Server-side resolver
return type supports multiple `all` requirements. Generated transports do not duplicate policy logic; they carry the
current Principal and invoke the same Server operation.

Health endpoints, static page shells, and authentication callbacks may be explicitly public. A new business operation
without access metadata fails contract generation or contract tests; it never defaults to public.

## Server PEP

Request order is fixed:

```text
transport authentication
  -> bind Principal and trusted request context
  -> validate request schema
  -> resolve action and resource
  -> AuthorizationProvider decision
  -> application service
  -> response
```

Schema validation and Family/selector compatibility validation that does not access a Repository may run before the
decision to establish a resource identity safely, but validation errors do not expose resource content. Every
Repository lookup, Handoff resolution, Memory search, Artifact Family read, target lookup, host inspection, Report
aggregate, and mutation runs only after all required decisions allow access.

The PEP lives in the Server adapter. It does not add `principal`, role, or permission parameters to
`application.context.for_scope(...)` or to Source, Memory, Handoff, Work, or Review domain methods. Local in-process
Runtime calls do not gain Server authentication automatically. A local integration that needs a security boundary
uses the same Access Control service or calls through the Server.

## HTTP, MCP, and Dashboard parity

HTTP is the complete remote contract. MCP and the Dashboard reuse the same operations and PEP:

- HTTP authentication establishes a Principal before the authorization wrapper runs for each operation;
- the MCP internal ASGI bridge propagates the original Principal, actor, and request ID in request-local context;
- `is_internal_bridge()` can avoid parsing the same external credential twice, but the authorization wrapper still
  runs;
- MCP tool discovery may filter unavailable tools for the current Principal, but hiding a tool is only UX and each
  invocation still receives a decision;
- the Dashboard uses `access/me`, authorized resource listing, and batch checks to show a Handoff inbox or “Shared with
  me” view and disable or hide unavailable actions, but it cannot bypass API enforcement;
- a background job carries the service Principal bound when it was created or an explicit system Principal, never an
  empty identity.

HTTP and MCP return the same allow or deny for the same Principal, action, resource, and policy revision. Adapter
conformance tests protect that guarantee.

## Listing and pagination

Lists can leak Project names, scope IDs, Artifact Family identities, Handoff objectives, or Candidate metadata. The
safe order is:

```text
AuthorizationProvider.resolve_resource_filter
  -> validate bounded exact keys and parent constraints
  -> Repository query applying their union
  -> stable pagination
  -> response
```

This implementation is prohibited:

```text
Repository.list_all -> page -> check each item -> remove denied rows
```

It leaks totals, cursors, holes, and timing, and can prevent an authorized user from ever reaching later rows. The
Repository applies the union of exact keys and parent constraints in one query. `total`, cursors, and page boundaries
describe only the authorized collection.

An exact Artifact receiver discovers granted resources through Resource Kind and Family filters on
`/v1/access/resources/list`. This does not place those resources in aggregate Project, Workstream, Memory search,
Artifact catalog, or Candidate Inbox results. Only scope-level read permits the corresponding aggregate query. A
publication target is not an authorization resource and does not appear in this list. A Principal authorized to
publish the exact Skill obtains redacted target choices through the Skill-domain preflight. Detailed operational
status is queried through a Server operation protected by `server.observe` or `server.admin`.

## Audit and diagnostics

Access Audit is an append-only Server security record. It contains at least:

- request ID, time, transport, and operation ID;
- the Principal's opaque identifier and trusted actor identifier, if present;
- action, Resource Kind, optional Artifact Family, and opaque resource identity;
- allow or deny, stable reason code, and policy revision;
- for Binding creation or revocation, binding ID, grantor, recipient subject, role, and expected/result version.

Audit does not contain:

- Bearer tokens, cookies, client secrets, or PDP credentials;
- Handoff objectives, state, or next action;
- Source, Memory, Artifact, Prompt, PreparedContext, or citation bodies;
- publication-target locators, host paths, credential references, or raw Receiver or OS errors;
- arbitrary exception fields, configured PDP URLs, or raw provider responses;
- email addresses, display names, or unnecessary directory attributes.

Ordinary logs, metrics, and traces use the same data-minimization boundary. Public readiness returns only stable
component states and safe reasons. Detailed provider diagnostics stay in a protected operator channel.

## Consistency and failure recovery

Committing a Handoff and creating an external authorization relationship are not a disguised cross-system
transaction. A “send to B” UI performs recoverable steps:

1. commit or reuse the same exact Handoff Revision;
2. create the Binding using a stable idempotency key;
3. display “shared” only after both steps succeed;
4. if the second step fails, display “Handoff saved, but not yet visible to B” and retry only Binding creation;
5. do not prepare, commit, or create another Revision.

When the Binding succeeded but the client lost the response, the same idempotency key returns the original Binding.
If an external RelationshipWriter cannot provide equivalent idempotency, its adapter performs a safe exact
relationship lookup first or declares self-service mutation unsupported.

Every Artifact Family follows the same “persist or approve first, bind second” sharing rule. A failed Binding creation
does not roll back or recreate a business Revision; the client retries only the same idempotent Binding mutation.
Skill publication is a projection operation protected by two decisions. It creates no content Revision and
creates no target Binding or change to target authorization state. A failed target apply retains retryable
desired/applied state and a safe reason without placing local paths or underlying errors in public audit.

Receipt creation retains the existing exact-selection and evidence rules. The decision occurs before the Receipt
transaction. If authority is revoked concurrently immediately after the check, a colocated Provider and Binding Store
use a policy revision or transaction fence to avoid an obvious stale write. A remote PDP has a bounded residual TOCTOU
window and records the decision revision. The first version does not cache allowed decisions.

## Provider profiles

### Built-in provider

The built-in profile uses fixed roles and a Server-owned Binding Store. It supports point checks, batch checks,
pushdown `AuthorizedResourceFilter` generation from exact, scope, and server Bindings, creation, revocation, and audit.
It does not need a business-resource inventory. It is the reference semantics for local deployments and conformance
tests and does not provide passwords, a directory, or a custom policy language.

### Casbin adapter

A Casbin adapter can use RBAC with domains:

- subject maps to an issuer-scoped opaque ID;
- domain maps a server resource to the deployment access namespace and a scope or Artifact resource to its canonical
  scope resource namespace;
- object maps to a canonical server key, scope key, or Artifact key containing Family and selector;
- action uses this RFC's action vocabulary;
- role assignment and policy mutation use the Casbin management API and a persistence adapter.

The Casbin domain is an adapter policy namespace. It does not turn `scope_id` into authentication or tenant proof. The
adapter derives the domain from a trusted ResourceRef supplied by the Server. For list filtering, exact-object policy
produces canonical keys while scope or server role assignments produce parent constraints; the Casbin adapter does not
enumerate the business Repository.

### OpenFGA adapter

OpenFGA naturally represents relationships among users, groups, scopes, and exact child resources. Every Artifact
Family uses one `artifact` object type. The object ID contains the canonical Family, Revision, and selector; the Server
validates relation compatibility through the Family registry before a tuple write. A new read-only Family therefore
does not require a new OpenFGA type:

```text
type user

type server
  relations
    define observer: [user]
    define admin: [user]
    define can_observe: observer or admin
    define can_admin: admin

type scope
  relations
    define parent: [server]
    define viewer: [user]
    define contributor: [user]
    define reviewer: [user]
    define delegator: [user]
    define admin: [user]
    define can_read: viewer or contributor or reviewer or delegator or admin or admin from parent
    define can_contribute: contributor or admin or admin from parent
    define can_review: reviewer or admin or admin from parent
    define can_delegate: delegator or admin or admin from parent
    define can_admin: admin or admin from parent

type artifact
  relations
    define parent: [scope]
    define viewer: [user]
    define handoff_viewer: [user]
    define handoff_receiver: [user]
    define prompt_user: [user]
    define skill_publisher: [user]
    define can_read: viewer or handoff_viewer or handoff_receiver or prompt_user or skill_publisher or can_read from parent
    define can_read_handoff_evidence: handoff_viewer or handoff_receiver or can_read from parent
    define can_acknowledge_handoff: handoff_receiver or can_contribute from parent
    define can_use_prompt: prompt_user or can_read from parent
    define can_publish_skill: skill_publisher or can_admin from parent
```

The adapter maps `server.observe` to `server#can_observe` and `server.admin` to `server#can_admin`. `admin from parent`
continues to make deployment `server.admin` imply scope administration and child Artifact Family actions in one
direction. `server.observer` gains none of those permissions.

The adapter uses an explicit authorization model ID for Check, ListObjects, and tuple writes. Tuples contain only
opaque IDs, never email addresses or Handoff content. Model migration switches the configured model ID explicitly; it
does not use an implicit latest model.
For lists, exact relations may produce canonical keys through ListObjects, while scope or server roles produce trusted
parent constraints directly. The adapter does not require an object tuple for every business Artifact that has no
exact Binding.

### AuthZEN, OPA, and Cerbos adapters

An AuthZEN adapter maps `AccessRequest` to the Authorization API subject, action, resource, and context and maps the
decision back to `AccessDecision`. An OPA adapter can submit the same structure as its input document. A Cerbos adapter
can map it to principal, resource, and actions.

Decision interoperability does not imply policy administration interoperability. If an organization manages policy
through GitOps, IAM, or a separate administration plane, PowerContext consumes decisions and safe resource filters but
does not write policy. The deployment declares `relationship_management=false`, and the Dashboard does not present a
self-service share control that could report false success. An adapter that cannot build an `AuthorizedResourceFilter`
from PDP search or trusted relationship data also reports `safe_resource_filtering=false`.

## Configuration and compatibility

The Server provides three explicit modes:

| Mode | Behavior |
| --- | --- |
| `disabled` | Preserve existing single-user, single-trust-domain behavior; Access API unavailable; no multi-user isolation claim |
| `legacy-static-admin` | Map the current static Bearer to a deployment-local `server.admin` Principal |
| `enforced` | Require both an authentication Provider and AuthorizationProvider; run the PEP for every business operation |

An upgrade cannot fall back to `disabled` because external identity is configured but a PDP is missing. Mode is
explicit. Capabilities and readiness report the current mode and whether relationship management, batch checks, and
`safe_resource_filtering` are available.

`disabled` is suitable only for a local environment whose caller already trusts the whole process and catalog.
Documentation cannot describe it as a secure multi-user configuration. Remote, multi-user, or shared-Dashboard
deployments use `enforced`.

`access/me` and readiness also report enabled Resource Kinds and an `artifact_families` capability map. Each Family
entry contains at least `enabled`, `share_unit`, available actions, and grantable roles. For example, a deployment
without the Prompt lifecycle reports `prompt.enabled=false`. `operation_capabilities.skill_publication` separately
reports whether host-local managed Skill publication and publisher-safe target selection are available. It is true
only when the Skill Family, both domain operations, and at least one enabled host-local target are available; it is
not a Resource Kind or bindable profile. When a Provider lacks `safe_resource_filtering`, multi-requirement checks, or
relationship mutation, the relevant capability is false. The Server must not accept a Binding it cannot subsequently
enforce or revoke.

```json
{
  "resource_kinds": ["server", "scope", "artifact"],
  "provider_capabilities": {
    "safe_resource_filtering": true,
    "multi_requirement_check": true,
    "relationship_management": true
  },
  "artifact_families": [
    {
      "family": "memory",
      "enabled": true,
      "share_unit": "memory_entry",
      "actions": ["artifact.read"],
      "grantable_roles": ["artifact.viewer"]
    },
    {
      "family": "prompt",
      "enabled": false,
      "share_unit": "revision",
      "actions": [],
      "grantable_roles": []
    }
  ],
  "operation_capabilities": {
    "skill_publication": {"enabled": true}
  }
}
```

Adding authorization metadata to an existing OpenAPI operation does not change its domain request or response schema,
but it adds a 403 response and changes unauthorized behavior. The generated Client maps 401, 403, and 503 to stable,
distinct exceptions; it does not treat 403 as an empty result.

## Implementation slices

Implementation proceeds in independently verifiable slices:

1. **Contract and Principal**: OpenAPI Access models, operation metadata, generated `Operation.access`, trusted request
   Principal, and stable errors.
2. **Built-in PEP/PDP**: fixed roles, Binding Store, `_add_route()` authorization wrapper, point/batch checks, and
   audit.
3. **Exact Handoff receiver**: post-commit Binding creation, exact Continue, citation-manifest resolver, exact
   acknowledge, revocation, and expiration.
4. **Artifact Family Access Profiles**: unified ArtifactResourceRef, Family registry, Memory selector, exact read/use
   resolvers, role compatibility, and non-transitive lineage.
5. **Skill publication**: a Server-configured host-local target registry, publisher-safe selection, operator status,
   read-plus-publish requirements on the same exact Skill, and redacted failure state.
6. **Safe listing and UI**: authorized resource listing, Handoff inbox, “Shared with me,” Dashboard permission projection, and
   authorization-aware pagination.
7. **MCP parity**: Principal propagation through the internal bridge, tool-discovery UX, and invocation-time
   enforcement.
8. **External adapters**: implement Casbin or OpenFGA first, then validate an AuthZEN-compatible PDP with the same
   conformance suite.
9. **Migration**: legacy static admin, configuration validation, Family capabilities, readiness, and operator
   documentation.

Every slice leaves the Server in a coherent state. An intermediate release cannot protect only HTTP while MCP bypasses
the PEP, or hide only Dashboard controls without API enforcement.

## Test and acceptance plan

The implementation of this RFC is complete only when these observable scenarios pass:

- an unauthenticated request to a protected operation returns 401;
- A with `scope.delegate` can grant B only an existing committed exact Handoff Revision in that scope, using
  `handoff.viewer` or `handoff.receiver`; another Artifact Family or role returns 422, while a missing action returns
  403, and neither failure writes a Binding;
- B can read, Continue, and acknowledge the granted exact Revision;
- B is denied latest, adjacent Revisions, the aggregate Handoff Report, Memory lists, Source lists, and Task Outcome
  writes;
- B reads manifest citations only through the authorized Handoff resolver and cannot submit an arbitrary citation to
  a general read endpoint;
- `handoff.viewer` cannot acknowledge while `handoff.receiver` can;
- an `accepted` Receipt creates no Binding or scope role;
- after revocation or expiration, B's later access is denied and authorized resource listing omits the Revision;
- Binding creation and revocation have stable CAS, idempotency, and audit behavior;
- 403 does not leak resource existence, and list cursors and totals describe only the authorized collection;
- an unavailable PDP returns 503 without calling an application service, Repository, or mutation;
- the MCP internal bridge uses the original Principal and returns the same denial as HTTP;
- the API denies a request even when Dashboard controls are bypassed or fail to hide it;
- a legacy static token becomes local admin only in the explicit compatibility mode;
- `server.observer` can read protected service and publication status but cannot modify access or target configuration;
  `server.admin` can perform both classes of operation, with equivalent Built-in, Casbin, and OpenFGA results;
- built-in, Casbin/OpenFGA, and AuthZEN adapters return equivalent decisions for the same conformance vectors;
- a request cannot submit an independent content profile; an unknown or disabled Family, `revision=latest`, a missing
  or extra selector, or a Family-role mismatch returns 422 and writes no Binding;
- `artifact.viewer` always maps only to `artifact.read` for Experience, Skill, Prompt, and a `memory_entry` selector;
  the Family never adds use, publish, acknowledge, or mutation implicitly;
- `artifact.viewer` can get an authorized Memory Entry through `family=memory` and a complete `memory_entry` selector,
  but cannot search, list, select current, revise, retire, or read adjacent versions;
- an exact Artifact viewer can read an approved Experience or managed Skill Revision but cannot see Candidates, later
  Revisions, or dereference lineage bodies;
- `artifact.viewer` may only read a Prompt while `prompt.user` may use it explicitly; neither role changes host
  instruction precedence or places the Prompt in normal recall automatically;
- an exact-resource role cannot revise, retire, replace, or commit a later Revision of the shared original, even when
  the request supplies the expected version;
- a Receipt created by acknowledgement and a target projection created by publication do not change the source
  identity, content, Revision, or digest;
- a fork, import, or copy is denied without `scope.contribute` on the destination scope; when allowed, it creates a new
  identity or Candidate and leaves the original unchanged;
- managed Skill publication runs only when both `artifact.read` and `skill.publish` allow access on the same exact
  Skill; any denial or unavailable decision prevents `target_id` resolution, host-path inspection, and projection
  writes; after authorization, an unknown or disabled target still rejects publication;
- the publisher target list reads the registry only after both requirements on the same exact Skill allow access and
  returns only safe identities and capabilities for enabled targets; detailed status still requires `server.observe`
  or `server.admin`;
- the first version rejects a remote Receiver target without reading remote credentials or opening a network
  connection;
- `skill.publisher` may publish its authorized exact Skill to any enabled target in the deployment; the first version
  has no target Binding or per-target delegation;
- `resources/list` totals, cursors, and rows describe only the selected Resource Kind and Artifact Family resources
  discoverable by the current Principal;
- a deployment without a Prompt lifecycle rejects `family=prompt` Bindings; one without an available publication
  operation reports `operation_capabilities.skill_publication.enabled=false`; and
- Access Audit contains no token, Handoff, Memory, Artifact, or Prompt content, Source body, target locator, or raw PDP
  error.

Cross-component acceptance scenarios belong in `tests/e2e/` and assert through the public HTTP and MCP contracts.
Focused tests cover the Family registry, selectors and canonical keys, resource resolvers, role mapping, Binding CAS,
provider failure, and citation membership without freezing private call order.

# Drawbacks

Every business request adds an authorization decision. A remote PDP adds a network dependency and latency. Safe lists
require a bounded pushdown `AuthorizedResourceFilter`, so a point-check-only adapter cannot support every Dashboard
list.

An exact Handoff transfer must be committed first. A temporary Prepared Handoff cannot become a revocable cross-user
resource. That adds a persistence step but avoids inventing a second identity and ACL model for temporary payloads.

Separating decisions from relationship management makes the adapter surface more complex than a single `check()`.
Assuming every external PDP lets PowerContext write policy would, however, make a false portability promise.

Revocation blocks future access but cannot erase information a receiver has already read, captured, or exported.
Handoffs, Memory, Artifacts, or Prompts containing highly sensitive material still need content minimization, external
data classification, and export controls.

Artifact Family Access Profiles add a registry, selectors, a role compatibility matrix, and conformance vectors. Skill
publication also checks `artifact.read` and `skill.publish` on the same exact Artifact. A remote PDP without an atomic
multi-requirement decision adds latency and a bounded TOCTOU risk whose policy revision must be recorded.

The first version does not place targets in authorization policy. A Principal with `skill.publisher` on an exact Skill
may publish it to any enabled target in the deployment. A deployment that needs target-specific isolation must defer
the capability, isolate deployments, or wait for a separate RFC to define a generic `execution_target` Resource. This
RFC does not prematurely encode that model as a Skill-specific resource.

The Prompt Family Access Profile defines only an authorization boundary. It cannot replace the Prompt Artifact
lifecycle or host instruction-precedence contract. A deployment reports that Family unavailable until those business
capabilities exist, so the RFC can deliver other Families first without claiming the complete product experience.

Fixed first-version roles limit organization-specific UX. An enterprise can map custom roles in its external PDP, but
the PowerContext public API does not immediately provide a custom role editor.

# Rationale and alternatives

## Chosen: independent Server PEP plus replaceable PDP

This design keeps Handoff, Memory, Artifact, Prompt, and Runtime models independent of the identity system
while giving HTTP, MCP, and the Dashboard one enforcement path. Stable action vocabulary maps across Casbin, OpenFGA,
OPA, Cerbos, and enterprise IAM more reliably than stable external role names.

An AuthZEN-compatible request shape gives remote PDPs a standard integration point. A separate RelationshipWriter
accurately reflects that AuthZEN does not standardize all grant mutations.

## Alternative: put ACL fields on Handoff or scope

Adding `allowed_users` to Handoff or encoding owner and tenant into `scope_id` looks direct but mixes identity
lifecycle, group expansion, revocation, external policy revision, and audit into domain data. An immutable Handoff
should not receive a new Revision whenever team membership changes. This alternative is rejected.

## Alternative: scope-level roles only

Granting only `scope.viewer` is easy, but B then sees the complete Workstream's Memory, Sources, history, and Report.
That violates least privilege for a temporary relay. Scope roles remain available for long-term collaboration;
exact-resource Bindings serve one-off transfers or asset sharing.

## Alternative: add one share API per domain

`/memory/share`, `/experience/share`, `/skill/share`, and `/prompt/share` would duplicate Principal, Binding, expiration,
revocation, audit, and external-PDP semantics and make transport behavior likely to diverge. This RFC uses one Access
API with one ArtifactResourceRef, Family role compatibility, and resolvers. Each domain still owns its business API.

## Alternative: one Resource Kind per Artifact Family

Separate `ResourceRef.type` values for `handoff`, `memory_entry`, `experience`, `skill`, and `prompt` would duplicate
scope parentage, exact Revision identity, canonical keys, and read-only sharing structure. Every new Family would also
extend the OpenAPI discriminator and external PDP object types. More importantly, `ResourceRef.type` and
`ArtifactReference.family` would become two potentially conflicting content discriminators. This RFC uses one
`artifact` Resource Kind and lets the Server derive the Access Profile from `ArtifactReference.family`. Only a Family
such as Memory that needs a narrower authorization unit adds an explicit selector.

## Alternative: recall every shared resource automatically

Adding every exact grant to PreparedContext conflates visibility with relevance, expands token budgets, and lets an
untrusted Prompt or Skill affect a receiver's model without explicit selection. The first version provides authorized
discovery and explicit attachment only. A later shared collection or subscription still passes through an independent
Context selection policy.

## Alternative: send an anonymous capability URL

A bearer share link treats knowledge of a URL as identity. Links can enter chat, logs, browser history, or model
context. They make it hard to identify the actual receiver or apply enterprise group policy and individual audit. The
first version requires B's own authenticated identity and does not provide anonymous capability URLs.

## Alternative: copy a redacted Handoff document

Copying Markdown avoids Server authorization work but loses exact Revision, evidence availability, Receipt,
concurrency, and revocation semantics. Export may become an explicit external publication feature, but it cannot
replace a PowerContext-internal transfer.

## Alternative: hide unauthorized Dashboard controls

UI hiding improves experience but an HTTP or MCP caller can bypass it. Enforcement always occurs at the Server PEP;
the Dashboard only consumes the same decisions.

## Alternative: require one policy engine

Casbin fits embedded RBAC, OpenFGA fits relationships and groups, and OPA or Cerbos fits an existing policy platform.
Requiring one implementation either increases deployment cost or restricts enterprise integration. PowerContext
defines semantics and a conformance contract rather than one engine.

## Alternative: store roles in access tokens

Token roles are simple but poorly suited to exact Handoff grants, revocation, large resource sets, and policy updates.
A token may carry trusted identity and group claims, but the PDP still makes the final resource decision.

## Alternative: authorize inside every Runtime method

Passing a Principal into Context, Source, Memory, Handoff, and Work spreads transport policy through the domain,
encourages divergent HTTP and MCP implementations, and changes local domain APIs. The Server PEP is the single remote
trust-boundary enforcement point.

# Prior art

PowerContext [RFC 0011](0011_remote_access_architecture.md) defines HTTP as the complete contract with the generated
Client and MCP projection sharing Server application semantics. This RFC adds authentication and authorization at the
same Server boundary rather than creating a parallel MCP policy service.

[RFC 0048](0048_handoff_artifact.md) defines Prepared Handoffs, immutable Handoff Revisions, Continue, and exact
evidence. [RFC 1223](1223_human_agent_work_continuity.md) defines Receipts and Task Outcomes and states that a transfer
does not grant tools, network access, or credentials. [RFC 0082](0082_handoff_report.md) provides scope- and
Project-level aggregate views. This RFC adds Principal-aware visibility to those reads and writes.

[RFC 0050](0050_artifact_candidate_review_inbox.md) defines Experience and Skill Candidates and their Review gate; a
pending or rejected Candidate is not a shareable Artifact. [RFC 0051](0051_experience_skill_artifact_families.md)
defines exact Experience and managed Skill Revisions, host-local External Skill authority, and the boundary that
approval or publication does not grant execution. This RFC adds Principal-aware visibility and managed Skill
publication authorization without changing that content authority.

The [OpenID AuthZEN Authorization API 1.0](https://openid.net/specs/authorization-api-1_0.html) defines the subject,
action, resource, context, and decision contract between PEPs and PDPs. This RFC aligns with that information model
while retaining an embedded Provider option.

[Casbin RBAC with Domains](https://casbin.apache.org/docs/rbac-with-domains/) demonstrates domain-scoped role
assignment. [OpenFGA concepts](https://openfga.dev/docs/concepts) use user, relation, and object tuples for object-level
authorization. [OPA](https://www.openpolicyagent.org/docs/integration) provides a general policy decision integration.
[Cerbos CheckResources](https://docs.cerbos.dev/cerbos/latest/api/index.html) provides batch decisions over principals,
resources, and actions. These systems are adapter targets; they do not change the PowerContext Handoff lifecycle.

# Unresolved questions

The RFC must resolve these choices before merge, but they do not change the core security boundary:

- whether the first external conformance adapter is Casbin or OpenFGA;
- whether the built-in Provider ships with the default Server extra or a separate optional extra;
- how the Dashboard selects a canonical recipient from the deployment identity directory; the Access API in this RFC
  does not provide directory search;
- whether an enforced deployment requires `safe_resource_filtering` or may disable the corresponding Dashboard lists;
- whether deployment policy sets a default expiration for `handoff.receiver` or the UI requires an explicit choice;
- whether the UI suggests a separate `scope.contributor` grant after an exact receiver creates a Receipt, without ever
  performing that upgrade automatically;
- whether the later Prompt Artifact lifecycle uses one fixed Review policy or distinguishes private personal templates
  from organization-approved templates.

Custom roles, organization hierarchy, cross-tenant export, anonymous share links, temporary elevation, approval
workflows, general Source object-level ACLs, dynamic Memory collections, Artifact catalog sharing, and automatic
following of future Revisions are explicitly deferred. They require separate threat models and RFCs.

# Future possibilities

The subject/action/resource contract can later support:

- group, team, and organization relationships;
- Project-to-Workstream inheritance and explicit deny;
- administrator checks, subject/resource search, and access-review campaigns;
- approval-backed temporary scope elevation;
- AuthZEN Search APIs, obligations, and richer decision metadata;
- policy bundles, signed decision metadata, and cross-service audit correlation;
- separate redaction, watermarking, and data-loss-prevention policy for Handoff export;
- registration of more approved Artifact Families under the existing `artifact` Resource Kind and base
  `artifact.read` action;
- a generic `execution_target` Resource Kind and per-target grants shared by Skill, Prompt, or other execution content,
  defined in a separate RFC;
- remote managed Skill targets after a separate Receiver distribution contract and trust-boundary review;
- shared collections with explicit membership and Revision manifests, plus subscription selection through Context
  policy;
- a bounded decision cache after a clear revocation-staleness guarantee exists.

These extensions cannot change the first-version invariants: `scope_id` is not an ACL, resource content does not grant
authority, exact grants do not follow later Revisions, reads do not enter Context or grant execution automatically, and
every transport fails closed at the Server PEP.
