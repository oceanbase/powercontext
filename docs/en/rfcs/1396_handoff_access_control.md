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
- `artifact`: one logical Artifact identity or Family-owned logical selector interpreted by an Artifact Family Access
  Profile.

The `artifact` Resource Kind registers enabled Artifact Family Access Profiles for `handoff`, `memory`, `experience`,
and `skill`. The `prompt` vocabulary is reserved, but its Profile is disabled until PowerContext implements a Prompt
lifecycle. `ArtifactReference.family` is the only Profile discriminator. A client does not submit a second content
type that could conflict with it.

User A can collaborate in two ways:

- grant a Workstream role to a long-term collaborator; or
- grant B access to one logical persisted or approved resource.

The second option is the least-privilege path in the first version. B may read existing and future versions of the
shared logical resource and perform only the actions defined by its Artifact Family Access Profile. A Handoff receiver
may inspect the evidence explicitly cited by the selected Revision through its resolver and leave a Receipt for that
Revision. A logical Memory or Artifact grant does not open the rest of the scope, aggregate search or list results,
another logical resource, or resources referenced by lineage.
Reading a Skill, publishing it to a target, and allowing a host to load or execute it are separate authorization
boundaries. An `accepted` Receipt, Artifact approval, Prompt read, or Skill publication never grants tools, network,
filesystem, model Provider, or credential access.

PowerContext defines a stable authorization request and decision, built-in roles, an Access API, and an OpenAPI
extension without requiring one policy engine. The current implementation provides a built-in Role Binding Store, an
embedded writable Casbin adapter, and a decision-only adapter for Policy Decision Points (PDPs) compatible with the
OpenID AuthZEN Authorization API. OpenFGA, OPA, and Cerbos remain possible future adapters.

# Motivation

PowerContext already has temporary Prepared Handoffs, immutable Handoff Revisions, Continue, Receipts, Task Outcomes,
Memory Entry Versions, approved Experience and managed Skill Revisions, and host-local Skill projections. The current
Server authentication model, however, is an optional global static Bearer token. A valid token can call every protected
operation. The Server cannot express that:

- A administers a Workstream while B can see only one transfer;
- B may acknowledge a transfer but may not publish another milestone;
- a team member may view a Handoff Report but may not approve an Experience or Skill;
- B may read the versions of one shared Memory Entry but may not search the scope or read another entry;
- B may read Revisions of one approved Experience or managed Skill but may not review a Candidate;
- a publisher may publish a selected managed Skill Revision but cannot thereby modify its source or gain host execution
  authority;
- an active Handoff Binding covers later Revisions, while a revoked receiver may not read any Revision afterward;
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
Prepared Handoff -> Commit -> logical Handoff -> immutable Revisions
                                  |
                                  +-> Access Binding for user B
                                           |
                              read any Revision / inspect / acknowledge
                                           |
                                    expire or revoke
```

The first commit does not share a Handoff automatically. Once a logical Handoff Binding exists, later immutable
Revisions of that same Handoff are covered without replacing the Binding. Sharing does not change Handoff content or
Revision. Revoking a Binding does not delete the Handoff, Receipt, or audit events.

## One Access Plane with Artifact Family-driven Profiles

The Access Control core answers only whether the current Principal may perform an action on a logical resource. A
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
│   └── family=prompt (reserved, disabled)
```

Each Artifact Family Access Profile must define:

| Family profile contract | Required definition |
| --- | --- |
| share unit | Which logical identity or Family-owned logical selector the grant covers across versions |
| shareable state | Which lifecycle states, such as committed, approved, or retained, allow Binding creation |
| parent | How scope- or server-level roles imply child-resource actions in one direction |
| actions | Stable actions for reading, using, acknowledging, publishing, and administration |
| grantable roles | Fixed roles that may bind to the resource and who may create those Bindings |
| resolution | Operations that can resolve the resource from a validated request and what they may not read first |
| listing | How a logical grant is discovered and which aggregate lists still require scope or server authority |
| transitivity | Whether reading the resource also reads lineage, citations, or other related resources |

All Families reuse the same `/v1/access/*` API. They do not add parallel authorization endpoints such as
`/memory/share`, `/experience/share`, `/skill/share`, or `/prompt/share`. A new logical-resource Family that reuses
`artifact.read` does not require another ResourceRef variant, but it must be registered explicitly. A Family that
introduces a semantic action, selector, or role must update OpenAPI, the fixed action and role vocabulary, Server-owned
resolvers, Provider conformance vectors, and generated transport artifacts together. Unknown Families are not
shareable by default.

Resource visibility, context selection, and external execution authority are separate planes:

```text
Access Plane:      Which logical resource the Principal may read, write, or share across versions
Context Plane:     Which authorized content enters bounded PreparedContext after explicit selection
Execution Plane:   Whether a host installs, loads, or executes a Skill or Prompt and which tools it may use
```

An allow decision does not propagate across planes. A logical Memory or Artifact grant does not place content
in normal scope recall automatically. A receiver first discovers it in a “Shared with me” view, then explicitly reads
it, attaches it to the current task, or forks it into a scope where the receiver may contribute. Shared content remains
`untrusted_history` or untrusted instruction; Context builders and hosts still enforce their own budgets, precedence,
approval, and sandbox policy.

## A transfers one logical Handoff to B

Assume A administers the `project:payments` Workstream and has prepared a transfer. The normal flow is:

1. A inspects and commits the Prepared Handoff, producing an immutable `ArtifactReference`:

   ```json
   {
     "family": "handoff",
     "artifact_id": "handoff",
     "revision": 12
   }
   ```

2. A explicitly selects B. The Dashboard or integration resolves B through the deployment's identity directory to a
   trusted canonical Principal. Model output, a display name, or email text cannot replace this resolution.
3. The Server checks whether A has `scope.delegate` on `project:payments`.
4. The Server validates that the selected Revision belongs to a committed Handoff, then creates an Access Binding with
   the `handoff.receiver` role for that logical Handoff and optionally sets an expiration time.
5. B signs in using B's own credential. `resources/list` returns logical Handoffs B may read. B never receives A's token
   or a new bearer share link.
6. B calls Continue with an exact or latest selection. The Server reads the selected Revision and resolves only the
   evidence it explicitly cites. Existing and future Revisions of the same logical Handoff use the same Binding.
7. After checking the live workspace, capability, and authorization state, B may leave an `accepted`,
   `needs_clarification`, or `declined` Receipt for the same Revision.

An example Binding creation request is:

```json
{
  "subject": {
    "type": "user",
    "id": "00u-bob"
  },
  "resource": {
    "type": "artifact",
    "scope_id": "project:payments",
    "identity": {
      "family": "handoff",
      "artifact_id": "handoff"
    },
    "selector": null
  },
  "role": "handoff.receiver",
  "expires_at": "2026-09-06T12:00:00Z",
  "reason": "Continue the payment retry investigation",
  "idempotency_key": "transfer-payments-12-to-bob"
}
```

The Server supplies `granted_by`, creation time, and policy revision. The caller cannot assert them.

## What B can see

`handoff.receiver` is a logical-resource role, not a scope role:

| Operation | Result | Reason |
| --- | --- | --- |
| Read Handoff Revision 12 | Allowed | The Binding identifies the logical Handoff |
| Inspect the citations of Revision 12 through Continue | Allowed | `handoff.evidence.inspect` covers only the selected Revision's immutable citation manifest |
| Acknowledge Revision 12 | Allowed | A receiver may leave a Receipt for the selected Handoff Revision it inspected |
| Request `latest` | Allowed | Latest resolves within the same bound logical Handoff |
| Read Revision 11 or 13 | Allowed when present | Historical and future Revisions of the same logical Handoff share one Access identity |
| Open the aggregate Handoff Report | Denied | The Report contains scope-level history and statistics |
| Search scope Memory or list Sources | Denied | A Handoff Binding does not grant general scope read |
| Commit a Handoff or record a Task Outcome | Denied | Those operations require `scope.contribute` |
| Approve a Candidate | Denied | Approval requires independent `scope.review` authority |

Least-privilege evidence access does not copy each Source or Memory item, and it does not require an external PDP to
store every citation. The Server first builds the logical Handoff `ArtifactResourceRef` from the validated request and
checks both `artifact.read` and `handoff.evidence.inspect` for B. Only when both decisions allow access may it select an
immutable Handoff Revision, obtain its citation manifest, and dereference exact citations in that manifest through the
Handoff resolver. The manifest is the bounded transitive authorization edge: B cannot reuse it by placing an arbitrary
Source, Memory, or Artifact identifier in a general read API.

If a citation has been deleted, retired, corrupted, or cannot be resolved, Continue marks the corresponding evidence
unavailable. A Handoff Binding does not override retention, legal hold, or data classification policy.

## Sharing other Artifact Families

Other Artifact Families use the same logical-share flow without inheriting Handoff evidence or Receipt semantics:

1. A selects a persisted version to identify a resource that can be authorized. The Server normalizes Memory to its
   logical `entry_id` selector and other Artifacts to `{family, artifact_id}`; Revision fields do not enter the Binding.
2. The Server checks whether A may create the relevant Binding in the resource's scope, then verifies that the resource
   exists and is in a shareable state.
3. B discovers the logical resource through `access/resources/list` and reads existing or future versions as B's own
   Principal.
4. To create a derivative, B proposes a new Artifact in a scope where B has `scope.contribute`. The new logical
   identity belongs to B; the original resource and Binding do not change.

First-version logical grants behave as follows:

| Family role | Allows | Does not allow |
| --- | --- | --- |
| `artifact.viewer` on a `family=memory` selector | Exact get of any version of one `entry_id` | Search, list, changes, revise, retire, or another entry |
| `artifact.viewer` | Exact get of any approved Revision of one Experience or managed Skill identity | Candidate read/review, publication, another Artifact, or lineage bodies |

The reserved `prompt` Profile cannot receive a Binding while `enabled=false`; `prompt.user` is therefore absent from
the usable roles returned for enabled Families. Internal prompts for Memory extraction, Experience or Skill
generation, and Handoff generation are Server implementation or configuration, not shareable Prompt Artifacts.

Except for Handoff's manifest-scoped evidence resolver, a logical resource response may return lineage or citation
identities defined by its schema, but the grant does not propagate to those referenced resources. A general Source,
Memory, or Artifact get still requires an independent decision for the target. A Provider must not create `can_read`
inheritance merely because “A references B.”

## Viewer Bindings are read-only; ownership governs the evolving identity

Each enabled logical Artifact has exactly one direct owner in enforced mode. The Server establishes that owner when it
creates the first Handoff or Memory identity, or records the proposer as the proposed owner and establishes ownership
when an Experience or Skill Candidate is approved. Ownership is a Server-managed relation, not a public
`artifact.owner` Binding, and it covers all existing and future Revisions of the same logical identity.

The owner receives `artifact.read`, `artifact.write`, and `artifact.share`; Handoff owners also receive
`handoff.evidence.inspect`. `artifact.write` is required when a request creates the next Revision, revises or retires
Memory, replaces an existing Experience or Skill target, or changes managed Skill lifecycle state. A scope role may
authorize contribution or review, but it does not silently make its holder the owner of an existing Artifact.

Viewer and receiver Bindings remain read-only with respect to the bound content. A later Revision written by the owner
becomes visible through the logical Binding, but the Binding cannot authorize the receiver to revise, retire, replace,
or commit the next Revision. To create a derivative, a receiver needs `scope.contribute` in the destination scope and
creates a new identity or Candidate whose ownership is independent of the source.

State produced by the receiver remains separate from the shared original:

| Receiver operation | Constraint |
| --- | --- |
| Acknowledge a Handoff | Creates a separate Receipt and does not modify the Handoff Revision |
| Submit feedback or a change request | Creates separate feedback or a change request and does not modify shared content |
| Publish a managed Skill | Writes projection or state to a Server-configured target and does not modify the source Skill Revision |
| Fork, import, or copy | Requires `scope.contribute` on the destination scope; creates a new identity or Candidate with lineage to the original |

Product surfaces should offer actions such as “View,” “Acknowledge,” “Request changes,” “Copy to my scope,” or
“Publish to configured target.” They should not present a logical share as “Edit shared content.” Ongoing co-maintenance
requires a separate scope role. For an Artifact Family with Review, a contributor still creates a Candidate and uses
the Review lifecycle to produce a new Revision instead of editing an approved Revision in place. Revocation prevents
later access, but it cannot erase content already seen by the receiver or automatically revoke a Receipt, projection,
or fork that was previously created under independent authority.

## Publishing an Artifact across Scopes

`POST /v1/artifact-publications` copies one exact source Artifact Revision into an independent Artifact in a target
Scope. The business request therefore contains an exact `ArtifactAddress`, but the Access Resource remains the
logical `{family, artifact_id}` identity without a Revision. Before loading or copying content, the Server requires:

```text
artifact.share on the logical source Artifact
scope.admin on the target Scope
```

This keeps the authorization durable across source revisions while preserving exact publication provenance. After the
copy succeeds, the Server establishes the publishing Principal as the direct owner of the new target identity before
returning success. The target does not inherit the source Binding or owner. Repeating the same publication repairs a
missing target-owner relation idempotently; a conflicting owner fails closed. A grant does not copy content by itself,
and a publication does not grant access to host paths, tools, networks, or credentials. Family-specific publication
support remains a Runtime concern; unsupported complete-state copies fail after authorization without weakening the
Access model.

## B takes over the Workstream

Seeing a transfer does not grant execution authority. If B will work on the Workstream over time, A or an administrator
must separately grant `scope.contributor`:

```text
handoff.receiver
  = read one logical Handoff across Revisions + inspect selected manifest citations + acknowledge a selected Revision

scope.contributor
  = read the Workstream + contribute Sources + prepare/commit Handoffs
    + acknowledge Handoffs + record Task Outcomes
```

PowerContext authorization governs only PowerContext resources and operations. The host, operating system, and
external services still govern Git changes, cloud APIs, production access, and credentials. A Handoff, Role Binding,
or Receipt cannot enlarge those permissions.

## Long-term team collaboration

A stable team can receive scope roles instead of a new Binding for each Revision:

- `scope.viewer` reads Handoffs, Memory, approved Artifacts, Sources, and read-only projections in the current scope;
- `scope.contributor` writes work evidence, Memory contributions, Handoffs, and Outcomes and proposes Artifact
  Candidates in addition to viewer access;
- `scope.reviewer` reviews Artifact Candidates in addition to viewer access;
- `scope.delegator` shares logical Handoffs with receivers in addition to viewer access;
- `scope.admin` administers roles and policies for the scope and can authorize Artifact sharing, but is not itself a
  content-reader or content-writer role.

`scope.delegate` authorizes only viewer or receiver Bindings for `family=handoff` Artifacts. The Artifact's direct
owner may also create or revoke its resource Binding through `artifact.share`. For other enabled Families, the owner,
`scope.admin`, or `server.admin` may administer resource Bindings. An existing Handoff delegator does not silently
gain a wider sharing boundary. `server.admin` administers server and scope policy but does not implicitly gain
`server.observe`, `scope.read`, or `artifact.write`; the legacy static Principal receives separate observer and
per-scope working roles for compatibility.

These fixed roles are wire-contract vocabulary. An external PDP does not have to persist the same role names. It may
map organization roles, teams, or relationships to these actions.

## Revocation and expiration

A, the applicable grant administrator, or a scope administrator can revoke a logical Artifact Binding within its
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
- support scope-level RBAC and logical Handoff receiver Bindings;
- define stable Resource Kinds and an Artifact Family Access Profile contract, with logical authorization for Handoff,
  Memory, Experience, and Skill resources while reserving disabled Prompt vocabulary;
- resolve evidence cited by a selected Revision of an authorized Handoff safely without opening the complete scope;
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
- multi-writer collaborative editing of a shared logical resource or ownership transfer through a Binding;
- dynamic subscription sharing for Memory collections or Artifact catalogs whose membership changes over time;
- the Prompt Artifact content schema, variable language, Review lifecycle, or host instruction-precedence policy;
- per-target publication delegation or a general `execution_target` Resource;
- the remote managed Skill Receiver distribution contract, which is defined by its own lifecycle RFC; or
- cross-host locators, automatic installation, or package distribution for External Skills.

## Trust model and invariants

An implementation must preserve these invariants:

1. `scope_id` is a business partition value, not proof of authority.
2. A Principal comes only from authentication middleware or trusted internal bridge context.
3. A `receiver`, `subject`, `actor`, role string, or Handoff prose in a request body cannot replace the current
   Principal.
4. Handoff, Memory, and Artifact content is `untrusted_history` or untrusted instruction and cannot grant an
   action.
5. `is_internal_bridge()` may skip repeated transport authentication but never authorization.
6. Every protected operation receives a decision before it accesses a Repository or application service.
7. A logical Handoff grant allows exact and latest selection for existing and future Revisions of the same Artifact,
   but no other Handoff or parent-scope collection.
8. An `accepted` Receipt does not create, update, or inherit an Access Binding.
9. A model may suggest a receiver or explain a denial, but it cannot choose a canonical Principal or invoke an
   allow-all fallback.
10. A Memory Entry grant consists of the logical `family=memory` Artifact identity and a `memory_entry` selector that
    contains only `entry_id`. Every other Artifact grant contains only `{family, artifact_id}`. A business request may
    select a positive integer Revision or version, but those fields never enter the Access Resource or Binding. The
    Server derives the Access Profile only from `identity.family`; it rejects an independent content profile, an
    unknown Family, or a selector mismatch.
11. Reading Memory or Artifact content does not grant its lineage or citation targets and does not place it in
    PreparedContext automatically.
12. A logical-resource Binding does not grant revise, retire, replace, commit-next-Revision, or any other mutation of
    shared content. Receipts, feedback, projections, and forks are separate resources or operations that require
    independent authorization and do not modify the original resource identity, content, or Revision.
13. Every enabled logical Artifact has one immutable direct owner relation. Public Bindings cannot create, replace, or
    transfer `artifact.owner`; a missing owner fails closed before Artifact authorization.
14. Host-local Skill projection requires `server.observe` and `artifact.read` before resolving `target_id` or
    inspecting the filesystem. Remote target administration requires `scope.admin`, and remote publication also
    requires `artifact.read`. These operations never grant host execution, tools, networks, filesystems, or secrets.
15. Public errors, logs, metrics, and traces do not contain credentials, Handoff, Memory, or Artifact content,
    Source bodies, target locators, or raw PDP responses.

## Principal model

`PrincipalRef` uses the stable opaque identity established by an authentication Provider:

```json
{
  "type": "user",
  "id": "00u-bob"
}
```

The fields mean:

| Field | Semantics |
| --- | --- |
| `type` | `user` or `service` |
| `id` | A deployment-wide stable opaque subject, not a display name or email address |
| `description` | Optional display metadata excluded from identity equality and policy keys |

Issuer namespacing, when needed, is normalized by the Authentication Provider into the deployment-wide opaque `id`;
`issuer` is not a public `PrincipalRef` field. Agent names, hosts, session IDs, and model names are provenance, not
Principals by default. When an enterprise token proves an on-behalf-of actor, an authentication adapter may add that
actor to trusted request context; a PDP may then constrain both subject and actor. A client cannot assert that actor in
a JSON body.

The existing Handoff Receipt `receiver` remains record content. The Server separately records the authenticated
Principal that produced the Receipt. If they differ, the Server rejects `accepted` or explicitly records the mismatch
for a non-accepted Receipt. It never treats the free-form `receiver` as a Principal.

The acknowledge response and Receipt Source GET expose `receipt_identity.principal` and
`receipt_identity.receiver_identity_matches`. This immutable attestation reuses the existing `pc_access_audit` table
with operation `handoff.receipt.identity`; no table or column is added. Its event ID hashes an unambiguous encoding of
the operation, Scope ID, and Source ID. The existing unique constraint prevents concurrent requests or retries from
replacing the attribution. This event records the reservation of the authenticated submitter, not successful Receipt
capture or completed work. It is written before Source capture and retained with the Receipt, rather than purged as a
short-lived log. It does not rewrite the Source body or its content digest. An accepted receipt with a mismatched
receiver is rejected; other statuses retain the self-reported receiver with `receiver_identity_matches=false`.
Reading a Receipt without this attestation returns 503.

## Resource model

Internal authorization requests use structured `ResourceRef` values. This avoids concatenating identifiers that may
contain `:`, `/`, or user data into policy strings:

| Resource Kind | Identity | Parent |
| --- | --- | --- |
| `server` | Deployment identifier | None |
| `scope` | Exact `scope_id` | Server |
| `artifact` | Logical `{family, artifact_id}`, optional Family-owned logical selector, and `scope_id` | Scope |

`ResourceRef` is an OpenAPI discriminated union. Each variant uses `additionalProperties: false` and accepts only these
fields:

| `type` | Required identity fields |
| --- | --- |
| `server` | `deployment_id` |
| `scope` | `scope_id` |
| `artifact` | `scope_id`, `identity`, and optional `selector` |

An ordinary logical Artifact has no selector or Revision:

```json
{
  "type": "artifact",
  "scope_id": "project:payments",
  "identity": {"family": "experience", "artifact_id": "exp-retry-budget"},
  "selector": null
}
```

Memory Entry uses a logical selector owned by the `memory` Family. `entry_version_id` and the backing Memory Artifact
Revision remain in business citations, not in Access Resources:

```json
{
  "type": "artifact",
  "scope_id": "project:payments",
  "identity": {"family": "memory", "artifact_id": "memory"},
  "selector": {
    "type": "memory_entry",
    "entry_id": "retry-policy"
  }
}
```

`ArtifactResourceRef.identity.family` is the only Artifact Family Access Profile discriminator. A request contains no
separate `profile` field. The Server derives the Profile from the validated logical identity, avoiding
conflicts such as `profile=prompt` with `family=skill`. Each Family declares its selector required, forbidden, or one
specific discriminated-union variant. The current implementation requires a `memory_entry` selector for `memory` and
forbids a selector for `handoff`, `experience`, `skill`, and the disabled `prompt` Profile.

The Family registry is a fixed Server-owned contract, not an administrator-editable policy DSL. Every registration
contains at least:

| Field | Requirement |
| --- | --- |
| `family` | Stable name that exactly matches `ArtifactReference.family` |
| `share_unit` | `artifact` or one explicit Family-owned logical selector type |
| `shareable_states` | Lifecycle states in which a Binding may be created |
| `base_action` | `artifact.read` in the first version |
| `additional_actions` | Family-specific read-side or acknowledgement actions |
| `grantable_roles` | Fixed logical-resource roles compatible with the Family |
| `mutation_semantics` | Owner-only mutations represented by `artifact.write` |
| `parent_implications` | Child actions implied by scope roles in one direction |
| `transitivity` | Whether lineage, citations, or other related resources need separate decisions; the default is none |
| `resolver` | How to resolve a selected business version after logical authorization and which safe identity to return |

The current registry is:

| Artifact Family | Enabled | Share unit | Shareable state | Family actions | Grantable resource roles |
| --- | --- | --- | --- | --- | --- |
| `handoff` | yes | logical Artifact | at least one committed Revision | `artifact.read`, `handoff.evidence.inspect`, `handoff.acknowledge` | `handoff.viewer`, `handoff.receiver` |
| `memory` | yes | logical `memory_entry` selector | active or retired entry exists | `artifact.read` | `artifact.viewer` |
| `experience` | yes | logical Artifact | at least one approved Revision | `artifact.read` | `artifact.viewer` |
| `skill` | yes | logical Artifact | at least one approved Revision | `artifact.read` | `artifact.viewer` |
| `prompt` | no | logical Artifact | reserved | reserved `artifact.read`, `prompt.use` vocabulary | none |

Every enabled row also accepts `artifact.write` for its direct owner and `artifact.share` for owner- or
administrator-controlled sharing. Those actions do not become viewer actions and are not grantable as separate
resource Bindings. `artifact.owner` is exposed by role discovery as a one-per-resource, system-managed role, while
owner relations are created only by Server business flows.

A Prepared Handoff has no persistent identity and cannot receive an Access Binding. A least-privilege cross-user
transfer must be committed first. A pending or rejected Candidate likewise cannot receive an Artifact Binding. Even a
new Family that reuses only `artifact.read` must be registered explicitly as shareable. Unknown, disabled, or
selector-incompatible Families are denied by default. `revision`, `entry_version_id`, a Memory current head, and a
search query are not authorization identities. Later Artifact Revisions and Memory Entry Versions are covered by the
same logical Binding, while aggregate discovery still requires scope authority.

Each Resource Kind defines a stable canonical serialization for adapter object IDs. An Artifact key includes
`scope_id`, `family`, `artifact_id`, and the logical selector when present. The same business
identity produces the same key over HTTP, MCP, and the Dashboard. Different Families or selectors cannot share a
Binding through string collisions.

An adapter maps a structured ResourceRef to an external PDP object ID. The mapping must be canonical and stable, and
must not write email addresses, tokens, resource content, publication target locators, or other PII into Casbin policy,
OpenFGA tuples, or audit keys.

### Artifact ownership

The Server stores ownership separately from ordinary `AccessBinding` rows. `ArtifactOwnerRelation` contains the
logical resource, one `PrincipalRef`, trusted creation time, policy revision, and an idempotency key. It deliberately
contains no Artifact Revision. The owner relation is immutable; creating it again is idempotent only for the same
owner and key, and a different owner returns a conflict. Ownership transfer is not part of this RFC.

In enforced mode, Artifact authorization fails closed with `artifact_owner_pending` until this relation exists. New
Memory entries and first Handoff commits are owned by the creating Principal. A new Experience or Skill Candidate
records a Server-side proposed-owner attestation; approval establishes that Principal as owner. A Candidate targeting
an existing identity must retain its existing owner. Cross-Scope publication establishes the publisher as owner of
the new target identity.

The first version assumes that a deployment enables enforced mode before it persists its first Artifact. It does not
backfill or infer owners for a catalog populated while access control was disabled, and it exposes no general owner
repair workflow. Switching such a catalog to enforced mode without a separate operator migration leaves those
Artifacts unavailable by design. If domain persistence succeeds but owner establishment fails, the request still
fails closed; only a business flow that explicitly supports idempotent replay may repair the relation on retry. A
general transactional outbox and operator recovery procedure are future work outside this RFC.

### Built-in relational persistence

The built-in and embedded Casbin providers use five Server-owned Access tables:

| Table | Purpose |
| --- | --- |
| `pc_access_relationships` | Role Bindings, their history, and unique singleton occupancy |
| `pc_access_owners` | Immutable Artifact ownership and Candidate proposed-owner attestations |
| `pc_access_relationship_heads` | The committed authorization revision |
| `pc_access_idempotency` | Binding mutation request fingerprints and replay results |
| `pc_access_audit` | Access audit events and trusted Handoff Receipt identity attestations |

`pc_access_owners.owner_kind` distinguishes `artifact` from `candidate`. Candidate attestations do not establish
Artifact ownership, grant permissions, or appear in owned-resource discovery. Approval establishes a separate
Artifact ownership record while retaining the original Candidate attestation. The identity keys preserve Scope,
Family, and Memory Entry boundaries; a Candidate ID is unique within its Scope across Families.

A singleton Binding occupies a nullable unique `singleton_key` on `pc_access_relationships`; ordinary Bindings
leave it null. Revocation releases the key, and replacement releases the old key and inserts its successor in one
transaction. A new grant can reclaim an expired key without deleting the historical Binding or its replay record.
The database unique constraint prevents competing claims from creating multiple receivers. Binding mutations lock
the authorization revision before Binding rows, and expiration comparisons use UTC timestamps. A reclaimed,
expired Binding cannot be replaced or release the current receiver's key when revoked.

## Action vocabulary

First-version actions are stable lowercase dotted strings:

| Action | Resource | Meaning |
| --- | --- | --- |
| `server.observe` | server | Read service-level operations and observability data |
| `server.admin` | server | Administer deployment access and publication-target configuration |
| `scope.read` | scope | Read general resources, approved content, and projections in a Workstream |
| `scope.contribute` | scope | Create Sources, new Memory/Handoff content, Outcomes, and Artifact Candidates |
| `scope.review` | scope | Review Artifact Candidates in the scope |
| `scope.delegate` | scope | Create viewer or receiver Bindings for logical Handoffs |
| `scope.admin` | scope | Administer roles, Bindings, and policy for the scope |
| `artifact.read` | logical artifact | Read selected existing and future versions of the identity or selector defined by its Family Profile |
| `artifact.write` | logical artifact | Mutate the owner-controlled logical identity through its Family lifecycle |
| `artifact.share` | logical artifact | Administer viewer/receiver Bindings or publish an exact Revision from the logical source identity |
| `handoff.evidence.inspect` | `family=handoff` artifact | Resolve a selected Revision's citation manifest through the Handoff resolver |
| `handoff.acknowledge` | `family=handoff` artifact | Create a Handoff Receipt for a selected Revision |
| `prompt.use` | `family=prompt` artifact | Reserved; unusable while the Prompt Profile is disabled |

`artifact.read` has one meaning across every enabled Family: read versions of only the logical identity or selector
named by the Binding. It does not include Handoff evidence, lineage bodies, write, or share. A Family adds a semantic
action only for an operation with a genuinely different security effect.

Business operations check actions rather than role names. External role and relationship models can therefore evolve
without changing application code.

The built-in parent implications are deliberately narrow. `scope.viewer`, `scope.reviewer`, and `scope.delegator`
imply `artifact.read` and Handoff evidence inspection for children. `scope.contributor` additionally implies Handoff
acknowledgement. `scope.admin` and `server.admin` imply `artifact.share`, while `server.admin` also implies
`scope.admin`. Administration never implicitly grants content read or write. The reverse implication never holds: a
resource viewer or owner does not gain a scope role.

## Built-in roles

| Role | Granted actions |
| --- | --- |
| `handoff.viewer` | `artifact.read`, `handoff.evidence.inspect` on one logical `family=handoff` Artifact |
| `handoff.receiver` | Viewer actions plus `handoff.acknowledge` on one logical Handoff |
| `artifact.viewer` | `artifact.read` on one compatible logical Artifact or selector |
| `prompt.user` | Reserved role; not usable while `family=prompt` is disabled |
| `artifact.owner` | `artifact.read`, `artifact.write`, `artifact.share`, and Handoff evidence inspection on one logical Artifact; system-managed |
| `scope.viewer` | `scope.read` |
| `scope.contributor` | `scope.read`, `scope.contribute` |
| `scope.reviewer` | `scope.read`, `scope.review` |
| `scope.delegator` | `scope.read`, `scope.delegate` |
| `scope.admin` | `scope.admin`; implies only `artifact.share` on child Artifacts |
| `server.observer` | `server.observe` |
| `server.admin` | `server.admin`; implies `scope.admin` and `artifact.share`, but no read or write action |

`handoff.receiver` and `artifact.owner` have `one_per_resource` cardinality; all other roles are
`many_per_resource`. Owner is system-managed. Receiver and owner subjects must be a user or service. Other public role
schemas also admit a group subject, but the built-in and Casbin compositions currently report `group_subjects=false`
and reject group Binding creation until a trusted group resolver is configured.

Every publicly grantable resource role is read-only with respect to its bound content. `handoff.receiver` adds only the
creation of a separate Receipt. Mutation of the original resource requires the system-managed owner relation and the
relevant domain lifecycle.

The first version does not allow the public API to create roles or change role-to-action mappings. Fixed roles give
OpenAPI, the Dashboard, and adapter conformance tests stable semantics. An enterprise PDP may map custom organization
roles to the actions externally.

A Principal with `scope.delegate` may create only `handoff.viewer` or `handoff.receiver`, and only for an existing
logical Handoff in that scope. Creating a scope role requires `scope.admin`. Creating `server.admin` requires an existing
`server.admin` and permission from deployment policy. A Principal cannot grant itself authority beyond the caller's
administration boundary.

An Artifact owner or `scope.admin` may create compatible viewer Bindings; `server.admin` inherits that administration
boundary. `artifact.viewer` may bind only to a logical Artifact or selector declared compatible by an enabled Family
Profile. Public `artifact.owner` Bindings and all Bindings for disabled `family=prompt` are rejected. A role and
Artifact Family Access Profile or Resource Kind mismatch returns 422; insufficient authority returns 403. The Server
must not forward an incompatible role string unchanged to an external RelationshipWriter.

| Resource or Artifact Family Profile | Grantable resource roles | Binding administrator |
| --- | --- | --- |
| `artifact` with `family=handoff` | `handoff.viewer`, `handoff.receiver` | owner, `scope.delegate`, `scope.admin`, or `server.admin` |
| `artifact` with `family=memory` and a `memory_entry` selector | `artifact.viewer` | owner, `scope.admin`, or `server.admin` |
| `artifact` with `family=experience` | `artifact.viewer` | owner, `scope.admin`, or `server.admin` |
| `artifact` with `family=skill` | `artifact.viewer` | owner, `scope.admin`, or `server.admin` |
| disabled `family=prompt` | none | none |

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
    "id": "00u-bob"
  },
  "action": {"name": "artifact.read"},
  "resource": {
    "type": "artifact",
    "scope_id": "project:payments",
    "identity": {
      "family": "handoff",
      "artifact_id": "handoff"
    },
    "selector": null
  },
  "context": {
    "request_id": "pc-01K...",
    "transport": "mcp",
    "operation": "continue_handoff"
  }
}
```

`AccessDecision` contains at least:

```json
{
  "allowed": true,
  "reason_code": "role-binding",
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

For example, cross-Scope Artifact publication resolves to two ordered requirements:

```json
{
  "match": "all",
  "requirements": [
    {
      "action": "artifact.share",
      "resource": {
        "type": "artifact",
        "scope_id": "project:payments",
        "identity": {"family": "skill", "artifact_id": "retry-runbook"},
        "selector": null
      }
    },
    {
      "action": "scope.admin",
      "resource": {
        "type": "scope",
        "scope_id": "team:runbooks"
      }
    }
  ]
}
```

The source Revision remains in the business request and publication provenance, not in the Access Resource. Host-local
and remote Skill projection likewise keep `target_id` as an operation parameter rather than an Access Resource.

Alternatives such as “scope role or resource role” do not require an `any` expression. The PEP requests the child-resource
action. A Provider uses a trusted parent relationship to decide whether a scope role implies that action, while a logical
Binding applies directly to the child. Providers therefore do not need an arbitrary nested policy expression language.

`resolve_resource_filter` is required for safe list operations. An `AuthorizedResourceFilter` is specific to the
current Principal and action. It contains bounded canonical resource keys produced by logical Bindings and bounded
server or scope constraints produced by parent roles. A parent constraint means that a Repository may query only
within that parent, requested Resource Kind, and Family; it is not a client-authored wildcard. The filter also carries
the policy revision. The Server validates its structure and bounds, then pushes the union of logical resource keys and parent
constraints into one Repository query before totals, ordering, or pagination are computed.

The built-in Provider derives logical resource keys and parent constraints directly from its Binding Store, so it does not mirror
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

The built-in and included Casbin compositions pair their `AuthorizationProvider` with the canonical relational Access
repository as the `RelationshipWriter`; the Provider class itself does not own relationship mutation. An external
decision adapter may instead supply a matching `RelationshipWriter` and declare `relationship_management=true`, so
receiver and other Bindings are not restricted to the built-in store. The included AuthZEN adapter is decision-only.
With that adapter, PowerContext Binding mutation returns `relationship_management_unavailable`, and administrators
configure relationships in the external system. Future OpenFGA, OPA, or Cerbos adapters must declare the capabilities
they actually implement. The Server must not report a successful grant and then write only a local shadow record.

## Access Binding model

The built-in Binding Store records at least:

| Field | Requirement |
| --- | --- |
| `binding_id` | Server-generated opaque ID |
| `subject` | Canonical `PrincipalRef` |
| `resource` | Canonical logical `ResourceRef` |
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

Artifact ownership is not an `AccessBinding`. It is stored in the separate one-per-resource owner relation described
above, has no expiration, and cannot be created or transferred through `/v1/access/bindings/*`.

The built-in Binding Repository belongs to a Server access-control component. It is not added to the Runtime
`context`, `source`, `memory`, `artifact`, `handoff`, or `work` application object. It may share a deployment
database with the Server, but it owns an independent schema, migrations, and API.

## Public Access API

The OpenAPI source of truth adds these operations:

| Operation | Purpose | Authorization |
| --- | --- | --- |
| `GET /v1/access/me` | Return the current Principal and access-control capabilities | Authenticated Principal |
| `POST /v1/access/check` | Check one compound `all` or `any` requirement for the current Principal | Current Principal only |
| `POST /v1/access/resources/list` | List resource identities available to the current Principal | Current Principal only |
| `POST /v1/access/roles/list` | Return fixed roles and action vocabulary | Authenticated Principal |
| `POST /v1/access/bindings/list` | List Bindings the caller may administer | owner `artifact.share`, `scope.delegate`, `scope.admin`, or `server.admin`, according to resource |
| `POST /v1/access/bindings/create` | Create a Family-compatible logical-resource or administrative Binding | Resource-specific administration action |
| `POST /v1/access/bindings/revoke` | Revoke a Binding using CAS | Same administration boundary |
| `POST /v1/access/bindings/replace` | Atomically revoke an immutable Binding and create its successor | Same administration boundary |
| `POST /v1/access/audit/list` | Query server- or scope-bounded security audit events | `scope.admin` or `server.admin` |

`check` and `resources/list` do not accept a client-selected subject. They evaluate only the current
authenticated Principal, preventing ordinary users from using the API as a personnel permission oracle.
Administrator checks for another Principal, subject search, and directory integration are deferred.

`bindings/create` necessarily accepts a recipient subject so A can name B, but the caller can create only fixed roles
on resources it may administer. The Server validates structure and role compatibility through the Resource Kind and
Artifact Family registry, performs the grant-administration check, and only then reads a Repository
to confirm that the resource exists, belongs to the declared parent, and is in an authorizable state. A nonexistent
and an invisible resource both return 403 to an unauthorized caller. A 404 or Family-specific conflict is available
only after the administration decision allows access.

The Access API does not create, modify, fork, or publish business resources. Memory, Artifact, cross-Scope
publication, and managed Skill projection operations retain their own contracts. Target configuration and operator
status are Server or scope operations. None enters the Access API or creates a target Binding. A Binding expresses
only who may perform which action on an existing resource.

The public `check` operation may return HTTP 200 with `allowed=false`. The same denial on a business operation returns
403 and does not call the application service. The Access API supports explanation and UI preflight; it never replaces
enforcement when the business request runs.

## Handoff operation requirements

The first-version Handoff mappings are:

| Operation | Required authorization |
| --- | --- |
| `prepare_handoff`, `finalize_handoff`, `handoff_current_work` | `scope.contribute` on request `scope_id` |
| first `commit_handoff` | `scope.contribute` on request `scope_id`; success establishes the caller as owner |
| later `commit_handoff` with `base` | `scope.contribute` on request `scope_id` and `artifact.write` on the logical Handoff |
| `continue_handoff(selection=latest)` | `artifact.read` and `handoff.evidence.inspect` on the logical `family=handoff` Artifact, directly or through parent `scope.read` |
| `continue_handoff(selection=exact)` | `artifact.read` and `handoff.evidence.inspect` on the logical `family=handoff` Artifact, directly or through parent `scope.read` |
| `continue_handoff(selection=prepared)` | `scope.read` on request `scope_id` |
| `acknowledge_handoff` with an exact Receipt | `scope.contribute` or `handoff.acknowledge` on the logical Handoff selected by the exact Revision |
| `record_task_outcome` | `scope.contribute` on request `scope_id` |
| Handoff Report with exact Scope selection | `scope.read` for every selected Scope; a logical Handoff grant is insufficient |
| Handoff Report with a non-exact selection | `server.observe` |

When a receiver calls Continue, the Server builds the logical Handoff ArtifactResourceRef before reading a Revision.
For `selection=exact`, it derives the logical identity from the request's exact `ArtifactReference`; for
`selection=latest`, it uses the registered logical Handoff identity for the scope. Only after authorization may it
resolve the requested Revision and its manifest.

A Prepared Handoff may contain complete caller-supplied content, so the narrow grant path does not accept
`selection=prepared`. Only a Principal with `scope.read` may use a prepared selection to resolve scope evidence.

## Artifact Family operation requirements

Family operations map as follows. “Scope or logical resource” behavior is implemented by Provider parent relationships, not by a
client-selected bypass path:

| Operation family | Required authorization |
| --- | --- |
| Memory search/list/changes | `scope.read` on request `scope_id`; a logical Memory Entry grant is insufficient |
| Exact Memory get | `artifact.read` on the logical `family=memory` Artifact plus `memory_entry.entry_id`, directly or through parent `scope.read` |
| Create a Memory entry | `scope.contribute`; success establishes the caller as owner |
| Flush Memory | `scope.contribute` plus `artifact.write` on every existing entry that may be changed; new entries become caller-owned |
| Revise or retire one Memory entry | `artifact.write` on the logical `memory_entry` selector |
| Approved Experience/managed Skill exact get | `artifact.read` on the logical Artifact identity derived from the exact request, directly or through parent `scope.read` |
| Experience/Skill propose or generate a new identity | `scope.contribute`; the Server attests the caller as proposed owner |
| Experience/Skill proposal targeting an existing identity | `scope.contribute` plus `artifact.write` on that identity |
| Candidate list/get | `scope.read`; a logical Artifact grant does not expose Candidates |
| Candidate revise | `scope.review` and the authenticated Principal must match the original proposer attested by the Server |
| Candidate approve/reject | `scope.review`; approval also requires a valid proposed-owner attestation |
| Managed Skill lifecycle mutation | `artifact.write` on the logical Skill |
| Host-local Skill projection status/publish/unpublish | `server.observe` and `artifact.read` on the logical Skill |
| Remote Skill target administration | `scope.admin` |
| Publish a Skill Revision to a remote target | `scope.admin` and `artifact.read` on the logical Skill |
| Cross-Scope Artifact publication | `artifact.share` on the logical source and `scope.admin` on the target Scope |

An exact-get resolver derives the complete logical identity from a validated business request and discards Revision
fields for authorization. A bare Memory `entry_id` or Artifact `artifact_id` without its scope and Family
is not an authorization key. Search, aggregate projections, and the Candidate Inbox remain collection operations; a
logical grant cannot enter them.

The Prompt Family Access Profile reserves authorization vocabulary only. The current deployment reports
`prompt.enabled=false`, rejects `family=prompt` Bindings, and omits `prompt.user` from roles usable by enabled Families.

`target_id` is an operation parameter, not an authorization key or Resource. Host-local target inspection requires
`server.observe` plus logical Skill read. The remote distribution lifecycle uses scope-owned targets: their
administration requires `scope.admin`, and setting desired publication additionally requires logical Skill read.
Target credentials protect Receiver-only reconcile, download, and receipt operations outside user-Principal Access.
Public status never exposes host paths, Agent homes, credentials, or raw OS errors.

## OpenAPI access metadata

Every protected operation declares `x-powercontext-access` in `openapi/powercontext.yaml`. The generator includes the
extension as `Operation.access`; Server `_add_route()` uses it to assemble the PEP wrapper. For example:

```yaml
/v1/handoff/commit:
  post:
    operationId: commit_handoff
    x-powercontext-access:
      resolver: commit_handoff_access
```

An operation whose policy depends on selection names a registered resolver rather than embedding executable
expressions in YAML:

```yaml
x-powercontext-access:
  resolver: continue_handoff_access
```

A resolver is deterministic, Server-owned, and unit-tested. It builds an AccessRequest only from the validated request
model and route metadata. It cannot read a business Repository before deciding what to authorize.

Operations whose resource is derived from business input use a resolver. Cross-Scope publication combines source
sharing and target administration in one deterministic check:

```yaml
/v1/artifact-publications:
  post:
    operationId: publish_artifact
    x-powercontext-access:
      resolver: publish_artifact_access
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

The Dashboard `/shared` page requires neither `server.observe` nor `scope.read`. It provides a Family-filtered
resource list and Handoff inbox. Selecting a resource checks logical read permission before resolving its current
version. Memory resolves only the selected entry citation without reading other entry bodies. Inspecting a Handoff
calls Continue; a receipt uses the selected exact Revision. Acceptance requires explicit live-state, capability, and
authorization confirmations, with the receiver fixed to the current Principal. Callers with sharing authority can
enter a canonical recipient ID, choose a viewer or receiver role and expiration, and revoke active shares.

Candidate responses expose advisory `permissions.can_revise/can_approve/can_reject` fields for the current caller.
The Review page disables unavailable actions and explains that revision requires both review permission and original
proposal ownership. Every submitted action still executes the PEP; cached UI hints do not grant authority.

## Listing and pagination

Lists can leak Project names, scope IDs, Artifact Family identities, Handoff objectives, or Candidate metadata. The
safe order is:

```text
AuthorizationProvider.resolve_resource_filter
  -> validate bounded logical resource keys and parent constraints
  -> Repository query applying their union
  -> stable pagination
  -> response
```

This implementation is prohibited:

```text
Repository.list_all -> page -> check each item -> remove denied rows
```

It leaks totals, cursors, holes, and timing, and can prevent an authorized user from ever reaching later rows. The
Repository applies the union of logical resource keys and parent constraints in one query. `total`, cursors, and page boundaries
describe only the authorized collection.

A logical Artifact receiver discovers granted resources through Resource Kind and Family filters on
`/v1/access/resources/list`. This does not place those resources in aggregate Project, Workstream, Memory search,
Artifact catalog, or Candidate Inbox results. Only scope-level read permits the corresponding aggregate query. A
publication target is not an authorization resource and does not appear in this list. A Principal authorized to
publish a selected Revision of the Skill obtains redacted target choices through the Skill-domain preflight. Detailed operational
status is queried through a Server operation protected by `server.observe` or `server.admin`.

After authorizing a scope collection, the Server checks committed Artifact and Memory entry owners through a
content-free identity catalog before reading bodies or preparing context. A missing owner makes the whole aggregate
return 503 `artifact_owner_pending`, including Memory list/search, Context Prepare, Artifact catalogs, the Dashboard
Skill library, and reports. Callers without a matching grant receive the same 403 for missing-owner and existing but
invisible resources, preventing existence disclosure.

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
- Source, Memory, Artifact, PreparedContext, or citation bodies;
- publication-target locators, host paths, credential references, or raw Receiver or OS errors;
- arbitrary exception fields, configured PDP URLs, or raw provider responses;
- email addresses, display names, or unnecessary directory attributes.

Ordinary logs, metrics, and traces use the same data-minimization boundary. Public readiness probes PDP decisions and the audit, relationship, owner, and Receipt identity stores within a
five-second bound. A valid deny is a successful probe; exceptions, timeouts, or invalid decisions mark the access
provider not ready and return 503. The probe creates no grants or audit events. Public readiness returns only stable
component states and safe reasons. Detailed provider diagnostics stay in a protected operator channel.

## Consistency and failure recovery

Committing a Handoff and creating an external authorization relationship are not a disguised cross-system
transaction. A “send to B” UI performs recoverable steps:

1. commit or reuse a Handoff Revision belonging to the same logical Handoff;
2. create the Binding using a stable idempotency key;
3. display “shared” only after both steps succeed;
4. if the second step fails, display “Handoff saved, but not yet visible to B” and retry only Binding creation;
5. do not prepare, commit, or create another Revision.

When the Binding succeeded but the client lost the response, the same idempotency key returns the original Binding.
If an external RelationshipWriter cannot provide equivalent idempotency, its adapter performs a safe canonical
relationship lookup first or declares self-service mutation unsupported.

Every Artifact Family follows the same “persist or approve first, bind second” sharing rule. A failed Binding creation
does not roll back or recreate a business Revision; the client retries only the same idempotent Binding mutation.
Skill projection is protected by logical Skill read plus the applicable server or scope administration boundary. It
creates no source content Revision and no Access Binding. A failed target apply retains retryable desired/applied state
and a safe reason without placing local paths or underlying errors in public audit.

Receipt creation retains the existing exact-selection and evidence rules. The decision occurs before the Receipt
transaction. If authority is revoked concurrently immediately after the check, a colocated Provider and Binding Store
use a policy revision or transaction fence to avoid an obvious stale write. A remote PDP has a bounded residual TOCTOU
window and records the decision revision. The first version does not cache allowed decisions.

## Provider profiles

### Built-in provider

The built-in profile uses fixed roles and a Server-owned Binding Store. It supports point checks, batch checks,
pushdown `AuthorizedResourceFilter` generation from logical Artifact, scope, and server Bindings, creation, revocation, and audit.
It does not need a business-resource inventory. It is the reference semantics for local deployments and conformance
tests and does not provide passwords, a directory, or a custom policy language.

### Casbin adapter

The included Casbin adapter uses the canonical Access relationships with Casbin enforcement semantics:

- trusted subject and group IDs select active Bindings from the canonical repository before evaluation;
- `act` uses this RFC's action vocabulary and `obj` uses a canonical server, scope, or Artifact key;
- `scope` and `deployment` are trusted parent constraints, not authentication or tenant proof;
- the fixed PowerContext role tables expand active Bindings into concrete action policies;
- the canonical relational Access repository remains the source of truth for Bindings and ownership. The adapter
  materializes those relationships into a fresh embedded Casbin enforcer for evaluation and does not maintain a second
  persistent Casbin policy store.

For list filtering, logical-object policy produces canonical keys while scope or server role assignments produce
parent constraints; the Casbin adapter does not enumerate the business Repository. A future native Casbin-backed
composition may provide both decision and relationship management, but its writer must satisfy the same canonical
idempotency, versioning, ownership, and audit contracts before declaring `relationship_management=true`.

### Future OpenFGA adapter

No OpenFGA adapter is included in the current implementation. A future adapter may map the same canonical server,
scope, Artifact, owner, viewer, and receiver relationships to tuples, but it must preserve the exact role table above:
administration must not imply content read or write, Artifact object IDs must omit Revision, and safe listing must not
enumerate the business repository before authorization. It must also expose an explicit authorization model ID and
declare relationship, group, and resource-filter capabilities accurately.

### AuthZEN adapter and future OPA or Cerbos adapters

The included AuthZEN adapter maps point and batch `AccessRequest` values to the Authorization API subject, action,
resource, and context and maps only a bounded decision plus optional policy revision back to `AccessDecision`. It is
decision-only: safe resource filtering and relationship management are unavailable. OPA and Cerbos are possible
future adapters, not current deployment options.

The standard AuthZEN context retains `request_id`, `transport`, and `operation`. A `context.powercontext` extension
also carries the trusted `actor` as a Principal object or `null`, plus `subject_groups` as a list of Group objects.
These identities use the same deployment-wide opaque IDs normalized by the Authentication Provider; there is no
separate caller-supplied issuer field. The adapter must preserve this context for both point and batch decisions so an
external PDP can enforce group membership and on-behalf-of constraints with the same authenticated facts as the
Server-owned Providers.

Decision interoperability does not imply policy administration interoperability. If an organization manages policy
through GitOps, IAM, or a separate administration plane, PowerContext consumes decisions and safe resource filters but
does not write policy. The deployment declares `relationship_management=false`, and the Dashboard does not present a
self-service share control that could report false success. An adapter that cannot build an `AuthorizedResourceFilter`
from PDP search or trusted relationship data also reports `safe_resource_filtering=false`.

## Configuration and compatibility

`POWERCONTEXT_SERVER_ACCESS_MODE` is the only supported Access switch and accepts two values:

| Mode | Behavior |
| --- | --- |
| `disabled` | Preserve existing single-user, single-trust-domain behavior; Access API unavailable; no multi-user isolation claim |
| `enforced` | Require an Authentication Provider and AccessControlService; run the PEP for every business operation |

An upgrade cannot fall back to `disabled` because external identity is configured but a PDP is missing. Mode is
explicit. Capabilities and readiness report the current mode and whether relationship management, batch checks, and
`safe_resource_filtering` are available.

`POWERCONTEXT_SERVER_AUTH_TOKEN` is compatibility authentication only. In `enforced` mode, when no Authentication
Provider is injected, it authenticates the fixed `service/server-token` Principal and the built-in Access service
bootstraps that Principal with separate `server.observer`, `server.admin`, and per-scope working roles. It cannot model
multiple users. The legacy pair `POWERCONTEXT_SERVER_AUTH_ENABLED=true` plus
`POWERCONTEXT_SERVER_AUTH_TOKEN=...` maps to `ACCESS_MODE=enforced`. A token without enforced mode is rejected, and an
enforced deployment without either an injected Authentication Provider or this compatibility token fails startup.

`disabled` is suitable only for a local environment whose caller already trusts the whole process and catalog.
Documentation cannot describe it as a secure multi-user configuration. Remote, multi-user, or shared-Dashboard
deployments use `enforced`.

`access/me` reports the Principal, mode, Resource Kinds, Provider capabilities, and an `artifact_families` capability
list. Each Family entry contains `enabled`, `share_unit`, action vocabulary, and grantable roles. Disabled Prompt still
reports its reserved actions but has no grantable role. Readiness separately reports stable Access mode, provider
state, Resource Kinds, and Family enabled/disabled state. When a Provider lacks safe filtering, multi-requirement
checks, relationship mutation, groups, or multiple Principals, the corresponding capability is false. The Server must
not accept a Binding it cannot subsequently enforce or revoke.

```json
{
  "resource_kinds": ["server", "scope", "artifact"],
  "provider_capabilities": {
    "safe_resource_filtering": true,
    "multi_requirement_check": true,
    "relationship_management": true,
    "group_subjects": false,
    "multi_principal": false,
    "max_direct_resource_keys": 10000
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
      "share_unit": "artifact",
      "actions": ["artifact.read", "prompt.use"],
      "grantable_roles": []
    }
  ]
}
```

Adding authorization metadata to an existing OpenAPI operation does not change its domain request or response schema,
but it adds a 403 response and changes unauthorized behavior. The generated Client maps 401, 403, and 503 to stable,
distinct exceptions; it does not treat 403 as an empty result.

## Implementation status

The current implementation delivers these independently verifiable slices:

1. **Contract and Principal**: OpenAPI Access models, operation metadata, generated `Operation.access`, trusted request
   Principal, and stable errors.
2. **Built-in PEP/PDP**: fixed roles, Binding Store, `_add_route()` authorization wrapper, point/batch checks, and
   audit.
3. **Logical Handoff receiver**: post-commit Binding creation, exact/latest Continue, citation-manifest resolver,
   exact acknowledge, future-Revision visibility, revocation, and expiration.
4. **Artifact Family Access Profiles and ownership**: unified ArtifactResourceRef, Family registry, Memory selector,
   system-managed logical ownership, read/write/share resolvers, role compatibility, and non-transitive lineage.
5. **Publication and distribution**: cross-Scope publication, host-local Skill projection, and remote Skill
   distribution with their distinct logical Artifact and administrative requirements.
6. **Safe listing and UI**: authorized resource listing, Handoff inbox, “Shared with me,” Dashboard permission projection, and
   authorization-aware pagination.
7. **MCP parity**: Principal propagation through the internal bridge, tool-discovery UX, and invocation-time
   enforcement.
8. **Provider adapters**: built-in and embedded Casbin relationship-capable profiles plus a decision-only AuthZEN
   adapter. OpenFGA, OPA, and Cerbos remain future work.
9. **Migration**: legacy static admin, configuration validation, Family capabilities, readiness, and operator
   documentation.

Every slice leaves the Server in a coherent state. An intermediate release cannot protect only HTTP while MCP bypasses
the PEP, or hide only Dashboard controls without API enforcement.

## Test and acceptance plan

The implementation of this RFC is complete only when these observable scenarios pass:

- an unauthenticated request to a protected operation returns 401;
- A with `scope.delegate` can grant B an existing logical Handoff with at least one committed Revision in that scope, using
  `handoff.viewer` or `handoff.receiver`; another Artifact Family or role returns 422, while a missing action returns
  403, and neither failure writes a Binding;
- B can read and Continue historical, current, and future Revisions of the granted Handoff, use `latest`, and
  acknowledge a selected exact Revision;
- B is denied another Handoff, the aggregate Handoff Report, Memory lists, Source lists, and Task Outcome writes;
- B reads manifest citations only through the authorized Handoff resolver and cannot submit an arbitrary citation to
  a general read endpoint;
- `handoff.viewer` cannot acknowledge while `handoff.receiver` can;
- an `accepted` Receipt creates no Binding or scope role;
- after revocation or expiration, B's access is denied and authorized resource listing omits the logical Handoff;
- Binding creation and revocation have stable CAS, idempotency, and audit behavior;
- 403 does not leak resource existence, and list cursors and totals describe only the authorized collection;
- an unavailable PDP returns 503 without calling an application service, Repository, or mutation;
- the MCP internal bridge uses the original Principal and returns the same denial as HTTP;
- the API denies a request even when Dashboard controls are bypassed or fail to hide it;
- in explicit `enforced` mode, a legacy static token becomes local admin only when no Authentication Provider is injected;
- `server.observer` can read protected service state but cannot modify access or target configuration; `server.admin`
  can administer those resources but does not implicitly receive content read or write;
- built-in and Casbin providers return equivalent decisions for the same canonical relationships; the AuthZEN adapter
  maps point and batch decisions and fails closed on malformed or unavailable responses;
- a request cannot submit an independent content profile or Revision in an Access Resource; an unknown or disabled
  Family, a missing or extra selector, or a Family-role mismatch returns 422 and writes no Binding;
- `artifact.viewer` always maps only to `artifact.read` for Experience, Skill, and a `memory_entry` selector;
  the Family never adds use, publish, acknowledge, or mutation implicitly;
- `artifact.viewer` can get historical and future versions of an authorized Memory Entry through `family=memory` and
  an `entry_id` selector, but cannot search, list, revise, retire, or read another entry;
- a logical Artifact viewer can read approved Revisions of one Experience or managed Skill but cannot see Candidates,
  another Artifact, or dereference lineage bodies;
- `family=prompt` is reported disabled, rejects Bindings, and does not expose `prompt.user` as usable for an enabled
  Family;
- a logical-resource role cannot revise, retire, replace, or commit a later Revision of the shared original, even when
  the request supplies the expected version;
- an enabled Artifact without an owner relation fails closed; first creation or approval establishes exactly one
  immutable owner, and public Binding APIs cannot assign or transfer `artifact.owner`;
- an Artifact owner can read, write, and share its logical identity across Revisions without receiving a separate
  viewer Binding; scope and server administration do not implicitly grant owner write access;
- a Receipt created by acknowledgement and a target projection created by publication do not change the source
  identity, content, Revision, or digest;
- a fork, import, or copy is denied without `scope.contribute` on the destination scope; when allowed, it creates a new
  identity or Candidate and leaves the original unchanged;
- host-local managed Skill projection requires both `server.observe` and logical Skill `artifact.read` before target
  resolution or filesystem inspection; remote target administration requires `scope.admin`, while publishing a
  Revision also requires logical Skill read;
- cross-Scope publication requires logical source `artifact.share` plus target `scope.admin`, preserves the exact
  source Revision as provenance, and establishes the publisher as owner of the new target identity;
- `resources/list` totals, cursors, and rows describe only the selected Resource Kind and Artifact Family resources
  discoverable by the current Principal;
- a deployment without a Prompt lifecycle rejects `family=prompt` Bindings and reports `enabled=false`; and
- Access Audit contains no token, Handoff, Memory, or Artifact content, Source body, target locator, or raw PDP
  error.

Cross-component acceptance scenarios belong in `tests/e2e/` and assert through the public HTTP and MCP contracts.
Focused tests cover the Family registry, selectors and canonical keys, resource resolvers, role mapping, Binding CAS,
provider failure, and citation membership without freezing private call order.

# Drawbacks

Every business request adds an authorization decision. A remote PDP adds a network dependency and latency. Safe lists
require a bounded pushdown `AuthorizedResourceFilter`, so a point-check-only adapter cannot support every Dashboard
list.

A logical Handoff transfer must be committed first. A temporary Prepared Handoff cannot become a revocable cross-user
resource. That adds a persistence step but avoids inventing a second identity and ACL model for temporary payloads.

Separating decisions from relationship management makes the adapter surface more complex than a single `check()`.
Assuming every external PDP lets PowerContext write policy would, however, make a false portability promise.

Revocation blocks future access but cannot erase information a receiver has already read, captured, or exported.
Handoffs, Memory, or Artifacts containing highly sensitive material still need content minimization, external
data classification, and export controls.

Artifact Family Access Profiles add a registry, selectors, ownership, a role compatibility matrix, and conformance
vectors. Multi-requirement publication and projection checks add decision work; a remote PDP without an atomic batch
decision adds latency and a bounded TOCTOU risk whose policy revision must be recorded.

The Access model does not make `target_id` a Resource. Host-local targets use the server-observer boundary; remote
targets use their scope-administration boundary. A deployment that needs grants for individual targets must isolate
them by Scope or wait for a separate RFC to define a generic `execution_target` Resource.

The Prompt Family Access Profile defines only an authorization boundary. It cannot replace the Prompt Artifact
lifecycle or host instruction-precedence contract. A deployment reports that Family unavailable until those business
capabilities exist, so the RFC can deliver other Families first without claiming the complete product experience.

Fixed first-version roles limit organization-specific UX. An enterprise can map custom roles in its external PDP, but
the PowerContext public API does not immediately provide a custom role editor.

# Rationale and alternatives

## Chosen: independent Server PEP plus replaceable PDP

This design keeps Handoff, Memory, Artifact, and Runtime models independent of the identity system
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
logical-resource Bindings serve one-off transfers or asset sharing.

## Alternative: add one share API per domain

`/memory/share`, `/experience/share`, `/skill/share`, and `/prompt/share` would duplicate Principal, Binding, expiration,
revocation, audit, and external-PDP semantics and make transport behavior likely to diverge. This RFC uses one Access
API with one ArtifactResourceRef, Family role compatibility, and resolvers. Each domain still owns its business API.

## Alternative: one Resource Kind per Artifact Family

Separate `ResourceRef.type` values for `handoff`, `memory_entry`, `experience`, `skill`, and `prompt` would duplicate
scope parentage, logical Artifact identity, canonical keys, and read-only sharing structure. Every new Family would also
extend the OpenAPI discriminator and external PDP object types. More importantly, `ResourceRef.type` and
`ArtifactReference.family` would become two potentially conflicting content discriminators. This RFC uses one
`artifact` Resource Kind and lets the Server derive the Access Profile from `ArtifactReference.family`. Only a Family
such as Memory that needs a narrower authorization unit adds an explicit selector.

## Alternative: recall every shared resource automatically

Adding every logical grant to PreparedContext conflates visibility with relevance, expands token budgets, and lets an
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

Token roles are simple but poorly suited to logical Handoff grants, revocation, large resource sets, and policy updates.
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

# Open questions

These product choices remain outside the implemented security boundary:

- how the Dashboard selects a canonical recipient from the deployment identity directory; the Access API in this RFC
  does not provide directory search;
- which external identity source supplies trusted group membership; the built-in Provider currently reports
  `group_subjects=false`;
- whether deployment policy sets a default expiration for `handoff.receiver` or the UI requires an explicit choice;
- whether the UI suggests a separate `scope.contributor` grant after a Handoff receiver creates a Receipt, without ever
  performing that upgrade automatically;
- whether a future governed workflow permits Artifact ownership transfer; and
- whether the later Prompt Artifact lifecycle uses one fixed Review policy or distinguishes private personal templates
  from organization-approved templates.

Custom roles, organization hierarchy, cross-tenant export, anonymous share links, temporary elevation, approval
workflows, general Source object-level ACLs, dynamic Memory collections, and Artifact catalog sharing are explicitly
deferred. They require separate threat models and RFCs.

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
- shared collections with explicit membership and Revision manifests, plus subscription selection through Context
  policy;
- a bounded decision cache after a clear revocation-staleness guarantee exists.

These extensions cannot change the first-version invariants: `scope_id` is not an ACL, resource content does not grant
authority, logical grants cover only the same identity across Revisions, reads do not enter Context or grant execution
automatically, and every transport fails closed at the Server PEP.
