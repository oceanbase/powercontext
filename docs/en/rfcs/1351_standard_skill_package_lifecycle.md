- Proposal Name: `standard_skill_package_lifecycle`
- Start Date: 2026-08-21
- RFC PR: [oceanbase/powercontext#1351](https://github.com/oceanbase/powercontext/pull/1351)
- Related RFCs: [RFC 0031](0050_artifact_candidate_review_inbox.md),
  [RFC 0051](0051_experience_skill_artifact_families.md),
  [RFC 0072](0072_scoped_statistics_and_usage.md), and
  [RFC 1304](1304_experience_skill_review_page.md)

# Summary

This RFC turns the PowerContext-managed `skill` Family from an instruction-only record into a governed, standard Agent
Skill package and closes the lifecycle from discovery or authorship through Review, Library search, target publication,
observed use, revision, deprecation, and safe unpublication.

A managed Skill Revision owns one immutable package rooted at `SKILL.md`. The package may also contain `scripts/`,
`references/`, `assets/`, and other bounded files allowed by the Agent Skills format. PowerContext stores a complete,
content-addressed package snapshot, preserves exact external packages during import, and publishes the same approved
bytes to compatible Codex and Claude Code targets. Agent-specific adapters choose locations and report compatibility;
they do not silently rewrite the approved package.

The implementation supports configured targets on the PowerContext Server host and an independently accepted remote
distribution slice. In remote mode, the Server stores the desired Revision for a target,
while a lightweight Receiver in the Codex or Claude Code integration pulls it over HTTPS by default, verifies it, installs it
atomically, and returns an exact receipt. A remote host does not need a complete PowerContext Server or database, but it
does need an enrolled Receiver. The Server does not write Agent directories through SSH or a remote filesystem.

The package declares content and requirements, not authority. Review, approval, search, publication, and an optional
`allowed-tools` field never grant execution, filesystem, network, secret, or dependency-install permissions. Script
execution remains owned by the receiving Agent and its host policy. This RFC defines static validation and environment
compatibility assessment but does not add a general PowerContext script runner.

The closed loop is:

```text
discover, upload, or generate a package
  -> capture an exact package snapshot
  -> validate format, files, provenance, and risk
  -> create a pending Candidate
  -> Review
  -> approve an immutable Skill package Revision
  -> index the current active head in Skills Library
  -> explicitly publish the exact Revision to a local target or declare it as a remote target's desired state
  -> let an available remote Receiver converge and report its observed Revision and digest
  -> record bounded selected/invoked/outcome evidence when the integration can observe it
  -> propose a successor Revision, deprecate, retire, or safely unpublish
```

# Motivation

## The managed Skill content is not yet a standard package

The current managed Skill stores `name`, `description`, `instructions`, and `validation`. Publication generates one
`SKILL.md` plus a PowerContext manifest. This is enough to review an instruction core, but it cannot preserve a normal
Agent Skill package containing scripts, references, templates, examples, licenses, or binary assets.

The current External Skill Registry already fingerprints every regular file under a local package. Explicit import,
however, snapshots only `SKILL.md` and asks a generation model to create new instruction-only content. That behavior is
appropriate for a semantic fork, but not for an exact import: a useful script or reference may disappear even though
the user selected a specific package fingerprint.

The result is an incomplete loop:

```text
external package with scripts and references
  -> exact whole-package fingerprint
  -> SKILL.md-only snapshot
  -> generated instruction core
  -> SKILL.md-only publication
```

PowerContext needs one package contract from import through publication so the reviewer can approve the content that an
Agent will actually discover.

## Package portability does not imply runtime portability

A package can be copied between hosts while its scripts still depend on a particular operating system, architecture,
interpreter, executable, working directory, network policy, or environment variable. Codex and Claude Code may also run
under different host policies even when both accept the same package layout.

PowerContext must not solve that mismatch by producing different unreviewed packages per target. The same approved
package remains authoritative. A target-specific environment profile and a rebuildable compatibility assessment explain
whether the target can use it. Missing capabilities produce `incompatible`, `unknown`, or `manual_review_required`; they
do not trigger an automatic package rewrite or dependency installation.

## A growing Library needs a governed working set

Package storage can deduplicate identical content, but storage alone does not prevent discovery noise. Publishing every
approved Skill into every Agent directory would make names conflict, expose stale packages, and enlarge the Agent's
working set without evidence that those Skills are useful for the current project.

PowerContext therefore distinguishes:

| Layer | Meaning |
| --- | --- |
| External Registry | Rebuildable observations of Agent-native packages owned elsewhere |
| Governed Library | Approved PowerContext-managed Skills and visible external registrations |
| Active managed heads | Managed Skills eligible for normal Library search and new publication |
| Published set | Exact managed Revisions physically present in one configured Agent target |
| Usage evidence | Bounded observations of selection, invocation, validation, and task outcome |

The Library may grow, while search remains limited to current eligible heads and publication remains explicit per target.

## Approval is not the end of the lifecycle

RFC 0051 and RFC 1304 establish Candidate Review, immutable Artifact Revision, and explicit host-local publication.
They intentionally defer package hosting, retirement, unpublication, ranking, and usage attribution. A usable Skills
product now needs the remaining transitions without weakening those trust boundaries.

The design must support correction and retirement without mutating history:

```text
Skill@1 is approved and published
  -> later use exposes a missing validation step
  -> exact usage evidence targets Skill@1
  -> Review approves Skill@2
  -> target reports update_available
  -> explicit publication replaces only the intact managed package
  -> Skill@1 remains exactly readable
```

# Guide-level explanation

## Think of a Skill as a package, not a procedure record

A standard managed Skill looks like this:

```text
release-check/
├── SKILL.md
├── scripts/
│   ├── verify.py
│   ├── linux/
│   │   └── prepare.sh
│   └── windows/
│       └── prepare.ps1
├── references/
│   └── release-policy.md
├── assets/
│   └── report-template.json
└── LICENSE
```

`SKILL.md` is the package entry point. Its YAML frontmatter provides the discoverable name and description. The Markdown
body tells an Agent when and how to use the package. Other files remain ordinary package resources; PowerContext does not
turn them into a workflow graph or execute them during import, Review, approval, search, or publication.

The package bytes are authoritative. Name, description, compatibility, and other parsed values shown in Skills Library
are validated caches derived from the exact `SKILL.md`, not an independent editable copy.

## Understand the four ways content enters the Library

### Discover an external Skill

Discovery records a local registration, locator, package fingerprint, Agent kind, host, and installation scope. The
external directory remains authoritative. No package bytes are copied into a managed Artifact, and disappearance or
fingerprint drift makes the registration unavailable.

### Import an external Skill exactly

The user selects one visible `external_skill_id` and exact fingerprint and chooses **Import**. PowerContext captures every
admissible file under the package root, verifies that the source fingerprint did not change during capture, stores a
canonical package snapshot, and creates a pending Candidate with the same package tree digest.

Exact import does not require an LLM and does not rewrite `SKILL.md`. Approval creates a new PowerContext-managed Skill
identity whose first Revision contains exactly the captured package. The external package and the imported managed Skill
are now independent authorities connected by lineage.

### Fork an external Skill

**Fork** first stores the same exact external snapshot as immutable Source evidence. A person or configured generation
model then proposes a different complete package. Review shows the original and proposed file trees and their diff.
Approval creates only the proposed managed Revision; the original snapshot remains readable as evidence.

### Author or generate a managed Skill

A person may upload a complete package, or a configured generation model may propose one from exact SourceRef and
ArtifactRef evidence. Generation produces package content outside the approval transaction. PowerContext validates and
stores it before creating a pending Candidate. A model cannot approve the Candidate, allocate final Artifact identity,
publish the package, or gain script authority.

## Review the package that will be published

The Skill Review detail presents:

- standard metadata and exact package digest;
- a bounded file tree with path, size, media type, content digest, and executable status;
- rendered `SKILL.md` as inert text or sanitized Markdown;
- text previews for bounded UTF-8 files;
- metadata-only rows for binary assets;
- a file diff for a successor Revision or fork;
- static validation, provenance, license, dependency, secret-scan, and risk findings;
- known target compatibility without executing package scripts.

Review actions remain Candidate actions. Revising a Candidate creates a complete replacement Candidate version. Approval
commits one immutable package Revision. Editing an approved Skill always creates a successor Candidate; it never reopens
or mutates the approved Revision.

## Browse current Skills without loading every package

Skills Library searches a rebuildable projection, not ZIP bytes. For an active managed Skill, PowerContext indexes:

- name and description;
- the bounded `SKILL.md` body;
- standard compatibility and metadata values;
- package paths;
- bounded text from `references/*.md` and `references/*.txt`.

The primary index records script and asset paths but does not mix complete script source or binary contents into the
default semantic text. Selecting a result resolves the exact ArtifactRef and package digest before the UI reads package
details.

Pending and rejected Candidates never enter Library search. Historical Revisions remain exactly readable but do not
enter the default current-head index.

## Keep lifecycle separate from Revision

Every managed Skill has a governance state independent from its immutable package Revisions:

| State | Library behavior | Publication behavior |
| --- | --- | --- |
| `active` | Included in normal search | May be published or updated explicitly |
| `deprecated` | Visible with replacement guidance; excluded by default recommendation | Existing binding remains; new publication requires explicit override |
| `retired` | Hidden from normal search; exact reads remain | New publication and update are blocked; safe unpublication remains available |

Changing lifecycle state does not create or modify package bytes. A deprecation may point to a replacement managed Skill.
Usage counts and similarity never change lifecycle automatically.

## Publish the same package to Codex and Claude Code

An Agent target identifies a configured Agent kind, host, installation scope, package root, and whether managed
publication is allowed. The target adapter validates the standard package and destination rules, materializes the exact
approved package into a staging directory, verifies the complete tree digest, and atomically moves it into place.

Codex and Claude Code receive the same package bytes. Their adapters may reject an incompatible name, format, or target,
but they do not rewrite frontmatter, remove scripts, or add a manifest inside the package. PowerContext stores publication
ownership and observed digests outside the standard package.

Publication does not execute a script. Unpublication removes a package only when its exact Artifact identity and tree
digest still match the recorded managed binding. A modified or foreign directory reports `drifted` or `conflict` and is
left untouched.

## Distribute to a remote host through Agent-side pull

Cross-host distribution does not turn PowerContext Server into a remote file manager. On first use, an administrator
gives the machine a recognizable name and creates a one-time enrollment code for a scope, Agent kind, and project in the
Dashboard or CLI. The user installs or enables the PowerContext Plugin/Integration on the remote host, selects the local
project, and submits that code. The Dashboard uses the readable name as the primary identity and keeps the stable
`target_id` in technical details; after enrollment it also shows the Receiver-reported hostname and workspace name.
Enrollment uploads no remote absolute path.

The user then selects that target and an exact Skill Revision in the Dashboard. **Publish** changes only the target's
desired state:

```text
Dashboard / CLI
  -> Server stores the target's desired Revision, tree digest, and generation
  -> remote Receiver requests reconciliation from a resident watch, Agent preflight, or explicit sync
  -> Receiver downloads the exact canonical package, verifies it, stages it locally, and installs it atomically
  -> Receiver reports the observed Revision, tree digest, generation, and result
  -> Dashboard shows current only when the receipt matches
```

The Codex or Claude Code PowerContext Plugin/Integration carries the Receiver. Its only responsibilities are package
synchronization and result reporting; it is not another PowerContext Server. The Plugin is the bootstrap and managed
Skills are dynamic data, so publishing a Skill does not require reinstalling the Plugin. Without an installed and enabled
Receiver, the Server can show only `pending` or `offline`, never successful delivery.

The Receiver's Agent adapter resolves the installation root on the remote host. A project-scoped Codex target uses
`.agents/skills/<name>/`; a project-scoped Claude Code target uses `.claude/skills/<name>/`. Neither the browser nor the
Server submits or interprets a remote absolute path. Both adapters install the same approved package bytes and only
validate their own naming, format, installation-scope, and environment constraints.

The first remote slice supports only project-scoped Codex and Claude Code targets, explicit Publish/Update/Unpublish,
a resident watch carried by a Linux systemd user service, and manual preflight/sync. An offline target is not a failed
delivery: its next reconciliation converges to the latest desired state. WebSocket/SSE wake-up, fleet policy, canary
rollout, automatic publication, and dependency installation are outside the first remote slice.

## Describe environment needs without granting them

Portable Skills should prefer one cross-platform implementation. A package that needs variants may include them under
`scripts/`. Standard `compatibility` text remains readable by people. A PowerContext-managed package may additionally
include the optional namespaced file `powercontext.runtime.yaml`:

```yaml
schema: powercontext.skill-runtime.v1
variants:
  - id: python
    entrypoint: scripts/verify.py
    interpreter: python
    requirements:
      operating_systems: [linux, darwin, windows]
      commands:
        python: ">=3.11"
      network: none
      writable_roots: [workspace]
  - id: windows-powershell
    entrypoint: scripts/windows/prepare.ps1
    interpreter: pwsh
    requirements:
      operating_systems: [windows]
      commands:
        pwsh: ">=7"
      network: required
```

This optional extension is part of the package digest. Consumers that do not understand it can ignore it and continue
to use `SKILL.md`. Exact import never inserts or changes this file. Adding it to an external package requires a fork.

An Agent environment profile reports observed operating system, architecture, command versions, network policy,
writable roots, dependency-install policy, and environment variable names. It never stores secret values. PowerContext
compares the exact package requirements with the target profile and reports `compatible`, `incompatible`, `unknown`, or
`manual_review_required` with reasons.

Requirements express needs. The environment and a later execution request control grants. A package declaring
`network: required` does not receive network access by being approved or published.

## Use outcomes to propose improvement

An Agent integration may record a bounded usage observation only for states it can actually observe:

```yaml
skill: artifact:skill/skill_release_check@2
package_digest: sha256:1234...
target_id: codex-project
selected: true
invoked: true
validation: passed
outcome: success
task_source: source:task-outcome/task_456
```

If the integration knows that a Skill was selected but cannot prove a script or instruction was used, `invoked` remains
`unknown`. Publication is not invocation, and invocation is not task success.

Usage observations are immutable Source evidence. They may update bounded aggregates and may seed a successor Candidate
against an exact Skill Revision. They never mutate content, approve a Candidate, increase permissions, retire a Skill,
or prove usefulness from a count alone.

# Reference-level explanation

## Scope and relationship to existing RFCs

This RFC defines:

- a standard package format for PowerContext-managed Skills;
- complete content-addressed package capture and database storage;
- exact external import and semantic fork behavior;
- package-level Review and migration from instruction-only managed Skills;
- current-head Library search, governance lifecycle, and bounded usage evidence;
- Codex and Claude Code environment assessment, publication, drift detection, and safe unpublication;
- target enrollment, desired-state reconciliation, delivery receipts, and trust boundaries for a later remote
  Agent-side pull extension;
- public package read semantics and implementation acceptance criteria.

This RFC refines RFC 0051's instruction-only managed Skill content and RFC 1304's two-file managed projection. It does
not change Experience content, general Candidate identity, Candidate CAS, Review terminal transitions, or Artifact
lineage semantics.

This RFC does not define:

- a general workflow, DAG, Routine, or Procedure runtime;
- automatic execution, dependency installation, secret resolution, or sandbox grants;
- SSH, Server-side writes to a remote filesystem, or browser-selected arbitrary remote paths;
- a resident fleet orchestrator, immediate push channel, automatic publication, or generic device management;
- organization-wide RBAC, reviewer identity, package signing, or marketplace billing;
- automatic semantic merge, automatic publication, automatic retirement, or unbounded background generation;
- generic binary extraction, OCR, malware verdicts, or complete code search.

## Standards baseline

A managed package conforms to the common Agent Skills package baseline:

- the package root contains a UTF-8 `SKILL.md`;
- YAML frontmatter contains required `name` and `description` string values;
- `name` is 1 through 64 lowercase letters, digits, or single hyphens, does not begin or end with a hyphen, contains no
  consecutive hyphens, and matches the package directory name;
- `description` is non-empty and at most 1,024 characters;
- optional standard fields such as `license`, `compatibility`, `metadata`, and `allowed-tools` are preserved;
- `scripts/`, `references/`, `assets/`, licenses, templates, and other bounded package files are preserved;
- unknown but syntactically valid frontmatter fields remain part of the exact package and are not rewritten.

PowerContext treats `allowed-tools` as untrusted package content. It may inform display or compatibility, but it is not a
tool grant and cannot bypass an Agent's policy.

The common baseline deliberately uses constraints accepted by both configured Agent adapters. A target adapter may
report a stricter incompatibility, but it cannot broaden the approved package contract by rewriting content.

## Skill package content model

New managed Skill Revisions use a discriminated content model:

```yaml
schema: powercontext.skill-package.v2
format: agent-skills
entrypoint: SKILL.md
package:
  tree_digest: sha256:...
  archive_digest: sha256:...
  file_count: 7
  uncompressed_size: 18234
  archive_size: 9541
metadata:
  name: release-check
  description: Verify a release candidate before publication.
  license: Apache-2.0
  compatibility: Python 3.11 or newer.
```

`package` identifies the authoritative package snapshot. `metadata` is a deterministic parsed cache used for validation,
listing, and search. On every write and package read, cached metadata must match `SKILL.md`; mismatch is an integrity
error. The cache cannot be edited independently.

Review reports, compatibility assessments, lifecycle state, publication bindings, and usage aggregates are not fields
inside `SkillPackageContent`. They have different authorities and change rates.

## Canonical package capture

Package identity represents content, not a filesystem image. Capture preserves:

- every admissible regular file under the selected package root;
- normalized POSIX relative path;
- exact file bytes;
- regular-file mode reduced to non-executable `0644` or executable `0755`.

Capture does not preserve modification time, user/group IDs, ownership, extended attributes, access-control lists, or
empty directories. Those values vary by host and are not part of Agent Skill content.

The tree digest is computed over a domain-separated canonical stream of sorted entries:

```text
format version
relative path length + relative path
normalized mode
file length
file sha256
```

PowerContext then creates a deterministic ZIP with sorted entries, fixed timestamps, normalized modes, no host-specific
extra fields, and a fixed compression policy. `tree_digest` is the content identity; `archive_digest` verifies the stored
and distributed ZIP. Semantically identical input ZIP files converge on one tree digest even when their original order
or compression differs.

Initial bounds retain the current local Registry scale:

| Bound | Value |
| --- | --- |
| Regular files | 256 |
| Total uncompressed bytes | 4 MiB |
| Canonical ZIP bytes | 5 MiB |
| `SKILL.md` bytes | 128 KiB |
| Path bytes after UTF-8 encoding | 512 |

The importer rejects rather than silently excludes:

- absolute paths, `..`, NUL, invalid UTF-8 paths, and paths outside the package root;
- symlinks, hard-link aliases, sockets, devices, FIFOs, and other special files;
- case-folding or Unicode-normalization path collisions;
- duplicate ZIP members;
- unsupported encryption or decompression bounds;
- packages exceeding any bound;
- a missing, non-UTF-8, malformed, or standard-incompatible `SKILL.md`;
- files blocked by configured secret or package policy.

If `.env`, `.git`, `node_modules`, or another path is forbidden, the error identifies the path. Exact import never
silently drops it and calls the result complete.

For a live external directory, capture writes every file into an isolated staging snapshot, computes the staged digest,
and resolves the external registration again. If the source fingerprint changed, capture fails with a typed conflict and
persists no Candidate. The staged bytes, not a later read of the live directory, become the package snapshot.

## Package persistence

The first implementation adds an immutable content-addressed table:

```text
pc_skill_packages
  scope_id
  tree_digest
  archive_digest
  archive_bytes
  manifest
  file_count
  uncompressed_size
  archive_size
  created_at

PRIMARY KEY (scope_id, tree_digest)
```

`archive_bytes` uses SQLAlchemy `LargeBinary`; SQLite stores a BLOB and the MySQL/OceanBase variant uses `MEDIUMBLOB`.
`manifest` is canonical JSON containing path, digest, size, media type, and normalized mode for each entry. No index
includes `archive_bytes` or `manifest` content.

`pc_artifacts.content`, Candidate proposal content, and captured external snapshot Sources store only the bounded package
reference. Package insertion and the first owning Source or Candidate write occur in one database transaction. Reusing
the same `(scope_id, tree_digest)` validates existing archive and manifest digests before returning the existing row.

Across the entire RFC, the only new business tables are `pc_skill_packages` and `pc_skill_publications`. Lifecycle uses
the existing Artifact Head, search uses a generic rebuildable projection, and usage evidence uses the existing Source
store.

Approved Artifact Revisions and retained Candidate or Source evidence keep their packages reachable. The first
implementation performs no automatic package garbage collection. A later collector may delete only packages with no
reachable Artifact, Candidate, or Source reference after a documented retention period.

## External reference, import, fork, and update

The operations have distinct authority semantics:

| Operation | Package authority | Copy | LLM required |
| --- | --- | --- | --- |
| Discover/reference | External local package | No | No |
| Exact import | New managed Artifact after approval | Exact canonical snapshot | No |
| Fork | New managed Artifact after approval | Exact source snapshot plus proposed replacement | Only for model-assisted semantic change |
| External update | External package until a new explicit import/fork | New exact snapshot | No for exact import; optional for fork |

An exact import Candidate's proposed `tree_digest` must equal the captured external snapshot digest. Candidate revision may
change review annotations but cannot change package content and still remain an exact import. Editing any file changes the
operation to a fork and creates a new proposed digest.

When a previously imported upstream package changes, Registry shows the new external fingerprint beside the import
provenance. PowerContext does not update the managed Skill automatically. The user may import it as a new managed Skill,
fork it, or propose a successor Revision targeting the current managed ArtifactRef.

## Validation and risk assessment

Validation has three layers:

1. **Package validation**: path safety, bounds, canonicalization, standard metadata, digests, and media detection.
2. **Static governance validation**: secret patterns, licenses, executable files, dependency manifests, runtime declarations,
   network/secrets/write requirements, and suspicious binary inventory.
3. **Target compatibility**: Agent format, package name, environment profile, and optional runtime variants.

None of these layers executes package scripts. A scanner finding is evidence for Review, not proof that a package is safe
or malicious. The Review UI reports scanner version and incomplete coverage where applicable.

A deterministic risk level helps triage without granting authority:

| Risk | Minimum trigger |
| --- | --- |
| `instruction_only` | `SKILL.md` and inert text/resources only |
| `local_script` | Any executable or script file |
| `workspace_write` | Declared workspace write requirement |
| `network` | Declared network requirement or network-oriented dependency |
| `secrets` | Declared secret/environment requirement |
| `privileged` | System path, process, container, or other elevated requirement |

Risk may require stronger Review or publication confirmation under deployment policy. It never authorizes the capability
that caused the level.

## Candidate and approval transaction

`SkillPackageContent` remains the Family proposal type, so generic Candidate storage and CAS continue to work. Candidate
detail resolves its package reference through the Skill Package Store.

Approval performs one transaction:

1. lock the expected pending Candidate head;
2. resolve and verify the exact package reference;
3. repeat required deterministic validation;
4. validate scope and direct SourceRef/ArtifactRef lineage;
5. create or revise the `skill` Artifact with immutable package content;
6. create the initial governance row for a new Skill, or preserve the existing lifecycle for a successor Revision;
7. update the current-head search projection;
8. commit the Candidate terminal result and Artifact Revision together.

A stale Candidate, target Artifact head, package mismatch, or validation version conflict returns `409` or a typed
validation failure. Approval never fetches remote content and never substitutes another package digest.

## Migration from instruction-only managed Skills

Existing approved Revisions remain readable through the current instruction-core content model. They are not rewritten in
place and retain their historical publication semantics.

The implementation supports a discriminated union:

```text
powercontext.skill-instruction.v1  -> existing name/description/instructions/validation
powercontext.skill-package.v2      -> standard package reference and parsed metadata
```

Creating a successor from a v1 Skill first renders the current deterministic `SKILL.md`, canonicalizes it as a one-file
v2 package, and presents that complete package as the starting Candidate. Approval creates the next Artifact Revision as
v2. This conversion is explicit and reviewable; reading an old Revision never causes migration.

New exact imports and new package uploads use v2. Existing semantic generation may initially produce a one-file standard
package, then add scripts or references only when exact evidence and Review justify them.

## Search projection and Skills Library

The ZIP BLOB never participates directly in search. `skill_searchable_text(package)` deterministically extracts bounded
text from the exact package:

```text
name
description
compatibility and metadata values
SKILL.md body
sorted package paths
bounded UTF-8 text from references/*.md and references/*.txt
```

The current managed head writes this text to `pc_artifact_heads.searchable_text`. SQLite replaces the rebuildable
Experience-only FTS5 projection with a generic `pc_artifact_fts` projection keyed by scope, Family, Artifact ID, and
Revision. This is a rebuildable replacement, not an additional Skill table. OceanBase continues to use its full-text
index on the generic head field. Both backends filter `family = 'skill'` and lifecycle state when searching Skills.
Rebuilding the projection resolves exact package references and verifies package digests before extraction.

The default Skill search does not return historical Revisions, pending or rejected Candidates, deprecated Skills unless
explicitly requested, or retired Skills. The projection does not contain full script source or arbitrary binary
extraction. Later code search or vector search uses a separate channel with path and content-digest provenance.

Skills Library presents a unified read model while preserving authority:

```text
managed current heads + governance + publication + usage projection
UNION
visible external registrations + local availability
```

Every row exposes `authority = managed | external`. Search never turns an external registration into a managed Artifact
or treats a managed package as still controlled by its upstream source.

## Managed lifecycle and working-set governance

Lifecycle state is mutable governance over one logical managed Skill and is not stored inside the package. It extends
the existing authoritative Head row instead of introducing `pc_skill_governance`:

```text
pc_artifact_heads
  scope_id
  family
  artifact_id
  revision
  searchable_text
  lifecycle_state        active | deprecated | retired
  replacement_artifact_id nullable
  governance_generation

PRIMARY KEY (scope_id, family, artifact_id)
```

Existing rows migrate to `active` with governance generation zero. Lifecycle updates require `family = 'skill'` and use
expected `governance_generation` CAS without changing the immutable Artifact Revision or the Head's `revision` pointer.
`replacement_artifact_id`, when present, identifies another in-scope managed Skill Head. Lifecycle transitions are
explicit:

```text
active <-> deprecated
active or deprecated -> retired
retired -> no automatic transition
```

Retirement is irreversible in this RFC. A mistaken retirement can fork or create a new logical Skill while the retired
history remains auditable. Deprecation may name one in-scope replacement and can be reversed explicitly.

Per-scope and per-target policy may bound pending Candidates, package bytes, active searchable heads, and published
packages. Exceeding a budget blocks the new operation with a typed error; it never evicts or retires an existing Skill.

## Agent targets and environment compatibility

`AgentSkillTarget` remains the configured publication boundary and gains an environment profile or provider capable of
observing one:

The Server uses one workspace as the local filesystem boundary. Without an explicit
`POWERCONTEXT_SERVER_EXTERNAL_SKILLS` value, the workspace defaults to the Server startup directory and produces two
writable project targets: `codex-project -> <workspace>/.agents/skills` and
`claude-project -> <workspace>/.claude/skills`. A missing directory means only that no external package exists yet; the
directory is created after the user confirms the first local installation. Service managers and containers pin the
workspace with `POWERCONTEXT_SERVER_WORKSPACE`. An explicit `POWERCONTEXT_SERVER_EXTERNAL_SKILLS` value replaces the
automatic targets and remains the advanced configuration for custom paths, user-level targets, environment profiles,
or disabling local discovery. The Dashboard never accepts a user-supplied local path.

```yaml
target_id: codex-project
agent_kind: codex
host_id: host-123
installation_scope: project
path: /workspace/.agents/skills
allow_managed_publish: true
environment:
  operating_system: linux
  architecture: x86_64
  commands:
    python: 3.12.4
    bash: 5.2.26
  network_policy: disabled
  writable_roots: [workspace]
  dependency_install_policy: denied
  environment_names: [CI]
```

Secret values never enter the profile. An observed profile has a deterministic fingerprint and timestamp. Compatibility
is keyed by exact Artifact Revision, package tree digest, environment fingerprint, and adapter version:

```text
compatible
incompatible(reason...)
unknown(reason...)
manual_review_required(reason...)
```

Compatibility is a rebuildable assessment, not an Artifact. Environment change invalidates the assessment without
changing the Skill Revision. Known Agent-format incompatibility blocks publication. Unknown runtime compatibility may be
published after the existing explicit confirmation because publication is not execution, but the UI must retain the
warning and cannot claim that scripts will run.

## Publication, distribution, and unpublication

The implementation supports host-local configured targets plus credential-bound remote Agent-side pull. It never pushes
to an arbitrary browser path or writes a remote filesystem directly.

Package download resolves an authorized exact ArtifactRef and returns a bounded JSON envelope containing the canonical
ZIP bytes:

```text
package: {tree_digest, archive_digest, file_count, uncompressed_size, archive_size}
archive_base64: <canonical ZIP encoded as base64>
```

The caller verifies both digests after decoding. The envelope keeps the generated JSON client contract consistent while
preserving byte-exact distribution; the Server never returns a mutable filesystem path.

Publication desired state and the latest exact observation share one binding row:

```text
pc_skill_publications
  scope_id
  target_id
  artifact_id
  desired_state
  desired_revision
  desired_tree_digest
  observed_revision nullable
  observed_tree_digest nullable
  observed_generation nullable
  destination nullable
  state
  selected_runtime_variant nullable
  environment_fingerprint nullable
  last_error_code nullable
  observed_at nullable
  generation
  updated_at

PRIMARY KEY (scope_id, target_id, artifact_id)
```

Publication stages the canonical package on the target filesystem, safely extracts it, recomputes the tree digest, and
atomically renames it. The target package contains only approved package files. The existing `powercontext.json` ownership
file is removed from the published package; ownership is represented by `pc_skill_publications` and verified against the
observed destination tree digest.

Observable publication state remains separate from runtime compatibility and external discovery:

```text
unpublished | pending | current | update_available | delivery_failed | conflict | drifted | incompatible
```

Safe update or unpublication requires expected publication `generation`, exact recorded Artifact identity, destination,
and observed tree digest. If local content changed, PowerContext reports drift and leaves it untouched. Unpublication
removes only the exact intact managed package and its binding; it never deletes the approved Artifact or package history.

## Remote Agent-side pull and desired-state convergence

This section specifies the implemented sixth-slice contract. Remote delivery reuses the `pc_skill_publications`
desired/observed model, but the
target-local Receiver, rather than the Server-local Publisher, produces the observation.

### Target enrollment and local path ownership

A remote Receiver uses a one-time enrollment code to register a stable `target_id`. Registration binds at least:

- an opaque host/installation identity, `agent_kind`, and project installation scope;
- the permitted `scope_id` and Server origin;
- the target-local adapter version, environment fingerprint, and last-seen time;
- an independent target credential subject whose secret value lives only in the remote operating-system secret store or
  equivalent secure storage.

The sixth slice adds a dedicated target registry instead of overloading External Skill Registration:

```text
pc_agent_skill_targets
  scope_id
  target_id
  display_name
  agent_kind
  installation_scope
  delivery_mode
  installation_id nullable
  state
  enrollment_token_digest nullable
  enrollment_expires_at nullable
  credential_subject nullable
  credential_verifier nullable
  receiver_version nullable
  environment_fingerprint nullable
  machine_hostname nullable
  workspace_name nullable
  last_seen_at nullable
  generation
  created_at
  updated_at

PRIMARY KEY (scope_id, target_id)
UNIQUE (scope_id, agent_kind, installation_scope, installation_id)
UNIQUE (enrollment_token_digest)
UNIQUE (credential_subject)
UNIQUE (credential_verifier)
```

The administrator supplies `display_name`; it may be renamed with target-generation CAS without changing credentials or
publication bindings. The Server generates a stable `target_id` for API, audit, and diagnostics. The Receiver generates
an opaque `installation_id` for its local Agent/project installation; it is not a filesystem path. At enrollment the
Receiver also reports `machine_hostname` and the workspace basename as `workspace_name`, never an absolute path. The
Dashboard can disambiguate and search targets by display name, hostname, workspace name, or technical ID. The first
remote slice allows only `delivery_mode=agent_pull` and the target
states `pending | active | revoked`:

- target creation persists only a digest and expiry for a high-entropy one-time enrollment code;
- enrollment validates pending state, expiry, token digest, and target generation in one transaction, binds the unique
  installation and credential subject/verifier, clears the enrollment token, and activates the target;
- display-name changes use the same target-generation CAS without changing the credential, `target_id`, or publication identity;
- the plaintext target credential exists only in the Receiver's operating-system secret store or owner-only credential
  file; the Server stores only its verifier;
- `last_seen_at` derives an offline display state and does not become a durable target state;
- enrollment and revocation use target `generation` CAS; revocation clears the usable verifier and rejects future
  enrollment, reconcile, download, and receipt calls without deleting historical identity or publications.

The Server stores only a logical installation scope. It neither stores nor accepts a browser-provided remote absolute
path. The Receiver resolves the package root from its locally enrolled workspace and rejects a Skill name or archive
path that escapes that root. One credential represents only its bound `target_id`; a reconciliation request cannot use
it to select another target.

A target row may exist before any Skill is published. The remote slice does not migrate existing host-local path
configuration into this table and does not reuse `pc_external_skill_registrations`: an external registration is an
observation of a package, not an authority for a remote installation or credential. Every `agent_pull` publication must
resolve to an active target in the same scope. Revocation does not cascade-delete publications or package history; it
only prevents remote authentication and makes the target unable to converge.

### Publication schema extension

The sixth slice migrates `pc_skill_publications` with these additions and changes:

```text
desired_state          # published | unpublished
observed_generation nullable
destination nullable   # required for host-local; null for agent_pull
last_error_code nullable
observed_at nullable
```

The existing `generation` remains the CAS generation of Server-owned desired state. `observed_generation` is the latest
generation processed by a valid receipt; an older receipt cannot update observed fields. Remote unpublication changes
`desired_state` to `unpublished`. The last desired Revision and digest remain as intent history, but are not deletion
authority. Safe deletion depends on the credential-bound ownership checkpoint reported by the Receiver and verified
again locally. A successful unpublication receipt clears the observed Revision and digest and sets the state to
`unpublished`.

`destination` remains required for host-local publication and must be null for `agent_pull`, because the Receiver resolves
the path locally. Remote publication uses the complete state set:

```text
unpublished | pending | current | update_available | delivery_failed | conflict | drifted | incompatible
```

`pending` means the desired generation has no matching receipt. `delivery_failed` carries a bounded
`last_error_code`. `offline` is derived from the active target's `last_seen_at`; it is not persisted as a publication
state.

SQLite and OceanBase migrations apply the same deterministic backfill:

- existing `state=unpublished` rows receive `desired_state=unpublished`; all other rows receive
  `desired_state=published`;
- existing rows receive `observed_generation=generation` and `observed_at=updated_at`;
- host-local `destination` values remain unchanged, while only new `agent_pull` rows use null;
- existing rows receive `last_error_code=null`;
- after backfill, `desired_state` is non-null and restricted to `published | unpublished`.

The first remote slice does not add a `pc_skill_delivery_receipts` table. After verifying
`publication.generation == receipt.generation`, the Server updates the latest observed fields on the
`(scope_id, target_id, artifact_id)` row. A success for the same generation may replace a failure; a failure cannot
replace an existing success; an identical receipt is a no-op; and an old generation never updates current state. If a
deployment later needs complete receipt audit history, it should use the existing Source/Event Store, never a second
authority for current publication state.

Outside the standard package, the Receiver maintains a credential-bound, integrity-protected ownership checkpoint. It
contains at least the target, ArtifactRef, tree digest, applied generation, and state for each managed artifact. A
bounded pending-action journal recovers a crash between package rename and checkpoint update: if the final directory
matches the authorized action, the Receiver completes the checkpoint and retries the receipt; if it still matches the
old checkpoint, it discards staging; otherwise it reports `conflict` instead of guessing ownership.

### Reconcile desired state instead of delivering a one-shot job

A remote publication is desired state:

```text
Server authority                         Remote target observation
desired_state                            observed state/result
desired_revision                         observed_revision nullable
desired_tree_digest                      observed_tree_digest nullable
generation                               observed_generation nullable
delivery_mode = agent_pull               bounded error code
```

Dashboard Publish, Update, and Unpublish operations only CAS-update desired state and `generation`. The Receiver submits
its local ownership checkpoint and actual directory tree digest:

```yaml
target_id: codex-project-7f31
last_processed_generation: 11
observed:
  - artifact_ref: artifact:skill/skill_release_check@1
    tree_digest: sha256:abcd...
    applied_generation: 9
```

The Server authenticates the target credential and verifies that the checkpoint ArtifactRef and tree digest name an
exact approved package for the same scope and artifact binding. The observation may be a local precondition for the
returned action, but only a successful receipt updates authoritative observed fields. Install and unpublish use
distinct action shapes:

```yaml
# install
generation: 12
action:
  operation: install
  desired:
    artifact_ref: artifact:skill/skill_release_check@2
    tree_digest: sha256:1234...

---
# unpublish
generation: 13
action:
  operation: unpublish
  artifact_id: skill_release_check
  expected_local:
    artifact_ref: artifact:skill/skill_release_check@2
    tree_digest: sha256:1234...
    applied_generation: 12
```

For unpublication, `expected_local` comes from the authenticated Receiver checkpoint and must match an exact approved
package for that artifact binding. It does not blindly reuse the Server's last observed or desired digest. This lets a
later reconciliation safely remove the exact package owned by the Receiver even when installation succeeded but its
receipt was lost.

The response contains no arbitrary destination path, shell command, dependency-install instruction, or unapproved
package body. The body still comes from the existing exact Download operation, and the credential may download only an
Artifact Revision referenced by its target's desired state. Reconciliation and receipts for the same
`(scope_id, target_id, generation, artifact_id)` are idempotent. A transient outage, repeated request, or Server restart
does not create duplicate directories or roll the target back to an older Revision.

Offline means only that a target has not converged. It neither changes desired state to failed nor discards an action.
`current` requires an exact receipt for the latest generation whose Revision and tree digest match the desired values;
until then the Dashboard shows `pending` or `offline`. An older-generation receipt cannot overwrite newer observed
state. A failed receipt writes the current `observed_generation`, preserves the last successful observed Revision and
digest, and sets `delivery_failed`. Reconciliation retries the same generation while desired state remains unsatisfied;
only new operator intent advances `generation`. A later success clears `last_error_code`.

### Receiver installation and receipt

For `install`, the Receiver performs these steps in order:

1. read the exact package envelope with the bound target credential;
2. verify the archive digest, safely extract into a bounded staging directory, and recompute the full tree digest;
3. run Agent-format and target-local compatibility checks without executing scripts or installing dependencies;
4. if the final directory and local checkpoint already match the desired Artifact and digest exactly, skip the rewrite
   and proceed to the receipt;
5. otherwise, only when the destination is absent or both it and the checkpoint match the old managed identity, persist
   the pending-action journal and atomically rename the complete package;
6. observe the final tree digest, atomically update the checkpoint, remove the journal, and submit the receipt. Any
   identity, digest, or checkpoint mismatch reports `drifted` or `conflict` without modifying the directory.

A receipt contains at least `target_id`, `generation`, operation, ArtifactRef, expected and observed tree digests,
result, environment fingerprint, Receiver version, and a bounded error code. It contains no package body, secret,
arbitrary command output, or absolute path. The Server validates the credential-bound target identity, generation, and
digests; an HTTP success alone is never installation success. The latest valid receipt updates
`pc_skill_publications` under the generation and success-precedence rules above; no separate receipt table is written.

For `unpublish`, the Receiver first verifies that the authenticated action, `expected_local`, local checkpoint, and
actual tree digest all match. It persists the journal, atomically renames the managed package into a Receiver-private
quarantine, records an absent checkpoint, submits the receipt, and only then removes the quarantine. User or third-party
changes produce `drifted` or `conflict`, and the content remains untouched. Receiver ownership, credentials,
pending-action journals, and receipt checkpoints stay outside the standard package.

### Codex and Claude Code triggers

| Agent | Receiver carrier in the first remote slice | Project installation root | Sync trigger |
| --- | --- | --- | --- |
| Codex | lightweight PowerContext Receiver | `.agents/skills/` | systemd user service running `remote-watch`, or Agent preflight/`remote-sync` |
| Claude Code | lightweight PowerContext Receiver | `.claude/skills/` | systemd user service running `remote-watch`, or Agent preflight/`remote-sync` |

The integration must verify the discovery boundary at which each Agent reads Skills. If SessionStart occurs after that
Agent's scan, a newly installed package may be declared discoverable only in the next session; `installed` must not be
reported as loaded in the current session. A deployment requiring first-session availability runs the same reconciliation
as a preflight before starting the Agent. `remote-watch` only schedules the same reconciliation; a later SSE/WebSocket
channel may also only wake the Receiver, while packages still arrive through the same authenticated pull transport.

## Usage observation and evolution

The owning Agent integration may capture a `skill-usage` Source at a bounded task or Agent completion boundary:

```yaml
skill_ref: artifact:skill/skill_release_check@2
package_digest: sha256:...
target_id: codex-project
selected: true
invoked: true | false | unknown
validation: passed | failed | unknown
outcome: success | failure | unknown
task_source: source:task-outcome/task_456
environment_fingerprint: sha256:...
```

The adapter must not infer `invoked=true` from retrieval, publication, prompt inclusion, or the model mentioning the Skill.
Unknown is a normal value. The Source records no prompt, secret, command arguments, or unbounded output by default.

A rebuildable daily projection may aggregate selected, invoked, validation-passed, success, and failure counts by exact
Skill Revision. Counts support Library health views but do not change search eligibility or lifecycle automatically.

A configured generation model may use caller-selected exact usage Sources to propose a successor Candidate. Exact import,
storage, Review, lifecycle changes, publication, unpublication, and usage recording remain non-LLM foundations.

## Public and Dashboard operations

The implementation exposes operations with these semantics; final OpenAPI names follow existing `/v1/skill/...` naming:

| Operation | Result |
| --- | --- |
| List Library | Managed heads and external registrations with authority-preserving filters |
| Get package manifest | Exact managed Revision metadata and file tree; no binary body |
| Download package | Canonical ZIP for an authorized exact managed Revision |
| Upload package proposal | Canonicalize a caller-provided ZIP and create a pending managed Candidate |
| Import external Skill | Exact import Candidate or fork Candidate from selected fingerprint |
| Update lifecycle | CAS transition for active, deprecated, or retired |
| Inspect publication | Publication and runtime compatibility for configured targets |
| Publish | Exact approved Revision to one configured target |
| Unpublish | Remove only an intact managed target package |
| Record usage | Capture bounded exact usage Source evidence |
| Create/enroll/revoke remote target | Create a one-time code, bind a credential, or revoke a target registration |
| Publish/unpublish remote desired state | CAS-declare an exact Revision or expected absence for a target |
| Reconcile remote target | Compare the target observation with the latest desired generation and return an idempotent action |
| Download remote package | Allow the target credential to download only the exact package referenced by its current generation |
| Record delivery receipt | Record the exact generation, ArtifactRef, digests, and installation result |

Every List Library item includes display provenance. A managed Skill without an external snapshot is `powercontext`, an
exact import is `external_import`, a fork is `external_fork`, and a registration that has not entered Review is presented
by the browser as `external`. The latter three expose the registration's `host_id`, `agent_kind`, `external_skill_id`,
`installation_scope`, and `locator`. For later managed Revisions, the Runtime checks direct SourceRefs first and then
traces upstream Skill ArtifactRefs to the first external snapshot, so a revision does not incorrectly erase its takeover
origin. This projection reuses persisted Source lineage and external snapshots, requiring no new table or historical-data
migration. Old data without an external snapshot claims only a PowerContext origin; it does not guess whether a human or
a model submitted it.

The browser submits `target_id`, the Agent kind selected when creating a target, exact ArtifactRef, expected Candidate
version or governance/publication generation, and explicit operation intent. It never submits an arbitrary destination
path, package digest substitution, or execution grant.
Remote operations are part of OpenAPI. Administrators use `remote-status`, `remote-target-create`,
`remote-target-rename`, `remote-publish`, `remote-unpublish`, and `remote-target-revoke` for the complete lifecycle.
Receivers use `remote-enroll`, `remote-watch`,
`remote-sync`, `remote-service-install`, and `remote-service-uninstall` to converge local directories and manage the Linux
user service. When an expected generation is omitted, the CLI reads current status before submitting the CAS mutation.
This does not bypass CAS: a concurrent update still returns a conflict, and automation may provide the generation explicitly.

The Skills Dashboard exposes a This Server / Remote machine choice in Delivery. Remote mode requires a readable machine
name at creation, searches by that name or Receiver-reported hostname/workspace (with technical IDs as a fallback), and
renames a target without changing its durable identity. It also supports Codex or Claude Code project targets, one-time
enrollment guidance, automatic target and delivery status refresh, exact Revision distribution, safe-removal requests,
and credential revocation. It shows the enrollment code only at creation and gives
copyable Receiver installation and `remote-enroll --install-service` commands. If the code was closed before it was saved,
the administrator revokes the pending target and adds it again. Remote mode refreshes silently every two seconds while a
delivery is pending and every ten seconds while stable; it stops while hidden or in local mode. The Dashboard presents
Publish and Unpublish as desired-state requests and shows installed or removed only after a matching Receiver receipt. It disables target revocation while any
publication is not confirmed unpublished, so credential revocation cannot permanently prevent safe cleanup.
The Server may configure the remotely reachable address once through `POWERCONTEXT_SERVER_PUBLIC_URL`. When it is unset,
the Dashboard uses its current HTTPS origin automatically, or its current HTTP origin after the explicit insecure switch
is enabled; otherwise the remote CLI's existing Server configuration provides the connection address. Adding a target
never asks the administrator to enter the address again.

HTTPS remains the default transport boundary. A first-phase internal PoC may explicitly enable direct cleartext HTTP by
setting `POWERCONTEXT_SERVER_ALLOW_INSECURE_HTTP=true` on the Server and passing `remote-enroll --allow-insecure-http` on
the target. Either side alone is insufficient: the Server continues to reject non-loopback HTTP Receiver requests when
its switch is off, while the CLI refuses the URL before sending the one-time enrollment code when its option is absent.
If the Server itself binds an unauthenticated listener to a non-loopback address, the operator must separately set
`POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true`; that setting acknowledges exposure of all Server routes
and is not implied by the Receiver-only transport exception.
The Dashboard accepts an advertised HTTP URL only while the Server switch is enabled, displays a persistent warning, and
adds the Receiver option to its copyable command. The Receiver stores the permission beside its credential in the
owner-only configuration file, so one-shot sync, watch mode, and the systemd user service share one transport policy.
This additive configuration field requires no database table or historical-data migration. It does not encrypt the
enrollment code, target credential, package, or Receipt, so it is limited to a protected private test network and must
not be treated as a production alternative to HTTPS.

### Remote distribution CLI flow

By default, the Server must expose a remotely reachable HTTPS URL. The explicit internal-HTTP PoC exception described
above is the only cleartext alternative. A target machine installs only the `powercontext[cli]` Receiver,
not a Server or database, and accepts no inbound connection from the Server. The administrator first creates a project
target:

```bash
powercontext --server-url https://powercontext.example.com \
  skill remote-target-create --scope-id project:demo --agent-kind codex --name "Hangzhou build machine"
```

The remote operator enters the one-time enrollment code from the target project. Omitting the command-line code uses a
no-echo prompt and stores the target credential in `.powercontext/remote-skill-target.json` with owner-only permissions:

```bash
cd /srv/project
powercontext --server-url https://powercontext.example.com \
  skill remote-enroll --workspace "$PWD" --install-service
```

For the explicit internal-HTTP PoC exception, the corresponding command is:

```bash
powercontext --server-url http://powercontext.internal.example:8765 \
  skill remote-enroll --workspace "$PWD" --install-service --allow-insecure-http
```

`--install-service` creates a target-scoped `systemd --user` unit and immediately runs `enable --now`. The unit references
the owner-only configuration file and never copies its credential. Existing enrollments can run
`powercontext skill remote-service-install`; `powercontext skill remote-service-uninstall` stops and removes the managed
unit. Environments without systemd can run `powercontext skill remote-watch` in their own process supervisor.

The administrator publishes an exact approved package Revision without manually discovering the initial or current
publication generation:

```bash
powercontext --server-url https://powercontext.example.com \
  skill remote-publish --scope-id project:demo --target-id codex-abc123 \
  --revision 2 release-check
```

The resident Receiver reconciles every five seconds by default. Codex receives `.agents/skills/`; Claude Code receives
`.claude/skills/`. A deployment that requires the first current session to discover a just-published Skill still runs an
explicit preflight before starting the Agent:

```bash
powercontext skill remote-sync
codex  # or claude
```

The administrator can inspect desired/observed status, request safe removal, or revoke the credential:

```bash
powercontext skill remote-status --scope-id project:demo --target-id codex-abc123
powercontext skill remote-unpublish --scope-id project:demo --target-id codex-abc123 release-check
powercontext skill remote-target-revoke --scope-id project:demo codex-abc123
```

`remote-publish` and `remote-unpublish` change only Server desired state. Only a later successful watch/sync Receipt makes
the publication `current` or `unpublished`. Dashboard auto-refresh reads only that durable state; it does not treat an HTTP
success as an installation or claim that the current Agent session rescanned its Skills.

## Security and trust boundary

Every package and every Candidate remains untrusted. PowerContext:

- parses ZIP and YAML with bounded safe parsers and no custom tags;
- renders package text inertly and never loads remote resources named by content;
- does not log package bodies, secrets, usage arguments, or arbitrary Source bodies;
- does not execute scripts during scan, import, indexing, Review, approval, publication, or compatibility assessment;
- does not install dependencies during publication;
- does not treat `allowed-tools`, compatibility text, runtime requirements, or risk level as permission;
- never exposes a package solely because the caller knows its digest;
- authorizes reads through scope and exact Source, Candidate, or Artifact reachability;
- verifies digests before every exact read, diff, download, and publication;
- requires HTTPS for every non-loopback remote connection by default; the internal-PoC escape hatch requires explicit
  Server and Receiver opt-in and keeps the cleartext risk visible;
- uses an independent credential per remote target, limited to reading its desired state, downloading the exact
  referenced Artifacts, and submitting its own receipts;
- binds each receipt to its Server-side target identity, generation, and digests and accepts no browser- or
  Receiver-selected arbitrary remote path;
- preserves restrictive browser Content Security Policy and safe rendering rules from RFC 1304.

`scope_id` remains a business partition, not an ACL. Deployments that need organizational authorization must enforce it
through Server authentication and policy; this RFC does not infer user permissions from scope names.

## Implementation slices

The implementation is organized as five independently dogfoodable local slices and one independently accepted remote
slice. Remote distribution does not change acceptance of the first five:

1. **Package foundation**: canonical package validation, `pc_skill_packages`, v1/v2 content union, exact reads, and
   SQLite/OceanBase round trips.
2. **Exact import and package Review**: complete external snapshots, non-LLM import, fork semantics, file tree, inert
   previews, digest-visible successor comparison, and approval transaction.
3. **Library and lifecycle**: generic SQLite/OceanBase Artifact FTS adapters, lifecycle columns and CAS on
   `pc_artifact_heads`, filters, and replacement guidance.
4. **Agent delivery**: environment profiles, compatibility assessment, `pc_skill_publications`, exact Codex/Claude Code
   publication, package download, drift detection, and safe unpublication.
5. **Observed evolution**: bounded usage Sources and explicitly triggered successor Candidates. Aggregated health views
   can be added later as rebuildable projections without changing usage evidence.
6. **Remote target reconciliation**: `pc_agent_skill_targets`, migration of remote fields in
   `pc_skill_publications`, Codex/Claude Code Receivers, one-time enrollment, per-target credentials, desired-state
   reconciliation, exact package pull, atomic installation, delivery receipts, offline convergence, and safe remote
   unpublication.

No slice introduces a PowerContext script runner. Each slice preserves exact reads and prior instruction-only Revisions.
Remote capability claims remain subject to the independent acceptance below. Installed, receipt-current, and discovered
in the Agent's current session remain three distinct facts.

## Acceptance

| Scenario | Passing condition |
| --- | --- |
| Standard package | A valid `SKILL.md` package with scripts, references, assets, license, and optional metadata round-trips exactly |
| Canonical identity | Equivalent directory and differently ordered ZIP inputs produce the same tree digest |
| Executable mode | A script's normalized executable bit survives capture, storage, download, and publication |
| Complete snapshot | Hidden and nested admissible files remain present; forbidden files cause named rejection rather than silent omission |
| Archive safety | Traversal, duplicate entries, symlinks, special files, collisions, malformed YAML, and decompression bounds are rejected |
| Mutable source | External content changing during capture produces conflict and no Candidate |
| Exact import | Import preserves the source tree digest, requires no LLM, and creates only a pending Candidate |
| Fork | Original exact package remains Source evidence and the proposed package has a separate digest and visible diff |
| Approval | Only the expected pending version commits one immutable package Artifact Revision and current search projection |
| Legacy read | Existing instruction-only Revisions remain exactly readable and are not migrated on access |
| Legacy successor | A successor from v1 presents an explicit one-file v2 package conversion before approval |
| SQLite package store | Maximum-sized canonical ZIP and manifest commit, read, and digest-check through SQLite |
| OceanBase package store | The same package round-trip uses `MEDIUMBLOB` and does not load ZIP bytes in list/search queries |
| Search | Generic Artifact FTS returns only active approved Skill heads by default; exact name and description queries return expected rows |
| External search | External availability remains local and does not become managed authority |
| Lifecycle | Head governance CAS controls deprecation and retirement, preserves all Revisions, and never auto-deletes or auto-publishes |
| Compatibility | The same package gets independent reasoned assessments for Codex and Claude Code environment profiles |
| No execution | Import, Review, indexing, approval, compatibility, publication, and unpublication never execute package scripts |
| Publication | Codex and Claude Code targets receive the same approved package tree without injected package files |
| Safe update | Only an intact, identity- and digest-matching managed destination can be replaced |
| Safe unpublication | Only an intact managed destination is removed; drift or foreign content remains untouched |
| Initial schema | The first five local slices add only `pc_skill_packages` and `pc_skill_publications`; the remote slice also adds `pc_agent_skill_targets` and migrates publication fields; SQLite FTS is rebuildable |
| Usage truth | Selected, invoked, validation, and outcome remain distinct; unknown observation is preserved |
| Evolution | Usage evidence can seed a pending successor against an exact Revision but cannot mutate or approve it |
| Scope | Package, Library, lifecycle, publication, usage, and download operations cannot cross caller scope |
| Browser trust | Candidate and package content remain inert in real Chromium, including malicious Markdown, SVG, and filenames |
| Packaging | Server templates and static assets for package Review and Library ship in the wheel |
| Local defaults | Without advanced target configuration, local Codex and Claude Code resolve `.agents/skills/` and `.claude/skills/` under the workspace; neither directory is created before the user confirms installation |

The implementation must run `make check`, `make test`, `make docs-test`, and `make contract-test` for API changes. It must
also exercise a real SQLite Server flow, an OceanBase package round trip, real Codex and Claude Code package discovery,
and a browser flow covering exact import, file inspection, approval, search, publication, drift, unpublication, both
locales, keyboard operation, and a narrow viewport.

### Remote-distribution slice acceptance

The implemented sixth slice must satisfy these conditions; local tests from the first five slices cannot replace them:

| Scenario | Passing condition |
| --- | --- |
| Enrollment | A one-time code creates only one credential-bound target; replay and cross-scope use are rejected |
| Remote schema | `pc_agent_skill_targets` is added and `pc_skill_publications` is migrated without adding a job queue or receipt-history table |
| Schema backfill | SQLite and OceanBase produce the same desired state, observed generation/time, destination, and error-field values for existing rows |
| Target uniqueness | One installation, enrollment token, or credential subject cannot bind multiple active targets; revoked credentials stop working |
| No full remote Server | The remote host installs only a Plugin/Integration Receiver, not PowerContext Server or its database |
| Agent roots | Codex and Claude Code adapters locally resolve `.agents/skills/` and `.claude/skills/`; the Server receives no absolute path |
| Exact delivery | The Receiver downloads the desired ArtifactRef's canonical package and verifies archive/tree digests before and after installation |
| Atomic install | Interruption, disk error, or verification failure leaves only removable staging, exposes no partial package, and preserves the intact old version |
| Offline convergence | After multiple updates while offline, the next reconciliation converges directly to the latest generation without replaying stale Revisions |
| Receipt truth | Only a receipt matching credential, target, generation, ArtifactRef, and digests can produce `current` |
| Idempotency | Repeated reconciliation, download, and receipt submission create no duplicate directories or bindings and cannot regress state |
| Lost receipt recovery | After installation succeeds and the receipt is lost, the Receiver uses its checkpoint to retry without rewriting or reporting a false conflict |
| Failed delivery retry | A failed receipt preserves the last successful observation and retries the same generation; only new intent advances generation |
| Safe remote update | A drifted target tree is not replaced and reports `drifted` or `conflict` |
| Safe remote unpublication | Only an intact identity/digest-matching managed package is removed; foreign content remains untouched |
| Transport isolation | Non-loopback plaintext HTTP is rejected by default and accepted only with Server plus Receiver opt-in; one target credential cannot read or acknowledge another target's state |
| Discovery boundary | Tests distinguish installed, discoverable in the current session, and discoverable in the next session without false success claims |
| No execution | Reconciliation, installation, and receipt handling execute no scripts, install no dependencies, and expand no Agent permissions |

# Drawbacks

- Full package governance adds ZIP parsing, BLOB persistence, file-level Review, and more failure states than an
  instruction-only record.
- Generic lifecycle columns broaden `pc_artifact_heads`, and SQLite must rebuild its Experience-only FTS projection as
  a Family-aware Artifact projection.
- Database BLOB storage is simple and transactional at the current bounds but is not the final answer for large packages
  or high-volume remote distribution.
- A common standard baseline may reject a package accepted by one Agent's more permissive parser.
- Static validation cannot prove that a script is safe or useful, while stronger sandbox execution is deliberately out
  of scope.
- Lifecycle, publication, compatibility, and usage are separate axes, increasing UI and API complexity.
- Remote desired/observed state, credential lifecycle, and eventual convergence add operational and failure states absent
  from local publication.
- Exact import may preserve redundant or low-quality files; the correct response is visible Review or fork, not silent
  normalization.
- Usage evidence will be incomplete until Agent integrations can distinguish actual invocation from retrieval or mention.

# Rationale and alternatives

| Alternative | Decision |
| --- | --- |
| Keep managed Skills instruction-only | Rejected; it cannot preserve or review normal Agent Skill packages |
| Store ZIP bytes directly inside generic Artifact JSON | Rejected; base64 inflates payloads and couples generic Artifact reads to package transfer |
| Store only a filesystem path | Rejected; paths are host-local, mutable, and cannot support immutable Review or distribution |
| Store one row per package file initially | Rejected; current 4 MiB packages can use one transactional canonical ZIP plus manifest with less schema and I/O complexity |
| Add a separate `pc_skill_governance` table | Rejected; lifecycle governs the current logical Artifact and fits the existing authoritative Head row with independent CAS |
| Keep publication ownership only on the local filesystem | Rejected; safe unpublication, target removal, multiple Server instances, and future remote delivery need a durable target binding |
| Use an object store immediately | Deferred; a `SkillPackageStore` abstraction keeps this path open without adding deployment dependencies now |
| Let exact import regenerate instructions with an LLM | Rejected; it loses package bytes and changes authority; model-assisted change is fork |
| Add PowerContext metadata inside every published package | Rejected; publication must preserve the approved standard package tree |
| Generate different approved packages for Codex and Claude Code | Rejected; target adapters report compatibility and location without creating unreviewed content variants |
| Auto-install dependencies during publication | Rejected; publication is not execution or environment mutation authority |
| Push from the Server through SSH, SCP, or a remote filesystem | Rejected; it expands Server privilege and network reachability and cannot safely handle offline targets, NAT, or local drift |
| Deliver remote packages through a one-shot job queue | Rejected; offline targets can lose or replay work, while desired-state reconciliation is naturally idempotent and converges to the latest state |
| Release a new Plugin for every published Skill | Rejected; the Plugin is stable bootstrap, while managed Skills update independently as exact package data |
| Synchronize before every user prompt | Rejected; it adds latency and noise; the resident watch stays outside the prompt path, while preflight only guarantees first-session discovery |
| Publish every active Library Skill | Rejected; Library inventory and Agent working set have different scale and intent |
| Auto-retire unused or low-success Skills | Rejected; observation coverage and attribution are incomplete, and counts cannot replace Review |
| Build a script runner in this RFC | Rejected; package governance and host policy can close a useful local loop without inventing another execution platform |

Not adopting a complete package model leaves external import lossy, keeps the Review surface disconnected from actual
Agent content, and makes scripts, assets, compatibility, and usage governance impossible to represent faithfully.

# Prior art

- The [Agent Skills specification](https://github.com/agentskills/agentskills/blob/main/docs/specification.mdx) defines a
  `SKILL.md` package with optional scripts, references, assets, and metadata. This RFC adopts that package as portable
  content while keeping PowerContext governance outside the standard authority boundary.
- The [OpenAI Skills API](https://developers.openai.com/api/reference/go/resources/skills) uses downloadable ZIP bundles
  and immutable Skill versions. This RFC similarly separates logical Skill identity, immutable content version, and
  package distribution.
- Skillsgate validates standard frontmatter, applies package-size limits, maps multiple Agent installation targets, and
  copies a directory package. PowerContext adopts its useful package/target separation but does not silently exclude
  files or treat installation as execution.
- RFC 0051 defines external versus managed content authority, exact local fingerprints, Candidate evolution, and the
  execution boundary. This RFC supplies the managed package format it deliberately deferred.
- RFC 1304 defines typed Review, explicit publication, safe update, and browser trust boundaries. This RFC extends those
  contracts from two generated files to the exact approved package and adds safe unpublication.
- Existing Memory and Experience indexes demonstrate authoritative rows plus rebuildable SQLite and OceanBase search
  projections. Skill search reuses that separation rather than indexing ZIP bytes.

# Unresolved questions

No unresolved question blocks the RFC. The implementation must confirm the documented canonical ZIP test vectors across
supported Python versions before publication.

The following decisions are intentionally outside this RFC:

- the organization-specific credential provider, short-lived token exchange, device attestation, rotation, and
  revocation implementation;
- object-store selection and package garbage-collection retention;
- organization-level owners, reviewer identity, RBAC, and two-person approval for privileged packages;
- package signatures, transparency logs, vulnerability databases, and marketplace trust levels;
- generic code search, embeddings, hybrid ranking, automatic recommendation, and just-in-time mounting;
- dependency environment creation, OCI execution, and a PowerContext-owned sandbox runner;
- whether another Artifact Family should represent reusable Procedure or Workflow semantics.

# Future possibilities

Natural extensions include:

- low-latency Receiver wake-up through SSE or WebSocket, plus fleet policy, canary rollout, and bulk target views, after
  resident Pull reconciliation is validated in real hosts;
- short-lived token exchange, automatic rotation, device attestation, or mTLS above the per-target credential contract;
- object-backed `SkillPackageStore` implementations while retaining database metadata and tree digests;
- signed package manifests and organization trust policies;
- path-level code and semantic search with exact package/chunk provenance;
- per-project enabled sets and temporary task-scoped mounting after measured retrieval quality;
- isolated dependency caches keyed by package, lock-file, runtime variant, platform, and environment fingerprint;
- a separately reviewed sandboxed `SkillRun` contract with read-only package mounts, explicit grants, resource limits, and
  bounded evidence;
- governance dashboards for unused, failing, drifted, incompatible, unowned, or upstream-outdated Skills.

These extensions must preserve the central contract: the approved package Revision is immutable content; environment,
publication, and execution authority remain explicit bindings outside it; and observed outcomes can propose change but
cannot silently rewrite governed history.
