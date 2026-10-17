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

Metrics are enabled by default at `/metrics`. In `enforced` mode, `/metrics` and `capabilities` require a Principal with
the `server.observe` permission. See [Troubleshoot](troubleshoot.md) for authentication and permission configuration.
Liveness at `/health/live` and readiness at `/health/ready` remain public.

`/metrics` exposes Prometheus-format metrics. Common metrics include:

| Metric | Use |
| --- | --- |
| `powercontext_server_transport_requests_total` | Count requests by transport, operation, and outcome |
| `powercontext_server_transport_request_duration_seconds` | Observe request duration |
| `powercontext_server_application_operations_total` | Observe successful and failed application operations |
| `powercontext_server_runtime_ready` | Determine whether the Runtime can accept operations |

`powercontext_server_runtime_ready=1` means only that the Runtime can accept operations. It may still be `1` while the
Server is `degraded`, so it does not prove that model capabilities are fully available. Combine it with the `status` from
`/health/ready` and `powercontext capabilities`.

To disable `/metrics`, set `POWERCONTEXT_SERVER_METRICS_ENABLED=false`; see [Configuration](configuration.md).

Use `powercontext ready` and `powercontext capabilities` to distinguish an unavailable database from a degraded
optional model provider. See [Troubleshoot](troubleshoot.md) for status definitions and recovery steps.

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
| `readiness_state{state}` | One-hot `ready`, `degraded`, `not_ready`; the compatible `runtime_ready` still means able to accept operations |
| `dependency_state{dependency,state}` | One-hot bounded dependency results; an absent dependency is unconfigured/unobserved, not a healthy zero |
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
`topic_memory.prepare` and `topic_memory.commit`. The child `artifact_processing.worker.execute` span describes a child
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
