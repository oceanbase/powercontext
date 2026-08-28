---
title: Configuration
description: PowerContext paths, Server, Client, inference, and Agent integration environment variables.
---

# Configuration

PowerContext reads configuration from environment variables when each process starts. The CLI does not search for a
`.env` file automatically. Export values in the shell, have the service manager or container supply them, or pass an
explicit file to a command that accepts `--env-file`. An Agent host may load its own environment file according to
that host's rules.

## Explicit environment files

Create a guided configuration, inspect it without printing credentials, and validate it before launch:

```bash
powercontext config init --output .env
powercontext config show --env-file .env
powercontext config validate --env-file .env
powercontext server run --env-file .env
```

`config init` writes the file with mode `0600`. When `server run` receives `--env-file`, assignments in that file
override same-named process values. Inherited `POWERCONTEXT_SERVER_*` values that are missing from the file are
ignored, so validation and launch use the same Server configuration. `config show` redacts recognized and
generator-recorded credentials; still treat the file itself as a secret-bearing deployment artifact. See the
[Full-capability Quick Start](../how-to/full-capability-runtime.md) for the guided setup and verification flow.

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
| `POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK` | `false` | Opt in to a non-loopback bind while authentication is disabled |
| `POWERCONTEXT_SERVER_DASHBOARD_ENABLED` | `true` | Enable the Dashboard at the Server root path `/` |
| `POWERCONTEXT_SERVER_DASHBOARD_SCOPES` | `[]` | JSON array of selectable Dashboard scopes |
| `POWERCONTEXT_SERVER_HANDOFF_REPORT_ENABLED` | `true` | Enable Handoff Report and its API routes |
| `POWERCONTEXT_SERVER_LOGGING_LEVEL` | `INFO` | Operational log level |
| `POWERCONTEXT_SERVER_LOGGING_FORMAT` | `console` | `console` or structured `json` output |
| `POWERCONTEXT_SERVER_LOGGING_ACCESS` | `true` | Log external HTTP and logical MCP request completion |
| `POWERCONTEXT_SERVER_METRICS_ENABLED` | `true` | Expose Prometheus metrics at `/metrics` |
| `POWERCONTEXT_SERVER_TRACING_ENABLED` | `false` | Enable span recording and OTLP export |
| `POWERCONTEXT_SERVER_DATABASE_KIND` | `sqlite` | Storage backend: `sqlite`, `seekdb`, or `oceanbase` |
| `POWERCONTEXT_SERVER_DATABASE_URL` | user data SQLite file | SQLAlchemy async URL for SQLite or OceanBase; do not set for seekDB |
| `POWERCONTEXT_SERVER_DATABASE_PATH` | user data `seekdb` directory | Embedded seekDB path; used only when `DATABASE_KIND=seekdb` |
| `POWERCONTEXT_SERVER_RUNTIME_SCOPE_CACHE_SIZE` | `128` | Inactive scope compositions retained by the Runtime; in-flight scopes are never evicted |
| `POWERCONTEXT_SERVER_RUNTIME_SOURCE_WINDOW_LIMIT` | `100` | Maximum Sources processed in one activation |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_EXTRACTION_PROFILE` | `coding` | Memory selection policy: `coding` or `conversation` |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_ENABLED` | `false` | Apply listwise reranking after coarse Memory retrieval |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_CANDIDATE_LIMIT` | `30` | Coarse candidate pool supplied to the reranker |
| `POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS` | unset | Scheduler interval; unset disables scheduling |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL` | unset | Pydantic AI model identifier for Memory extraction |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_TIMEOUT_SECONDS` | `30` | Generation timeout |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_BATCH_SIZE` | `10` | Maximum texts sent in one embedding request |
| `POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS` | unset | Experience incubation interval; unset disables that job |
| `POWERCONTEXT_SERVER_EXTERNAL_SKILLS` | unset | JSON object containing the host identity and explicit Agent Skill targets |

Static bearer authentication is disabled by default. When enabled, API and MCP requests must include
`Authorization: Bearer <token>`; the liveness and readiness endpoints remain public. Plain HTTP is trusted only on a
loopback address (`localhost`, `::1`, or any address in `127.0.0.0/8`). The Server refuses to start when it binds to a
non-loopback address while authentication is disabled; either enable authentication, keep the bind on loopback, or,
when TLS is terminated upstream or the network is otherwise controlled, set
`POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true` to opt in explicitly. Use TLS before exposing an
authenticated Server over a network.

The Python Client and CLI apply the matching rule for outbound requests: a configured unencrypted `http://` Server
URL is accepted only for loopback hosts. The Client refuses to send any request, authenticated or not, over
unencrypted non-loopback HTTP. Code whose `http://` base URL is only a routing label for a transport that is secure in
practice, such as an in-process ASGI app, Unix-domain socket, or TLS-terminating proxy, must supply its own
`http_client` and pass `trust_transport_security=True` explicitly. See
[Deploy the Server](../how-to/deploy-server.md) for a safe Docker and remote-access setup.

The Dashboard is enabled by default and shares the Server listener and port with the HTTP API and MCP. With no scopes
configured, the page shows an empty state. Dashboard initialization failures are logged with their direct cause and do
not prevent the Server HTTP API, MCP, or health checks from starting.

Handoff Report is independently enabled by default at `/handoff-reports`. When no scope contains a committed Handoff,
it shows a data-free template preview. See [Use Handoff Report](../how-to/use-handoff-report.md) for scope discovery,
inspection, Revision writes, and export.

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

The default `coding` extraction profile keeps cross-task work context such as preferences, decisions, constraints,
expensive facts, and unfinished progress. Select `conversation` when the product must preserve independently
answerable personal facts, relationships, events, exact dates, lists, and historical states from dialogue evidence:

```bash
export POWERCONTEXT_SERVER_RUNTIME_MEMORY_EXTRACTION_PROFILE=conversation
```

The profile affects future Source processing only. It does not reinterpret existing Memory revisions.

Enable answer-oriented Memory reranking when broad Hybrid recall is more important than the latency and token cost of
one additional structured generation request:

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
export POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_ENABLED=true
export POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_CANDIDATE_LIMIT=30
```

Reranking is disabled by default. When enabled, the Runtime retrieves and fuses the configured candidate pool, then
uses the generation model at temperature zero to select no more than the search request's final `limit`. It does not
change stored Memory or indexes. Provider and structured-output failures remain visible as inference errors; disable
reranking when search must remain independent of model availability. See
[RFC 0080](/en/rfcs/0080_memory_search_reranking/) for the algorithm, concurrency, and API boundaries.

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
approve them, place them in PreparedContext, create a managed Skill, export it to an Agent target, or execute anything.
The Memory and Experience jobs share the APScheduler sidecar under `POWERCONTEXT_HOME`, but keep independent job
identities and business cursors. Unsetting one interval removes only that job.
See [Create and review an Experience](../how-to/create-and-review-experience.md) for setup and verification steps.

### Agent Skill targets

Configure Codex and Claude Code host-local targets as one JSON value:

```bash
export POWERCONTEXT_SERVER_EXTERNAL_SKILLS='{
  "host_id": "workstation-1",
  "targets": [
    {
      "target_id": "codex-project",
      "agent_kind": "codex",
      "installation_scope": "project",
      "path": "/srv/project/.agents/skills",
      "allow_managed_publish": true
    },
    {
      "target_id": "claude-project",
      "agent_kind": "claude_code",
      "installation_scope": "project",
      "path": "/srv/project/.claude/skills",
      "allow_managed_publish": true
    }
  ]
}'
```

Target IDs must be unique. `agent_kind` supports `codex` and `claude_code`; installation scopes are `user`, `project`,
and `plugin`. PowerContext scans only the immediate Skill package directories under these explicit targets; it does not
infer a home directory, install packages, or grant execution authority. `allow_managed_publish` defaults to `false`;
when true, the authenticated Skills Library or Review page may explicitly create or safely update an approved managed
Skill in that target. The page still cannot submit an arbitrary path or overwrite a foreign or modified package. The
`host_id`, locator, and registration are local-environment state, not a cross-host contract. Existing `codex_roots`
configuration remains accepted as a Codex-only compatibility form; new configuration should use `targets`.

The Server always creates non-recording OpenTelemetry request context so `X-PowerContext-Request-ID` can be derived from the
inbound span. To enable recording and export for a CLI-managed Server, install
`powercontext[cli,server,tracing-otlp]`, enable tracing, and configure standard OpenTelemetry variables such as
`OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS`, and `OTEL_SERVICE_NAME`. Programmatic Server integrations
that do not use the `powercontext` command may omit the `cli` extra.

Enabling tracing also produces spans for the generation and embedding calls that PowerContext constructs, without
recording prompts, model responses, Memory content, or vectors. See
[Trace with Phoenix](../how-to/trace-with-phoenix.md) for a working configuration.

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

### SQLite vector search

SQLite vector and hybrid search use [sqlite-vec](https://alexgarcia.xyz/sqlite-vec/), which is bundled with the
`powercontext[builtin]` dependency set. Configure the complete embedding profile; no extension path is needed:

```bash
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL=provider:embedding-model
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID=embedding-model-v1
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=1024
export POWERCONTEXT_SERVER_DATABASE_URL=sqlite+aiosqlite:////srv/powercontext/powercontext.db
powercontext server run
```

PowerContext loads and probes the bundled extension when the Server opens the database. Startup fails if the package
does not contain a library compatible with the current platform or SQLite build.

In another terminal, confirm that the initialized runtime reports vector and hybrid search:

```bash
powercontext capabilities
```

SQLite full-text search remains available when no embedding model is configured.

## CLI Server connection

| Variable | Default | Meaning |
| --- | --- | --- |
| `POWERCONTEXT_CLIENT_SERVER_URL` | `http://127.0.0.1:8000` | Server base URL |
| `POWERCONTEXT_CLIENT_API_TOKEN` | unset | Bearer token sent to an authenticated Server |
| `POWERCONTEXT_CLIENT_TIMEOUT` | `10` | HTTP timeout in seconds |

Equivalent one-off flags are available for the Server URL and timeout on `powercontext`. The token is accepted
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

## Claude Code plugin

| Variable | Default | Meaning |
| --- | --- | --- |
| `POWERCONTEXT_CLAUDE_SERVER_URL` | `http://127.0.0.1:8000` | Server base URL used by the Hook |
| `POWERCONTEXT_CLAUDE_SCOPE_ID` | derived from Git remote or project path | Override project scope |
| `POWERCONTEXT_CLAUDE_AUTHORIZATION` | unset | Complete `Bearer <token>` header for Hook and MCP requests |
| `POWERCONTEXT_CLAUDE_CAPTURE_PROMPTS` | `true` | Capture user prompts as ordinary Source evidence |
| `POWERCONTEXT_CLAUDE_FLUSH_ON_CAPTURE` | `false` | Wait for Source processing after capture |
| `POWERCONTEXT_CLAUDE_REQUEST_TIMEOUT_SECONDS` | `1` | Per-request Hook timeout |
| `POWERCONTEXT_CLAUDE_HTTP_BUDGET_SECONDS` | `4` | Shared Hook HTTP budget for recall, capture, and optional flush |
| `POWERCONTEXT_CLAUDE_FLUSH_MAX_CALLS` | `4` | Maximum flush calls per prompt; valid values are 1 through 16 |

`powercontext setup claude-code` stores `server_url` and `capture_prompts` as non-sensitive Claude Code plugin
options. The corresponding `POWERCONTEXT_CLAUDE_*` variables take precedence for the process that starts Claude Code.
Authorization is environment-only and must not be added to the Server URL or plugin options.

The outer `UserPromptSubmit` Hook timeout is ten seconds. Recall and capture use one shared wall-clock budget but fail
independently. Plain HTTP is accepted only for loopback endpoints; use HTTPS for a remote Server. Restart Claude Code
after changing its environment.

## DeepSeek Harness plugin

| Variable | Default | Meaning |
| --- | --- | --- |
| `POWERCONTEXT_DSH_BASE_URL` | `http://127.0.0.1:8000` | Server base URL used by the plugin |
| `POWERCONTEXT_DSH_SCOPE_ID` | derived from Git remote or project path | Override project scope |
| `POWERCONTEXT_DSH_AUTHORIZATION` | unset | Complete `Bearer <token>` header for plugin HTTP requests |
| `POWERCONTEXT_DSH_CAPTURE_PROMPTS` | `true` | Capture user prompts as Source evidence |
| `POWERCONTEXT_DSH_FLUSH_ON_CAPTURE` | `false` | Wait for Source processing after capture |

`timeoutMs`, `requestTimeoutMs`, `maxBytes`, and `flushMaxCalls` are plugin patch settings. Server unavailability fails open for recall and capture; restart `dsh web` after changing these variables.

## Pi package

| Variable | Default | Meaning |
| --- | --- | --- |
| `POWERCONTEXT_PI_BASE_URL` | `http://127.0.0.1:8000` | Server base URL; non-loopback endpoints must use HTTPS |
| `POWERCONTEXT_PI_SCOPE_ID` | derived from Git remote or project path | Override project scope |
| `POWERCONTEXT_PI_AUTHORIZATION` | unset | Complete `Bearer <token>` header for package HTTP requests |
| `POWERCONTEXT_PI_CAPTURE_PROMPTS` | `true` | Capture eligible user prompts as Source evidence |
| `POWERCONTEXT_PI_REQUEST_TIMEOUT_MS` | `1000` | Per-request timeout in milliseconds |
| `POWERCONTEXT_PI_HTTP_BUDGET_MS` | `4000` | Shared recall/capture HTTP budget in milliseconds |
| `POWERCONTEXT_PI_MAX_BYTES` | `8000` | Requested and validated PreparedContext byte limit (`512`–`32768`) |
| `POWERCONTEXT_PI_FLUSH_ON_CAPTURE` | `false` | Wait for captured Source processing during the prompt hook |
| `POWERCONTEXT_PI_FLUSH_MAX_CALLS` | `4` | Maximum flush attempts for one pending Source |

Pi rejects base URLs containing credentials, a query, or a fragment. Recall, capture, and boundary flushing fail open;
explicit `pc_*` durable writes require confirmation and are refused when Pi has no interactive UI. Restart Pi after
changing these variables.

## Other Agent integrations

Some integrations have their own configuration file or environment prefix. Their guides are the source of truth:

- [Hermes](../how-to/configure-hermes.md)
- [LangChain](../how-to/configure-langchain.md)
- [LangGraph](../how-to/configure-langgraph.md)
- [OpenClaw](../how-to/configure-openclaw.md)
- [OpenCode](../how-to/configure-opencode.md)
- [Pydantic AI adapter preview](../how-to/configure-pydantic-ai.md)
- [WorkBuddy](../how-to/configure-workbuddy.md)
