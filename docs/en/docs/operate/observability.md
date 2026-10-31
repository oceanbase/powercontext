---
title: Logs, metrics, and tracing
description: Locate requests in logs and inspect Server metrics and traces.
---

# Logs, metrics, and tracing

Use logs to locate an operation, metrics to observe aggregate behavior, and traces to follow a request across stages.
These signals describe service behavior; they do not replace inspection of stored Memory or Source evidence.

## Logs

The foreground Server writes operational logs to its process output. Configure
`POWERCONTEXT_SERVER_LOGGING_LEVEL` (default `INFO`), `POWERCONTEXT_SERVER_LOGGING_FORMAT` (`console` or `json`),
and `POWERCONTEXT_SERVER_LOGGING_ACCESS` (default `true`). For a native personal service, run
`powercontext service status` to find the journal selector or log paths. Docker users can inspect container logs.

### File output, rotation, and compression

Enable file output with `POWERCONTEXT_SERVER_LOGGING_FILE=true`. The Server creates the directory and writes operational logs to files instead of stdout. Python's `RotatingFileHandler` defaults to rotation near 20 MiB and retains up to ten gzip archives per active file. Adjust the threshold, retention, and compression to fit the available storage and CPU budget. Restarting appends to the active file; no external rotation tool is required.

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `POWERCONTEXT_SERVER_LOGGING_FILE` | `false` | Write managed files instead of stdout |
| `POWERCONTEXT_SERVER_LOGGING_DIRECTORY` | User data directory + `logs/` | Directory for log files; respects `POWERCONTEXT_HOME` |
| `POWERCONTEXT_SERVER_LOGGING_MAX_BYTES` | `20971520` | Positive size threshold for rotation |
| `POWERCONTEXT_SERVER_LOGGING_BACKUP_COUNT` | `10` | Maximum archives per active file; must be at least one |
| `POWERCONTEXT_SERVER_LOGGING_COMPRESSION` | `gzip` | Archive compression: `gzip` or `none` |

```bash
export POWERCONTEXT_SERVER_LOGGING_FILE=true
export POWERCONTEXT_SERVER_LOGGING_DIRECTORY=/var/lib/powercontext/logs
powercontext server run
```

Files are named by the existing Server process role: `all.log`, `api.log`, or `background.log`; explicit configuration without a role uses `server.log`. Workers share their owning Server's file, with execution identity recorded in each entry. Archives are named `all.log.1.gz`, `all.log.2.gz`, and so on, with `.1` being the newest archive. Without compression they use `.1`, `.2`, and so on. Changing compression does not remove archives from the previous policy. The active file remains uncompressed. The size threshold is approximate; one large record can exceed it. Retention applies per active file, not to unrelated files in the directory. `POWERCONTEXT_SERVER_LOGGING_FORMAT` selects readable text or JSON for either stdout or file output.

Workers send already formatted, allowlisted records over dedicated socketpairs to their owning Server. Each channel is created before spawn, and only its sending end is passed to that Worker. The receiver does not listen on a network port or deserialize Python objects. JSON-line envelopes preserve multiline output. Worker identity and request/trace correlation are captured before sending. One receiver thread multiplexes the streams and one writer thread consumes a bounded in-process queue; logging starts no additional processes. Stream-only output keeps the standard stream handlers.

Each file has an exclusive writer lock; a second Server with the same directory and role is rejected. Use separate directories for independent replicas. Stop workers before closing the logging owner. Shutdown drains Worker streams for at most one second, then waits for queued output through `QueueListener.stop()` before closing the handlers. File writes, compression, and close use native logging cleanup without a separate timeout. A stalled file sink can therefore delay Server process exit. Incomplete records are discarded without printing their contents. A failed Worker stream does not stop other streams. Send waits are bounded; a failed send retires that Worker's stream. The receiver handles up to 256 concurrent streams, and JSON-line envelopes are limited to 256 KiB after UTF-8 encoding. Oversized records, full queues, transport failures, and forced process exits can lose logs. Logs remain best-effort.

macOS and Windows personal services enable managed file output by default. An explicit `POWERCONTEXT_SERVER_LOGGING_FILE=false` keeps stream output. Linux personal services keep journal output by default. Host-owned stdout/stderr files still capture startup diagnostics and direct stream writes; those files are outside the managed handler's rotation policy. `powercontext service status` reports the host's diagnostic directory or journal selector; a custom managed directory is selected through `POWERCONTEXT_SERVER_LOGGING_DIRECTORY`.

An unavailable directory or an existing writer prevents file logging configuration. Once running, formatting, transport, queue, write, and compression failures use the same content-free `observability.log_failed` notification as stream failures. Completed gzip archives are published before the original file is removed. SDK Runtime use does not configure logging; embedding applications retain ownership. An embedded Server with file output can own a worker-only sink without reconfiguring the embedding application's loggers.

Use the response's `X-PowerContext-Request-ID` to correlate a failed request with diagnostics. The host guides also
explain content-free recall and capture diagnostics; a missing context result can be valid rather than an outage.

### Operational fields and correlation

For event examples, field extension, and regression checks, see the
[developer guide](../../development/observability.md).

JSON and console logs use the same explicit operational field allowlist. Unrecognized extras are not serialized.
Both formats retain application/transport outcomes and error diagnostics, plus worker correlation on nested events,
including code stages. Code-specific diagnostic fields are included only for `code.*` events.

| Fields | Meaning |
| --- | --- |
| `service_name`, `service_version`, `service_role`, `service_instance_id` | Service name (honors `OTEL_SERVICE_NAME`), installed package version, process role, and generated process identity |
| `event`, `operation`, `unit`, `outcome` | Event name, declared application operation or stage, work unit, and result |
| `request_id`, `trace_id`, `span_id` | Bound ingress request and current trace context, when available |
| `worker_id`, `family`, `binding`, `request_generation`, `supervisor_group`, `supervisor_generation` | One worker attempt and its accepted work/fence; correlate child output with supervisor diagnostics |
| `duration_ms`, `error_code`, `error_category` | Duration in milliseconds, diagnostic code, and bounded operational error classification |
| `stage`, `processing_stage`, `failure_reason`, `input_item_count`, `estimated_input_tokens`, `token_estimator_id`, `effective_budget`, `provider_request_id`, `finish_reason`, `failure_location`, `cause_exception_type` | Processing failure stage/reason and safe accounting fields; `stage` names the emitting boundary or domain location (Worker/supervisor stage, domain block name, or `worker` fallback), `processing_stage` the bounded domain stage. See [Processing failure diagnostics](../../development/processing-failures.md) |

Server roles are `all` (API and supervisor), `api`, and `background`; child processes use `worker`. Explicit logging
configuration without a deployment role uses `server`. JSON identity fields correspond to trace resource attributes
`service.name`, `service.version`, `service.instance.id`, and `powercontext.service.role`. `service_version` is absent
when installed distribution metadata is unavailable, such as a source checkout or a frozen bundle. A new process
receives a new instance ID; a worker retry receives a new `worker_id`. None of these execution identifiers are metric
labels.

The request ID is inherited by nested request logs unless an event provides an explicit ID. Scheduled processing does
not acquire the request ID that happened to be active when the supervisor started, including supervisor control logs.
Trace IDs still exist with recording disabled, but do not imply that a backend has stored the trace.
Child logging alone does not establish cross-process trace propagation.

Common outcomes are `success`, `failure`, `cancelled`, and `noop`; admission blocks use `blocked` rather than success or failure.
If a worker encounters a durable processing block, `artifact_processing.worker.blocked` is emitted at `WARNING`
with outcome and error category `blocked`, preserving the block's diagnostic code and stage.
Topic block `source_after` and known `source_through` are Source journal positions. When the exact selected end is
unknown, block logs omit `source_through`; attempt/request/token counters remain available.
Worker control results can also be `cursor_conflict`,
`head_conflict`, or `leadership_lost`. A `artifact_processing.worker.returned` event describes the child's result;
`artifact_processing.worker.acknowledged` indicates that the supervisor verified durable acknowledgement. A child
return does not by itself prove business completion. Forced process termination can prevent a child terminal event;
the supervisor records interruption or failure instead.

`error_category` is absent when not applicable. Its vocabulary is `authentication`, `authorization`, `configuration`,
`rate_limit`, `dependency`, `timeout`, `not_found`, `conflict`, `invalid_request`, `internal`, `cancelled`, and `blocked`.
Server spans use the same vocabulary in `powercontext.error.category`; each boundary classifies its own observed result.
Existing diagnostic codes remain available for more specific investigation.
Supervisor worker and wait spans preserve the child's bounded failure category across the process boundary.
Policy blocks and child cancellation use `blocked` and `cancelled` outcomes without an error span status;
inference failures retain their timeout, configuration, or dependency classification.
With streamed generation aggregation enabled (`POWERCONTEXT_SERVER_INFERENCE_GENERATION_STREAMING=auto`),
an `inference.generation.usage_missing` WARNING with `unit` `inference` reports one generation request that
completed successfully while the provider did not return usage with the stream; usage statistics for that
request will be missing. The event carries no prompt or response content and does not affect the request result.
When `auto` is set and the model provider prefix is outside `anthropic`, `openai`, `openai-chat`, and
`openai-responses`, an `inference.generation.streaming_not_applied` WARNING with `unit` `inference` reports that
the model stayed on a non-streaming request. The event carries no prompt or response content and does not change
the request result.
For child cancellation, the supervisor emits `artifact_processing.failed` at `INFO` with outcome/category `cancelled`
and diagnostic code `worker_cancelled`. Application spans classify mapped diagnostic codes even when sensitive
exception values are excluded from logs and traces.

At `DEBUG`, `code.*` events retain `seconds` and add `duration_ms`. Their `operation` is the stage name (`code.<stage>`),
matching the span identity; an existing requested query kind moves to `code_operation`. Code diagnostics can include `selected_count`,
`hit_count`, `hit_count_is_lower_bound`, `cache_bytes`, `status`, `limitations`, and `reason`. Where supported by the OS,
`process_lifetime_max_rss_bytes` and `children_lifetime_max_rss_bytes` are process-lifetime high-water marks in bytes;
they are not per-request allocations or simultaneous process-tree totals, and must not be added together.
Failed code stages retain the diagnostic `error_code` and, for domain errors, `status_code`; logs and spans use these
values to distinguish timeouts, conflicts, and invalid requests from internal failures.

Server-created workers use the configured log level and format. Importing or running the Runtime as an SDK does not
configure global logging; an embedding application owns its logging configuration. Logging bootstrap failure does not
fail processing. The Server handler emits at most one content-free `observability.log_failed` notification to stderr
per handler if formatting or writing fails; it does not dump the failed record. Operational logs remain best-effort,
not a durable audit or billing ledger. The identity/error fields are additive; console lines now include identity.

## Metrics and readiness

Metrics are enabled by default at `/metrics` in Prometheus format. In `enforced` mode, `/metrics` and `capabilities`
require authentication as a Principal with `server.observe`. See [Troubleshoot](troubleshoot.md).
On API hosts, `/health/live` and `/health/ready` remain public.

The `background` role exposes `/metrics` and public `/health/live` using `POWERCONTEXT_SERVER_HTTP_HOST` and
`POWERCONTEXT_SERVER_METRICS_BACKGROUND_PORT` (default `8001`). It has the same metrics authentication and authorization
as the API host, but no business APIs, MCP, Dashboard, OpenAPI, or `/health/ready`.
Give API and background processes different ports when sharing a host.
`POWERCONTEXT_SERVER_METRICS_ENABLED=false` removes `/metrics`; for `background` it also prevents opening the listener.
Scraping this listener does not run API-host model readiness probes.

### Passive observations and freshness

Metrics collection copies existing, bounded, process-local Runtime/Supervisor observations. Collection does not query
the database, scan backlog, refresh readiness, invoke a model, or wait for a worker. Endpoint authentication and
authorization may still perform bounded database reads. Background pending observation, discovery, and readiness
owners produce the observations independently of scraping. A successful scrape therefore proves neither current
dependency health nor fresh backlog data; scraping never invokes a model.

All metric names below start with `powercontext_server_`. Timestamps are UNIX seconds; durations and ages are seconds;
counts count the named unit. State metrics are one-hot: the matching `state` is `1`, the other supported states are `0`.
Absent observations are not healthy zeroes. Counters and histogram buckets are cumulative for the owning process or
Runtime/Supervisor instance and reset when that owner is recreated; scrapes do not reset them. Use counter-aware
`rate`/`increase` functions and preserve scrape target labels such as `instance` and deployment identity.

| Metric suffix | Labels and meaning |
| --- | --- |
| `transport_requests_total`, `transport_request_duration_seconds` | `transport`, `operation`, `outcome`: external requests and duration |
| `application_operations_total`, `application_operation_duration_seconds` | `operation`, `outcome`: application operations and duration |
| `service_role_info` | `role`: `all`, `api`, `background`, or `server`; value `1` identifies this process |
| `runtime_ready` | Compatibility gauge: Runtime admission, not full model availability |
| `runtime_readiness_state` | `state`: `unobserved`, `ready`, `degraded`, `not_ready` |
| `runtime_readiness_observed_timestamp_seconds` | Last completed readiness evaluation; absent before evaluation; may include cached results |
| `dependency_state` | `dependency`, `state`: `unobserved`, `disabled`, `ready`, `unavailable`, `timeout`, `misconfigured`, `not_ready`, `degraded`, `leader`, `standby` |
| `dependency_blocking` | `dependency`: `1` when this dependency blocks readiness |
| `dependency_observed_timestamp_seconds` | `dependency`: last completed actual probe; absent before a completed probe |
| `dependency_last_success_timestamp_seconds` | `dependency`: last actual successful probe; absent before success |
| `dependency_probe_duration_seconds` | `dependency`, `outcome`: actual probes, excluding cache hits |
| `telemetry_collection_timestamp_seconds` | Last successful passive snapshot collection; `0` before collection |
| `telemetry_collection_failures_total` | Failed passive collection attempts; the previous observation is retained |
| `telemetry_log_records_dropped_total` | Observed records that the process-owned handler or Worker receiver failed to deliver; clean EOF/reset is not a loss, and unobserved losses are not counted |

Detailed probe observations cover `database`, `inference.generation`, `inference.rerank`, `inference.decision`, and
`inference.embedding`. The same `dependency_state` family also includes bounded aggregate checks for `runtime`,
`artifact_processing_supervisor`, registered `artifact_processing.<family>` values, `access_provider`, and
`authentication_provider`. Detailed Runtime snapshots take precedence over aggregate state for the same dependency;
each dependency has only one one-hot state set. Probe timestamps and durations remain actual-probe observations,
not aggregate evaluation times. Disabled dependencies have no probe timestamps or samples. Probe outcomes are `ready`,
`unavailable`, `timeout`, `misconfigured`, or `cancelled`; cancellation contributes duration but does not replace the
last completed state/timestamp. A cache hit updates neither actual probe timestamps nor histogram counts. One sample is one complete probe refresh;
provider-internal retries, if any, remain inside that duration and are not counted as separate network attempts.
Built-in cached probes normally retain results for 300 seconds, or 30 seconds for `timeout`/`unavailable`; cache expiry
alone does not run a probe. The aggregate evaluation timestamp, probe timestamps, collection timestamp, and Prometheus
scrape timestamp answer different freshness questions.

`runtime_ready=1` may coexist with `degraded`: it means operations can be admitted, not that all models are available.
Use `runtime_readiness_state` with dependency states and freshness, or request `powercontext ready` and
`powercontext capabilities` to diagnose an unavailable database versus an optional model failure. A background-only
process evaluates readiness at startup and every 30 seconds when metrics are enabled; `/metrics` itself never probes.
Before a completed evaluation, or in an embedding application that does not evaluate readiness, `unobserved` is valid.

### Processing metrics

The following suffixes have the prefix `powercontext_server_artifact_processing_`. All per-Family series use `family`;
additional labels are listed explicitly. Only registered, validated Family names are exported. No scope, worker,
request, trace, content, provider error text, or database URL is a metric label.

| Metric suffix | Meaning |
| --- | --- |
| `supervisor_present` | Process-level `0`/`1`; API-only absence is not Supervisor failure |
| `supervisor_running`, `automatic_processing_enabled` | Per-Family `0`/`1` lifecycle/configuration observations |
| `supervisor_role` | Additional `state`: `leader`, `standby` |
| `status` | Additional `state`: `disabled`, `leader`, `standby`, `degraded` |
| `max_workers`, `available_workers`, `used_workers` | Configured capacity and current worker slots |
| `ready`, `retry_wait`, `blocked_wait` | Local ready queue, retained waiting retries (including blocked entries), and retained blocked scopes |
| `unacknowledged_requests` | Pending Scope count from the latest durable pending snapshot, not the number of request generations or a scrape-time query |
| `requested_snapshot_timestamp_seconds` | Database UTC time of that pending snapshot, in UNIX seconds; `0` before observation |
| `pending_observation_failures_total` | Failed background pending observations, including timeouts; the previous pending Scope count and snapshot timestamp are retained |
| `observed_pending_scopes` | Number of retained first-observed pending keys, bounded to 1000 |
| `oldest_observed_pending_age_seconds` | Approximate local age lower bound for retained pending generations, in seconds; `0` if none retained |
| `last_acknowledgement_timestamp_seconds` | Last verified durable acknowledgement; `0` before any acknowledgement |
| `discovery_seconds`, `last_invocation_seconds` | Legacy latest discovery/invocation duration gauges |
| `completed_total`, `failed_total`, `timeouts_total` | Legacy acknowledged, failed, and timed-out invocation counters |
| `attempt_duration_seconds` | Additional `outcome`: duration of each worker attempt, including retries |
| `discovery_duration_seconds` | Additional `outcome`: duration of each discovery pass |
| `errors_total` | Additional `category`: bounded error categories listed in the log contract above |
| `retries_total` | Additional `reason`: `failure`, `conflict`, `blocked`; retry scheduling decisions, including block rechecks and overflow |
| `admission_blocks_total` | Observed blocked admission decisions; repeated decisions may count again |

Durable pending observations run periodically, independently of worker capacity, with a default interval of 30 seconds
and a 2-second deadline per observation. A full set of occupied worker slots does not pause this observation.
Failures preserve the last successful snapshot and increment `pending_observation_failures_total`; a fresh collection
or scrape does not make that snapshot fresh. These reads run in the background, never during metrics collection.

The pending snapshot reads the existing processing intents and counts pending Scopes for the binding. It does not
store a timestamp per request generation or require a new table or schema migration. Query work scales with the
binding's intents; the retained aggregate snapshot uses O(Families) memory. Before the first successful observation,
a zero snapshot timestamp means unobserved, even when count gauges are zero. Restart and leadership changes require
fresh observations; do not interpret retained or missing measurements as current durable backlog truth.

`oldest_observed_pending_age_seconds` is an approximate **local lower bound**, measured from first local observation
of up to 1000 retained admitted keys. It tracks locally observed pending generations, not request creation time or
exact durable backlog age. Membership may be stale between discovery passes while the local age advances. Unseen
keys can be older; acknowledgement or eviction can remove local observations. Restart and leadership changes reset
this local history. A local age of `0` does not prove an empty durable backlog, and a large value alone never implies
a stall: processing may still be making verified acknowledgement progress. Use pending evidence, snapshot freshness,
verified acknowledgement progress, and startup grace together.

Attempt outcomes are `success`, `hold`, `noop`, `failure`, `cancelled`, `blocked`, `cursor_conflict`, `head_conflict`,
and `leadership_lost`. Discovery outcomes are `success`, `failure`, `cancelled`, and `leadership_lost`. The duration
histograms expose `_bucket`, `_count`, and `_sum`; bucket boundaries are 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1,
2.5, 5, 10, 30, 60, and `+Inf` seconds. `_count` counts attempts/passes, not unique requests or business successes.
A retry contributes another attempt. `hold` and `noop` remain distinct from business success. The legacy
`completed_total` counts verified durable acknowledgements and can include acknowledged holds/no-ops; it is not an
attempt-success or produced-artifact counter. Neither histogram nor acknowledgement counter is a billing ledger.

### Dashboards and alert rules

Choose and record explicit operator thresholds for scrape failure, collection age, probe age, requested-snapshot age,
startup grace, and time without acknowledgement. These are deployment-specific alert policies, not built-in defaults.
For example, an operator may select 120 seconds for collection freshness, 600 seconds for probe freshness, and 900
seconds without acknowledgement while pending work exists; validate those choices against scrape cadence, readiness
traffic, schedules, and expected worker duration. Do not apply a 600-second probe rule to a host that never runs probes.

- First check Prometheus `up`, collection freshness, and collection failures. A fresh scrape can contain an old snapshot
  after collection failure. Missing or zero timestamps need an explicit unobserved branch, separate from stale data
- For dependency alerts, require an observed, sufficiently fresh state. Treat `disabled`, `unobserved`, stale, degraded,
  and unavailable separately; optional-provider failure should not automatically page as database failure
- Separate stopped processing from stalled processing. With a fresh collection, `supervisor_present=1` and
  `supervisor_running=0`, alert on stopped processing when retained local work or a sufficiently fresh positive
  `unacknowledged_requests` snapshot establishes pending work. Do not require a running Leader for this rule. If only
  stale last-known pending evidence remains, report stopped processing with uncertain backlog and investigate;
  a missing target is handled separately through `up`
- For a running Leader, gate possible-stall alerts on a fresh collection and pending-work evidence: current
  `observed_pending_scopes>0` or `used_workers>0`, or a nonzero, sufficiently fresh
  `requested_snapshot_timestamp_seconds` with `unacknowledged_requests>0` from the same target/Family.
  Require sustained pending work and no verified acknowledgement progress for the chosen interval, allowing a startup
  grace period after restart or leadership change. Use `last_acknowledgement_timestamp_seconds` and changes in
  `completed_total` to assess verified progress; process exit or successful attempt duration is not acknowledgement.
  For example, after the startup grace period, 900 seconds of sustained pending evidence without acknowledgement
  can indicate a possible stall; an old locally observed key while acknowledgements advance does not.
  A zero acknowledgement timestamp means never acknowledged, not a UNIX-era outage: use the grace period and a
  sustained observation window instead of subtracting zero from the current time. Account for clock skew when
  comparing timestamps, and do not treat missing observations as zero
- Durable pending observation continues when worker slots are full; observation failures can still leave stale data.
  A stale snapshot must not suppress a possible-stall alert supported by current retained work and absent verified
  progress. The durable count is a last-known background observation; local retained keys and worker slots are not
  a complete durable backlog. `oldest_observed_pending_age_seconds` is only supporting context, never an independent
  stall trigger or a substitute for acknowledgement progress and freshness
- Keep idle healthy instances quiet. Automatic processing disabled is configuration, standby is expected, and missing
  API-only Supervisor data is not a zero-capacity outage. Stale pending snapshots without current local evidence merit a freshness diagnostic, not a
  fabricated backlog conclusion. Watch blocked waits, admission blocks, retry reasons, errors, and worker utilization
- Scrape API and background targets separately. Do not add API and background backlog snapshots or leader and standby
  counts for the same deployment/Family. Select the current running leader for backlog/capacity dashboards; preserve
  deployment/group boundaries when combining Families. Counter rates are process-local and may need aggregation
  across role transitions, but the same target must never be scraped twice into the same total
- Alert on increasing pending-observation failures, collection failures, and dropped log records. OTLP SDK queue occupancy and exporter drop counts
  are unsupported here: their absence means unavailable telemetry, not zero pressure or zero loss. Inspect the
  collector/backend's own diagnostics when investigating trace delivery

See [Configuration](configuration.md) for endpoint settings and [Troubleshoot](troubleshoot.md) for recovery.

## Tracing

Install the `tracing-otlp` extra, enable `POWERCONTEXT_SERVER_TRACING_ENABLED=true`, and configure an OTLP HTTP
receiver. Follow the complete [Phoenix](trace-with-phoenix.md) or [Langfuse](trace-with-langfuse.md) procedure.
Without the extra, enabling tracing fails at startup. PowerContext traces its own transport, application, and model
calls; tracing does not capture unrelated host model calls automatically. Model spans disable automatic exception events
and status descriptions so provider error bodies cannot export Source, prompt, or response content; only a bounded
`error.type` and error status are retained.

For environment-file deployments, put the tracing and OpenTelemetry variables in the explicit file passed to the
Server. See [environment configuration](../get-started/configure-server-environment.md) for precedence rules.

### Background service and observation freshness

Split API/background roles require OceanBase. SQLite and embedded seekdb retain the `all` role.

The `background` role exposes only `/health/live` and `/metrics` on `HTTP_HOST` and the separate
`POWERCONTEXT_SERVER_METRICS_BACKGROUND_PORT` (default `8001`). It does not expose business APIs, MCP, the dashboard,
or OpenAPI. API/all roles continue using their normal HTTP port. Disabling `METRICS_ENABLED` also disables the background
HTTP listener. Authentication and `server.observe` authorization use the same Server profile as the API metrics endpoint;
non-loopback binding follows the existing Server bind policy. Configure the metrics port in the background environment
file when multiple processes share a host.

When metrics are enabled, readiness is evaluated at startup and every 30 seconds. Scraping metrics never invokes a
model, pings the database, or refreshes readiness. Inference probes retain their existing 300-second normal and 30-second
transient caches. `readiness_evaluated_timestamp_seconds` dates the aggregate evaluation, not a fresh provider request;
this timestamp does not bypass or describe the age of an individual cached probe.

| Metric (prefix `powercontext_server_`) | Interpretation |
| --- | --- |
| `readiness_state{state}` | One-hot `ready`, `degraded`, `not_ready`; it follows the same merged aggregate as `/health/ready`, including access and authentication provider failures. The compatible `runtime_ready` still means able to accept operations |
| `dependency_state{dependency,state}` | One-hot bounded results merged with detailed Runtime observations described above. Aggregate-only checks include runtime, supervisor, access and authentication providers; config echo such as `access_mode` stays absent. Detailed disabled/unobserved dependencies retain their explicit states |
| `readiness_evaluated_timestamp_seconds` | Unix seconds of the latest aggregate evaluation, which may use cached probes |
| `artifact_processing_supervisor_alive`, `artifact_processing_leader`, `artifact_processing_degraded` | Local supervisor task, held fence, and discovery health, by registered family |
| `artifact_processing_blocked` | Number of locally retained policy-blocked invocations; not worker failures |
| `artifact_processing_discovered_timestamp_seconds`, `artifact_processing_unacknowledged_observed_timestamp_seconds` | Unix time of the last successful discovery pass and last unacknowledged-count query, respectively; they are different observations |
| `artifact_processing_last_success_timestamp_seconds` | Latest supervisor-verified acknowledgement, not merely child return |
| `artifact_processing_oldest_observed_pending_timestamp_seconds` | Earliest local admission still retained in ready/running/retry state; zero means none observed |
| `artifact_processing_invocation_duration_seconds{family,outcome}` | Histogram of finalized invocation attempts including launch, execution and acknowledgement; each retry is another sample |
| `model_usage_queue_depth`, `model_usage_queue_capacity` | Parent-process recorder queue and capacity; excludes the in-flight write and child queues |
| `model_usage_dropped_records_total`, `model_usage_write_failures_total` | Parent recorder losses plus received child loss counters; best-effort statistics, not billing |
| `model_usage_worker_reports_total{outcome}` | Invocation attempts with `received` recorder counters or `missing` observations; missing does not mean zero losses |
| `metrics_collection_failures_total` | Failed processing snapshot refreshes; an old snapshot can remain visible |

Processing timestamps use `0` for an observation that has never occurred. All counts, histograms and timestamps belong
to the current process/supervisor lifetime and reset or disappear on restart/removal. The pending timestamp is a local
lower bound on unfinished-work age, not the oldest durable Source or a complete database backlog: undiscovered work,
retry-cache eviction and restart can remove its history. Combine it with backlog counts and observation freshness.
A healthy idle worker need not have a recent success. Use pending work plus stalled progress/freshness as alert conditions;
keep policy blocks separate from outages. Invocation outcomes are `success`, `failure`, `blocked`, `cancelled`, `conflict`
and `timeout`; a slot retained for leadership cleanup is not counted again as a completed reap.

Processing labels use registered family names and bounded outcomes, never scope/worker/request identities. Scrapes read
in-memory snapshots; local ready/retry/running observation work is bounded by scheduler limits rather than database size.
Built-in spawned workers return two cumulative loss counters after recorder shutdown, on both success and failure.
The owning supervisor adds each attempt's counters once, independently of acknowledgement and tracing configuration.
Multiple recorders opened during one child invocation contribute to the same report. Scrapes do not recount reports.
Child queue depth/capacity are not live measurements in this endpoint. A running child has not yet reported; a crash,
forced termination, or invocation that never observed a recorder produces a `missing` report rather than known zero losses.
This projection is not durable: parent restart or a lost child report can lose observations. Custom launchers must supply
their own report and observer wiring. An API-only process cannot observe another background service's children.
Scrape every deployed API/background target and keep role/instance attribution in target labels. An absent target or
missing sample must not be interpreted as zero failures. Do not sum process snapshots as if they were one durable ledger.

### Worker execution traces and integration coverage

With tracing enabled and `tracing-otlp` installed, Server-composed workers capture only W3C `traceparent`/`tracestate`
when launched. Each child creates its own provider/resource, joins that supervisor execution, and instruments its real
family or Topic Memory model composition. Memory processing includes the existing commit stages; Topic Memory adds
`topic_memory.prepare` and `topic_memory.commit`. Explicit Dream dispatch — including the Topic Memory dream branch —
runs under the same child span and shares the same model instrumentation, so instrumented model spans appear for both. The child `artifact_processing.worker.execute` span describes a child
result; the supervisor acknowledgement remains the evidence of verified business completion.

A scheduled/coalesced invocation starts an independent supervisor trace. It does not inherit a capture request ID,
keep an HTTP request open, or claim a durable causal link to all contributing Sources. No incoming context is persisted
for replay. Custom launchers own their bootstrap; SDK-only composition stays unconfigured unless an embedding application
supplies the optional worker wrapper. Parent-based sampling decisions propagate to the child. Child export/shutdown is
best-effort, with at most a one-second shutdown wait; forced termination can leave incomplete spans. Missing exporters
still fail an explicitly configured Server at startup, while child telemetry setup/export failure cannot change its domain result.

| Integration boundary | Coverage |
| --- | --- |
| HTTP and MCP ingress/internal bridge | Existing W3C propagation and logical-operation semantics; infrastructure scrapes are excluded |
| Python Client | Existing outbound spans and context injection under the application's configured provider |
| Pydantic AI capability | Real Agent/capability and Python Client propagation validated against a local HTTP Server/OTLP receiver; fixture model, not an external provider acceptance claim |
| Built-in Memory and Topic Memory spawn workers | Real entrypoints, local provider HTTP success/timeout, child spans and OTLP reception verified |
| Other native host integrations | Host diagnostics remain available; automatic host trace propagation is not claimed by this coverage |

OTLP tests use an actual local protobuf HTTP receiver, not a production trace database. To validate storage and backend
queries, follow the Phoenix/Langfuse instructions or the chosen backend's deployment procedure. Metrics dashboards,
trace storage, log shipping and alert delivery are separate deployment capabilities.

## Topic generation rejection codes

Topic processing may report `invalid_topic_placeholder` for recognizable numbered placeholder bodies or
`invalid_topic_coordination_content` for identity-coordination-only bodies. These content-free diagnostic codes
identify recoverable generation rejection, not successful NOOP. Retries use the existing durable Window budget;
exhaustion retains the pending frontier. Existing worker events and correlation fields carry the diagnostic code.
Source text, generated bodies and provider responses must not be added to operational logs.
