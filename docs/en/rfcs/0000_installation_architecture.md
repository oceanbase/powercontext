- Proposal Name: `installation_architecture`
- Start Date: 2026-08-31
- RFC PR: [oceanbase/powercontext#0000](https://github.com/oceanbase/powercontext/pull/0000)
- Related RFCs: [RFC 1299](1299_local_server_availability_and_service_installation.md)

# Summary

PowerContext will make personal distribution installation the responsibility of a standalone, distribution-owned
installer. The installer will install a compatible PowerContext Runtime and an explicit set of Agent host integrations
from one immutable Release Manifest. The Runtime CLI will continue to run, configure, and diagnose PowerContext, but it
will no longer download source repositories, build integration artifacts, or install and update host plugins.

An installation is resolved from three inputs:

```text
Release Manifest + Runtime Profile + selected Hosts = Installation Plan
```

The installer will preflight the complete plan, install every component through an integration-specific adapter,
verify observable host state, and record exact component results. Repeating the same plan will be idempotent. A failure
in one host integration will not erase a successfully installed Runtime or another host, and the same plan can be run
again to recover.

The existing `powercontext setup` command group will be deprecated and then removed. `powercontext config`,
`powercontext doctor`, content commands, and `powercontext server` remain Runtime CLI responsibilities. This RFC does
not make personal Server service registration implicit and does not replace the explicit service lifecycle defined by
RFC 1299.

# Motivation

PowerContext currently exposes its repository and implementation layout as part of the normal installation path. A
user first installs the Python application from a Git ref and then invokes one or more host-specific setup commands:

```text
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext setup codex --source oceanbase/powercontext --ref master
powercontext setup opencode --source oceanbase/powercontext --ref master
```

This path requires users to coordinate the Runtime requirement, Git source, Git ref, selected host integrations, host
prerequisites, and update commands. Updating the Runtime does not update its integrations automatically. Repeating a
setup command is the documented refresh mechanism, but its relationship to the installed Runtime version is a user
convention rather than a release contract.

The implementation has the same coupling. The Runtime CLI acts as:

- an interactive host catalog;
- a Git source parser and checkout cache;
- a JavaScript plugin builder;
- a native host package-manager client;
- a filesystem installer and configuration merger;
- a rollback coordinator; and
- an installation diagnostics surface.

These responsibilities are duplicated across host modules. A new first-class host requires changes to the catalog,
single-host command surface, multi-host dispatch, post-install dispatch, errors, output, tests, and documentation. Some
hosts already provide a native marketplace or package manager, while others are installed by copying repository files
or building source on the user's machine. Treating all of these paths as Runtime CLI behavior makes the public CLI and
the release topology evolve together.

Installation, operational configuration, and diagnostics also have different lifecycles:

- installation places a versioned component and registers it with a host;
- configuration controls mutable values such as Server URL, scope, and capture policy; and
- diagnostics observe the resulting environment without changing it.

Combining them forces users to rerun an installer to change configuration and tempts diagnostics to become repair
commands. PowerContext needs one durable boundary before adding more host integrations or another installation mode.

The intended outcome is:

- one entry point for a ready-to-run personal installation;
- one immutable compatibility coordinate for the Runtime and integrations;
- explicit interactive and non-interactive host selection;
- no release plugin builds from a moving repository checkout;
- idempotent retry and upgrade behavior with component-level results;
- a smaller Runtime CLI whose installation-independent commands remain usable; and
- a release contract that can later support additional platform entry points without duplicating installation policy.

# Guide-level explanation

## The Installation Plan

The installer turns three user choices into an Installation Plan.

A **Release Manifest** identifies one immutable PowerContext release and the compatible integration artifacts shipped
with it. A **Runtime Profile** selects the PowerContext application roles and optional local backend. The initial
profiles are:

- `local`, containing the CLI and ready-to-run Server with the default SQLite-backed Runtime; and
- `seekdb`, extending `local` with the embedded seekDB backend on qualified platforms.

A **Host Selection** is an explicit set of Agent hosts whose PowerContext integrations should be installed. It is
independent of the Runtime Profile. Selecting `seekdb` does not imply Codex, and selecting Codex does not change the
database backend.

The resolved plan contains exact versions and artifacts. `latest` may be used to discover a release, but it is replaced
by an exact release identity before any installed state changes.

## Interactive installation

On a supported macOS or Linux system, a new user starts the canonical installer:

```text
curl -fsSL https://oceanbase.github.io/powercontext/install.sh | sh
```

The bootstrap obtains `uv` when necessary and starts the standalone installer. The installer presents the Runtime
Profile and supported host catalog. Host detection annotates choices with observable prerequisites, such as whether the
host CLI is available, but it does not silently select or omit a host.

Before mutation, the installer prints the resolved release, Runtime Profile, selected hosts, target directories, and
any unsupported selections. The user confirms that Installation Plan once. The installer then installs the Runtime,
installs each selected integration, and verifies every component.

A successful report resembles:

```text
Release       0.1.0
Runtime       installed  local
Codex         installed  powercontext 0.2.0
Claude Code   skipped
OpenCode      installed  powercontext-opencode 0.0.1
```

The report ends with operational next steps, such as `powercontext config init`, `powercontext server run`, and
`powercontext doctor`. It does not register or start a persistent personal service automatically.

## Non-interactive installation

Automation selects every variable explicitly:

```text
curl -fsSL https://oceanbase.github.io/powercontext/install.sh | sh -s -- \
  --version 0.1.0 \
  --profile local \
  --host codex \
  --host claude-code
```

For a clean installation without a terminal, omitting a Runtime Profile or omitting both `--host` and an explicit
`--no-hosts` is an error. For an existing installation, omitting host arguments reuses the recorded Host Selection. The
installer does not infer an installation set from `PATH`. Structured output is available for automation and contains
the same component facts as human output.

## Repeating and upgrading an installation

The installer records the exact successful Installation Plan and component results under the distribution-owned
installation root. Repeating that plan succeeds without semantic changes when every component is current.

Selecting a newer release creates a new plan. Its Host Selection starts with every successfully recorded host and adds
any newly selected hosts. The installer preflights the complete set before replacing the Runtime, then reconciles every
host to artifacts declared by the same release. User data, Server databases, and operational configuration are outside
the installation root and are not replaced.

Omitting a previously installed host from a later command does not uninstall it. Removal is never inferred from
absence. A downgrade requires an explicit version and fails before mutation when the target manifest does not declare
a compatible migration path.

## Recovering from partial failure

The Runtime and each host integration are separate installation transactions. If OpenClaw fails after the Runtime and
Codex succeed, the installer reports:

```text
Runtime       current
Codex         current
OpenClaw      failed    host version is below the declared minimum
```

The Runtime and Codex remain usable. After updating OpenClaw, the user repeats the same command. Current components are
verified without unnecessary replacement, and the OpenClaw adapter retries its transaction.

The installer never reports the whole plan as rolled back when independently observable host state has committed. It
exits nonzero if any selected component is unsupported or failed and preserves the per-component result needed for
recovery.

## Existing installations

The standalone installer initially coexists with `powercontext setup`. Existing installations and host plugins keep
working. During the compatibility period, each `setup` command prints a deprecation message with the equivalent
installer selection and retains its current behavior.

After the compatibility period, the `setup` command provider and its installation implementations are removed from the
Runtime CLI. Existing databases, configuration, marketplaces, host packages, hooks, and Skills are not removed. A user
adopts installer ownership by running an Installation Plan containing the already installed hosts; each adapter first
recognizes and verifies compatible PowerContext-owned state before reconciling it.

# Reference-level explanation

## Goals and non-goals

This RFC has the following goals:

- define one personal distribution installation contract;
- assign installation ownership outside the Runtime CLI;
- install the Runtime and host integrations from one compatible release;
- make installation idempotent, recoverable, and observable;
- use native host installation mechanisms where they provide the required contract; and
- define a conformance boundary for every advertised platform and host combination.

The following are not goals:

- installing Agent host applications;
- managing production or privileged deployments;
- changing integration runtime protocols or PowerContext data contracts;
- automatically installing, publishing, approving, or executing managed Skills;
- providing an atomic transaction across unrelated host package managers;
- replacing Python dependency management for applications importing a PowerContext SDK role; or
- changing the explicit personal-service lifecycle accepted by RFC 1299.

## Responsibility boundaries

The installation architecture has these layers:

```text
Release Pipeline
    |
    v
Immutable Release Manifest
    |
    v
Bootstrap -> Installer Engine
                 |
                 +---- Runtime Environment
                 |
                 +---- Integration Adapters
                            |
                            v
                   Native Host Interfaces
```

| Concern | Owner | Required behavior |
| --- | --- | --- |
| Build and publish versioned artifacts | Release Pipeline | Produce one internally compatible release and its digests |
| Obtain `uv` and start the installer | Bootstrap | Remain small and contain no host transaction logic |
| Resolve, execute, and record a plan | Installer Engine | Enforce manifest, ownership, retry, and result contracts |
| Register one integration | Integration Adapter | Use the host-native interface or an owned atomic file transaction |
| Run and configure PowerContext | Runtime CLI | Avoid modifying its own distribution or host package state |
| Observe installed state | CLI diagnostics | Remain read-only and report facts independently |
| Persistently launch a personal Server | RFC 1299 service layer | Remain explicit, opt-in, and separate from distribution installation |
| Run a managed deployment | Operator or orchestrator | Use deployment-owned packaging, configuration, and lifecycle |

The Installer Engine is a standalone release artifact. It is not imported by `powercontext server`, Client SDKs,
integration hooks, or normal CLI startup. Integration Adapters are installer extensions, not Runtime CLI command
providers.

## Release artifacts

A release qualified for personal installation contains:

- the PowerContext wheel and exact Runtime requirement;
- the standalone Installer Engine;
- one Release Manifest;
- every integration artifact advertised as supported by that manifest;
- cryptographic digests for downloaded executable or installable content; and
- the compatibility and minimum-host-version metadata required for preflight.

Formal release artifacts must not require a checkout of the repository or a local JavaScript build. A native
marketplace coordinate is an acceptable artifact identity when the marketplace can resolve an immutable version. A
mutable branch such as `master` or `main` is not a release identity.

The release pipeline publishes all required artifacts before publishing the discoverable release index. A release is
not advertised by the installer until its manifest and required artifacts pass release verification.

## Release Manifest contract

The Release Manifest is immutable after publication and contains at least:

- `schema_version`;
- release name and package version;
- Installer Engine locator and digest;
- Runtime requirements by profile;
- supported platforms;
- integration identifiers and display names;
- adapter kinds and artifact locators;
- artifact versions and digests where applicable;
- host executable and minimum-version constraints; and
- compatibility or migration constraints.

Profile and integration identifiers are stable lowercase kebab-case names. Exactly one Runtime Profile may be marked
as the interactive default. No Host Selection is an implicit default.

An unsupported manifest schema, duplicate identifier, unsafe package specification, missing digest, or inconsistent
release identity fails resolution before mutation. Unknown optional metadata may be ignored only when the manifest
schema marks it as non-normative.

A mutable channel index may map `latest` to a manifest URL and digest. The resolved Installation Plan records the exact
manifest identity and never records `latest` as installed state.

## Bootstrap and trust boundary

The shell bootstrap performs only these operations:

1. validate its arguments and platform;
2. obtain or locate `uv`;
3. resolve a channel or exact release to a manifest;
4. download and verify the matching Installer Engine; and
5. execute that engine with the original installer arguments.

PowerShell or package-manager entry points added later must start the same Installer Engine rather than reimplement its
policy. A bootstrap may update the current process `PATH`, but persistent shell mutation is best-effort and reported
separately from installation success.

The canonical bootstrap origin, channel index, Release Manifest, and artifact hosts form the installation trust
boundary. Redirects to origins outside the manifest allowlist fail. Checksums are verified before executable content is
run or passed to a host package manager. Output never contains credentials, complete environments, or authenticated
artifact URLs.

## Installation lifecycle

The Installer Engine executes one plan through these phases:

```text
resolve -> preflight -> install runtime -> install integrations -> verify -> record
```

`resolve` is read-only and produces exact components. `preflight` is read-only and validates platform support, host
availability, versions, paths, artifact reachability, ownership conflicts visible before installation, and available
disk space where it can be determined reliably.

Runtime installation commits before integration installation because integration commands and diagnostics may use the
new Runtime executable. Each integration then executes as an independent ordered transaction. Verification observes
the same public host state used by `powercontext doctor`; it does not trust only the adapter's command exit status.

The state record is written atomically after each committed component. An interruption can therefore distinguish
unattempted, uncertain, and verified components. An uncertain component is verified before any retry. The installer
does not repeat a destructive action merely because the previous process did not record its result.

## Runtime environment and state

The distribution owns one per-user application environment and one executable link. Platform-qualified paths are
resolved through the Installer Engine rather than embedded independently in every bootstrap. On Unix-like systems the
expected shape is:

```text
<installation-root>/venv/
<installation-root>/installer/state.json
<user-executable-dir>/powercontext -> <installation-root>/venv/bin/powercontext
```

The state record contains the manifest digest, release identity, Runtime Profile, successful component identities,
adapter result metadata needed for later ownership checks, and the last verification status. It contains no
credentials.

PowerContext application data, Server databases, generated configuration, logs, and integration-owned runtime state
remain outside the installation root. Replacing the application environment must not delete or migrate those paths
implicitly.

## Integration Adapter contract

Every advertised integration supplies an adapter implementing four logical operations:

```text
preflight -> install -> verify -> describe result
```

An adapter must:

- validate the host executable and supported version before mutation;
- consume only artifacts selected by the resolved manifest;
- prefer a native marketplace, package manager, or plugin command;
- validate PowerContext ownership before replacing filesystem or configuration state;
- make a single-host installation idempotent;
- preserve or restore the previous valid owned state when its transaction fails before commit;
- return an uncertain result when external command completion cannot be established;
- verify through observable host state after commit; and
- provide a content-free, actionable failure without leaking host credentials.

An adapter may merge required registration into host configuration when the host has no package interface. Such a
merge must preserve unrelated values, write atomically, and keep enough ownership metadata to distinguish a future
PowerContext update from a foreign entry. Shell text substitution is not an acceptable configuration transaction.

## Integration distribution matrix

The initial target architecture is:

| Host | Distribution unit | Installation owner | Mutable configuration owner |
| --- | --- | --- | --- |
| Codex | Versioned marketplace plugin | Codex CLI | Plugin configuration |
| Claude Code | Versioned marketplace plugin | Claude Code CLI | Claude Code settings |
| DeepSeek Harness | Versioned package or release bundle | DSH CLI | Environment or DSH configuration |
| OpenClaw | Published package | OpenClaw CLI | OpenClaw configuration |
| OpenCode | Published package or release bundle | Native config or installer adapter | OpenCode configuration |
| Pi | Versioned Pi package | Pi CLI | Environment or Pi configuration |
| Hermes | Versioned package or release bundle | Hermes CLI or installer adapter | Hermes configuration |
| WorkBuddy | Versioned integration bundle | Installer adapter | WorkBuddy settings and MCP configuration |

This table is a release requirement, not a claim that every row is immediately qualified. An integration appears in a
Release Manifest only after its distribution unit and adapter pass conformance. Until then, its existing manual guide
may remain available, but the installer must not advertise it as supported.

## Configuration boundary

Installation establishes that a selected component exists, belongs to PowerContext, and is discoverable by its host.
Operational configuration establishes how that component connects and behaves. Server URL, scope selection, capture
policy, model credentials, and database configuration are not component identities.

The installer may collect initial non-secret configuration or invoke a host's native configuration interface, but the
same values remain editable later without reinstalling the component. `powercontext config` or the host-native settings
surface owns those changes. The installer never copies the caller's complete process environment into host settings.

`powercontext doctor` and host-specific diagnostics remain read-only. They expose the facts used by installer
verification but do not download, repair, enable, or replace a component.

## Ownership, idempotency, and rollback

Native host package-manager identity is the preferred ownership proof. For direct filesystem installation, an adapter
uses a versioned manifest containing the PowerContext owner, integration identifier, release identity, installed
artifact digest, and owned file set.

An adapter may replace current or stale PowerContext-owned state. It refuses to overwrite a foreign package, file,
directory, configuration entry, or locally modified owned artifact unless the user supplies a future explicit recovery
operation. Name similarity is not ownership proof.

The Runtime and every integration define separate commit boundaries. Failure before a component commits restores its
previous valid owned state where the host interface permits. Failure after an external package manager commits retains
that state and reports verification failure. The installer never claims a cross-host rollback that it cannot observe or
enforce.

Repeated installation of the same desired state succeeds. Reconciliation may avoid writes when versions, digests,
registration, and verification are current. Idempotency does not mean suppressing verification.

## Versioning and upgrade

One Installation Plan contains exactly one PowerContext release identity. All integration artifacts are selected from
that release's manifest even when individual plugin versions use independent number spaces.

An upgrade resolves and preflights the complete target plan before replacing the Runtime. The plan carries forward all
successfully recorded hosts; a host does not leave the compatibility set merely because the user omitted a `--host`
argument. If any recorded or newly selected host is incompatible with the target release, the installer stops before
mutation. The initial contract provides no partial Runtime upgrade that knowingly retains an incompatible integration.

A downgrade is explicit and supported only when the target manifest accepts the installed application-data format and
every selected integration can be reconciled safely. The installer never downgrades or deletes Server data. Data-format
migration remains owned by the Runtime and requires a separate compatibility contract.

## Result and exit contract

Each selected component finishes with one of these states:

```text
unsupported | skipped | installed | current | stale | failed | uncertain
```

Human and structured output contain the same component identity, desired version, observed version when available,
state, and recovery action. `failed` means the adapter established that its desired state did not commit. `uncertain`
means an external operation may have committed and verification could not establish the result. The next run verifies
an uncertain component before mutation.

The installer exits zero only when the Runtime and every selected component are `installed` or `current`. A skipped
component not selected by the user does not affect exit status. Unsupported, stale, failed, or uncertain selected
components produce a nonzero exit.

## Compatibility and migration

The new installer ships before the Runtime `setup` command is removed. During one documented compatibility window:

- existing setup commands retain their behavior;
- each setup command emits a deprecation message and equivalent Host Selection;
- installer adapters recognize compatible state created by setup; and
- documentation presents the standalone installer as the normal path.

After the window, the `setup` CLI entry point, multi-host setup dispatcher, Git checkout installers, and setup-only
result models are removed. Read-only diagnostic implementations remain and may move to modules that do not import
installer code.

This RFC supersedes RFC 1299 only where that RFC describes the then-current assumption that setup continues to install
integrations. It retains RFC 1299's separation between distribution ownership and Server execution, including the
explicit `powercontext service install`, `status`, and `uninstall` lifecycle. The distribution installer may recommend
service installation but never performs it implicitly.

Contributor installation from a checkout remains a development workflow through repository commands such as
`make install`. Python applications continue to add Client, builtin, or framework integration packages to their own
project environments. Neither workflow is redirected through the personal distribution installer.

Offline customer bundles may provide their own bootstrap origin and manifest, but must preserve the same plan,
ownership, verification, and result contracts. They do not need network access after their bundle integrity is
verified.

## Conformance

A combination is advertised as supported only after the following matrix passes for its qualified platform, Runtime
Profile, and host version:

```text
Platform x Runtime Profile x Integration Adapter x Lifecycle Scenario
```

Required lifecycle scenarios include:

- clean interactive and non-interactive installation;
- installation without an existing Python or `uv` executable;
- exact-plan repetition;
- upgrade from the previous supported release;
- explicit supported downgrade where declared;
- missing or too-old host executable;
- artifact digest or manifest validation failure;
- interruption before and after each component commit;
- recovery of an uncertain external command;
- foreign and locally modified artifact conflicts;
- one integration failure after Runtime success;
- structured and human result equivalence; and
- preservation of application data and unrelated host configuration.

Shell syntax tests and PowerShell parser tests are necessary but do not qualify a platform. A platform entry point ships
only with an end-to-end clean-install and upgrade job on that operating system.

# Drawbacks

The proposal creates a new release surface consisting of bootstraps, an Installer Engine, manifests, integration
artifacts, and cross-platform conformance jobs. Release publication becomes an ordered transaction and cannot declare a
version complete after publishing only the Python wheel.

Moving installation out of the Runtime CLI does not eliminate every host-specific adapter. WorkBuddy and hosts without
a sufficient native package interface still require ownership-aware file or configuration transactions. This code is
better isolated, but it remains maintenance work.

Component-level commit boundaries make failure truthful and recoverable, but they do not provide the simplicity of a
single all-or-nothing transaction. Users may see a usable Runtime beside a failed selected integration and must follow
the reported recovery action.

An immutable compatibility manifest reduces drift at the cost of preventing casual combinations of a Runtime from one
commit and plugins from another. Contributors retain source workflows, but release users lose `master` as a supported
installation version.

# Rationale and alternatives

## Keep installation in `powercontext setup`

This preserves current code and allows the installed CLI to reuse Python for complex host transactions. It also keeps
distribution, source checkout, host mutation, configuration, and diagnostics in one public runtime surface. Adding a
new entry point without changing that ownership would not address the coupling motivating this RFC.

## Wrap `powercontext setup select` in a shell installer

This would quickly provide a one-line experience, but the bootstrap would first install the Runtime and then invoke the
same setup graph. Runtime and integration compatibility would remain a user convention, and all existing setup code
would remain public. It is a valid transition step but not the accepted target architecture.

## Implement every installer in shell and PowerShell

Shell is appropriate for bootstrap, not for manifest validation, multi-file configuration transactions, ownership
proof, structured state, or recovery. Independent shell and PowerShell implementations would duplicate the most
sensitive policy and drift across platforms.

## Ship the Installer Engine inside the Runtime CLI package

A separate module in the same wheel could improve internal structure, but the Runtime would still distribute its own
installer and retain installer dependencies and public coupling. A standalone release artifact gives installation an
independent lifecycle while allowing one implementation to serve every bootstrap.

## Delegate all behavior to native host package managers

Codex and Claude Code are close to this model, but not every supported host provides a sufficient versioned package,
configuration, verification, or rollback interface. Native interfaces remain preferred adapter mechanisms; they cannot
replace the common Installation Plan and result contract.

## Standalone installer with an immutable manifest

This design adds a release component but creates the clearest ownership boundary. It supports one compatibility
coordinate, keeps host-specific mutation isolated, avoids duplicating policy in platform bootstraps, and lets the
Runtime CLI evolve around operational behavior rather than distribution mechanics.

# Prior art

Bub's standalone installer bootstraps `uv`, creates a dedicated environment, resolves a versioned preset catalog, and
supports interactive and non-interactive selection. It demonstrates that plugin choice can occur before normal Runtime
use and that installer contracts can be tested with fake external commands.

Bub still invokes `bub install` to mutate plugin dependencies and currently allows plugin coordinates that follow a
moving branch. PowerContext adopts the external orchestration and catalog lessons but not the Runtime CLI ownership or
mutable release identity. Its integrations also target heterogeneous host package systems rather than one shared Python
environment, so Host Selection remains independent of Runtime Profile.

Codex and Claude Code marketplaces demonstrate the preferred adapter boundary: the host owns plugin registration and
discovery while PowerContext supplies a versioned plugin. Existing PowerContext filesystem installers show why an
ownership and rollback contract is still needed when a host lacks that boundary.

# Unresolved questions

- Which signing or provenance mechanism, beyond mandatory digests and HTTPS origins, qualifies the first public Release
  Manifest?
- Which host integrations have release artifacts mature enough to appear in the first manifest, and which remain
  documented manual installations?
- How long is the `powercontext setup` compatibility window, and which release removes it?
- Does the initial supported downgrade contract include only application code, or does it require a Runtime-owned data
  compatibility declaration first?
- Which Windows Runtime Profile and host combinations can pass conformance before a public `install.ps1` is advertised?

# Future possibilities

The release index and manifest can support signed provenance, mirrored artifact origins, enterprise allowlists, and
policy-selected channels without changing the Installation Plan.

Package-manager frontends such as Homebrew or WinGet can resolve the same manifest and start the same Installer Engine.
They do not need separate host integration semantics.

A future explicit repair or uninstall plan can use recorded ownership and component results. Absence from an install or
upgrade plan will continue not to imply removal.

Third-party integrations may eventually provide independently signed adapters and artifacts through a registry. That
requires a separate trust, compatibility, and extension API design and is not implied by the first-party manifest
schema in this RFC.
