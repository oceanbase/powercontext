---
title: Install and run
description: Install PowerContext from Git and run the local Server.
---

# Install and run

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

## Choose a version

Keep a released package and integration on the same tag. For example, install `0.2.0`:

```bash
uv tool install "powercontext[cli,server]==0.2.0"
```

The following examples use `master`, including unreleased capabilities. Check the
[capability matrix](../integrations/capabilities.md); `master_only` and `experimental` capabilities
are not release guarantees.

## Install the application

You need Python 3.11 or newer, Git, and [`uv`](https://docs.astral.sh/uv/) on macOS, Linux, or Windows. Then install
PowerContext directly from a Git ref:

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
```

The command does not leave a repository checkout for you to manage. Git uses its normal credential configuration,
including credential helpers and SSH settings. For an SSH-based install, replace the HTTPS URL with the Git URL
approved for your environment. `--force` also refreshes an existing tool from the current commit behind the selected
Git ref; without it, `uv` may report the same requirement as already installed without fetching a newer `master`.

To install a tested branch or tag, replace `master` after the final `@`.
Follow the [guide for each integration](../integrations/index.md) for Agent installation, connection options, and verification, using the same ref as the Server.

## Run the local Server

```bash
powercontext server run
```

With no environment variables, the Server:

- binds to `127.0.0.1:8000`;
- enables Streamable HTTP MCP at `/mcp`;
- creates a default Scope;
- creates a persistent SQLite database in the operating system's user data directory;
- supports explicit Memory operations without an inference provider.

`Ctrl-C` performs a clean shutdown. Restarting the command reopens the same database.

The Dashboard is an optional content viewer for personal use and demonstrations. It is disabled by default and needs
no separate frontend installation or model configuration. To enable it, put these settings in a protected environment
file and replace the token example with your own long random credential:

```dotenv
POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true
POWERCONTEXT_SERVER_ACCESS_MODE=enforced
POWERCONTEXT_SERVER_AUTH_TOKEN=replace-with-your-random-token
```

```bash
chmod 600 /path/to/powercontext.env
powercontext config validate --env-file /path/to/powercontext.env
powercontext server run --env-file /path/to/powercontext.env
```

Open `http://127.0.0.1:8000/dashboard/home` and enter the same token. Use the actual port if you change it.
The token also protects the Server API and MCP, so connected Agents need it too. The CLI does not automatically load
a directory's `.env` file.

The first sign-in selects the Server default Scope. Pages are empty until content is saved. Save a Memory through an
Agent or public API, then refresh Memories in the same Scope. Experiences, skills, handoffs, and usage also come from
saved records. The Dashboard does not capture sessions, run generation, or approve candidates. The Dashboard and Agent
must use the same Server and Scope.

All token holders use one identity. Multi-user RBAC deployments should leave the Dashboard disabled and use the API,
MCP, or host integrations. See [Deploy the Server](../operate/deploy-server.md) for network and credential configuration.

This minimal launch does not enable model-backed extraction or vector search. To generate and validate one explicit
environment file for those capabilities, continue with the
[Enable extraction and vector search](configure-models.md).

## Use embedded seekDB

Embedded seekDB is available on Linux and macOS when a compatible `pylibseekdb` wheel is available. Windows does not
support this embedded backend. Install or replace the tool with the optional seekDB extra:

```bash
uv tool install --force "powercontext[cli,server,seekdb] @ git+https://github.com/oceanbase/powercontext.git@master"
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

To replace the installed tool with a chosen ref:

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@<ref>"
```

Update each installed host using its [integration guide](../integrations/index.md) and the same ref. Restart the Server and open a new host session
after updating. Existing SQLite data remains in the user data directory unless `POWERCONTEXT_HOME` or the database URL
changes.

## Install a Python role

An application that imports the async Client SDK should add it to that application's environment:

```bash
uv add "powercontext[client] @ git+https://github.com/oceanbase/powercontext.git@master"
```

Use `builtin` for in-process Python composition, `server` for the service, `client` for the Python SDK, or `cli` for
the Server-backed command line. An extra that is only present in the isolated `uv tool` environment is not importable
by an unrelated Python project.
