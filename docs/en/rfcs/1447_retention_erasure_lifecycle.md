- Proposal Name: `retention_erasure_lifecycle`
- Start Date: 2026-09-03
- Status: Draft
- RFC PR: [oceanbase/powercontext#1447](https://github.com/oceanbase/powercontext/pull/1447)
- Tracking Issue: [oceanbase/powercontext#1425](https://github.com/oceanbase/powercontext/issues/1425)
- Related Issues: [oceanbase/powercontext#1219](https://github.com/oceanbase/powercontext/issues/1219),
  [oceanbase/powercontext#1321](https://github.com/oceanbase/powercontext/issues/1321),
  [oceanbase/powercontext#1395](https://github.com/oceanbase/powercontext/issues/1395),
  [oceanbase/powercontext#1397](https://github.com/oceanbase/powercontext/issues/1397)
- Related RFCs: [RFC 0014](0014_memory_layer_design.md), [RFC 0046](0046_observability_foundations.md),
  [RFC 0048](0048_handoff_artifact.md), [RFC 0050](0050_artifact_candidate_review_inbox.md),
  [RFC 0051](0051_experience_skill_artifact_families.md), [RFC 0082](0082_handoff_report.md),
  [RFC 1345](1345_scope_organization_and_agent_integration.md),
  [RFC 1351](1351_standard_skill_package_lifecycle.md), [RFC 1396](1396_handoff_access_control.md),
  [RFC 1400](1400_source_definition_and_observation_model.md)

# Summary

This RFC defines how PowerContext retains, hides, archives, purges, and physically erases Sources and context Artifacts without rewriting immutable history.

Five operations stay distinct because they answer different questions and have different consequences:

- **Logical forgetting** removes content from normal retrieval and injection without rewriting history. It is the existing revisioned Memory `forget()` and `reactivate()` behavior.
- **Governance** changes whether an Artifact is eligible for discovery or publication. It is the existing `active`, `deprecated`, and `retired` head state.
- **Archival** changes the default visibility of a whole Scope while preserving its durable history.
- **Retention purge** deletes bounded operational records and unreferenced material after a versioned policy window.
- **Physical erasure** removes authoritative content that must no longer be retained, together with every declared projection and copy, and leaves only a content-free tombstone where references must remain valid.

The RFC adds a versioned Lifecycle Policy per deployment and Scope, a Lifecycle Run protocol with preview, plan digest, approval by digest, bounded batches, crash recovery, verification, and a content-free run receipt, Legal Holds that block purge and erasure and are visible in every preview, in-place content-free tombstones for erased authoritative rows, an impact report and an explicit derived-Artifact policy for Source erasure, stable erased states for exact citations, cleanup rules for full-text, vector, head, cache, package, and remote Receiver copies, Scope archival, and an append-only redacted lifecycle audit.

Nothing in this RFC deletes or rewrites history automatically. The default policy retains everything, so upgrading changes no behavior until an operator sets a policy version and at least one window. Automatic importance decay is not a lifecycle mechanism; any age-based or access-based policy must be explicit, previewable, and configured independently from retrieval ranking.

# Motivation

PowerContext already has three partial lifecycle mechanisms, each correct in its own domain and each explicitly stopping short of retention and erasure:

- RFC 0014 defines Memory `forget()` as a new Revision that marks entries `inactive` and `reactivate()` as the reverse. RFC 0014 states that forgetting "only prevents later retrieval or injection and does not satisfy a physical-erasure requirement" and leaves physical erasure to a separate design. The HTTP operation `retire_memory_entry` performs this logical forgetting; no public operation exposes reactivation.
- RFC 1351 defines Artifact head governance with `active`, `deprecated`, and `retired` states and a `governance_generation` CAS on `pc_artifact_heads`. The public lifecycle operation accepts only `family = 'skill'`. RFC 1351 also states that the first implementation "performs no automatic package garbage collection" and that a later collector "may delete only packages with no reachable Artifact, Candidate, or Source reference after a documented retention period."
- RFC 1400 protects any Source observation referenced by a durable Artifact revision from ordinary retention and garbage collection, states that head deletion does not authorize evidence removal, and defers retention to a design that first defines how exact evidence reports unavailable content and how legal or user-requested deletion interacts with immutable lineage.

Several stores therefore grow without any policy:

- `pc_sources` holds captured payloads of up to 4 MiB each. Handoff Receipts, Work Contracts, Task Outcomes, and Skill usage captures are Sources too, so operational evidence accumulates at the same rate as user content.
- `pc_artifact_candidate_versions` keeps the complete proposal of every rejected or approved Candidate forever, even though the approved content lives in the Artifact Revision and the rejected proposal is never read again.
- `pc_skill_packages` keeps every canonical package archive whether or not any Revision still references it.
- `pc_model_usage_daily` and `pc_recall_token_daily` are removed only with their Scope, and no Scope removal exists.

Three production requirements cannot be met by any of the existing mechanisms:

- **External deletion obligations.** A Connector may observe that an external object was positively deleted. RFC 1400 records that only as a head state. Some deployments must also stop retaining the captured value after a bounded window.
- **Erasure requests and offboarding.** A user or administrator may require that specific content, or everything in a Scope, no longer exist in PowerContext. Today the only options are a logical `forget` that keeps the text, or a manual database edit that breaks the schema.
- **Legal hold.** A deployment may need to suspend every scheduled deletion for selected material and prove that it did so.

The schema shapes the solution. Every reference between rows uses `ondelete="RESTRICT"`: Artifact lineage to `pc_sources` and `pc_artifacts`, heads to their Revision, Memory entry versions and heads to their Revision, Candidate heads to their versions. Deleting a referenced row is impossible without first destroying the references that make exact citations and lineage trustworthy. Erasure must therefore keep the row and remove the content.

The alternatives are attractive and wrong. A TTL column per table says nothing about authority, cascade, citations, projections, or audit. Deleting old Revisions breaks lineage, exact citations, Handoff verification, and rollback. Treating an Ebbinghaus-style relevance decay as a deletion policy conflates ranking with user intent, compliance, and durable history. Keeping everything forever after a logical retire cannot meet storage limits, external deletion obligations, or erasure requests. This RFC gives each requirement its own explicit operation.

# Guide-level explanation

## Five questions, five operations

| Question | Operation | Object | Reversible | Rewrites history | Physical |
| --- | --- | --- | --- | --- | --- |
| Should this still be recalled? | `forget` / `reactivate` | Memory entry | Yes | No, new Revision | No |
| Should this still be discovered or published? | `deprecate` / `retire` | Experience or Skill head | Deprecation yes, retirement no | No | No |
| Should this Scope stay in default views? | `archive` / `unarchive` | Scope | Yes | No | No |
| Has this record outlived its window? | `purge` | Operational records, unreferenced material | No | No | Yes, rows deleted |
| Must this content stop existing? | `erase` | Authoritative content | No | No, tombstone keeps identity | Yes, content removed |

The first three operations change visibility and are available to ordinary contributors and reviewers through the existing domain APIs. The last two mutate storage and are available only through the Lifecycle Run protocol, which always previews before it mutates.

## Think in record classes

Every stored row belongs to one record class. The class decides which lifecycle operations may touch it.

| Record class | Examples | Lifecycle operations |
| --- | --- | --- |
| Authoritative content | Source payloads, Artifact Revision content, Memory entry text, Candidate proposals, Skill package bytes | `erase` when explicitly requested; `purge` only when unreferenced and outside a policy window |
| Identity and lineage | Primary keys, lineage rows, publication provenance, journal positions, content digests | Never removed while any row depends on them; they are what a tombstone keeps valid |
| Rebuildable projection | Head search text, Memory entry heads, full-text and vector indexes, in-process Scope compositions | Cleaned as part of `erase`; rebuildable from authoritative content; never authoritative |
| Operational record | Source cursors, Connector checkpoints, publication desired state, revoked remote targets, daily statistics, completed Lifecycle Runs | `purge` after a policy window |
| Audit record | Lifecycle audit rows, Access Audit when RFC 1396 is implemented | Retained at least as long as the tombstones they explain; compacted into summaries only after their own window |

## Example 1: forget and reactivate a Memory entry

Nothing changes for this flow. A user asks the Agent to stop remembering a preference. The Agent calls `retire_memory_entry` with the exact `MemoryCitation`. PowerContext commits a new Memory Revision whose manifest marks the entry `inactive` and records `op="deactivate"`. The entry text, its version, and every earlier Revision remain readable through exact references. Retrieval and Prepare Context stop returning the entry.

Later the user changes their mind. The Agent calls `reactivate_memory_entry`, which this RFC adds as the public counterpart of the existing Runtime `reactivate()`. PowerContext commits another Revision marking the same entry version `active` again. No content version is created in either direction.

Logical forgetting never triggers purge or erasure. An inactive entry is not more eligible for erasure than an active one, and an erased entry cannot be reactivated because there is no content to restore.

## Example 2: preview a retention run

An operator enables retention for the first time:

```yaml
lifecycle:
  policy_version: "2026-09"
  max_items_per_run: 1000
  retention:
    terminal_candidates_days: 90
    unreferenced_sources_days: 365
    unreachable_skill_packages_days: 30
    statistics_days: 400
    completed_runs_days: 365
  unreferenced_sources_by_type:
    handoff-receipt: 180
```

Before anything is deleted, the operator previews one Scope:

```text
powercontext lifecycle preview --scope ws_7f3a --action purge
```

```text
policy_version: 2026-09          effective_policy_digest: sha256:9b1c…
plan_digest: sha256:4e0f…        truncated: false

record_class  family / source_type   reason_code      disposition  count
operational   candidate               policy_expiry    selected     41
operational   candidate               policy_expiry    held         3     hold: lh_a91b
authoritative source: handoff-receipt unreferenced     selected     118
authoritative source: content         unreferenced     protected    2     referenced by lineage since selection
authoritative skill package           unreachable      selected     6
operational   statistics              policy_expiry    selected     212
```

Every count is exact for the bounded selection. `held` rows name the Legal Hold that excludes them. `protected` rows explain why a row that matched a window is still not eligible. The preview mutates nothing and is safe to repeat.

To apply, the operator submits the same request with the plan digest:

```text
powercontext lifecycle apply --scope ws_7f3a --action purge --plan-digest sha256:4e0f…
```

The Server recomputes the plan. If any selected identity changed since the preview, it returns `lifecycle_plan_stale` and the operator previews again. Otherwise it creates a Lifecycle Run, deletes in bounded batches, and returns a run receipt with the final disposition counts. The receipt and the audit rows contain identities and counts, never content.

## Example 3: erase one Source observation

A wiki page captured into a Scope contained a secret. Forgetting is not enough; the payload must go. An administrator requests an erasure preview for the exact `SourceRef`:

```text
powercontext lifecycle preview --scope ws_7f3a --action erase \
  --source wiki_page:page-8841@obs-3 --derived retain
```

The preview returns the impact report:

```text
target      source wiki_page  identity_digest sha256:71aa…             disposition selected
derived     memory entry versions citing the Source                     2   policy retain
derived     experience revisions with the Source in lineage             1   policy retain
derived     handoff revisions citing the Source                         1   policy retain
copies      artifact publications of derived revisions in other scopes  0
holds       none
```

With `retain`, derived Artifacts keep their content. Their lineage still names the Source, and every exact citation to the Source resolves to a stable `erased` state. With `invalidate`, PowerContext additionally retires derived Experience and Skill heads and forgets derived Memory entries in new Revisions. With `cascade`, it erases the derived Revisions and entry versions as well, and the preview lists each of them so that nothing cascades silently.

After apply:

- `get_source` for the exact observation returns `410 content_erased` with the identity, `erased_at`, and `run_id`.
- The `pc_sources` row still exists with an empty payload, so Artifact lineage rows remain valid and the Scope journal position is unchanged.
- Handoff Continue on a Revision that cited the Source reports that evidence as `unavailable` with reason `erased`; it never substitutes another observation.
- Search projections contain nothing derived from the payload, and the run's verification phase proves it before the run completes.

## Example 4: legal hold

Counsel asks that nothing in a Scope be deleted until a review ends. An administrator creates a hold:

```text
powercontext lifecycle hold create --scope ws_7f3a --selector scope --reason legal --reference CASE-2026-14
```

From that moment every preview in the Scope lists matching items as `held` with the hold identifier, and every apply skips them. Scheduled purge does the same and records the skipped counts. Forgetting, governance, and archival remain available because they preserve content. The hold is released explicitly, and both creation and release appear in the lifecycle audit.

## Example 5: archive a completed Scope

A Workstream finished months ago. Its Handoffs and Memory should remain readable but should stop appearing in default lists, Scope selections, Context Reference expansion, and scheduled processing. The owner archives it:

```text
powercontext scope archive ws_7f3a --expected-version 12
```

Exact reads continue to work. New writes are refused with `scope_archived` so that a stale integration binding cannot silently resume a closed Workstream. `unarchive` restores the Scope with the next version. Archival deletes nothing and has no retention consequence.

## What changes for existing deployments

Nothing by default. New columns are nullable and new tables are created by the same additive schema path used for Skill distribution. Existing operations keep their names and behavior. Purge runs only when a policy window is set and a run is applied or scheduled. Erasure runs only when explicitly requested and approved by plan digest.

# Reference-level explanation

## Goals and non-goals

This RFC aims to:

- preserve the existing revisioned Memory `forget()` and `reactivate()` semantics and the existing Artifact head governance unchanged;
- define lifecycle states and allowed transitions for Memory entries, Experience and Skill heads, Handoff Revisions, Candidates, Source observations, Skill packages, Scopes, and remote targets;
- define a versioned Lifecycle Policy by deployment, Scope, record class, family, and Source type;
- fix the precedence among Legal Hold, administrator erasure, explicit user action, external-system deletion evidence, and policy expiry;
- define a Lifecycle Run protocol with preview, plan digest, authorization, approval by digest, bounded batches, idempotent apply, crash recovery, verification, and a content-free receipt;
- define in-place tombstones that keep identity, lineage, and referential integrity valid after erasure;
- define how derived Artifacts are retained, invalidated, or cascaded when a Source is erased, always with an impact report;
- define the exact read and citation behavior after forgetting, archival, purge, and erasure;
- define cleanup and verification of full-text, vector, head, cache, package, and remote Receiver copies;
- define which append-only records may be compacted and which authority may never be rewritten;
- define a redacted, append-only lifecycle audit.

This RFC does not define:

- Scope deletion; every reference to `pc_scopes` is `RESTRICT`, and the Scope model belongs to RFC 1345 and issue #1219;
- Memory manifest compaction or a maximum Memory size; that is issue #1321;
- an `archived` state on individual Artifact heads;
- automatic importance decay, automatic retirement, or any deletion driven by ranking, usage counts, or similarity;
- cryptographic shredding of stored payloads;
- recall of content already delivered to Agents, model Providers, exports, or hosts before erasure;
- the authorization implementation; this RFC names actions in the RFC 1396 vocabulary and defines behavior until that RFC is implemented;
- a Dashboard editing surface; the first version exposes lifecycle operations through HTTP and the CLI.

## Vocabulary

| Term | Meaning |
| --- | --- |
| Lifecycle action | One of `forget`, `reactivate`, `deprecate`, `retire`, `archive`, `unarchive`, `purge`, `erase` |
| Record class | `authoritative`, `identity`, `projection`, `operational`, or `audit` |
| Lifecycle Policy | A versioned document of retention windows and erasure defaults; the deployment policy overlaid by an optional Scope override |
| Effective policy digest | SHA-256 of the canonical merged policy that governed one run |
| Lifecycle Run | One bounded `purge` or `erase` execution in one Scope, created only from a plan digest |
| Plan | The bounded, ordered selection a preview computed, plus counts and the impact report |
| Plan digest | SHA-256 of the canonical selected identities and dispositions; apply requires an equal digest |
| Disposition | Why an item is or is not acted on: `selected`, `held`, `protected`, `blocked`, `skipped`, `erased`, `purged`, `forgotten`, `retired`, `remote_pending`, `verification_failed` |
| Reason code | Stable enum recorded in audit: `policy_expiry`, `unreferenced`, `unreachable`, `user_request`, `external_deletion`, `administrator`, `legal`, `derived_invalidate`, `derived_cascade`, `declared_copy` |
| Legal Hold | A persisted selector that excludes matching items from purge and erase until released |
| Tombstone | A row whose content columns hold the canonical empty value and whose `erased_at` and `erasure_run_id` are set |
| Declared copy | Content PowerContext itself wrote elsewhere from an exact Revision: publication copies in other Scopes, Skill packages, and Receiver-installed packages |
| Derived Artifact | A Revision or Memory entry version whose lineage or references include the erased target, directly or transitively |

## Record classes and stores

| Record class | Store | Notes |
| --- | --- | --- |
| Authoritative | `pc_sources.payload` | Captured observation value, up to `MAX_SOURCE_OBSERVATION_BYTES` |
| Authoritative | `pc_artifacts.content` | One exact Revision of any family |
| Authoritative | `pc_memory_entry_versions.text` | Entry text; `source_refs`, `artifact_refs`, and `entry_content_hash` are identity |
| Authoritative | `pc_artifact_candidate_versions.proposal`, `reason` | Proposal content and free-text reason |
| Authoritative | `pc_skill_packages.archive_bytes`, `manifest` | Canonical package; digests and sizes are identity |
| Identity | `pc_artifact_lineage_sources`, `pc_artifact_lineage_artifacts`, `pc_artifact_publications`, `pc_source_journal_heads`, all primary keys | Never erased or purged while a dependent row exists |
| Projection | `pc_artifact_heads.searchable_text`, `pc_memory_entry_heads`, SQLite `pc_memory_entry_fts`, `pc_memory_vector_entries`, `pc_memory_entry_vec`, OceanBase FULLTEXT index and vector table, Experience and Skill search projections, in-process Scope compositions | Rebuilt by the existing `rebuild_projections` paths, which must skip tombstones |
| Operational | `pc_source_cursors`, `pc_connector_checkpoints`, `pc_skill_publications`, `pc_agent_skill_targets` in state `revoked`, `pc_scope_creation_requests`, `pc_model_usage_daily`, `pc_recall_token_daily`, completed `pc_lifecycle_runs`, publisher staging directories | Purged by window; never cited |
| Audit | `pc_lifecycle_audit` | Append-only; see Lifecycle audit |

Handoff Receipts, Work Contracts, Task Outcomes, and Skill usage captures are Sources of dedicated types and follow the Source rules. Their retention window may be set per Source type.

## Lifecycle states and transitions by family

Existing state machines are unchanged. This RFC adds only the orthogonal content state `erased` on authoritative rows, the Scope state `archived`, and purge eligibility for terminal operational records.

```text
Memory entry (RFC 0014)          active <-> inactive           forget / reactivate, new Revision each time
Experience or Skill head (1351)  active <-> deprecated         CAS on governance_generation
                                 active | deprecated -> retired   irreversible
Candidate (RFC 0050)             pending -> approved | rejected   terminal
Source head (RFC 1400)           active | deleted              catalog state, unchanged here
Remote target (RFC 1351)         pending -> active -> revoked
Scope (this RFC)                 active <-> archived           CAS on scope version
Authoritative row (this RFC)     retained -> erased            irreversible, orthogonal to the states above
```

Rules per family:

- **Memory.** Entry versions are the only Memory erasure target. A Memory Revision is a manifest and is never erased; erasing it would break every later manifest. Erasing an entry version that is `active` in the current head first commits the existing forget path with reason `lifecycle_erasure`, so every head manifest stays consistent with the rule that active entries have content. An erased entry cannot be reactivated. Memory head governance is not defined here.
- **Experience and Skill.** Head governance is opened to `family = 'experience'` through a generic operation with the same three states, the same transitions, and the same CAS. Erasing the head Revision leaves `lifecycle_state` unchanged; head reads report the erased state, and Skill publications of that Revision converge to `unpublished`.
- **Handoff.** Committed Revisions remain immutable. There is no Handoff head governance. Erasure targets one exact Revision. When the erased Revision is the committed head, `latest` returns the erased state and never resolves to an earlier Revision; committing a new Handoff is the way forward.
- **Candidate.** Terminal Candidates become purge-eligible after `terminal_candidates_days`. Pending Candidates are never purged by age. Explicit erasure of a pending Candidate version rejects it first with `decision_reason = "lifecycle_erasure"` and then tombstones the version, so the head CHECK constraint holds.
- **Source.** An observation is `retained` or `erased`. Positive deletion evidence from a Connector changes only the head, as RFC 1400 defines; it becomes an erasure candidate only under `external_deletion.erase_after_days`, and that candidate still requires an approved run.
- **Skill package.** A package is `reachable` while any non-erased Skill Revision content, Candidate proposal, or skill-package Source references its `tree_digest`. Unreachable packages become purge-eligible after `unreachable_skill_packages_days`. Erasing the last non-erased Revision that references a package tombstones the package bytes in the same run because the package is a declared copy of that Revision.
- **Scope.** `archive` sets `archived_at` and increments `version`; `unarchive` clears it. Archival is per Scope; archiving a subtree is a client loop over descendants.
- **Remote target.** Revoked targets become purge-eligible after `revoked_targets_days`; RFC 1351 already guarantees they hold no usable credential.

## Reference graph, protection, and reachability

A row is **referenced** when another authoritative or identity row depends on it:

- a Source observation is referenced by any `pc_artifact_lineage_sources` row, and by any Memory entry version whose `source_refs` include it;
- an Artifact Revision is referenced by any `pc_artifact_lineage_artifacts` row, by any `pc_artifact_publications` row as source or target, by any Candidate version that targets it, by any Memory entry version whose `artifact_refs` include it, and while it is the head of its logical Artifact;
- a Skill package is referenced as defined above.

Generic lineage is the primary reference index. Memory and Handoff already record every Source and Artifact they cite in generic lineage when they commit a Revision. Every family that commits Revisions must keep that property, and the conformance suite verifies for each family that its citations are a subset of its lineage. An implementation may therefore compute reachability from `pc_artifact_lineage_sources` and `pc_artifact_lineage_artifacts`, and must additionally consult Memory entry `source_refs` and `artifact_refs` for entry-level derived items.

Referenced authoritative content is **protected** from purge. Policy expiry never deletes it. Only `erase` may remove it, and only through an approved run whose plan lists it.

Reachability is computed inside the apply transaction for every item, not only at preview time. An item that became referenced between preview and apply is recorded as `skipped` with reason `protected`.

## Lifecycle Policy

The deployment policy is configuration. A Scope override is a persisted document with the same shape; only the keys it sets override the deployment values.

```yaml
lifecycle:
  policy_version: "2026-09"            # operator label; required when any window or auto_apply is set
  max_items_per_run: 1000              # bounded selection per run, 1..10000
  auto_apply: false                    # scheduled purge only; erase never auto-applies
  schedule_seconds: null               # required when auto_apply is true
  retention:
    terminal_candidates_days: null     # approved and rejected Candidate heads and versions
    unreferenced_sources_days: null    # Source observations with no reference
    unreachable_skill_packages_days: null
    statistics_days: null              # pc_model_usage_daily, pc_recall_token_daily
    revoked_targets_days: null
    completed_runs_days: 365           # completed, failed, and cancelled Lifecycle Runs
    audit_days: null                   # purge audit compaction; never below any other window
  unreferenced_sources_by_type: {}     # source_type -> days; overrides unreferenced_sources_days
  external_deletion:
    erase_after_days: null             # positive deletion evidence -> erasure candidates
  source_erasure_derived_artifacts: retain   # retain | invalidate | cascade
```

`null` means retain forever. Validation rejects a policy that sets any window without `policy_version`, sets `auto_apply` without `schedule_seconds`, or sets `audit_days` below another window. A Scope override is written with CAS on its `generation` and records its own `policy_version`.

The effective policy for a run is the deployment policy overlaid by the Scope override. Its canonical JSON digest is the effective policy digest. Every run, receipt, and audit row records `policy_version` and the effective policy digest, so an operator can prove which policy authorized which mutation even after the configuration changes.

Windows are evaluated against trusted Server time and against the record's own timestamp: `created_at` for packages, runs, targets, Sources, and Revisions, `usage_date` for statistics, and `decided_at` for Candidates. Where a table lacks the timestamp today, the implementation adds it as a nullable column populated on write; rows without a timestamp are never eligible by age.

## Precedence

When more than one rule applies to one item, the first matching rule below wins:

1. **Legal Hold.** A matching active hold excludes the item from `purge` and `erase` regardless of requester, policy, or external evidence. Holds never block `forget`, `reactivate`, governance, or archival because those preserve content.
2. **Administrator erasure.** An approved erase run removes content regardless of retention windows and regardless of the item's visibility state. It cannot bypass a hold; the hold must be released first, and the release is audited.
3. **Explicit user action.** `forget`, `reactivate`, `deprecate`, `retire`, `archive`, and `unarchive` change visibility only. They never make an item purge-eligible and never remove content.
4. **External-system deletion evidence.** Positive deletion evidence updates the Source head. With `external_deletion.erase_after_days` set, the observation becomes an erase candidate after the window and appears in erase previews with reason `external_deletion`. It is never erased without an approved run.
5. **Policy expiry.** Windows drive `purge` only. Purge never removes referenced authoritative content and never creates tombstones.

## Legal Hold

```text
pc_lifecycle_holds
  hold_id            PK, opaque identity
  scope_id           FK pc_scopes RESTRICT
  selector_kind      scope | source_type | family | source | artifact | memory_entry
  selector           canonical payload of identities only
  reason_code        legal | administrator | user_request
  reference          nullable, <= 128 characters, opaque ticket or case reference
  created_at, created_by (nullable opaque principal)
  released_at, released_by (nullable)
  generation         CAS
```

A hold is active while `released_at` is null. An item matches a hold when the selector covers it: the whole Scope, every Source of one type, every Artifact of one family, or one exact Source, Revision, or entry version. A hold on a Revision also covers its declared copies and its package.

Effects:

- `preview` lists matching items with disposition `held` and the hold identifier, and reports per-hold counts;
- `apply` and scheduled purge skip matching items, count them as `held`, and complete normally;
- an explicit `targets` selector that names a held item is refused with `legal_hold_active` before any plan is computed, while policy selections skip held items; releasing the hold is a separate audited operation;
- creating or releasing a hold writes an audit row and never touches content or projections.

## Lifecycle Run protocol

### Request

```text
LifecycleRunRequest
  scope_id
  action              purge | erase
  selection
    policy            {}                       select by effective policy windows and external-deletion candidates
    targets           [Selector...]            explicit erase targets
  derived_policy      retain | invalidate | cascade   erase only; default from policy
  reason_code         administrator | user_request | external_deletion | legal | policy_expiry
  reference           nullable opaque reference
  plan_digest         required for apply, absent for preview

Selector
  source              SourceRef
  artifact            ArtifactRef                    any family except memory
  memory_entry        MemoryCitation
  memory_artifact     artifact_id                    every entry version of one Memory
  scope               {}                             every erasable authoritative row in the Scope
```

A Memory Revision, a Scope, a lineage row, or an identity-only row is not a valid erase target; the request fails with `lifecycle_target_invalid` and nothing is planned.

### Preview and plan

Preview is stateless and idempotent. It computes, inside one read transaction:

1. the candidate set for the selection, bounded by `max_items_per_run` in a deterministic order (journal position for Sources, then family, artifact identity, revision, entry version);
2. the disposition of each candidate: `selected`, `held`, `protected`, or `blocked` when the item lies in a Scope the caller may not mutate;
3. for `erase`, the impact report: derived Memory entry versions, derived Revisions by family through the transitive lineage closure, declared copies in other Scopes, and packages that become unreachable, each with the disposition the chosen `derived_policy` implies;
4. exact counts grouped by record class, family or Source type, reason code, disposition, and hold;
5. `truncated = true` when the candidate set exceeded the bound.

```text
LifecycleRunPlan
  scope_id, action, reason_code
  policy_version, effective_policy_digest
  plan_digest
  truncated
  items[]      {selector_kind, identity, record_class, reason_code, disposition, hold_id?}   bounded
  counts[]     {record_class, family?, source_type?, reason_code, disposition, hold_id?, count}
  impact       {derived_policy, derived[], declared_copies[], packages[]}
```

`plan_digest` is SHA-256 over the canonical ordered list of `(selector_kind, identity, disposition)` for every item, including derived items and declared copies. Two previews over unchanged data produce equal digests.

### Apply, batches, and recovery

`apply` recomputes the plan under the same rules and compares digests. A mismatch returns `409 lifecycle_plan_stale` and writes nothing. An equal digest creates one run:

```text
pc_lifecycle_runs
  run_id                  PK, opaque identity
  scope_id                FK pc_scopes RESTRICT
  action                  purge | erase
  state                   running | completed | completed_with_skips | verification_failed | failed | cancelled
  policy_version, effective_policy_digest, plan_digest
  reason_code, reference (nullable)
  plan                    canonical payload of plan items, identities only
  cursor                  nullable canonical payload: index of the next unprocessed item
  counts                  canonical payload of disposition counts
  requested_by            nullable opaque principal
  lease_until             nullable, resume fencing
  error_code              nullable
  created_at, updated_at, completed_at (nullable)
```

Execution rules:

- items are processed in plan order in batches; each batch is one database transaction that performs the item mutations, their projection cleanup, the audit rows, and the cursor advance together;
- every item is re-validated inside its batch: still exists, still eligible, not held, not already erased; an item that fails re-validation is recorded as `skipped` with the reason and the run continues;
- a second `apply` with the same plan digest while a run is `running` returns that run; after completion it returns `409 lifecycle_run_conflict` with the completed run identity, because the plan can no longer be recomputed equal;
- a run whose lease expired while `running` is resumed by `resume_lifecycle_run` or by the scheduler; resumption re-reads `cursor` and continues, so a crash between batches loses nothing and repeats nothing;
- `cancel_lifecycle_run` stops a run between batches; completed batches stay applied and are audited;
- for `erase`, declared copies in other Scopes are erased in the same run under one authorization decision; if any copy Scope is unauthorized, `preview` reports those items as `blocked` and `apply` refuses the whole plan with `lifecycle_forbidden_scope` before creating a run.

### Verification and receipt

After the last batch, an `erase` run verifies every erased identity: no `pc_memory_entry_heads` row, no full-text row, no vector metadata or vector row, `searchable_text` null for an erased head Revision, and the tombstone marker present. Any remaining projection moves the run to `verification_failed` and records the identity in audit. Remediation is the existing `rebuild_projections`, which rebuilds from authoritative rows and must skip tombstones, followed by `verify_lifecycle_run`. Remote copies are verified through Receiver observation: the run counts them as `remote_pending` until each target reports the exact package as unpublished, and the receipt lists pending targets by `target_id` only.

The receipt is the run row itself as returned by `get_lifecycle_run`: state, digests, disposition counts, pending remote targets, and timestamps. It contains no content, payload, text, or free-text reason.

### Scheduling

With `auto_apply` and `schedule_seconds` set, the Runtime scheduler runs a policy-selection `purge` for every non-archived and archived Scope, bounded per Scope by `max_items_per_run`, using the same preview-then-apply path with an internally computed digest. The scheduler never runs `erase`. External-deletion candidates appear in erase previews but always require an explicit apply.

## Tombstone contract

Erasure never deletes an authoritative row that another row depends on. It converts the row into a tombstone:

```text
tombstone(row):
  content columns      := canonical empty value   (zero-length bytes for canonical payload columns, "" for text)
  erased_at            := trusted Server time
  erasure_run_id       := run_id
  every other column   unchanged
```

| Table | Content columns emptied | Columns kept |
| --- | --- | --- |
| `pc_sources` | `payload` | `scope_id`, `source_type`, `source_id`, `journal_position` |
| `pc_artifacts` | `content` | identity, `revision` |
| `pc_memory_entry_versions` | `text` | identity, `version`, `previous_version_id`, `kind`, `source_refs`, `artifact_refs`, `entry_content_hash`, `created_in_revision` |
| `pc_artifact_candidate_versions` | `proposal`, `reason` | identity, `family`, `source_refs`, `artifact_refs`, target columns |
| `pc_skill_packages` | `archive_bytes`, `manifest` | `tree_digest`, `archive_digest`, `file_count`, sizes, `created_at` |

Readers check `erased_at` before decoding any content column and never decode a tombstone. `NOT NULL` constraints, primary keys, foreign keys, and CHECK constraints all continue to hold, because no column is set to `NULL` and no size or count column changes. A tombstone is irreversible; there is no un-erase.

`entry_content_hash`, `content_digest`, `tree_digest`, and `archive_digest` remain because manifests, publications, and Receivers verify identity through them. A digest of erased content is identity, not content; the Drawbacks section records the residual risk for very short texts.

A tombstone row remains referenced or unreferenced by the same rules as a retained row. An unreferenced tombstone becomes purge-eligible under the same window as an unreferenced retained row, so tombstones do not accumulate forever unless something still cites them.

## Erasure procedures by target

Every procedure runs inside the batch transaction that also writes the audit rows and advances the run cursor.

### Memory entry version

1. If the entry version is `active` in the current head manifest, commit the existing forget path against the current head with reason `lifecycle_erasure`. This is an ordinary Revision with `op="deactivate"`; on head CAS conflict the batch retries against the new head. RFC 0014 already requires the forget path to delete the head projection row and its full-text and vector rows.
2. Tombstone the `pc_memory_entry_versions` row.
3. Delete any remaining `pc_memory_entry_heads`, full-text, vector metadata, and vector rows for the version, then verify absence.
4. Later manifests keep referencing `entry_version_id` and `entry_content_hash`. `entries()` and `list_memory_entries` exclude erased versions unless `include_erased` is set, in which case the version appears with `erased_at`, an empty text, and its identity. `get_memory_entry` and `validate_citation` on the erased version report the erased state.

A `memory_artifact` selector expands to every entry version of that Memory and follows the same steps per version. The Memory Revisions themselves are never touched.

### Artifact Revision

1. Tombstone the `pc_artifacts` row for the exact Revision.
2. If the Revision is the head, set `pc_artifact_heads.searchable_text` to `NULL` and remove the Experience or Skill search projection. `lifecycle_state`, `replacement_artifact_id`, and `governance_generation` are unchanged. `latest` and head listings report the erased state and never fall back to an earlier Revision.
3. For a Skill Revision, set `desired_state = unpublished` for every `pc_skill_publications` row that publishes that exact Revision. The host-local publisher removes the installed package; remote Receivers converge and report the observation. The run counts each target as `remote_pending` until observed.
4. For a Skill Revision, tombstone the referenced `pc_skill_packages` row when no other non-erased Revision, Candidate version, or Source references its `tree_digest`.
5. Erase every declared copy: each `pc_artifact_publications` row whose source is this Revision names a target Revision with equal `content_digest` in another Scope; that target is erased by the same procedure, and its own copies transitively.
6. Handoff Continue, Resolve, and Handoff Report treat an erased Handoff Revision as unavailable content; report schema changes belong to RFC 0082.

### Source observation and derived Artifacts

1. Tombstone the `pc_sources` row. The journal position and every lineage row remain.
2. Compute the derived set: Revisions with the Source in `pc_artifact_lineage_sources`, Revisions downstream of those through `pc_artifact_lineage_artifacts`, and Memory entry versions whose `source_refs` include the Source. The set is computed at preview time for the plan digest and re-validated in the batch.
3. Apply `derived_policy`:
   - `retain`: no change to derived rows. Their lineage keeps the exact `SourceRef`; every exact citation to the Source resolves as `erased`.
   - `invalidate`: additionally transition derived Experience and Skill heads whose head Revision is derived to `retired` through the governance CAS, and forget derived active Memory entries in a new Revision with reason `source_erased`. Handoff Revisions are immutable and unchanged; their evidence checks report `erased`.
   - `cascade`: additionally erase every derived Revision and entry version by the procedures above, including their declared copies and packages.
4. The Source head defined by RFC 1400 is unchanged; erasure is not deletion evidence.

`invalidate` and `cascade` create ordinary Revisions or tombstones that appear in the plan and in the audit under reason `derived_invalidate` or `derived_cascade`. No derived item is ever changed unless the plan digest that approved the run included it.

### Scope-wide erasure

A `scope` selector selects every authoritative row in the Scope in journal and identity order, bounded by `max_items_per_run`, with `truncated = true` until a preview returns nothing selectable. Each run leaves the Scope in a consistent state: every erased Revision is a tombstone, heads still resolve, lineage is intact, and Memory manifests remain valid. Scope-wide erasure does not archive or delete the Scope; an operator may archive it afterwards.

## Retention purge classes

Purge deletes rows. It is allowed only for rows that nothing references and that are outside their window.

| Class | Eligible rows | Deletion |
| --- | --- | --- |
| Terminal Candidates | Heads in `approved` or `rejected` and their versions, decision older than `terminal_candidates_days` | Delete the head row, then the version rows |
| Unreferenced Sources | Observations, retained or tombstoned, with no reference, committed before the type-specific or default window | Delete the row; `pc_source_journal_heads.position` unchanged |
| Unreachable Skill packages | Packages no non-erased Revision, Candidate version, or Source references, `created_at` older than the window | Delete the row |
| Statistics | `pc_model_usage_daily` and `pc_recall_token_daily` rows with `usage_date` older than `statistics_days` | Delete the rows |
| Revoked remote targets | `pc_agent_skill_targets` in state `revoked`, `updated_at` older than the window | Delete the row and its publication rows |
| Completed runs | `pc_lifecycle_runs` in a terminal state, `completed_at` older than `completed_runs_days` | Delete the row; audit rows remain |

Cursors and Connector checkpoints are never purged by age because they are the resume state of live bindings. Removing a binding removes them, which is existing behavior.

## Projection, cache, and copy cleanup

Cleanup order inside each batch transaction:

1. authoritative tombstone or row deletion;
2. head projections: `pc_memory_entry_heads` rows, `pc_artifact_heads.searchable_text`;
3. full-text rows: SQLite `pc_memory_entry_fts` rows by identity; on OceanBase the FULLTEXT index follows the head row;
4. vector rows: SQLite `pc_memory_vector_entries` metadata and `pc_memory_entry_vec` rows; OceanBase `pc_memory_vector_entries` rows;
5. audit rows and run cursor.

After the batch commits:

6. the Runtime evicts the Scope's in-process compositions and locks, which is the existing `evict` path;
7. publication desired-state changes are already durable; publishers and Receivers converge asynchronously and remove staging directories on reconcile;
8. the verification phase reads back every projection for every erased identity.

`rebuild_projections` for Memory, Experience, and Skill must skip tombstones and must not fail on them. A rebuild after erasure is therefore a valid remediation and can never resurrect erased text. PreparedContext and Context Pack are not persisted, so they need no cleanup. Client-side caches, Agent transcripts, and model Provider logs are outside PowerContext and outside this RFC.

## Read and citation behavior after lifecycle actions

| State of the target | Exact read | `latest` or head | Listing | Search and recall | Handoff evidence check |
| --- | --- | --- | --- | --- | --- |
| Forgotten Memory entry | Readable | Head excludes it | Included only with `include_inactive` | Excluded | `available` |
| Deprecated head | Readable | Readable | Included, marked | Included only on request | `available` |
| Retired head | Readable | Readable | Excluded by default | Excluded | `available` |
| Archived Scope | Readable | Readable | Scope excluded by default | Excluded from Context Reference expansion | `available` |
| Purged row | Not found | Not applicable | Absent | Absent | `unavailable`, reason `missing` |
| Erased row | `410 content_erased` | `410 content_erased` when the head Revision is erased | Identity and `erased_at` only, with `include_erased` | Absent | `unavailable`, reason `erased` |

The `410` body carries the identity, `erased_at`, and `run_id`, and nothing else. An exact citation never resolves to a different Revision, entry version, observation, or to `latest`. `HandoffEvidenceCheck` gains `unavailable_reason` with values `erased` and `missing`; RFC 1396 may add `denied` when a policy denies the citation. Existing consumers that read only `status` are unaffected.

## Scope archival

`pc_scopes` gains a nullable `archived_at`. `archive_scope` and `unarchive_scope` take `scope_id` and `expected_version`, update `archived_at`, increment `version`, and return the descriptor. A conflicting version returns the existing scope conflict.

An archived Scope:

- is excluded from `list_scopes`, `resolve_scope_selection`, Dashboard aggregates, and Handoff Report candidate detection unless `include_archived` is set;
- is skipped by Prepare Context when it appears as a Context Reference of another Scope;
- is skipped by scheduled Source flush and Experience incubation;
- refuses writes with `409 scope_archived`: Source capture, `remember`, Handoff commit, Candidate creation, publication into the Scope, and remote enrollment;
- keeps every read, exact resolution, `get_scope`, lifecycle preview, apply, and hold operation available;
- keeps its children unchanged; archival is not inherited;
- is reported as archived by `resolve_scope_binding`, so an integration can prompt for a new Workstream rather than write into a closed one. Binding behavior beyond the flag belongs to RFC 1345.

Archival deletes nothing, creates no tombstone, and changes no retention eligibility.

## Append-only records and compaction

- **Source journal.** Journal positions are never reused or renumbered and `pc_source_journal_heads.position` never decreases. Purge leaves gaps. Source windows, flush, cursors, and `entries()` must tolerate gaps; the conformance suite verifies this.
- **Lifecycle audit.** Audit is append-only. Rows that explain an existing tombstone are never purged. Rows about purge, holds, and skips may be compacted after `audit_days` into one daily summary row per Scope, action, record class, reason code, and disposition with the count; the compaction itself writes an audit row.
- **Artifact authority.** `pc_artifacts` rows are never rewritten or renumbered, Memory manifests are never compacted by this RFC, and lineage rows are never deleted while either side exists.
- **Adjacent modules.** RFC 0082's Activity Event retention and purge remain that module's contract and must follow the same data-minimization rules.

## Lifecycle audit

```text
pc_lifecycle_audit
  audit_id                 PK, monotonic
  scope_id                 FK pc_scopes RESTRICT
  run_id, hold_id          nullable
  recorded_at
  action                   purge | erase | hold_create | hold_release | policy_set | archive | unarchive | compaction | verification
  record_class
  family, source_type      nullable
  identity                 nullable canonical payload of opaque identity; NULL for Source targets
  identity_digest          SHA-256 of the canonical identity tuple including scope_id
  reason_code
  disposition
  policy_version, effective_policy_digest   nullable
  principal_id             nullable opaque identifier
```

Audit contains identities, digests, counts, codes, and timestamps. It never contains payloads, Artifact content, Memory text, Candidate proposals, package bytes, free-text reasons, Source identifiers that may embed paths or URLs, credentials, host paths, or raw errors. Source targets are recorded by `source_type` and `identity_digest` only; Artifact and Memory targets are recorded by their opaque generated identifiers. Run receipts, logs, metrics, traces, and error bodies obey the same boundary, which is the boundary RFC 0046 and RFC 1396 already define.

## Persistence and migration

New tables: `pc_lifecycle_runs`, `pc_lifecycle_holds`, `pc_lifecycle_audit`, and `pc_lifecycle_scope_policies`.

```text
pc_lifecycle_scope_policies
  scope_id          PK, FK pc_scopes RESTRICT
  policy            canonical payload of the override document
  policy_version
  generation        CAS
  updated_at
```

New nullable columns: `erased_at` and `erasure_run_id` on `pc_sources`, `pc_artifacts`, `pc_memory_entry_versions`, `pc_artifact_candidate_versions`, and `pc_skill_packages`; `archived_at` on `pc_scopes`; `created_at` on `pc_sources` and `pc_artifacts`; and `decided_at` on `pc_artifact_candidate_heads`, set when a Candidate reaches a terminal state.

Migration follows the existing additive path: new tables through `create_all(checkfirst=True)` in the shared metadata, new columns through an idempotent `ensure_lifecycle_schema(connection)` that issues `ALTER TABLE ... ADD COLUMN` per dialect and is invoked at composition next to `ensure_skill_distribution_schema`. No CHECK constraint changes, no table rebuild, no data rewrite. Existing rows have `NULL` in every new column and are therefore retained, unarchived, and ineligible by age until a timestamp exists.

SQLite and OceanBase share the table definitions and the contract tests. Full-text and vector cleanup differ by backend exactly as their projections already do.

Downgrade after an erasure is safe for the schema and unsafe for reads of tombstones: older code that decodes an empty payload fails with the existing invalid-stored-payload error. Operator documentation must state this.

## Runtime and service boundary

The Runtime exposes lifecycle operations beside the existing domain applications:

```python
class LifecycleApplication(Protocol):
    def for_scope(self, scope_id: str, /) -> ScopedLifecycle: ...


class ScopedLifecycle(Protocol):
    async def preview(self, request: LifecycleRunRequest, /) -> LifecycleRunPlan: ...
    async def apply(self, request: LifecycleRunRequest, /) -> LifecycleRun: ...
    async def resume(self, run_id: str, /) -> LifecycleRun: ...
    async def verify(self, run_id: str, /) -> LifecycleRun: ...
    async def cancel(self, run_id: str, /) -> LifecycleRun: ...
    async def get_run(self, run_id: str, /) -> LifecycleRun: ...
    async def list_runs(self, *, cursor: str | None = None, limit: int = 50) -> LifecycleRunPage: ...
    async def create_hold(self, request: CreateLegalHold, /) -> LegalHold: ...
    async def release_hold(self, hold_id: str, expected_generation: int, /) -> LegalHold: ...
    async def list_holds(self, *, include_released: bool = False) -> tuple[LegalHold, ...]: ...
    async def effective_policy(self) -> EffectiveLifecyclePolicy: ...
    async def set_policy_override(self, request: SetScopeLifecyclePolicy, /) -> EffectiveLifecyclePolicy: ...
    async def list_audit(self, *, cursor: str | None = None, limit: int = 100) -> LifecycleAuditPage: ...
```

Tombstone awareness lives in the persistence repositories, not in callers. Source, Artifact, Memory, Candidate, and package repositories raise a typed `ContentErasedError` from `powercontext.errors`, a `PowerContextError` and `LookupError`, with family-specific subclasses. Family services translate it into their domain results: Handoff evidence checks yield `unavailable` with reason `erased`, Memory `validate_citation` raises it, and the Server maps it to `410 content_erased`.

`MemoryEntryVersion` gains an optional `erased_at`. `ArtifactCatalog.revisions()` excludes erased Revisions; `get()` on an erased Revision raises the typed error; erased identities are discoverable through listings with `include_erased` and through the audit.

## Public API

`openapi/powercontext.yaml` remains the source of truth; generated clients are regenerated with `make api-generate`. All operations use `POST` with JSON bodies and explicit `scope_id`, as the existing Scope, Memory, and Skill operations do.

| Operation | Path | Purpose | Action |
| --- | --- | --- | --- |
| `preview_lifecycle_run` | `/v1/lifecycle/runs/preview` | Compute a plan without mutation | `lifecycle.preview` |
| `apply_lifecycle_run` | `/v1/lifecycle/runs/apply` | Create and start a run from a plan digest | `lifecycle.apply` |
| `resume_lifecycle_run` | `/v1/lifecycle/runs/resume` | Continue a run whose lease expired | `lifecycle.apply` |
| `verify_lifecycle_run` | `/v1/lifecycle/runs/verify` | Re-run verification after remediation | `lifecycle.apply` |
| `cancel_lifecycle_run` | `/v1/lifecycle/runs/cancel` | Stop between batches | `lifecycle.apply` |
| `get_lifecycle_run` | `/v1/lifecycle/runs/get` | Read the run receipt | `lifecycle.preview` |
| `list_lifecycle_runs` | `/v1/lifecycle/runs/list` | Page runs in a Scope | `lifecycle.preview` |
| `create_legal_hold` | `/v1/lifecycle/holds/create` | Create a hold | `lifecycle.hold` |
| `release_legal_hold` | `/v1/lifecycle/holds/release` | Release a hold with CAS | `lifecycle.hold` |
| `list_legal_holds` | `/v1/lifecycle/holds/list` | List holds | `lifecycle.preview` |
| `get_lifecycle_policy` | `/v1/lifecycle/policy/get` | Effective policy and digest for a Scope | `lifecycle.preview` |
| `set_scope_lifecycle_policy` | `/v1/lifecycle/policy/set` | CAS-update the Scope override | `lifecycle.policy` |
| `list_lifecycle_audit` | `/v1/lifecycle/audit/list` | Page audit rows | `lifecycle.audit.read` |
| `archive_scope` | `/v1/scopes/archive` | Archive with CAS | `scope.archive` |
| `unarchive_scope` | `/v1/scopes/unarchive` | Unarchive with CAS | `scope.archive` |
| `reactivate_memory_entry` | `/v1/memory/entries/reactivate` | Public counterpart of `retire_memory_entry` | same as `retire_memory_entry` |
| `update_artifact_lifecycle` | `/v1/artifacts/lifecycle/update` | Head governance for `experience` and `skill` | same as `update_skill_lifecycle` |

`retire_memory_entry`, `update_skill_lifecycle`, and every existing read keep their contract. Exact reads of erased content return `410`; list responses gain optional `erased_at` and `include_erased`; Scope responses gain optional `archived_at`.

| Error code | Status | Meaning |
| --- | --- | --- |
| `content_erased` | 410 | The exact target is a tombstone; body carries identity, `erased_at`, `run_id` |
| `lifecycle_plan_stale` | 409 | Recomputed plan digest differs from the submitted one |
| `lifecycle_run_conflict` | 409 | A run for this plan already completed, or the run is not in a state that allows the operation |
| `legal_hold_active` | 409 | An explicit erase target is under an active hold |
| `scope_archived` | 409 | A write was attempted in an archived Scope |
| `lifecycle_forbidden_scope` | 403 | The plan includes declared copies in a Scope the caller may not mutate |
| `lifecycle_target_invalid` | 422 | The selector is not an erasable target |
| `lifecycle_policy_invalid` | 422 | The policy document fails validation |

The CLI adds `powercontext lifecycle preview|apply|resume|verify|cancel|runs|run|hold create|hold release|hold list|policy show|policy set|audit`, `powercontext scope archive|unarchive`, and `powercontext memory reactivate`. MCP does not expose lifecycle mutations in the first version. The Dashboard must render erased and archived items as unavailable without content; an editing surface is a future possibility.

## Authorization

Actions use the RFC 1396 vocabulary and are granted to `scope.admin` for the Scope and to `server.admin` for every Scope:

| Action | Meaning |
| --- | --- |
| `lifecycle.preview` | Preview plans, read runs, holds, and effective policy |
| `lifecycle.apply` | Create, resume, verify, and cancel runs |
| `lifecycle.hold` | Create and release Legal Holds |
| `lifecycle.policy` | Set the Scope policy override |
| `lifecycle.audit.read` | Read lifecycle audit |
| `scope.archive` | Archive and unarchive the Scope |

A plan that erases declared copies in other Scopes requires `lifecycle.apply` in every one of them or `server.admin`; otherwise those items are `blocked` and apply refuses the plan. Until RFC 1396 is implemented, every lifecycle operation is available only to the caller RFC 1396 treats as the legacy local administrator: the configured bearer token, or the loopback caller when authentication is disabled. When a PDP is unavailable, lifecycle operations fail closed with `503` and mutate nothing.

## Configuration

`LifecycleConfig` is a pydantic model under the Builtin and Server configuration beside `runtime`, `inference`, and `external_skills`, loaded through the same settings mechanism. Its shape is the policy document above. Validation errors are configuration errors at startup, and `lifecycle_policy_invalid` for Scope overrides.

## Observability

Following RFC 0046, lifecycle emits:

- metrics `powercontext_lifecycle_items_total{action, record_class, disposition}`, `powercontext_lifecycle_runs_total{action, state}`, and `powercontext_lifecycle_run_duration_seconds{action}`; labels are bounded enums and never include `scope_id`, identities, or hold identifiers;
- structured log events `lifecycle.run.completed`, `lifecycle.run.failed`, `lifecycle.run.verification_failed`, and `lifecycle.hold.changed` with `run_id`, `scope_id`, `action`, counts, and error codes;
- a `lifecycle.run` span per run with the same attributes.

No signal carries content, free-text reasons, Source identifiers, or raw backend errors.

## Compatibility

- Existing deployments observe no change until a policy sets a window or an operator applies a run.
- `forget()`, `reactivate()`, `retire_memory_entry`, head governance, Candidate review, and Source capture keep their semantics; `reactivate_memory_entry` and `update_artifact_lifecycle` are additive.
- OpenAPI changes are additive: new operations, optional response fields, and one new status code on exact reads.
- Python changes are additive: optional `erased_at` on `MemoryEntryVersion`, `include_erased` and `include_inactive` listing options, the typed erased error, and `unavailable_reason` on evidence checks.
- Schema changes are additive and idempotent on both backends.
- Downgrade keeps the schema usable; reading a tombstone with older code fails with the existing invalid-stored-payload error.

## Implementation slices

Each slice leaves the system coherent and independently verifiable:

1. **Schema and tombstone reads.** New columns and tables, `ensure_lifecycle_schema`, tombstone-aware repositories, `ContentErasedError`, `410 content_erased`, `unavailable_reason` on evidence checks, `include_erased` listings, and `rebuild_projections` skipping tombstones.
2. **Policy and preview.** `LifecycleConfig`, Scope override with CAS, effective policy digest, reference graph and reachability, purge and erase planning, impact report, plan digest, and the preview operation.
3. **Holds and purge runs.** `pc_lifecycle_holds`, hold matching, run rows, batches, cursor, lease, resume, cancel, audit rows, receipts, and purge for every class on both backends.
4. **Erasure.** Memory entry, Artifact Revision, Source with `retain`, `invalidate`, and `cascade`, declared copies, package handling, projection cleanup, verification, and remote convergence accounting.
5. **Archival and governance parity.** `archived_at`, `archive_scope`, `unarchive_scope`, archived-Scope effects, `reactivate_memory_entry`, and `update_artifact_lifecycle` for Experience.
6. **Surfaces and operations.** OpenAPI, generated clients, CLI, scheduler auto-apply for purge, operator how-to documentation, and Dashboard rendering of erased and archived items.

## Test and acceptance plan

The implementation is complete only when these observable scenarios pass on SQLite and OceanBase:

- a user forgets and later reactivates a Memory entry through the public API; no content version is created and every earlier Revision reads unchanged;
- two previews over unchanged data return equal plan digests; a preview after any eligible change returns a different digest, and apply with the old digest returns `lifecycle_plan_stale` and writes nothing;
- a preview reports exact bounded counts by record class, family or Source type, reason code, and disposition, and `truncated` when the selection exceeds the bound;
- a purge run deletes only unreferenced rows outside their window, leaves journal positions and cursors valid, and Source windows and flush tolerate the gaps;
- a run interrupted between batches resumes from its cursor, completes without repeating or skipping an item, and its counts equal the plan minus recorded skips;
- an erased Memory entry, Artifact Revision, and Source observation each leave a tombstone row, preserve every foreign key, return `410 content_erased` on exact reads, are absent from search, recall, full-text, vector, and head projections, and stay absent after `rebuild_projections`;
- `latest` of an erased head Revision returns `410` and never returns an earlier Revision; a Handoff citing erased evidence resolves it as `unavailable` with reason `erased` and never substitutes another observation or version;
- erasing a Source with `retain` changes no derived row; `invalidate` retires and forgets exactly the derived items listed in the plan; `cascade` erases exactly them; no derived item changes unless the plan digest included it;
- erasing a Skill Revision converges its publications to `unpublished` on local and remote targets, tombstones its package when unreachable, and erases declared copies in other Scopes only when authorized there;
- an active Legal Hold appears in preview with `held` counts and the hold identifier, apply skips the held items, an explicit erase of a held item returns `legal_hold_active`, and release is audited;
- an archived Scope is excluded from default listing, selection, Context Reference expansion, and scheduled processing; exact reads work; writes return `scope_archived`; unarchive restores it;
- a scheduled purge runs only when `auto_apply` is set, never runs `erase`, and records the same audit and receipts as a manual run;
- a sentinel string placed in erased content never appears in logs, metrics labels, traces, audit rows, run receipts, or error bodies produced by the run;
- the same contract vectors produce identical dispositions and counts on both backends.

Cross-component scenarios belong in `tests/e2e/` and assert through the public HTTP contract. Focused tests cover policy validation and merging, reachability, plan ordering and digest, hold matching, tombstone readers, batch re-validation, and lineage-covers-citations conformance for every family, without freezing private call order.

# Drawbacks

- Tombstones keep identities, digests, sizes, and timestamps forever unless the row becomes unreferenced. A digest of a very short erased text is a weak oracle; deployments that consider this unacceptable need cryptographic shredding, which this RFC does not provide.
- Content already delivered before erasure cannot be recalled: Agent transcripts, model Provider logs, exported Handoff Reports, and Receiver hosts that never reconnect. The run receipt makes the remote gap visible but cannot close it.
- Erasure is per Revision and per entry version. Free-text `reason` fields in Memory changes and Handoff omissions are not erasable at field granularity; removing them means erasing the whole Revision.
- Four new tables, tombstone and timestamp columns on seven existing tables, and per-dialect migrations increase the schema surface that both backends must keep in step.
- Approval by plan digest forces a fresh preview whenever eligible data changes. Busy Scopes may need smaller `max_items_per_run` values to apply at all.
- Derived Memory entries are found through `source_refs`, which is a JSON column; large Scopes pay a scan unless a reference index is added later.
- Refusing writes in an archived Scope is stricter than a pure visibility change and may surprise an integration whose binding still points at the Scope.
- Verification reads every projection for every erased identity, which makes large erase runs slower than the mutation alone.

# Rationale and alternatives

## Chosen: in-place tombstones, explicit runs, one policy document

Every reference in the schema is `RESTRICT`, so the only erasure that keeps exact citations, lineage, manifests, and publications verifiable is one that keeps the row and removes the content. A run protocol with preview, digest, batches, and receipts is the smallest mechanism that satisfies bounded previews, authorization, idempotency, crash recovery, and audit at once. One versioned policy document covers deployment, Scope, record class, family, and Source type without a rules engine.

## Alternative: a TTL column on every table

Rejected. It cannot express authority, protection by reference, cascade, citation behavior, projection cleanup, holds, or audit, and it silently deletes referenced rows or fails on foreign keys.

## Alternative: delete old Revisions

Rejected. Deleting Revisions breaks lineage, exact citations, Handoff verification, and rollback, and violates RFC 0048 and RFC 1400.

## Alternative: use Ebbinghaus-style decay as the deletion policy

Rejected. Relevance decay is a ranking signal. Using it for deletion conflates retrieval quality with user intent, compliance, and durable history, and it cannot be previewed, held, or audited as a decision.

## Alternative: keep everything forever after a logical retire

Rejected. It cannot meet storage limits, external deletion obligations, erasure requests, or offboarding.

## Alternative: a separate tombstone table and deletion of the original row

Rejected. Foreign keys from lineage, heads, manifests, and publications point at the original row. Moving identity to another table would require rewriting every reference, which is exactly the history rewrite this RFC forbids.

## Alternative: physical row deletion with cascading foreign keys

Rejected. Cascades would delete lineage rows, heads, and manifests, destroying the evidence graph and making exact citations resolve to nothing or to the wrong thing.

## Alternative: cryptographic shredding of payloads

Deferred. Per-Scope or per-observation keys would let erasure become key destruction, but full-text and vector projections still hold derived plaintext, key management adds an operational dependency, and tombstones already satisfy the acceptance criteria. Shredding can be layered on later.

## Alternative: persist previews server-side and approve by identifier

Rejected for the first version. Recomputing the plan and comparing digests gives the same approval guarantee without a plan store, and it detects concurrent changes that a stored plan would hide.

## Alternative: an `archived` state on Artifact heads

Deferred. Changing `lifecycle_state` values requires CHECK constraint changes on both backends and a table rebuild on SQLite. Scope archival meets the visibility requirement with a nullable column and existing CAS.

# Prior art

- RFC 0014 defines revisioned `forget()` and `reactivate()` and explicitly leaves physical erasure to a separate design; this RFC is that design and keeps the Memory contract intact.
- RFC 1351 defines head governance, defers package garbage collection to a collector with a documented retention period, and requires unpublication to remove only exact intact packages. This RFC defines that collector and reuses the publication desired-state model for erasure convergence.
- RFC 1400 protects referenced observations, keeps head deletion separate from evidence, and requires unavailable evidence to be reported rather than resolved elsewhere. This RFC implements those requirements.
- RFC 1396 defines the audit data-minimization boundary, the action vocabulary, and the rule that Continue marks deleted or retired citations unavailable. This RFC reuses all three and adds the `erased` reason.
- RFC 0082 defines Activity Event retention with a purge operation and an archived catalog state for Workstreams; both informed the purge classes and Scope archival here.
- RFC 0046 defines what telemetry may never contain; lifecycle signals follow it unchanged.
- Outside PowerContext, tombstone records in log-compacted streams, garbage collection of unreachable objects with a grace period in content-addressed stores, object-lock legal holds in cloud storage, and index lifecycle phases in search engines use the same separation between identity, content, visibility, and physical removal.

# Unresolved questions

- Scope deletion. Every reference to `pc_scopes` is `RESTRICT`; whether a Scope may ever be deleted, and what it means for children and publications, belongs to the Scope model in issue #1219.
- Whether external-deletion erasure candidates may auto-apply after their window in deployments that opt in, or must always be approved manually as this RFC requires.
- Whether writes into an archived Scope should be refused, as specified here, or allowed with a warning.
- Whether previews should be persisted for audit in addition to runs.
- Whether Memory and Handoff heads need governance states, and whether individual Artifact heads need an `archived` state.
- How Memory manifest compaction from issue #1321 must treat erased entry versions.
- Whether field-level erasure of free-text `reason` and omission fields is required.
- Whether audit compaction into summaries is needed in the first implementation or can wait for real volume.
- Whether a persisted reference index for Memory entry `source_refs` and `artifact_refs` should be part of the first implementation.

# Future possibilities

- A Dashboard lifecycle page for runs, holds, policy, and audit, and Dashboard actions for archival.
- Policy templates for common compliance regimes, expressed as the same document.
- Per-Source-Definition mapping from positive deletion evidence to retention behavior, building on RFC 1400 Connector guarantees.
- Cryptographic shredding for captured payloads and packages layered under the same run protocol.
- An export-before-erase bundle that produces a verifiable copy for the requester before content is removed.
- MCP exposure of read-only lifecycle status for Agents that must explain why evidence is unavailable.
- Project-level retention, pinning, and legal hold for Handoff Reports as RFC 0082 anticipates.
