---
Proposal Name: powercontext_permission_control
Start Date: 2026-08-27
RFC PR: #0000
Related RFCs: 0011, 0048, 0072
---

# Summary

This RFC proposes a unified permission model for PowerContext across enterprise (B2B) and consumer (C2C) use cases. It covers authorization, delegation, handoff, and auditing for memories, context artifacts, and related operations.

The model keeps the permission core small and stable: Subject, Resource Scope, Action, and request Context. Tenant isolation is a mandatory system boundary. Team, project, region, environment, business line, scenario category, and data domain are not fixed authorization hierarchy levels; they are configurable Context Attributes.

This RFC does not define Prompt content, inheritance, or resolution. User/Agent-level custom Prompt is a memory/context capability and should be designed in a separate RFC. Prompt is included here only as a resource type whose access can be controlled.

# Motivation

PowerContext serves enterprise applications, Agents, individual users, and local processes. Different callers need different access: one caller may search personal memories, another may write project memories, and another may consume a handoff package. Adding a fixed role and permission dimension for every business scenario would make the model grow quickly and would not support user-defined scenario boundaries.

The design needs to answer:

1. Which PowerContext resources a principal may access.
2. Which resource objects an Action operates on, including compound operations.
3. How B2B administrators, users, Agents, applications, and processes are authorized.
4. How a C2C user can grant limited access to their own Agent or application.
5. How access to source context works after a Handoff.
6. How a process can use user-defined scenarios without treating a PID as a durable security identity.

# Guide-level explanation

## 1. The four fixed permission dimensions

Authorization is evaluated as:

```text
Authorize(subject, scope, action, context) -> AuthorizedAccess | Deny
```

- `Subject`: the caller identity, including User, Agent, Application, and Process/Workload. A process is not a durable identity; process permissions are bound to a verifiable Service or Workload Identity.
- `Resource Scope`: the tenant, ownership, and lifecycle boundary of a resource. A Scope may represent a personal domain, team domain, project domain, application domain, or an isolated Handoff domain.
- `Action`: an operation on a resource, such as `memory.search` or `handoff.consume`.
- `Context`: trusted request context and attributes, such as scenario, environment, region, data classification, time, and device state.

Tenant is an immutable isolation boundary and cannot be customized or crossed by a user. The platform exposes only a small set of stable concepts; other business dimensions are expressed as Context Attributes.

## 2. B2B and C2C authorization entry points

B2B uses administrative layers without requiring every business dimension to become a fixed role:

- Tenant Admin manages tenant security boundaries, principal registration, global policies, and emergency access constraints.
- Authorization Domain/Project Admin manages PolicySets within the tenant boundary, such as binding an application class to a project Scope.
- Scope Owner grants access within a specific Scope and may issue limited delegation to an Agent or application.

C2C uses a Personal Domain by default. A user explicitly authorizes an Agent or Application with a Scope, Action set, Context conditions, and expiry. The consent UI should explain what the principal can do, where it can do it, and when the grant expires; the user should not need to understand role inheritance.

## 3. Process-level and scenario-level customization

Process-level authorization is bound to a stable `Workload Identity`, such as a signed service identity, an application installation, or a local Agent instance—not to a PID, command line, or ephemeral port. Each request carries:

- `subject_id`: a verifiable user, Agent, application, or workload identity.
- `scope_id`: the resource Scope to access.
- `action`: the requested operation.
- `context`: scenario and environment attributes for this run.

Users can create custom scenarios such as “customer-service review”, “driver profiling”, “production troubleshooting”, or “financial reconciliation”, then configure attributes and allowed Actions for each scenario. A scenario is a user-defined Context Attribute; it cannot bypass Tenant, Scope, or resource ACLs.

Example:

```json
{
  "name": "customer-service-review",
  "attributes": {
    "scenario.category": "customer_service_review",
    "data.classification": "internal",
    "environment": "production"
  },
  "allowed_actions": ["memory.search", "memory.extract"],
  "scope_refs": ["scope://tenant-a/project/cs"],
  "expires_at": "2026-09-30T00:00:00Z"
}
```

Every request attribute carries an origin and trust level:

- `system`: produced by the server or trusted runtime, such as tenant, subject, and Workload Identity.
- `managed`: configured by an administrator and validated by the server, such as project, scenario category, and data domain.
- `request`: declared by the caller. It can satisfy an explicit limiting condition but cannot elevate the principal.

# Reference-level explanation

## 1. Scope and resource boundaries

Every PowerContext resource belongs to an explicit Scope. The platform may support the following Scope types without turning all of them into fixed authorization hierarchy levels:

| Scope type | Typical use | Default manager |
| --- | --- | --- |
| Tenant | Tenant isolation and global governance | Tenant Admin |
| Personal | C2C personal memories and context | User |
| Team/Project | B2B shared resources | Scope Owner |
| Application/Workload | Resources required by an application or process | Application Owner |
| Handoff | A protected resource created for one handoff | Creator or designated receiver |

Scope answers “where does this resource belong?”. PolicySet and AuthorizationGrant answer “which subject may perform which Action under which conditions?”. A principal type such as Agent does not imply access to a resource.

Core PowerContext resources include:

- `Memory`: long-term or short-term memory, versions, provenance, and metadata.
- `Source`: raw input, sessions, or external references used to extract memories or build context.
- `Artifact`: reusable context packs, experiences, skills, summaries, and similar products.
- `Candidate`: a memory candidate pending review or persistence.
- `Prompt`: a Prompt resource whose access may be controlled; this RFC does not define Prompt functionality.
- `Handoff`: a handoff request, package, and consumption credential.
- `Audit`: authorization, denial, delegation, handoff, and emergency-access events.

## 2. Actions and resource objects

An Action is bound to a resource object. Having an Action on one resource does not grant the same-named Action on another resource.

| Resource | Typical Actions | Operated object |
| --- | --- | --- |
| Scope | `scope.read`, `scope.grant`, `scope.revoke` | Scope metadata, membership, and grants |
| Source | `source.read`, `source.attach` | Raw input, sessions, and external references |
| Memory | `memory.search`, `memory.read`, `memory.write`, `memory.update`, `memory.delete` | Memory content, versions, and metadata |
| Memory/Candidate | `memory.extract`, `candidate.review`, `candidate.accept`, `candidate.reject` | Extraction jobs, candidates, and review results |
| Artifact | `artifact.read`, `artifact.write`, `artifact.share`, `artifact.revoke` | Context packs, experiences, skills, and similar products |
| Prompt | `prompt.read`, `prompt.use`, `prompt.bind`, `prompt.approve`, `prompt.revoke` | Prompt access and binding relationships |
| Handoff | `handoff.prepare`, `handoff.read`, `handoff.consume`, `handoff.accept`, `handoff.share`, `handoff.commit`, `handoff.revoke` | Handoff package, grants, and state |
| Audit | `audit.read`, `audit.export` | Audit events and compliance exports |

`memory.extract` is a compound operation. It requires read access to the relevant Source, Artifact, and current Memory, and `memory.write` in the target Scope when the result is persisted. These internal dependencies apply only to the request and must not be interpreted as permanent grants to other Source or Memory Actions.

## 3. Handoff authorization semantics

A Handoff is an independently protected resource. The creator selects the receiver, accessible Scopes, Actions, data range, and expiry. The receiver can read or consume the package only when the handoff grant is valid.

Handoff does not create implicit permission inheritance:

- A receiving Agent does not obtain all permissions of the creating Agent or User.
- A source Scope referenced by a Handoff is not automatically opened to the receiver.
- The receiver can use only the resource references and Actions granted by the Handoff.
- Once a Handoff expires, is revoked, or reaches a terminal state, the server denies operations outside the allowed state transition.

The default mode should be `Reference`: the receiver gets a controlled reference, and source reads still enforce the source Scope policy. The creator may explicitly choose `Snapshot` when the business requires a copy of the permitted data in a Handoff Scope. A Snapshot remains subject to classification, redaction, expiry, and revocation rules.

Typical flow:

```text
creator prepare -> server validates and creates Handoff
receiver accept/read -> validates receiver, Scope, Action, state, and expiry
receiver consume/commit -> records consumption and may close or revoke by policy
```

`handoff.share` is allowed only when re-sharing is explicitly permitted, and the new grant cannot exceed the sharer’s current effective permissions.

## 4. Context Attributes and custom scenarios

The fixed permission core evaluates policies without enumerating every organization structure. A PolicySet may refer to common attributes such as:

```text
context.scenario.category
context.project
context.team
context.region
context.environment
context.data.classification
context.device.trust_level
context.time_window
context.request.origin
```

A user or tenant may add a namespace and value constraints, such as `context.business_line` or `context.customer_tier`. To prevent callers from forging privileged attributes, each policy condition declares the required trust level. A `request` attribute can narrow access but cannot assert facts such as “administrator” or “internal data”.

Recommended custom-scenario workflow:

1. Create a scenario template with a name, attribute schema, and allowed values.
2. Bind the template to one or more Scopes.
3. Add a minimal Action set and data-classification ceiling in a PolicySet.
4. Bind a Workload Identity, Agent, or User to the scenario.
5. At runtime, validate Subject, Scope, Action, Context, and expiry, then emit an audit event.

A scenario can be disabled at any time. Disabling affects subsequent requests; whether already issued short-lived AuthorizedAccess is revoked immediately is controlled by the tenant revocation policy.

## 5. PolicySet, grants, and delegation

### PolicySet

A PolicySet is a long-lived policy collection for tenant, project, and application governance. The recommended default is deny-by-default, with explicit resources, Actions, conditions, and effects:

```json
{
  "policy_id": "pol_cs_review",
  "subject_selector": {"workload_id": "agent://qa-bot"},
  "resource_selector": {"scope_id": "scope://tenant-a/project/cs"},
  "actions": ["memory.search", "memory.extract"],
  "conditions": {
    "context.scenario.category": {"equals": "customer_service_review"},
    "context.data.classification": {"in": ["public", "internal"]}
  },
  "effect": "allow"
}
```

Deny rules and tenant-level constraints take precedence over ordinary allow rules. PolicySet changes require a version, publisher, approval state, and effective time.

### AuthorizationGrant

An AuthorizationGrant is a short-lived grant for user consent, temporary access, or a Handoff. It contains at least the issuer, grantee, Scope, Actions, Context conditions, expiry, re-delegation flag, and revocation state.

Delegation must satisfy:

```text
new_grant ⊆ issuer_effective_permissions
```

A grant cannot cross the tenant boundary or exceed Scope Owner limits, data-classification ceilings, or the Handoff’s own range. The authorization service issues a short-lived `AuthorizedAccess`; callers cannot construct principals, roles, owners, or permissions themselves.

## 6. Runtime authorization flow

A PowerContext request should go through these stages:

1. Authenticate and verify the Subject and Workload Identity.
2. Resolve the target resource, its Scope, and resource state.
3. Combine PolicySet, AuthorizationGrant, resource ACL, Handoff state, and tenant constraints.
4. Validate Context Attribute origin, trust level, time window, and data classification.
5. Expand compound Actions into minimum dependencies and authorize each object.
6. Issue a short-lived AuthorizedAccess containing a `decision_id`, range, Actions, expiry, and revocation information.
7. Execute the read/write operation and record an allow or deny audit event.

The authorization result should contain at least:

```json
{
  "decision": "allow",
  "decision_id": "dec_123",
  "subject_id": "agent://qa-bot",
  "scope_id": "scope://tenant-a/project/cs",
  "actions": ["memory.search"],
  "expires_at": "2026-08-27T10:05:00Z",
  "constraints": {
    "data.classification": ["public", "internal"]
  }
}
```

The client carries only a server-issued access credential. The server must not trust client-supplied owner, tenant, role, or permission fields.

## 7. Suggested data model

```text
Subject
  subject_id, subject_type, tenant_id, workload_id, status

Scope
  scope_id, tenant_id, parent_scope_id, scope_type, owner_subject_id, status

PolicySet
  policy_id, tenant_id, version, statements, state, effective_at

AuthorizationGrant
  grant_id, issuer, grantee, scope_id, actions, conditions,
  expires_at, delegable, revoked_at

Handoff
  handoff_id, source_subject, receiver_subject, handoff_scope_id,
  mode, references, allowed_actions, state, expires_at

AuthorizedAccess
  decision_id, subject_id, scope_id, actions, constraints,
  issued_at, expires_at, revocation_ref
```

Every server-side object must carry `tenant_id`; tenant isolation should be enforced both in persistence and service layers. Deletion, revocation, and Handoff closure should be auditable state transitions rather than relying only on clients deleting local credentials.

## 8. Auditing, emergency access, and security boundaries

The following events must be audited: grant creation, modification, and revocation; policy publication and denial; memory write/delete; Prompt use; Handoff creation, read, share, and consumption; administrator access; and emergency access.

Audit fields should include at least:

```text
event_id, time, tenant_id, subject_id, workload_id,
resource_refs, action, decision, decision_id,
policy_refs, reason, source_ip, request_id
```

The system may support Break-glass access, but it must require a reason or ticket, the shortest practical expiry, limited Scope and Actions, required approval, complete audit, and post-event review. Emergency access must not become a default superuser path.

If a tenant root administrator is required, model it as a tightly constrained system-management principal. It can manage tenant policy and grants, but should not by default read personal Memory, Source, or Handoff content. Reading another user’s private content requires an explicit Break-glass flow with time, scope, reason, and audit constraints.

# Drawbacks

- Combining resources, Scopes, policies, and Context Attributes increases the complexity of the authorization service and SDKs.
- Custom Context Attributes improve flexibility but require schema, trust, and lifecycle governance to avoid semantic drift.
- Reference Handoff requires the receiver to access the source Scope at runtime, which can add latency and failure points.
- Snapshot Handoff creates data-copy, revocation, and sensitive-data-spread risks.
- Authorization decisions, short-lived credentials, and revocation need predictable consistency under high concurrency.

# Rationale and alternatives

## Small fixed core plus custom Context Attributes

This balances understandability and extensibility. Subject, Scope, Action, and Context keep APIs and the authorization engine stable, while each organization can define its own structures and business scenarios.

## Not RBAC only

RBAC is easy to understand but does not naturally express Handoff, one-time grants, Workload Identity, data classification, and scenario conditions. Roles can be one type of PolicySet selector, but not the only model.

## Not ABAC only

A model based only on attributes makes resource ownership unclear and is vulnerable to untrusted request attributes. Scope first establishes resource ownership; trusted attributes then refine the decision.

## No default maximum-permission administrator

Managing authorization and reading all business data are separate capabilities. A default all-powerful administrator would increase the blast radius of insider misuse and credential leakage. A constrained Break-glass flow covers operations while preserving least privilege.

# Prior art

This RFC reuses PowerContext’s existing RFC boundaries for remote access, Handoff, scoped statistics, and artifacts, and unifies them under Subject/Scope/Action/Context authorization.

# Unresolved questions

1. Should Scope inheritance have fixed rules, or should it be expressed entirely by PolicySet?
2. Should Reference Handoff read source data using real-time authorization, or may it cache a short-lived decision?
3. Should the schema, version, and namespace of custom Context Attributes be tenant-managed or platform-managed?
4. What consistency window should be supported for authorization caches and revocation propagation?
5. Should C2C consent add step-up authentication, device binding, or stronger confirmation for high-risk Actions?
6. In which memory/context RFC should the concrete Prompt model, versioning, and runtime resolution be defined?

# Future possibilities

- A tenant policy simulator showing which Actions a principal can perform for a given Context.
- Scenario-template marketplace and policy linting for attribute conflicts, over-broad Scopes, and invalid delegation.
- Data minimization, field-level redaction, and receiver receipts for Handoff.
- Correlation of authorization decisions with usage, anomaly detection, and compliance reports.
- Local SDK pre-checks while retaining the server authorization service as the final authority.
