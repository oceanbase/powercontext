---
title: Configuration
description: PowerContext paths, Server, Client, inference, and Agent integration environment variables.
---

# Configuration

PowerContext reads configuration from environment variables when each process starts. The CLI does not search for a
`.env` file automatically. A command that accepts `--env-file` loads environment assignments from that file, including
Server and provider settings, and overrides same-named process values. Agent hosts can load their own environment files
according to their host-specific rules.

For the configuration-file workflow, including generation, redacted inspection, validation, and launch, see
[Configure a Server environment](../how-to/configure-server-environment.md). Treat every environment file as a
secret-bearing deployment artifact.

`service install` additionally requires the file to be a regular, non-symlink file owned by the current user with no
group or other permissions. The service records its identity and refuses to launch if the file is replaced or its
ownership, permissions, or contents change; run `service install` again after an intentional update.

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
| `POWERCONTEXT_SERVER_WORKSPACE` | Server startup directory | Resolution root for local project Agent Skill folders |
| `POWERCONTEXT_SERVER_MCP_ENABLED` | `true` | Enable Streamable HTTP MCP |
| `POWERCONTEXT_SERVER_MCP_PATH` | `/mcp` | MCP path |
| `POWERCONTEXT_SERVER_AUTH_ENABLED` | `false` | Require one static bearer token for HTTP and MCP |
| `POWERCONTEXT_SERVER_AUTH_TOKEN` | unset | Static bearer token; required when authentication is enabled |
| `POWERCONTEXT_SERVER_PUBLIC_URL` | unset | Remotely reachable base URL used by remote Skill enrollment guidance; HTTPS is required by default |
| `POWERCONTEXT_SERVER_ALLOW_INSECURE_HTTP` | `false` | Explicitly allow cleartext HTTP for remote Skill Receiver endpoints and guidance |
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
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL` | unset | Pydantic AI model used by configured extraction, generation, Handoff, and reranking operations |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL` | provider default | Custom generation provider base URL |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_HEADERS` | `{}` | JSON object of static generation client headers; values are secrets |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL_SETTINGS` | `{}` | JSON object of Pydantic AI generation model settings |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_TIMEOUT_SECONDS` | `30` | Timeout in seconds for one structured generation operation |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MAX_REQUESTS` | `2` | Maximum provider requests for one structured generation operation, including retries |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL` | unset | Pydantic AI embedding model; requires profile ID and dimension |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_BASE_URL` | provider default | Custom OpenAI-compatible embeddings base URL |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_HEADERS` | `{}` | JSON object of static embedding client headers; values are secrets |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL_SETTINGS` | `{}` | JSON object of Pydantic AI embedding model settings |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID` | unset | Stable identity for the model, dimension, and normalization used by the vector index |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION` | unset | Positive output dimension requested from and validated against the embedding model |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_NORMALIZATION` | `unit` | Vector normalization: `unit` or `none` |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_TIMEOUT_SECONDS` | `30` | Timeout in seconds for one embedding request |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_BATCH_SIZE` | `10` | Maximum texts sent in one embedding request |
| `POWERCONTEXT_SERVER_INFERENCE_RERANK_MODEL` | generation model | Optional dedicated Pydantic AI model for LLM reranking |
| `POWERCONTEXT_SERVER_INFERENCE_RERANK_BASE_URL` | inherited/provider default | Custom LLM reranker provider base URL |
| `POWERCONTEXT_SERVER_INFERENCE_RERANK_HEADERS` | `{}` | JSON object of static LLM reranker client headers; values are secrets |
| `POWERCONTEXT_SERVER_INFERENCE_RERANK_MODEL_SETTINGS` | `{}` | JSON object of Pydantic AI reranker model settings |
| `POWERCONTEXT_SERVER_INFERENCE_RERANK_TIMEOUT_SECONDS` | generation timeout | LLM reranker timeout |
| `POWERCONTEXT_SERVER_INFERENCE_RERANK_MAX_REQUESTS` | generation request limit | Maximum model requests in one rerank operation |
| `POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS` | unset | Experience incubation interval; unset disables that job |
| `POWERCONTEXT_SERVER_EXTERNAL_SKILLS` | automatic local project targets | JSON override containing the host identity and explicit Agent Skill targets |

Static bearer authentication is disabled by default. When enabled, API and MCP requests must include
`Authorization: Bearer <token>`; the liveness and readiness endpoints remain public. Plain HTTP is trusted only on a
loopback address (`localhost`, `::1`, or any address in `127.0.0.0/8`). The Server refuses to start when it binds to a
non-loopback address while authentication is disabled; either enable authentication, keep the bind on loopback, or,
when TLS is terminated upstream or the network is otherwise controlled, set
`POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true` to opt in explicitly. Use TLS before exposing an
authenticated Server over a network.

The Python Client and CLI apply the matching rule for general outbound requests: a configured unencrypted `http://`
Server URL is accepted only for loopback hosts. The explicit remote Skill Receiver PoC exception is documented below.
Code whose `http://` base URL is only a routing label for a transport that is secure in practice, such as an in-process
ASGI app, Unix-domain socket, or TLS-terminating proxy, must supply its own `http_client` and pass
`trust_transport_security=True` explicitly. See
[Deploy the Server](../how-to/deploy-server.md) for a safe Docker and remote-access setup.

The Dashboard is enabled by default and shares the Server listener and port with the HTTP API and MCP. With no scopes
configured, the page shows an empty state. Dashboard initialization failures are logged with their direct cause and do
not prevent the Server HTTP API, MCP, or health checks from starting.

By default, the Server treats its startup directory as the workspace and exposes two writable local project targets:
`<workspace>/.agents/skills` for Codex and `<workspace>/.claude/skills` for Claude Code. Missing directories are harmless
and are created only after the user confirms an installation in the Dashboard. Set `POWERCONTEXT_SERVER_WORKSPACE` once
for systemd, containers, or other launchers whose working directory is not the project; the page does not ask users to
enter Skill paths.

Configure `POWERCONTEXT_SERVER_PUBLIC_URL` once when remote Skill Receivers should connect through a different externally
reachable origin than the one used to open the Dashboard. The Skills Dashboard then generates the enrollment command
without asking for an address on every target. When it is unset, the Dashboard automatically uses its current HTTPS
origin, or its current HTTP origin when the explicit insecure switch is enabled. If neither is available, the enrollment
command relies on the remote CLI's configured Server URL.

For a first-phase PoC on a protected internal test network, direct HTTP requires explicit consent on both sides. Set
`POWERCONTEXT_SERVER_ALLOW_INSECURE_HTTP=true`, advertise an `http://` `POWERCONTEXT_SERVER_PUBLIC_URL`, and bind the
listener to an address reachable by the target. The Dashboard shows a cleartext warning and adds
`remote-enroll --allow-insecure-http`; a manually entered enrollment command must include the same option. Without the
Server setting, the remote endpoints reject non-loopback HTTP. Without the Receiver option, the CLI rejects the URL
before transmitting the one-time enrollment code. The permission is stored in the owner-only Receiver configuration so
`remote-watch` and its systemd user service keep the same policy without embedding credentials or extra flags in the
unit. This switch adds no TLS, network isolation, or protection against interception: do not use it on the public
Internet or an untrusted network, and prefer HTTPS for persistent deployments.

```bash
export POWERCONTEXT_SERVER_HTTP_HOST=0.0.0.0
export POWERCONTEXT_SERVER_PUBLIC_URL=http://powercontext.internal.example:8765
export POWERCONTEXT_SERVER_ALLOW_INSECURE_HTTP=true
export POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true
powercontext server run

# On the target project:
powercontext --server-url http://powercontext.internal.example:8765 \
  skill remote-enroll --workspace "$PWD" --install-service --allow-insecure-http
```

The non-loopback opt-in in this example is independent of the Receiver transport exception: it acknowledges that all
Server routes on this listener are reachable without the Server-wide bearer token. Prefer enabling authentication or
terminating TLS in front of a loopback-bound Server whenever the deployment permits it.

When bearer authentication is enabled, the HTML shells at `/`, `/skills`, `/reviews`, and `/handoff-reports`, plus
their static assets, remain public so the browser can render the sign-in form. Data requests stay protected. Enter the
Server token in that form; the browser keeps it only in the current tab's session storage. Disable both Dashboard and
Handoff Report if even these sign-in pages must not be exposed.

Handoff Report is independently enabled by default at `/handoff-reports`. When no scope contains a committed Handoff,
it shows a data-free template preview. See [Use Handoff Report](../how-to/use-handoff-report.md) for scope discovery,
inspection, Revision writes, and export.

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

The built-in reranker is an LLM listwise reranker, not a dedicated cross-encoder protocol. By default it reuses the
generation model and its provider settings. Set `POWERCONTEXT_SERVER_INFERENCE_RERANK_MODEL` to give that LLM operation
an independent model, base URL, headers, settings, timeout, and request limit.

The same configured generation model gates explicit Experience generation, managed Skill generation, and semantic
Skill fork/evolution. Exact external Skill import and complete package upload do not use a model: PowerContext validates
and stores the canonical package bytes, then creates a pending Candidate with the same package digest. Without a
generation model, semantic generation returns a capability error before persisting a Candidate; Review, package
inspection and download, exact import, usage recording, and external Skill scan/list/resolve continue to work.

Experience incubation is a separate APScheduler job with its own persisted Source cursor. Each activation inspects a
fixed window of at most 32 Sources and exposes only Content Sources whose metadata contains
`"kind": "task-outcome"` to the model. It creates pending Experience Candidates in the Review Inbox; it does not
approve them, place them in PreparedContext, create a managed Skill, export it to an Agent target, or execute anything.
The Memory and Experience jobs share the APScheduler sidecar under `POWERCONTEXT_HOME`, but keep independent job
identities and business cursors. Unsetting one interval removes only that job.
See [Create and review an Experience](../how-to/create-and-review-experience.md) for setup and verification steps.

### Agent Skill targets

The zero-configuration flow uses the Codex and Claude Code project folders under the workspace. Provide a JSON override
only for custom paths, user-level targets, environment compatibility facts, or to explicitly disable local discovery.
For a basic JSON shape and verification flow, see
[Configure Agent Skill targets](../how-to/configure-agent-skill-targets.md). A compatibility-aware override looks like:

```bash
export POWERCONTEXT_SERVER_EXTERNAL_SKILLS='{
  "host_id": "workstation-1",
  "targets": [
    {
      "target_id": "codex-project",
      "agent_kind": "codex",
      "installation_scope": "project",
      "path": "/srv/project/.agents/skills",
      "allow_managed_publish": true,
      "environment": {
        "operating_system": "linux",
        "architecture": "x86_64",
        "commands": {"python": "3.13.2", "bash": "5.2"},
        "network_policy": "restricted",
        "writable_roots": ["workspace"],
        "dependency_install_policy": "denied",
        "environment_names": ["CI"]
      }
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
Setting `POWERCONTEXT_SERVER_EXTERNAL_SKILLS` replaces both automatically generated project targets in full; use
`{"host_id": null, "targets": []}` to disable local discovery and publication. Target IDs must be unique. `agent_kind`
supports `codex` and `claude_code`; installation scopes are `user`, `project`, and `plugin`. PowerContext scans only the
immediate Skill package directories under default or explicit targets; it does not infer a user home directory, install
packages, or grant execution authority. The two generated project targets let users explicitly install from the
Dashboard. Custom targets default `allow_managed_publish` to `false`; when true, the authenticated Skills Library or
Review page may explicitly create or safely update an approved managed
Skill in that target. Publication materializes the exact reviewed package, including scripts and references, without
executing it or injecting a sidecar into the package. The same pages can safely unpublish only an intact package whose
binding and tree digest still match; local drift and foreign content remain untouched. The page still cannot submit an
arbitrary path or overwrite a foreign or modified package. The
`host_id`, locator, and registration are local-environment state, not a cross-host contract. Existing `codex_roots`
configuration remains accepted as a Codex-only compatibility form; new configuration should use `targets`.

The optional `environment` object contains only observed, secret-free compatibility facts. Command values are version
labels, and `environment_names` records names only, never values. PowerContext does not probe or execute package scripts
to construct this profile. When it is absent, packages containing scripts report unknown compatibility; when present,
the Skills Library compares known script interpreters with the observed command names and displays a reasoned assessment.
The assessment does not grant network, filesystem, dependency-install, or environment access.

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

### Embeddings and SQLite vector search

Vector search requires all three embedding identity variables: model, stable profile ID, and positive dimension.
Normalization defaults to `unit`; timeout and batch size are optional controls. SQLite vector and hybrid search use the
bundled sqlite-vec extension. The Server probes it when opening the database, and startup fails if the installed library
is incompatible with the platform or SQLite build. Full-text search remains available without an embedding profile.
For configuration and capability verification, see [Configure vector search](../how-to/configure-vector-search.md).

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
