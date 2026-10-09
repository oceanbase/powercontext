---
title: Install and run
description: Install the latest PowerContext release with Bash or PowerShell, configure mirrors, and run the Server.
---

# Install and run

For an Agent connecting from another machine, see [Connect to a remote Server](../operate/connect-remote-server.md)
for guided URL confirmation, unattended setup, and endpoint-bound HTTP consent.

Start with the [Quick Start](quickstart.md) for your first session. This page covers version selection,
platforms, installation roles, startup, diagnostics, and updates.

## Platform support

| Platform | Status |
| --- | --- |
| macOS, Linux | Supported |
| Windows | `experimental` |

Windows CLI, Server, and personal-service support is experimental. Each Agent Host still has its own platform
requirements. Examples using Bash syntax require a Bash environment and cannot be pasted directly into PowerShell.
Embedded seekDB is unavailable on Windows.

## Install with the recommended script

The script installs the CLI and local Server in an isolated uv tool environment. It reuses compatible uv/Python,
or installs uv and Python 3.12 in user-owned directories. It does not need administrator privileges.

macOS or Linux (Bash; `curl` or `wget` is required):

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash
```

Windows (PowerShell 5.1 or newer):

```powershell
powershell -ExecutionPolicy Bypass -c "& ([scriptblock]::Create((irm https://powercontext.oceanbase.io/install.ps1)))"
```

Apply the PATH command printed at completion before running `powercontext` in your terminal. The installer does not
edit shell startup files or the persistent Windows PATH. `UV_INSTALL_DIR` selects where a missing uv is installed;
`UV_TOOL_BIN_DIR` selects the PowerContext executable directory.

Installation runs unattended by default. Pass `--host codex` (repeatable) to install selected Agent integrations.
Only Agent setup needs Git and each host's prerequisites. `--no-hosts` remains an optional way to state the default;
it cannot be combined with `--host`. The [capability matrix](../integrations/capabilities.md) describes host support.

For an existing remote Server, add `--profile client` to install only the CLI and Client dependencies. The default
`--profile local` includes the local Server. Neither profile starts a Server, registers a service, or overwrites
configuration or data. For a local installation, continue with `powercontext config init` and [Quick Start](quickstart.md).
Client-only installations use endpoint settings in the [remote connection guide](../operate/connect-remote-server.md).

## Configure and install a personal service

For personal macOS/Linux use, the recommended lifecycle is a native current-user service. The installer can
explicitly compose configuration and service registration:

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- \
  --configure --service --env-file "$HOME/.config/powercontext/powercontext.env"
```

`--configure` opens the existing wizard through the controlling terminal and requires `--env-file`. Save the file
to continue; cancellation stops service and host setup. Choose loopback binding for a personal service. Linux needs
an available `systemd --user` manager. In a non-interactive environment, provide an existing protected file instead:

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- \
  --service --env-file /path/to/powercontext.env
```

The installer validates that file, runs `service install`, checks `service status`, and runs `doctor --env-file`
against the Server settings in the same file. An explicit `--host` also receives this file and the installed release
ref. Client-only installations reject `--configure` and `--service` before installation. `--env-file` alone can supply
connection settings to an explicitly selected host without installing a local service.

Windows supports the same explicit options experimentally; `--service` opts into login auto-start. Run `--configure`
in an interactive console. Protect an existing file using the ACL steps in [Deploy the Server](../operate/deploy-server.md).

These options need an installed release providing `config init --require-write` and `doctor --env-file`. If the selected
release lacks them, the installer reports the failed stage and retains the Runtime; use a compatible release or the
separate configuration/service commands. Runtime installation, saved configuration, service registration, and Server
readiness are separate outcomes. `degraded` diagnostics return nonzero. A failed post-install stage does not roll back
packages, delete configuration, or remove an already committed service registration.

After any file edit, including writing returned Scope IDs, reconcile using the original file:

```bash
powercontext service install --env-file /path/to/powercontext.env
powercontext service status
powercontext doctor --env-file /path/to/powercontext.env
```

`doctor --env-file` gives the file authority over shell defaults. A Server file selects its listener; a client-only
file selects its Client URL. `--server-url` can explicitly override the diagnostic target. Generated next-step instructions
cover Scope creation and Agent acceptance; a running service does not establish an Agent workflow.

## Choose a version

The default `--version latest` installs or upgrades to the newest stable release available from the selected index
and compatible with the chosen Python. It excludes prereleases. `--version` accepts an exact release, including an
explicit prerelease such as `1.3.0rc1`; it never substitutes a different version if that release is unavailable.

For example, install PowerContext 1.2.0 with its Codex integration:

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- --version 1.2.0 --host codex
```

This selects package `1.2.0` and Git tag `powercontext-v1.2.0`. With `latest`, the installer reads the installed CLI's
version and uses its matching tag; it never uses the moving `master` branch for host setup. To add an integration later:

```bash
powercontext setup codex --ref "powercontext-v$(powercontext --version)"
```

Use `powercontext setup select --ref "powercontext-v$(powercontext --version)"` for an interactive host picker.

In PowerShell, the same double-quoted expression works. An integration failure leaves the installed Runtime usable
and exits with an error and a retry instruction. Installation success does not establish Server readiness or host
workflow correctness; use the checks below and the integration's own guide.

The installer verifies the installed version and advertised commands before reporting success. A package can install
successfully while command verification fails; in that case the old executable may already have been replaced. Review
the named command error and rerun with a known working `--version`. The installer does not roll package files back.

## Retry dependency downloads with a mirror

Package indexes, uv binaries, and Python distributions are separate downloads. Changing the package index does not
change the uv or Python download location.

| Download | Explicit setting | Automatic China source | Global source |
| --- | --- | --- | --- |
| PowerContext and Python packages | `--index-url URL`, uv index environment variables or `uv.toml` | Tsinghua PyPI mirror | PyPI |
| uv installer | `POWERCONTEXT_UV_INSTALLER_URL` | USTC uv release mirror | Astral installer |
| uv binaries | `UV_DOWNLOAD_URL` or `UV_INSTALLER_GITHUB_BASE_URL` | USTC uv release mirror | Astral release channels |
| Python distributions | `UV_PYTHON_INSTALL_MIRROR` | NJU python-build-standalone mirror | uv default channels |

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- --region cn
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- --index-url https://pypi.org/simple
```

`--region auto|cn|global` overrides `POWERCONTEXT_INSTALL_REGION`. Auto selection uses a named local timezone, then the
locale territory, then global; it makes no geolocation request. An unavailable automatic mirror can fall back to the
official source. A reachable China package mirror remains selected even if it lacks the requested release; uv reports
resolution or artifact failures without retrying against another index. Use `--region global` or an explicit source
to select another index. Explicit sources never fall back automatically.
A mirror may lag PyPI: `latest` means the newest compatible stable release on the chosen index.

Existing uv index settings and configuration files take precedence over automatic package mirrors. An explicit
`--index-url` overrides the default index only; additional uv indexes keep their priority. Private index credentials
belong in uv's authentication configuration, not in script arguments. uv does not read `PIP_INDEX_URL` or
`PIP_EXTRA_INDEX_URL`. Existing Python mirror settings and `uv.toml` also suppress automatic Python mirror selection.
`UV_ASTRAL_MIRROR_URL` is passed through for uv versions that support it. These settings are scoped to the installation;
your persistent package-manager configuration is unchanged. An existing uv is reused without upgrading it.

To inspect the installer or pass several options in PowerShell, save it first:

```powershell
irm https://powercontext.oceanbase.io/install.ps1 -OutFile install.ps1
powershell -ExecutionPolicy Bypass -File .\install.ps1 --region cn --version 1.2.0
```

Cached reinstallation with `UV_OFFLINE=1` can work when uv, compatible Python, and all required packages are already
present. This is not an offline distribution bundle; missing downloads fail explicitly.

## Manual package or source installation

If you already manage Python 3.11+ and [uv](https://docs.astral.sh/uv/), you can install the package directly:

```bash
uv tool install --force "powercontext[cli,server]==1.2.0"
```

For a source installation of the same version (requires Git):

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@powercontext-v1.2.0"
```

The Git command does not leave a repository checkout for you to manage. Git uses its normal credential configuration,
including credential helpers and SSH settings. For an SSH-based install, replace the HTTPS URL with the Git URL
approved for your environment. `--force` also refreshes an existing tool from the current commit behind the selected
Git ref; without it, `uv` may report the same requirement as already installed without fetching a newer `master`.

To install another branch or tag, replace the ref after the final `@`. The `master` branch can include unreleased changes.
Follow the [guide for each integration](../integrations/index.md) for Agent installation, connection options, and verification, using the same ref as the Server.

## Run the local Server

On personal macOS/Linux, install the native current-user service:

```bash
powercontext service install
powercontext service status
powercontext doctor
```

Use `powercontext server run` for development, debugging, temporary use, and platforms without a supported native manager.
Stop an existing foreground instance before installing the service. Service installation remains an explicit operation.

Without environment variables or an environment file, the Server:

- binds to `127.0.0.1:8000`;
- enables Streamable HTTP MCP at `/mcp`;
- creates a default Scope;
- creates a persistent SQLite database in the operating system's user data directory;
- supports explicit Memory operations without an inference provider.

The service manager owns startup and restart. Reinstall its definition after relevant upgrades or configuration changes.
A foreground `server run` stops cleanly on `Ctrl-C`. Both entry points reopen the same configured database.

The Dashboard is an optional content viewer for personal use and demonstrations. It is disabled by default and needs
no separate frontend installation or model configuration. To enable it locally without a token, save these settings
in an environment file:

```dotenv
POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1
POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true
POWERCONTEXT_SERVER_ACCESS_MODE=disabled
```

To require authentication, set `POWERCONTEXT_SERVER_ACCESS_MODE=enforced` and set `POWERCONTEXT_SERVER_AUTH_TOKEN` to
your own long random credential. The [configuration wizard](configure-server-environment.md#local-dashboard-and-optional-authentication)
also offers this choice when enabling Dashboard locally.

```bash
chmod 600 /path/to/powercontext.env
powercontext config validate --env-file /path/to/powercontext.env
powercontext service install --env-file /path/to/powercontext.env
powercontext service status
powercontext doctor --env-file /path/to/powercontext.env
```

Open `http://127.0.0.1:8000/dashboard/home`, using the actual port if you change it. With authentication disabled, the
page opens directly. When enabled, sign in with the Server token and configure connected Agents to use it for API
and MCP requests. The CLI does not automatically load a directory's `.env` file.

The first visit selects the Server default Scope. Pages are empty until content is saved. Save a Memory through an
Agent or public API, then refresh Memories in the same Scope. Experiences, skills, handoffs, and usage also come from
saved records. The Dashboard does not capture sessions, run generation, or approve candidates. The Dashboard and Agent
must use the same Server and Scope.

Open **Profile** to read the saved profile, inspect **Version history**, or verify its sources. Selecting a historical
revision does not change the current profile. In **Handoff**, use **Export Markdown** on a collection entry or its
detail page to download that exact revision, including its full text, omissions, and citations. If sign-in expires,
sign in again to return to the selected detail, then repeat the download.

When authentication is enabled, all token holders use one identity. Multi-user RBAC deployments should leave the
Dashboard disabled and use the API, MCP, or host integrations. See [Deploy the Server](../operate/deploy-server.md)
for network and credential configuration.

This minimal launch does not enable model-backed extraction or vector search. To generate and validate one explicit
environment file for those capabilities, continue with the
[Enable extraction and vector search](configure-models.md).

## Use embedded seekDB

Embedded seekDB is available on Linux and macOS when a compatible `pylibseekdb` wheel is available. Windows does not
support this embedded backend. Install or replace the tool with the optional seekDB extra:

```bash
uv tool install --force "powercontext[cli,server,seekdb]==1.2.0"
```

When switching from SQLite, remove `POWERCONTEXT_SERVER_DATABASE_URL` from the Server process environment. An explicit
SQLAlchemy database URL is not valid for seekDB. Then select the backend and start the Server:

```bash
unset POWERCONTEXT_SERVER_DATABASE_URL
export POWERCONTEXT_SERVER_DATABASE_KIND=seekdb
powercontext server run
```

`server run` loads `.env` from the current directory when present. Export values in the shell to override that file,
pass `--env-file <path>` to select another file, or pass `--no-env-file` to ignore environment files. Process managers
and containers should normally provide an explicit environment instead of relying on their working directory.

PowerContext always uses seekDB's built-in `test` database. Leave `POWERCONTEXT_SERVER_DATABASE_PATH` unset to store
the instance in the `seekdb` subdirectory of the PowerContext user data directory. If `POWERCONTEXT_HOME` is set, the
default is `$POWERCONTEXT_HOME/seekdb`; set `POWERCONTEXT_SERVER_DATABASE_PATH` only when a different location is
required.

In another terminal, verify that the Server and database are ready:

```bash
powercontext doctor
powercontext ready
powercontext capabilities
```

## Verify the installation

```bash
powercontext doctor
powercontext ready
powercontext capabilities
```

`doctor` checks the installed package, Server liveness, and Server readiness without requiring an integration. Server
readiness covers the database and each configured inference provider. Runtime or database failures return
`not_ready`; an inference failure returns `degraded` without removing database-backed operations from traffic.
`ready` and `capabilities` show the readiness and enabled capabilities of the running service.
For Agent diagnostics, use the [guide for each integration](../integrations/index.md). For Server status definitions and recovery steps, see [Troubleshoot](../operate/troubleshoot.md).

For a long-running process, Docker, authentication, or remote access, continue with
[Deploy the Server](../operate/deploy-server.md).

## Update or replace an installation

Before upgrading an existing deployment, back up its database and configuration. Version 1.1.0 upgrades legacy
tag-table constraints during Server startup to support Topic Memory tags. Stop the old Server instances and start
one upgraded instance first so the schema upgrade completes before other instances connect. Databases from before
1.0.0 also need the [Artifact processing migration](../operate/artifact-processing-migration.md) if it has not already
been completed. Upgrade the Server, clients, and Agent integrations together.
The Dashboard must be explicitly enabled; static Bearer authentication is optional for local use. See
[Deploy the Server](../operate/deploy-server.md). Remote plaintext HTTP connections require explicit client consent;
see [Connect to a remote Server](../operate/connect-remote-server.md).

For a registered personal service, use `powercontext service uninstall` to stop it while preserving data. After
the package update, repeat `service install --env-file` with the original path.

To upgrade to the latest stable version, rerun the installer. To keep an exact release, add `--version`:

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash
```

To replace the installed tool with another Git ref:

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@<ref>"
```

Update each installed host using its [integration guide](../integrations/index.md) and the same ref. After updating,
rerun `powercontext service install --env-file /path/to/powercontext.env` with the original file, check `service status`
and `doctor --env-file`, then open a new host session. Existing SQLite data remains in the user data directory unless `POWERCONTEXT_HOME` or the database URL
changes.

## Install a Python role

An application that imports the async Client SDK should add it to that application's environment:

```bash
uv add "powercontext[client]==1.2.0"
```

Use `builtin` for in-process Python composition, `server` for the service, `client` for the Python SDK, or `cli` for
the Server-backed command line. An extra that is only present in the isolated `uv tool` environment is not importable
by an unrelated Python project.
