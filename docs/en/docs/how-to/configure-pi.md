---
title: Configure Pi
description: Install the native PowerContext package for Pi and control recall, capture, and durable tool writes.
---

# Configure Pi

## Install or refresh the package

Install Pi, then install the package from the same PowerContext ref as the CLI:

```bash
powercontext setup pi --source oceanbase/powercontext --ref master
```

A local checkout works as well:

```bash
powercontext setup pi --source .
```

`setup pi` calls Pi's native package installer and creates PowerContext's data directory. It does not start the
Server. Start the Server, then open a new Pi session in a project directory:

```bash
powercontext server run
pi
```

## Understand what the package does

Before Pi starts an agent turn, the package calls `POST /v1/context/prepare` once with an 8000-byte default budget.
It strictly accepts only `powercontext.prepared-context.v1` and appends a label that marks the result as untrusted
historical evidence. Current system instructions, repository guidance, and the user's request take precedence.

Eligible user prompts are captured separately as Content Sources. The package never synchronizes the complete Pi
transcript. Recall, capture, and boundary flushing fail open: an unavailable Server, timeout, redirect, or invalid
response leaves Pi's prompt unchanged and never blocks ordinary work.

The package resolves one Server-owned Scope in this order: `POWERCONTEXT_PI_SCOPE_ID`, a durable binding for the
workspace, then the Server default. The workspace path is hashed only as an external binding key; it never becomes a
Scope ID. Keep the explicit variable unset unless the host must force one existing Scope.

## Control prompt capture

Prompt capture is enabled by default. Disable it before starting Pi when current work must not be recorded:

```bash
export POWERCONTEXT_PI_CAPTURE_PROMPTS=false
pi
```

Secret-looking prompts and prompts above 200,000 UTF-8 bytes are never captured. Turning capture on does not itself
guarantee Memory extraction; the Server still needs a configured generation model.

For testing, make capture wait for Source processing:

```bash
export POWERCONTEXT_PI_FLUSH_ON_CAPTURE=true
pi
```

This adds latency and is not the normal interactive setting. Without it, Pi records Source positions and makes a
short, bounded best-effort flush at agent and session boundaries.

## Use explicit tools and commands

The `project-context` skill explains when to use native `pc_*` tools. The core tools are:

- `pc_search`, `pc_memory_list`, `pc_memory_get`, `pc_memory_revise`, and `pc_memory_retire`;
- `pc_remember`, `pc_prepare_context`, and `pc_capture_source`;
- `pc_handoff_activate`, `pc_handoff_prepare`, `pc_handoff_finalize`, `pc_handoff_commit`, and
  `pc_handoff_continue`.

Explicit durable writes require confirmation in an interactive Pi session. Without an interactive UI, Pi refuses the
write rather than persisting it silently. `/pc doctor`, `/pc search <query>`, `/pc remember <text>`, `/pc flush`, and
`/pc stats` offer direct status and maintenance commands.

## Connect to an authenticated Server

Start an authenticated Server from a protected environment:

```bash
export POWERCONTEXT_SERVER_AUTH_ENABLED=true
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_LOCAL_TOKEN"
powercontext server run
```

Start Pi with the complete matching header:

```bash
export POWERCONTEXT_PI_AUTHORIZATION="Bearer $POWERCONTEXT_LOCAL_TOKEN"
pi
```

Do not put credentials in `POWERCONTEXT_PI_BASE_URL`. The package accepts plain HTTP only for loopback Servers; use
HTTPS for any remote Server.

## Verify the installation

```bash
powercontext doctor
powercontext doctor pi
```

`doctor pi` checks that the Pi executable is available and that Pi lists the PowerContext package. Restart Pi after
changing PowerContext environment variables.
