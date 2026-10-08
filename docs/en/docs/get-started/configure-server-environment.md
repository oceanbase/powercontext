---
title: Configure a Server environment
description: Generate, inspect, validate, and run PowerContext from an explicit environment file.
---

# Configure a Server environment

Use an explicit environment file when the Server needs inference, scheduling, storage, or deployment settings.

## 1. Generate the file

```bash
powercontext config init --output .env
```

The command opens an English/Chinese wizard. On first use, it selects the default language from `LC_ALL`, then
`LC_MESSAGES`, then `LANG`; when none is set, it checks the system language (including macOS language preferences).
An undetectable or unsupported language falls back to English. Change the selection on the first screen, or set
`--language en` or `--language zh` explicitly. An existing file remembers the previous wizard language.

After choosing the language, select storage, the usage scenario, and the required memory capabilities. The wizard
then asks about Dashboard and access settings and only the model connections needed by those capabilities. Basic
memory uses explicit Agent-saved memories and full-text recall without a separate model API; automatic processing
and semantic retrieval require their respective model settings. Existing files can be reused or adjusted by module.

A fresh local setup leaves Dashboard and authentication disabled. Enabling Dashboard does not require authentication.
For manual authentication setup, set `POWERCONTEXT_SERVER_ACCESS_MODE=enforced` and `POWERCONTEXT_SERVER_AUTH_TOKEN`.
Remote setup enables authentication. Existing Dashboard and authentication settings are preserved when accepting defaults.

Agent configuration selects one Agent at a time and can then add another; configured choices are removed from the
menu. Each Agent can independently use the default Scope, bind an existing Scope, or plan a new isolated Scope.
Planned titles use `codex-<random>` or `claude-code-<random>`, but the real `scope_id` is the opaque value returned
after Server creates it. The wizard never treats the title as an ID.

Review the configuration before saving. The command generates files and follow-up instructions; it does not start
the Server, install Agent plugins, migrate databases, or probe remote storage and model endpoints. It can inspect
existing local SQLite metadata read-only, which does not prove deployment compatibility. Saving a full memory
configuration does not verify that memory extraction works.

If embedded seekdb dependencies are missing, the wizard asks for consent before installing them incrementally in the
background. After saving, it displays activity and waits if installation is still running. On failure it prints a
manual installation command. This does not start the service.

To retain the basic model-free template instead of the wizard, use:

```bash
powercontext config init --template --output .env
```

In template mode, replacing an existing file requires `--force`. If replacement removes inference settings or
provider credentials, a separate confirmation defaults to no. The wizard previews selected changes and preserves
unrelated settings. Both paths back up existing files before replacing them.

On macOS and Linux, generated environment files and backups use mode `0600`. Enter provider credentials through
hidden wizard prompts, your environment, or a secret manager, not in command-line arguments.

Windows support is `experimental`. Before using the file for a personal service, restrict its ACL as described in
[Deploy the Server](../operate/deploy-server.md).

### Local Dashboard and optional authentication

When enabling Dashboard in a new local setup, the wizard offers optional authentication, disabled by default.
Accepting the default generates no Server token: the browser opens directly, and HTTP API and MCP requests need no
Authorization header. The minimal settings are:

```dotenv
POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1
POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true
POWERCONTEXT_SERVER_ACCESS_MODE=disabled
```

To require authentication, select it when enabling Dashboard. The wizard generates a token and writes
credentials for the selected Agents. For manual configuration, set `POWERCONTEXT_SERVER_ACCESS_MODE=enforced` and
`POWERCONTEXT_SERVER_AUTH_TOKEN`. Dashboard, HTTP API, and MCP share this setting. Remote scenarios still enable authentication.

Existing authenticated configurations retain their token and access mode.

### Choose or change the Web / Server port

Dashboard, HTTP API, and MCP share one Server listener; there is no separate Dashboard port.
Run `powercontext config init --output .env`, select local usage, and enter a port such as `18000` in
**Dashboard and access**. The wizard suggests `17429` for a fresh setup and uses the configured port for an
existing file. The selected port is saved explicitly; runtime and `--template` defaults remain `8000`.
Valid ports are integers from `1` to `65535`. The wizard probes the selected local Server listener. If the port is occupied,
choose another port or explicitly keep it; keeping it requires stopping the occupying process before starting
Server. The wizard never terminates processes. This check does not reserve the port or inspect SSH forwarded
ports on another computer. If availability cannot be checked, the wizard warns you to verify it before startup.

For an existing file, choose **Edit selected modules**, then **Dashboard and access**. Change the port,
choose **Review and save**, and confirm. You can also restore `8000` this way. The wizard saves
`POWERCONTEXT_SERVER_HTTP_PORT`; configure Agent connections again when their saved endpoints need updating.

Start the Server with `powercontext server run --env-file .env`. If it is already running, stop it and
restart it with that file; saving does not reload or restart the process. With port `18000`, open
`http://127.0.0.1:18000/` for Dashboard; MCP uses `http://127.0.0.1:18000/mcp`.
CLI options and process environment variables still override the saved file.

For remote access, the public HTTPS URL remains separate from the internal listener port. With SSH
forwarding, select the Server port and the client-side forwarded port separately, then use the generated tunnel command.

### Install another Agent with the same connection

Setup resolves one connection URL per Agent and reuses it for plugin/MCP configuration and URL-bound
credentials. It discovers `.env` in the current directory, or reads an explicit file without executing it:

```bash
powercontext setup --env-file .env codex
powercontext setup --env-file .env pi
```

`--env-file` belongs before the Agent subcommand. Client URLs and existing Agent settings are checked for
conflicts rather than silently overriding one another. If no client URL exists, setup uses the configured
public URL, or derives a local URL from `POWERCONTEXT_SERVER_HTTP_PORT`. Use an explicit URL to resolve a
conflict, for example `powercontext setup --env-file .env codex --server-url http://127.0.0.1:18000`.
Conflicting process URL variables must also be unset or aligned because they can override installed settings.

This selects the connection; it does not migrate every Agent's secret storage. Hermes and OpenClaw retain
their native authentication setup. Restart an already-running Agent after changing its connection.

## 2. Inspect and validate it

```bash
powercontext config show --env-file .env
powercontext config validate --env-file .env
```

`config show` redacts recognized credentials. Validation accepts minimal Server-only files; when inference models or
inference-dependent runtime features are configured, it also checks the Runtime composition without printing secrets.

## 3. Run the same configuration

```bash
powercontext server run
```

`server run` discovers `.env` in the current directory. Use `--env-file <path>` to select a different file or
`--no-env-file` to disable file loading. CLI options take precedence, followed by process environment variables, the
selected file, and defaults. The command prints the resolved file path without printing credentials.

Keep the Server running. In another terminal, return to the configuration directory and load the generated client
configuration before checking the service:

```bash
set -a
. ./.env
set +a
powercontext ready
powercontext capabilities
```

This supplies the client address and, when authentication is enabled, the Server token. Local unauthenticated configurations need no token.
Follow `.env.next-steps.md` to create Scopes and install plugins, then verify real memory using the [quickstart](quickstart.md).

For every variable, default, and precedence rule, see [Configuration](../operate/configuration.md).
