---
title: Install and run
description: Install PowerContext from Git and run the local Server.
---

# Install and run

## Install the application

Install `uv`, then install PowerContext directly from a Git ref:

```bash
uv tool install "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
```

This works on macOS and Linux and does not require a user-managed repository checkout. Git uses its normal credential
configuration, including credential helpers and SSH settings. For an SSH-based install, replace the HTTPS URL with the
Git URL approved for your environment.

To install a tested branch or tag, replace `master` after the final `@`. Use the same ref when configuring integrations:

```bash
powercontext setup codex --source oceanbase/powercontext --ref <ref>
powercontext setup dsh --source oceanbase/powercontext --ref <ref>
powercontext setup pi --source oceanbase/powercontext --ref <ref>
```

For host-specific options, see [Configure Codex](configure-codex.md) and
[Configure DeepSeek Harness](configure-dsh.md).

## Run the local Server

```bash
powercontext server run
```

With no environment variables, the Server:

- binds to `127.0.0.1:8000`;
- enables Streamable HTTP MCP at `/mcp`;
- enables the Dashboard at `/`; when no scopes are configured, the page shows an explicit empty state;
- creates a persistent SQLite database in the operating system's user data directory;
- supports explicit Memory operations without an inference provider.

After startup, the terminal prints the Dashboard URL, such as `http://127.0.0.1:8000/`. The Dashboard shares the
Server listener and port with the HTTP API and MCP. If Dashboard initialization fails, the Server logs a warning with
the direct cause and continues serving the other interfaces. Set `POWERCONTEXT_SERVER_DASHBOARD_ENABLED=false` to
disable the Dashboard explicitly.

`Ctrl-C` performs a clean shutdown. Restarting the command reopens the same database.

## Use embedded seekDB

Embedded seekDB is available on Linux and macOS when a compatible `pylibseekdb` wheel is available. Windows does not
support this embedded backend. Install or replace the tool with the optional seekDB extra:

```bash
uv tool install --force "powercontext[cli,server,seekdb] @ git+https://github.com/oceanbase/powercontext.git@master"
```

When switching from SQLite, remove `POWERCONTEXT_SERVER_DATABASE_URL` and
`POWERCONTEXT_SERVER_DATABASE_VEC1_EXTENSION` from `.env`, or unset them in the shell. Those settings are not valid
for seekDB. Then select the backend and start the Server:

```bash
unset POWERCONTEXT_SERVER_DATABASE_URL
unset POWERCONTEXT_SERVER_DATABASE_VEC1_EXTENSION
export POWERCONTEXT_SERVER_DATABASE_KIND=seekdb
powercontext server run
```

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
powercontext doctor codex
powercontext doctor dsh
powercontext doctor pi
powercontext ready
powercontext capabilities
```

`doctor` checks the installed package, Server liveness, and Server readiness without requiring an integration. Server
readiness covers the database and each configured inference provider. Runtime or database failures return
`not_ready`; an inference failure returns `degraded` without removing database-backed operations from traffic.
`doctor codex`, `doctor dsh`, and `doctor pi` separately check their optional host CLI and PowerContext integration. The content commands exercise the
public HTTP SDK path. `ready` and `capabilities` show the readiness and enabled capabilities of the running service. For complete status definitions and
recovery steps, see [Troubleshoot](troubleshoot.md).

## Update or replace an installation

To replace the installed tool with a chosen ref:

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@<ref>"
powercontext setup codex --source oceanbase/powercontext --ref <ref>
powercontext setup dsh --source oceanbase/powercontext --ref <ref>
powercontext setup pi --source oceanbase/powercontext --ref <ref>
```

Restart the Server and open a new host session after updating. Existing SQLite data remains in the user data
directory unless `POWERCONTEXT_HOME` or the database URL changes.

## Install a Python role

An application that imports the async Client SDK should add it to that application's environment:

```bash
uv add "powercontext[client] @ git+https://github.com/oceanbase/powercontext.git@master"
```

Use `builtin` for in-process Python composition, `server` for the service, `client` for the Python SDK, or `cli` for
the Server-backed command line.
An extra that is only present in the isolated `uv tool` environment is not importable by an unrelated Python project.
