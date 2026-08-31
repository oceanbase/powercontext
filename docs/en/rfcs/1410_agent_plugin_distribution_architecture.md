- Proposal Name: `agent_plugin_distribution_architecture`
- Start Date: 2026-08-31
- RFC PR: [oceanbase/powercontext#1410](https://github.com/oceanbase/powercontext/pull/1410)
- Tracking Issue: [oceanbase/powercontext#1405](https://github.com/oceanbase/powercontext/issues/1405)

# Summary

PowerContext will use the existing [Agent Plugins](https://agent-plugins.org/) package at
`integrations/agent-plugin/powercontext/` as the only hand-maintained source for portable integration content and
deterministically project it into host-native distributions. Agent Skills, portable MCP configuration, plugin metadata,
and shared naming are authored once. A target adapter translates that source into a host's packaging dialect using a
validated target descriptor. Host-native lifecycle behavior remains handwritten runtime code. Generated distributions
are committed for review and release, but are never authoritative sources.

# Motivation

PowerContext currently ships integrations for several agent hosts. The hosts differ in extension layout, MCP
configuration, installation, lifecycle hooks, and runtime APIs. Those differences are real and should not be hidden
behind a fictional universal host API.

However, the current distributions also independently maintain content that is not inherently host-specific:

- the project-context Skill and its guidance;
- MCP server identity and connection intent;
- plugin name, version, description, and repository metadata;
- operation and tool naming conventions;
- repeated helper assets used by more than one distribution.

Independent copies have already developed different names, tool prefixes, MCP shapes, versions, and Skill behavior.
Every correction must be rediscovered and applied per host. Adding another host multiplies this maintenance cost and
makes reviews focus on copied text instead of intentional differences.

The desired outcome is not identical integrations. It is a single ownership rule:

> Author portable behavior once, describe target-specific packaging explicitly, and handwrite only behavior that
> depends on a host runtime.

This RFC establishes that rule and the generation boundary needed to enforce it.

# Guide-level explanation

## Mental model

The distribution system has four named parts:

| Part | Meaning |
| --- | --- |
| **Canonical Plugin** | The installable Agent Plugins package containing portable metadata, Skills, and MCP configuration. |
| **Target Adapter** | Deterministic projection logic for one host packaging format. |
| **Native Runtime** | Handwritten code that uses host-specific hooks, APIs, or lifecycle semantics. |
| **Target Distribution** | The committed files that users install for a particular host. |

Their relationship is:

```text
Canonical Plugin + Target Adapter + Native Runtime = Target Distribution
```

The effective capabilities of a distribution are deliberately bounded:

```text
effective capabilities = canonical capabilities ∩ host capabilities ∩ adapter support
```

Generation therefore does not promise feature equality between hosts. It promises that every portable capability has
one source and that any omission is explicit.

## Ownership rule

Contributors decide where to edit a behavior by asking what makes it different:

| Change | Authoritative location |
| --- | --- |
| Skill instructions shared by hosts | Canonical Plugin |
| MCP server identity and portable connection settings | Canonical Plugin |
| Host manifest shape or config key mapping | Target Adapter |
| A provider-only manifest field with no portable equivalent | Validated target descriptor consumed by the Target Adapter |
| Session hooks, tool registration, event handling, or host APIs | Native Runtime |
| A generated copy in a host directory | Nowhere; edit its source and regenerate |

For example, a correction to project-memory recall guidance is made once in
`integrations/agent-plugin/powercontext/skills/project-context/SKILL.md`. A Claude Code-only lifecycle hook is still
changed in the Claude Code runtime. A different MCP header syntax belongs in the corresponding target descriptor and
adapter, not in another copy of the MCP server definition.

## Contributor workflow

The generator exposes three stable workflows:

```text
uv run python -m scripts.agent_plugins build
uv run python -m scripts.agent_plugins build --target codex
uv run python -m scripts.agent_plugins check
```

The first command rebuilds all target distributions. The second narrows local iteration to one target. The third
performs validation and fails if committed generated files differ from a fresh projection.

A normal change follows this sequence:

1. Edit the Canonical Plugin, a Target Adapter, or a Native Runtime according to the ownership rule.
2. Generate the affected distributions.
3. Review the generated diff together with the source diff.
4. Run conformance and drift checks.
5. Commit both source and generated artifacts.

Generated output must consist of ordinary files. Installations and source archives must not depend on repository
symlinks, submodules, or a local build step.

## Examples

### Change shared Skill behavior

Issue [#1378](https://github.com/oceanbase/powercontext/issues/1378) requires the project-context Skill to recognize
explicit memory requests. The accepted trigger contract is updated in the canonical `project-context` Skill. Adapters
may mechanically encode declared operation identifiers for a host, but cannot replace the Skill body, append arbitrary
provider instructions, or override its frontmatter.

### Change MCP configuration

The canonical `mcp.json` identifies the server as `powercontext` and expresses connection settings supported by Agent
Plugins. If one host spells environment-provided HTTP headers differently, its adapter translates that field from
allowlisted data in the target descriptor. A target descriptor cannot replace the canonical server identity, URL, or
transport with an unrelated definition.

### Change lifecycle behavior

If Pi needs a runtime event to flush or restore context, that implementation remains in Pi's TypeScript runtime. The
generator may package that runtime and validate its declared capabilities; it does not synthesize the event-handling
business logic.

## Migration

Migration is incremental:

1. Inventory existing distributions and classify every file as portable source, adapter policy, native runtime, or
   generated output.
2. Adopt the existing canonical plugin without changing installation behavior.
3. Add adapters one host at a time and compare their output with the existing distribution.
4. Switch each migrated portable file to generated ownership and enable drift checks for that target.
5. Normalize names and retain documented compatibility aliases for a bounded release window.
6. Remove obsolete copies and aliases after all supported installation paths consume generated distributions.

Until a target completes step 4, its current directory remains authoritative. A file must never have two claimed
sources during migration.

# Reference-level explanation

## Scope and responsibility boundaries

This RFC owns packaging source-of-truth, projection, generated-artifact policy, and shared naming. It composes with,
but does not replace, existing work:

| Work item | Responsibility |
| --- | --- |
| [#1244](https://github.com/oceanbase/powercontext/issues/1244) | Provides the existing reusable Agent Plugin that becomes the canonical source. |
| [#1301](https://github.com/oceanbase/powercontext/issues/1301) | Owns multi-host installation and user configuration. |
| [#1338](https://github.com/oceanbase/powercontext/issues/1338) | Defines expected coding-agent access capabilities and user-facing alignment. |
| [#1352](https://github.com/oceanbase/powercontext/issues/1352) | Owns the broader Agent integration roadmap. |
| [#1357](https://github.com/oceanbase/powercontext/issues/1357) | Defines the versioned integration capability vocabulary, status, and evidence. |
| [#1362](https://github.com/oceanbase/powercontext/issues/1362) | Defines lifecycle-aware integration behavior and host-neutral lifecycle contracts. |
| [#1378](https://github.com/oceanbase/powercontext/issues/1378) | Owns explicit memory routing behavior in the canonical Skill. |
| [#1397](https://github.com/oceanbase/powercontext/issues/1397) | Defines publication and installation lifecycle for separately managed Skill packages. |

This RFC has the following non-goals:

- defining a universal lifecycle-hook API;
- forcing all hosts to expose the same capabilities or tools;
- generating host-native business logic from templates;
- publishing or installing separately managed Skill packages;
- building a general-purpose ecosystem compiler in the first implementation;
- rewriting reviewed managed Skills that have an independent release lifecycle.

## Repository model

The source layout extends the existing canonical package:

```text
integrations/
├── agent-plugin/
│   ├── powercontext/
│   │   ├── plugin.json
│   │   ├── mcp.json
│   │   └── skills/
│   │       └── project-context/
│   │           ├── SKILL.md
│   │           ├── references/
│   │           └── scripts/
│   └── targets/
│       ├── target.schema.json
│       ├── claude-code.json
│       ├── codex.json
│       └── ...
├── claude-code/
├── codex/
└── ...
scripts/
└── agent_plugins/
    ├── __main__.py
    ├── model.py
    └── targets/
```

`integrations/agent-plugin/powercontext/` remains directly installable and is the source for portable content.
`integrations/agent-plugin/targets/` contains schema-validated target descriptors, not text patches or replacement
templates. `scripts/agent_plugins/targets/` contains adapter implementations.

Existing host directories remain installation and release boundaries. They contain Native Runtime files and generated
projections side by side. A target definition records the exact generated paths so ownership can be checked without
inferring it from directory names.

## Artifact classes

Every integration path belongs to exactly one class:

| Class | Hand-edited | Committed | Drift-checked |
| --- | --- | --- | --- |
| Canonical source | Yes | Yes | Validated for conformance |
| Target descriptor or adapter | Yes | Yes | Validated against its schema and contract |
| Native Runtime | Yes | Yes | Tested by the host integration |
| Generated distribution artifact | No | Yes | Compared byte-for-byte after normalization |

Generated files include a source marker where the target format permits comments. Formats that do not permit comments
are tracked by the target definition and the generator's output manifest. The marker is informative; declared output
ownership is authoritative.

## Canonical plugin contract

The Canonical Plugin follows the Agent Plugins specification:

- `plugin.json` is the portable manifest and declares the specification schema version;
- `skills/` contains immediate child directories conforming to Agent Skills;
- `mcp.json` contains portable MCP server configuration and uses the same specification version;
- the canonical Skill has local name `project-context` and package-qualified identity `powercontext/project-context`;
- all referenced paths resolve within the plugin root;
- packaged Skills are real files, not external symlinks.

The canonical package can be installed directly by conforming hosts. An adapter for such a host may therefore be an
identity projection plus release metadata, rather than a second manifest dialect.

## Target adapter contract

Each Target Adapter receives a typed, validated model rather than raw source text. Its inputs are:

- the Canonical Plugin model;
- the target identifier and target capability record;
- the schema-validated target descriptor;
- version and repository release metadata;
- the repository root and an allowlisted output root.

It produces:

- a mapping of normalized relative paths to bytes;
- structured diagnostics;
- a machine-readable record of projected, omitted, and unsupported capabilities.

An adapter must classify every canonical component as `projected`, `unsupported`, or `not_applicable`. Silent omission
is an error. Unsupported required capabilities fail generation; unsupported optional capabilities produce an explicit
diagnostic and capability record.

Adapters contain structural translation, not content forks. They may rename fields, wrap documents, select supported
components, and materialize provider metadata. They must not carry an alternative Skill body, an alternative portable
MCP server definition, or host runtime business logic.

## Target descriptors

Each maintained distribution has one descriptor validated by `target.schema.json`. The descriptor declares the target
ID, output root, adapter kind, capability-manifest entry, provider-only manifest fields, naming projection, native
runtime roots, compatibility aliases, and the generated-output manifest path.

A target descriptor:

- may populate only provider fields allowlisted by its adapter schema;
- may refer to canonical component and OpenAPI operation identifiers;
- cannot contain a Skill body, MCP document, executable code, or arbitrary text patches;
- cannot override canonical plugin version, Skill frontmatter, or MCP server identity;
- cannot contain secrets or environment-specific credential values.

Conditional structural translation belongs in a reviewed Target Adapter. Host behavior belongs in the Native Runtime.
This boundary deliberately avoids a general overlay language.

## Target set and capability manifest

The normative target set is the set of validated descriptors checked into `integrations/agent-plugin/targets/`.
Directory presence elsewhere in `integrations/` does not make a distribution a generator target. Each descriptor must
resolve to an `agent_host` entry in the versioned manifest from [#1357](https://github.com/oceanbase/powercontext/issues/1357)
and cannot refer to an `unsupported` or `proposed` integration.

The generator consumes the target's integration ID, availability, and declared capability set to compute the effective
intersection. It checks that projected and unsupported capability records agree with that manifest. Evidence paths are
validated for existence, while their behavioral claims remain the responsibility of focused integration tests. The
generator does not infer capabilities from files, tool counts, or runtime introspection.

## Projection algorithm

Generation is a pure function of repository inputs and proceeds as follows:

1. Load and schema-validate the Canonical Plugin.
2. Validate Agent Skills and MCP configuration, including path containment.
3. Load and validate the target descriptor and referenced capability record.
4. Resolve the effective capability intersection.
5. Ask the Target Adapter to render its complete output map and diagnostics.
6. Reject duplicate, absolute, escaping, symlinked, or undeclared output paths.
7. Normalize UTF-8 text to LF line endings, stable key ordering, and one trailing newline.
8. Materialize ordinary files and remove stale files previously owned by the generator.
9. In check mode, compare the normalized output map with the committed files without writing.
10. Emit actionable errors naming the source component, target, and violated contract.

Generation must not depend on network access, wall-clock time, locale, user configuration, or secrets. Identical inputs
must produce byte-identical outputs.

Stale-file removal is limited to paths recorded in the previous generated-output manifest and revalidated under the
target output root. The generator must never recursively clean an integration directory.

## Skill projection

The canonical built-in Skill has local name `project-context` and package-qualified identity
`powercontext/project-context`, matching the existing Agent Plugin. A target with a flat global Skill namespace encodes
that identity mechanically as `powercontext-project-context`; this encoded form is not a second semantic identity.

The complete canonical Skill is the default projection. Version 1 defines no free-form extension slots. An adapter may
rewrite only inline operation tokens that exactly match declared OpenAPI operation IDs, using the target's naming
projection. Any unmatched, ambiguous, or undeclared rewrite fails generation. Provider-specific guidance remains a
separate Native Runtime asset rather than being appended to the shared Skill.

Adapters must preserve:

- the canonical Skill's logical identity and semantic purpose;
- all portable trigger and safety instructions;
- referenced assets, scripts, and references;
- the relative internal link structure.

Any host unable to preserve those properties reports the Skill as unsupported instead of publishing a misleading
partial copy.

## MCP projection

The canonical MCP server identifier is `powercontext`. Portable transport, command, argument, URL, environment, and
header intent are represented in `mcp.json` when the Agent Plugins schema supports them.

Target adapters may translate configuration syntax, such as wrapping `mcpServers`, mapping environment-provided HTTP
headers, or substituting a provider's plugin-root placeholder. Provider-only configuration must come from allowlisted
target-descriptor fields. Adapters do not change MCP wire behavior or duplicate the OpenAPI operation contract.

Credentials are references to runtime environment variables or provider secret stores. Canonical sources, target
descriptors, generated artifacts, and diagnostics must never contain resolved credentials.

## Native Runtime boundary

Native Runtime code remains the authority for:

- provider lifecycle hooks and event ordering;
- native tool registration and invocation;
- session state and host storage APIs;
- provider-specific authentication or consent flows;
- host UI, commands, and error presentation.

The generator may copy, package, or validate declared Native Runtime entry points, but it does not generate their
business logic. Shared runtime libraries may be extracted when concrete duplication justifies them; they are not a
prerequisite for this RFC and do not imply a universal lifecycle abstraction.

## Implementation language boundary

The deterministic compiler is implemented in Python 3.11+ because PowerContext is a Python project and already uses
Python for repository generators and validation. The implementation reuses the project's schema tooling and follows
the existing `scripts/generate_js_operations.py` write/check pattern.

This choice does not constrain runtime languages:

- TypeScript integrations keep TypeScript Native Runtimes;
- Python hooks and integrations keep Python Native Runtimes;
- JSON, YAML, Markdown, OpenAPI, and JSON Schema remain language-neutral contracts.

The compiler invokes no Node or Python provider runtime while rendering. Target-specific runtime builds remain the
responsibility of their existing package workflows.

## Naming contract

The normalized names are:

| Entity | Canonical form |
| --- | --- |
| Plugin | `powercontext` |
| Project context Skill | local `project-context`; qualified `powercontext/project-context` |
| MCP server | `powercontext` |
| API operation | OpenAPI `<operation_id>` |
| Native global tool | `powercontext_<operation_id>` |
| Transitional compatibility alias | explicit target mapping, including existing `pc_*` names |

Adapters may encode a canonical name to satisfy a provider's syntax, but the mapping must be mechanical and recorded.
They must not invent a different semantic name. Compatibility aliases are listed explicitly because existing `pc_*`
names are not always mechanical operation-ID prefixes. New aliases must record their introduction and removal release,
emit deprecation metadata where supported, and remain for at least two minor releases and 90 days.

## Generated-artifact and release policy

Generated distributions are committed because they are user-installable release artifacts, make adapter effects
reviewable, and allow consumers to install from source archives without generator dependencies. A pull request that
changes canonical inputs or adapter logic includes the resulting generated diff.

CI runs generation in check mode and fails on:

- schema or Agent Plugins conformance errors;
- generated drift or stale owned files;
- undeclared component omissions;
- output paths escaping their target root;
- symlinks in portable packaged content;
- naming or capability-manifest inconsistencies;
- nondeterministic output detected by two clean renders.

The generator version is not embedded as a timestamp. Distribution versions originate in the canonical manifest and
are projected into every provider manifest or marketplace entry representing that distribution. A separately released
Native Runtime package may retain its own version only when the target descriptor marks that field as independently
owned; it cannot be presented as the canonical plugin version.

## Compatibility and rollout

The first rollout preserves existing installation paths and runtime behavior. Differences discovered during projection
are classified before they are normalized:

- an accidental divergence is fixed in the canonical source;
- a provider constraint is encoded in a target descriptor or adapter;
- a true capability difference is recorded in the capability manifest;
- a lifecycle difference remains in Native Runtime code.

Changing provider-facing Skill encodings or native tool prefixes may affect prompts, documentation, and saved
configurations. Generated distributions retain explicit aliases for at least two minor releases and 90 days; alias
removal requires release notes and a target-descriptor change. A host that cannot safely expose aliases keeps the legacy
name until a separately reviewed breaking change. No persisted PowerContext memory format or HTTP API changes as part
of this RFC.

## Security and authority

Projection is packaging, not authorization. A generated declaration does not grant a host capability, permission, or
access that its runtime and user configuration do not provide.

The generator enforces path containment for source references and outputs. It reports structural diagnostics only;
diagnostics must not include resolved secrets, prompt bodies, stored memories, or access tokens. Target descriptors are
reviewed source files and may reference environment-variable names, but cannot contain credential values.

# Drawbacks

- The repository continues to contain generated copies, increasing diff and checkout size.
- The generator and adapter schemas become maintained infrastructure with their own compatibility burden.
- Contributors must understand the distinction between canonical source, adapter policy, Native Runtime, and generated
  output.
- Provider format changes can require urgent adapter updates even when PowerContext behavior is unchanged.
- Some changes temporarily produce larger diffs while existing distributions are migrated and aliases coexist.
- Restricting target descriptors may require small adapter modules for cases that a permissive template could express
  more quickly.

# Rationale and alternatives

## Why this design

Agent Plugins defines a credible portable minimum for Skills and MCP servers without claiming to standardize every
agent runtime. Using it as the Canonical Plugin makes the central source independently valid and directly consumable by
conforming hosts. Deterministic adapters then isolate unavoidable packaging differences, while the Native Runtime
boundary prevents generation from obscuring lifecycle semantics.

Committing generated output trades repository size for reviewability, offline installation, and release determinism.
Using a Python compiler aligns with the repository's existing toolchain and keeps the generator separate from any one
TypeScript host.

## Alternatives considered

**Continue independent maintenance.** This has no migration cost, but every new host and every shared correction
multiplies drift. It does not satisfy the ownership objective.

**Support only hosts that consume Agent Plugins directly.** This is the simplest architecture, but would drop useful
existing integrations because their native packaging or lifecycle surfaces differ. Agent Plugins is the portable core,
not the complete PowerContext integration contract.

**Choose one existing provider distribution as the source.** A Claude Code or Codex manifest contains provider
assumptions and makes other providers look like lossy derivatives. A vendor-neutral canonical package provides a
cleaner boundary.

**Adopt a general third-party plugin compiler.** No established tool currently covers PowerContext's exact combination
of Agent Plugins, committed provider packages, capability evidence, and handwritten runtimes. Depending on one before
its target semantics are stable would move rather than remove maintenance. The adapter contract can later become a
backend for a mature external compiler.

**Use a multi-agent Skill installer as the distribution system.** Tools such as `npx skills` can copy one Skill into
several host locations, but they do not project MCP configuration, plugin manifests, marketplace metadata, capability
degradation, or Native Runtime packaging. They remain useful installation consumers, not the source compiler.

**Generate Native Runtime code from templates.** This reduces visible duplication but hides behavior in templates,
couples unrelated host APIs, and makes lifecycle changes harder to review. Shared runtime libraries are a safer later
refactoring where semantics truly match.

**Use symlinks from distributions to a shared directory.** Symlinks are fragile in archives, package managers, Windows,
and plugin containment checks. Generated real files are more portable.

**Do not commit generated artifacts.** This keeps the repository smaller, but shifts generator and toolchain
requirements onto installers and makes release contents less visible in review.

**Implement the compiler in TypeScript.** TypeScript would align with several Native Runtimes, but the compiler is a
repository build tool rather than runtime code. Python minimizes new toolchain coupling and matches existing generators.

# Prior art

The [Agent Plugins specification](https://agent-plugins.org/specification) supplies the canonical package model,
fixed locations for Skills and MCP configuration, client extension namespaces, schema versioning, and path-containment
rules. This RFC adopts it as the portable source rather than inventing another plugin format.

The [Agent Skills specification](https://agentskills.io/) provides the Skill directory and `SKILL.md` contract. It
supports portable Skill content but intentionally does not solve provider packaging, MCP projection, or native
lifecycle integration.

[Dodo Payments' agent plugin](https://github.com/dodopayments/dodo-agent-plugin) is a close operational precedent. It
maintains an Agent Plugins source, provider metadata, and a single generator with drift checking that produces packages
for several hosts. Its move away from symlinked Skill content also supports this RFC's requirement to materialize real
files. PowerContext adopts the build/check pattern without adopting an unrestricted overlay mechanism.

[wshobson/agents](https://github.com/wshobson/agents) demonstrates generation at a larger catalog scale. Its adapter
and capability-oriented tooling projects many plugins into multiple agent harnesses. The relevant lesson is to make
target support explicit and testable instead of embedding target conditions throughout content.

PowerContext already uses the same source/generate/check pattern in `scripts/generate_js_operations.py`, which projects
OpenAPI operations into multiple TypeScript integrations. This RFC generalizes that proven repository convention for
plugin packaging while preserving the OpenAPI contract as the authority for operation identifiers.

# Unresolved questions

No architectural question blocks acceptance of this RFC. Target descriptors define the normative target set; generated
artifacts become committed and drift-checked as each target migrates; version 1 has no free-form Skill extension slot;
compatibility aliases have a minimum two-minor-release and 90-day window; and the capability-manifest boundary is
defined above.

Shared TypeScript or Python runtime libraries, public third-party adapter APIs, and automatic host installation are
separate decisions. They require their own evidence and do not block this distribution model.

# Future possibilities

The same compiler model can support multiple PowerContext plugins, public third-party adapters, manifest-driven support
matrices, marketplace metadata, provenance attestations, and reproducible release bundles. A mature adapter interface
could be extracted into a general Agent Plugins distribution tool if other projects converge on the same needs.

Once migration data shows stable runtime overlap, TypeScript and Python integrations may share small native libraries
without changing the canonical packaging model. After compatibility windows close, generated aliases and legacy
provider shims can be removed, leaving direct Agent Plugins consumption as the preferred path where hosts support it.
