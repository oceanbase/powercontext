---
title: "RFC 0000: Sharing, querying, and processing across Scopes"
description: "Define how Scopes express cascading business relationships, with sharing, queries, processing, and lifecycle capabilities for memory and RAG use cases."
---

- Proposal Name: `scope_sharing_and_processing`
- Start Date: 2026-11-09
- RFC PR: Pending

# Summary

This proposal extends PowerContext infrastructure for Agent context, using Scope structure to express business concepts requiring cascade management. Sharing starts with a standing authorization boundary. The system automatically discovers eligible material within it and pins the inputs for each run. Dream or subsequent processing in another Scope coordinates those inputs, resolves conflicts, and forms its own outputs. Reads and further processing start from the target Artifact. Scopes define ownership and access ranges, Artifacts store versioned content, and exact origins preserve input provenance.

Team/project memory and RAG are use cases. Applications own business mappings, sharing rules, and triggers. PowerContext enforces range resolution, authorization, processing, and lifecycle controls. Archive stops ordinary writes and processing in the named Scope while authorized consumers can still use its material in their own Scopes. Deletion requires an explicit request with separate completion conditions for access cutoff, resource cleanup, and physical erasure.

```text
Standing authorization + sharing rule -> automatic discovery -> pinned inputs
    +-- query -> results retain exact origins
    `-- Dream / processing -> reviewed target Artifact
Explicit Scope lifecycle operation -> bounded resource handling
```

# Motivation

People accumulate Artifacts in several work Scopes. Teams and projects need automatic access to eligible material within an agreed boundary, coordination of different conclusions, and their own Profiles, Skills, or other outputs. Users should be able to configure rules covering future content, with the system selecting inputs and initiating processing. Knowledge-base RFC 2570 covers document ingestion, chunking, retrieval, and deletion. Its use of a `knowledge-base` Artifact to organize documents is a transitional design. [Knowledge-base PR #2570](https://github.com/oceanbase/powercontext/pull/2570)

Reading sources, using them for processing, and disclosing team-memory conclusions each require authorization. Membership alone cannot determine those permissions. The transitional RAG design exposes another problem. Existing Artifacts have no substantive parent-child cascade relationship. Introducing a knowledge-base Artifact requires the Artifact layer to handle cascading lifecycle and processing, including child-document visibility after parent invalidation, pending jobs, and resource cleanup. This proposal extends ordinary Scopes so that their structure can express business concepts with cascade relationships and control data and processing within those ranges. Documents remain versioned Artifacts. [Object and deletion design in RFC 2570](https://github.com/oceanbase/powercontext/blob/71e842cd2a34a7943ab0adecb3542771e3da387e/docs/en/rfcs/2570-knowledge-base.md)

```text
Team/project memory                   Transitional RAG model
Authorized work Artifacts             knowledge-base Artifact -> documents
         |                                      |
         v                                      v
Target Profile or Skill               Artifact lifecycle + processing cascades
         +----------------------+---------------+
                                v
              Scope structure, sharing, processing, lifecycle
```

| Use-case requirement | Common capability needed | Use-case-specific logic |
| --- | --- | --- |
| Several contributors form project or team memory | Standing sharing boundaries, automatic selection, source authorization, one target, and review. | Input selection, acceptance and review workflows, and generation policy. |
| Libraries organize documents and retrieve across collections | Scope ownership, structure, shared entries, and bounded cross-Scope queries. | Document parsing, chunks, search projections, and domain configuration. |
| A collection closes, material is withdrawn, or deletion is requested | Write controls, visibility, constraints on in-flight work, and recoverable cleanup. | Business triggers, retention rules, and operation presentation. |

# Guide-level explanation

## Shared data and operation model

A Scope identifies data ownership. A Family is an Artifact kind, such as Experience, Profile, Skill, or the proposed document Family. Diagrams use display names. `Retry trace@3` means revision 3 of "Retry trace". Its full address includes Scope ID, Family, Artifact ID, and revision. Dream generates revisions from evidence. A Candidate is a proposed revision awaiting review.

```text
Scope                   Family       Artifact title   Revision
Alice's investigations  Experience   Retry trace      3
```

Applications configure standing sharing boundaries and selection rules. The system resolves each operation to Scope ranges and exact inputs and checks permissions at read, processing, and commit. Manual selection supports exceptions and corrections. Requests still pin addresses for reproducibility, but rules can generate those addresses automatically. One operation can read several sources and specifies one target Scope when saving its result.

```text
Selected source Scopes and exact Artifacts
                    |
             check each operation
                /           \
       query and read       process with current target
              |                       |
      qualified results       Candidate -> review -> target revision
```

## Team, project, and personal memory

In an Orders investigation, Alice and Bob save Experiences in their own work Scopes. The Orders project selects a Scope for its Profile, and the Reliability team selects another for its on-call Skill. These Scopes may belong to different structural subtrees. Using the material requires authorization for the operation.

```text
Alice's investigations / Retry trace@3 --------+
Bob's adapter reviews / Duplicate callback@1 --+-> Orders project Profile@4
                                                  -> Dream -> review -> @5

Authorized shift-review Experiences -> Reliability team Scope / on-call Skill
```

The project Profile says callbacks may be retried after timeout. Alice records a successful retry on a new channel using an idempotency key. Bob records duplicate processing on the legacy channel. If evidence explains the difference, Dream proposes a conditional revision with citations. Otherwise the current conclusion remains. A manual target edit requires reevaluating an old Candidate against the new baseline. A personal Profile can use the same processing path, subject to source authority, Family admission, and review.

A sharing boundary can cover existing and future eligible material in named work Scopes. Once usable Experiences from Alice's investigations and Bob's adapter reviews are approved for the Orders project, new Artifacts and revisions automatically become input candidates. Personal private Scopes remain outside the boundary. Rules specify use, processing identity, and output audience, with permissions rechecked at use. Member access can reuse existing Scope grants, with optional external directories. Applications select the target Scopes. [Scopes and access](../docs/workflows/scopes-and-access.md)

## Knowledge bases, knowledge spaces, and shared material

Knowledge spaces and knowledge bases are business concepts with cascade relationships. The example below maps those relationships to Scope structure. Scopes manage lifecycle and processing controls within structural ranges. Document Artifacts store revisions, and the document service maintains chunks and indexes. The parsing, durable jobs, and versioned passages from RFC 2570 can be adapted to this structure. [RAG document-processing proposal](https://github.com/oceanbase/powercontext/blob/71e842cd2a34a7943ab0adecb3542771e3da387e/docs/en/rfcs/2570-knowledge-base.md)

```text
Transitional RAG model
Workspace Scope -> knowledge-base Artifact -> document Artifacts
                   Artifact lifecycle and processing cascades

Business concepts with cascade relationships -> mapped Scope hierarchy
Workspace Scope
  +-- Interface Scope -> document revisions and processing resources
  +-- Help Scope      -> document revisions and processing resources
```

A knowledge space can correspond to an upper Scope or exist only in an application mapping. Below, Orders owns Interface and Help as structural children and is authorized to use the Runbook Scope owned by Operations. Products can display these relationships as ownership and sharing. Services execute the concrete structure, selection, and authorization operations.

```text
Orders space Scope                   Operations space Scope
  +-- owns Interface Scope             +-- owns Runbook Scope
  +-- owns Help Scope                           ^
  +-- selects shared Runbook Scope -------------+  authorized use

Application-only Orders entry -> [Interface, Help, Runbook] Scope IDs
```

An Orders query resolves authorized knowledge-base Scopes and invokes document retrieval, retaining Scope, document revision, and chunk identity. Sharing only revision 3 of the Callback runbook needs corresponding item and revision authority. It cannot expand into a search of the whole Runbook Scope. Processing hits into Topic Memory also needs a writable target and a supported input path.

Archiving Interface stops ordinary content writes there while other authorized spaces can still use its material. Explicitly deleting the Orders Scope and its structural descendants handles Interface and Help. The external Runbook Scope retains its own lifecycle. Removing a shared entry affects only that entry and use of its associated authority.

# Reference-level explanation

## Support status and implementation boundary

The implementation baseline is [master at fd9f1682b](https://github.com/oceanbase/powercontext/tree/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a). RFC 2570 is a design input. Documents, chunks, and knowledge-base jobs still need document-service integration. The table distinguishes existing contracts, host-composable paths, and proposed extensions.

**Current implementation (master)**

Orders can already organize Interface and Help, but search calls still target one Scope, and Dream resolves evidence and targets in its run Scope. Structure alone does not include child material in those operations. Public Scope archive and delete operations are absent.

```text
Orders Scope
+-- Profile: Service behavior@4
+-- Interface Scope
|   `-- Experience: Retry trace@3
`-- Help Scope
    `-- Experience: FAQ correction@1

Search(Interface, Experience) -> Interface hits only
Dream(Orders) -> evidence and target inside Orders only
No public Scope archive or delete operation
```

**Expected behavior (proposal)**

The proposal brings selected sources under common authorization, query, and processing controls so Orders can maintain its Profile using material from several Scopes. Applications can initially compose existing query calls. Dream across Scopes and Scope lifecycle still need implementation, while source ownership stays unchanged.

```text
Orders Scope [target]
+-- Profile: Service behavior@4 -> review -> @5
+-- Interface Scope
|   `-- Experience: Retry trace@3
`-- Help Scope
    `-- Experience: FAQ correction@1

Runbook Scope [outside Orders]
`-- Experience: Timeout notes@2

Authorized inputs: Interface + Help + Runbook
  +-- query -> results keep each origin
  `-- Dream -> one Candidate in Orders
Explicit Orders subtree delete: Orders + Interface + Help only
```

| Capability | Existing or composable | Proposed extension |
| --- | --- | --- |
| Organization and selection | Scope identity, acyclic single-parent structure, direct Context References, and observation selection. | Resolve eligible inputs under standing rules, bound expansion, and pin the exact list for each run. |
| Authorization and sharing | Scope and supported logical Artifact grants, revocation and expiry, and publication for supported Families. | Establish authority for processing, disclosure, and entry/revision restrictions. |
| Query and read | Single-Scope, single-Family search and limited direct-Scope Prepare recall. Hosts can aggregate authorized per-Scope calls. | Specify result identity, budgets, failures, and item-level candidate filtering. Integrate RAG chunk retrieval per Scope. |
| Processing in a target | Local Dream, evidence manifests, review, and conditional Family writes. | Preserve source Scope and input views through evidence and commit, maintaining processed outputs. |
| Lifecycle | Relationship version checks, Binding lifecycle, and Family state. An internal generic processing-status projection is implemented with no public generic entry. | Scope archive, reopen, explicit deletion, and resource participation. |

## Scope identity, structure, and entries

**Supported.** A Scope ID is opaque. The descriptor holds a title, external references, `parent_scope_id`, `context_references`, and a version. Relationship changes use `expected_version`. A Scope has at most one structural parent and the parent relation is acyclic. Context References currently hold only Scope IDs for direct Prepare inputs. [Scope model](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/scope/models.py#L55) and [relationship validation and updates](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/scope/application.py#L195)

The diagrams use display names from the Orders example. A tree branch between Scopes is a structural parent-child edge. A branch to an Artifact places it in that Scope. `..>` marks a reference or selection path, and `~~>` marks evidence provenance. References grant no authority. Proposed document resources still need integration.

**Current implementation (master)**

Interface and Help are structural children of Orders. Runbook belongs under Operations and participates in Orders Prepare only through a direct reference. Current subtree selection returns descriptors, and the Skill evidence relationship shown here stays within Interface.

```text
Orders Scope                                 Operations Scope
+-- Profile: Service behavior@4              `-- Runbook Scope
+-- Interface Scope                              `-- Experience: Timeout notes@2
|   +-- Experience: Retry trace@3
|   `-- Skill: Callback checks@1
|         ~~> Retry trace@3 [same Scope]
`-- Help Scope
    `-- Experience: FAQ correction@1

Orders Scope ..> Runbook Scope [direct Context Reference]
Prepare(Orders): Orders + direct Runbook, for supported Families
subtree(Orders): descriptors of Orders, Interface, Help
```

**Expected behavior (proposal)**

The same tree supports explicit range operations, while Profile evidence addresses extend to different source Scopes. The box marks one explicitly selected subtree. Runbook remains outside it, so authorized queries or processing can use it without references or provenance adding it to that subtree deletion.

```text
Explicitly selected Orders subtree           Outside that subtree
+--------------------------------------+     Operations Scope
| Orders Scope                         |     `-- Runbook Scope
| +-- Profile: Service behavior@5      |         `-- Experience: Timeout notes@2
| +-- Interface Scope                  |
| |   +-- Experience: Retry trace@3    |
| |   `-- Skill: Callback checks@1     |
| `-- Help Scope                       |
|     `-- Experience: FAQ correction@1 |
+--------------------------------------+

Orders Scope ..> Runbook Scope [separately selected and authorized]
Orders / Service behavior@5
  +~~> (Interface Scope, Retry trace@3)
  +~~> (Help Scope, FAQ correction@1)
  `~~> (Runbook Scope, Timeout notes@2)

Explicit subtree delete selects boxed Scopes only.
References and lineage do not enlarge that set.
```

An application can map its external identifiers to Scope IDs and pass actual Scope addresses to services. Scope structure expresses business concepts that require cascade management. Registered services store document configuration against the mapped Scopes. The same approach can organize individuals, teams, projects, and knowledge spaces. Applications decide their relationships.

Structure determines actual descendants. References and application mappings determine selectable sources. Adding a shared entry preserves source identity and ownership. Services validate structure and concurrent versions and limit work per operation, including expanded nodes and traversal depth. Hosts can impose stricter organization rules in creation and move workflows and choose the hierarchy shown in the product.

## Selection, authorization, and sharing paths

**Supported.** `ScopeSelection` resolves `exact`, `subtree`, and `all` to Scope descriptors. HTTP checks `scope.read` on every exact target. Subtree and all require `server.observe`. An observation set does not establish content access. Logical Artifact resource keys omit revision, and an exact revision address does not narrow the grant. [Selection resolver](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/scope/application.py#L293), [observation access](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/server/app.py#L5732), and [Family authorization](1396_handoff_access_control.md)

**Current implementation (master)**

One identity can search Interface, read only the authorized FAQ Artifact in Help, and has no Runbook read authority. Placing these sources under a business entry does not change those permission results.

```text
Request identity
+-- Scope read grant: Interface Scope
`-- Artifact read grant: Help / FAQ correction

Orders Scope
+-- Interface Scope   [Scope search and exact reads allowed]
`-- Help Scope        [exact FAQ read allowed; Scope search denied]

Runbook Scope         [no read authority]
subtree(Orders) observes descriptors; HTTP requires server.observe
```

**Expected behavior (proposal)**

Combining inputs in one operation still requires checking each actual origin and rechecking required permissions at commit. Here the withdrawn Runbook read permission blocks the Candidate. Pinning an input version fixes its address. Access modes must enforce revision and use restrictions.

```text
Orders input rule [standing boundary + application mapping]
+..> Interface Scope                [whole-Scope input]
+..> Help / FAQ correction@1         [pinned Artifact input]
`..> Runbook Scope                  [required read permission withdrawn]

Selected inputs
+-- query/read -> authorize each actual origin
`-- Dream -> Orders / Service behavior@4
             `-- Candidate cannot commit with a required input revoked

Pinned address does not imply a revision-only grant.
Entry, use, and revision restrictions need an enforceable Access mode.
```

Fix the input selection, deduplicate exact addresses while retaining entry paths, and authorize the actual operation. Parentage and shared entries grant no access on their own. The current subtree resolver reads all Scope descriptors. Larger deployments need descendant lookup by parent and bounded work. Processing that requires complete inputs must narrow an over-budget selection and retry.

| Action | Existing contract and extension boundary |
| --- | --- |
| Search an entire Scope | Currently requires `scope.read` on it. An item grant supports its corresponding exact read. |
| Generate a target from evidence | Local Dream checks evidence, contribution, and target authority. Cross-Scope processing must check actual origins and the basis for target use and disclosure. |
| Inspect and approve a Candidate | Local review checks evidence, `scope.review`, and required target write authority. The extension must check actual origins as well. |
| Deliver an independent revision | Experience publication requires source `artifact.share` and target `scope.admin`. Topic Memory requires `scope.admin` on both ends. Supported copies have independent addresses and lifecycles. |
| Use a pinned revision only through an entry | Entry, purpose, revision, and withdrawal semantics still need a joint Access design. Reject modes whose restrictions cannot be enforced. |

If both the original and its conclusion are authorized for independent delivery, publish a copy and process it in the target. Private evidence with a team-visible conclusion needs rules for the processor, reviewer, audience, and withdrawal. Publishing the original delivers it to the recipient and cannot meet that confidentiality requirement. Existing read access does not include permission to disclose to a new audience. [Publication checks](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/server/app.py#L5752) and [Dream authorization](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/server/dream_access.py#L79)

Applications configure sharing boundaries, use, and audience as standing rules, and the system automatically discovers material within them. Rules can cover future content, with manual sharing as a supplementary path. Authorization can still expire or be revoked. Application mappings and rules cannot make it permanent.

### Continuous sharing and automatic selection

Sharing starts with a standing boundary, and the system automatically includes eligible material within it. The boundary covers source Scopes, eligible Artifacts and revision conditions, processing identity, permitted use, target, and output audience. It can cover future content without item-by-item sharing. Authorized integration workflows establish grants for newly included source Scopes under agreed rules. Structural attachment or a membership label grants no read access on its own.

Source ranges can resolve dynamically from Scope relationships or business mappings without enumerating Artifacts. Authorized integration workflows can also automate discovery and grants for new source Scopes. PowerContext does not need team or project membership tables.

**Current implementation (master)**

Whole-Scope read authority can cover new readable content within that Scope, and a logical Artifact grant can cover new revisions of that Artifact. Existing query and read calls can discover this material. The recurring selection rules, input checkpoints, and target processing proposed here are not an integrated cross-Scope flow today.

```text
Orders task identity [existing read grants]
+..> Alice work Scope
|    `-- Experience: Retry trace@3 -> @4 [readable under the Scope grant]
`..> Bob work Scope
     `-- Experience: Duplicate callback@1

Applications can call existing discovery and read operations.
Cross-Scope recurring selection and Dream are not integrated.
```

**Expected behavior (proposal)**

Applications configure the standing agreement as a reusable rule. Eligible new Artifacts and revisions automatically become available inputs. Each task pins the inputs it actually uses. The target Family still governs generation, review, and commit after automatic selection.

```text
Standing rule [version=2, target=Orders, permitted use and audience]
+..> Alice work Scope [current approved Experiences]
+..> Bob work Scope   [current approved Experiences]
`-- Alice private Scope [outside the range]

New revision or periodic discovery
  -> resolve rule + check current authority
  -> pin exact input list and target baseline
  -> Dream / processing -> Candidate -> reviewed Orders Artifact

Retry -> same pinned inputs; next run may select newer revisions.
```

PowerContext needs versioned selection-rule resolution, recoverable input processing state, and authorization checks shared with queries and Dream. Each run records the rule version, target, exact input addresses and digests, and target baseline. Applications can initially schedule calls, with event triggers added later. Processing state records the last successfully adopted inputs and target revision to coalesce duplicate notifications and skip processed inputs. It does not reuse the target's local Source cursor.

The current target revision is a comparison baseline, not a new fact supporting its own revision. An automatic target commit must not alone trigger repeated processing of itself. Input views and derived Sources retain evidence origins, and rules must avoid treating transformations or feedback of the same content as new independent support.

Rules execute only within current authority. After a rule change, source revocation, or target write suspension, tasks recheck whether they can run and commit. An obsolete rule version requires fresh resolution. A retry keeps its pinned inputs without switching to newer heads. Expansion, discovery, and processing must bound targets, inputs, and work, splitting excessive ranges into traceable tasks.

The target Scope owns rule configuration, input checkpoints, and task state. Archiving the target stops related ordinary processing. Deletion includes these resources in that Scope operation. An archived source Scope remains readable by external rules under current authority. A deleted source is reported unavailable according to rule completeness requirements, with later runs reevaluated.

Execution identity is separate from the data Scope. Starting a task in a team or project Scope still requires an authorized task identity to read sources and write the target. Applications translate business agreements into grants and rule configuration. PowerContext enforces them at use and commit. Use or audience restrictions beyond existing grants need an Access contract first.

## Cross-Scope queries and exact readback

**Supported and composable.** Artifact Search queries current searchable heads in one Scope and Family. Built-in Experience, Skill, Topic Memory, and Atomic Memory support it. Profiles support exact reads and explicitly selected Prepare snapshots. Prepare recalls Atomic Memory and Experience across current and directly referenced Scopes, and Profile snapshots can cover those Scopes. Topic Memory searches only the current Scope. Prepare with references does not support context reuse. [Search contract](../docs/workflows/search-artifacts.md), [Prepare selection](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/runtime/application.py#L1330), and [local Topic search](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/runtime/application.py#L2148)

**Current implementation (master)**

The three sources can be searched separately, with the application merging the results of authorized calls. Prepare already recalls direct references, but it does not query Interface and Help automatically because they are children of Orders.

```text
Orders Scope
+-- Interface Scope -> Search(Interface, Experience) -> Retry trace@3
`-- Help Scope      -> Search(Help, Experience)      -> FAQ correction@1

Operations Scope
`-- Runbook Scope   -> Search(Runbook, Experience)   -> Timeout notes@2

Application merges these separately authorized calls.
Prepare(Orders) reads current + direct references, not implicit children.
```

**Expected behavior (proposal)**

The business entry resolves to an explicit Scope selection, followed by bounded queries in the application or a proposed aggregate API. RAG also needs document retrieval integration. Results retain source Scope, document revision, and chunk identity for reauthorized readback. Parentage still grants no search authority.

```text
Orders Scope                                   Operations Scope
+-- Interface Scope                            `-- Runbook Scope
|   `-- document: Callback API@3 / chunk 2         `-- document: Timeout notes@2 / chunk 4
`-- Help Scope
    `-- document: Retry FAQ@1 / chunk 1

Orders query ..> [Interface, Help, Runbook] [explicit mapping + authority]
                   `-- document retrieval -> bounded result merge

One returned hit: (Runbook Scope, Timeout notes@2, chunk 4)
Readback -> recheck that exact origin and current resource state
```

Hosts can compose existing calls for queries across libraries. A PowerContext aggregate API would reuse the same selection and authorization rules and bound targets, candidates, and final results. Deduplicate Artifacts by `(scope_id, family, artifact_id, revision)`. Independent copies retain their addresses. When scores are not comparable, merge by per-target ranks with stable ordering. Complete mode returns an error if a required target fails. Partial mode must mark incomplete coverage and return only authorized results.

Under the RAG mapping above, the application resolves a business query to an authorized Scope selection. The document service returns `(scope_id, document_ref, chunk_id)` and original locators. It decides which chunks are eligible and owns indexing and ranking, while aggregation preserves those response semantics. Business `knowledge_base_ids` must resolve through the mapping to a Scope selection. Library configuration and job ownership must also fit Scope structure and lifecycle. Document retrieval, exact reads, and delivery still need implementation.

Permission to read an exact revision may also cover other revisions of that Artifact. A grant limited to one revision needs further restrictions. Sharing one document revision requires limiting candidates before scoring, truncation, and snippet generation. Searching the entire Scope and filtering later can distort ranking and omit valid hits. An index of only the latest revision cannot retrieve older content. Until the required indexing and access modes exist, supported paths are existing exact read authority or independent delivery for a suitable Family. Opening hits, reading chunks, and later context assembly must recheck current permissions and resource state.

## Processing one target from multiple sources

**Supported.** A Dream request resolves `ArtifactRef` and `SourceRef` in the run Scope, records exact evidence and a target baseline, and produces a Candidate, `no_change`, or `needs_evidence`. Request admission, execution, Candidate creation, and review have their respective authorization or evidence checks. The target Family enforces revision preconditions. [Dream RFC](1715_multi_artifact_dreaming.md), [local resolver](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/evidence/resolver.py#L90), and [execution and commit](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/dream/service.py#L419)

**Current implementation (master)**

The project review and current Profile inside Orders can participate in local Dream, with approval committing under target revision preconditions. Alice's material lives in another Scope. Existing local references and evidence resolution cannot directly include it in this run.

```text
Orders Scope [Dream run and target]
+-- Experience: Project review@2 [evidence]
+-- Profile: Service behavior@4  [baseline]
`-- Candidate: revised behavior [pending review]

Project review@2 + behavior@4 -> local manifest -> Dream -> Candidate
Approve with valid evidence and unchanged baseline -> behavior@5

Alice work Scope
`-- Experience: Retry trace@3 [outside run Scope; not a local input]
```

**Expected behavior (proposal)**

The extension changes where evidence can come from while results still write only to Orders. The Candidate must retain exact Alice and Bob addresses. Review rechecks sources, target, and baseline and continues to use the existing Family conditional write mechanism.

```text
Alice work Scope                         Orders Scope [target]
`-- Experience: Retry trace@3            +-- Profile: Service behavior@4
                                         `-- Candidate: conditional retry rule
Bob work Scope                               +~~> (Alice work, Retry trace@3)
`-- Experience: Duplicate callback@1         `~~> (Bob work, Duplicate callback@1)

Selected remote evidence + target@4 -> Dream -> Candidate in Orders
Review rechecks both origins, target authority, writable state, and baseline.
Only a valid approval writes Orders / Service behavior@5.
```

1. Discover inputs under the current rule and extend requests using the existing `ArtifactAddress` form. Local references still resolve in the run Scope. Pin the rule version, input list, and target baseline. Record source Scope in manifest nodes, root evidence groups, Candidate citations, and committed provenance. The same ID and revision in different Scopes are distinct inputs. Several paths to one address count it once. Preserve identifiers and digests of existing manifests.
2. Check permissions, Family admission, and state at the actual origin, resolving evidence under the input view and operation contract. Separately authorize underlying Sources, lineage, and attachments when the operation needs to read them. Compare supporting evidence, counterevidence, and the current target together. Derived material and existing conclusions cannot count again as independent support. Return the corresponding outcome when evidence is insufficient or no change is needed.
3. Show diffs and evidence only to authorized reviewers. Approval rechecks the Candidate, rule version, source use, output audience, target permissions, baseline, and writable state. Withdrawal of required authority blocks the old Candidate. Dropping counterevidence cannot make it approvable. A changed target requires reevaluation on its new baseline. Write only to the target and maintain the output under its retention and disclosure rules.

Idempotent retries preserve the selected inputs and target without switching to later source heads. If authorization cannot complete because of a temporary failure, preserve the error and retry under the recovery rules. Revocation requires a new valid basis. Actual writes and review operations must enforce these checks. [Evidence manifest](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/evidence/models.py#L90) and [Candidate approval](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/review/service.py#L676)

### Processing outputs and input views

Dream or subsequent processing in a team, project, or other target Scope forms and maintains target Artifacts. It can merge repeated conclusions, explain conflicts, add conditions, or retire old conclusions. Reads and further processing start from the committed target Artifact. Source records provide evidence and origins without requiring verbatim preservation in the output. A run may also return `no_change` or `needs_evidence`.

**Current implementation (master)**

Dream already distinguishes `sources` and `artifacts` and resolves them into evidence projections. Operations also restrict input Families and roles. For example, `revise_profile` permits only the current target Profile, and `revise_skill` rejects Skills other than its target. Resolution expands evidence and sources under Family rules. Arbitrary ready-made Artifacts from other Scopes cannot be admitted directly. [Operation bindings](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/dream/bindings.py#L71) and [input and target validation](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/dream/models.py#L123)

```text
Orders Scope
+-- sources: captured Source references
+-- artifacts: local Experience + current target Profile
`-- revise_profile
    -> Family and role checks
    -> evidence resolution -> EvidenceProjection -> Candidate

Another Profile or unsupported Family -> rejected by operation admission
```

The system already marks internal Sources from Artifact creation, replacement, and restoration as `lineage_only` and excludes them from generation selection. These provenance records cannot directly become original facts for another processing run. [Source purpose markers](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/sources/content.py#L87) and [generation read rules](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/persistence/generation_sources.py#L28)

**Expected behavior (proposal)**

Preserve source Artifact identity and Family and adapt processors through explicit input views. A processor that needs content can receive a controlled projection of that revision. The projection still records its origin as processed material. New Families or roles need server-side admission and validation rules. Wrapping them as Sources cannot bypass existing constraints.

```text
Alice work Scope
`-- Experience: Retry trace@3 [exact origin and digest]
    +-- Artifact view: Family snapshot + provenance --------+
    `-- Content view: derived projection + adapter version -+
                                                           |
                                                           v
Orders Scope [authorized use and output audience]
+-- pinned input manifest [one origin per input, admitted view]
+-- Profile: Service behavior@4
`-- Dream / processing -> reviewed Service behavior@5
```

| Input view | Information and processing | Required extensions |
| --- | --- | --- |
| Existing Artifact | Process an exact source revision while retaining Family, structured content, digest, and authorized provenance. | Scope-qualified resolution, input-role admission, and evidence validation at the Artifact boundary. |
| Artifact content projection | Adapt the source revision to processor content, preserving origin address, transformation version, and digest, and marking it as derived material. | Registered adapters and projection validation. A transient projection does not automatically become a new fact in the target Source Journal or advance its cursor. |

Different views of one Artifact retain one evidence origin and cannot count as independent support twice. Reusing a processor that must read a Source Journal also needs an explicit derived-Source ingestion contract. It must retain source revision and transformation identity, make writes idempotent, and define cursor advancement. Ordinary Source ingestion does not currently provide these derived-input semantics.

The proposal treats an authorized source Artifact as the input unit. If a Family requires verification of original Sources, attachments, or root evidence, that operation reads and authorizes them separately. Artifact read access grants no access to underlying data. Provenance does not create additional independent support. Each input contract must define whether it accepts the Artifact directly or expands root evidence. Disabling existing checks cannot provide this extension.

Input contracts must also distinguish the target baseline from new supporting evidence. An output authorized for retention does not cease to exist as a comparison baseline because historical originals are unavailable, and it cannot become new evidence supporting itself. Existing resolution and each Family revision path need separate verification and adaptation at this boundary.

Independent publication remains available for original-content delivery, without becoming a prerequisite for processing in another Scope. Source admission must cover processing use and output audience. Target outputs follow the agreed retention and disclosure rules. Reading a committed conclusion and inspecting original evidence require separate authorization. Verification, rebuilding, or retention requirements determine whether original snapshots are also needed.

### Profile, Topic Memory, and the document service

**Supported.** Subject bindings resolve an ordinary Scope using `integration=subject`, `kind=user`, and `external_id=subject_key`. The first write can name an existing Scope or let the service create one. Later writes follow the same binding, giving personal Profiles a stable main Scope. Source capture can write business and subject Scopes in one transaction. Each Scope independently maintains at most one `profile/profile`. Profile and Topic Memory process local Source windows and cursors. The service writes Sources to both Scopes, where Profiles are processed separately. [Source dual write](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/scope/subject_sources.py#L99), [Profile processing](1485_profile_artifact.md), and [Topic Memory](1417_topic_memory.md)

**Current implementation (master)**

Personal Profiles already have a main Scope resolved through a stable binding. The dual write stores Sources in two Scopes, each using its own local window and cursor. They do not share one Profile processing position.

```text
Subject binding: (subject, user, alice) ..> Alice profile Scope

One Source capture, one transaction
+-- Orders work Scope
|   `-- Source: Shift review -> local processing and cursor
`-- Alice profile Scope
    +-- Source: Shift review -> local processing and cursor
    `-- Profile: Preferences@2

Each Scope processes its own Source Journal.
```

**Expected behavior (proposal)**

The target can use authorized remote Artifacts as evidence while retaining its local Source processing position. A new manifest across Scopes records those inputs. The target Family still owns admission and commit. Remote material does not need to be represented as local Sources.

```text
Alice work Scope
`-- Experience: Retry trace@3
          |
          | admitted, authorized remote evidence
          v
Orders Scope [target]
+-- Profile: Service behavior@4 -> reviewed revision@5
+-- Local Source Journal + cursor [unchanged by remote evidence]
`-- Qualified manifest
    `~~> (Alice work Scope, Retry trace@3)

The registered Family admits and commits the target revision.
```

Team, project, and other targets can also use ordinary Scopes and mappings, subject to the per-Scope Profile constraint. Record evidence from other Scopes separately. Consuming remote Artifacts cannot manufacture local Sources or advance the local cursor. Initializing a first Profile from remote evidence needs explicit preconditions. Dream still restricts input Families. Documents and chunks need their evidence and processing rules adapted for admission. Registered document services can provide parsing, indexing, and jobs and must enforce the common Scope state checks and commit conditions.

## Scope lifecycle and cascades

### Archive, reopen, and effective state

**Supported.** Scopes have versioned metadata and relationships, with no public archive or delete operation. Access Bindings can be revoked or expire. Atomic Memory’s `forgotten` state excludes ordinary recall while retaining history. These govern access and Family state. An internal processing-status projection summarizes local progress, blockers, and Dream state, with no public generic entry wired yet. [Scope model](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/scope/models.py#L144), [Atomic lifecycle](../docs/workflows/atomic-memory.md), and [internal read-only processing status](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/processing_status.py#L1)

**Current implementation (master)**

Existing revocation controls an identity's access, and Atomic Memory's `forgotten` state controls recall of that entry. The Scope itself has no archive state that uniformly stops local content writes and processing.

```text
Orders Scope [metadata version=7; no archive state]
+-- Interface Scope
|   +-- Experience: Retry trace@3
|   `-- Atomic Memory: Retry hint [forgotten; excluded from recall]
`-- Help Scope

Consumer identity ..> Interface [grant can be active, revoked, or expired]
Processing status remains an internal read-only projection.
```

**Expected behavior (proposal)**

Archiving Interface stops its ordinary writes and processing while Orders, Help, and the external Reliability Scope retain their own states. Reliability can still read material under current grants and write to its own target. Archive and revocation control different behavior.

```text
Orders Scope [writable]
+-- Interface Scope [ARCHIVED]
|   +-- Experience: Retry trace@3 [readable with current authority]
|   `-- New Sources, local Dream, Candidate commits [blocked]
`-- Help Scope [writable]

Reliability Scope [writable, outside Orders]
+..> Interface / Retry trace@3 [authorized source use continues]
`-- Dream -> own on-call Skill [allowed with required authority]

Reopen Interface -> same Scope identity, writable again
Archive changes only Interface; deletion still requires an explicit request.
```

Archive affects only the named Scope. It stops new Sources, Artifact content writes, ordinary processing, and Candidate commits. Structural children and referring Scopes retain their own states. Services still allow management queries, revocation, and reopen, as well as cleanup for an effective deletion. Existing source grants retain their range and expiry.

State transitions must share a transaction lock or equivalent version condition with writes, Candidate creation, approval, and plugin publication. Services check state at enqueue, before computation, at Candidate persistence, and at final commit. They cancel in-flight computation or discard its result. Reopen preserves identity, and rescheduled work rechecks current permissions and baseline. Deletion always requires an explicit request.

### Deletion sets, shared references, and cleanup outcomes

**Current implementation (master)**

Existing parent and reference records constrain relationships, but there is no public Scope deletion and resource cleanup flow. Resolution reports a conflict when a persistent binding points to a missing Scope and requires explicit repair.

```text
Orders Scope                          Operations Scope
`-- Interface Scope                   `-- Runbook Scope
    `-- Experience: Retry trace@3

Orders Scope ..> Runbook Scope [stored Context Reference]
Structural parent and incoming-reference FKs use RESTRICT.
There is no public Scope delete operation.

Persistent binding ..> missing Scope
  `-- resolution -> 409 scope_binding_target_missing
```

**Expected behavior (proposal)**

Deletion becomes an explicit operation on a confirmed set. Runbook retains its original outside the box, and Analytics also remains. Its referenced Interface is inside the set and becomes unavailable. Access cutoff and cleanup completion are reported separately. Physical erasure also needs its own range and completion conditions.

```text
Confirmed deletion set      Outside the deletion set
+---------------------+     Operations Scope
| Orders Scope        |     `-- Runbook Scope [retained]
| +-- Interface Scope |
| `-- Help Scope      |     Analytics Scope [retained]
+---------------------+     `..> Interface Scope [origin becomes unavailable]

Orders ..> Runbook [detach reference; retain Runbook]
Confirmed set -> access cutoff -> owned-resource handling
Cleanup progress is separate from any physical-erasure completion.
```

Removing a business entry can update only a mapping or reference. Deleting a library itself must select its Scope. Single-Scope deletion should report a conflict if structural children remain. Explicit subtree deletion previews the set and checks permissions and structural state on every target. Checking only the root version would miss a newly attached descendant, so deletion must serialize with attachments, moves, and publication and reconfirm a changed set. Sharing and evidence lineage cannot enlarge the deletion set. A selected descendant still follows the requested action when external entries reference it. Consumers then see an unavailable origin. Keeping a shared entry does not preserve the original.

Scope management stores operation identity, targets, access-cutoff state, and resource progress. Participating services handle owned Artifacts, originals, selection rules and checkpoints, unpublished document registrations, jobs, chunks, indexes, and caches. Work can proceed in stages across stores, with retries continuing unfinished work. After a Scope becomes unavailable to ordinary reads, generic reads, search, evidence previews, Prepare, plugin reads, and stale job commits must check that state. Authorized management entries can still inspect limited status.

A deletion request must specify its action and completion condition. Logical deletion can retain content and history. Cleanup completion means the agreed work has finished. Physical erasure also needs a defined range of originals, history, copies, and stores. Required work that fails leaves an incomplete or failed operation. Empty search results cannot prove erasure. This proposal defines archive reopening. Deletion restoration and physical erasure need separate contracts.

### Resource participation, dependencies, and retention

**Supported.** Parentage, source evidence, and publication records have persistence constraints. Existing resolution returns `409 scope_binding_target_missing` when a persistent Binding or default pointer names a missing Scope, requiring explicit repair. [Evidence and publication constraints](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/src/powercontext/builtin/persistence/tables.py#L330) and [binding resolution](../docs/workflows/scopes-and-access.md)

**Current implementation (master)**

Orders can already retain an independently published copy, but the publication record still constrains source identity through a foreign key. Direct source removal encounters storage constraints, and resource persistence paths lack common Scope cleanup coordination.

```text
Alice work Scope
`-- Experience: Retry trace@3

Orders Scope
`-- Experience: Published retry trace@1 [independent copy]

Publication record ~~> (Alice work Scope, Retry trace@3) [FK RESTRICT]
Source identity is constrained while the independent target is retained.
Families and other resource stores currently use their own persistence paths.
```

**Expected behavior (proposal)**

Resource handlers report progress to the Scope operation. Artifact and Source handling can finish while a pending index retry keeps overall cleanup incomplete. The independent Orders copy follows its own retention rules. Keeping an exact unavailable origin after source removal needs storage migration.

```text
Lifecycle operation: Alice work Scope [access cutoff effective]
+-- Artifact / Source handler [complete]
+-- Index handler             [retry pending]
`-- Overall cleanup          [incomplete]

Orders Scope [outside the operation set]
+-- Experience: Published retry trace@1 [retained]
`-- Exact origin ~~> (Alice work Scope, Retry trace@3) [unavailable]

Retaining an unavailable origin while purging source content needs
storage changes, such as a content-free address tombstone.
```

Participating services declare their Scope resources, visibility checks, and idempotent handling. If a resource type cannot meet the selected deletion or erasure action, reject the mode before acceptance. Each service implements its resource handling. Scope management coordinates access cutoff, gathers progress, and reports completion under the agreed conditions. The document service participates in collection cascades through this mechanism.

Purging source content while preserving target provenance requires resolving existing RESTRICT foreign keys. One option is content-free address tombstones and a storage migration preserving exact identity, unavailable status, and historical identifiers and digests. Processed outputs committed for an authorized use and audience permitting independent retention, along with independent copies, follow target rules. Source updates, revocation, or deletion constrain later reads and processing without automatically deleting those committed outputs. A disclosure mode requiring continuing source authority needs separate withholding and rebuilding rules.

Explicitly reassign a deployment default before deleting it. Remove or repair other persistent bindings. Hosts update their application mappings, and services reject stale mappings that point to unavailable resources. For account erasure, the application identifies the personal data set and services execute within that range, preserving collective team records. After an administrator changes, an authorized successor maintains the same target.

## Composition and responsibilities

PowerContext APIs receive resolved Scopes, actions, and resources. An application entry can compose several calls. Shared services enforce constraints when material is used and results are committed. Registered Family and document services also implement these data capabilities. Hosts call their interfaces for Artifact and document operations.

```text
Product action -> application mapping and operation selection
                         |
              Scope / Access / query / Dream / lifecycle
                         |
              registered Family and resource services
```

| Operation | PowerContext and registered services guarantee | The host selects or wraps |
| --- | --- | --- |
| Organize collections | Scope identity, valid structure, relationship versions, and controlled range resolution. | Business-object mapping, visible hierarchy, and create/move workflows. |
| Use shared material | Standing range resolution, current permission checks, exact inputs, and mode restrictions. | Sharing boundaries, automatic inclusion rules, permitted use, and output audience. |
| Query and form context | Bounded calls, result identity, visibility, and state checks. | Query entries, business filters, ranking policy, and presentation. |
| Form durable Artifacts | Input-view and Family admission, rule versions, manifests, review, and conditional commits. | Target outputs, processing strategy, scheduling, prompts, and review assignments. |
| Close or clean a collection | Archive and explicit deletion boundaries, resource handling, and queryable outcomes. | Triggers, retention periods, approval, and external copy management. |

When several business objects share one Scope, a Scope operation affects all related entries. To stop only one entry from using material, change its selection, job, or associated grant. To stop all ordinary writes, archive the target Scope. A business operation spanning unrelated Scopes must handle each target result. These operations do not guarantee atomicity across stores.

## Contract evolution and acceptance

New HTTP and SDK fields remain to be specified. Existing Scope, Artifact, and Access APIs retain their contracts. Implement selection, origin resolution, and lifecycle checks in shared runtime services, then route HTTP, SDK, MCP, and registered extensions through them. Existing Scopes start writable. Old evidence still resolves in its original run Scope and retains historical identities and digests. Processing across Scopes, resource participation, and deletion modes need explicit persistence migrations.

```text
Existing local calls and records -> preserve their interpretation
Extended contracts -> shared service checks -> HTTP / SDK / MCP / extensions
```

| Acceptance scenario | Existing foundation | Extended checks |
| --- | --- | --- |
| A granted work Scope gains Artifacts or revisions | Whole-Scope read access can cover new readable content. Standing rules are proposed. | Automatically discover eligible material, exclude private ranges, and pin the inputs for this run. |
| Only an automatic target commit produces an update | Existing target roles and `lineage_only` rules prevent self-support. | Do not treat target updates or content projections as new independent facts that repeatedly trigger processing. |
| A change is notified twice or a task retries | Local idempotency and revision checks provide a foundation. | Do not adopt identical rule, input, and target-baseline work twice. Keep pinned heads on retry. |
| An external Artifact participates through different input views | Local Artifact/Source distinctions and Family constraints exist. | Preserve origin and derived role. Reject Source wrapping that bypasses admission. |
| A source becomes unavailable after its output is authorized for independent retention | Target Artifacts have their own identities and revisions. | Stop later use of the unavailable source, maintain the target under its output agreement, and authorize evidence inspection separately. |
| Select the same material through team and project entries | Exact addresses and existing Scope/Artifact access. | Deduplicate by address, check each use, and retain entry paths. |
| A directly referenced Scope references a third | Prepare consumes direct references only. | Bound expansion, respect authority and budgets, and distinguish structure from content access. |
| Two Experiences revise a target Profile | Local Dream, Family writes, and review. | Preserve different origins, retain counterexamples, and avoid a commit with insufficient evidence. |
| Authority is withdrawn or the target manually advances during review | Local reauthorization and target version checks. | Recheck actual origins and reject invalid Candidates or reevaluate a new baseline. |
| A knowledge space queries a shared library | Single-Scope calls and grants are reusable. RAG service integration is pending. | Authorize each library Scope, retain document/chunk origins, and keep item grants from expanding to library search. |
| Archive a source or target | Scope archive is proposed. | Continue authorized source reads, block ordinary target writes and commits, and allow cleanup completion. |
| Delete a knowledge space’s structural subtree | Scope deletion and resource participation are proposed. | Confirm targets, preserve external sources, resume failed work, and report cutoff and completion accurately. |
| Erase personal data or hand over administration | Scope identity and ownership are independent. | Erase only the named personal set and let authorized successors maintain collective records. |

Existing cases cover direct references, source access, local Dream revocation, and review races and provide a regression foundation. New paths across Scopes and lifecycle operations need end-to-end acceptance. Expected flows in the diagrams remain proposed behavior. [Direct references](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/tests/e2e/test_builtin_runtime.py#L335), [source access](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/tests/e2e/test_access_control_regressions.py#L330), [Dream revocation](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/tests/e2e/test_artifact_dreaming.py#L1781), and [review races](https://github.com/oceanbase/powercontext/blob/fd9f1682b89f6766efb25d1bfb1caf14cb90e09a/tests/e2e/test_artifact_dreaming.py#L3130)

# Drawbacks

Operations need permission and resource-state checks, with relationship checks for structural ranges. Continuous sharing adds incremental discovery, deduplication, and retry state. Processing across sources can lose inputs during review. Content projections must retain their derived origin, and lifecycle across stores needs reconciliation. Mapping cascading business relationships to Scopes also requires adapting RAG library configuration, ownership, and query APIs.

```text
More source Scopes -> more checks and possible invalidation
More resource stores -> staged cleanup and explicit completion accounting
```

# Rationale and alternatives

Ordinary Scopes already have identity, grants, and structure that can express business concepts requiring cascade management. Business parent-child relationships map to Scope structure, while references and grants express shared use. Team/project memory and RAG can both use these capabilities. Artifact Families continue to manage content and revisions, and resource services handle their specific operations.

```text
Business cascade -> collection Artifact -> Artifact lifecycle/processing cascades
Business cascade -> Scope structure    -> common lifecycle + resource handlers
```

- Keeping a knowledge-base Artifact for the business hierarchy requires lifecycle and processing rules for Artifacts that previously had no substantive cascade relationship. Mapping these business relationships to Scope structure centralizes cascade ranges while Artifacts continue to manage content and revisions.
- Built-in team, project, and knowledge-space types would bring business relationships and product rules into the common data model. Ordinary Scopes and external mappings express the needed entries.
- Independent publication suits independently deliverable content. Copies add identities and maintenance, and source revocation does not recall an independent copy.
- Deleting through the reference graph can affect other collections. Compute deletion from named Scope ownership and structural descendants while accessing shared sources through references and current grants.

# Prior art

Source authorization, Profile evolution, and review for team and project memory can reuse the existing Scope, Access, Dream, and Family designs. The transitional knowledge-base Artifact in RAG RFC 2570 exposes Artifact-level cascade requirements. Its document processing, durable jobs, and versioned passages can still be adapted after business relationships map to Scopes. [Knowledge-base proposal](https://github.com/oceanbase/powercontext/pull/2570)

GitHub assigns team roles per repository. Linear keeps private-issue access restrictions in projects spanning teams. When projects bring together work from several teams, private content retains its access restrictions. [GitHub repository roles](https://docs.github.com/en/organizations/managing-user-access-to-your-organizations-repositories/managing-repository-roles/repository-roles-for-an-organization) and [Linear private teams](https://linear.app/docs/private-teams)

Nowledge Mem supports reading material from several authorized Spaces, writing to a specified Space, and recording the origins of conclusions drawn from several sources. Its read, write, and provenance boundaries provide a reference. PowerContext uses its own authorization and lifecycle rules. [Spaces](https://mem.nowledge.co/docs/spaces) and [Crystals](https://mem.nowledge.co/docs/concepts/crystals)

```text
Resource roles -> authorized material
Read several spaces -> save in one destination
Derived conclusion -> retain contributing origins
```

# Unresolved questions

- Who configures and changes standing sharing rules, how is task identity bound, and how can Access enforce processing use, output audiences, and withdrawal effects?
- Which operations can accept an approved Artifact as their input boundary, and which must verify underlying Sources? When is explicit materialization as a derived Source needed, and how are evidence independence and cursor semantics preserved?
- Which Families and input roles should Dream across Scopes support first, how should a first target Artifact be initialized, and how are existing evidence meanings preserved?
- Which deletion actions, retention rules, and resource participation protocol should the first Scope lifecycle support? How should address tombstones, default pointers, and external-store migrations be defined?
- How should RAG library configuration, model switches, and durable jobs fit the business-to-Scope mapping and lifecycle contracts? Which technical budgets should be fixed or configurable server-side?

```text
Requested mode -> supported authorization and resource capabilities?
                     +-- yes -> enforce the complete path
                     +-- unresolved -> define the contract before admission
```

# Future possibilities

Continuous selection and automatic inclusion belong to this proposal, with applications able to schedule calls initially. Later additions can provide event subscriptions, scheduling optimizations, and batch wrappers for specific hosts. Multi-hop sharing still needs selection, permission, and withdrawal rules at each hop. Further Artifact Families and storage services can join the resource participation mechanism.

```text
Event subscription -> coalesced scheduling -> standing rules -> current checks
```
