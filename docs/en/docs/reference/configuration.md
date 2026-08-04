---
title: Configuration
description: PowerContext paths, Server, Client, inference, and Codex environment variables.
---

# Configuration

PowerContext reads configuration from environment variables when each process starts.

## User data

`POWERCONTEXT_HOME` overrides the directory used by the installed Server:

```bash
export POWERCONTEXT_HOME=/srv/powercontext
```

Without an override, the default is:

- Linux: `$XDG_DATA_HOME/powercontext`, or `~/.local/share/powercontext`;
- macOS: `~/Library/Application Support/powercontext`.

The default SQLite database is `powercontext.db` in this directory. Scheduled processing uses `scheduler.db` in the
same directory.

## Server

Server settings use the `POWERCONTEXT_SERVER_` prefix.

| Variable | Default | Meaning |
| --- | --- | --- |
| `POWERCONTEXT_SERVER_HTTP_HOST` | `127.0.0.1` | Listener address |
| `POWERCONTEXT_SERVER_HTTP_PORT` | `8000` | Listener port |
| `POWERCONTEXT_SERVER_MCP_ENABLED` | `true` | Enable Streamable HTTP MCP |
| `POWERCONTEXT_SERVER_MCP_PATH` | `/mcp` | MCP path |
| `POWERCONTEXT_SERVER_AUTH_ENABLED` | `false` | Require one static bearer token for HTTP and MCP |
| `POWERCONTEXT_SERVER_AUTH_TOKEN` | unset | Static bearer token; required when authentication is enabled |
| `POWERCONTEXT_SERVER_LOGGING_LEVEL` | `INFO` | Operational log level |
| `POWERCONTEXT_SERVER_LOGGING_FORMAT` | `console` | `console` or structured `json` output |
| `POWERCONTEXT_SERVER_LOGGING_ACCESS` | `true` | Log external HTTP and logical MCP request completion |
| `POWERCONTEXT_SERVER_METRICS_ENABLED` | `true` | Expose Prometheus metrics at `/metrics` |
| `POWERCONTEXT_SERVER_TRACING_ENABLED` | `false` | Enable span recording and OTLP export |
| `POWERCONTEXT_SERVER_DATABASE_URL` | user data SQLite file | SQLAlchemy async database URL |
| `POWERCONTEXT_SERVER_RUNTIME_SOURCE_WINDOW_LIMIT` | `100` | Maximum Sources processed in one activation |
| `POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS` | unset | Scheduler interval; unset disables scheduling |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL` | unset | Pydantic AI model identifier for Memory extraction |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_TIMEOUT_SECONDS` | `30` | Generation timeout |
| `POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS` | unset | Experience incubation interval; unset disables that job |
| `POWERCONTEXT_SERVER_EXTERNAL_SKILLS` | unset | JSON object containing the host identity and explicit Codex Skill roots |

Static bearer authentication is disabled by default. When enabled, API and MCP requests must include
`Authorization: Bearer <token>`; the liveness and readiness endpoints remain public. Plain HTTP should remain on a
loopback address. Use TLS before exposing an authenticated Server over a network.

Example with a controlled SQLite path and scheduled extraction:

```bash
export POWERCONTEXT_SERVER_DATABASE_URL=sqlite+aiosqlite:////srv/powercontext/runtime.db
export POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS=30
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
powercontext server run
```

Provider credentials, such as `OPENAI_API_KEY`, are read by the configured inference provider. Do not place secrets in
command-line arguments, documentation, or Memory. Replace `provider:model-name` with a model identifier supported by
Pydantic AI. Scheduled extraction requires both a generation model and
`POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS`. An explicit Memory write does not require either.

The same configured generation model gates explicit Experience generation, managed Skill generation and evolution,
and external Skill import or fork. Without it, these operations return a capability error before persisting a
Candidate. Candidate Review, exact reads, and external Skill scan/list/resolve continue to work.

Experience incubation is a separate APScheduler job with its own persisted Source cursor. Enable it with:

```bash
export POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS=30
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
powercontext server run
```

Each activation inspects a fixed window of at most 32 Sources and exposes only Content Sources whose metadata contains
`"kind": "task-outcome"` to the model. It creates pending Experience Candidates in the Review Inbox; it does not
approve them, place them in PreparedContext, create a managed Skill, export it for Codex, or execute anything.
The Memory and Experience jobs share the APScheduler sidecar under `POWERCONTEXT_HOME`, but keep independent job
identities and business cursors. Unsetting one interval removes only that job.

### External Codex Skills

Configure host-local roots as one JSON value:

```bash
export POWERCONTEXT_SERVER_EXTERNAL_SKILLS='{
  "host_id": "workstation-1",
  "codex_roots": [
    {
      "root_id": "repository",
      "installation_scope": "project",
      "path": "/srv/project/.agents/skills"
    }
  ]
}'
```

Root IDs must be unique. Supported installation scopes are `user`, `project`, and `plugin`. PowerContext scans only
the immediate Skill package directories under these explicit roots; it does not infer a home directory, install
packages, or grant execution authority. The `host_id`, locator, and registration are local-environment state, not a
cross-host or cross-Agent contract.

The Server always creates non-recording OpenTelemetry request context so `X-PowerContext-Request-ID` can be derived from the
inbound span. To enable recording and export for a CLI-managed Server, install
`powercontext[cli,server,tracing-otlp]`, enable tracing, and configure standard OpenTelemetry variables such as
`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, and `OTEL_SERVICE_NAME`. Programmatic Server integrations
that do not use the `powercontext` command may omit the `cli` extra.

To use OceanBase, provide its URL through your environment or secret manager:

```bash
export POWERCONTEXT_SERVER_DATABASE_KIND=oceanbase
export POWERCONTEXT_SERVER_DATABASE_URL="$OCEANBASE_URL"
```

The URL must use the `mysql+aoceanbase` driver, include an explicit port and database, and set `charset=utf8mb4`. The
tenant must use MySQL compatibility mode.

### Embeddings

Embedding search is enabled only when all three identity fields are set:

```bash
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL=provider:embedding-model
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID=embedding-model-v1
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=1024
```

Replace the example values with the selected provider model, a stable profile ID, and that model's dimension.

Optional settings are `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_NORMALIZATION` and
`POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_TIMEOUT_SECONDS`.

Embedding normalization defaults to `unit`.

### SQLite Vec1

SQLite vector and hybrid search additionally require a
[SQLite Vec1](https://sqlite.org/vec1/doc/trunk/doc/vec1.md) 0.7 or newer loadable extension. PowerContext does not
download, build, or update this native library. Obtain it for the Server's operating system and architecture, then
set its path together with the complete embedding profile:

```bash
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL=provider:embedding-model
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID=embedding-model-v1
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=1024
export POWERCONTEXT_SERVER_DATABASE_VEC1_EXTENSION=/opt/sqlite-extensions/vec1
powercontext server run
```

The extension path must identify a library that the SQLite loader can open. PowerContext loads and probes the
extension when the Server opens the database; startup fails if the library is incompatible or older than 0.7.

In another terminal, confirm that the initialized runtime reports vector and hybrid search:

```bash
powercontext client capabilities
```

If Vec1 is unavailable, leave `POWERCONTEXT_SERVER_DATABASE_VEC1_EXTENSION` unset. SQLite full-text search remains
available without an embedding model or native extension.

## Client CLI

| Variable | Default | Meaning |
| --- | --- | --- |
| `POWERCONTEXT_CLIENT_SERVER_URL` | `http://127.0.0.1:8000` | Server base URL |
| `POWERCONTEXT_CLIENT_API_TOKEN` | unset | Bearer token sent to an authenticated Server |
| `POWERCONTEXT_CLIENT_TIMEOUT` | `10` | HTTP timeout in seconds |

Equivalent one-off flags are available for the Server URL and timeout on `powercontext client`. The token is accepted
only through the environment so it does not appear in command-line arguments.

## Codex plugin

| Variable | Default | Meaning |
| --- | --- | --- |
| `POWERCONTEXT_CODEX_SCOPE_ID` | derived from Git remote or project path | Override project scope |
| `POWERCONTEXT_CODEX_AUTHORIZATION` | unset | Complete `Bearer <token>` header for Hook and MCP requests |
| `POWERCONTEXT_CODEX_CAPTURE_PROMPTS` | `true` | Capture user prompts as Source evidence |
| `POWERCONTEXT_CODEX_FLUSH_ON_CAPTURE` | `false` | Wait for Source processing after capture |
| `POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS` | `1` | Per-request hook timeout |
| `POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS` | `4` | Shared hook HTTP budget |
| `POWERCONTEXT_CODEX_FLUSH_MAX_CALLS` | `4` | Maximum flush calls per prompt |

The outer Codex hook timeout is ten seconds. Recall, capture, and flush fail independently and never block Codex when
the Server is unavailable or rejects authentication. The variable must be present in the environment that starts
Codex; restart Codex after changing it.

## Builtin CLI

`powercontext builtin` uses the same database, runtime, and inference field names under the
`POWERCONTEXT_BUILTIN_` prefix. Its default SQLite database is in memory; configure
`POWERCONTEXT_BUILTIN_DATABASE_URL` when CLI invocations must share state.
