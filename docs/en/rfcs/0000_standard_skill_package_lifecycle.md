- Proposal Name: `standard_skill_package_lifecycle`
- Start Date: 2026-08-21
- RFC PR: [oceanbase/powercontext#0000](https://github.com/oceanbase/powercontext/pull/0000)
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
  -> explicitly publish the exact Revision to an Agent target
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
- public package read semantics and implementation acceptance criteria.

This RFC refines RFC 0051's instruction-only managed Skill content and RFC 1304's two-file managed projection. It does
not change Experience content, general Candidate identity, Candidate CAS, Review terminal transitions, or Artifact
lineage semantics.

This RFC does not define:

- a general workflow, DAG, Routine, or Procedure runtime;
- automatic execution, dependency installation, secret resolution, or sandbox grants;
- remote cross-host push or pull agents;
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

The first implementation supports host-local configured targets and exact authenticated package download. It does not
push to an arbitrary browser path or remote host.

Package download resolves an authorized exact ArtifactRef and returns a bounded JSON envelope containing the canonical
ZIP bytes:

```text
package: {tree_digest, archive_digest, file_count, uncompressed_size, archive_size}
archive_base64: <canonical ZIP encoded as base64>
```

The caller verifies both digests after decoding. The envelope keeps the generated JSON client contract consistent while
preserving byte-exact distribution; the Server never returns a mutable filesystem path.

Publication state is stored in the second and only other new business table introduced by this RFC:

```text
pc_skill_publications
  scope_id
  target_id
  artifact_id
  desired_revision
  desired_tree_digest
  observed_revision nullable
  observed_tree_digest nullable
  destination
  state
  selected_runtime_variant nullable
  environment_fingerprint nullable
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
unpublished | current | update_available | conflict | drifted | incompatible
```

Safe update or unpublication requires expected publication `generation`, exact recorded Artifact identity, destination,
and observed tree digest. If local content changed, PowerContext reports drift and leaves it untouched. Unpublication
removes only the exact intact managed package and its binding; it never deletes the approved Artifact or package history.

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

The browser submits `target_id`, exact ArtifactRef, expected Candidate version or governance/publication generation, and
explicit operation intent. It never submits an arbitrary destination path, Agent kind, package digest substitution, or
execution grant.

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
- preserves restrictive browser Content Security Policy and safe rendering rules from RFC 1304.

`scope_id` remains a business partition, not an ACL. Deployments that need organizational authorization must enforce it
through Server authentication and policy; this RFC does not infer user permissions from scope names.

## Implementation slices

Implementation proceeds through five independently dogfoodable slices:

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

No slice introduces a PowerContext script runner. Each slice preserves exact reads and prior instruction-only Revisions.

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
| Schema | The feature adds only `pc_skill_packages` and `pc_skill_publications`; SQLite FTS is a rebuildable replacement |
| Usage truth | Selected, invoked, validation, and outcome remain distinct; unknown observation is preserved |
| Evolution | Usage evidence can seed a pending successor against an exact Revision but cannot mutate or approve it |
| Scope | Package, Library, lifecycle, publication, usage, and download operations cannot cross caller scope |
| Browser trust | Candidate and package content remain inert in real Chromium, including malicious Markdown, SVG, and filenames |
| Packaging | Server templates and static assets for package Review and Library ship in the wheel |

The implementation must run `make check`, `make test`, `make docs-test`, and `make contract-test` for API changes. It must
also exercise a real SQLite Server flow, an OceanBase package round trip, real Codex and Claude Code package discovery,
and a browser flow covering exact import, file inspection, approval, search, publication, drift, unpublication, both
locales, keyboard operation, and a narrow viewport.

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

- remote Agent distributor authentication and delivery receipts;
- object-store selection and package garbage-collection retention;
- organization-level owners, reviewer identity, RBAC, and two-person approval for privileged packages;
- package signatures, transparency logs, vulnerability databases, and marketplace trust levels;
- generic code search, embeddings, hybrid ranking, automatic recommendation, and just-in-time mounting;
- dependency environment creation, OCI execution, and a PowerContext-owned sandbox runner;
- whether another Artifact Family should represent reusable Procedure or Workflow semantics.

# Future possibilities

Natural extensions include:

- an Agent-side pull distributor using short-lived scoped package tokens and exact publication receipts;
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
