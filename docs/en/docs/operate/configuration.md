---
title: Configuration options
description: PowerContext paths, Server, Client, and inference environment variables.
---

# Configuration options

Windows support is `experimental`.

PowerContext reads configuration from environment variables when each process starts. `server run` loads `.env` from the
current working directory when that file exists. Pass `--env-file <path>` to select a different file without also
merging `.env`, or pass `--no-env-file` to disable file loading. For `server run`, CLI options override process
environment variables, process variables override values from the selected file, and defaults apply last. Agent hosts
can load their own environment files according to their host-specific rules.

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

The default SQLite database is `powercontext.db` in this directory. The four built-in background processors persist
intents and scheduling checkpoints there, alongside distributed Work Ledger leases and operation state. The former
`scheduler.db` sidecar is no longer part of execution. Existing installations require
[offline migration](artifact-processing-migration.md).

## Server

Server settings use the `POWERCONTEXT_SERVER_` prefix.

| Variable | Default | Meaning |
| --- | --- | --- |
| `POWERCONTEXT_SERVER_HTTP_HOST` | `127.0.0.1` | Listener address |
| `POWERCONTEXT_SERVER_HTTP_PORT` | `8000` | Listener port |
| `POWERCONTEXT_SERVER_WORKSPACE` | Server startup directory | Resolution root for local project Agent Skill folders |
| `POWERCONTEXT_SERVER_MCP_ENABLED` | `true` | Enable Streamable HTTP MCP |
| `POWERCONTEXT_SERVER_MCP_PATH` | `/mcp` | MCP path |
| `POWERCONTEXT_SERVER_DASHBOARD_ENABLED` | `false` | Personal and demonstration Dashboard; requires static Bearer authentication and does not support injected authentication or authorization Providers |
| `POWERCONTEXT_SERVER_AUTH_ENABLED` | `false` | Legacy static bearer switch; `true` maps to `ACCESS_MODE=enforced` and requires `AUTH_TOKEN` |
| `POWERCONTEXT_SERVER_AUTH_TOKEN` | unset | Legacy static bearer token; used as compatibility authentication and mapped to the built-in administrator when no Authentication Provider is injected |
| `POWERCONTEXT_SERVER_ACCESS_MODE` | `disabled` | The only supported Access switch: `disabled` or `enforced` |
| `POWERCONTEXT_SERVER_ACCESS_DEPLOYMENT_ID` | `powercontext` | Stable deployment identity used by the `server` Access Resource |
| `POWERCONTEXT_SERVER_ACCESS_BACKGROUND_PRINCIPAL_ID` | unset | Explicit service Principal for scheduled jobs in a multi-user enforced deployment |
| `POWERCONTEXT_SERVER_ACCESS_BACKGROUND_PRINCIPAL_DESCRIPTION` | unset | Optional display-only description for the scheduled service Principal |
| `POWERCONTEXT_SERVER_PUBLIC_URL` | unset | Remotely reachable base URL used by remote Skill enrollment guidance; HTTPS is required by default |
| `POWERCONTEXT_SERVER_ALLOW_INSECURE_HTTP` | `false` | Explicitly allow cleartext HTTP for remote Skill Receiver endpoints and guidance |
| `POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK` | `false` | Opt in to a non-loopback bind while authentication is disabled |
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
| `POWERCONTEXT_SERVER_DEPLOYMENT_MODE` | `single_node` | `single_node` or `distributed` process topology |
| `POWERCONTEXT_SERVER_DEPLOYMENT_ROLE` | `all` | `all`, `api`, `scheduler`, or `worker`; distributed mode forbids `all` |
| `POWERCONTEXT_SERVER_DEPLOYMENT_ID` | `local` | Non-secret operator instance label; boot ownership remains unique |
| `POWERCONTEXT_SERVER_DEPLOYMENT_BEHAVIOR_REVISION` | `default` | Non-secret rollout compatibility revision shared by all replicas |
| `POWERCONTEXT_SERVER_COORDINATION_SCHEDULER_LEASE_SECONDS` | `30` | Scheduler leader lease duration using database time |
| `POWERCONTEXT_SERVER_COORDINATION_SCHEDULER_RENEW_SECONDS` | `10` | Scheduler renewal interval; at most one third of the lease |
| `POWERCONTEXT_SERVER_COORDINATION_SCAN_PAGE_SIZE` | `100` | Maximum scopes inspected in one discoverer page |
| `POWERCONTEXT_SERVER_COORDINATION_MEMBER_TTL_SECONDS` | `30` | Runtime member advertisement lifetime |
| `POWERCONTEXT_SERVER_COORDINATION_MEMBER_HEARTBEAT_SECONDS` | `10` | Runtime member heartbeat interval |
| `POWERCONTEXT_SERVER_COORDINATION_EMIT_PAYLOAD_VERSION` | `1` | Work payload version emitted during a rolling deployment |
| `POWERCONTEXT_SERVER_WORKER_CONCURRENCY` | `4` | Maximum attempts executed concurrently by one Worker |
| `POWERCONTEXT_SERVER_WORKER_LEASE_SECONDS` | `120` | Worker claim lease duration |
| `POWERCONTEXT_SERVER_WORKER_HEARTBEAT_SECONDS` | `30` | Claim heartbeat interval; less than one third of the lease |
| `POWERCONTEXT_SERVER_WORKER_SHUTDOWN_GRACE_SECONDS` | `90` | Maximum graceful drain time; less than the lease |
| `POWERCONTEXT_SERVER_WORKER_MAX_ATTEMPTS` | `5` | Automatic attempt budget before operator recovery is required |
| `POWERCONTEXT_SERVER_WORKER_RETRY_BASE_SECONDS` | `2` | Full-jitter exponential retry base |
| `POWERCONTEXT_SERVER_WORKER_RETRY_MAX_SECONDS` | `300` | Full-jitter retry ceiling |
| `POWERCONTEXT_SERVER_WORKER_POLL_SECONDS` | `1` | Idle claim polling interval |
| `POWERCONTEXT_SERVER_OPERATIONS_DEFAULT_WAIT_SECONDS` | `10` | Default HTTP Memory flush wait |
| `POWERCONTEXT_SERVER_OPERATIONS_MAXIMUM_WAIT_SECONDS` | `30` | Maximum accepted `Prefer: wait=N` value |
| `POWERCONTEXT_SERVER_OPERATIONS_POLL_SECONDS` | `0.2` | Local operation completion polling interval |
| `POWERCONTEXT_SERVER_OPERATIONS_RETENTION_DAYS` | `30` | Successful and cancelled operation history retention |
| `POWERCONTEXT_SERVER_OPERATIONS_CLEANUP_BATCH_SIZE` | `500` | Maximum records removed by one maintenance attempt |
| `POWERCONTEXT_SERVER_OPERATIONS_CLEANUP_INTERVAL_SECONDS` | `3600` | Durable maintenance discovery interval |
| `POWERCONTEXT_SERVER_RATE_LIMIT_ENABLED` | `false` | Enable shared database fixed-window limiting |
| `POWERCONTEXT_SERVER_RATE_LIMIT_REQUESTS` | `120` | Requests allowed for one principal and policy window |
| `POWERCONTEXT_SERVER_RATE_LIMIT_WINDOW_SECONDS` | `60` | Shared rate-limit window duration |
| `POWERCONTEXT_SERVER_RUNTIME_SCOPE_CACHE_SIZE` | `128` | Inactive scope compositions retained by the Runtime; in-flight scopes are never evicted |
| `POWERCONTEXT_SERVER_RUNTIME_SOURCE_WINDOW_LIMIT` | `100` | Maximum Sources processed in one activation |
| `POWERCONTEXT_SERVER_RUNTIME_CONTEXT_ASSEMBLY_MAX_ENTRIES` | `8` | Maximum sum of explicit `assembly.sections[].limit`; positive integer. Per-family limits still apply. |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_EXTRACTION_PROFILE` | `coding` | Memory selection policy: `coding` or `conversation` |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_ENABLED` | `false` | Apply listwise reranking after coarse Memory retrieval |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_CANDIDATE_LIMIT` | `30` | Coarse candidate pool supplied to the reranker |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_SCHEDULE_SECONDS` | unset | Memory automatic admission interval; `SCHEDULE_SECONDS` remains a compatibility alias |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SCHEDULE_SECONDS` | unset | Topic Memory automatic admission interval; unset disables new automatic admission |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SOURCE_WINDOW_LIMIT` | `10` | Maximum Sources per Topic Memory Window, capped at 100; one Scope invocation can finish several Windows |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_HISTORY_MAX_CANDIDATES` | `20` | Maximum historical Topic candidates considered while processing |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_HISTORY_RRF_THRESHOLD` | `70` | RRF acceptance threshold normalized to `0..100` |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_HISTORY_MIN_CANDIDATES` | `5` | Minimum historical recall count when the threshold returns too few candidates |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_MAX_WORKERS` | `10` | Topic Worker quota; `ARTIFACT_PROCESSING_MAX_WORKERS` is its compatibility alias |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_WORKER_TIMEOUT_SECONDS` | `600` | Total Scope invocation timeout, including child startup; old `ARTIFACT_PROCESSING_WORKER_TIMEOUT_SECONDS` is its alias |
| `POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_ROLE` | `all` | Single-node Supervisor role: `all`, `api`, or `background`; distributed mode requires `all` because `DEPLOYMENT_ROLE` owns process separation |
| `POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_SUPERVISOR_MODE` | `global` | `global` owns one Lease; `dedicated` owns one Lease per registered Family |
| `POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_FAMILIES` | inferred from models | JSON Family list; API-only instances can declare capabilities without model credentials |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_MAX_WORKERS` | `1` | Independent Memory Worker quota |
| `POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_MAX_WORKERS` | `1` | Independent Experience Worker quota |
| `POWERCONTEXT_SERVER_RUNTIME_PROFILE_MAX_WORKERS` | `4` | Independent Profile Worker quota; alias `PROFILE_MAX_CONCURRENCY` |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_WORKER_TIMEOUT_SECONDS` | `600` | Total Memory Scope timeout |
| `POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_WORKER_TIMEOUT_SECONDS` | `600` | Total Experience Scope timeout |
| `POWERCONTEXT_SERVER_RUNTIME_PROFILE_WORKER_TIMEOUT_SECONDS` | `600` | Total Profile Scope timeout |
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
| `POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS` | unset | Experience automatic admission interval; unset preserves accepted work and stops new automatic admission |
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

Background Memory, Topic Memory, Experience, and Profile processing use the service Principal selected by
`ACCESS_BACKGROUND_PRINCIPAL_ID`, falling back to the fixed static Principal. That Principal must have
`scope.contribute` for each processed scope and write permission on existing Artifacts it changes. New entries,
Artifacts, and Candidates retain its ownership or owner attestation in the same transaction as processing completion.
An enforced deployment with background capabilities fails startup if its identity or authorization provider cannot
be reconstructed in a child process, even when automatic schedules are disabled: accepted work still needs recovery.
The built-in provider supports this reconstruction. Injected providers and model objects remain usable by synchronous
SDK/Server operations with background capabilities disabled (`ARTIFACT_PROCESSING_FAMILIES=[]`).

SDK workers without a Server identity do not require Server authorization dependencies. Built-in background workers
use the built-in Source definitions. A custom Source registry requires custom processing bindings for every enabled
family, or disabling built-in background families with `ARTIFACT_PROCESSING_FAMILIES=[]`; otherwise startup fails
before accepting work. Turning off schedules alone is insufficient because explicit requests still start workers.
Custom Source registries remain available to synchronous SDK contexts and API-only composition.

The authenticated `/metrics` endpoint exposes `powercontext_server_artifact_processing_*` observations with only a `family`
label: Worker capacity, ready/retry queues, unacknowledged Scopes, discovery and invocation duration, completions,
failures, and timeouts. Unacknowledged counts reflect the latest discovery; counters reset with the Supervisor instance.

Remote and multi-user deployments must use `enforced`. In that mode, HTTP, MCP, and metrics share one Server PEP.
`/v1/access/me` reports the `server`/`scope`/`artifact` Resource Kinds,
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

By default, the Server treats its startup directory as the workspace and exposes two writable local project targets:
`<workspace>/.agents/skills` for Codex and `<workspace>/.claude/skills` for Claude Code. Missing directories are harmless
and are created only by an explicit publication operation. Set `POWERCONTEXT_SERVER_WORKSPACE` once for systemd,
containers, or other launchers whose working directory is not the project.

Configure `POWERCONTEXT_SERVER_PUBLIC_URL` when remote Skill Receivers should connect through a stable externally
reachable origin. Enrollment commands may otherwise use the remote CLI's configured Server URL.

For a first-phase PoC on a protected internal test network, direct HTTP requires explicit consent on both sides. Set
`POWERCONTEXT_SERVER_ALLOW_INSECURE_HTTP=true`, advertise an `http://` `POWERCONTEXT_SERVER_PUBLIC_URL`, and bind the
listener to an address reachable by the target. The enrollment command must include
`remote-enroll --allow-insecure-http`. Without the
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

Handoff Report API routes are independently enabled by default. See
[Use Handoff Report](../workflows/use-handoff-report.md) for selection, inspection, and export.

The Artifact Processing Supervisor is enabled by the default `all` role in `single_node` deployment mode. A single-node
OceanBase deployment may split that Supervisor into `api` and `background` runtime roles;
`powercontext server run --role background` starts no HTTP, MCP, or Dashboard listener, and multiple background
candidates use the database Lease to elect one active Leader. This split is distinct from
`POWERCONTEXT_SERVER_DEPLOYMENT_MODE=distributed`, where `DEPLOYMENT_ROLE` selects `api`, `scheduler`, or `worker`, and
`ARTIFACT_PROCESSING_ROLE` must remain `all`.

In single-node mode, Memory, Topic Memory, Experience, and Profile use the Supervisor. In distributed mode, Memory,
Experience, and Profile use the Work Ledger instead; Topic Memory processing is not supported in distributed v1 and a
configured Topic Memory schedule is rejected at startup. SQLite and embedded seekDB support only the single-process
`all` role. Automatic Topic Memory waves remain disabled until a positive interval is set; explicit flush work remains
recoverable regardless of that interval. Topic workers need file-backed SQLite: configuring a generation model with an
in-memory SQLite database is rejected before processing is advertised. Use a persistent
`POWERCONTEXT_SERVER_DATABASE_URL`, such as `sqlite+aiosqlite:////srv/powercontext/runtime.db`. Every Supervisor Family
has its own quota and timeout; spare quota is not shared. Disabling automatic admission preserves already accepted
requests. Split Supervisor API and background instances must agree on mode, registered Families, and trigger
capabilities. Model resources are only required by execution processes. Changing Supervisor modes requires
[coordinated offline migration](artifact-processing-migration.md); mixed modes cannot start.
Conflicting explicit old/new configuration aliases fail startup; equal values are accepted.

Normal Runtime startup initializes and recovers the configured search indexes. Topic Workers reuse that database
without rebuilding the unrelated Memory/Experience search projections for each Window; Topic index validation and
publication guards still apply. If an empty database is reconfigured to another Topic retrieval shape or embedding
profile, reopen existing Runtimes with the same configuration: stale Runtimes reject Topic search, exact get, and
current-head browsing with a retrieval-shape error instead of reading another vector space.

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

Experience incubation has its own Supervisor binding and persisted Source cursor. Each invocation inspects a
finite window controlled by `SOURCE_WINDOW_LIMIT` and exposes only Content Sources whose metadata contains
`"kind": "task-outcome"` to the model. It creates pending Experience Candidates in the Review Inbox; it does not
approve them, place them in PreparedContext, create a managed Skill, export it to an Agent target, or execute anything.
Memory and Experience keep independent scheduling intervals, Worker quotas, and business cursors. In distributed mode,
their accepted operations use independent Work Ledger lanes and logical keys. Unsetting an interval stops new automatic
admission for that Family while preserving accepted work.
See [Create and review an Experience](../workflows/create-and-review-experience.md) for setup and verification steps.

### Distributed roles and migrations

Distributed mode requires OceanBase. Run `powercontext server migrate --env-file ...` with a DDL-capable account before
starting any role. Role processes never create or alter schema. Start or roll forward in this order: migrate, Workers,
Schedulers, then APIs. Use a new `POWERCONTEXT_SERVER_DEPLOYMENT_BEHAVIOR_REVISION` when a rollout changes non-secret
behavior that must not mix across replicas.

An API replica can remain ready enough to accept durable work while Scheduler or Worker members are absent; readiness
is `degraded` and names the missing role. Scheduler and Worker roles expose health and metrics only. Distributed MCP is
stateless and needs no load-balancer affinity. Host-local External Skill targets are rejected because replicas could
otherwise return different results.

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
packages, or grant execution authority. Custom targets default `allow_managed_publish` to `false`; when true, an
explicit publication operation may safely create or update an approved managed Skill in that target. Publication
materializes the exact reviewed package, including scripts and references, without executing it or injecting a sidecar
into the package. Unpublication succeeds only for an intact package whose binding and tree digest still match; local
drift and foreign content remain untouched. Publication cannot submit an arbitrary path or overwrite a foreign or
modified package. The
`host_id`, locator, and registration are local-environment state, not a cross-host contract. Existing `codex_roots`
configuration remains accepted as a Codex-only compatibility form; new configuration should use `targets`.

The optional `environment` object contains only observed, secret-free compatibility facts. Command values are version
labels, and `environment_names` records names only, never values. PowerContext does not probe or execute package scripts
to construct this profile. When it is absent, packages containing scripts report unknown compatibility; when present,
the Skills Library compares known script interpreters with the observed command names and returns a reasoned assessment.
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
