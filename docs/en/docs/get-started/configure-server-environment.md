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

This supplies the client address and Server Token without loading model API keys into the client environment.
Follow `.env.next-steps.md` to create Scopes and install plugins, then verify real memory using the [quickstart](quickstart.md).

For every variable, default, and precedence rule, see [Configuration](../operate/configuration.md).
