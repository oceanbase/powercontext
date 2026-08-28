---
title: Full-capability Quick Start
description: Start all PowerContext capabilities in five minutes.
---

# Full-capability Quick Start

## Minimal versus full capability

`powercontext server run` starts a minimal Server without any configuration. It stays up and accepts Sources, but the
capabilities that need models stay off. The guided `config init` flow writes one `.env` that turns everything on:

| Capability | Default minimal server | Full-capability runtime |
| --- | --- | --- |
| Source capture | Enabled | Enabled |
| Memory extraction | Disabled; Sources stay pending | Enabled; Scheduler processes Sources every 60 s |
| Search modes | `auto, fts` only | `auto, fts, vector, hybrid` |
| Dashboard Scopes | None configured | `project:quickstart` visible |
| MCP endpoint | `/mcp` enabled | `/mcp` enabled |

Both modes use SQLite by default. Vector search additionally uses the bundled `sqlite-vec` extension; when the
Embedding model or its profile is not configured, the Server falls back to SQLite FTS and reports
`Search modes: auto, fts`. Recall still works through FTS, but semantic and hybrid search need the Embedding model.

## Choose the Scope ID first

The Scope ID is PowerContext's data namespace. Think of it as the project ID. Sources, Memories, and Handoffs belong to
a Scope; the Dashboard and Coding Agent must use the same Scope ID for Agent-written data to appear in the web UI.

A Server can store multiple Scopes. The Server configuration determines which Scopes the Dashboard can display, while
the Coding Agent configuration determines which Scope the current session reads and writes:

```text
Coding Agent ── read/write ──> project:quickstart <── display ── Dashboard
```

Use any short, stable, non-empty string. Do not include keys or other secrets. For example:

```text
project:quickstart
git:github.com/oceanbase/powercontext
team:payment-service
```

This Quick Start uses:

```text
project:quickstart
```

## Quick Start

### Part 1: Start the Server

#### 1. Install

```bash
uv tool install "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
```

#### 2. Generate the configuration

```bash
powercontext config init --output .env
```

Enter a provider credential when prompted. Pydantic AI providers require a credential during construction; for a
local service that ignores authentication, use a non-secret placeholder accepted by that service.

When finished, the command prints setup and launch commands for Codex, Claude Code, DeepSeek Harness, OpenCode, and Pi.
The generated `.env` groups every setting you would otherwise assemble by hand: Server HTTP, Dashboard, Scope,
Generation model, Embedding model with profile ID and dimension, database kind and location, scheduler interval, and
per-host integration URLs. Inspect it any time with `powercontext config show --env-file .env`; credentials print as
`<redacted>`, and `powercontext config validate --env-file .env` checks the syntax and model settings.

#### 3. Start the Server

```bash
powercontext server run --env-file .env
```

With `--env-file`, assignments in the file override same-named shell values, and stale `POWERCONTEXT_SERVER_*`
variables missing from the file are ignored. This makes `config validate` and `server run` use the same Server config.

#### 4. Verify the Server

Run this in a second terminal:

```bash
set -a
. ./.env
set +a
powercontext doctor
powercontext ready
powercontext capabilities
```

Confirm these results:

```text
package: ok - powercontext <version>
server liveness: ok - http://127.0.0.1:8000 status=ok
server readiness: ok - http://127.0.0.1:8000 status=ready
Status: ready
Memory extraction: enabled
Search modes: auto, fts, vector, hybrid
```

The full-capability runtime is ready when `doctor` reports all checks as `ok`, `Status: ready`,
`Memory extraction: enabled`, all four search modes are listed, and the Dashboard at
<http://127.0.0.1:8000/> contains `Quick Start`.

If `powercontext capabilities` lists only `auto, fts`, the Server is running in FTS-only fallback mode. Vector and
hybrid search are unavailable, so the runtime does not meet the full-capability check above.

### Part 2: Verify the Memory loop

Extraction runs when Sources are flushed, so verify one full round trip before starting a Coding Agent. Use a unique
Source ID so this check remains valid when the guide is run again. With the same environment loaded:

```bash
SOURCE_ID="quickstart-$(date +%s)-$$"
curl -fsS -X POST http://127.0.0.1:8000/v1/sources/content \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"project:quickstart\",\"source_id\":\"${SOURCE_ID}\",\"content\":\"PowerContext quick start check: prefer small, verifiable steps.\"}"
```

The Server replies `202` with `"status":"accepted"` and a numeric `position`; keep both the Source ID and position.
Then flush the Scope, which runs Memory extraction:

```bash
curl -X POST http://127.0.0.1:8000/v1/memory/flush \
  -H 'content-type: application/json' \
  -d '{"scope_id":"project:quickstart"}'
```

The flush response contains `current_cursor`. It must be greater than or equal to the capture response's `position`.
The Scheduler may process the Source before this request, so `status:"idle"` is valid when the cursor has already
reached that position. If it has not, flush again.

Now list the current Memory entries:

```bash
curl -fsS -X POST http://127.0.0.1:8000/v1/memory/entries/list \
  -H 'content-type: application/json' \
  -d '{"scope_id":"project:quickstart"}'
```

Find an entry whose `source_refs` contains the capture response's `source_id`, and record that entry's
`citation.entry_id`. This proves the captured Source produced Memory. If no entry cites this Source, extraction ran but
produced no candidate; use a new Source ID with a clearer durable fact or preference and repeat the check.

Confirm the inventory and verify Embedding by searching with vector mode:

```bash
curl -X POST http://127.0.0.1:8000/v1/memory/search \
  -H 'content-type: application/json' \
  -d '{"scope_id":"project:quickstart","query":"verifiable steps","mode":"vector","limit":50}'
```

Embedding is verified only when the response contains `"mode":"vector"` and a hit whose `citation.entry_id` equals
the source-linked entry recorded above and whose `matched_by` contains `"vector"`. A hit for another entry, an empty
`hits` list, or `"mode":null` does not verify this round trip. Check
`powercontext capabilities`: if `vector` is absent from `Search modes`, the Server is in FTS-only fallback mode; if it
is present, the source-linked entry has not been confirmed through vector search yet. An explicit vector request against
existing Memory returns HTTP 422 when vector capability is unavailable.

Finally, check the stats for model usage:

```bash
powercontext stats --scope-id project:quickstart
```

```text
Embedding: 1 requests, ...
```

The Embedding request count is cumulative. A non-zero value corroborates model use, but only the source-linked vector
hit above proves this round trip.

### Part 3: Start a Coding Agent

The Config Generator prints setup and launch commands for every supported Coding Agent. Open a new terminal, choose an
Agent, and copy the two commands under it. The first installs the PowerContext integration; the second loads the
generated `.env` and starts the Agent, so you do not need to enter the Scope ID again.

After the Coding Agent starts, send an ordinary prompt in the project. The integration first recalls relevant Memory
from `project:quickstart`, then saves the prompt as a Source. The Scheduler extracts Memory from new Sources within
about 60 seconds, so the flush from Part 2 is only needed once to prove the loop.

## Where data lives

The generated configuration leaves the database unset, so the Server stores data in the user data directory instead of
a project-local file. With `POWERCONTEXT_HOME` unset, SQLite keeps `powercontext.db` and the scheduler state in
`scheduler.db` under:

- macOS: `~/Library/Application Support/powercontext/`
- Linux: `~/.local/share/powercontext/`

Set `POWERCONTEXT_HOME` before starting the Server to relocate all of this. Changing the database URL later points the
Server at a different (possibly empty) database; keep the previous value if you need the old data.

## Stop and restart

Press `Ctrl+C` in the Server terminal to stop it. Data persists in SQLite across restarts. To resume, load the same
`.env` and run `powercontext server run --env-file .env` again; pending Sources are processed on the next Scheduler run
or flush.

## Quick troubleshooting

| Symptom | Action |
| --- | --- |
| Dashboard is empty | Compare the complete Dashboard and Agent Scope strings |
| `ready` is `degraded` | Check the Generation and Embedding models, keys, and Base URLs |
| No `vector` or `hybrid` search | Configure the Embedding model, profile ID, and dimension together; without them recall stays on FTS (`auto, fts`) |
| Sources remain pending | Enable the Scheduler or call `/v1/memory/flush` |
| Existing data is missing | Restore the previous database URL or `POWERCONTEXT_HOME` |

See [Troubleshooting](troubleshoot.md) for error states and [Configuration](../reference/configuration.md) for all
variables.
