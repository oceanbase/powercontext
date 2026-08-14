---
title: Configure DeepSeek Harness
description: Install the PowerContext DeepSeek Harness plugin and control its local behavior.
---

# Configure DeepSeek Harness

## Install or refresh the plugin

Install DeepSeek Harness first and make sure the web profile exists. Then run:

```bash
powercontext setup dsh --source oceanbase/powercontext --ref master
```

The command installs the plugin from `integrations/dsh/plugins/powercontext` and creates the user data directory. The directory must contain a built `lib/index.js`. It is safe to run again: a valid checkout is reused, and a broken checkout for the same ref is replaced. Pass the same `--ref` used to install the PowerContext tool. `--source` accepts a GitHub slug or a `https://github.com/...` URL.

A local checkout works the same way:

```bash
powercontext setup dsh --source .
```

`setup dsh` calls `dsh plugin --profile web add`. Open a new `dsh web` session after setup.

## Understand what the plugin does

The plugin has two paths to the same Server:

- before each model step it asks the Runtime to prepare one final, bounded context value, then independently captures the user's prompt as Source evidence;
- named `pc_*` tools call the public HTTP API to remember, search, revise, retire, and audit Memory.

Memory scope comes from the normalized Git remote when one is available, or from the project path otherwise. Set `POWERCONTEXT_DSH_SCOPE_ID` only when you need an explicit scope that is independent of both.

The plugin calls `POST /v1/context/prepare` once before the model analyzes the prompt. Explicit `remember_memory` calls do not require a model.

## Control prompt capture

Prompt capture is enabled by default. Disable it before starting DeepSeek Harness when the current work must not be recorded:

```bash
export POWERCONTEXT_DSH_CAPTURE_PROMPTS=false
dsh web
```

For testing only, make the plugin wait for captured Source processing:

```bash
export POWERCONTEXT_DSH_FLUSH_ON_CAPTURE=true
```

This adds inference latency to each prompt and is not the normal interactive setting. `timeoutMs`, `requestTimeoutMs`, `maxBytes`, and `flushMaxCalls` are plugin patch settings, not environment variables.

## Connect to an authenticated local Server

```bash
export POWERCONTEXT_SERVER_AUTH_ENABLED=true
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

Start DeepSeek Harness from an environment that contains the matching complete Authorization header:

```bash
export POWERCONTEXT_DSH_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
dsh web
```

Do not put the token in the patch file or the Server URL. If the Server is unavailable, recall and capture fail open. Plugin load still requires the DeepSeek Harness peer modules.

## Verify the installation

```bash
powercontext doctor
powercontext doctor dsh
```

`doctor` checks the package and Server. `doctor dsh` checks the DeepSeek Harness CLI and that dump-config contains the plugin id `powercontext-dsh`.
