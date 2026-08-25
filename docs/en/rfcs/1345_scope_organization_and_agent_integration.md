- Proposal Name: `scope_organization_and_agent_integration`
- Start Date: 2026-08-21
- Related Discussion: [oceanbase/powercontext#1238](https://github.com/oceanbase/powercontext/pull/1238),
  [oceanbase/powercontext#1219](https://github.com/oceanbase/powercontext/issues/1219)
- Related RFCs: [RFC 0019](0019_local_source_memory_runtime.md), [RFC 0028](0028_context_pack.md),
  [RFC 0048](0048_handoff_artifact.md), [RFC 0082](0082_handoff_report.md)

# Summary

This RFC defines how PowerContext represents hierarchy, isolation, and sharing for durable state.

Scope is the only durable ownership boundary. Sources, Artifact revisions, and statistics belong to the Scope that
produced them. Memory, Handoff, Experience, and Skill are the currently defined Artifact families. A request binds
one current Scope and writes only to that Scope.

Scopes have two independent relationships. Organization Parent organizes results that can continue and be handed
off independently. Context Reference expands the read set used by Prepare Context. Parent grants no read capability.
A Context Reference changes no ownership and is not transitive. Cross-Scope delivery uses exact Artifact
publication.

The Dashboard, Statistics, and Handoff Report no longer depend on a separate Project identity. They aggregate a
caller-selected set of Scopes. `All` means every observable Scope. A Report Root means one top-level Organization
Scope and its descendants. Both are query selections, not durable objects.

The Scope application layer generates opaque `scope_id` values. An Agent integration resolves the current Scope from
an explicit binding, a durable external binding, or the configured `default_scope_id`, in that order, and fixes it
before every request. The `default_scope_id` points to an ordinary Scope. It is a host binding fallback, not a Scope
type, hierarchy, sharing rule, or observation range. A repository, branch, directory, session, or Agent identity may
help find a binding, but does not derive `scope_id` or establish hierarchy.

This RFC applies to one user or one existing authorization domain. Multi-tenant identity and cross-tenant sharing
are out of scope.

# Motivation

The current Runtime isolates state by `scope_id`, but three different models have formed around it. Agent plugins
derive a Scope from a Git remote or directory. The Dashboard lists statically configured Scopes. Handoff Report uses
a separate `project_id` and Workstream Catalog. These models cannot consistently answer:

1. where state from the current request belongs;
2. which shared material the request may read;
3. which results have an organizational containment relationship;
4. which material stays local and which material can be delivered;
5. which range a Dashboard or report aggregates.

One identifier or one parent-child relationship cannot carry all these responsibilities. A session can handle
several items of work in sequence. Several Agents can share one result or progress in isolation. A repository,
branch, or directory identifies an external resource location. Report containment must not change the execution
read set.

This RFC keeps one durable boundary and defines organization, reads, delivery, and observation separately.

# Guide-level explanation

## Domain model

Read this model by establishing state ownership first, then the read, organization, delivery, and observation ranges.
The latter four behaviors take Scopes as input. They do not create another ownership boundary.

Scope corresponds to a namespace or partition in memory and storage systems. Objects inside a Scope have separate
responsibilities:

| Concept | PowerContext representation | Responsibility |
| --- | --- | --- |
| Source | Source | Preserves original input and its provenance |
| Artifact | Artifact revision | Preserves versioned state for reference, review, and delivery |
| Artifact family | Memory, Handoff, Experience, Skill | Distinguishes Artifact content, generation, and lifecycle |
| Statistics | Scope-local records | Preserves runtime measurements traceable to one Scope |
| Index | Runtime retrieval projection | Accelerates retrieval and can be rebuilt from data in the Scope |

The Memory family preserves recallable state. The Handoff family preserves a definite continuation snapshot.
Experience and Skill preserve generated and reviewed results. All four share the Artifact identity, revision, and
provenance primitives. Each family defines its own generation and state transitions.

Sources, Artifact revisions in every family, and statistics belong to one Scope. Physical storage layout and index
implementation may change without changing logical ownership.

```text
Scope
|
+-- Sources
+-- Artifacts
|   +-- memory
|   +-- handoff
|   +-- experience
|   `-- skill
+-- Statistics
`-- rebuildable Index
```

Five behaviors operate around a Scope:

| Behavior | Representation | Question answered |
| --- | --- | --- |
| Write binding | current Scope | Where does this request write? |
| Shared read | current Scope + Context References | Where may Prepare Context read? |
| Organization | Parent + descendants | Which Scopes form a result and its sub-results? |
| Delivery | exact publication | Which revisions enter another Scope? |
| Observation | `all`, `exact`, or `subtree` selection | Which Scopes do the Dashboard and reports aggregate? |

```text
Agent request
     |
     | bind
     v
current Scope -------- Context References
     |                         |
     | write                   | read
     v                         v
Sources / evidence -> Artifact revisions
                      [memory | handoff | experience | skill]
                       |
                       | exact publication
                       v
                 another Scope

Scope selection -> aggregate -> Dashboard / Statistics / Handoff Report
```

These five behaviors may act on the same Scopes, but none can be derived from another. Agents, sessions, workspaces,
repositories, and branches are external identities, provenance, or binding signals. They are not Scopes and do not
change these rules.

## Binding and Scope creation

Every Agent request first binds a current Scope. An integration can expose two operation surfaces. They are not
durable objects:

| Surface | Scope selection | Behavior |
| --- | --- | --- |
| scope-bound | Binding fixes the current Scope | Prepare Context, Capture, and operations for each Artifact family |
| multi-scope | Operations name authorized Scopes explicitly | Discover, create, Parent, Context References, binding, publication, and report |

```text
resolve current Scope
          |
          +-- explicit binding
          +-- durable external binding
          `-- configured default_scope_id
                         |
                         v
                scope-bound requests

independent boundary -> create ordinary Scope -> persist binding
```

Each deployment provides one ordinary Scope as the default binding target. The system uses it only when a request
has neither an explicit binding nor a durable external binding. Defaultness is host configuration, not Scope
metadata, and grants no additional read, write, publication, or observation capability. Changing the
`default_scope_id` affects only later requests without a binding. It does not move data, change Parent, or overwrite
saved bindings.

The Scope application layer allocates new `scope_id` values. An integration creating a Scope supplies a title,
summary, optional Parent, Context References, external references, and a stable idempotency key. It does not compose
or hash a `scope_id` from a repository remote, path, branch, Agent, or session.

Create a Scope only when at least one condition applies:

- state must be isolated from the current work;
- work needs independent continuation or authorization;
- a result needs an independent handoff, delivery, or observation boundary.

Otherwise, reuse the resolved Scope. Do not create a layer for every turn, Agent, session, directory, or lifecycle
phase. A workspace, repository, or orchestrator lifecycle can find an existing binding or provide external
references and idempotency input. The binding is resolved before Prepare Context and remains fixed for the request.
A scope-bound operation does not accept an arbitrary `scope_id` that overrides the binding.

Scopes, Agents, Sub-agents, and sessions have many-to-many relationships. A new session does not automatically create
a Scope. Resume reuses its saved binding. Switching models or sequentially switching Agents also keeps the current
Scope. One session may switch binding at a request boundary. Parallel Agents create Scopes only when intermediate
state or results must remain independent. An integration without a stable external identity lets the host select a
Scope explicitly and persist the binding.

## Organization and Context sharing

Each Scope has at most one Organization Parent. A Parent means the child is an independent sub-result and takes part
in navigation and aggregation for the parent subtree. Parent relationships are acyclic. Sharing a repository,
session, Agent, or Context does not establish a Parent.

A Scope can read several Scopes through Context References, and several Scopes can reference the same Scope:

```text
personal conventions ----+
team knowledge ----------+----> current Scope
repository notes --------+
```

A Context Reference only expands the read set for later Prepare Context calls. It does not expose all local state or
Handoff history from the referenced Scope. It grants no write, reverse read, or transitive read capability. One-time
input and result exchange uses exact publication instead of a long-lived reference.

## Material ownership and delivery

Every durable Artifact revision belongs to the Scope that produced it. Publication names a source Scope, an exact
revision, and a target Scope. It creates a new target revision with source records and copies no other source-Scope
state.

| Material | Default handling |
| --- | --- |
| Sources and unselected Artifact revisions | Stay in the producing Scope |
| Feature deliverables and validation results | Publish after selecting an exact revision |
| Reusable knowledge | Review, then publish to a shareable Scope |
| Personal information, debugging fragments, and rejected results | Do not select or publish |

Publication requires source-read and target-write authorization. Parent, Context Reference, or knowledge of a
`scope_id` cannot replace authorization. The target revision retains the source Scope, exact source ref, content
digest, and resolvable provenance.

Each Scope retains its own Handoff. A subtree report preserves hierarchy and the exact Handoff for each Scope. It
does not merge Handoff histories. An exact report can focus on one child.

## Dashboard and reports

The Dashboard, Statistics, and Handoff Report are different projections over the same observation selection:

| Selection | Meaning |
| --- | --- |
| `all` | Every Scope the caller may observe |
| `subtree(root_scope_id)` | One top-level Organization Scope and its descendants |
| `exact(scope_ids)` | A Scope set used for temporary focus or a stable report |

`All` is the default observation selection. The configured default Scope is only a write-binding fallback. It is not
`All` and does not automatically become a Report Root. A Report Root is a selection of a top-level Scope, not a
Project, Scope type, or durable View. A sub-scope may be searched, focused, and reported exactly, but does not
automatically become a top-level View.

```text
Scope selection
     |
     +-- Overview: aggregate totals
     +-- Organization: preserve Parent structure
     `-- Handoff: exact Handoff or no_handoff per Scope
```

Context References appear separately as Context inputs. They do not enter Organization, statistics, or Handoff
selection. When a saved root is invalid, the Dashboard falls back to `all`. It does not infer another root from a
workspace, repository, branch, session, or Agent.

## Representing scenarios

The following scenarios combine the binding, Organization, Context Reference, publication, and observation selection
defined above. They do not define Feature, Bug, Project, Agent, or Session Scope types. Directory layout is orthogonal
to every scenario. One Scope may use several directories, and several Scopes may use the same directory.

| Scenario | current Scope binding | Scope creation | Sharing and delivery | Observation |
| --- | --- | --- | --- | --- |
| One Session for one item of work | Session binds the work Scope; otherwise use the default Scope | Create only when the work needs an independent boundary | Context References read long-lived material | Exact Scope or its containing subtree |
| One Session switches work | Switch at request boundaries | Use a different Scope for each independent item | No automatic sharing between items | Focus separately or aggregate under a shared root |
| Peer Agents collaborate | Each request binds its agreed Scope | Reuse when all state is shared; create children for isolation | Reference shared material and publish selected results | Root subtree preserves each Handoff |
| A main Agent drives Sub-agents | A Sub-agent binds the caller Scope or an explicit child | Create a child only for independent progress | Exact input enters the child; exact result returns to the parent | Show with parent or focus the child exactly |
| One Agent hands work to another | Receiver binds the original Scope, or the target Scope when the boundary changes | Agent change alone creates no Scope | Read the committed Handoff in one Scope, or publish the exact Handoff and dependency revisions across Scopes | Show one continuous Scope or separate source and target Scopes |

### One Session for one item of work

```text
session -> feature Scope
```

The session does not create another Scope. All writes belong to the feature Scope. Repository knowledge and personal
conventions are read through Context References. Work ends with a Handoff for that Scope.

### One Session switches between items of work

```text
request 1: session -> bug-fix Scope
request 2: session -> feature Scope
request 3: session -> bug-fix Scope
```

The integration switches the binding explicitly before Prepare Context and reuses the existing Scope when returning
to earlier work. Ordinary natural language cannot silently change a binding during a request. The two Scopes share a
Parent only when they belong to the same result organization.

### Peer Agents collaborate

The current Runtime has one Source-processing line, Memory head, and Handoff lifecycle per Scope. Several Agents bind
the same Scope when they can share that state. When intermediate state or continuation must be isolated, create a
child for each independent result:

```text
feature Scope
|-- agent-a result
`-- agent-b result
```

A child does not automatically read its parent or siblings. Use Context References for continuous sharing and exact
publication to deliver accepted results into the feature Scope.

### A main Agent drives Sub-agents

A Sub-agent reuses the caller's current Scope when it shares all caller state. It binds an explicit child when it
needs independent progress, handoff, or authorization:

```text
main Scope
|-- research result
`-- validation result

input:  selected Artifact -> child
result: child Artifact    -> main Scope
```

Parent represents result decomposition only. The host delivers selected input to the child and selects deliverable
output afterwards. Unselected debugging fragments, personal information, and intermediate material stay in the
child.

### One Agent hands work to another

Changing Agent identity does not change work ownership. When the receiving Agent continues the same work, the
delivering Agent commits a Handoff Artifact in the current Scope. The receiver then binds the same Scope and restores
the work from that Handoff and the Memory in the Scope:

```text
agent-a -> commit Handoff -> work Scope <- bind <- agent-b
```

This flow changes only the binding. It creates no Scope, sets no Parent, and performs no publication. The Handoff
revision, Memory-family state, and later Artifact revisions remain in one Scope, so the Dashboard and reports show
one continuous work history.

A handoff crosses Scopes only when ownership, isolation, authorization, or independent reporting changes. The host
selects the exact Handoff revision and the Artifact revisions required for continuation from the source Scope, then
publishes them into the target Scope:

```text
agent-a -> source Scope
              |
              | exact Handoff + selected Artifact revisions
              v
         target Scope <- agent-b
```

The receiving Agent binds the target Scope and uses the published material as its initial continuation input. Artifact
revisions produced afterwards in every family belong to the target Scope. A cross-Scope handoff names an exact
Handoff revision and never resolves `latest` in the source Scope. If the receiver needs ongoing read access to the
source Scope and has authorization, the host can add a separate Context Reference. A Context Reference does not
replace Handoff publication or bring unselected personal information and intermediate material into the target.

The same rules apply outside coding. A long-running customer matter can be a root, with independent research and
approval results as children. Team knowledge is read through Context References, and an accepted conclusion is
published into the customer matter. The business names differ, but the boundary decisions do not.

# Reference-level explanation

## Scope identity and metadata contract

A new Scope has these creation semantics:

```text
create_scope(
    title,
    summary,
    parent_scope_id?,
    context_refs[],
    external_refs[],
    idempotency_key,
)
```

The Scope application layer generates a cryptographically secure 128-bit payload and encodes it as `scp_` followed
by 26 lowercase Crockford Base32 characters. A `scope_id` is globally unique, opaque, and immutable. Existing formats
remain readable. The format constrains only newly generated identifiers.

A title and summary are required for a new Scope. Metadata updates use a version condition. The `scope_id` does not
change with metadata, Parent, or binding changes. Retrying the same request with the same caller and idempotency key
returns the same Scope. Different parameters return a conflict.

## Relationship and data-flow contract

1. Writes enter only the current Scope.
2. Context comes only from the current Scope and explicit Context References.
3. Parent grants no read, write, or publication capability.
4. A Context Reference is not transitive and does not enter report hierarchy.
5. Parent relationships are acyclic, and each child is an independently continuable, handoff-capable, or observable
   sub-result.
6. Reparenting changes no Context Reference, binding, or Artifact ownership, including Handoff history.
7. Publication delivers only selected exact revisions and preserves their source.
8. A `scope_id` is not a credential. Every cross-Scope behavior still requires authorization.

## Integration contract

A scope-bound request resolves exactly one current Scope from an explicit binding, a durable external binding, or
`default_scope_id`, in that order, and keeps it fixed until the request ends. A session may switch its binding at a
request boundary. A multi-scope operation uses exact Scope IDs and limits Parent, reference source, publication
source, and target choices to the authorized integration range.

The current Runtime serializes mutations to one Scope unless an operation defines idempotent or conflict-safe
concurrency. Handoff has a linear head and uses version checks. Agents with independent objectives, state, or next
actions should not mutate one Scope concurrently.

For an Agent-to-Agent handoff within one Scope, the integration commits the source Scope Handoff before binding the
receiving Agent to that Scope. A cross-Scope handoff publishes the exact Handoff revision and every exact Artifact
revision required for continuation. It does not use `latest` from the source Scope. Changing Agent identity, session,
or workspace does not change Artifact ownership implicitly.

## Observation contract

Observation selection supports `all`, `exact`, and `subtree`. Report generation freezes the final Scope set and the
exact Handoff for each Scope. The Dashboard can save only `all` or a top-level Organization Scope as a top-level
selection. `exact` provides temporary focus and creates no durable View.

Aggregates retain their selection and Scope dimensions so totals can be traced back to individual Scopes.
Statistics, Dashboard, and Handoff Report use the same selection resolver. They do not maintain separate Project
membership, static Scope lists, or implicit workspace inference.

## Implementation

The Scope-local Runtime continues to partition Sources, Artifacts in every family, and Statistics by `scope_id`.
Implement the proposal with these changes:

1. The Scope application layer provides Scope creation, metadata, Parent, Context References, and observation
   selection resolution. It generates every new `scope_id`.
2. The Codex and Claude Code plugins remove ID derivation from Git remotes and directories. A plugin first resolves
   an explicit or durable binding, then falls back to `default_scope_id`. It calls `create_scope` only when the host
   establishes an independent boundary, supplying a title, summary, external references, and idempotency key, then
   persists the returned `scope_id`.
3. The Dashboard and Statistics share one observation selection resolver. The Dashboard submits `all` by default,
   `subtree` for a selected top-level Scope, and `exact` for temporary focus. The statistics service expands the
   selection and aggregates it while retaining per-Scope detail.
4. Handoff Report accepts the same observation selection, resolves its Scope set, and freezes the exact Handoff for
   every Scope. The report page and Dashboard use the same selection picker and root list.
5. Remove Project Catalog, Workstream Catalog, and Project membership. Remove `project_id` from Handoff Report
   requests, events, domain models, workspace binding, and storage. Scope metadata supplies each Workstream title,
   summary, and external references. Parent supplies the result hierarchy.

Existing Project data does not remain as another runtime model. Rewrite a Project used only as a saved range into an
observation selection preference. Rewrite a Project that denotes an independent result into a Scope, then set Parent
after confirming result containment. Do not convert records whose semantics cannot be determined automatically.
After rewriting the data, remove the Project and Workstream catalog tables.

# Drawbacks

- Separating Parent, Context Reference, publication, and observation selection requires integrations to maintain
  each behavior explicitly.
- Explicit publication adds one delivery operation but prevents intermediate material and personal information from
  spreading automatically.
- Scope has no business type, so the UI relies on title, summary, Organization, and external references for human
  understanding.
- Removing Project Catalog requires coordinated changes to OpenAPI, domain models, storage, the Dashboard, and Agent
  integrations.

# Rationale and alternatives

| Alternative | Decision |
| --- | --- |
| Parent represents both organization and sharing | Rejected because report reorganization would change the read set |
| Fixed-purpose or lifecycle Scope types | Rejected because business phases are not stable state boundaries |
| Encode repository, Agent, or hierarchy in `scope_id` | Rejected because external signal changes would invalidate identity |
| Flat Scopes with request-level filters only | Rejected because they lack stable result organization and a minimum sharing boundary |
| Promote all child material automatically | Rejected because it cannot separate deliverables, intermediate material, and personal information |
| Keep a separate Project identity as the report dimension | Rejected because it duplicates Scope organization and observation selection |

# Prior art

- RFC 0019 defines an integration-owned opaque business partition. RFC 0048 and RFC 0082 define exact Handoff and
  stable report inputs.
- NowledgeMem separates Space, Agent Identity, and Session, and expands reads through explicitly linked Spaces.
- CocoIndex separates Component Path, shared Context, and statistics grouping, showing that ownership, dependencies,
  and observation should not use one hierarchy.
- EverOS separates app/project partition, owner, session, and memory lineage. Shared material moves through common
  Knowledge or explicit copies.
- Mem0 and Graphiti provide flat addressing through caller filters but do not provide the result organization and
  exact delivery semantics required here.

# Future work

## Several Memory Artifacts in one Scope

This RFC preserves the RFC 0019 Runtime mapping. One Scope has one active Memory progression line. Its `scope_id`
selects the Source journal, trigger cursors, active Memory Artifact identity, and Handoff Artifact lifecycle together.
A Scope can own Artifacts in several families and several revisions, but that does not mean it can advance several
independent Memory heads.

A later RFC supporting several Memory progression lines in one Scope must define a stable Memory binding, Source
assignment, per-Memory cursors, Prepare Context selection, and concurrency conflicts. Such an extension does not
change Scope authorization, ownership, Handoff, or observation boundaries.

## Multi-tenancy

This RFC assumes the caller already operates within one authorization domain. Tenant identity, tenant-local Scope
namespace, and cross-tenant publication require a separate design. They cannot be derived from Parent, Context
Reference, or the `scope_id` format.
