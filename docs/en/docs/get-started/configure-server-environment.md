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

The guided command writes a private file with mode `0600` and does not ask for models or provider credentials during
deployment. The default file can start the Server directly; add the required model, credential, embedding profile ID,
and dimension when you need automatic extraction, model generation, or vector retrieval.

When `--force` would remove existing model, embedding, inference schedule, or provider credential settings, the
command identifies that impact and requires an explicit confirmation that defaults to no. After confirmation, it
creates a mode-`0600` backup before replacing the file.

On macOS and Linux, the guided command writes a private file with mode `0600`. Enter provider credentials through your environment or
secret manager, not in command-line arguments.

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
powercontext server run --env-file .env
```

Values in the file override same-named process values. Inherited `POWERCONTEXT_SERVER_*` values missing from the file
are ignored, so validation and launch use the same Server settings.

The Server starts with the configured capabilities. Use `powercontext ready` and `powercontext capabilities` to check
its readiness and enabled features.

For every variable, default, and precedence rule, see [Configuration](../operate/configuration.md).
