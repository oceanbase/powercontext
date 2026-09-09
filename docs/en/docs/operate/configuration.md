---
title: Configuration options
description: PowerContext paths, Server, Client, and inference environment variables.
---

# Configuration options

Windows support is `experimental`.

PowerContext reads configuration from environment variables when each process starts. The CLI does not search for a
`.env` file automatically. A command that accepts `--env-file` loads environment assignments from that file, including
Server and provider settings, and overrides same-named process values. Agent hosts can load their own environment files
according to their host-specific rules.

For the configuration-file workflow, including generation, redacted inspection, validation, and launch, see
[Configure a Server environment](../get-started/configure-server-environment.md). Treat every environment file as a
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
- macOS: `~/Library/Application Support/powercontext`;
- Windows: `%LOCALAPPDATA%\\powercontext`.

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
| `POWERCONTEXT_SERVER_AUTH_ENABLED` | `false` | Legacy static bearer switch; `true` maps to `ACCESS_MODE=enforced` and requires `AUTH_TOKEN` |
| `POWERCONTEXT_SERVER_AUTH_TOKEN` | unset | Legacy static bearer token; used as compatibility authentication and mapped to the built-in administrator when no Authentication Provider is injected |
| `POWERCONTEXT_SERVER_ACCESS_MODE` | `disabled` | The only supported Access switch: `disabled` or `enforced` |
| `POWERCONTEXT_SERVER_ACCESS_DEPLOYMENT_ID` | `powercontext` | Stable deployment identity used by the `server` Access Resource |
| `POWERCONTEXT_SERVER_ACCESS_BACKGROUND_PRINCIPAL_ID` | unset | Explicit service Principal for scheduled jobs in a multi-user enforced deployment |
| `POWERCONTEXT_SERVER_ACCESS_BACKGROUND_PRINCIPAL_DESCRIPTION` | unset | Optional display-only description for the scheduled service Principal |
| `POWERCONTEXT_SERVER_PUBLIC_URL` | unset | Remotely reachable base URL used by remote Skill enrollment guidance; HTTPS is required by default |
| `POWERCONTEXT_SERVER_ALLOW_INSECURE_HTTP` | `false` | Explicitly allow cleartext HTTP for remote Skill Receiver endpoints and guidance |
| `POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK` | `false` | Opt in to a non-loopback bind while authentication is disabled |
| `POWERCONTEXT_SERVER_DASHBOARD_ENABLED` | `true` | Enable the Dashboard at the Server root path `/` |
| `POWERCONTEXT_SERVER_HANDOFF_REPORT_ENABLED` | `true` | Enable Handoff Report and its API routes |
| `POWERCONTEXT_SERVER_LOGGING_LEVEL` | `INFO` | Operational log level |
| `POWERCONTEXT_SERVER_LOGGING_FORMAT` | `console` | `console` or structured `json` output |
| `POWERCONTEXT_SERVER_LOGGING_ACCESS` | `true` | Log external HTTP and logical MCP request completion |
| `POWERCONTEXT_SERVER_METRICS_ENABLED` | `true` | Expose Prometheus metrics at `/metrics` |
| `POWERCONTEXT_SERVER_TRACING_ENABLED` | `false` | Enable span recording and OTLP export |
| `POWERCONTEXT_SERVER_CURSOR_SIGNING_SECRET` | local persisted key | Shared secret of at least 32 bytes for signing REST pagination cursors |
| `POWERCONTEXT_SERVER_DATABASE_KIND` | `sqlite` | Storage backend: `sqlite`, `seekdb`, or `oceanbase` |
| `POWERCONTEXT_SERVER_DATABASE_URL` | user data SQLite file | SQLAlchemy async URL for SQLite or OceanBase; do not set for seekDB |
| `POWERCONTEXT_SERVER_DATABASE_PATH` | user data `seekdb` directory | Embedded seekDB path; used only when `DATABASE_KIND=seekdb` |
| `POWERCONTEXT_SERVER_RUNTIME_SCOPE_CACHE_SIZE` | `128` | Inactive scope compositions retained by the Runtime; in-flight scopes are never evicted |
| `POWERCONTEXT_SERVER_RUNTIME_SOURCE_WINDOW_LIMIT` | `100` | Maximum Sources processed in one activation |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_EXTRACTION_PROFILE` | `coding` | Memory selection policy: `coding` or `conversation` |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_ENABLED` | `false` | Apply listwise reranking after coarse Memory retrieval |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_CANDIDATE_LIMIT` | `30` | Coarse candidate pool supplied to the reranker |
| `POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS` | unset | Scheduler interval; unset disables scheduling |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SCHEDULE_SECONDS` | unset | Per-binding Topic Memory automatic-wave interval; unset disables automatic waves |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SOURCE_WINDOW_LIMIT` | `10` | Maximum Sources assigned to one Topic Memory Worker, capped at 100 |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_HISTORY_MAX_CANDIDATES` | `20` | Maximum historical Topic candidates considered while processing |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_HISTORY_RRF_THRESHOLD` | `70` | RRF acceptance threshold normalized to `0..100` |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_HISTORY_MIN_CANDIDATES` | `5` | Minimum historical recall count when the threshold returns too few candidates |
| `POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_MAX_WORKERS` | `10` | Global child-Worker concurrency across Artifact bindings |
| `POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_WORKER_TIMEOUT_SECONDS` | `600` | Supervisor timeout for one child Worker's bounded Source Window |
| `POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_ROLE` | `all` | Process role: `all`, `api`, or `background` |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL` | unset | Pydantic AI model used by configured extraction, generation, Handoff, and reranking operations |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL` | provider default | Custom generation provider base URL |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_HEADERS` | `{}` | JSON object of static generation client headers; values are secrets |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL_SETTINGS` | `{}` | JSON object of Pydantic AI generation model settings |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_TIMEOUT_SECONDS` | `30` | Timeout in seconds for one structured generation operation |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MAX_REQUESTS` | `2` | Maximum provider requests for one structured generation operation, including retries |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL_CONTEXT_WINDOW_TOKENS` | `125000` | Total generation-model context window used to budget Topic processing |
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

Topic Workers enforce a durable allowance per unadvanced Scope Cursor: 3 attempts, 512 reserved provider requests,
and 64,000,000 estimated token-capacity units across all retries. A Window admits at most 4,194,304 canonical evidence
characters including metadata; nested input is also bounded. Exhaustion preserves Sources, Cursor, Pending, and the
same-Scope tail, and stops further provider calls. Flush and restart do not reset it; inspect
`pc_topic_memory_work_budgets` and structured errors for operator remediation.

Topic generation accepts `max_tokens`, `temperature`, `top_p`, `top_k`, `seed`, `presence_penalty`, `frequency_penalty`,
`timeout`, `openai_reasoning_effort`, `openai_text_verbosity`, `service_tier`, `openai_service_tier`,
`anthropic_service_tier`, and `anthropic_effort` as bounded scalar settings. Topic Embedding accepts only `dimensions`
and `truncate`. Background/hidden-history/native-tool settings and `extra_body` disable Topic processing while ordinary
inference continues; explicitly configured automatic Topic scheduling fails startup instead. Supported
provider prefixes are `openai`, `openai-chat`, `openai-responses`, `anthropic`, `azure`, `azure-responses`, `deepseek`,
and `openrouter`, plus the local `test` model; Embedding must also be supported by its SDK adapter. Topic SDK transport
retries and automatic continuations are disabled. Non-Topic inference keeps its existing settings behavior.

When the cursor signing secret is unset, a file-backed SQLite Server creates a private key beside its database;
other persistent backends create one in the PowerContext user data directory. In-memory SQLite uses a process-local
key. Configure the same `POWERCONTEXT_SERVER_CURSOR_SIGNING_SECRET` on every replica so a cursor remains valid after
restart or when the next request reaches another replica. Never expose or rotate this value while issued cursors
must remain valid.

Access Control is disabled by default. In `enforced` mode, API and MCP requests must establish a Principal through the
selected Authentication Provider; the liveness and readiness endpoints remain public. The built-in `static-bearer`
Provider accepts `Authorization: Bearer <token>`. Plain HTTP is trusted only on a
loopback address (`localhost`, `::1`, or any address in `127.0.0.0/8`). The Server refuses to start when it binds to a
non-loopback address while authentication is disabled; either enable authentication, keep the bind on loopback, or,
when TLS is terminated upstream or the network is otherwise controlled, set
`POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true` to opt in explicitly. Use TLS before exposing an
authenticated Server over a network.

`POWERCONTEXT_SERVER_ACCESS_MODE` is the only supported switch. `disabled` bypasses authorization decisions inside the
trusted local boundary. `enforced` enables one policy enforcement point plus Binding and audit behavior. Authorization
defaults to the built-in implementation and can be replaced through `create_server_app(access_control=...)`;
Authentication is supplied through `create_server_app(authentication_provider=...)`. Without an injected Authentication
Provider, the Server accepts only the legacy `AUTH_TOKEN` fallback and bootstraps its fixed `server-token` Principal as a
built-in administrator. Startup fails when neither is available. The old `AUTH_ENABLED=true` plus `AUTH_TOKEN`
configuration maps automatically to `ACCESS_MODE=enforced`.

Authentication establishes a Principal; Access Control decides what that Principal may do. Principal IDs are
deployment-wide unique, non-reused identifiers; `description` is display metadata and is not part of identity. The
built-in static token always represents one service Principal, so it cannot distinguish user A from user B. The
compatibility token materializes explicit Server and per-scope roles for that Principal. Inject the deployment
Authentication Provider and corresponding AccessControlService when different users or groups need different access.

Scheduled Source processing and Experience incubation run as the fixed static Principal, or as the service Principal
selected by `ACCESS_BACKGROUND_PRINCIPAL_ID`. That Principal must have `scope.contribute` for each processed scope;
new Memory entries and Candidates retain it as their direct proposed owner. An enforced multi-user deployment that
configures a schedule without this explicit Principal fails at startup.

Remote, multi-user, and shared-Dashboard deployments must use `enforced`. In that mode, HTTP, MCP, Dashboard data
routes, and metrics share one Server PEP. Configured Dashboard scopes are filtered by the current Principal's
`scope.read` decision before they are returned. `/v1/access/me` reports the `server`/`scope`/`artifact` Resource Kinds,
Provider batch/list/relationship capabilities and Artifact Family profiles. Managed Skill export and installation do
not introduce separate Access actions: the recipient first needs `artifact.read` on the logical Skill identity, then
chooses whether and how to install an exact Revision.

The built-in Access schema uses the configured SQLite, seekDB, or OceanBase backend, but remains Server-owned rather
than becoming a Runtime domain. A custom deployment can inject an `AccessControlService` into `create_server_app`.
`CasbinAuthorizationProvider` is the included writable external adapter: it evaluates the fixed action vocabulary in
embedded Casbin while using the canonical Binding Store as its persistent adapter, so it supports point/batch checks,
safe resource filters, create/revoke, expiry, and CAS without a second policy shadow. Pass that provider as both the
decision provider and `relationships`, and retain the relational repository as the audit store.

`AuthZenAuthorizationProvider` is an included decision-only adapter for the OpenID AuthZEN Authorization API 1.0
`evaluation` and `evaluations` endpoints. Configure its capabilities with `multi_requirement_check=true`,
`relationship_management=false`, and `safe_resource_filtering=false`; self-service Binding mutation and authorized
resource listing then return 503 instead of claiming an unsafe capability. The adapter accepts HTTPS endpoints or
loopback HTTP, rejects credentials embedded in URLs, and does not expose PDP response bodies or errors. An
authentication middleware must still bind an opaque `PrincipalRef`; `scope_id` is only a resource partition and never
establishes identity.

The Python Client and CLI apply the matching rule for general outbound requests: a configured unencrypted `http://`
Server URL is accepted only for loopback hosts. The explicit remote Skill Receiver PoC exception is documented below.
Code whose `http://` base URL is only a routing label for a transport that is secure in practice, such as an in-process
ASGI app, Unix-domain socket, or TLS-terminating proxy, must supply its own `http_client` and pass
`trust_transport_security=True` explicitly. See
[Deploy the Server](deploy-server.md) for a safe Docker and remote-access setup.

The Dashboard is enabled by default and shares the Server listener and port with the HTTP API and MCP. It discovers
the default Scope and every created Scope from the Server. Dashboard initialization failures are logged with their
direct cause and do not prevent the Server HTTP API, MCP, or health checks from starting.

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

When compatibility static Bearer authentication is enforced, the HTML shells at `/`, `/topics`, `/skills`, `/reviews`, and `/handoff-reports`, plus
their static assets, remain public so the browser can render the sign-in form. Data requests stay protected. Enter the
Server token in that form; the browser keeps it only in the current tab's session storage. Disable both Dashboard and
Handoff Report if even these sign-in pages must not be exposed.

Dashboard scopes are a UI discovery list, not an authorization boundary. The optional bearer token is Server-wide,
not per user or per scope. The private Topic Memory support routes accept only configured Dashboard scopes, while the
public API continues to apply its existing scope contract. A deployment that requires per-user or per-scope access
control must provide that boundary separately.

Handoff Report is independently enabled by default at `/handoff-reports`. When no scope contains a committed Handoff,
it shows a data-free template preview. See [Use Handoff Report](../workflows/use-handoff-report.md) for scope discovery,
inspection, Revision writes, and export.

The Artifact Processing Supervisor is enabled by the default `all` role. OceanBase deployments may run `api` and
`background` separately; `powercontext server run --role background` starts no HTTP, MCP, or Dashboard listener, and
multiple background candidates use the database Lease to elect one active Leader. SQLite and embedded seekDB support
only the single-process `all` role. Automatic Topic Memory waves remain disabled until a positive interval is set;
explicit flush work remains recoverable regardless of that interval. Topic workers need file-backed SQLite: configuring
a generation model with an in-memory SQLite database is rejected before processing is advertised. Use a persistent
`POWERCONTEXT_SERVER_DATABASE_URL`, such as `sqlite+aiosqlite:////srv/powercontext/runtime.db`.
Memory, Experience, and Profile APScheduler jobs belong exclusively to `all`: configuring
`POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS`, `POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS`, or enabling
`POWERCONTEXT_SERVER_RUNTIME_PROFILE_SCHEDULE_ENABLED` with either split role is rejected at startup. Keep `all`
when those jobs are required; assigning them to a separate process is outside the current split-role contract.

Normal Runtime startup initializes and recovers the configured search indexes. Topic Workers reuse that database
without rebuilding the unrelated Memory/Experience search projections for each Window; Topic index validation and
publication guards still apply. If an empty database is reconfigured to another Topic retrieval shape or embedding
profile, reopen existing Runtimes with the same configuration: stale Runtimes reject Topic search, exact get, and
current-head browsing with a retrieval-shape error instead of reading another vector space.

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
See [Create and review an Experience](../workflows/create-and-review-experience.md) for setup and verification steps.

### Agent Skill targets

The zero-configuration flow uses the Codex and Claude Code project folders under the workspace. Provide a JSON override
only for custom paths, user-level targets, environment compatibility facts, or to explicitly disable local discovery.
For a basic JSON shape and verification flow, see
[Configure Agent Skill targets](../workflows/configure-agent-skill-targets.md). A compatibility-aware override looks like:

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
[Trace with Phoenix](trace-with-phoenix.md) for a working configuration, and
[Trace with Langfuse](trace-with-langfuse.md) for a backend that authenticates the exporter through
`OTEL_EXPORTER_OTLP_HEADERS`.

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
For configuration and capability verification, see [Configure vector search](../workflows/configure-vector-search.md).

## CLI Server connection

| Variable | Default | Meaning |
| --- | --- | --- |
| `POWERCONTEXT_CLIENT_SERVER_URL` | `http://127.0.0.1:8000` | Server base URL |
| `POWERCONTEXT_CLIENT_API_TOKEN` | unset | Bearer token sent to an authenticated Server |
| `POWERCONTEXT_CLIENT_TIMEOUT` | `10` | HTTP timeout in seconds |

Equivalent one-off flags are available for the Server URL and timeout on `powercontext`. The token is accepted
only through the environment so it does not appear in command-line arguments.

## Agent integrations

For installation, connection, authentication, and environment variables, use the [guide for your integration](../integrations/index.md).
