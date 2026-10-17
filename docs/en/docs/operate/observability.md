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

The `background` role exposes a metrics-only HTTP listener using `POWERCONTEXT_SERVER_HTTP_HOST` and
`POWERCONTEXT_SERVER_HTTP_PORT`. It has the same metrics authentication and authorization as the API host, but no
API, MCP, Dashboard, OpenAPI, or health routes. Give API and background processes different ports when sharing a host.
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
| `dependency_state` | `dependency`, `state`: `unobserved`, `disabled`, `ready`, `unavailable`, `timeout`, `misconfigured` |
| `dependency_blocking` | `dependency`: `1` when this dependency blocks readiness |
| `dependency_observed_timestamp_seconds` | `dependency`: last completed actual probe; absent before a completed probe |
| `dependency_last_success_timestamp_seconds` | `dependency`: last actual successful probe; absent before success |
| `dependency_probe_duration_seconds` | `dependency`, `outcome`: actual probes, excluding cache hits |
| `telemetry_collection_timestamp_seconds` | Last successful passive snapshot collection; `0` before collection |
| `telemetry_collection_failures_total` | Failed passive collection attempts; the previous observation is retained |
| `telemetry_log_records_dropped_total` | Records that the process-owned log handler failed to deliver |

Dependency labels are limited to `database`, `inference.generation`, `inference.rerank`, `inference.decision`, and
`inference.embedding`. Disabled dependencies have no probe timestamps or samples. Probe outcomes are `ready`,
`unavailable`, `timeout`, `misconfigured`, or `cancelled`; cancellation contributes duration but does not replace the
last completed state/timestamp. A cache hit updates neither actual probe timestamps nor histogram counts. One sample is one complete probe refresh;
provider-internal retries, if any, remain inside that duration and are not counted as separate network attempts.
Built-in cached probes normally retain results for 300 seconds, or 30 seconds for `timeout`/`unavailable`; cache expiry
alone does not run a probe. The aggregate evaluation timestamp, probe timestamps, collection timestamp, and Prometheus
scrape timestamp answer different freshness questions.

`runtime_ready=1` may coexist with `degraded`: it means operations can be admitted, not that all models are available.
Use `runtime_readiness_state` with dependency states and freshness, or request `powercontext ready` and
`powercontext capabilities` to diagnose an unavailable database versus an optional model failure. A background-only
process may legitimately retain `unobserved` readiness; `/metrics` does not turn it into a readiness checker.

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
| `oldest_pending_timestamp_seconds` | Database UTC creation time of the oldest unacknowledged request generation, in UNIX seconds; omitted when no pending work exists or any pending Scope has unknown oldest age |
| `oldest_pending_age_seconds` | Exact durable oldest age at the snapshot: database observation time minus oldest request time, in seconds; does not advance on passive reads and is omitted when idle, unobserved, or any pending oldest age is unknown |
| `pending_age_unknown_scopes` | Pending Scopes whose oldest unacknowledged generation has no durable timestamp; distinguishes unknown legacy age from an empty backlog |
| `pending_observation_failures_total` | Failed background pending observations, including timeouts; the previous count, oldest timestamp and age, unknown count, and snapshot timestamp are retained |
| `observed_pending_scopes` | Number of retained first-observed pending keys, bounded to 1000 |
| `oldest_observed_pending_age_seconds` | Age of the oldest retained first-observed pending generation; `0` if none retained |
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

Startup creates the additive `pc_artifact_processing_requests` table, which stores one database-UTC timestamp per
unacknowledged request generation (millisecond precision on SQLite, microsecond precision on MySQL). Stop old-version
writers before starting the new version: mixed-version writers can continue creating requests without timestamps. Request creation and acknowledgement pruning are atomic with the corresponding intent transition.
Acknowledgement deletes timestamps through the acknowledged generation, preserving newer requests. Restarts and
leadership changes preserve durable timestamps; the next successful observation restores their snapshot.
Existing pending generations without timestamps are not assigned an invented migration-time creation date. Their
oldest age remains unknown until those generations are acknowledged, even if newer timestamped requests arrive.
Both `oldest_pending_timestamp_seconds` and `oldest_pending_age_seconds` are omitted for the whole Family while
`pending_age_unknown_scopes>0`, rather than presenting the oldest known request as an exact answer. Before the first observation, the zero snapshot timestamp
means unobserved, including when count gauges are zero.

The count query examines the binding's intents with a primary-key timestamp probe per pending Scope: work scales
with binding intents, not all retained request generations. The oldest timestamp uses a binding/time index and
`LIMIT 1`. The retained aggregate snapshot uses O(Families) memory. Durable timestamp storage is O(unacknowledged
request generations), with acknowledgement pruning; it has no unsafe hard cap or lossy coalescing. A stalled Scope
receiving many requests can therefore grow storage until acknowledgement, even though it counts as one pending Scope.

`oldest_observed_pending_age_seconds` remains a separate **local lower bound**, measured from first local observation
of up to 1000 retained admitted keys. It tracks locally observed pending generations, not durable request creation
time. Membership may be stale between discovery passes while the local age advances. Unseen keys can be older;
acknowledgement, eviction, leadership changes, or restart can remove or reset local observations. A local age of `0`
does not prove an empty durable backlog. Use the durable count, timestamp, unknown count, and freshness together.

Attempt outcomes are `success`, `hold`, `noop`, `failure`, `cancelled`, `blocked`, `cursor_conflict`, `head_conflict`,
and `leadership_lost`. Discovery outcomes are `success`, `failure`, `cancelled`, and `leadership_lost`. The duration
histograms expose `_bucket`, `_count`, and `_sum`; bucket boundaries are 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1,
2.5, 5, 10, 30, 60, and `+Inf` seconds. `_count` counts attempts/passes, not unique requests or business successes.
A retry contributes another attempt. `hold` and `noop` remain distinct from business success. The legacy
`completed_total` counts verified durable acknowledgements and can include acknowledged holds/no-ops; it is not an
attempt-success or produced-artifact counter. Neither histogram nor acknowledgement counter is a billing ledger.

### Dashboards and alert rules

Choose and record explicit operator thresholds for scrape failure, collection age, probe age, requested-snapshot age,
pending age, and time without acknowledgement. These are deployment-specific alert policies, not built-in defaults.
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
- For a running Leader, gate stalled-processing alerts on a fresh collection and pending-work evidence: current
  `observed_pending_scopes>0` or `used_workers>0`, or a sufficiently fresh `unacknowledged_requests>0` snapshot.
  Then evaluate time without acknowledgement or the local pending-age lower bound against your chosen limit.
  Durable pending observation continues when worker slots are full; observation failures can still leave stale data.
  A stale snapshot must not suppress a stall alert supported by current retained work. The durable count is last-known
  background observation; local retained keys and worker slots are not a complete durable backlog. A zero acknowledgement timestamp means never
  acknowledged, not a UNIX-era outage; handle it separately with a startup grace period and sustained pending work
- `oldest_pending_age_seconds` reports age at the successful database observation, not at scrape time. To extrapolate
  from that snapshot, compute durable oldest age as `time() - powercontext_server_artifact_processing_oldest_pending_timestamp_seconds`
  only with `unacknowledged_requests>0`, `pending_age_unknown_scopes=0`, and a nonzero, sufficiently fresh
  `requested_snapshot_timestamp_seconds` from the same target/Family. Use a fresh collection too. Treat unknown legacy
  age, unobserved snapshots, and stale snapshots separately; missing oldest timestamps are not zero age. Account for
  clock skew between Prometheus and the database when choosing thresholds
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
calls; tracing does not capture unrelated host model calls automatically.

For environment-file deployments, put the tracing and OpenTelemetry variables in the explicit file passed to the
Server. See [environment configuration](../get-started/configure-server-environment.md) for precedence rules.
