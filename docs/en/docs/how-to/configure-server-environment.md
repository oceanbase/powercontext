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

The guided command writes a private file with mode `0600`. Enter provider credentials through your environment or
secret manager, not in command-line arguments.

## 2. Inspect and validate it

```bash
powercontext config show --env-file .env
powercontext config validate --env-file .env
```

`config show` redacts recognized credentials. Validation checks the document and the selected model settings without
printing secrets.

## 3. Run the same configuration

```bash
powercontext server run --env-file .env
```

Values in the file override same-named process values. Inherited `POWERCONTEXT_SERVER_*` values missing from the file
are ignored, so validation and launch use the same Server settings.

The Server starts with the configured capabilities. Use `powercontext ready` and `powercontext capabilities` to check
its readiness and enabled features.

For every variable, default, and precedence rule, see [Configuration](../reference/configuration.md).
