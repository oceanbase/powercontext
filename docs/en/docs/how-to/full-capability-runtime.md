---
title: Full-capability Quick Start
description: Configure models, start the Server, and verify the complete Memory loop.
---

# Full-capability Quick Start

`powercontext server run` works without model configuration, but model-backed extraction and vector search stay off.
The guided configuration enables generation, embeddings, scheduled Source processing, metrics, and tracing settings.

| Capability | Minimal Server | Full-capability runtime |
| --- | --- | --- |
| Source capture | Enabled | Enabled |
| Memory extraction | Disabled | Enabled |
| Search modes | `auto, fts` | `auto, fts, vector, hybrid` |
| Dashboard | Default Scope | Default Scope and every created Scope |
| MCP endpoint | `/mcp` | `/mcp` |

The Server creates one opaque default Scope on first startup. The Dashboard discovers Scope descriptors from the
Server; it does not use a configured list. Integrations may bind a Session or workspace to that default or to another
existing Scope.

## 1. Install and configure

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext config init --output .env
```

Enter the provider connection and credential when prompted. For a local provider that ignores authentication, use a
non-secret placeholder accepted by that provider.

Inspect and validate the generated file without printing credentials:

```bash
powercontext config show --env-file .env
powercontext config validate --env-file .env
```

The generated file contains Server, model, database, scheduler, and integration transport settings. Scope identity is
owned by the running Server and is not invented by the Config Generator.

## 2. Start and verify the Server

```bash
powercontext server run --env-file .env
```

In another terminal:

```bash
set -a
. ./.env
set +a
powercontext doctor
powercontext ready
powercontext capabilities
```

The full runtime is ready when readiness is `ready`, Memory extraction is enabled, and search modes include `vector`
and `hybrid`. If only `auto, fts` appear, check the Embedding model, profile ID, dimension, credential, and Base URL.

Open <http://127.0.0.1:8000/> and confirm that the default Scope is available. Retrieve its opaque ID for the following
API checks:

```bash
SCOPE_ID="$(curl -fsS http://127.0.0.1:8000/v1/scopes/default \
  | python -c 'import json, sys; print(json.load(sys.stdin)["scope_id"])')"
export SCOPE_ID
```

## 3. Verify the Memory loop

Capture a Source with a unique ID:

```bash
SOURCE_ID="quickstart-$(date +%s)-$$"
curl -fsS -X POST http://127.0.0.1:8000/v1/sources/content \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${SCOPE_ID}\",\"source_id\":\"${SOURCE_ID}\",\"content\":\"PowerContext quick start check: prefer small, verifiable steps.\"}"
```

Keep the returned `position`, then flush the same Scope:

```bash
curl -fsS -X POST http://127.0.0.1:8000/v1/memory/flush \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${SCOPE_ID}\"}"
```

The returned `current_cursor` must be at least the capture `position`. `status: "idle"` is valid when the Scheduler
already processed the Source.

List Memory entries:

```bash
curl -fsS -X POST http://127.0.0.1:8000/v1/memory/entries/list \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${SCOPE_ID}\"}"
```

Find an entry whose `source_refs` contains the captured Source and record its `citation.entry_id`. Then verify vector
retrieval:

```bash
curl -fsS -X POST http://127.0.0.1:8000/v1/memory/search \
  -H 'content-type: application/json' \
  -d "{\"scope_id\":\"${SCOPE_ID}\",\"query\":\"verifiable steps\",\"mode\":\"vector\",\"limit\":50}"
```

The round trip is verified when the response has `mode: "vector"`, the recorded `entry_id`, and `vector` in
`matched_by`. Confirm model usage with:

```bash
powercontext stats --scope-id "$SCOPE_ID"
```

## 4. Start Codex

Install the plugin using the command printed by Config Generator, load `.env`, and start Codex. Do not set
`POWERCONTEXT_CODEX_SCOPE_ID` for the normal Session flow: the plugin resolves the Session binding, then the workspace
binding, then the Server default Scope. Set it only when the host must select a known existing Scope explicitly.

After Codex starts, send an ordinary prompt. The plugin recalls from the bound Scope and captures the prompt as Source
evidence. Scheduled processing handles new Sources within the configured interval.

## Data and restart behavior

With no database override, SQLite stores `powercontext.db` and `scheduler.db` under the user data directory:

- Linux: `$XDG_DATA_HOME/powercontext`, or `~/.local/share/powercontext`;
- macOS: `~/Library/Application Support/powercontext`.

Press `Ctrl+C` to stop the Server. Restart it with the same `.env` and data directory. The default Scope and its opaque
ID remain stable because they are persisted in the database.

| Symptom | Action |
| --- | --- |
| A Scope is missing from Dashboard | Confirm it was created through the Scope API and refresh the page |
| Readiness is `degraded` | Check model identifiers, credentials, and Base URLs |
| No `vector` or `hybrid` mode | Configure Embedding model, profile ID, and dimension together |
| Sources remain pending | Enable the Scheduler or call `/v1/memory/flush` |
| Existing data is missing | Restore the previous database URL or `POWERCONTEXT_HOME` |

See [Troubleshooting](troubleshoot.md) and [Configuration](../reference/configuration.md) for details.
