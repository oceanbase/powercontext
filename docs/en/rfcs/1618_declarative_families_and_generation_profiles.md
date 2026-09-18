- Proposal Name: `declarative_artifact_families_and_generation_profiles`
- Start Date: 2026-09-14
- RFC PR: [oceanbase/powercontext#1618](https://github.com/oceanbase/powercontext/pull/1618)

# Summary

This RFC defines governed extension points for two things that require source changes today: introducing "which class of derived artifact is produced", and changing "how content is generated".

**Artifact Families are declarative.** An administrator-installed extension package contains only a closed JSON manifest and a few JSON Schema documents, and no extension code runs at runtime. The manifest declares schema versions, Review policy, evolution mode, lineage discipline, and the default behavior for retrieval projection and context contribution. A family may hold several `schema_version`s in parallel, and a historical Revision is always validated and decoded under the version it recorded.

**Generation Profiles are Scope-owned versioned artifacts.** `generation-profile` is a built-in configuration Artifact family (unrelated to the user-profile family `family=profile` described outside this RFC; the name does not reuse `profile`), stored as immutable Revisions, declaring the target family, prompt identity, model catalog entry, bounded settings, output limits, the empty-result field, and the failure policy. It **declares no schema** — the output contract is resolved from the target family's current `schema_version`.

Combined, the generation pipeline is one fully generic path: evidence envelope in → catalog model out → validate against the target family schema → pending Candidate → human approval → immutable Revision. In S1 extension families only use the **review** lifecycle: artifacts can only be produced by approved candidates, and no generic direct-write path is provided yet.

Six invariants are not relaxed by extension: exact evidence, schema validation, Review policy, authorization, resource bounds, and Runtime ownership of context assembly.

# Motivation

An organization may want to change how a proposal is generated, or to produce a domain-specific result: runbooks, incident briefs, requirement decisions, support resolutions. The safe extension options today are narrow. A caller can supply Sources and use the built-in Memory, Experience, Skill, and Handoff behavior only. Family identity is already a free string at the domain-value level, but every execution path maintains its own closed set — artifact classes, authorization profiles, ID prefixes, processing bindings, contract enums — so any change to the set of families requires platform source changes, and "extend without forking" does not hold today.

# Guide-level explanation

## Five new concepts

**Extension Package.** A distribution unit that contains data only: one `powercontext.extension.json` at its root, plus JSON Schema documents inline or in the same directory. No executable code.

**Artifact Family extension.** A new type declared in the manifest: content schema and versions, Review policy, evolution mode, lineage discipline, and optional defaults for projection and context contribution.

**Generation Profile.** A Scope-owned, versionable configuration object that answers "how is this family's content generated": which prompt, which model from the catalog, how much output is allowed, timeout and retry bounds, how the evidence scope is narrowed, and how empty results and failures are handled. It does not define what the family is, and it cannot change the output type.

**Model Catalog.** The set of models and endpoints a deployment declares, with hard bounds on each entry. A Scope-level profile may only reference a catalog entry name and cannot carry its own URL. This constraint is also the mount point for egress policy.

**Generation Provenance.** Every Candidate and Revision produced by a profile records "which revision of which profile generated it", plus structured provenance (prompt reference, effective bounds, target schema version) and its derived digest for equality comparison. Provenance is not factual evidence, so it is stored separately from the evidence tuple.

## Example 1: add a domain family

An administrator installs an extension package and enables `runbook` in the server configuration. The manifest declares: `review_policy = "review"`; `cardinality = "collection"`; lineage requiring replacement candidates to carry an exact target and non-empty Source evidence; a content schema `acme.runbook.v1` constraining `{schema, title, summary, symptoms[], steps[], failure_handling[]}`, where `schema` is a `required + const` version marker; and the default behavior for retrieval projection and context contribution.

After an incident closes:

1. Generation takes the incident's Source evidence and approved Experience of the same kind, and calls the catalog model inside the platform adapter. The model input is the platform's fixed bounded evidence envelope; a family can only narrow which evidence participates.
2. The output is validated against `acme.runbook.v1` and enters the Draft → Candidate path with generation provenance attached. If `steps` is empty (the evidence supports no remediation step), the result is classified as empty by `noop_field`: no candidate is written, an explicit `no_op` is returned, and one content-free diagnostic is recorded.
3. At this point it is neither searchable nor injectable. Pending and rejected Candidates are fully isolated from Artifact search and PreparedContext.
4. A human Review approves it, committing an immutable Runbook Revision in the same database transaction, updating the retrieval projection, and marking the Candidate approved; if any step fails, everything rolls back.
5. Because the manifest declares projection, the Runbook enters retrieval; because it declares context contribution and the deployment has allowed that family, it may enter context as bounded, precisely cited, untrusted-history content, and it cannot inject raw system or developer instructions.

## Example 2: change how content is generated, not the type

An administrator has already loaded a `runbook` extension family (installation and manifest shape in Example 1), considers the auto-generated runbook steps too diffuse, and wants a different model and tighter output:

1. The deployment has declared two catalog entries, `small-reasoner` and `strong-reasoner`, with their bounds.
2. A Scope administrator writes revision 1 with `family=generation-profile` and `artifact_id=runbook.generate`, specifying `target_family="runbook"`, `prompt.ref="runbook.generate"` (the template id declared in the extension manifest), `model="small-reasoner"`, bounded settings, and `output_max_bytes`.
3. Every Runbook Candidate produced afterwards records that exact profile revision and its structured provenance; existing Revisions are unaffected.
4. To roll back, read the old revision's content and write it as a new, monotonically increasing revision.

The observed difference: lineage for candidates and committed Revisions gains one exact generation-provenance reference; evidence, schema, Review, and authorization paths are unchanged. Switching the prompt to another template id, the model to another catalog entry, or any bounded setting produces a new profile revision, so "what changed" is visible in lineage. Several profiles can also coexist for the same `target_family`, resolved by profile key: for example `runbook.fast` (small model, low latency) and `runbook.slow` (strong model, high quality), where naming a key at generation time yields a different model and budget.

## Author workflow

Extensions are pure data, so the author workflow involves neither platform internals nor writing code:

```text
powercontext extension init ./runbook --family runbook        # generate the manifest and a schema skeleton
powercontext extension validate ./runbook --powercontext 1.0.0  # offline validation
powercontext extension lock ./runbook                          # write powercontext.extension.lock.json
powercontext extension diff ./runbook@1 ./runbook@2 --against powercontext.extension.lock.json
```

A sample package is a pure-data directory, and `init` generates everything except the lock file:

```text
powercontext-runbook/
├── powercontext.extension.json        manifest: family declaration, prompt templates, compatibility range
├── powercontext.extension.lock.json   semantic fingerprint produced by lock; can be committed
├── schemas/runbook.v1.json            one content schema per version, $ref confined to the document
└── README.md
```

The package contains no code or executable entry point, no ID prefix, no model URL or credential, no profile, and no projection or rendering function — those belong to the platform, to deployment configuration, and to the Scope administrator respectively.

Because extensions contain no code, `validate` **does not execute any extension code**: it performs JSON parsing, schema checking, and reference resolution only. The platform version is a required offline axis, and omitting it fails. `validate`, readiness, and the discovery endpoint share one set of error codes and locations, and the `diff` exit code distinguishes backward-compatible from breaking changes.

## The life of an extension family

```text
install package → administrator enables → explicit generation or (later slice) automatic trigger → pending Candidate
      → human Review → immutable Revision → projection → recall / injection → governance change (deactivate / downgrade / reinstall)
      → deactivate: generation and recall stop, history is still validated and rendered under the family description
```

Deactivation is not deletion. Committed Revisions and lineage are fully retained, and because the family description is stored when the family is activated, exact reads after deactivation still return **content validated and rendered against the JSON Schema**, not bare bytes.

## Default deny and two failure layers

An extension family's authorization profile is registered as default-deny: it inherits the surrounding Scope's read permission, but it cannot create share bindings and it does not participate in context injection. Until a family declares projection and context contribution it is neither searchable nor injectable, and context contribution additionally requires a per-family allow from the deployment. Default-deny must be **explicable**: the four states (declared / authorized / projection declared / injection allowed) and "what is missing next" are visible on `GET /v1/extensions`; otherwise the first integration only ever looks like "nothing happens".

Failures fall into two classes that must not be conflated:

- **Activation-time fail-fast.** An invalid or self-contradictory manifest (unknown fields, illegal schema, duplicate family, incompatible naming, undeclared `target_family`) is rejected at startup; that extension stays inactive and appears with a content-free reason code in readiness and on `GET /v1/extensions`. It neither blocks Runtime startup nor affects other families.
- **Runtime fail-soft.** When a generation fails, output does not satisfy the schema, a timeout or request bound is exceeded, or evidence is insufficient, it fails before persisting a candidate and affects only that generation; unrelated capabilities keep working.

## Relationship to existing users

- Scope-level custom instructions for Prompts stay as they are and remain the only entry point for built-in family guidance. Generation profiles do not replace them and can only reference them.
- The `family=profile` user-profile family keeps its per-scope `activation_mode` and does not migrate to the generic Review policy.
- No built-in family is migrated: they keep using the existing typed generation endpoints. Integrating built-in families with generation profiles is not part of S1 in this RFC (see Future possibilities); until that work lands, `generation_profile` is always empty for built-in candidates.
- Generation-origin information in existing Handoff content keeps working; moving it into the generic provenance slot is follow-up work, not a prerequisite for this slice.

# Reference-level explanation

## Scope and interfaces

| Surface | Change |
| --- | --- |
| HTTP | Add generic Artifact writes for `family=generation-profile`; add a generic generation endpoint; open the `family` parameter on read paths; reuse existing endpoints for candidate operations; add a read-only discovery endpoint. |
| Python Client | Family parameters change from a generated enum to strings; add profile content models and discovery models. |
| Runtime | Load extension manifests at startup and construct thin wrapper types; the generation path builds `InferenceLimits` and model settings from the resolved profile. |
| Host integration | No new automatic injection behavior; families without a declared contributor do not enter context. |
| MCP | No automatic registration or profile-write tools; add the discovery endpoint to the read-only allowlist so Agents can list available families and profile keys. |
| Persistence | Add a generation-provenance table, the family-description table `pc_extension_families`, and the template-text snapshot table `pc_extension_prompt_templates`; add a `schema_version` column (derived from the content marker) and structured generation-provenance columns to `pc_artifacts` and the candidate version table; profiles and extension family artifacts reuse `pc_artifacts`/`pc_artifact_heads`. |
| CLI | Add a `powercontext extension` command group (a new command provider): author side `init`, `validate`, `lock`, `diff`; administrator side `enable`, `print-config`, `generation-profile`. The config wizard gains an extensions step. |
| Configuration | Add the extension package path list and the model catalog, both server configuration with no HTTP write interface. |

## Extension manifest

The manifest is `powercontext.extension.json` at the root of the extension package and its structure is closed: unknown fields or unknown enum values fail loading. Loading happens at startup in a fixed order, and any failed step skips the whole package.

```text
powercontext.extension.json
  extension_id          str    globally unique
  version               str    human-readable label
  compatibility         {powercontext: version range}
  families[]            see below, at most 16
  prompt_templates[]    {id, text}, at most 32
  side_effects          {network, filesystem, subprocess}, S1 allows "none" only
  diagnostics           "content-free"
```

### Family declaration

| Declaration | Required | Description |
| --- | --- | --- |
| `family` | yes | Globally unique; `^[a-z][a-z0-9-]*$`, at most 128 characters. The artifact ID prefix is derived by the platform from the family; authors do not declare one |
| `content_schemas` | yes | `{schema_version: JSON Schema}`, one document per version |
| `current_schema_version` | yes | The version used for new writes; existing candidates and Revisions are validated and decoded under the version each recorded, see "Version marker and recording" |
| `review_policy` | yes | S1 accepts `review` only; fixed after registration, not a runtime knob |
| `cardinality` | yes | `singleton` evolves the same logical artifact; `collection` adds or revises by explicit target |
| `lineage` | yes | `{target: optional\|required\|forbidden, require_source: bool, allowed_ref_families: [...]}` |
| `projection` | no | `none` or `fts-heads`; the family becomes searchable only after declaring it |
| `prepared_context` | no | `{display_name}`; injection still requires a per-family allow from the deployment |
| `schedule` | no | Not accepted in S1; explicit generation is the only entry point, see "Scheduling and automatic triggering" |

The manifest schema's size and nesting depth, the family and template limits, and the existing `MAX_ARTIFACT_FAMILY_LENGTH` are all defined centrally in `limits.py`.

### Loading order and failure isolation

```text
read manifest + closed validation → compatibility matrix (platform version range) → per-version check_schema + pre-resolve every $ref + verify the version marker is `required + const` and equals its own version key
→ structural lint (per-level `additionalProperties: false`, non-empty `required`, no dead `$defs`) + projection/render dry run on a skeleton payload (including the element-by-element rendering path over non-empty arrays)
→ de-duplicate family and `prompt_templates[].id`, verify target_family is declared
→ construct thin wrapper types and register → persist the family description → hand to runtime / repository / authorization
```

Any failed step skips the whole package, producing typed error codes and content-free logs, and readiness is non-blocking. Once assembled, the set of families is immutable; there is no runtime re-registration. Built-in family names and built-in prompt keys form the reserved set, and a `prompt_templates[].id` that declares the same name conflicts.

## Content validation for declarative families

### Thin wrapper types

Each family constructs a set of types at load time and plugs them into the existing strict decode entry points:

```text
RunbookContent = RootModel[dict[str, Any]] + after validator (runs the family schema's Draft202012Validator)
RunbookDraft   = subclass of ArtifactDraft[RunbookContent], ClassVar family = "runbook"
Runbook        = subclass of Artifact[RunbookContent],      ClassVar family = "runbook"
```

`RootModel` keeps the payload flat: the stored JSON is the content itself, with no wrapper key. This shape matches the built-in families, so `codec`'s strict decoding and byte round-trip work unchanged. It relies on four existing invariants: type identity (`_require_content` / `_require_proposal` use `type(content) is expected`), the reflection path where `ArtifactRepository` derives the content type from `model_fields["content"].annotation`, `RootModel` payload flattening and byte round-trip, and cross-family type isolation. The four invariants are guaranteed by the existing `codec`, `ArtifactRepository`, and `CandidateRepository`.

### The manifest schema is the only authority

The dynamic class's own `model_json_schema()` carries none of the manifest's constraints — Pydantic generates it from the class definition, so it reflects only the `RootModel[dict[str, Any]]` layer. Therefore:

- Generation profile provenance must use **the manifest schema text** (or its normalized digest), never `ContentType.model_json_schema()`.
- The discovery endpoint, `diff`, and the family description all treat the manifest schema as authoritative.
- Approval validation, generation-output validation, and post-deactivation decode validation all go through the same `Draft202012Validator` — the existing `_json_schema_validator` in `builtin/runtime/relational.py:1763-1768` with an empty `Registry()` for closed reference semantics, adding no new dependency surface; error codes are mapped to the extension-family vocabulary rather than reusing the Source Definition error type.

`$ref` resolution is confined to the document; `http(s)`, `file`, and unknown URNs are rejected outright. Once published, a `schema_version` must keep its declaration while any historical Revision references it: a family may hold several versions in parallel, new writes use only `current_schema_version`, and historical Revisions and pending candidates are validated and decoded under the version each recorded. The platform never rewrites committed bytes on read.

### Version marker and recording

The `schema` field inside content is the version authority: every `content_schemas` entry must be `required + const` to force it to equal its own version key. Storage keeps a column recording the version, for constraints and reverse lookup:

- `pc_artifacts` and `pc_artifact_candidate_versions` each gain a `schema_version` column, **derived from the validated content marker** on write, with a read-time assertion that the two agree; a mismatch is a typed error — content is always the only authority, and the column is only an index.
- That column carries a composite `RESTRICT` foreign key to `pc_extension_families(family, schema_version)`, making "as long as a historical Revision still references a version, its description must be retained" a database-level guarantee rather than developer discipline.
- `GET /v1/extensions` and `diff` can therefore reverse-look-up still-referenced versions in one SQL statement instead of extracting JSON on every dialect.
- Built-in families declare no schema and their column is `NULL`; for declarative families the column is non-null — expressed as an exclusivity between "`family` is in the built-in set" and "`schema_version` is `NULL`", with the DDL never referencing the description table.

Non-backward-compatible changes therefore do not break historical interpretability: each version is validated and rendered under its own schema, and the platform rewrites no committed bytes. The cost is that old and new content may no longer look uniform in presentation and retrieval — rendering and projection follow the fields the content itself carries, and the platform performs no cross-version field rewriting.

### Payload normalization

`dump_model` uses `model_dump_json(by_alias=True)`, which is **order-preserving but not normalizing**: the payload's key order determines the stored bytes. Built-in families gain stability from model field declaration order, while a declarative family's key order comes from the author or the model output, so semantically identical content can produce different bytes. S1's approach is recursive key sorting (array order preserved) inside the wrapper model's `mode="before"` validator, which the generation and approval entry points both inherit by construction.

## Default rules for retrieval projection and context contribution

A declarative family cannot carry projection or rendering functions, so the platform decides both with **one fixed rule**, introducing no configurable dialect:

- **Projection**: traverse the content tree depth-first, take every string value, exclude the version marker `schema`, and concatenate them in a stable order consistent with payload normalization into searchable text; a newline boundary is inserted between array entries. This rule happens to reproduce the sample family's expected projection and adds no configuration.
- **Rendering**: recurse along the same traversal — scalar fields as `Label: value` (the label comes from the schema's `title` annotation, falling back to the field name), arrays element by element as `Label[1]:`, `Label[2]:`, and nested objects expanded under path labels; the section title is taken from `prepared_context.display_name`.
- **Bounds unchanged**: projection returns text only, and analysis, index writes, and the `lifecycle_state='active'` filter all stay in the platform; contribution entries carry exact `ArtifactRef` citations and a per-entry byte cap, and when the budget is exceeded whole array elements are dropped with a visible truncation marker — never a character cut inside an element — while the recursion depth and element-count caps go into `limits.py`; the trust envelope, citation format, and truncation are still generated by Runtime.

**Rebuild the family's projection on activation and upgrade**: after a schema or projection declaration changes, the indexes of existing heads must be made consistent with the new declaration again, otherwise retrieval results drift silently. The rebuild reuses the same entry point and runs once after the family finishes activating.

Tags are a built-in feature, `pc_artifact_tags` has a family CHECK constraint, extension families carry no tags in S1, and `TaggableArtifactFamily` stays closed in the contract.

## Generation profiles

### Storage and identity

A generation profile is the built-in configuration Artifact family `generation-profile`: stored as immutable Artifact Revisions, where `artifact_id` is the profile key (for example `runbook.generate`) and the version dimension is `revision`. Writes go through the generic Artifact create/replace paths and are performed by a Scope administrator; rollback reads an old revision and writes it as a new, monotonically increasing revision.

Because the profile is itself an Artifact in the same Scope, it can feed the composite foreign key into `pc_artifacts` directly.

**A revision is the version of an owner-writable state, not a reproducible generation identity.** `(artifact_id, revision)` identifies only the immutable snapshot of references the administrator wrote; the configuration actually in effect is decided at resolution time by that revision, the catalog entry content, the extension package identity, and `target_schema_version`, and is identified by the resolution identity `digest`. Therefore: changing the profile bytes yields a new revision; changing catalog entry content leaves the revision unchanged while `digest` necessarily changes.

### Content model

```text
GenerationProfileContent
  schema_version      "powercontext.generation-profile.v1"
  target_family       str           this profile serves exactly one family
  prompt              object        {ref}, namespace determined by target_family
  model               str           a model catalog entry name, not a URL
  model_settings      object        bounded settings within the catalog entry's bounds
  timeout_seconds     int           <= the catalog entry's bound
  max_requests        int           <= the catalog entry's bound
  output_max_bytes    int           <= the family schema's content cap
  noop_field          str | null    an empty or missing field in the output means an empty result
  failure             object        {on_model_error, on_invalid_output} in {raise, noop}
  evidence_policy     object        may only narrow the evidence set, never widen it (by Source kind and Artifact family)
```

**The prompt reference has exactly one namespace, determined by the target family.** When `target_family` is a built-in family, `prompt.ref` must point at a prompt key; when it is an extension family, it must point at a `prompt_templates[].id` from an extension manifest loaded in this deployment. Both are versioned (the former by definition/builtin version, the latter by package content addressing), and generation provenance records **a digest of the resolved template text**, `template_digest`.

**The output contract does not belong to the profile.** The family holds the manifest schema; a profile can only reference it and tighten `output_max_bytes`.

**The input shape is owned by the platform.** The model input is a fixed bounded evidence envelope (`evidence_id` + kind + content + truncated, with caps on entry count and per-entry characters), and an extension can only **narrow** which evidence participates by Source kind and Artifact family; it cannot declare its own input schema. This is an explicit tradeoff of the declarative approach, see Drawbacks.

**`noop_field` and `failure` reuse the existing declaration and construction-time validation**: `PromptDefinition.noop_field` is validated against the output type's fields, and a profile's `noop_field` must likewise be validated against the field's existence in the `target_family`'s current schema. The runtime empty-result decision today is `proposal is None`; `no_op` is a generation response status, and the persisted candidate status remains `pending`. An empty result and a failure are two different things — the latter is a model error or an invalid schema, the former is a normal outcome where "the evidence supports no substantive result".

### Resolution and freezing

At generation time, Runtime resolves one exact profile revision for the operation and freezes it for the whole operation, including model retries. Resolution is **keyed by profile key** and never reverse-looked-up by target family — one family may have several profiles, so resolving by family is ambiguous; the family identity is derived from the resolved `target_family` instead. The mechanism follows the existing Prompt approach: a ContextVar holds the resolution result, `current_generation_profile(profile_key)` reads only the selection bound to that operation and never a mutable global head, and concurrent Scopes do not interfere. There are only two levels of precedence: a Scope profile → no profile (falling back to built-in Auto and deployment settings). A caller cannot decide profile content through a request: a caller may only name one profile key, while the profile body is written by an administrator and subject to authorization and budget validation.

Generation provenance is **structured fields plus a derived digest**: `prompt_ref` (the resolved prompt key or template id) and the template text digest `template_digest`, the model identity quadruple `catalog_entry` (catalog entry name) / `provider` / `model` (model id) / `base_url`, the effective bounds `effective_limits {model_settings, timeout_seconds, max_requests, output_max_bytes}` (the values actually in effect after taking the min of each profile declaration and the catalog entry bound), the extension package identity `{extension_id, version, lock_digest}`, `target_schema_version`, and `digest`, whose input contract is frozen by `digest_input_version`. The structure is for auditing and reading in `diff`.

### Authorization

The generation profile family registers in the shared family authorization profile table: base action `artifact.read`, additional action `generation-profile.use`, no shareable states, and no grantable binding roles. Writing requires `SCOPE_ADMIN`, consistent with Prompt: a profile affects the whole Scope's generation behavior, and Artifact ownership alone is not enough to rewrite it.

### Generation entry point

Built-in families keep using the existing typed generation endpoints (`POST /v1/experience/generate` and so on), whose per-call parameters are owned by their own service layers. Extension families have no equivalent endpoint, so one generic generation endpoint is added, and in S1 the explicit trigger is exactly this:

```text
POST /v1/generation/generate
  scope_id     str
  profile_key  str | null    either this or family
  family       str | null    either this or profile_key; a manual proposal
  proposal     object | null  required when only family is given; validated against the family's current_schema_version
  sources      [SourceRef]
  artifacts    [ArtifactRef]
  target       ArtifactRef | null
  reason       str | null
```

- With `profile_key`, generation follows the profile: resolve the Scope's profile revision for that key (a key that does not exist in the Scope is an error), gather evidence, call the catalog model, validate the output against the target family's `current_schema_version`, write a pending Candidate, and record generation provenance.
- With `family` only, it is a manual proposal: the payload is validated against that family's `current_schema_version` and generation provenance is empty. **A family without a profile can therefore still produce candidates** and never becomes a dead end; this is the counterpart of the built-in propose endpoints' semantics for extension families.
- Authorization: requires `scope.contribute`; revising an existing artifact with `target` additionally requires that artifact's `artifact.write`. When unmet, the request is rejected before persistence.
- Errors: an unknown profile key or unregistered family returns `generation_profile_unknown` / `family_unknown` with zero writes; an invalid output is handled by the profile's `failure`, and a candidate is never written; supplying both `profile_key` and `family`, or neither, is a typed error.
- A caller may only name a profile key and **cannot submit profile content**: the body is always written by an administrator and subject to authorization and budget validation.

## Model catalog

The catalog is server configuration declared by the deployment, shaped as `name → {provider, model, base_url, credential env name, bounds}`. The existing `InferenceConfig.generation_*` is normalized at configuration load time into a catalog entry named `default`, so existing deployments keep working unchanged. A profile's limits are min'd item by item against the catalog entry's bounds, and any clamped item records a content-free diagnostic.

## Generation provenance

`ArtifactLineage` gains a pair of nullable non-evidence fields, `generation_source: ArtifactRef | null` and `generation_provenance: GenerationProvenance | null`, following the existing all-or-nothing validation. `GenerationProvenance` is **structured fields plus a derived digest**, and the structured fields record a complete snapshot taken at resolution time rather than mutable references: `prompt_ref`, the template text digest `template_digest`, the extension package identity `{extension_id, version, lock_digest}`, the model identity quadruple `catalog_entry` (catalog entry name) / `provider` / `model` (model id) / `base_url`, the effective bounds `effective_limits {model_settings, timeout_seconds, max_requests, output_max_bytes}`, `target_schema_version`, `digest`, and `digest_input_version`. Recording only an entry name or key would lose identity once the catalog or package changes, and lineage must still answer "what was actually used": catalog entry content keeps no historical versions, so this snapshot carries its forensics, while the extension template text is stored per `template_digest` in `pc_extension_prompt_templates` and is append-only, so it remains recoverable from that anchor after an upgrade or uninstall.

The digest serves equality, de-duplication, and cross-deployment comparison — a 64-character hex string that can be indexed and leaks no content; the structure serves auditing and `diff`, letting operations and reviewers read directly "which prompt, what limits, which target schema version", where a hash can only answer "same or not". The shape is not new: `HandoffGenerationOrigin` in `builtin/artifacts/handoff/generation_metadata.py:44-58` is already "structured fields + `compiled_digest` + `original_draft_digest`".

The digest's input contract must be frozen explicitly: `digest` is a `sha256` over `rfc8785`-canonicalized **fixed field subset**, identified by `digest_input_version`; its first version already includes the model identity quadruple, the effective bounds `effective_limits`, the template text digest, the package identity, and `target_schema_version`, so a catalog entry content change changes the digest and not only the entry name; newly added optional fields by default do **not** enter the digest input, avoiding the false difference where "adding one field makes every historical and new record compare unequal". Coverage includes **the content of the model catalog entry** (provider / base_url / bounds), not just the entry name — otherwise a deployment changing the catalog would be invisible in lineage. Whether content was edited by a human is a separate matter, carried by a different digest and never mixed with the configuration digest.

Persistence follows the existing shape: `pc_artifacts` has no provenance columns, so generation provenance likewise lives in a separate table, written by `ArtifactRepository` when a revision is written and backfilled when lineage is read. On the candidate side, `pc_artifact_candidate_versions` gains four columns — `generation_profile_family` / `generation_profile_artifact_id` / `generation_profile_revision` / `generation_provenance` — plus a **derived** `generation_digest` column, all five living and dying together, with a composite foreign key into `pc_artifacts` (the profile and the artifact share a Scope, so the foreign key holds).

`_candidate_draft` carries it into the new Artifact draft at approval time, making "generated by whom" visible in the Revision's lineage.

## Registry-driven family dispatch

Family dispatch becomes registry-driven.

Approval still completes inside a single database transaction: validate the proposal under the candidate's recorded `schema_version`, enforce declarative lineage, write the Artifact, update derived indexes, and `mark_approved` — five steps in one transaction, rolling back entirely if any step fails. With `cardinality = "singleton"`, the singleton target is frozen **when the candidate is created**: an explicitly given target is recorded as is, and an omitted one resolves the current head at that moment and records it into the candidate's `target_*` columns (a first creation with no head records the expected absence); approval then CASes only against the recorded value and never re-resolves the latest at approval time, eliminating head drift between creation and approval. `collection` keeps explicit semantics.

Owner semantics are required for extension families: new families are automatically covered by `logical_artifacts()`, and a missing owner relation makes the whole Scope's context unavailable.

## Persistence change list

| Table | Nature | Change |
| --- | --- | --- |
| `pc_artifact_generation_provenance` | **new** | Records Artifact Revision generation provenance by `(scope_id, family, artifact_id, revision)`: the profile reference and the structured generation provenance. Shaped like the existing `pc_artifact_publications`, with a composite foreign key into `pc_artifacts` on the reference columns. |
| `pc_extension_families` | **new** | Family description: primary key `(family, schema_version)`, storing that version's JSON Schema text, the source extension identifier, and the activation time; append-only, referenced by the artifact and candidate tables through `RESTRICT` composite foreign keys. It is what allows historical Revisions to be validated and rendered after deactivation, as well as `diff`'s authoritative input. |
| `pc_extension_prompt_templates` | **new** | Template-text snapshot: primary key `template_digest`, storing `template_id` and the resolved template text; append-only, referenced by generation provenance through a `RESTRICT` foreign key. It is what resolves generation provenance's template anchor: the template-text version axis is independent of the family description's schema version axis, so several template texts can coexist under one `(family, schema_version)`. |
| `pc_artifact_candidate_versions` | modified | Adds a `schema_version` column (derived from the content marker, non-null, with a composite foreign key into `pc_extension_families`), four generation-provenance columns plus a derived `generation_digest`, a five-column all-or-nothing CHECK, and a composite foreign key into `pc_artifacts`. Existing `target_*` columns are unchanged. |
| `pc_artifacts` | modified | Adds a `schema_version` column: `NULL` for built-in families, non-null for declarative families (built-in set ⇔ `NULL`), with a `RESTRICT` composite foreign key into `pc_extension_families(family, schema_version)`. All other columns are unchanged. |
| `pc_artifact_heads` | unchanged | `family` is already a free string with no CHECK; `searchable_text` is already a generic column, and extension families reuse the same active-head filter. |

## Contract changes

The places that must be opened up are those that must accept extension families: the five `{family}` path parameters on generic Artifact read paths, the `BaseArtifactFamily`-driven response fields (`ArtifactCreated`, `ArtifactCollectionItem`, `ArtifactRevision`), `CandidateFamily`, `ContextAssemblySection.family`, and the union of candidate responses and proposals (adding an extension-family branch).

Write paths do not need to be opened up: extension artifacts are only produced by approval, so the discriminated unions of `CreateArtifactRequest` and `replace_artifact` stay closed, and `generation-profile` is handwritten as one enumerable variant as a built-in family, exactly as `CreatePromptArtifactRequest` was.

Places that stay closed: `TaggableArtifactFamily` (tags are a built-in feature) and `PromptKey` (prompt keys are registered by the server). Opening up changes the family types re-exported by `powercontext.http` (enum → string), so typed integrations will see a type change: this is an intentional breaking change, and it must ship with `make api-generate` and `make contract-test` alongside the contract change, with a dedicated release-notes entry.

A new read-only discovery endpoint `GET /v1/extensions` is added: it returns activated extensions, families, current and historical `schema_version`s, the declaration and allow status of projections and contributors, available profile keys, and compatibility; for each family it gives four states — `declared` (declared in the manifest) → `authorized` (authorization profile registered) → `projection` (projection declared) → `prepared_context` (contribution declared and allowed by the deployment) — plus "what is missing next". Retrieval and injection failures use this to distinguish the three reason codes "family does not exist / projection not declared / not allowed", instead of a blanket unavailability. It requires `server.observe` and must be content-free: no package install paths, configuration fragments, or credentials.

## Install, deactivate, and historical readability

Persistence guarantees data is not lost, not that it remains semantically interpretable. Artifact content is always JSON inside `pc_artifacts`, and heads and lineage are there too; what an uninstalled package loses are the three things needed to interpret those bytes: the authorization entry's family registration, the family → content type mapping, and the schema needed to validate by `schema_version`.

Three independent gates sit on the read path:

1. **The authorization entry.** `artifact_family_profile()` raises `AccessInvalidRequestError("artifact-family")` for an unregistered family, which hits reads, writes, and shareability validation, so **resource discovery and shareability validation must skip unregistered families**. Readiness' gate is not the family profile but the owner relation: `require_scope_content_ready` takes the owner of every `logical_artifacts()` entry, and `topic-memory` already sets the precedent for an exemption.
2. **The type registry.** `_decode_row` needs family → content type; an unregistered family has no type.
3. **Schema validation.** This is where the declarative approach pays off: `pc_extension_families` stores each `schema_version`'s JSON Schema text, so after deactivation the payload can **still be validated under the version of the time and rendered field by field**, not merely returned as undecoded bytes.

Deactivation contract:

- **Deactivation is an operational action, not a deletion.** It removes the family from assembly but not from registration: an installed-but-disabled package stays registered with `enabled=false`, and only uninstalling the package removes registration. After deactivation the family no longer generates, no longer accepts writes, and no longer participates in recall or injection; committed Revisions are retained.
- **Exact reads for an unregistered family return results validated and rendered from the family description**, clearly marked as coming from the description table rather than from an active registration.
- **Pending candidates freeze**: approval needs a content type and lineage rules. Candidates of an unregistered family can be listed and validated under their recorded version, but `approve` returns a typed error.
- **Deactivating a family with history warns but does not block**: operations are explicitly told which `schema_version`s will only be interpretable through the description table.
- **The description table cannot be reclaimed**: as long as a historical Revision references a `schema_version`, its description must be retained; guaranteed by the `RESTRICT` composite foreign keys on the `schema_version` columns of `pc_artifacts` and the candidate table.

State machine:

| Event | Contract |
| --- | --- |
| Upgrade: new `schema_version` | Keep the old versions' declarations; `current_schema_version` advances; existing heads' projections are rebuilt |
| Upgrade: `projection` changes | Triggers that family's projection rebuild; historical Revision content is unchanged |
| Upgrade: `prepared_context` changes | Injection renders under the new declaration; historical Revisions are unchanged |
| Deactivate | Generation and recall stop; reads go through the family description; pending candidates freeze |
| Reinstall: same `schema_version` | Generation, recall, and writes resume, behaving as before deactivation |
| Reinstall: different `schema_version` | Handled as an upgrade; historical Revisions are decoded under the version each recorded, and stored bytes are not migrated |
| Downgrade | Allowed only if the target version still declares every `schema_version` needed to cover existing Revisions and pending candidates; otherwise activation is refused |

## Execution boundary and resource policy

**S1 executes no extension code**: the only things running on the production path are manifest parsing and JSON Schema validation, neither of which executes logic provided by the extension author, and `side_effects` are all `none`. No sandbox, capability boundary, or process resource cap is therefore needed for extension code, and `validate` only performs data validation.

What still applies is the model-call boundary driven by the profile:

| Dimension | Policy |
| --- | --- |
| Time | The profile's `timeout_seconds` combined with `max_requests`, nested inside the existing worker timeout |
| Output | The family schema, `output_max_bytes`, and the evidence envelope's entry and character caps |
| Request count | The profile's `max_requests`, bounded by the catalog entry's bound |
| Network | Only through the platform adapter, validated by the "model catalog and egress policy" rules |
| Secret | Read from environment variables only inside the platform adapter; the manifest is pure data and has no credential surface |
| Diagnostics | Content-free: extension ID, family, and reason code only |

## Scheduling and automatic triggering

S1 has explicit generation only: a caller (Agent, CLI, or integration) issues one generation through the generic endpoint when the evidence is ready, and profiles produce no automatic scheduling.

Later slices that add automatic triggering reuse two design points: the family declares evidence filter conditions, and when "a window has qualifying Sources but all of them were filtered out", one content-free diagnostic is recorded so silent failure becomes discoverable.

## Author tools

| Command | What it solves | Key output |
| --- | --- | --- |
| `powercontext extension init <dir> --family <name>` | Start from a validatable skeleton so authoring does not drift | Manifest, `schemas/<family>.v<k>.json`, prompt templates |
| `powercontext extension validate <path> --powercontext <version>` | Reproduce every activation-time check offline and block an unconstrained schema | The same typed error codes as activation + JSON Pointer locations; the structural lint and skeleton dry-run conclusions; `--json` for machines |
| `powercontext extension lock <path>` | Fix the package's current semantic fingerprint for later upgrade comparison | `powercontext.extension.lock.json` (`rfc8785` digests of the manifest and all schemas), committable |
| `powercontext extension diff <old> <new> [--against <lock>]` | Upgrade impact preview | Affected families and profiles, removed `schema_version`s, projections that need rebuilding, compatibility verdict; the exit code distinguishes backward-compatible from breaking changes |

All four commands share one constraint: they do not load the package, do not connect to a database, and do not execute extension code. The platform version is a required axis for `validate` — the compatibility matrix is itself an activation-time check, and omitting this axis would give a green light that disagrees with activation. File fingerprints do not carry package integrity (that is the distribution layer's job); `lock` cares about **semantic** impact.

## Administrator tools

The administrator side reuses three more subcommands of the same command group, so that no manifest or profile JSON is handwritten:

| Command | What it solves | Key output |
| --- | --- | --- |
| `powercontext extension enable <id>` | Put the package into the enable list | Updates the extension paths and enable entries in server configuration |
| `powercontext extension print-config` | Tell a human what is still missing | Paste-ready enable list entries, a model catalog skeleton, and per-family allow entries |
| `powercontext generation-profile new\|set\|rollback` | Write or roll back a generation profile | Validates against the family schema and catalog bounds before writing; echoes the new revision and structured provenance after writing |

The `powercontext config` wizard gains an extensions step that reuses the existing bilingual prompts and document-rendering pipeline, putting "enable an extension family" on the same guided path as "configure inference". The four states and "what is missing next" returned by the discovery endpoint are exactly these commands' information source.

## Implementation and acceptance

Implementation proceeds in two slices, and the acceptance items below are a global bar that is not reordered per slice:

- **S1**: one Runbook extension family plus one generation profile, covering exact evidence → Candidate → human Review → retrieval and context contribution → exact reads after deactivation; including the generic generation endpoint, `pc_extension_families` and `schema_version` persistence, template-text snapshots, and generation provenance. Corresponds to acceptance items 1, 2a–2e, 3–16, 17, 19–22, 25–27.
- **S2**: `extension lock/diff`, `enable` / `print-config` maturity, the config wizard's extensions step, and the four-state refinement of the discovery endpoint. Corresponds to acceptance items 18, 23, 24.

Acceptance covers external behavior:

1. A sample extension (shipped with the implementation, not as an attachment to this RFC) derives a typed Candidate from exact evidence, gets approved through human Review, and commits an immutable Revision; evidence and provenance are exactly readable from lineage.
2. Generation-identity changes are observable: **2a** profile content changes ⇒ a new exact profile revision; **2b** a family schema change ⇒ a new exact `schema_version`; **2c** replacing template text under the same `prompt_templates[].id` ⇒ a new extension package version identity; **2d** catalog entry content changes ⇒ no new version, but `digest` necessarily changes. They are visible in lineage as `generation_profile_revision`, `target_schema_version`, `extension_package` and `template_digest`, and `digest` respectively; 2d additionally requires the structured snapshot to answer which model and endpoint were used after an entry is renamed, a model swapped, or an entry removed. **2e** Under one `schema_version` and one `prompt_templates[].id`, two package versions carrying different template text ⇒ both template-text snapshots are retained; after the extension package is uninstalled both texts remain readable, and each revision's `template_digest` can be checked against its original text.
3. An invalid manifest (unknown fields, illegal schema, duplicate family, naming conflict, undeclared `target_family`) is rejected at activation and appears only in readiness and the discovery endpoint; other families and generation paths are unaffected. Runtime failures stop before a Candidate is persisted.
4. The generator cannot allocate the final Artifact identity, approve its own Candidate, publish content, or authorize itself; the relevant APIs do not exist.
5. Artifact IDs are allocated by the platform with a family-derived prefix and a CAS, independently of content; authors do not declare a prefix and there is no prefix-conflict failure class.
6. Empty results are decided by `noop_field`: no candidate is written, an explicit `no_op` is returned, the diagnostic is content-free, and the path is distinguishable from `failure`.
7. `cardinality` takes effect: `singleton` evolves the same logical artifact when a candidate gives no target and performs a head CAS; `collection` keeps explicit semantics. The singleton target is frozen when the candidate is created: if the head advances concurrently between candidate creation and approval, approval CASes against the candidate's recorded target and returns a version conflict rather than silently attaching the content to the new head. A first creation compares against the expected absence and conflicts if the head has been created concurrently.
8. The full expressive power of declarative schemas takes effect: `allOf`/`if`/`then`, nested `minLength`/`maxItems`, `const`, `additionalProperties: false`, and cross-family payload rejection are all enforced on the activation and approval paths.
9. Payload normalization takes effect: payloads with the same semantics but different key order produce identical stored bytes.
10. An extension family without a declared projection is not searchable; a contributor that is undeclared or not allowed is not injected; requesting assembly for that family directly returns 422.
11. Unapproved Candidates appear in neither retrieval nor PreparedContext.
12. Injected content stays bounded, precisely cited, and wrapped as untrusted history; extensions cannot inject raw system or developer instructions.
13. Out-of-bounds profile content (exceeding catalog or family bounds) is rejected at write time; URLs in profiles and manifests are rejected by schema.
14. Egress validation rejects non-http/https, localhost, loopback, private, and reserved addresses, on both configuration load and request paths.
15. Diagnostics contain no content; model output and evidence bodies appear in no error, readiness signal, discovery endpoint, or capabilities response.
16. **Deactivation safety**: after deactivating a family with historical Revisions, the affected Scope's context remains available (unregistered families are skipped in resource discovery and shareability validation, while readiness is bounded only by the owner relation), exact reads are validated and rendered from the family description, pending candidates can be listed but their approval is refused, and reinstalling the same version restores behavior to what it was before deactivation.
17. After an upgrade raises `schema_version`, pending candidates created before the upgrade are approved under their recorded version; a projection declaration change triggers a rebuild and the index matches the new declaration.
18. `powercontext extension validate` returns success for a valid package and, for an invalid one, the same typed error codes as activation (the same validation code and locations, machine-readable with `--json`); the platform version is a required axis and omitting it fails; it does not connect to a database and **does not execute any extension code**; a package generated by `init` passes `validate` directly, while the structural lint and skeleton dry run fail on an unconstrained schema; `lock`'s digest can be committed and `diff --against` correctly reports removed `schema_version`s and projections needing rebuild, with exit codes distinguishing backward-compatible from breaking changes.
19. Each database dialect is verified once for submitting, retrieving, exactly reading, and deactivating/downgrading an extension family.
20. The generic generation endpoint works: with `profile_key` it generates following the profile and records provenance; with `family` only it takes the manual-proposal path and validates against `current_schema_version`; supplying both or neither, and an unregistered key or family, all return typed errors with zero writes.
21. An extension family without a profile can still produce candidates through a manual proposal, and such candidates have empty generation provenance; when one family has several profiles, resolution goes by profile key rather than by family, and different keys yield different models and budgets.
22. **Version landing**: the content marker and the `schema_version` column always agree (the column is derived from the content, and a mismatch is a typed error); an unregistered version cannot be written; reclaiming a still-referenced description is refused by `RESTRICT`; after deactivation and downgrade, content can still be validated and rendered under its recorded version.
23. **Explicable default-deny**: `GET /v1/extensions` gives the four states and "what is missing next"; retrieval and injection failures distinguish "family does not exist / projection not declared / not allowed"; an MCP read-only client can list extension families and available profile keys.
24. **Administrator closed loop**: going from an empty configuration to "the family can generate" requires no handwritten JSON — `extension enable` / `print-config` produce configuration fragments, the `generation-profile` subcommand writes the profile and echoes the revision and structured provenance, and the config wizard covers the same step.
25. A Runbook's `steps[]` appear in PreparedContext in original order as `Steps[1]:`…, and `symptoms[]` / `failure_handling[]` are equally reachable; over budget, whole elements are dropped with a visible truncation marker.
26. Projection covers nested strings: words inside `steps` are hit by retrieval; the `schema` version marker does not enter projection.
27. Payloads with the same semantics but different key order produce identical `searchable_text` bytes (following the same order as payload normalization).

# Drawbacks

- The declarative approach sets a clear ceiling on expressiveness: no custom projection or rendering, no custom lineage validation, no declaring an input shape, and no output transformation. Domains that need these can only be served by extending the kinds of declarations, or by the code-hook extension points in later work.
- Model input is fixed to the platform's bounded evidence envelope, and a family cannot express its own input contract.
- The manifest schema is the only authority, while the dynamic type carries no constraints.
- Two new tables and two new column sets (`schema_version` and generation provenance) must be kept in sync across every database dialect's migration and probe logic.
- The platform performs no content migration: a historical Revision is validated and rendered under its recorded version, with no cross-version field rewriting. A renamed or re-semanticized field therefore makes old and new content differ in presentation and retrieval (old field names still appear in rendered text and search terms), and unifying them requires a new family name or a later presentation-layer projection.
- Multiple versions coexisting means the description table only grows, and operations will eventually need an archival or compaction strategy.
- A family may hold several `schema_version`s, but the platform does not judge which version is more appropriate; authors must still maintain compatibility on their own through locked samples and `diff`.

# Rationale and alternatives

- **Why families are declarative rather than code.** Declarative is friendlier for extension authors, and while code-based families are more expressive, they cost three things: extension code runs at runtime (needing a sandbox and capability boundary), types disappear once the package is uninstalled, and any change to the family set requires platform source and contract enum changes. The cost of solving those is high.
- **Why a profile declares no schema.** The output contract is resolved from the target family, and a profile can only tighten bounds. If a profile could change the output schema, it could conjure types out of nothing, piercing `codec`'s strict validation and `_require_content`'s type guarantee.
- **Why keep multiple `schema_version`s instead of collapsing to one.** A single version requires evolution to stay backward compatible (a new schema must accept old payloads), whereas keeping multiple buys historical interpretability that depends on no author discipline and requires no migration code at runtime.
- Returning untyped JSON from a prompt and writing it into one generic table: erases family semantics, lifecycle, Review, and compatibility, and pierces `codec`'s strict type validation.
- Treating every new result as Memory: conflates persistent facts and decisions with domain-specific deliverables.
- Letting custom output enter PreparedContext automatically: bypasses selection, citation, budget, trust, and authorization.
- Profiles carrying their own `base_url`: would let a Scope administrator target any egress destination, with no place to mount egress policy.
- Letting a profile revision follow catalog entry content or extension package identity changes: catalog entries are written by the deployment and package versions by the extension author, while a profile write needs only `SCOPE_ADMIN`; following would mean requiring the catalog to be append-only and moving control over model reachability from the deployment to each Scope — operational burden, not reproducibility.
- A separate `/v1/extensions/...` operation surface for extension families: splits one resource into two API surfaces and conflicts with the single repository route.
- A management direct-write path for extension families: would require opening up the `CreateArtifactRequest` discriminated union and would introduce "extension-preseeded content" into the trust surface. S1 keeps approval as the only producer.

# Prior art

[RFC 0050](0050_artifact_candidate_review_inbox.md) establishes that Review is fixed by the family, that a Candidate is not an Artifact, and that candidates enter neither search nor PreparedContext.

[RFC 1468](1468_scope_owned_prompt_management.md) establishes the Scope-level versioning model for configuration families and makes explicit that model settings, credentials, schemas, and resource limits live outside Prompt content.

[RFC 1458](1458_artifact_generation_source_access.md) establishes how generation evidence is admitted.

[RFC 1489](1489_prepared_context_text_assembly.md) establishes Runtime ownership of final content, exact citations, and budgets.

# Unresolved questions

- The slice boundary for automatic triggering: reuse the source-window rounds or introduce a separate scheduling binding.
- Whether code-hook extension points (custom projection, rendering, lineage) are opened, and what trust boundary and sandbox semantics they would need.
- The migration path and timing for converging existing prompt ref semantics into the unified provenance slot.
- Whether extension families join tags and cross-Scope publication, and which authorization vocabulary each would require.
- The description table's archival scheme: the retention policy is settled (append-only plus `RESTRICT`), but long-term archiving and compaction have no path yet.
- How extension families are presented on the Dashboard; S1 explicitly does not present them.

# Future possibilities

- Automatic triggering: attach to the existing source-window rounds, reusing evidence filtering and the "all filtered out" diagnostic.
- Integrating built-in families with generation profiles: letting built-in families such as Experience and Skill also be generated under a profile.
- A declarative field list for projection and rendering, strengthening declarative expressiveness.
- Code hooks as an escape hatch: capability-bounded extension points for families that need custom projection, rendering, and lineage, while declarative stays the default.
- Extensions providing vector projections or custom ranking, fused with the existing FTS.
- Monetary cost budgets: requiring model pricing data and finer usage attribution.
- Package signing and origin verification, giving "the administrator-controlled package boundary" verifiable supply-chain evidence.
