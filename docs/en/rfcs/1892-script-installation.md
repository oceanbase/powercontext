---
title: Script Installation
description: Install the latest or an exact release with Bash and PowerShell, independent package and runtime mirrors, and explicit Agent selection.
---

- Proposal Name: `script_installation`
- Start Date: 2026-10-09
- RFC PR: [oceanbase/powercontext#1892](https://github.com/oceanbase/powercontext/pull/1892)
- Related RFCs: [RFC 1733](1733-usability-and-agent-workflows.md), [RFC 1299](1299_local_server_availability_and_service_installation.md)

# Summary

Provide distribution-owned Bash and PowerShell entry points that install PowerContext through uv. The default
release is the latest stable version from the selected package index; `--version` selects an exact release.
The scripts provision missing uv and Python, install the selected Runtime profile, and optionally invoke the
existing Agent integration adapters with a tag matching the installed Runtime.

Installation, configuration, diagnostics, and service operation have separate responsibilities. Installing software
does not configure inference providers, start a Server, or register a persistent service by default. Explicit
`--configure` and `--service` compose those existing CLI operations using one selected `--env-file`. The scripts and their
installation guide are the first recommended path in the READMEs and Quick Start.

# Motivation

A normal installation should not require users to coordinate Python discovery, uv installation, package extras,
package indexes, and integration Git refs. Package downloads, uv binaries, and Python distributions also use different
sources: changing the PyPI index alone cannot fix an inaccessible Python download.

Installation places a versioned component and registers explicitly selected integrations. Configuration controls
mutable values such as Server URL, Scope, and capture policy. Diagnostics observe the resulting environment.
Keeping these responsibilities distinct lets installation improve independently of configuration storage, shared
Hooks, an operations tool, or service lifecycle changes.

The intended outcome is one installation entry point per shell, reuse of existing dependencies, predictable version
selection, explicit host selection, recoverable partial failures, and verification through the installed product.

# Guide-level explanation

## Install a Runtime profile

On macOS or Linux:

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash
```

On Windows, using PowerShell 5.1 or newer:

```powershell
powershell -ExecutionPolicy Bypass -c "& ([scriptblock]::Create((irm https://powercontext.oceanbase.io/install.ps1)))"
```

`local`, the default profile, installs CLI, Client, and local Server dependencies. `--profile client` installs CLI and
Client dependencies for an existing Server. Profile and host selection are independent: choosing a database role does
not select an Agent host, and selecting Codex does not change the Runtime profile.

The installer reuses uv and compatible system or uv-managed Python. Otherwise it provisions uv and Python 3.12 in
user-owned locations. It prints the actual Runtime version, profile, executable directories, PATH command, and next
configuration command. It does not change persistent PATH settings. Apply the printed PATH command before continuing.

## Select releases and integrations

`--version latest` is the default. Repeating it checks for newer stable releases on the configured index. For a
repeatable package selection, use an exact version:

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- --version 1.2.0 --host codex
```

Installation runs unattended by default, without Git or a host picker. `--host` is repeatable and opts into integration
setup; selected hosts require Git and their own prerequisites. `--no-hosts` explicitly states the default and cannot
be combined with `--host`. For an interactive picker after installation, run
`powercontext setup select --ref "powercontext-v$(powercontext --version)"`.

The installed CLI supplies the exact version for `powercontext-vVERSION`. Host setup never resolves `latest` again
and never defaults to `master`. If a host installation fails, the Runtime remains installed and the script returns
nonzero with instructions to retry setup at that tag. Each adapter supplies its existing host-specific result.

For personal macOS/Linux, configure a protected file, then run `powercontext service install --env-file .env`,
`powercontext service status`, and `powercontext doctor --env-file .env`. The native user manager owns the Server lifecycle.
Development, debugging, temporary use, and unavailable managers keep the foreground `server run` path.
Client-only installations follow the remote connection guide. Service registration remains explicit.

## Explicit configuration and personal service setup

The installer supports three composable options:

| Option | Contract |
| --- | --- |
| `--configure` | Run `config init --require-write --output PATH` through a controlling terminal |
| `--service` | Validate the selected file, install the native user service, inspect status, and diagnose readiness |
| `--env-file PATH` | Select the same explicit configuration for configuration, service, diagnostics, and host setup |

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- \
  --configure --service --env-file "$HOME/.config/powercontext/powercontext.env"
```

`--configure` and `--service` require the local profile and an explicit file. Unattended setup requires an existing
file and omits `--configure`. Invalid combinations, a missing existing file, and unavailable interactive input fail
before Runtime installation. The Bash wizard receives `/dev/tty` independently of the downloaded script's stdin;
PowerShell requires an interactive console. Ordinary installation remains unattended.

The configuration CLI exposes `--require-write` without changing its normal cancellation behavior. With this option,
cancelled saving returns 130 and prevents service/host changes, including when a previous valid file remains present.
Both wrappers report saved configuration, validation, service verification, and any later failure separately.

Service setup delegates protected-file loading, loopback restrictions, manager support, registration ownership,
reconciliation, and startup to the existing service layer. Windows remains experimental; the wrapper's explicit
`--service` also opts into login startup. A registered service is not sufficient for full completion: `service status`
must pass and `doctor --env-file` must report healthy readiness. Degraded readiness returns nonzero.

`doctor --env-file` uses the strict Python environment loader and restores process settings afterwards. Server files
select the configured listener, independently of Client URLs used for SSH forwarding or stale caller defaults.
Client-only files select their Client URL; an explicit diagnostic `--server-url` takes priority. Shell does not source
the file or interpret its contents. Host setup receives `setup --env-file PATH select` at the installed release tag.

The installed release must provide the added CLI options; an older release reports the failed stage and leaves the
Runtime installed. Distribution publication must coordinate the package containing these options with the website.
After any file edit, including Scope IDs, or an upgrade, users rerun `service install --env-file` with the original file.
Generated configuration output and next-step instructions recommend this flow, including reconciliation after Scope
creation. A post-install failure retains packages, saved files, and committed registrations for explicit recovery.

## Choose download sources

`--region auto|cn|global` overrides `POWERCONTEXT_INSTALL_REGION`. Automatic selection checks local named timezone,
then locale territory, then uses global sources. It does not call a network location service.

| Component | Global source | Automatic China source | Explicit control |
| --- | --- | --- | --- |
| PowerContext and dependencies | PyPI | Tsinghua PyPI mirror | `--index-url`, uv index settings and configuration |
| uv installer and binaries | Astral channels | USTC release mirror | `POWERCONTEXT_UV_INSTALLER_URL`, `UV_DOWNLOAD_URL`, uv installer mirror variables |
| Python | uv default channels | NJU python-build-standalone mirror | `UV_PYTHON_INSTALL_MIRROR`, uv Python download configuration |

Existing uv configuration suppresses automatic package mirror selection. An explicit `--index-url` changes only the
default index; additional indexes keep uv's priority rules. uv owns authentication and index resolution. Script URL
arguments must use HTTPS and omit credentials; credentials belong in uv configuration. pip index environment variables
are not uv configuration. Python and uv download overrides are independent of package indexes.

# Reference-level explanation

## Installation responsibilities

The entry points are `website/public/install.sh` and `website/public/install.ps1`, served as static website assets.
Their supported options and failure behavior are equivalent. They own bootstrap and sequencing; uv owns environment
creation and package resolution, while existing `powercontext setup` adapters own integration installation.

The effective installation inputs are:

```text
Package requirement + Runtime profile + explicit Host selection = installation operation
```

The package manager resolves the requirement before replacing the tool environment. Once installed, the CLI's exact
version is verified and used for all host setup. This keeps one release coordinate for the operation without adding a
second package resolver or introducing a release manifest dependency.

The scripts preserve `powercontext setup`, `config`, `doctor`, and service commands. They do not move domain behavior
into shell, add a standalone Python installer engine, or change Runtime/public HTTP APIs.

## Version and profile semantics

- `latest` passes an unpinned profile requirement to `uv tool install --upgrade --reinstall-package powercontext --prerelease disallow`. It means the
  newest stable version compatible with the selected interpreter and configured sources, not necessarily the version
  most recently uploaded to another mirror. The PowerContext package is reinstalled so an existing prerelease cannot bypass stable selection. Resolution failures
  are reported without substituting another requirement.
- An exact `X.Y.Z`, optionally followed by `aN`, `bN`, or `rcN`, uses `==VERSION`. Explicit prereleases are supported;
  source refs, ranges, and URLs are not `--version` values. Releases before 0.1.0 are excluded.
- `local` uses `powercontext[cli,server]`; `client` uses `powercontext[cli]`, whose dependencies include the Client.
  Rerunning with a different profile replaces that tool's dependency set. Independently added extras must be managed
  through the documented manual installation path.
- The executable in `uv tool dir --bin` must report a release version. Exact requests must match it. All profiles
  must expose CLI help and `capabilities --help`; `local` additionally requires
  `config init --help` and `server run --help`. The script prints success only after these commands succeed.
  These checks establish imports and command availability, not Server readiness or Agent workflow correctness.

uv is bootstrapped at an installer-controlled version; that version is independent of the PowerContext release.
An existing uv is reused rather than upgraded silently. Python discovery excludes virtual environments so a project
venv cannot become an accidental installation prerequisite. If no compatible Python 3.11+ is found, uv provisions
Python 3.12. uv remains responsible for platform, architecture, and wheel compatibility errors.

## Mirror precedence and recovery

User-specified uv/Python sources are preserved without fallback. For automatic China selection, an unavailable uv
mirror installer falls back to Astral; failed mirrored uv artifacts may use official sources. Python's exact build
URL is obtained from uv, and its mirrored artifact is checked before installation. If the automatic Python mirror
fails, installation retries with uv's default channels.

Automatic China package selection makes a bounded availability request to the PowerContext index page. An
unreachable mirror falls back to PyPI before tool installation. A reachable mirror remains selected even if it lacks
the requested release; uv reports that resolution failure. Global and explicit sources go directly to uv. The scripts
never interpret index HTML, wheel filenames, encoded URLs, package compatibility, or authentication. They do not
switch indexes after a tool installation attempt, which may already have changed installed files.

Shell environment changes are local to the installer process; PowerShell restores temporary source variables in
`finally`. Existing configuration files are neither parsed by shell nor rewritten. Only installer-owned temporary
downloads are cleaned. `UV_INSTALL_DIR`, `UV_TOOL_DIR`, and `UV_TOOL_BIN_DIR` retain their location controls.

## Persistence and compatibility

Ordinary package installation does not read or rewrite `.env`, credentials, data directories, or database schemas,
and does not stop or restart a service. Explicit `--configure` asks the CLI to save only the selected file; explicit
`--service` asks the native layer to reconcile its owned registration and restart when its existing contract requires it. Users follow the existing upgrade and migration instructions when starting an updated
Server. An exact-version retry may reuse cached packages; `latest` intentionally allows upgrades.

`UV_OFFLINE=1` permits cached reinstallation only when uv, a compatible Python, and all dependencies are present.
It disables installer source probes as well as uv networking. It does not promise a complete offline distribution.
Prerequisites remain available after later failures. A resolution failure for an unavailable exact version preserves
the existing tool. Once uv accepts a package, verification can fail after replacing the old executable: report
"package installed, verification failed" and allow an explicit version retry, without claiming rollback. A later host
setup failure preserves the verified Runtime. There is no cross-component rollback or repair of unrelated host state.

Windows retains its experimental product status. Native acceptance runs on Linux, macOS, and Windows; host-specific
support still comes from each integration's capability contract.

## Executable contract and acceptance

`tests/fixtures/installation/*.json` defines shared public inputs and expected outcomes for both shell adapters.
`tests/test_installation_contract.py` executes those cases with real uv and small offline fixture wheels: host opt-in,
profiles, exact-version verification, unavailable commands, and partial host failure. Cases observe installed
capabilities, selected hosts, release refs and exit results. They do not fix internal function boundaries or call order.
The same catalog runs on each native operating system; fixture host/service execution is not native service or Agent
acceptance. POSIX controlling-terminal cases cover a piped configuration request and cancellation. Native service CI
adds installed-script acceptance for persistent memory, environment-file reconciliation, and a stopped-service upgrade.

`tests/native/test_installation.py` runs the actual shell installer, uv, installed PowerContext CLI, and HTTP Server.
A wheel built from the tested commit uses release-shaped metadata and a direct local file constraint. That constraint
selects the tested artifact; it does not establish an independently enforced checksum. Qualification includes:

- missing uv/Python under global and China source selection, existing tools/configuration, and spaces/Unicode paths;
- real stable/prerelease wheel resolution through a controlled package index, default upgrade, exact selection, and
  preserving the installed version when a requested release is unavailable;
- Client-only installation and profile changes, with no local Server state created by installation;
- `.env` generation and validation, readiness, Memory remember/search, cached offline reinstall, and restart readback;
- explicit index failure, conflicting host choices, unattended and piped Bash installation, and the downloaded
  PowerShell scriptblock entry point.

A separate CI matrix executes the suite on all three operating systems. Release reference checks preserve script
`latest` defaults while updating explicit version examples. Website validation confirms documentation links and the
static build. Syntax checks alone do not establish Windows or macOS installation support, and these tests do not
claim that a real Agent host completes its capture/recall workflow.

[Reproducible first-principles and ablation studies](https://github.com/PsiACE/powercontext/tree/feat/installation-contract/experiments/installation) pin upstream
sources, installer baselines, uv versions, controlled inputs and observed results. They support these decisions:
removing implicit host selection preserves explicit host failure semantics; a filename-based preflight rejects valid
encoded package URLs; prerelease-only indexes require explicit stable policy; and an accepted package can still have
an unusable entry point. They remain separate from maintained product acceptance and carry their own execution limits.

# Drawbacks

Bash and PowerShell duplicate some sequencing and source-selection policy. Automatic mirrors add operational
availability dependencies. `latest` deliberately changes over time and is unsuitable for reproducible deployment
without an exact version. Matching tags coordinate releases but do not independently prove host compatibility.

# Rationale and alternatives

A uv-only command is retained for users who already manage Python and uv; it cannot bootstrap a clean machine.
Source installation is useful for development but is not the default release channel. Pinning the public script to
a PowerContext version would leave new users on stale releases until the website is republished.

A Python engine still needs a bootstrap on machines without Python. Generating the shell adapters would still require
native behavioral tests and would add generator/versioning machinery. Shared conformance cases provide executable
parity without another runtime asset. Introduce an engine or generator only when a concrete policy needs it.

Immutable manifests and a second installation ledger are useful when distributing independently versioned raw
artifacts. Here uv owns wheel resolution, tool environments and its records. The installer observes the actual launcher
and delegates repair to an explicit uv-backed retry. This boundary avoids two authorities for the same installation.

# Prior art

[RFC 1408](https://github.com/oceanbase/powercontext/pull/1408) defines the separation of installation, configuration,
and diagnostics; independent Runtime profiles and host selection; component-level recovery; and explicit service
registration. [RFC 1299](1299_local_server_availability_and_service_installation.md) defines personal service lifecycle.

[Magpie's installer](https://github.com/yetone/magpie/blob/023f5aaad2ecd41ae04390166b9cac9a0b300d81/site/public/install.sh)
separates an authoritative release feed from artifact mirrors and verifies a binary checksum before replacement.
Its raw binary ownership justifies that verification; its manifest is not a replacement for Python package standards.

[Lody's daemon installation contract](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/specs/daemon-upgrade-installation.md)
resolves and executes the actual npm installation destination and checks replacement readiness. Its separate
[managed Agent runtime](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/apps/cli/src/agent/managed-agent-runtime.ts)
owns raw archives and therefore owns manifests and completion records. Apply the actual-executable check here;
retain service readiness in the service boundary. The public tag-release workflow does not publish installers.

[Bub](https://github.com/bubbuild/bub/tree/b4a61bf1326729a024161d22ba20019b8500f907/website/public) bootstraps uv before
running its Python preset resolver. Its terminal and macOS Bash fixes motivate explicit host setup and native tests.
[uv tools](https://docs.astral.sh/uv/concepts/tools/) and
[configuration](https://docs.astral.sh/uv/concepts/configuration-files/) define the reused environment and source
semantics; the [Python Simple API](https://packaging.python.org/en/latest/specifications/simple-repository-api/)
defines package index interpretation. A shell regex cannot implement those contracts.

# Unresolved questions

No further cross-subsystem decision is required for this contract. Complete offline bundles, cryptographic installer
manifests, independently packaged host artifacts, and changes to configuration storage need separate designs.
The platform matrix must remain green before promotion; Windows product support remains experimental.

# Future possibilities

The scripts can bootstrap a distribution-owned installer engine if installation plans later need immutable component
manifests, independent integration versions, or durable per-component repair records. An offline bundle can include
all required artifacts explicitly. Neither extension should make Server startup or service registration implicit.

The personal-service onboarding work tracks [Issue #1900](https://github.com/oceanbase/powercontext/issues/1900).
