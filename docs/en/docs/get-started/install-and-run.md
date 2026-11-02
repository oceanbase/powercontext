---
title: Install and run
description: Install PowerContext 1.4.1 and run the local Server.
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

## Choose a version

These instructions use PowerContext 1.4.1. Keep the package and Agent integration on
the same version: package `1.4.1` and Git tag `powercontext-v1.4.1`.

```bash
uv tool install --force "powercontext[cli,server]==1.4.1"
powercontext setup codex --ref powercontext-v1.4.1
```

Check the [capability matrix](../integrations/capabilities.md) for host support and maintenance status.
Capabilities marked `experimental` remain experimental in this release.

## Install the application

You need Python 3.11 or newer, Git, and [`uv`](https://docs.astral.sh/uv/) on macOS, Linux, or Windows. Then install
PowerContext from PyPI:

```bash
uv tool install --force "powercontext[cli,server]==1.4.1"
```

For a source installation of the same version:

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@powercontext-v1.4.1"
```

For a machine that only connects to a remote Server, the `cli` extra includes `setup`, `doctor`, and remote content
commands. Add `server` to run a local Server or use the `service` commands. If a command provider has a missing
dependency, the CLI reports the provider and missing module so the installation can be repaired.

The Git command does not leave a repository checkout for you to manage. Git uses its normal credential configuration,
including credential helpers and SSH settings. For an SSH-based install, replace the HTTPS URL with the Git URL
approved for your environment. `--force` also refreshes an existing tool from the current commit behind the selected
Git ref; without it, `uv` may report the same requirement as already installed without fetching a newer `master`.

To install another branch or tag, replace the ref after the final `@`. The `master` branch can include unreleased changes.
Follow the [guide for each integration](../integrations/index.md) for Agent installation, connection options, and verification, using the same ref as the Server.

## Run the local Server

```bash
powercontext server run
```

Without environment variables or an environment file, the Server:

- binds to `127.0.0.1:8000`;
- enables Streamable HTTP MCP at `/mcp`;
- creates a default Scope;
- creates a persistent SQLite database in the operating system's user data directory;
- supports explicit Memory operations without an inference provider.

`Ctrl-C` performs a clean shutdown. Restarting the command reopens the same database.

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
powercontext server run --env-file /path/to/powercontext.env
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
uv tool install --force "powercontext[cli,server,seekdb]==1.4.1"
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

PowerContext defaults to a dedicated `powercontext` database and creates it before initializing application tables.
To select another database, set `POWERCONTEXT_SERVER_DATABASE_DATABASE=my_context`; a missing database is created,
and an existing one is reused. Names containing backticks are rejected before initialization.
Leave `POWERCONTEXT_SERVER_DATABASE_PATH` unset to store
the instance in the `seekdb` subdirectory of the PowerContext user data directory. If `POWERCONTEXT_HOME` is set, the
default is `$POWERCONTEXT_HOME/seekdb`; set `POWERCONTEXT_SERVER_DATABASE_PATH` only when a different location is
required.

In another terminal, verify that the Server and database are ready:

```bash
powercontext doctor
powercontext ready
powercontext capabilities
```

### Existing seekDB installations

Before upgrading, back up the seekDB instance and configuration, then explicitly retain the old database:

```bash
export POWERCONTEXT_SERVER_DATABASE_KIND=seekdb
export POWERCONTEXT_SERVER_DATABASE_DATABASE=test
powercontext server run
```

Keep the original `POWERCONTEXT_SERVER_DATABASE_PATH` or `POWERCONTEXT_HOME`. The instance directory and database name
are separate settings: changing either can select different data. Configure services and containers with the same values.

If no database name was explicitly configured and `test` contains tables named with PowerContext's `pc_` prefix,
startup stops before creating application tables and asks you to choose a database. It never automatically selects,
copies, or deletes the old database. Explicitly choose `test` to retain old data, or `powercontext` to use the new database.
The configuration wizard also asks for the database name; existing users should enter `test`.

To move data, keep using `test` until you have a backup and a validated export/import procedure for your engine version.
Stop all source writers, provision the target database, export/import the schema and data using that procedure, verify
identities and relationships, and read the migrated data through PowerContext before switching the configuration.
Automatic database creation does not perform this migration. Do not assume an arbitrary SQL dump is compatible across
engine or schema versions; existing schemas remain subject to compatibility checks and explicit schema maintenance.
Code graph references identify their instance and database: copying relational rows alone does not migrate graph storage.
Include target Code index rebuilding and queries in your validation; explicitly retaining `test` preserves legacy graph identities.

After switching, keep the old database without writers for a retention period you choose. Record the source and target
instance paths and names, backup location, stop-write and cutover times, validation results, and a review date. Before
manually deleting the source, confirm that no deployment uses it and that a usable backup remains. New writes to the
target are not copied back to the source, so keeping the source does not provide lossless rollback. PowerContext does
not automatically migrate data, send retention reminders, compare databases, or delete old databases.

## Use OceanBase

Connect to an existing OceanBase **MySQL-compatible user tenant**. Prepare the selected database and initialize its
full Server schema through explicit maintenance before starting the Server. PowerContext does not provision an
OceanBase instance, tenant, or account. Ensure network access and complete the Server
installation steps above, then configure the connection:

```bash
export POWERCONTEXT_SERVER_DATABASE_KIND=oceanbase
# Store credentials in your secret manager or a protected environment file.
export POWERCONTEXT_SERVER_DATABASE_URL='mysql+aoceanbase://user%40tenant:encoded-password@host:2881/?charset=utf8mb4'
```

Use the tenant account and the actual direct-connection or OBProxy endpoint and port. Percent-encode reserved characters
in the URL username and password; for example, `user@tenant` becomes `user%40tenant`. The URL requires the
`mysql+aoceanbase` driver, an explicit port, and `charset=utf8mb4`.

The example omits the database name and therefore selects `powercontext`. To select a custom database, put its name in
the URL path, for example `/team_context?charset=utf8mb4`. Existing URLs with an explicit database continue to select
that database. Select the database only in the URL path; `db` and `database` query parameters are rejected.
Database identifiers support 1–64 BMP characters, excluding control characters, slash, backslash, dot, backticks,
leading/trailing whitespace, and system database names; names are never silently trimmed or renamed by PowerContext. The server's identifier case rules still apply.
Names containing backticks are rejected during configuration validation, before connecting or creating a database.
Choose a name such as `powercontext`, `team_context`, or a Chinese name without backticks.

Python SDK initialization first connects to the selected database. If it is usable, no database-creation privileges are required. Only a
confirmed unknown-database error triggers an instance/tenant connection and `CREATE DATABASE IF NOT EXISTS`, followed
by a new connection to the selected database and application-table initialization. Authentication and network errors
do not trigger creation or fallback. If access is denied without proving whether the database exists, the error asks
an administrator to check both existence and privileges.

For a new full Server deployment, create the selected database first. If SDK initialization cannot create a missing
database, the same administrator step applies. Run the following in the **same tenant**, using your configured name,
then grant the application account the required database privileges:

```sql
CREATE DATABASE IF NOT EXISTS `powercontext` CHARACTER SET utf8mb4;
```

For the full Server, follow [database migrations](../operate/database-migrations.md) to plan, apply, and verify the
registered Server bundle with an explicit evidence directory and coordination mode. Start `powercontext server run`
only after the database is ready. Server startup checks readiness and does not initialize OceanBase schemas or
automatically migrate existing databases. SDK callers can retry initialization using the same URL after provisioning.
Creating missing tables requires table-creation privileges; an existing fully initialized database does not acquire a
new database-creation privilege requirement. Initialization never drops or
recreates an existing database, never switches to another database after a failure, and does not authorize schema upgrades.
Concurrent creation uses idempotent database/table DDL and handles duplicate-index creation races. This does not provide
coordination for existing-schema migrations or change embedded seekDB's process-ownership restrictions.

For Python callers, `OceanBaseProfile.open()` resolves an omitted URL database and provisions it when necessary.
`OceanBaseProfile.open_existing()` connects only to an already provisioned database, and `attach()` requires a
caller-owned engine that already selects an existing database; neither replaces that engine or provisions a different
connection target. Read-only inspection (`plan`, `verify`, and the `db-migrate` inspection commands) never creates a
missing OceanBase or embedded seekDB database; explicit writable seekDB maintenance can provision its configured database
before applying its bundle.

Read-only seekDB inspection requires an initialized instance directory. A missing or empty path is rejected before
starting the native engine, without creating parent directories or storage files. A file or a nonempty directory without
recognized native storage is also rejected without changing its contents. Ordinary startup still initializes new instances.

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

<!-- upgrade-support:start -->
Upgrades from 1.3.0 and 1.4.0 require passing full Server acceptance on SQLite, seekDB and OceanBase. These migration paths are not yet verified; do not upgrade existing databases.
<!-- upgrade-support:end -->

Keep existing deployments on their working version until their source version and backend have an accepted upgrade
path. To evaluate a fresh installation, use a separate program environment, database location, and port. Replacing
the installed package does not migrate its database. Backups do not make an unsupported migration supported.

The support declaration is checked against the release matrix and installed-artifact acceptance; see
[Preparing a release](../../development/releasing.md). Domain-specific
[Processing](../operate/artifact-processing-migration.md) and
[Atomic Memory](../operate/atomic-memory-migration.md) maintenance guides do not establish full Server readiness.

Review the [Dream coordinated upgrade requirements](../workflows/review-dream-revisions.md#coordinated-upgrade)
before upgrading an existing database. The unified Candidate routes replace `/v1/artifact-candidates/*` with
`/v1/candidates/*` without aliases, and Dream Clients require contract 2. Existing Dream storage needs an explicit
migration after unfinished runs have drained. [Full Server migration](../operate/database-migrations.md) requires
`state=ready` and `server_ready=true`; partial Artifact or Atomic Memory verification cannot replace it. Do not bypass
the migration gate.

To install 1.4.1 in a separate environment for a fresh deployment:

```bash
uv venv /path/to/powercontext-preview
uv pip install --python /path/to/powercontext-preview/bin/python "powercontext[cli,server]==1.4.1"
```

To install another Git ref in the separate environment:

```bash
uv pip install --python /path/to/powercontext-preview/bin/python "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@<ref>"
```

Use the matching ref for clients and Agent integrations connected to the fresh deployment. Preserve the original
deployment's database location and configuration; do not point the new Server at it as an installation check.
On Windows, the virtual environment interpreter is at `Scripts/python.exe`.

## Install a Python role

An application that imports the async Client SDK should add it to that application's environment:

```bash
uv add "powercontext[client]==1.4.1"
```

Use `builtin` for in-process Python composition, `server` for the service, `client` for the Python SDK, or `cli` for
the Server-backed command line. An extra that is only present in the isolated `uv tool` environment is not importable
by an unrelated Python project.
