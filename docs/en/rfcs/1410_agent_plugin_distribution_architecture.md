- Proposal Name: `agent_plugin_distribution_architecture`
- Start Date: 2026-08-31
- RFC PR: [oceanbase/powercontext#1410](https://github.com/oceanbase/powercontext/pull/1410)
- Tracking Issue: [oceanbase/powercontext#1405](https://github.com/oceanbase/powercontext/issues/1405)

# Summary

PowerContext uses one conforming [Agent Plugin](https://agent-plugins.org/) as the source for portable integration content. Agent Skills, portable MCP configuration, plugin metadata, and shared names are maintained there.

Every maintained Agent integration uses the same Agent Integration Core for its PowerContext client and standard integration logic. A handwritten Target Hook connects host lifecycle events and APIs to Core Operations. A Target Profile describes that mapping, the capabilities it supports, packaging rules, and target-owned extensions.

A target distribution is built deterministically from those inputs. It is an installable artifact, not another source of truth.

This RFC defines the ownership rules and conformance requirements for that model. It does not prescribe a repository layout, implementation language, command-line interface, template engine, or delivery form for the Agent Integration Core.

# Motivation

PowerContext integrations currently repeat material that is not host-specific:

- Skill guidance and safety rules;
- MCP server identity and connection intent;
- plugin names, versions, descriptions, and repository metadata;
- scope resolution, context preparation, Source capture, budgets, diagnostics, and failure handling;
- operation and tool naming.

The copies already differ in names, MCP shapes, tool prefixes, Skill behavior, and hook behavior. Fixing one integration does not fix the others.

Most maintained Agent plugins already perform the same scope resolution, context preparation, Source capture, request budgeting, diagnostics, and fail-open flow. These are PowerContext integration rules rather than host features. Their overlap is broad enough that separate ownership has no useful maintenance purpose.

Hosts do have real differences. They use different package layouts, lifecycle events, payloads, installation APIs, and user interfaces. Agent Plugins standardizes Skills and MCP servers, but it deliberately leaves hooks and other client extensions to each host. PowerContext therefore needs a common source for shared behavior and explicit host mappings, not a universal host API.

The ownership rule is:

> Maintain portable content and shared behavior once. Describe host differences explicitly. Keep handwritten target code only for behavior that depends on the host.

## Goals and non-goals

This RFC requires maintained Agent distributions to:

- consume common Skills and MCP configuration from the canonical Agent Plugin where the host supports them;
- use the project-maintained Agent Integration Core for PowerContext lifecycle operations they support;
- record component merge rules and hook mappings in a validated target profile;
- build complete target artifacts deterministically from declared sources;
- report capability differences instead of simulating parity.

This RFC does not:

- standardize host lifecycle APIs or require hosts to use the same event names;
- require every host to expose the same capabilities or tools;
- choose a language, package manager, process model, or build command;
- require handwritten hook logic to be generated from templates;
- define automatic installation or a public plugin compiler API;
- govern separately released, reviewed Skills that have their own source and release lifecycle.

## Related work

This RFC sets distribution ownership and projection rules. It relies on related work for adjacent contracts:

| Work item | Responsibility |
| --- | --- |
| [#1244](https://github.com/oceanbase/powercontext/issues/1244) | Provides the existing reusable Agent Plugin. |
| [#1301](https://github.com/oceanbase/powercontext/issues/1301) | Owns multi-host installation and user configuration. |
| [#1357](https://github.com/oceanbase/powercontext/issues/1357) | Owns the versioned integration capability contract and evidence. |
| [#1362](https://github.com/oceanbase/powercontext/issues/1362) | Owns lifecycle behavior and the semantics of common integration operations. |
| [#1378](https://github.com/oceanbase/powercontext/issues/1378) | Owns explicit memory routing in the canonical Skill. |
| [#1397](https://github.com/oceanbase/powercontext/issues/1397) | Owns separately managed Skill package publication and installation. |

# Guide-level explanation

## Mental model

The model has four authoritative inputs:

| Concept | Responsibility |
| --- | --- |
| **Canonical Agent Plugin** | Portable metadata, Skills, MCP configuration, and shared names. |
| **Agent Integration Core** | The common PowerContext client and standard integration logic used by every maintained Agent plugin. |
| **Target Profile** | Declarative packaging, merge, hook, capability, compatibility, and output ownership rules. |
| **Target Hook** | Handwritten code that connects one host's lifecycle events, payloads, results, and APIs to Core Operations. |

The output is a **Target Distribution**. It contains ordinary installable files assembled from portable content, the Agent Integration Core, the Target Profile, and the handwritten Target Hook.

```text
Target Distribution = Project(
    Canonical Agent Plugin,
    Agent Integration Core,
    Target Profile,
    Target Hook,
    Release Metadata,
)
```

Projection preserves one source for each shared component and records target feature differences explicitly.

## Choose the source of a change

Use the narrowest source that still preserves reuse:

| Change | Source |
| --- | --- |
| Portable Skill instructions or assets | Canonical Agent Plugin |
| MCP server identity or portable connection settings | Canonical Agent Plugin |
| PowerContext client, scope, prepare, capture, checkpoint, budget, diagnostic, or fail-open behavior | Agent Integration Core |
| Host event name, payload mapping, output mapping, package field, alias, or generated path | Target Profile |
| Host event registration, payload decoding, lifecycle timing, state, result injection, or host API calls | Target Hook |
| A generated file in a target distribution | None; edit its source and rebuild |

The Agent Integration Core is the default owner of PowerContext integration behavior. Most maintained plugins already repeat the same flow, leaving no useful reason to maintain it independently. A Target Hook owns only the host boundary and depends on the Core; the Core does not depend on target hooks. When a host cannot preserve a Core Operation's semantics, the Target Profile records the constraint and the resulting capability difference instead of adding a separate implementation.

## Map lifecycle hooks

PowerContext defines versioned Core Operations for the behavior it owns. A Target Profile maps host events to those operations and records how the handwritten Target Hook invokes them.

```text
Host Event
  -> Handwritten Target Hook
  -> Agent Integration Core Operation
  -> PowerContext Server
  -> Core Result
  -> Target Hook
  -> Host Result
```

The dependency is one-way: `Target Hook -> Agent Integration Core -> PowerContext Server`. The Core owns scope resolution, request budgets, Server calls, response validation, diagnostics, idempotency, and fail-open behavior. It has no dependency on target hook code.

The Target Hook owns event registration, available host fields, output shape, lifecycle ordering, invocation multiplicity, host state, and result injection. The Target Profile records those choices so the hook's behavior and claimed capabilities can be validated. This is an internal PowerContext contract, not a claim that host lifecycle models are interchangeable.

## Combine plugin components

Combination follows ownership rather than a general deep-merge algorithm:

1. Canonical plugin fields and portable components keep their canonical identity.
2. Target data may add namespaced client extensions and provider fields allowed by the target profile.
3. Agent Integration Core assets come from the declared Core source.
4. Handwritten Target Hook files occupy only paths assigned to that hook.
5. Generated paths are replaced or removed only according to recorded output ownership.
6. An undeclared collision or override is an error.

The target profile records the rule for every place where multiple inputs contribute to one target artifact. The build must reject ambiguous precedence. Arbitrary text patches and unbounded overlays are not valid merge rules.

Installation into an existing user environment is a separate merge boundary. Installers preserve user-owned data and modify only keys or paths whose ownership is declared by the distribution.

## Examples

### Change shared Skill behavior

A correction to project-memory routing belongs in the canonical `powercontext-project-context` Skill. Every target that supports the Skill receives the same corrected content on its next projection. A host-specific invocation hint remains in a declared client extension and cannot replace the canonical safety or routing rules.

### Translate MCP configuration

The Canonical Agent Plugin identifies the MCP server as `powercontext` and declares its portable connection intent. If a host uses a different field for an environment-provided authorization header, its Target Profile declares that mapping. The target keeps the canonical server identity and never places the resolved credential in a source or generated file.

### Reuse a prompt hook

Codex, Claude Code, and WorkBuddy can expose different payload or command forms for a prompt event. Their handwritten Target Hooks map those host events to the same Core Operation. The Agent Integration Core owns context preparation, Source capture, request budgets, diagnostics, and fail-open behavior. Each Target Hook handles its host's input and output shapes, while the Target Profile records and validates the mapping.

## Make a shared change

For a change to a Skill, MCP configuration, Core Operation, Target Hook, or Target Profile:

1. Edit the authoritative source.
2. Build every affected target distribution.
3. Review the source change and projected differences together.
4. Run conformance, contract, and drift checks.
5. Release the source and derived artifacts through the repository's normal process.

The project may provide commands for all targets and for one target, but those command names are not part of this RFC.

## Add or migrate a target

Migration is incremental:

1. Inventory the current target and assign each file to the canonical plugin, Agent Integration Core, Target Profile, Target Hook, or generated output.
2. Add a target profile that describes the current supported behavior without normalizing intentional differences.
3. Replace copied portable content with projection from the canonical plugin and replace duplicated common behavior with calls to the Agent Integration Core.
4. Compare the built distribution with the existing installable artifact and test its observable behavior.
5. Enable drift checks, then remove obsolete maintained copies.

During migration, each path has one source of truth. A target that has not migrated a component may continue to own it, but the profile must record that state. The same component cannot be canonical and target-owned at the same time.

# Reference-level explanation

The terms `MUST`, `MUST NOT`, `SHOULD`, `SHOULD NOT`, and `MAY` are normative.

## Terminology

| Term | Definition |
| --- | --- |
| **Canonical Agent Plugin** | The conforming Agent Plugin that owns portable PowerContext integration content. |
| **Agent Integration Core** | The project-maintained PowerContext client and standard integration logic shared by all maintained Agent plugins. |
| **Core Operation** | A versioned PowerContext integration operation exposed by the Agent Integration Core. |
| **Target Profile** | The validated machine-readable contract for one maintained target. |
| **Target Hook** | Handwritten target code that translates host-specific lifecycle events, payloads, results, and APIs into Core Operation calls. |
| **Projection** | The deterministic process that assembles a target distribution from declared inputs. |
| **Target Distribution** | The complete installable artifact for one target. |
| **Output Ownership Record** | The machine-readable list of generated paths and their source owners. |

## Source ownership

Every maintained integration component MUST have exactly one authoritative source. A generated artifact MUST NOT be an authoritative source and MUST NOT be edited independently.

The project MUST be able to identify the source owner of every generated path without inferring ownership from a file name or directory convention.

## Canonical Agent Plugin

The Canonical Agent Plugin MUST conform to the Agent Plugins specification. Skills and MCP configuration MUST conform to their referenced specifications.

Portable components MUST use stable PowerContext identities. A Target Profile or Target Hook MUST NOT silently rename or replace a canonical Skill, MCP server, or operation.

Host-specific instructions MUST NOT be inserted into the canonical Skill body. A host MAY provide additional guidance through a declared client extension or another target-owned surface. The target profile MUST state how that guidance is loaded and how conflicts with canonical guidance are handled.

When a host cannot preserve a required portable component, the target MUST report it as unsupported. It MUST NOT ship a partial component under the canonical identity.

## Agent Integration Core

The Agent Integration Core is the authoritative implementation of the PowerContext client and standard integration logic used by every maintained Agent plugin. It MUST expose versioned Core Operations and MUST own their observable input, output, idempotency, budget, diagnostic, and failure semantics.

The Core's delivery form is an implementation decision. The project MAY expose the same implementation through a library, executable, service boundary, target-compatible client binding, or another versioned mechanism. This RFC does not require Target Hooks to use the same language or process model.

The Core MUST own common client transport, error normalization, prepared-context validation, scope resolution, context preparation, Source capture, checkpoint and flush sequencing, request budgets, diagnostics, idempotency, and fail-open policy where those behaviors apply. It MUST reuse Server-owned domain operations rather than duplicate Memory, Handoff, persistence, ranking, or rendering policy.

The Core MUST NOT own host event registration, host lifecycle timing, host payload or result shapes, host session state, host user interface, or host privacy and consent controls. It MUST NOT import or depend on Target Hook code.

Every maintained Agent plugin MUST consume the same project-maintained Core implementation. The project MAY package that implementation in multiple target-compatible forms, but those forms MUST come from the same declared Core release and MUST NOT create independent behavior owners. A Target Hook MUST NOT reimplement a Core Operation. If a host cannot invoke an operation with its required data or lifecycle semantics, the target MUST report the capability as unsupported or not applicable.

## Target Profile

Every maintained target MUST have one validated Target Profile. Its serialization format and repository location are implementation decisions.

The profile MUST declare:

- target identity and the referenced capability record;
- projected canonical components and any explicit omissions;
- Core Operations used by the target;
- lifecycle hook mappings;
- component merge and conflict rules;
- target-owned extension data and Target Hook inputs;
- compatibility aliases and their lifecycle;
- generated output ownership;
- independently owned release metadata, if any.

The profile MUST NOT contain secrets, resolved credentials, executable business logic, Skill bodies, complete MCP documents, or arbitrary text patches.

## Target Hook

A Target Hook MUST be handwritten at the host boundary. It owns host event registration, payload decoding, lifecycle timing and multiplicity, host state, result injection, host API calls, and host-specific privacy or consent behavior. It MUST call Core Operations for PowerContext integration behavior and MUST NOT fork portable content or Core behavior.

The Target Hook depends on the Agent Integration Core. The Core MUST NOT depend on the Target Hook. Target Hooks MUST have focused tests at the host boundary. A hook's existence does not create a capability; the declared hook mapping and behavioral evidence remain authoritative.

## Hook mapping

For every Core Operation covered by the referenced capability contract, the Target Profile MUST declare:

- the host event or invocation surface;
- the Target Hook entry point and input mapping;
- the result mapping and injection surface;
- timing, ordering, and multiplicity constraints that affect behavior;
- failure and diagnostic behavior;
- whether the operation is supported, unsupported, or not applicable.

Silent omission is an error. A hook mapping MUST NOT claim semantic equivalence when the host event lacks data or timing required by the Core Operation. Hook conformance validation MUST reject a supported capability unless its Core Operation, hook mapping, and host-boundary behavioral evidence are all present and mutually consistent.

## Merge rules

Merge behavior MUST be explicit and bounded. Canonical core fields remain canonical. Target-specific manifest data and files MUST use a namespace or ownership boundary defined by the host and recorded in the profile.

If two inputs claim the same field or output path, the build MUST apply a declared rule or fail. It MUST NOT choose a winner from file order, directory order, discovery order, or adapter implementation detail.

Removal of stale generated files MUST be limited to paths in a previous Output Ownership Record and revalidated inside the target output root. A build MUST NOT recursively clean a mixed-ownership integration directory.

## Capabilities

Target profiles MUST reference the versioned integration capability contract owned by [#1357](https://github.com/oceanbase/powercontext/issues/1357). They MUST NOT create a second capability vocabulary.

A projected capability requires all of the following:

- the canonical component or Core Operation provides the behavior;
- the Target Hook mapping preserves the required semantics;
- the target capability record declares support;
- focused tests provide behavioral evidence.

Files, tool counts, or adapter branches are not capability evidence.

## Deterministic projection

Projection MUST be a pure function of versioned repository or release inputs. It MUST NOT depend on network access, wall-clock time, locale, ambient user configuration, resolved secrets, or undeclared environment state.

Identical inputs MUST produce byte-identical normalized outputs. The build MUST use stable serialization and MUST reject absolute, escaping, duplicate, symlinked, or undeclared output paths.

Determinism applies to the complete Target Distribution, including packaged Agent Integration Core assets, handwritten Target Hook sources or built outputs, provider manifests, and outputs from any target-specific build step. A target build tool MAY be used, but its inputs, version, and outputs MUST be declared and reproducible.

Projection SHOULD generate structural artifacts such as portable plugin content, provider manifests, operation maps, representable hook registrations, capability matrices, and output ownership records. It MUST NOT generate handwritten Target Hook business logic. Determinism means that a complete distribution is reproducible from declared inputs; it does not require every input to be generated.

The project MUST provide a write mode and a non-writing check mode. Check mode MUST report drift, stale owned files, undeclared omissions, invalid mappings, and nondeterministic output. The exact commands are implementation details.

## Naming and compatibility

The canonical names are:

| Entity                | Canonical form                                    |
| --------------------- | ------------------------------------------------- |
| Plugin                | `powercontext`                                    |
| Project context Skill | `powercontext-project-context`                    |
| MCP server            | `powercontext`                                    |
| API operation         | OpenAPI `<operation_id>`                          |
| MCP tool              | `<operation_id>` within the `powercontext` server |
| Native global tool    | `powercontext_<operation_id>`                     |

A target MUST preserve these names where its host can represent them. Compatibility aliases MUST be explicit in the Target Profile, have a documented introduction and removal policy, and remain separate from canonical identities.

## Generated artifacts and releases

A project MAY commit generated distributions, produce them during release, or use both approaches. In every case, the released artifact MUST be reproducible from declared inputs and MUST pass check mode.

A generated artifact MUST contain ordinary files. Installation MUST NOT depend on repository symlinks, submodules, or an undeclared local build.

Canonical plugin versions and independently released Agent Integration Core versions MAY differ. The Target Profile MUST declare which source owns each version field so that one version is not presented as another.

## Security

Projection is packaging, not authorization. A generated declaration does not grant a host permission or access that its runtime and user configuration do not provide.

Builds MUST enforce source and output path containment. Sources, profiles, generated artifacts, and diagnostics MUST NOT contain resolved credentials, access tokens, prompt bodies, stored memories, or other user content.

Profiles MAY refer to environment variable names or provider secret stores. They MUST NOT contain credential values.

## Conformance

A maintained target conforms to this RFC when:

- portable Skills and MCP configuration come from the Canonical Agent Plugin;
- every declared lifecycle capability uses the relevant Core Operation from the shared Agent Integration Core;
- hook, capability, and merge mappings are explicit, mutually consistent, and validated;
- every generated path has one source owner;
- the complete distribution is reproducible;
- check mode detects drift and invalid ownership;
- focused tests verify the declared host behavior and failure semantics.

# Drawbacks

The model adds a profile schema, build checks, a shared Agent Integration Core, and host-boundary tests. Provider format changes may require Profile or Target Hook updates even when PowerContext behavior does not change. Migration also produces large diffs while copied files move to generated ownership.

These costs are visible and testable. Independent copies have lower tooling cost but make behavioral drift part of normal maintenance.

# Rationale and alternatives

The design keeps the Agent Plugins package useful on its own, gives common client and integration behavior one implementation, and keeps host differences in a validated Profile and handwritten Target Hook. The one-way dependency preserves a clear boundary without claiming that host lifecycle models are interchangeable.

Without this design, portable files and hook behavior remain independent maintenance surfaces. Drift checks can catch copied files, but they cannot establish common ownership or prevent equivalent integration behavior from diverging.

## Maintain every distribution independently

This avoids a projection system but keeps the current drift and repeats every shared correction.

## Share only Skills and MCP configuration

This uses the portable part of Agent Plugins but leaves client orchestration, diagnostics, scope, and failure behavior in separate implementations. It does not satisfy the common implementation rule.

## Define a universal host lifecycle API

Host events differ in timing, payload, and guarantees. PowerContext needs hooks that call its own Core Operations, not a new standard for host runtimes.

## Generate Target Hook business logic from templates

Templates can hide duplicated behavior while making host lifecycle code harder to review. This standard shares common behavior through the Core, keeps hooks handwritten at the host boundary, and uses generation for structural artifacts and validation data.

## Use an unrestricted overlay or deep merge

An overlay makes precedence difficult to audit and allows canonical content to fork inside target configuration. This RFC uses explicit ownership and bounded merge rules.

# Prior art

The [Agent Plugins specification](https://agent-plugins.org/specification) defines a portable package with fixed locations for Agent Skills and MCP configuration. It also defines namespaced client extensions without assigning them portable lifecycle semantics. This RFC uses that boundary rather than extending the portable core with PowerContext hook behavior.

The [Agent Skills specification](https://agentskills.io/) defines the Skill directory and `SKILL.md` contract. It does not define MCP projection, hook behavior, or host package assembly.

PowerContext already projects OpenAPI operation metadata into integration code and checks the result for drift. The same source, projection, and check pattern applies here, with additional ownership and lifecycle mapping rules.

# Unresolved questions

No unresolved question blocks acceptance of the ownership and projection model. The following implementation choices remain outside this RFC:

- the Agent Integration Core delivery form and language bindings;
- the Target Profile serialization and schema location;
- build command names and internal projection architecture;
- which targets and Core Operations migrate first;
- whether generated distributions are committed, produced during release, or both.

Those choices must satisfy this RFC's ownership, mapping, determinism, and conformance rules. They do not change the architecture described here.

# Future possibilities

The same model can support more PowerContext plugins, additional Core Operations, public Target Profiles, and reproducible release attestations. A projection tool may later become a reusable package if other projects adopt the same ownership and mapping rules.
