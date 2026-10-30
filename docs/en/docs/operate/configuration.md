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

## Atomic Memory

See [Use Atomic Memory](../workflows/atomic-memory.md) for creation, search, lifecycle and restoration examples,
and [offline migration](atomic-memory-migration.md) for existing databases.

Restoration previews require an explicit shared signing key. Every process that generates or validates tokens must
use the same secret and key ID:

| Variable | Default | Meaning |
| --- | --- | --- |
| `POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_PREVIEW_SIGNING_SECRET` | unset | Secret of at least 32 characters; without it, previews and token validation return `422 invalid_preview`, while direct restoration remains available |
| `POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_PREVIEW_SIGNING_KEY_ID` | `atomic-memory-v1` | Current key ID, 1–128 characters; deployment configuration loads only this one key |
| `POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_PREVIEW_TTL_SECONDS` | `300` | Preview lifetime, 1–3600 seconds |
| `POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_RESTORE_RETRY_BUDGET` | `3` | Re-read and preparation retries after `atomic_memory_changed` during restoration without a token, 0–10; the default permits the initial attempt plus 3 retries |
| `POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_RELATED_MODE` | `auto` | Related-memory recall during Source processing: `auto`, `fts`, `vector` or `hybrid`; auto selects fts without a profile and hybrid with one |
| `POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_RELATED_MAX_DISTANCE` | `1.0` | Maximum exact L2 distance for complete related-vector enumeration, nonnegative; does not apply to ordinary searches with a limit |
| `POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_RELATED_FTS_FALLBACK` | `false` | Explicitly permit full-text enumeration when the related query vector is unavailable, profiles mismatch or vector projections are incomplete; ordinary search error handling is unchanged |
| `POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_COMPARISON_BATCH_SIZE` | `20` | Maximum related objects per comparison call, positive integer; it is not a total candidate limit |
| `POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_INPUT_TOKENS_LIMIT` | `24000` | Estimated tokens per extraction/comparison input, including instructions and complete input, positive integer |

An unset secret never generates a random process key. Changing the secret or key ID invalidates old tokens; preview
again. A restoration with a token returns `preview_stale` on changes instead of reinterpreting the intent. The retry
budget does not replay a write whose commit outcome is unknown after a connection failure.

Source processing generates candidates, then enumerates related active memories satisfying permission, tag and
retrieval eligibility. Fts enumerates all qualifying matches; vector enumerates all results within its threshold;
hybrid combines both. Neither comparison batch size nor token budget truncates the total candidates. Oversized
comparison input reduces the batch until every candidate is processed. A single object or complete extraction input
that exceeds the budget fails the window without partial publication or cursor advancement. Fallback changes recall
evidence and requires an explicit choice; by default errors retain the window for diagnosis and retry.

Atomic Memory has no legacy collection capacity budget or compact operation. `MEMORY_MAX_ACTIVE_ENTRIES`,
`MEMORY_MAX_MANIFEST_ENTRIES`, `MEMORY_MAX_MANIFEST_BYTES` and `MEMORY_COMPACTION_*` settings remain parseable but do not
limit new memories or enable compaction. MemoryWriteGate is fail-open and only governs the write decision; it never
changes authoritative history. For a hosted decision backend, set `MEMORY_WRITE_GATE_PRIVACY_BOUNDARY=hosted_redacted`
and explicitly select `MEMORY_WRITE_GATE_SANITIZER=known_secrets`. The sanitizer covers known sensitive environment/Codex
values and a bounded set of provider-key prefixes; it is not a general PII or password detector. The sanitizer selection
defaults to `none`, and hosted-redacted composition fails with a named configuration error until one is selected.
The existing limit of 32 tags per Artifact still applies. If merged input tags exceed it, the whole merge fails;
Source processing also retains the window without partial publication or cursor advancement.

## User data

`POWERCONTEXT_HOME` overrides the directory used by the installed Server:

```bash
export POWERCONTEXT_HOME=/srv/powercontext
```

Without an override, the default is:

- Linux: `$XDG_DATA_HOME/powercontext`, or `~/.local/share/powercontext`;
- macOS: `~/Library/Application Support/powercontext`;
- Windows: `%LOCALAPPDATA%\\powercontext`.

The default SQLite database is `powercontext.db` in this directory. Built-in background processors persist
intents and scheduling checkpoints in the same database. Existing installations require [offline migration](artifact-processing-migration.md).

The built-in SQLite backend requires Python's linked SQLite library (`sqlite3.sqlite_version`) to be **3.38.0 or newer**,
with FTS5, JSON functions, and loadable sqlite-vec for vector search. This retains the backend minimum specified in
[the Memory design](../../rfcs/0014_memory_layer_design.md#sqlite-adapter), including native vector `IN` filtering.
Older libraries are rejected before database initialization or accepting writes.

## Server

Server settings use the `POWERCONTEXT_SERVER_` prefix.

Each Server process admits at most 64 concurrent external HTTP requests, including REST, MCP, Dashboard, and health
requests. A request occupies a slot while its body is read and until its response finishes, including streaming
responses. Saturation returns `503` with error code `server_overloaded` and `Retry-After: 1` immediately, without
queueing or reading the rejected request body. Exceptions and cancellation release the slot. Trusted in-process
MCP-to-HTTP projections reuse the external request's admission.

Before downstream parsing, the Server checks the actual body against a 16 MiB limit, including chunked uploads.
An oversized declared `Content-Length` is rejected early; an absent or smaller declared length cannot bypass the
actual-byte check. Oversized requests return `413` with error code `request_too_large`, and reading stops at the
first excess chunk. This deployment-level cap is configurable and does not change domain field limits. The default
fits the current 5 MiB Skill archive limit, whose base64 representation takes at most 6,990,508 characters, plus its
JSON envelope. Lower caps can reject otherwise valid operations. Both HTTP limits must be positive integers.
Size them for available memory: 64 requests retaining 16 MiB each already represent 1 GiB of body data, before
parsing and application overhead; replay briefly copies each accepted body once before releasing its input buffer.
Reverse proxies own request-rate quotas, upload timeouts, connection limits,
and aggregate limits across Server processes.

MCP uses stateless Streamable HTTP so each tool execution receives the current request's authenticated identity.
It does not issue transport session IDs or retain resumable cross-request SSE sessions. Reconnect clients after
upgrading from a stateful Server. Persisted Scope, Memory and Handoff data are unaffected. A long-lived response
still occupies an HTTP slot until it finishes; reserve other slots for concurrent requests.

| Variable | Default | Meaning |
| --- | --- | --- |
| `POWERCONTEXT_SERVER_HTTP_HOST` | `127.0.0.1` | TCP listener address; ignored when `HTTP_UDS` is set |
| `POWERCONTEXT_SERVER_HTTP_PORT` | `8000` | TCP listener port; ignored when `HTTP_UDS` is set; the configuration wizard suggests `17429` for new setups |
| `POWERCONTEXT_SERVER_HTTP_UDS` | unset | HTTP Unix socket path for `server run` on Linux/macOS; selects UDS only, with no TCP listener; absolute path, at most 103 filesystem-encoded bytes, without `..` or NUL |
| `POWERCONTEXT_SERVER_HTTP_UDS_EXTERNAL_SCHEME` | unset | Operator-declared external `http` or `https` scheme for a trusted proxy on the private UDS; requires `HTTP_UDS`. Set `https` only with trusted TLS termination. Forwarded scheme and client-IP headers remain ignored |
| `POWERCONTEXT_SERVER_HTTP_MAX_REQUEST_BODY_BYTES` | `16777216` | Maximum external HTTP body bytes before parsing, including chunked uploads |
| `POWERCONTEXT_SERVER_HTTP_MAX_CONCURRENT_REQUESTS` | `64` | Per-process external HTTP requests in flight, including full streaming responses |
| `POWERCONTEXT_SERVER_WORKSPACE` | Server startup directory | Resolution root for local project Agent Skill folders |
| `POWERCONTEXT_SERVER_MCP_ENABLED` | `true` | Enable Streamable HTTP MCP |
| `POWERCONTEXT_SERVER_MCP_PATH` | `/mcp` | MCP path |
| `POWERCONTEXT_SERVER_DASHBOARD_ENABLED` | `false` | Personal and demonstration Dashboard; local `ACCESS_MODE=disabled` needs no token, while `enforced` requires a static Bearer token; injected authentication or authorization Providers are unsupported |
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
| `POWERCONTEXT_SERVER_LOGGING_FILE` | `false` | Write managed files instead of stdout; enabled by default for macOS/Windows personal services |
| `POWERCONTEXT_SERVER_LOGGING_DIRECTORY` | User data directory + `logs/` | Managed log directory |
| `POWERCONTEXT_SERVER_LOGGING_MAX_BYTES` | `20971520` | Positive size threshold for rotation |
| `POWERCONTEXT_SERVER_LOGGING_BACKUP_COUNT` | `10` | Positive maximum archive count per active file |
| `POWERCONTEXT_SERVER_LOGGING_COMPRESSION` | `gzip` | Archive compression: `gzip` or `none` |
| `POWERCONTEXT_SERVER_METRICS_ENABLED` | `true` | Expose Prometheus metrics at `/metrics` |
| `POWERCONTEXT_SERVER_METRICS_BACKGROUND_PORT` | `8001` | Background-role infrastructure listener port; uses `HTTP_HOST` and is disabled with metrics |
| `POWERCONTEXT_SERVER_TRACING_ENABLED` | `false` | Enable span recording and OTLP export |
| `POWERCONTEXT_SERVER_CURSOR_SIGNING_SECRET` | local persisted key | Shared secret of at least 32 bytes for signing REST pagination cursors |
| `POWERCONTEXT_SERVER_DATABASE_KIND` | `sqlite` | Storage backend: `sqlite`, `seekdb`, or `oceanbase` |
| `POWERCONTEXT_SERVER_DATABASE_URL` | user data SQLite file | SQLAlchemy async URL for SQLite or OceanBase; do not set for seekdb |
| `POWERCONTEXT_SERVER_DATABASE_PATH` | user data `seekdb` directory | Embedded seekdb path; used only when `DATABASE_KIND=seekdb` |
| `POWERCONTEXT_SERVER_DATABASE_DATABASE` | `powercontext` | seekDB database name; explicitly set `test` to retain legacy data |
| `POWERCONTEXT_SERVER_DATABASE_BUSY_TIMEOUT_MS` | `5000` | Milliseconds a business connection waits for SQLite's single write lock before raising; it does not govern usage accounting, which has its own bounded budget |
| `POWERCONTEXT_SERVER_RUNTIME_SCOPE_CACHE_SIZE` | `128` | Inactive scope compositions retained by the Runtime; in-flight scopes are never evicted |
| `POWERCONTEXT_SERVER_RUNTIME_SOURCE_WINDOW_LIMIT` | `100` | Maximum Source journal positions per activation; Memory reduces its window after generation timeouts |
| `POWERCONTEXT_SERVER_RUNTIME_CONTEXT_ASSEMBLY_MAX_ENTRIES` | `8` | Maximum sum of explicit `assembly.sections[].limit`; positive integer. Per-family limits still apply. |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_EXTRACTION_PROFILE` | `coding` | Memory selection policy: `coding` or `conversation` |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_ENABLED` | `false` | Apply listwise reranking after coarse Memory retrieval |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_CANDIDATE_LIMIT` | `30` | Coarse candidate pool supplied to the reranker |
| `POWERCONTEXT_SERVER_RUNTIME_DECISION_ASSISTANCE_ENABLED` | `false` | Enable decision-model assistance; requires a decision or generation model |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_WRITE_GATE_ENABLED` | `false` | Enable the fail-open Memory write decision gate |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_WRITE_GATE_HOLD_ON` | `yes` | Decision outcome that means evidence is insufficient: `yes` or `no` |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_WRITE_GATE_THRESHOLD` | unset | Optional confidence threshold from `0` to `1`; a hold-direction verdict below it is flagged instead of held |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_WRITE_GATE_PRIVACY_BOUNDARY` | `no_external_call` | Decision egress boundary: `no_external_call` or `hosted_redacted` |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_WRITE_GATE_SANITIZER` | `none` | Required for `hosted_redacted`; choose `known_secrets` to install the built-in projection sanitizer |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_MAX_ACTIVE_ENTRIES` | `5000` | Legacy collection setting; does not limit Atomic Memory, but parsing still validates it against the manifest limit |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_MAX_MANIFEST_ENTRIES` | `10000` | Legacy collection setting; Atomic Memory has no collection manifest |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_MAX_MANIFEST_BYTES` | `4194304` | Legacy collection setting; does not limit total Atomic Memory content |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_COMPACTION_ENABLED` | `false` | Legacy collection setting; Atomic Runtime has no compact operation |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_COMPACTION_MIN_TOMBSTONE_REVISIONS` | `10` | Legacy collection setting; Atomic Runtime has no compact operation |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_MAX_HISTORY_REVISIONS` | `100` | Maximum Memory history Revisions read by the Runtime |
| `POWERCONTEXT_SERVER_RUNTIME_RECALL_GATE_ENABLED` | `false` | Enable the optional recall-sufficiency gate; disabling it keeps recall identical to a deployment without the feature |
| `POWERCONTEXT_SERVER_RUNTIME_RECALL_GATE_MAX_ROUNDS` | `2` | Most expansion rounds after the first recall; `0` to `2`, where `0` assesses without expanding |
| `POWERCONTEXT_SERVER_RUNTIME_RECALL_GATE_MIN_CANDIDATES` | `2` | Fewest candidates a recall needs to count as sufficient |
| `POWERCONTEXT_SERVER_RUNTIME_RECALL_GATE_MIN_TOP_SCORE` | `0.35` | Lowest top-candidate score still accepted as sufficient |
| `POWERCONTEXT_SERVER_RUNTIME_RECALL_GATE_MIN_TOP_GAP` | `0.02` | Smallest family-local top-minus-mean score gap required; the gate uses the maximum across scoring families |
| `POWERCONTEXT_SERVER_RUNTIME_RECALL_GATE_MIN_LEXICAL_OVERLAP` | `0.5` | Lowest lexical coverage still accepted as sufficient |
| `POWERCONTEXT_SERVER_RUNTIME_RECALL_GATE_ROUND1_MIN_SEMANTIC_SIMILARITY` | `0.15` | Semantic-similarity admission floor for the first expansion round; must not exceed the round-zero `0.3` |
| `POWERCONTEXT_SERVER_RUNTIME_RECALL_GATE_ROUND2_MIN_SEMANTIC_SIMILARITY` | `0.10` | Semantic-similarity admission floor for the second expansion round; must not exceed the first round's value |
| `POWERCONTEXT_SERVER_RUNTIME_RECALL_GATE_ALLOW_WITH_RERANK` | `false` | Whether expansion may still run when `MEMORY_RERANK_ENABLED` is set; by default reranking ends the expansion |
| `POWERCONTEXT_SERVER_RUNTIME_RECALL_FTS_WEIGHT` | `1.0` | Relative FTS contribution in hybrid Atomic Memory and Topic Memory RRF; `0` excludes FTS-only matches from hybrid results |
| `POWERCONTEXT_SERVER_RUNTIME_RECALL_VECTOR_WEIGHT` | `1.0` | Relative vector contribution in hybrid Atomic Memory and Topic Memory RRF; `0` excludes vector-only matches from hybrid results |
| `POWERCONTEXT_SERVER_RUNTIME_RECALL_EMBEDDING_TIMEOUT_SECONDS` | `0.5` | Seconds one context preparation waits for query embeddings in total, at most `60`. The Atomic Memory searches of the Scope and each of its context references and the Topic Memory search share this budget, and only the time spent waiting for an embedding is charged to it: each search waits only for what the earlier ones left, so the preparation's wait stays within it however many Scopes are searched, while database reads and reranking between the searches do not use it up. The search whose embedding wait spends the budget logs `memory.search.embedding_fallback` or `topic_memory.search.embedding_fallback` once. A later search in the same preparation sends no new embedding request and logs no fallback event: it still uses a query vector an earlier search obtained, and otherwise continues with lexical matching only. The default covers a typical remote embedding round trip; keep it below the prepare request timeout of the hosts in use, the shortest default being 1 second on Kimi Code, ZCode, and DSH |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_SCHEDULE_SECONDS` | unset | Memory automatic admission interval; `SCHEDULE_SECONDS` remains a compatibility alias |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SCHEDULE_SECONDS` | unset | Topic Memory automatic admission interval; unset disables new automatic admission |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SOURCE_WINDOW_LIMIT` | `10` | Maximum Sources per Topic Memory Window, capped at 100; one Scope invocation can finish several Windows |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_HISTORY_MAX_CANDIDATES` | `20` | Maximum historical Topic candidates considered while processing |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_HISTORY_RRF_THRESHOLD` | `70` | RRF acceptance threshold normalized to `0..100` |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_HISTORY_MIN_CANDIDATES` | `5` | Minimum historical recall count when the threshold returns too few candidates |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_MAX_WORKERS` | `10` | Topic Worker quota; `ARTIFACT_PROCESSING_MAX_WORKERS` is its compatibility alias |
| `POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_WORKER_TIMEOUT_SECONDS` | `600` | Total Scope invocation timeout, including child startup; old `ARTIFACT_PROCESSING_WORKER_TIMEOUT_SECONDS` is its alias |
| `POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_ROLE` | `all` | Process role: `all`, `api`, or `background` |
| `POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_SUPERVISOR_MODE` | `global` | `global` owns one Lease; `dedicated` owns one Lease per registered Family |
| `POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_FAMILIES` | inferred from models | JSON Family list; API-only instances can declare capabilities without model credentials |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_MAX_WORKERS` | `1` | Independent Memory Worker quota |
| `POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_MAX_WORKERS` | `1` | Independent Experience Worker quota |
| `POWERCONTEXT_SERVER_RUNTIME_SKILL_MAX_WORKERS` | `1` | Skill Worker quota for Dream derivation |
| `POWERCONTEXT_SERVER_RUNTIME_SKILL_WORKER_TIMEOUT_SECONDS` | `600` | Total Skill Scope invocation timeout |
| `POWERCONTEXT_SERVER_RUNTIME_DREAM_ENABLED` | `true` | Accept explicit Dream requests for declared Experience/Skill Families; no automatic artifact selection |
| `POWERCONTEXT_SERVER_RUNTIME_DREAM_MAX_PENDING_PER_SCOPE` | `32` | Combined queued and running DreamRun limit per Scope |
| `POWERCONTEXT_SERVER_RUNTIME_DREAM_BUDGET` | `{}` | JSON budget; may tighten evidence limits, at most 2 model calls and 120 seconds from first execution |
| `POWERCONTEXT_SERVER_RUNTIME_GENERATION_CONCURRENCY` | `4` | Foreground Runtime generation concurrency; background Workers use per-Family quotas |
| `POWERCONTEXT_SERVER_RUNTIME_PROFILE_MAX_WORKERS` | `4` | Independent Profile Worker quota; alias `PROFILE_MAX_CONCURRENCY` |
| `POWERCONTEXT_SERVER_RUNTIME_MEMORY_WORKER_TIMEOUT_SECONDS` | `600` | Total Memory Scope timeout |
| `POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_WORKER_TIMEOUT_SECONDS` | `600` | Total Experience Scope timeout |
| `POWERCONTEXT_SERVER_RUNTIME_PROFILE_WORKER_TIMEOUT_SECONDS` | `600` | Total Profile Scope timeout |
| `POWERCONTEXT_SERVER_RUNTIME_MODEL_USAGE_QUEUE_CAPACITY` | `256` | Accepted model-usage records a Runtime holds in memory; further records are dropped with a diagnostic |
| `POWERCONTEXT_SERVER_RUNTIME_MODEL_USAGE_WRITE_TIMEOUT_SECONDS` | `1` | Budget for one usage record's own statistics transaction, spent in slices so a busy writer gets several attempts |
| `POWERCONTEXT_SERVER_RUNTIME_MODEL_USAGE_FLUSH_TIMEOUT_SECONDS` | `0.5` | Bounded wait for already-accepted usage at an operation-completion or statistics-read boundary |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL` | unset | Pydantic AI model used by configured extraction, generation, Handoff, and reranking operations |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL` | provider default | Custom generation provider base URL |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_HEADERS` | `{}` | JSON object of static generation client headers; values are secrets |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL_SETTINGS` | `{}` | JSON object of Pydantic AI generation model settings |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_TIMEOUT_SECONDS` | `30` | Timeout in seconds for one structured generation operation |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MAX_REQUESTS` | `2` | Maximum provider requests for one structured generation operation, including retries |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_STREAMING` | `disabled` | When `auto`, only generation models whose provider prefix is `anthropic`, `openai`, `openai-chat`, or `openai-responses` use a streamed request aggregated locally into one complete response. Other prefixes stay on non-streaming requests. `disabled` keeps every model on non-streaming requests |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_STREAM_IDLE_TIMEOUT_SECONDS` | `60` | Maximum idle seconds while aggregating a streamed request: while the stream opens and waits for its first model event, and between model events (text, thinking, or tool-call parts). Provider heartbeat frames do not reset this timer; a longer gap fails as a timeout |
| `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL_CONTEXT_WINDOW_TOKENS` | `125000` | Total generation-model context window used to budget Topic processing |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL` | unset | Pydantic AI embedding model; requires profile ID and dimension |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_BASE_URL` | provider default | Custom OpenAI-compatible embeddings base URL |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_HEADERS` | `{}` | JSON object of static embedding client headers; values are secrets |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL_SETTINGS` | `{}` | JSON object of Pydantic AI embedding model settings |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID` | unset | Stable identity for the model, dimension, and normalization used by the vector index |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION` | unset | Positive output dimension stored and validated. Also sent as the `dimensions` request field by default |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_SEND_DIMENSIONS` | `true` | Whether embedding requests include `dimensions`. When false, returned vectors are still checked against the configured dimension. Set false for fixed-dimension models such as SiliconFlow `BAAI/bge-m3` |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_NORMALIZATION` | `unit` | Vector normalization: `unit` or `none` |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_TIMEOUT_SECONDS` | `30` | Timeout in seconds for one embedding request |
| `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_BATCH_SIZE` | `10` | Maximum texts sent in one embedding request |
| `POWERCONTEXT_SERVER_INFERENCE_RERANK_MODEL` | generation model | Optional dedicated Pydantic AI model for LLM reranking |
| `POWERCONTEXT_SERVER_INFERENCE_RERANK_BASE_URL` | inherited/provider default | Custom LLM reranker provider base URL |
| `POWERCONTEXT_SERVER_INFERENCE_RERANK_HEADERS` | `{}` | JSON object of static LLM reranker client headers; values are secrets |
| `POWERCONTEXT_SERVER_INFERENCE_RERANK_MODEL_SETTINGS` | `{}` | JSON object of Pydantic AI reranker model settings |
| `POWERCONTEXT_SERVER_INFERENCE_RERANK_TIMEOUT_SECONDS` | generation timeout | LLM reranker timeout |
| `POWERCONTEXT_SERVER_INFERENCE_RERANK_MAX_REQUESTS` | generation request limit | Maximum model requests in one rerank operation |
| `POWERCONTEXT_SERVER_INFERENCE_DECISION_MODEL` | generation model | Optional dedicated Pydantic AI model for decision assistance and the Memory write gate |
| `POWERCONTEXT_SERVER_INFERENCE_DECISION_BASE_URL` | inherited/provider default | Custom decision-model provider base URL; requires `DECISION_MODEL` |
| `POWERCONTEXT_SERVER_INFERENCE_DECISION_HEADERS` | `{}` | JSON object of static decision client headers; values are secrets and inherit generation headers when no dedicated model is set |
| `POWERCONTEXT_SERVER_INFERENCE_DECISION_MODEL_SETTINGS` | `{}` | JSON object of decision model settings; merged with generation settings when no dedicated model is set |
| `POWERCONTEXT_SERVER_INFERENCE_DECISION_TIMEOUT_SECONDS` | generation timeout | Timeout in seconds for a decision operation |
| `POWERCONTEXT_SERVER_INFERENCE_DECISION_MAX_REQUESTS` | generation request limit | Maximum model requests for one decision operation, including model-output validation retries; excludes provider SDK HTTP retries |
| `POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS` | unset | Experience automatic admission interval; unset preserves accepted work and stops new automatic admission |
| `POWERCONTEXT_SERVER_EXTERNAL_SKILLS` | automatic local project targets | JSON override containing the host identity and explicit Agent Skill targets |

The recall channel weights are a ratio and apply to Atomic Memory and Topic Memory only after hybrid candidates pass
their channel admission floors. Single-channel searches remain unweighted; `auto` applies the weights only when it
selects hybrid search. Atomic Memory excludes zero-weight channels from `matched_by` and recall-gate relevance.
Topic Memory retains their evidence for Topics selected by a positive-weight channel.
Scaling both values equally has no effect. Setting one value to `0` excludes that channel from hybrid fusion, but a
higher weight cannot restore a candidate rejected before fusion. A zero weight does not disable that channel's
retrieval or admission work: vector embeddings may still be generated, and retrieval/admission counts still include
the zero-weight channel. A Topic or Atomic Memory Artifact search request that supplies its own `fusion` parameters replaces the
deployment recall weights entirely. The deployment weights apply only when the request does not specify `fusion`
parameters. After normalization, each non-zero channel weight must be at least the smallest normal IEEE 754 binary64
value (`2.2250738585072014e-308`); smaller values are rejected at startup. Only an explicit `0` disables a channel's
fusion contribution. Evaluate weight changes together with recall-gate rounds, semantic admission floors, and
historical Topic candidate selection instead of treating the knobs independently. In particular, weighting FTS more
heavily can move a Topic's normalized score across
`POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_HISTORY_RRF_THRESHOLD` (default `70`) and change which historical Topics
are sent to the planner.

The recall-sufficiency gate is disabled by default. When enabled, the Runtime assesses the first recall's candidate
count, family coverage, top-candidate score, and lexical coverage during `prepare_context`; when it judges the result
insufficient, it may expand up to two more rounds and relaxes the semantic-similarity admission floor each round. The
assessment calls no model, and expansion reuses the same request's Scope, families, limits, and context budget along
with the query vectors already produced. The round-one and round-two similarity floors must stay in decreasing order, or
startup fails. Enabling the gate can add search rounds and latency, so evaluate retrieval results and latency on your own
data.

When the gate produces a `RecallEffort`, the relational Runtime adds one observation to `pc_recall_effort_daily`, keyed by
`(scope_id, usage_date, policy_id, assessment)`. Dates use UTC. The additive counters describe committed search rounds,
expansion actions, candidate-pool sizes, search-reported additional inference calls, and final byte-budget truncations and
drops. The two drop subclasses sum to `dropped_items`; invalid counts are rejected before persistence. The table contains
only its key dimensions and counters, without entry identities, queries, prompts, citations, or content bodies. It is an
internal aggregate and is not included in the statistics overview or HTTP/MCP responses.

Recall-effort recording makes one write attempt within `POWERCONTEXT_SERVER_RUNTIME_MODEL_USAGE_WRITE_TIMEOUT_SECONDS`.
A failed write logs a safe diagnostic and preserves the prepared context. Request cancellation waits for the owned
transaction's native cleanup before propagating, preserving a shared in-memory SQLite database. A caller-supplied
`recall_effort_sink` replaces the relational recorder; a disabled gate invokes neither. Existing SQLite and OceanBase
databases gain the missing relation through the normal additive table-creation path.

Model usage accounting is best-effort and never gates a model call. Each Runtime owns one bounded recorder that accepts a
record without any I/O and writes it in an independent short transaction, so a statistics outage, a full queue, or a lock
that outlives the record's own budget drops that record with a diagnostic instead of failing the operation that produced it.
Already-accepted usage is drained within a bounded wait when an operation completes or statistics are read, and again at
shutdown. Contended business writes fail by design once they have waited this long: SQLite admits one writer at a time, so a
concurrent writer holding the lock for longer than the configured value makes the waiting business write report
`database is locked`. That is why multi-scope deployments raise `POWERCONTEXT_SERVER_DATABASE_BUSY_TIMEOUT_MS`; the same
lock pressure no longer reaches usage accounting, and usage records are dropped rather than failing the operation that
produced them.

Topic Workers enforce a durable allowance per unadvanced Scope Cursor: 3 attempts, 512 reserved provider requests,
and 64,000,000 estimated token-capacity units across all retries. A Window admits at most 4,194,304 canonical evidence
characters including metadata; nested input is also bounded. Exhaustion preserves Sources, Cursor, Pending, and the
same-Scope tail, and stops further provider calls. Flush and restart do not reset it; inspect
`pc_topic_memory_work_budgets` and structured errors for operator remediation.

Topic generation accepts `max_tokens`, `temperature`, `top_p`, `top_k`, `seed`, `presence_penalty`, `frequency_penalty`,
`timeout`, `openai_reasoning_effort`, `openai_text_verbosity`, `service_tier`, `openai_service_tier`,
`anthropic_service_tier`, and `anthropic_effort` as bounded scalar settings. Topic Embedding accepts only `dimensions`
and `truncate`. For generation with `openai-chat:<model>`, the sole `extra_body` exception is
`{"chat_template_kwargs":{"enable_thinking":false}}`, with a strict boolean and no extra keys at either level.
Background/hidden-history/native-tool settings and all other `extra_body` forms disable Topic processing while ordinary
inference continues; explicitly configured automatic Topic scheduling fails startup instead. Supported
provider prefixes are `openai`, `openai-chat`, `openai-responses`, `anthropic`, `azure`, `azure-responses`, `deepseek`,
and `openrouter`, plus the local `test` model; Embedding must also be supported by its SDK adapter. Topic SDK transport
retries and automatic continuations are disabled. Non-Topic inference keeps its existing settings behavior.

When the cursor signing secret is unset, a file-backed SQLite Server creates a private key beside its database;
other persistent backends create one in the PowerContext user data directory. In-memory SQLite uses a process-local
key. Configure the same `POWERCONTEXT_SERVER_CURSOR_SIGNING_SECRET` on every replica so a cursor remains valid after
restart or when the next request reaches another replica. Never expose or rotate this value while issued cursors
must remain valid.

Set `HttpConfig.uds`, `POWERCONTEXT_SERVER_HTTP_UDS`, or `server run --uds /absolute/path/core.sock` to select UDS.
The JSON form `POWERCONTEXT_SERVER_HTTP='{"uds":"/absolute/private/core.sock"}'` also selects UDS.
Flat `HTTP_UDS` values override JSON within one source; for `server run` and Agent setup, process HTTP settings
override environment-file settings. The wizard/template reject UDS in the edited file or effective process configuration,
and Agent setup never infers a TCP URL from a UDS listener.
The CLI option takes precedence; explicit `--host` or `--port` options conflict with an effective UDS setting. UDS is
supported for roles `all` and `api` (the latter still requires OceanBase), and rejected for `background`. Personal
services and local ops/Desktop management require TCP. See [Listen on a Unix domain socket](deploy-server.md#listen-on-a-unix-domain-socket)
for directory ownership, permissions, safe restart behavior, and authenticated clients. UDS does not enable the proposed
Rust Gateway or change API authorization.

Access Control is disabled by default. In `enforced` mode, API and MCP requests must establish a Principal through the
selected Authentication Provider; the liveness and readiness endpoints remain public. The built-in `static-bearer`
Provider accepts `Authorization: Bearer <token>`, including over UDS. Plain HTTP over TCP is trusted only on a
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

Source-driven background Memory, Topic Memory, Experience, and Profile processing use the service Principal selected by
`ACCESS_BACKGROUND_PRINCIPAL_ID`, falling back to the fixed static Principal. That Principal must have
`scope.contribute` for each processed scope and write permission on existing Artifacts it changes. New entries,
Artifacts, and Candidates retain its ownership or owner attestation in the same transaction as processing completion.
An enforced deployment with background capabilities fails startup if its identity or authorization provider cannot
be reconstructed in a child process, even when automatic schedules are disabled: accepted work still needs recovery.
The built-in provider supports this reconstruction. Injected providers and model objects remain usable by synchronous
SDK/Server operations with background capabilities disabled (`ARTIFACT_PROCESSING_FAMILIES=[]`).

Dream reconstructs the original requester's current permissions inside Experience/Skill Workers, and Candidate ownership
remains with that requester. Background service Principal permissions do not replace that identity. An OceanBase split
deployment can declare `ARTIFACT_PROCESSING_FAMILIES=["experience","skill"]` on a model-free API process and declare the same
capabilities with model configuration on the background process; automatic schedules may reference only declared Families.
SQLite uses `all`. Dream pins its model identity on first execution, and disabling automatic schedules does not prevent
accepted explicit Dream requests from completing.

SDK workers without a Server identity do not require Server authorization dependencies. Built-in background workers
use the built-in Source definitions. A custom Source registry requires custom processing bindings for every enabled
family, or disabling built-in background families with `ARTIFACT_PROCESSING_FAMILIES=[]`; otherwise startup fails
before accepting work. Turning off schedules alone is insufficient because explicit requests still start workers.
Custom Source registries remain available to synchronous SDK contexts and API-only composition.

The authenticated `/metrics` endpoint exposes `powercontext_server_artifact_processing_*` observations with a bounded `family`
label and, where applicable, `state`, `outcome`, `category`, or `reason`: Worker capacity, ready/retry queues,
unacknowledged Scopes, discovery and invocation duration, completions,
failures, and timeouts. Unacknowledged counts reflect the latest discovery; counters reset with the Supervisor instance.
See [Logs, metrics, and tracing](observability.md) for freshness, state, attempt/acknowledgement, and alert semantics.

The `extraction` object in `GET /v1/capabilities` separates configuration, background execution, and local observations.
Reading this snapshot does not query the database or call a model, and does not produce an overall health verdict.
`powercontext capabilities` prints the same facts. Missing diagnostics print `unavailable`, a null supervisor role
prints `absent`, and unknown values print `unknown`. Success and failure times are historical observations, not a
current health verdict. `--json` returns the Server payload unchanged.

- `configuration` is `configured` when a local extraction model or custom pipeline is assembled, or a local Memory
  worker is registered. It is `unconfigured` when none is available. Registration does not verify credentials or
  connectivity. External execution reports `unknown`.
- `background.location` is `local`, `external`, or `none`. The local Supervisor's `role` (`leader` or `standby`) is
  separate from its `state` (`running`, `degraded`, or `stopped`); standby is normal. A worker crash can coexist with a
  running Supervisor that arranges retries. `automatic_processing_enabled=false` still permits explicit work.
  External execution has unknown state and schedule; no running local Supervisor means a null role.
- `observation.since` marks the start of this Runtime's local observation window, including its child Memory workers.
  `status=unverified` means no execution outcome or control failure has been observed; `observed` means evidence exists.
  `last_success_at` records a successful nonempty flush or acknowledged worker invocation. `last_failure` records a safe
  `code`, `stage`, and `occurred_at`, such as `model_timeout` at `inference` or `worker_crash` at `worker`.
  Generic failures retain the observing boundary (`flush` or `worker`) without assuming a model failure.

Success and failure records are independent: Scope B succeeding does not clear Scope A's failure or prove its recovery.
A historical failure also does not mean an incident is still unresolved. Observations reset on Runtime restart and do
not prove model connectivity or remote worker health. Use structured logs for Scope-specific failures and retries, and
the existing metrics for queue and progress observations; local ready counts are not a global Source backlog.

Remote and multi-user deployments must use `enforced`. In that mode, HTTP, MCP, and metrics share one Server PEP.
`/v1/access/me` reports the `server`/`scope`/`artifact` Resource Kinds,
Provider batch/list/relationship capabilities and Artifact Family profiles. Managed Skill export and installation do
not introduce separate Access actions: the recipient first needs `artifact.read` on the logical Skill identity, then
chooses whether and how to install an exact Revision.

The built-in Access schema uses the configured SQLite, seekdb, or OceanBase backend, but remains Server-owned rather
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

The Artifact Processing Supervisor is enabled by the default `all` role. OceanBase deployments may run `api` and
`background` separately. When metrics are enabled, `powercontext server run --role background` exposes `/metrics` and
public `/health/live` using `POWERCONTEXT_SERVER_HTTP_HOST` and `POWERCONTEXT_SERVER_METRICS_BACKGROUND_PORT`
(default `8001`); it does not serve business APIs, MCP, Dashboard, OpenAPI, or `/health/ready`.
`/metrics` uses the same authentication and `server.observe` authorization as the API host.
Use distinct ports for API and background processes on the same host. `POWERCONTEXT_SERVER_METRICS_ENABLED=false`
opens no background listener. Multiple background candidates use the database Lease to elect one active Leader.
SQLite and embedded seekdb support only the single-process `all` role. Automatic Topic Memory waves remain disabled until a positive interval is set;
explicit flush work remains recoverable regardless of that interval. Topic workers need file-backed SQLite: configuring
a generation model with an in-memory SQLite database is rejected before processing is advertised. Use a persistent
`POWERCONTEXT_SERVER_DATABASE_URL`, such as `sqlite+aiosqlite:////srv/powercontext/runtime.db`.
Memory, Topic Memory, Experience, and Profile all use the Supervisor. OceanBase permits their schedules in split roles.
SQLite and embedded seekdb retain one `all` host. Every Family has its own quota and timeout in both modes; spare quota
is not shared. Disabling automatic admission preserves already accepted requests. The API and background instances must
agree on mode, registered Families and trigger capabilities. Model resources are only required by workers. Changing modes
requires [coordinated offline migration](artifact-processing-migration.md); mixed modes cannot start.
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

Python deployments can inject the optional `JevMemoryReranker` with a
`JevRerankConfig` and a caller-owned HTTPX client into
`open_builtin_runtime(memory_reranker=...)`. It requires a dedicated API key,
System One endpoint, and pinned model such as `jev-1.13.0`. Its total deadline
and concurrency/request-size bounds are explicit. Provider failures propagate as
inference errors under RFC 0080, including timeouts, unavailable providers, invalid
scores, missing usage receipts, and model-version drift. Injected rerankers also
disable recall expansion unless the existing `RECALL_GATE_ALLOW_WITH_RERANK`
setting enables it. The Server environment switch above selects the existing LLM
reranker. See `evaluation/memory/locomo/README.md` in the repository for Jev
configuration, the three-arm comparison, and execution coverage.

The same configured generation model gates explicit Experience generation, managed Skill generation, and semantic
Skill fork/evolution. Exact external Skill import and complete package upload do not use a model: PowerContext validates
and stores the canonical package bytes, then creates a pending Candidate with the same package digest. Without a
generation model, semantic generation returns a capability error before persisting a Candidate; Review, package
inspection and download, exact import, usage recording, and external Skill scan/list/resolve continue to work.

Experience incubation has its own Supervisor binding and persisted Source cursor. Each invocation inspects a
finite window controlled by `SOURCE_WINDOW_LIMIT` and exposes only Content Sources whose metadata contains
`"kind": "task-outcome"` to the model. It creates pending Experience Candidates in the Review Inbox; it does not
approve them, place them in PreparedContext, create a managed Skill, export it to an Agent target, or execute anything.
Memory and Experience keep independent scheduling intervals, Worker quotas, and business cursors. Unsetting an interval
stops new automatic admission for that Family while preserving accepted work.
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

The URL must use the `mysql+aoceanbase` driver, include an explicit port, and set `charset=utf8mb4`. An omitted database
defaults to `powercontext`; an explicit database is preserved. The tenant must use MySQL compatibility mode.

seekDB startup and OceanBase Python SDK initialization create a missing selected database before initializing tables and reuse existing databases
without issuing a database-creation statement. Missing-database creation failures include manual provisioning guidance;
other connection errors do not trigger fallback. seekDB detects PowerContext-named tables in the old `test` database
when no name was explicitly configured and requires an explicit choice before initializing a new database.
Both backends reject database names containing backticks during configuration validation, before any database writes.
Choose a name without backticks, such as `team_context`.
Full OceanBase Server deployments require a prepared database and explicit Server schema maintenance before startup;
database auto-provisioning does not replace that admission contract.
See [Install and run](../get-started/install-and-run.md) for custom names, permissions, and old seekDB data handling.

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
