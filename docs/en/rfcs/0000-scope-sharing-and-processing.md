---
title: "RFC 0000: Sharing, querying, and maintaining Artifacts across Scopes"
description: "Define how applications can keep using authorized material across Scopes, maintain a target Artifact, and manage business data with cascading lifecycles."
---

- Proposal Name: `scope_sharing_and_processing`
- Start Date: 2026-11-09
- RFC PR: [oceanbase/powercontext#2611](https://github.com/oceanbase/powercontext/pull/2611)

# Summary

PowerContext provides data infrastructure for Agent context. This proposal defines how material can be used across Scopes and what happens to it on archive or deletion. An application agrees on a sharing range with its contributors. The system automatically discovers qualifying material already in that range and material added later. A query preserves the address of each hit. A processing run pins its inputs, compares the evidence, reconciles conflicting findings, and maintains an official Artifact in one target Scope. Applications can map business relationships that require cascading operations to an ordinary Scope tree. Archiving stops ordinary writes and local processing in the named Scope. Explicit deletion acts on a confirmed set of owned resources and Scopes.

```text
Orders project                          Orders knowledge space
  Inputs: Alice and Bob's work records    Owns: Interface and Help
  Reviewed output: project Profile@5      Shared library: Operations / Runbook
  Cites: Alice's Retry trace@3            Delete Orders subtree: Orders, Interface, Help
```

# Motivation

The Orders project needs to answer “Can we retry a callback after a timeout?” Retries succeed on the new channel with an idempotency key, but the old channel may charge a customer twice. Sending both incident reports to the team leaves readers to work out which rule applies. The project needs a conclusion that members can read, correct, and revisit when new facts arrive. Personal Profiles already consume Sources captured in their own Scope. Evidence distributed across work Scopes also calls for a controlled way to collect new inputs over time.

RAG has a different requirement. Closing a knowledge space may entail stopping writes in its libraries, handling unfinished imports, and cleaning up indexes. The transitional design in RFC #2570 uses `knowledge-base` Artifacts to manage documents within a Scope. Existing Artifacts had no parent-child relationship requiring such cascades. Under that design, a library Artifact would require the Artifact layer to coordinate child visibility, pending work, and cleanup when the parent becomes unavailable. This proposal maps those business relationships to the Scope tree. Artifacts continue to manage document content and revisions. An external library can contribute search results while its owner manages its lifecycle. Indentation and `+--` group the Interface library with the document and import job it manages. [Knowledge-base proposal](https://github.com/oceanbase/powercontext/blob/71e842cd2a34a7943ab0adecb3542771e3da387e/docs/en/rfcs/2570-knowledge-base.md)

```text
RFC #2570: Orders Scope                  Proposed: Orders Scope
  +-- Interface knowledge-base Artifact    +-- Interface Scope
       +-- API document@3                       +-- API document@3
       +-- pending import for API@4             +-- pending import for API@4
```

# Guide-level explanation

A Scope identifies the data that belongs to it. A Source captures an observation such as an incident review. An Artifact has readable revisions and belongs to a Family such as Experience, Profile, or Skill. In the diagrams `Retry trace@3` is revision 3 of “Retry trace.” Its full address for readback also identifies its Scope, Family, and Artifact ID. Dream proposes revisions from evidence. A Candidate is a change awaiting review.

- **Team and project memory.** Alice allows the Orders project to use qualifying incident reports from her work Scope. She can set this up once for future reports. The official project Profile might say “On the new channel, retry with an idempotency key. On the old channel, check transaction status first.” A reader with access to the evidence can open the cited revision. Project maintainers can correct the conclusion. Alice uses her personal Profile in her own Scope.
- **RAG.** A search in the Orders knowledge space can return a passage from its own Interface library or from the Operations team's shared runbook. Readers can open the passage in the cited document revision. Removing the shared entry leaves the original in Operations.

```text
Orders project                         Orders knowledge space
  read: Service behavior@5               search: Interface + shared Runbook
  inspect evidence: separate grant       open: Runbook / Timeouts@2 / chunk 4
```

# Reference-level explanation

Applications map business IDs to Scopes, agree on material, permitted uses and audiences, and choose when to act. PowerContext and its registered services resolve the range, check authority at each origin, commit through the target Family, and enforce archive and deletion. HTTP, SDK, MCP, and registered extensions must use the common runtime for these checks. Verify the behavior through the corresponding entry points. Existing local calls keep their contracts. New endpoints and fields remain open.

In the table, “existing” means present in the pinned implementation. “Composable” means an application can carry out the steps using existing services. Standing sharing rules, general cross-Scope Dream, and public archive and deletion still need implementation. The documents, chunks, and durable import jobs in the final example come from the #2570 design. The document service, cross-library retrieval, consumer eligibility checks for file originals, and the corresponding product entry points still need integration. [Implementation baseline 1cb0e75db](https://github.com/oceanbase/powercontext/tree/1cb0e75dbd56227bb13550d63f301b8f39feb931) · [Knowledge-base proposal](https://github.com/oceanbase/powercontext/blob/71e842cd2a34a7943ab0adecb3542771e3da387e/docs/en/rfcs/2570-knowledge-base.md)

| Capability | Existing or composable | Proposed work |
| --- | --- | --- |
| Determine the operation's range | Ordinary Scopes, a single-parent tree, direct references, and selection for observation | Resolve and confirm a bounded range that distinguishes structural descendants from external sources |
| Deliver or reference material | Scope and logical Artifact grants, independent publication for supported Families | Enforce the terms for origins, uses, audiences, and withdrawal |
| Keep supplying inputs | Local Source capture, window-based generation, application scheduling | Discover material within the agreed range and record the inputs and outcome of each evaluation |
| Query and open results | Single-Scope search, limited direct-reference Prepare, application-side aggregation | Define cross-origin query budgets, coverage, and exact readback |
| Maintain one official Artifact | Local Dream, Candidate review, revision-conditional writes | Admit external Sources or Artifacts per operation and recheck their actual origins |
| Manage archive and deletion | Relationship versions, grant revocation, and Family-specific states | Name the affected writes or reads and report handling of each resource |

## Scope relationships and the operation's range

**Current.** Scopes have opaque IDs. `parent_scope_id` defines an acyclic single-parent tree. `context_references` holds directly referenced Scope IDs. Relationship updates require `expected_version`. The `exact`, `subtree`, and `all` modes of `ScopeSelection` return descriptors. HTTP checks `scope.read` on every exact Scope and requires `server.observe` for the other two modes. The current subtree resolver reads every Scope descriptor. Listing nodes and reading their contents have separate authorization checks. [Scope relationship validation](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/scope/application.py#L195) · [Selection resolution](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/scope/application.py#L293)

**Diagram notation.** The following connectors distinguish ownership, use of a source, and evidence provenance. Arrow direction matters.

| Notation | Meaning |
| --- | --- |
| Indentation and `+--` | In a Scope tree, a Scope under another Scope is a structural child. A Source or Artifact under a Scope is stored there. In a flow diagram, these branches only list steps or alternatives. |
| `Orders ..> Runbook` | **Consumer to origin.** Orders references or selects material from Runbook without changing Runbook's structural ownership. The diagram labels distinguish an existing Context Reference from a proposed sharing entry. |
| `Profile@5 ~~> Retry trace@3` | **Output revision to evidence.** Revision 5 of Profile relies on revision 3 of Retry trace. This is lineage recording the origin of that revision's conclusion. |
| `Source -> Dream -> Candidate` | **Input to processing step or result.** Plain arrows indicate the labeled data flow, processing sequence, or revision change. |

References and evidence links grant no authority and do not expand subtree deletion. Square brackets annotate an operation set or state.

```text
Current
  Orders                            Operations
    +-- Interface                     +-- Runbook
    +-- Help
  Orders ..> Runbook                direct Context Reference for Prepare

Proposed
  [Orders, Interface, Help]         confirmed structural subtree
  Orders ..> Runbook                also selectable for authorized use
  Orders / Profile@5 ~~> Runbook / Timeout notes@2
                                   evidence for one revision
```

**Proposed.** The service computes descendants from parent-child edges. An application can separately select an external Scope or an exact Artifact revision and retain the path by which it selected it. If Runbook references a third Scope, the direct Orders reference does not select that third Scope. The service may include that third Scope only when the rule covers it, subject to depth and count limits and an independent check of authority at the third Scope. Artifact lineage records the material used for a revision and can cross these boundaries. In the diagram, Runbook supplies material and appears in the Profile's provenance. It still belongs to Operations when the Orders subtree is deleted. Conversely, a selected structural child belongs to the deletion set even if an external Scope references it.

Applications resolve business IDs to actual Scopes before calling the service. People, teams, projects, and knowledge spaces can all use this mapping. A team and a project choose their own targets and may belong to different subtrees. Products can associate a team with several projects or a project with several teams, and enforce their own rules on creation and moves. If several entries share one Scope, archive or deletion affects them all. To close just one entry, the application can change its mapping, selection, or applicable grants. Large operations need descendant lookup by parent and limits on nodes and depth.

## Delivering shared material and authorizing its use

**Current.** Searching an entire Scope requires read authority on that Scope. A logical Artifact grant for a supported Family can permit exact reads of that Artifact. The resource key omits revision. Pinning revision 3 specifies the content to read, and makes no change to which other revisions the caller may access. Members and processing identities can use existing Scope grants. Applications can integrate a membership directory if needed. [Scopes and access](../docs/workflows/scopes-and-access.md) · [Artifact authorization](1396_handoff_access_control.md)

| How the recipient gets the material | Existing support and limits |
| --- | --- |
| Give the recipient an original they may keep independently | **Copy.** Publishing an exact Experience, Skill, or Handoff revision requires `artifact.share` at the source and `scope.admin` at the target. Topic Memory uses its own publication path and requires `scope.admin` on both ends. The copy starts at revision 1 with a new target identity. Atomic Memory, Profile, and Prompt do not support publication. |
| Read material under the origin's current permissions | **Reference.** Retain its address and check origin access at use. Existing direct Scope references supply supported Prepare inputs. Existing read authority can allow an exact revision read. Rule-driven processing across Scopes still needs implementation. |

```text
Current
  Alice / Experience@3 -> publication -> Orders / Experience copy@1
  Alice / Profile@3 [publication unsupported]
  Orders ..> Alice work Scope [direct reference grants no read authority]

Proposed
  Material selected for Orders
    +-- Copy: deliver an independently retained revision
    +-- Ref: read the selected revision under a current origin grant
  Proposed Orders conclusion -> check permitted audience and retention
```

**Proposed.** The contributors and the application agree on the material, execution principal, permitted use, target, and readers. Reading or copying the original, using it as evidence, reviewing the Candidate, and disclosing the conclusion each require the corresponding authority. Alice might allow a task to use a private record for a project conclusion while restricting human inspection of the original to designated reviewers. The target Scope identifies where the output belongs. The service checks each origin's read permission and the target's write permission against the actual execution principal. Those permissions alone do not authorize disclosure to a new audience. Before offering this mode, the Access design must establish who can grant that use, who can inspect the original, and how the service enforces the audience and retention terms. Access must also enforce entry-only or revision-only use. If the service cannot enforce the requested restrictions, it must refuse that use. [Publication implementation](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/publication.py#L151) · [Dream authorization](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/server/dream_access.py#L79)

A one-time copy contains the delivered revision. A pinned reference also identifies just that revision. Future changes to the target depend on new inputs, a processing trigger, and a way to commit the change. Expiry or revocation blocks subsequent reads and pending Candidate commits that require that grant. Copies and committed conclusions authorized for independent retention continue under target rules. Retaining a conclusion does not authorize a snapshot of the original. Keep such a snapshot only if verification, rebuilding, or retention terms require it and its retention is authorized. A mode that must withhold a conclusion on source revocation needs explicit conditions for withholding and rebuilding it.

## Discovering material and pinning the inputs of a run

**Current.** Applications can write work records directly to the target Scope when authorized to collect them there. Ordinary Profile and Topic Memory processing each read windows from the local Source Journal and track progress with their own cursors. The Profile processor compares the last committed Profile with eligible Sources in the new window. If the conclusion still holds, the cursor can advance without a new revision. Once a Profile policy and generation model are configured, applications can trigger processing explicitly or enable the background scheduler, which is off by default. [Profile generation and scheduling](../docs/workflows/use-profiles.md) · [Profile input window](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/artifacts/profile/service.py#L223)

```text
Current: Orders
  Source Journal: reviews A and B [consumed], review C [after cursor]
  Profile@4 and review C -> local Profile processing

Proposed: rule v2 for Orders
  +-- Alice work / Retry trace@3 [selected]
  +-- Bob work / incident Source [selected]
  Alice private [outside rule]

  Run 1 and its retry: same inputs, including Retry trace@3
  Alice publishes Retry trace@4 -> eligible for selection in a new run
```

**Proposed.** The application lets contributors and consumers agree on a standing sharing range. It can include qualifying records and Artifact revisions already present in designated work Scopes and those added later. An authorized workflow may discover new source Scopes and establish the required grants. Membership labels, structural attachments, and selection rules grant no read access. Contributors can correct selections manually without having to approve every item that falls within a standing agreement.

**One run.** The target Scope manages the rule that defines where future material can be found. Each processing run pins the rule version, target and baseline, and each input's origin address, digest, and role. If several entries select the same address, retain each path, check the applicable use, and count the evidence once. A retry uses that same manifest and rechecks authority. A newly published source revision is eligible for a new run. Invalidation of the rule stops work that depends on it. A new run resolves the range under a valid rule. Transient authorization failures can be retried. Revoked authority must be reestablished.

The target's processing record identifies the rule, inputs, baseline, and evaluation outcome, so a duplicate notification can refer back to the run. Additional evidence or an explicit request for reassessment can start a new run. The committed revision records the evidence it actually adopted. Processing external inputs does not advance the target's local Source Journal cursor.

Bound the number of discovered Scopes, input count, and total work. When complete coverage exceeds the budget, the caller must explicitly narrow the range or the service must process traceable batches and verify completeness before adopting the result. An application can schedule periodic discovery to pick up new material under the rule. External rules may continue to read archived origins under current grants. After source deletion, report the unavailable evidence and apply the rule's completeness requirement. Updates to the target itself and repeated transformations of the same content supply no new facts to trigger another revision.

## Querying across Scopes and reading exact results

**Current.** Artifact Search queries current searchable revisions within one Scope and Family. The built-in searchable Families are Experience, Skill, Topic Memory, and Atomic Memory. Applications can search authorized Scopes separately and merge results. Prepare recalls Experience and Atomic Memory from the current Scope and directly referenced Scopes and can explicitly select Profile snapshots there. Prepare searches Topic Memory in the current Scope only. Prepare with references does not support context reuse. Structural children are not automatically searched. [Artifact search](../docs/workflows/search-artifacts.md) · [Prepare assembly](../docs/workflows/prepare-context-text.md) · [Direct-reference case](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/tests/e2e/test_builtin_runtime.py#L335)

```text
Current
  Search(Interface, Experience) -> Retry trace@3
  Search(Help, Experience)      -> FAQ correction@1
  Runbook / Timeout notes@2 [exact read under an item grant]

Proposed: Orders query
  +-- Interface -> (Interface, Experience, Retry trace, 3)
  +-- Help      -> (Help, Experience, FAQ correction, 1)
  +-- Runbook   -> item search only if permitted and supported
  Open Retry trace hit -> check access to Interface / Retry trace@3
```

**Proposed.** Whether the application or the platform aggregates results, it must resolve an explicit set of origins, authorize each one, and bound the number of Scopes, candidates, and total results. Deduplicate by `(scope_id, family, artifact_id, revision)`. An independently published copy retains its own address. When scores are not comparable across origins, merge by each origin's internal rank with a stable tie order. In complete mode, failure of a required origin returns an error. In partial mode, return only authorized hits and mark coverage as incomplete if a required origin fails or the selected range is not fully searched. Recheck origin permissions and visibility when opening a hit, reading passages through plugins, or assembling Agent context through Prepare.

If the caller may search only a particular Artifact or revision, restrict the candidates before scoring, truncation, or snippet generation. Searching the entire library and filtering afterwards can omit authorized hits or disclose unauthorized material. A service that indexes only the current revision must decline searches restricted to historical revisions. Exact reads and independent publication each follow their own authorization and Family contract. For example, after Interface publishes revision 4 of “Retry trace,” an earlier hit still points to revision 3. Opening it checks current access and availability at that historical address.

## Maintaining one target Artifact from Sources or Artifacts

**Current.** Local Dream resolves `SourceRef` and `ArtifactRef` in the run Scope. Each operation admits particular Families and roles. For example, `revise_profile` takes the current target Profile and can use an eligible Experience or Source. The `derive_skill` operation does not require an existing target Skill. The service records the evidence and target baseline and produces a Candidate, `no_change` if the conclusion still holds, or `needs_evidence` if support is insufficient. Only a successful commit creates a new official revision. Dream runs independently of a Family's ordinary Source processing progress. [Dream inputs and operations](1715_multi_artifact_dreaming.md) · [Operation bindings](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/dream/bindings.py#L71)

```text
Current: Orders
  local Source and local Experience -> Dream with Profile@4 -> Candidate
  published Experience copy [searchable, unresolved for Dream]

Proposed: Dream with Orders / Profile@4
  +-- Alice work / Retry trace@3 [supports retry with an idempotency key]
  +-- Bob work / Duplicate callback@1 [counterexample on the old channel]
  +-- Orders / incident Source [local observation]
  Evidence and baseline -> no_change, needs_evidence, or Candidate
  Approved Candidate -> Orders / Profile@5
```

**Proposed.** Each operation declares which inputs and roles it accepts and whether it requires an existing target or can create one. Creating a first Profile from external evidence likewise requires an explicit initialization contract. The processor maintains a usable conclusion. It can combine repeated observations, explain contradictory evidence, qualify a recommendation, or retire obsolete advice. Readers and subsequent processing use the committed target Artifact and open the cited evidence when needed and authorized.

| Input | What the processor reads | Admission and records |
| --- | --- | --- |
| Original Source | Authorized observations, work records, or counterexamples. They can arrive continuously in the target's local Journal. | Ordinary local processing uses the Family's window. An operation admitting external Sources must record the source Scope, Source identity, and digest and check access and permitted use separately. |
| Existing Artifact | The structured content of an exact source revision, such as a reviewed incident report | Retain its Scope, Family, ID, revision, digest, and evidence role. Each operation must specify whether it can rely on that revision or must inspect underlying Sources, attachments, or root evidence. Each additional read needs authority. |
| Converted Artifact content | Text or fields from the same revision converted into a form the processor can consume | Record the origin, transformation version, and digest. Count the original and its conversion as one source of support. Persisting a derived Source for a journal-based processor also requires an explicit role, idempotent ingestion, and cursor semantics. |

**Limits of existing copies.** A published Experience copy is searchable in its target. The Dream evidence resolver marks an Experience with `publication_source` as `unresolved` and omits its body from generation. A copy therefore supports independent delivery, inspection, or manual adoption. Direct use by a processor needs an implemented and verified input contract for that operation. Internal `lineage_only` Sources from Artifact creation, replacement, or restoration record provenance. Treating these or an Artifact's converted content as a new independent fact would bypass Family admission or count support twice. [Experience publication and indexing](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/publication.py#L171) · [Evidence resolution for copies](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/evidence/resolver.py#L390) · [Source admission](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/persistence/generation_sources.py#L28)

The manifest, root evidence groups, Candidate references, and final revision evidence must each identify the origin Scope. Artifact inputs retain the existing `ArtifactAddress`. Two Artifacts with the same ID and revision in different Scopes remain distinct inputs. If verified provenance traces both inputs to the same original observation, count that observation only once as independent support. Matching IDs or content digests alone do not establish a common origin. Existing local references and evidence continue to resolve in their original run Scopes, with identities and digests preserved through migration. The processor compares supporting evidence, counterexamples, and the target baseline. A target authorized for independent retention remains a valid baseline after a historical original becomes unavailable. Its current revision supplies no new evidence for revising itself.

**Review and commit.** The service checks authority and state at admission, when a background task reads the evidence, when it stores a Candidate, and at approval. Reviewers need authority to inspect the diff and the corresponding origin permission to open the original evidence. They may edit the proposed text while preserving the target and evidence. Approval rechecks the rule version, actual origins, use and audience, review authority (`scope.review`), target write authority, writable state, and baseline revision. Loss of a required origin grant blocks the pending commit, even if someone proposes dropping the counterexample. If a maintainer edits the target, the Family's conditional write rejects the stale Candidate. Reassessment starts another run against the new revision. Reading the committed conclusion and opening its evidence have separate authorization checks. [Candidate approval](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/review/service.py#L676) · [Local Dream revocation case](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/tests/e2e/test_artifact_dreaming.py#L1781)

**After evaluation.** The proposed cross-Scope flow uses Dream runs, Candidate decisions, and Artifact revisions to distinguish these outcomes. If the approval response is lost, look up the original Candidate decision and the corresponding Artifact revision. Return a confirmed commit to an authorized caller. If the outcome is still unknown, keep it unresolved without regenerating or republishing.

| Run outcome | What the target Scope can establish |
| --- | --- |
| `no_change` | This manifest was evaluated against the named baseline. The official revision stays as it was. |
| `needs_evidence` | Record the evaluation and the missing evidence. The current conclusion stands and can be reassessed when more evidence arrives. |
| Candidate pending or rejected | The proposal, evidence, and review state can be inspected. No official revision has been published from it. |
| Candidate approved | The stored decision identifies the committed target revision, which retains the evidence it adopted. |

## Scope archive, deletion, and cleanup

**Current.** Scopes have versioned relationships and revocable grants. Some Families manage their own states. Atomic Memory's `forgotten` state excludes that item from ordinary recall. Internal processing status summarizes local progress and blockers. The public API cannot yet archive or delete an entire Scope. [Scope API](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/openapi/powercontext.yaml#L759) · [Local processing status](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/processing_status.py)

```text
Current: Orders
  +-- Interface
  +-- Help
  Orders ..> Operations / Runbook [direct reference]
  No public Scope archive or delete

Proposed: archive Orders
  Orders [ordinary local writes, processing and Candidate commits stopped]
    +-- Interface [own state, still writable]
    +-- Help [own state, still writable]
  Reliability ..> Orders [valid grant to use archived material in Reliability]

Proposed: explicitly delete the confirmed subtree [Orders, Interface, Help]
  +-- access cutoff -> ordinary reads and Candidate commits stop in that set
  +-- resource cleanup -> Interface index [retry pending]
  +-- physical erasure [separate terms, if supported]
  Operations / Runbook [outside, retained]
  Analytics [outside] ..> Interface [unavailable]
```

**Archive and reopen.** Archive stops new Sources, Artifact content writes, ordinary local processing, and Candidate commits in the named Scope. Structural children keep their own states. Other Scopes can still use the archived material under valid source grants and write to their own targets. Grant management, reopening, and cleanup of an effective deletion remain available. Check state at enqueue, computation start, Candidate persistence, and final publication. State transitions and writes must share a transaction lock or equivalent version check so work in flight cannot commit after the cutoff. Reopen retains the Scope ID. Rescheduled work rechecks current authority and the target baseline.

**Deletion and retention.** Deletion requires an explicit request. A single-Scope delete should return a conflict if it still has children. For explicit subtree deletion, preview the set and authorize every target. Checking only the root version can miss a newly attached descendant. Serialize confirmation with attachment, move, and publication, or provide an equivalent check. Reconfirm any changed set. Delete only the Scopes in the confirmed set and their owned resources. The service removes outgoing sharing references to external Scopes. Consumers outside the set see references to deleted origins as unavailable. External Scopes continue to exist.

| Result | What the service can promise |
| --- | --- |
| Access cutoff effective | Ordinary reads, historical readback, evidence previews, Prepare, search, and plugin reads can no longer access the target, and stale jobs cannot commit. Authorized management calls can inspect limited status. Logical unavailability may leave stored content intact. |
| Requested cleanup complete | All required resource handlers finished the agreed actions. If index, cache, or job cleanup fails, keep a failed or incomplete state and resume unfinished work on retry. |
| Physical erasure complete under the agreed terms | Define the originals, history, copies, and stores covered and obtain completion evidence for that range. An empty search result cannot establish erasure. |

The Scope service persists the operation identity, confirmed set, access cutoff, and per-resource progress. Each registered service declares which resources it owns, how it checks visibility, and how it handles those resources idempotently per Scope. The affected resources include Artifacts, Sources, standing rules and checkpoints, document registrations, pending jobs, chunks, indexes, and caches. If a required service cannot perform the requested action, reject that mode before accepting the request. Applications choose initiation, retention periods, and how to handle copies under their control. Cross-store cleanup and batch operations on unrelated Scopes do not promise an atomic result across all targets. Cleanup resumes from the recorded progress, and the batch reports a result per Scope.

Existing Scopes start writable. Introducing deletion state requires a storage migration. Explicitly change the deployment default before removing its Scope, and clear or repair affected persistent bindings. Stale business mappings must also honor the access cutoff. Resolving a persistent binding or default pointer to a missing Scope currently returns `409 scope_binding_target_missing` and needs explicit repair. After an origin is purged, an authorized retained conclusion should still identify the exact origin and show that it is unavailable. Existing provenance and publication foreign keys constrain removal of origin records. One migration option is a content-free address tombstone preserving the original identity and digest. The storage design, deletion recovery, and physical erasure modes remain open. [Persistence constraints](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/src/powercontext/builtin/persistence/tables.py#L330) · [Binding resolution](../docs/workflows/scopes-and-access.md)

**Acceptance.** Once archive takes effect, even a previously queued local task cannot publish. A child attached or moved into or out of the deletion set after preview requires fresh confirmation. If index cleanup fails, ordinary readers can no longer open revisions in the deleted set, and an administrator can still see and retry the unfinished work.

## Using and maintaining team and project memory

The Orders project owner chooses an ordinary Scope for the official project Profile. The Reliability team can choose a different Scope for its on-call Skill. Members see material they are authorized to access through existing Scope grants. Applications should let users read the current memory, manage its ongoing input sources, and review or correct a conclusion.

```text
Alice's personal Scope
  captured Sources -> Profile policy -> personal Profile -> Prepare

Orders project Scope [review required]
  +-- current: local review Sources -> local Profile processing
  +-- proposed: Alice and Bob's work inputs -> cross-Scope Dream
  Candidate -> review -> Orders / Profile@5 -> members and their Agents
  Orders / Profile@5 ~~> Bob work / Duplicate callback@1
```

**Personal use.** A user chooses which work records to capture. A subject binding maps a user ID to an ordinary Scope. Capturing a record requires write access, and establishing a binding requires separate authority. The service can write a selected record into the work and personal Scopes in one transaction. The two Sources share an ID and digest but have independent Journal positions and processing progress. A new HTTP attempt creates a new pair of Sources, which callers must account for when retrying. Creating a new subject Scope enables its Profile policy. Binding an existing Scope preserves its policy.

Users can enable background scheduling or explicitly trigger generation. The default activation mode is `automatic`. To require review of each change, select `review_required`. Users can edit the proposed text, approve the Candidate, or reject it. Editing a Candidate preserves its target and evidence. Users can also replace the committed Profile using its current ETag or restore historical content as a new revision. A manual edit does not replay consumed Sources.

To use the Profile in an Agent turn, select it explicitly in Prepare. Reading the committed Profile does not trigger generation. Products can expose capture, policy configuration, processing, and context selection through plugins. [Personal Profile workflow](../docs/workflows/use-profiles.md) · [Agent context configuration](../docs/workflows/prepare-context-text.md) · [Cindy operations](https://github.com/oceanbase/powercontext/blob/1cb0e75dbd56227bb13550d63f301b8f39feb931/integrations/cindy/plugins/powercontext-hooks/node/worker.cjs#L192)

**Delivering material and maintaining a conclusion.** If the project may keep a particular incident report, a contributor can publish an Experience copy for members to search, inspect, or adopt manually. To maintain an official conclusion, the project uses a standing rule to select authorized external material automatically and can combine it with locally captured reviews, decisions, and counterexamples. A project that maintains a Profile can store at most one `profile/profile` in its Scope. Use `review_required` when a maintainer must review each change.

**Expected cross-origin processing.** The project and its contributors agree which work records can be used to produce which outputs, who may inspect evidence, and who may read the conclusions. The system selects qualifying updates automatically. Orders compares Alice's successful retry with Bob's duplicate charge and rewrites the general advice to specify the channel and idempotency conditions. Members open the committed project revision. Reviewers can inspect the exact counterexample under their grants. An operation such as `revise_profile` must specify whether it can accept an external Experience directly or needs its original Sources. The same kind of rule can update a personal Scope's Profile if the origins authorize that use and the execution principal may write the personal target.

Sharing only finished Experiences can leave out incident context or counterexamples that no one put in an Artifact. The application should let administrators fill known gaps, correct mistakes, and choose which project facts to capture in the future. After a manual edit, a stale Candidate cannot be approved but can still be rejected. Reassessment requires a new run against the new baseline. A new administrator needs the appropriate grants. The same Scope continues to own the collective record without a transfer of the outgoing administrator's personal Artifacts. For account erasure, the application identifies the personal data set and the collective outputs authorized for independent retention. The service handles each under its agreed scope and completion conditions.

## How RAG ingests, shares, and retrieves documents

**Expected product.** In the Orders knowledge space, users see their own Interface and Help libraries and the shared Operations runbook. When the Orders space manages the lifecycle of the first two libraries, the application can map them to structural child Scopes. A navigation-only space can use an application mapping alone. A user selects a library and imports a file. The document service returns a durable job, and the interface shows progress stage, failure reason, and available revision. Once publication succeeds, search results point to passages in exact document revisions. [RFC #2570 ingestion and retrieval design](https://github.com/oceanbase/powercontext/blob/71e842cd2a34a7943ab0adecb3542771e3da387e/docs/en/rfcs/2570-knowledge-base.md)

```text
Orders space Scope                              Operations space Scope
  +-- Interface Scope                             +-- Runbook Scope
  |     +-- immutable file Source                   +-- Timeouts@2 / chunk 4
  |     +-- import job: file Source -> API@3
  |     +-- API@3 with versioned chunks
  +-- Help Scope

Orders space Scope ..> Operations / Runbook Scope (shared entry, authorized use)

  Search Orders: authorized calls -> hit (Runbook, Timeouts@2, chunk 4)
  Remove shared entry: detach it     Delete Orders subtree: Interface + Help + Orders
```

**Ingestion and revision.** RFC #2570 reuses Source storage for immutable file originals. Document jobs parse those originals. A `document` Artifact represents a whole document revision, and the document service owns chunks, original locators, and indexes. A changed file or explicit reparse can publish a new revision. Failed publication leaves the previous published revision usable. Document jobs track progress separately from ordinary memory Source cursors. Integration must enforce consumer eligibility at write notification, startup reconciliation, automatic consumption, and manual generation so only the document pipeline consumes these originals. File originals and internal `lineage_only` operation records have different purposes.

**Sharing and readback.** When Orders references the Operations runbook library, its queries can find newly published content in the authorized libraries. The application resolves business library IDs to Scopes. The document service owns candidate filtering, indexing, and ranking within each library. It returns `(scope_id, document_ref, chunk_id)` and a location in the original. Aggregation preserves each hit's origin and the meaning of its rank within that library. After the runbook changes, an earlier hit still names the original document revision. Opening it rechecks access and availability at that revision. If sharing covers only one document or a named revision, exact reads must stay within that range. Searching it also requires Access to enforce the grant, the document service to restrict candidates before scoring, and index coverage for the selected revision. An independent handoff of a fixed revision requires document copy support and permission for the recipient to retain it. Using a hit to produce Topic Memory requires a writable target and validation of the document or chunk input under the operation's contract.

**Ownership and closure.** Removing the shared runbook only changes the Orders entry. Archiving Interface stops ordinary writes and local import publication there, while authorized external spaces can still use existing revisions. Explicit deletion of Orders and its structural subtree includes Interface and Help plus their registered originals, pending jobs, chunks, and indexes. Runbook remains in Operations and failed cleanup remains visible.

**Model configuration and rebuilds.** RFC #2570's transitional design lets libraries within one Scope configure parsing and chunking per library while sharing an embedding and index profile. Switching models rebuilds that shared Scope. The new mapping must specify which libraries share configuration, which are affected by a switch, and how existing `knowledge_base_ids` and jobs resolve to Scopes. [Configuration and model switches](https://github.com/oceanbase/powercontext/blob/71e842cd2a34a7943ab0adecb3542771e3da387e/docs/en/rfcs/2570-knowledge-base.md#81-change-model-and-rebuild-the-index)

# Drawbacks

Standing rules require change discovery, processing checkpoints, and revocation handling. More origins increase the chance that authorization or the target baseline will change during review. Cross-library queries need work limits and coverage reporting. Cross-store deletion must account for each cleanup result. The RAG service also needs to adapt configuration and job ownership before it can participate in Scope operations.

```text
Orders / Candidate [pending] -> Bob's required grant expires -> commit blocked
Delete Interface [access stopped] -> index cleanup fails -> cleanup incomplete
```

# Rationale and alternatives

Ordinary Scopes already provide stable identity, structure, and grants. Mapping cascading business relationships to Scopes lets additional resource services participate in a common range and lifecycle mechanism. Keeping the knowledge-base Artifact can organize documents but leaves each such collection to handle parent invalidation, unpublished resources, and cleanup at the Artifact layer. Built-in team or project types would introduce product-specific organization rules into the shared data model. Application mappings support both structurally owned spaces and navigation-only entries.

Applications can publish individual revisions for independent retention or write authorized observations directly to the target as Sources. These paths support fixed handoffs and local fact collection. Using updates still managed at their origin also requires recurring discovery within the agreed range and a manifest pinned for each run. An origin reference saves the storage and upkeep of a copy, but later reads depend on origin authority. Following references or lineage during deletion could remove another application's material. Confirming the structural subtree bounds the operation.

```text
Knowledge-base Artifact approach       Scope approach
  +-- Interface Artifact                  +-- Interface Scope
       +-- API@4 import job                    +-- API@4 import job
  Artifact service coordinates closure    Scope confirms range, job owner handles cleanup
```

# Prior art

PowerContext's Profile processing and Dream review provide two existing update paths. RFC #2570's file originals, per-document jobs, versioned passages, and indexes supply the RAG document-processing design. [Profile](1485_profile_artifact.md) · [Dream](1715_multi_artifact_dreaming.md) · [RAG](https://github.com/oceanbase/powercontext/pull/2570)

GitHub assigns team roles per repository. Linear retains private-content access restrictions when projects span teams. Nowledge Mem's Spaces and Crystals illustrate reading several authorized spaces, storing a conclusion in one, and preserving its origins. [GitHub repository roles](https://docs.github.com/en/organizations/managing-user-access-to-your-organizations-repositories/managing-repository-roles/repository-roles-for-an-organization) · [Linear private teams](https://linear.app/docs/private-teams) · [Spaces](https://mem.nowledge.co/docs/spaces) · [Crystals](https://mem.nowledge.co/docs/concepts/crystals)

```text
Personal Scope: Profile@4 and new Sources -> Profile processing
Orders Scope:   local evidence -> Dream -> Candidate -> review -> Artifact revision
```

# Unresolved questions

| Open decision | Recommended way to resolve it |
| --- | --- |
| Who may change a rule or authorize a project-visible conclusion from private evidence? | Define the rule editor, execution principal, evidence reviewers, and output audience with the Access design. Work through revocation during review and after commit to establish who may still read each conclusion or original and when disclosure must stop. |
| Which inputs suffice to revise or create a target? | Define the accepted Sources, Artifact Families and roles, originals or attachments that must be verified, and initial creation without a committed baseline for each operation. If a processor requires a persisted derived Source, specify its identity, deduplication, and cursor behavior on retry. |
| Which resources does each deletion mode cover, and what establishes completion? | Identify the required resources, access cutoff, and completion evidence for each action. Use an unresolved binding or default, a retained citation to a purged origin, and a failed index cleanup to settle service responsibilities and migrations. Recovery after deletion and physical erasure each need an explicit contract. |
| How is search limited to an item or historical revision, and how wide can an operation's range be? | Specify which grants allow search and which revisions the index covers. Use an item grant to check the candidate set before scoring, then verify read access at the exact address of a hit. Set discovery, query, and processing budgets according to each operation's coverage requirements and cost. |
| Which libraries must participate in a model switch after libraries are mapped to Scopes? | Map a set of `knowledge_base_ids` and in-flight jobs to their Scopes and identify the libraries that share an active configuration and must rebuild together. |

```text
Alice work / incident Source [private] -> Orders / proposed project conclusion
  +-- origin grant withdrawn before commit: block Candidate, retain Profile@4
  +-- origin grant withdrawn after a lawful commit: which terms govern Profile@5?
```

# Future possibilities

Event subscriptions can shorten the delay and coalescing notifications can reduce duplicate scheduling. Applications could package batch operations for their own workflows. Multi-hop sharing needs selection, authorization, and withdrawal behavior for each hop. Further Families and external stores can participate in resource handling.

```text
Alice publishes Retry trace@4 -> notifications collected -> one discovery run
  Orders ..> Alice work [recheck rule and grants before selecting @4]
```
