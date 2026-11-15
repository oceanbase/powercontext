---
title: Script installation
description: Bootstrap Python and uv, install a PowerContext release, and select matching Agent integrations.
---

- Proposal Name: `script_installation`
- Start Date: 2026-10-09
- RFC PR: [oceanbase/powercontext#1892](https://github.com/oceanbase/powercontext/pull/1892)
- Tracking Issue: [oceanbase/powercontext#1900](https://github.com/oceanbase/powercontext/issues/1900)
- Related RFCs: [RFC 1733](1733-usability-and-agent-workflows.md), [RFC 1299](1299_local_server_availability_and_service_installation.md)

# Summary

Provide Bash and PowerShell scripts that install PowerContext through uv. The scripts reuse or install Python and uv, select a Runtime profile, and optionally install Agent integrations at the matching release tag. The default package version is the latest stable release on the selected index; users can request an exact version.

Configuration and personal-service registration remain separate CLI operations. The scripts finish with instructions for those steps. User instructions belong in the [installation guide](../docs/get-started/install-and-run.md).

# Motivation

Installing PowerContext requires a compatible Python, uv, the right package extras, and a matching integration ref. Users also need separate source settings for Python packages, uv binaries, and Python distributions. Changing a package index alone cannot fix a failed Python download.

The installer should handle these prerequisites and release selection while reusing the existing configuration, integration, and service commands. This keeps package installation usable without starting a Server or selecting an Agent host.

# Guide-level explanation

## Runtime profiles and prerequisites

The default `local` profile installs the CLI, Client, and local Server dependencies. `--profile client` installs the CLI and Client dependencies for a remote Server. Profile selection and Agent selection are independent.

The installer reuses existing uv and compatible system or uv-managed Python. If either is missing, it installs the prerequisite in a user directory. It runs unattended by default, requires no administrator privileges, and prints the PATH command needed in a new terminal.

## Releases and Agent integrations

`--version latest` selects the newest compatible stable release on the configured index. An exact version gives a repeatable package selection. For example:

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- --version 1.2.0 --host codex
```

Only explicit `--host` arguments run integration setup. Each selected host requires Git and its own prerequisites. Multiple hosts can be selected. `--no-hosts` explicitly skips integrations and conflicts with `--host`.

The installer reads the installed CLI version and passes its `powercontext-vVERSION` tag to `powercontext setup`. All hosts use that same release ref. Setup runs once per selected host. `--env-file PATH` passes an existing connection file to host setup; the installer does not parse or source it.

## Configuration and personal services

After installation, users configure a local Server and register a personal service through the existing CLI. The native user-service manager runs the Server after the terminal closes. Client-only installations use the remote connection guide. The [installation guide](../docs/get-started/install-and-run.md#configure-and-install-a-personal-service) documents configuration, service registration, and connection checks.

The installer leaves configuration, Server startup, and service lifecycle to their existing commands. Personal-service constraints, including loopback binding and supported managers, follow [RFC 1299](1299_local_server_availability_and_service_installation.md). Windows support remains experimental.

## Download sources

Package indexes, uv binaries, and Python distributions have separate source settings. Users can select a region or specify sources independently. Explicit settings take precedence over automatic mirrors; an automatic mirror can fall back to an official source as described below.

# Reference-level explanation

## Responsibilities and execution order

The entry points are `website/public/install.sh` and `website/public/install.ps1`, served as static website assets. Both implement the same public options and failure behavior.

| Responsibility | Owner |
| --- | --- |
| Validate installer arguments, prepare prerequisites, and sequence installation | Shell script |
| Resolve packages, select compatible artifacts, and maintain the tool environment | uv |
| Install selected Agent integrations | Existing `powercontext setup` adapters |
| Configure and run a Server, diagnose it, and manage a personal service | Existing CLI and service layer |

The scripts perform these steps in order:

1. Validate arguments, including conflicting host options and the existence of a supplied connection file.
2. Reuse or install uv and Python, then select the package index.
3. For a client profile with selected hosts, check `setup --help` in an isolated environment with the selected package requirement. Reject unsupported combinations before replacing the installed tool; otherwise install the selected requirement in a uv tool environment.
4. Locate the installed launcher through `uv tool dir --bin` and verify its version and command availability.
5. Print the installed version and PATH instructions, then run any selected host setup at that version's tag.
6. Print local-service or remote-connection guidance. Return a nonzero status if host setup failed.

## Prerequisite reuse

A missing uv is installed at a version maintained by the installer, independently of the PowerContext version. An existing uv is reused without an upgrade.

Python discovery accepts Python 3.11 or newer below 4.0 and excludes virtual environments. If no compatible interpreter is found, uv installs Python 3.12. uv selects the platform, architecture, and artifact. `UV_INSTALL_DIR`, `UV_TOOL_DIR`, and `UV_TOOL_BIN_DIR` retain their location controls.

## Version and profile semantics

| Selection | Package requirement and behavior |
| --- | --- |
| `--profile local` (default) | `powercontext[cli,server]` |
| `--profile client` | `powercontext[cli]`, including Client dependencies |
| `--version latest` (default) | Unpinned profile requirement with `uv tool install --upgrade --force --prerelease disallow` |
| Exact version | Profile requirement with `==VERSION` |

`latest` means the newest stable version compatible with the selected interpreter and configured index. `--force` makes older uv versions resolve a fresh tool environment, so an installed prerelease cannot bypass stable selection. Resolution failure is reported with the requested requirement unchanged.

Exact versions use `X.Y.Z`, optionally followed by `aN`, `bN`, or `rcN`. Releases before 0.1.0, version ranges, source refs, and URLs are outside this option's contract. Changing the profile replaces the tool's dependency set; users who manage additional extras use the manual installation path.

The installed launcher must report a release version, and an exact request must match it. All profiles must pass CLI help and `capabilities --help`; `local` must also pass `config init --help` and `server run --help`. These checks verify imports and command availability. Server readiness is checked after the user configures and starts the Server.

## Source selection and fallback

Region selection follows `--region`, `POWERCONTEXT_INSTALL_REGION`, local named timezone, locale territory, then global sources. Detection uses local settings without a network location service.

| Component | Global source | Automatic China source | Explicit control |
| --- | --- | --- | --- |
| PowerContext and dependencies | PyPI | Tsinghua PyPI mirror | `--index-url`, uv index settings and configuration |
| uv installer and binaries | Astral channels | USTC release mirror | `POWERCONTEXT_UV_INSTALLER_URL`, `UV_DOWNLOAD_URL`, uv installer mirror variables |
| Python | uv default channels | NJU python-build-standalone mirror | `UV_PYTHON_INSTALL_MIRROR`, uv Python download configuration |

Existing uv configuration suppresses automatic package mirrors. `--index-url` replaces the default index; additional indexes retain uv's priority rules. uv handles authentication and the package index protocol. Script URL arguments require HTTPS without embedded credentials. Download and availability-probe redirects must remain HTTPS. Windows captures native output as UTF-8 so Unicode paths remain usable, then restores the console encoding on exit. pip index variables do not configure uv.

Explicit uv and Python sources are preserved without fallback. For automatic selection, an unavailable uv mirror installer falls back to Astral; mirrored uv artifacts can fall back to official sources. The Python build URL comes from uv. The script checks its mirrored artifact and retries with uv's default channels if the automatic mirror fails.

An automatic package mirror receives a bounded availability request before tool installation. An unreachable mirror falls back to PyPI. A reachable mirror stays selected even if the requested version is missing. uv reports resolution and artifact failures; the script does not switch indexes after a tool installation attempt. This avoids retrying a package replacement against a different source after files may have changed. uv owns index parsing and compatibility checks.

## Failure and recovery

Each completed component remains available when a later step fails:

| Failure | Result and recovery |
| --- | --- |
| A prerequisite cannot be installed | Stop and report the failed download; retain prerequisites already installed |
| Package resolution fails, including an unavailable exact version | Preserve the installed tool and report the uv error |
| The installed launcher fails verification | Report that the package was installed but verification failed; allow an explicit version retry |
| Host setup fails | Preserve the verified Runtime, return nonzero, and identify the matching release ref for a setup retry |

Verification can fail after replacing the previous executable. The installer does not roll package files back or undo completed host operations. It cleans only its temporary downloads. Source and PATH changes remain local to the installation process; PowerShell restores temporary source variables in `finally`.

## Persistence and compatibility

Package installation leaves `.env`, credentials, data directories, and database schemas unchanged. It does not stop or restart a service. Users follow the existing migration and service instructions when starting an updated Server. Runtime APIs and the existing configuration, diagnostic, and service commands retain their contracts.

`UV_OFFLINE=1` disables installer probes and uv networking. Cached reinstallation requires an existing uv, compatible Python, and all required packages. Missing prerequisites or artifacts fail explicitly.

## Executable contract and acceptance

Both shell adapters must pass the shared behavior cases in `tests/fixtures/installation/*.json`. These cases check public arguments, installed capabilities, matching host refs, and exit results through real uv and small fixture wheels.

Acceptance covers:

- Bootstrap with missing uv or Python, reuse of existing tools and configuration, and paths containing spaces or Unicode.
- Latest stable and exact releases, explicit prereleases, and preservation of an installed tool when resolution fails.
- Client/local profile replacement, host opt-in and partial failure, and passing an existing connection file.
- Unattended and piped Bash execution, plus PowerShell file and downloaded-scriptblock entry points.
- Installation of the actual wheel, configuration validation, HTTP readiness, Memory persistence, cached offline reinstall, and restart readback.

The native CI matrix runs installation acceptance on Linux, macOS, and Windows. Controlled-index source tests verify mirror selection and failure handling on Linux. Platform qualification requires native execution; syntax checks and fixture hosts do not establish service-manager or Agent workflow support. Release-reference checks keep explicit version examples current while preserving the `latest` default.

# Drawbacks

Bash and PowerShell require separate implementations of prerequisite and source selection. Automatic mirrors add availability dependencies. `latest` changes as the selected index publishes releases, so repeatable deployments need an exact version. A matching integration tag coordinates versions; each host still needs its own compatibility checks.

# Rationale and alternatives

Direct uv commands remain suitable for users who already manage Python and uv. The scripts cover machines without those prerequisites. Source installation remains available for development. The script defaults to `latest` so its package version does not become stale between website releases.

A Python installer engine would still need a shell bootstrap. Generated shell adapters would add a generator while retaining the need for native tests. Two small adapters with shared behavior cases keep the initial implementation simpler.

uv already maintains the package environment and installation records. A second installation ledger would duplicate that state. Separate manifests and records would be needed if PowerContext later distributed independently versioned raw artifacts.

# Prior art

[RFC 1408](https://github.com/oceanbase/powercontext/pull/1408) separates installation, configuration, and diagnostics, and defines independent profiles, host selection, and component recovery. [RFC 1299](1299_local_server_availability_and_service_installation.md) defines the personal-service lifecycle used here.

[Magpie's installer](https://github.com/yetone/magpie/blob/023f5aaad2ecd41ae04390166b9cac9a0b300d81/site/public/install.sh) separates release sources from artifact mirrors and checks binary checksums before replacement. Its manifest serves direct binary distribution; this design delegates package distribution to uv.

[Lody's daemon installer](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/specs/daemon-upgrade-installation.md) resolves the actual npm installation destination and verifies the installed executable. That check also applies here. Its [managed Agent runtime](https://github.com/LodyAI/Lody/blob/811b573329716b23e1144e5d66211ea4ddfb0dfd/apps/cli/src/agent/managed-agent-runtime.ts) separately manages raw archives and their records.

[Bub](https://github.com/bubbuild/bub/tree/b4a61bf1326729a024161d22ba20019b8500f907/website/public) bootstraps uv before a Python preset resolver. This proposal also uses a shell bootstrap and tests both shell adapters on their native platforms. [uv tools](https://docs.astral.sh/uv/concepts/tools/), [uv configuration](https://docs.astral.sh/uv/concepts/configuration-files/), and the [Python Simple API](https://packaging.python.org/en/latest/specifications/simple-repository-api/) define the environment and package-source behavior delegated to uv.

# Unresolved questions

Complete offline bundles, cryptographic installation manifests, independently packaged host artifacts, and changes to configuration storage need separate designs. This RFC retains Windows' experimental status.

# Future possibilities

An installer engine may become useful for independent component versions, immutable manifests, or persistent repair records. An offline bundle could carry the required artifacts. Such extensions would retain explicit Server startup and service registration.
