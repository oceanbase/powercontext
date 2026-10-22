---
title: Reference observability stack
description: Run an optional local metrics, trace and structured log backend with provisioned dashboards and alerts.
---

# Reference observability stack

Use the opt-in [reference deployment](https://github.com/oceanbase/powercontext/tree/master/deploy/observability)
when you need to store and query all three operational signals locally. Prometheus stores metrics, Tempo stores
traces, Loki stores reduced structured logs, and Grafana provisions their datasources and dashboards. An OpenTelemetry
Collector receives OTLP/HTTP traces and logs. The managed stack also starts a local Phoenix instance for trace
inspection. Normal local and embedded use requires none of these services. External Phoenix and Langfuse integrations
remain supported through `connect`.

## Connect an existing observability platform

Use `connect` when Prometheus, Collector, Tempo, Loki and Grafana already run elsewhere. Docker is not required.
The command generates local connection/import files; it does not change remote configuration or start/stop components:

```bash
powercontext observability connect \
  --otlp-endpoint http://existing-collector:4318 \
  --server-env /path/to/existing.env \
  --metrics-target powercontext.example.internal:8000 \
  --prometheus-url https://monitoring.example/prometheus \
  --tempo-url https://monitoring.example/tempo \
  --loki-url https://monitoring.example/loki
```

The OTLP/HTTP base endpoint must be reachable from the Server; do not use the gRPC port or append `/v1/traces`.
Query URLs must be reachable from the CLI and may include reverse-proxy prefixes. TLS certificate verification stays
on; private CAs must be installed in the trust store used by the corresponding process.

Set `POWERCONTEXT_OBSERVABILITY_HOME` before connecting to use a separate directory. Managed and external records
cannot share a directory; existing deployments are never overwritten. Repeating `connect` without options displays
saved configuration. To change it, explicitly edit the saved connection/private configuration or choose a new directory.

Supply credentials through JSON header files, for example `{"Authorization":"Bearer ...","X-Scope-OrgID":"tenant"}`.
`--otlp-headers-file` configures Server export; `--prometheus-headers-file`, `--tempo-headers-file` and
`--loki-headers-file` configure separate query credentials. Headers are never reused across query backends. Do not embed
credentials in URLs or command arguments.

Follow the generated `external/README.md`:

- Merge `prometheus-scrape.yaml` into existing scrape jobs. If authentication is enabled, mount the generated
  `observe-token` read-only into Prometheus and grant its container user read access. This is the existing Server token.
- Import JSON files under `external/dashboards/` through Grafana Import, selecting existing datasources at import time.
  Datasource UIDs need not match the reference stack. Configure exemplar/trace-to-logs links in existing datasources.
- Add `otlp` to the existing Collector logs pipeline and apply the generated allowlist transform. The Server sends
  reduced structured fields directly through OTLP/HTTP. Optional `--log-directory /path/to/logs` configures a rotated
  JSON file for local audit only; do not add filelog for the same Server or logs can be duplicated.
- Restart the same Server with its original business config and `server run --env-file /path/to/existing.env
  --observability`. Native service installation also accepts `--observability`; containers can load the generated
  `server.env` as an additional env file.
- Review/import `external/alerts.yaml` if needed; reference-stack telemetry alerts are excluded. The Collector fragment
  is not a complete configuration and does not replace existing receivers, exporters, pipelines or notification routing.

```bash
powercontext observability status
powercontext observability check
powercontext observability check --exercise
```

`status` displays saved configuration only. `check` queries configured backends without generating business requests.
Missing query URLs are `NOT VERIFIED`; no trace/log in the last 15 minutes is `IDLE OR NOT DELIVERED`, not delivery proof.
`--exercise` requires all three query URLs and sends one sampled read-only capabilities request. It verifies the same
trace ID in trace storage, logs and a metric exemplar. Existing Prometheus must enable OpenMetrics and exemplar storage.
`up/down` refuse to control external components.

## Connect an existing Server with the CLI

Install PowerContext with `cli`, `server`, and `tracing-otlp` extras. Docker and Compose must be installed and the engine
must be running. A source checkout is not required.

```bash
powercontext observability up --server-env /path/to/existing.env
powercontext server run --env-file /path/to/existing.env --observability
powercontext observability check --exercise
```

Stop the old foreground process normally before running the second command. Reuse its environment file and database.
The CLI reads the file's bind/token settings but does not change it, restart the business process, or create a demo Server.
Without an environment file, use `observability up` and `server run --observability`. Use `--server-url` on first up for
another existing Server origin. Explicit conflicting telemetry settings are rejected without printing values.

`--backend docker-compose` is the default. Only this backend is implemented; `local` and `kubernetes` are rejected.
The backend selects how monitoring runs, independently of how the business Server runs.
Configuration and private credentials live under the platform user data directory's `powercontext/observability`,
independently of cwd and business `POWERCONTEXT_HOME`. Override it with `POWERCONTEXT_OBSERVABILITY_HOME`.
Up prints the Grafana and Phoenix URLs and the Grafana password-file path, never the password. Repeated up preserves
templates, credentials, and targets. Initialization options are only accepted on first up.

Up prints a restart command, the generated `server.env` (or Docker `container.env`) path, and the effective telemetry
settings: tracing enablement, log directory, OTLP endpoint/timeout, sampler and batch settings. Export authentication
headers stay private. `--observability` automatically loads the telemetry file; no manual export is needed for the CLI.
If `--server-env` was supplied on first up, its path is retained for the restart command. For a custom launcher, the
output includes POSIX commands to export `server.env` before the original start command. Keep business configuration
and the `tracing-otlp` extra in the Server's own Python environment.

### Local Phoenix

Phoenix is a service in the same Compose file: one container serving its Web UI and OTLP/HTTP receiver on port 6006.
The default URL is `http://127.0.0.1:6006`; select another port with `--phoenix-port` on the first `up`.
Phoenix stores traces in SQLite on a persistent volume. The UI has no built-in login, so only the host loopback port
is published. Keep the volume; repeated `up` does not reset stored traces.

The Collector configuration ships with an `otlphttp/phoenix` exporter on the trace pipeline, so the same traces reach
Tempo and Phoenix without rewriting any packaged file. Server telemetry configuration stays unchanged.
`up` does not block on Phoenix readiness; the container starts alongside the other backends, and a slow image pull or
startup cannot fail an otherwise healthy deployment. Use `status` to check it and `check --exercise` to query Phoenix
for the new request's trace ID. No prompt, content or model response collection is enabled.
`down` stops Phoenix and retains its volume. Delete the volume explicitly to discard its traces.

Allow about 4 GiB of Docker memory for the complete stack. The Phoenix image is about 1.5 GB and adds roughly 0.5-1 GiB
of memory; image download and first startup can take longer than a normal restart.

### Host reachability

Prometheus uses `host.docker.internal` by default. Docker Desktop and native Linux do not have the same routing.
On Linux, a container cannot reach a host Server listening only on loopback. Explicitly configure an authenticated,
Docker-reachable bind and `--metrics-target host:port`, restricted by the host firewall. The CLI never widens a bind or
disables authentication. Native personal services require loopback; use an authenticated foreground managed deployment
or Docker instead of bypassing that restriction on Linux. A successful up means containers started, not that business
signals arrived; run check.

### Native personal services

```bash
powercontext service install --env-file /path/to/existing.env --observability
powercontext service status
```

The service definition persists the profile location for ordinary restarts. Existing protected env-file identity checks
remain in effect. Explicitly stopped services remain stopped after installation; use `powercontext service start` when
ready. Pass `--observability` again when reinstalling a registration that should retain the integration.

### Existing Docker services

The official `docker/Dockerfile` includes the OTLP exporter. Upgrade to an image containing this capability, retaining the
business data volume. For a business Compose service named `powercontext` running as the default `10001:10001`:

```bash
powercontext observability up --server-mode docker \
  --server-env /path/to/existing.env --server-url http://127.0.0.1:8000
docker compose -f /path/to/business.yaml -f /absolute/observability/server.compose.yaml \
  up -d --no-deps powercontext
powercontext observability check --exercise
```

Replace the override path with the absolute configuration directory printed by up. The override adds a telemetry env
file, optional log-audit volume and monitoring network; it does not replace the database, image or command. Rename its
service key if necessary and initialize with `--metrics-target actual-service-name:8000`. For another container
identity, use `--server-uid` and `--server-gid` on first up. A one-shot container sets ownership on the optional log
volume; the business container remains non-root. Collector does not mount the business database volume.
Existing explicit Compose `environment` values take precedence over env files. Resolve conflicting old telemetry settings,
custom networks, log paths and permissions. This workflow targets one Docker host; updating one replica can interrupt traffic.
For split API/background deployments, initialize with `--background-target background-service:8001`; opt in both processes,
and add the same monitoring network and dedicated log volume to the background container as well.
Kubernetes installation and local binary management are not implemented.

### Inspect, verify and stop

```bash
powercontext observability status
powercontext observability check
powercontext observability down
```

Status inspects containers. Check queries real backends without generating requests; it reports current metrics scrape
health and whether traces/logs exist in the last 15 minutes. No recent samples mean idle or not delivered, not verified
success. Explicit `check --exercise` sends one sampled read-only capabilities request and waits up to 60 seconds for the
matching Tempo trace, Loki log and Prometheus exemplar. It does not create business Source/Memory records.
Down stops only the CLI's own monitoring stack; it retains volumes, configuration and credentials and leaves business
processes running. A lock prevents concurrent lifecycle mutations. After a crash, remove a leftover `operation.lock` only
after confirming no operation is running. Select free `--grafana-port`/`--gateway-port` on first up. If startup fails, inspect
status and retry up after fixing the cause. Package upgrades do not silently replace deployed configuration; incompatible
template versions fail explicitly and require the matching version to manage that deployment.

Server-owned JSON files preserve console output and use independent process files. Each file rotates at 10 MiB with
three backups; startup prunes to at most 64 process file groups (nominally 2.5 GiB; live processes can recreate pruned
files, so this is not a strict disk quota). Write and OTLP log-export failures do not alter business results. The
Collector receives approved structured fields over OTLP and drops arbitrary messages, exception stacks and paths.

## Start from a source checkout

Install Docker Compose, OpenSSL and `uv`. Run from the repository root:

```bash
uv run --locked python deploy/observability/init.py
docker compose -f deploy/observability/compose.yaml up -d
uv run --locked --extra tracing-otlp python deploy/observability/run-host.py
```

The foreground command starts an isolated authenticated Server on port 18080 with its own database. It removes
inherited PowerContext/model configuration. It makes no model calls or automatic synthetic writes. It binds the host
listener for Docker-to-host access; restrict the port with a host firewall. Existing databases are not mounted.

Open <http://127.0.0.1:13000> with user `admin` and the generated password in
`deploy/observability/.local/grafana-password`. In a second terminal, run:

```bash
uv run --locked --extra tracing-otlp python deploy/observability/verify.py
```

This checks real Prometheus, Tempo and Loki queries for one sampled request, including its metric exemplar and matching
log. It also checks authentication and datasource/dashboard provisioning. Reports remain under the Git-ignored `.local`
directory. A successful port or health check alone is insufficient.

## Deploy and interpret

Follow the deployment README for container topology, host networking on macOS/Linux, dedicated observation credentials,
retention, quotas, sampling, bounded queues/retry and telemetry fault verification. Split API/background deployments
require OceanBase; scrape both processes separately. The background listener serves metrics/liveness only and uses its
own port. Container startup ordering does not require telemetry storage to become healthy before the Server can work.

The Service dashboard covers requests, latency and application outcomes. Processing shows local pending observations,
verified acknowledgements, policy blocks and worker/recorder health. Dependencies shows cached checks, pipeline health
and active Prometheus alerts. Each panel states its data source and interpretation. Missing or stale values are unknown;
usage is not an authoritative billing ledger. A pending timestamp is a local lower bound rather than durable backlog age.

Trace-to-log correlation is provisioned. Copy a log's trace ID into Tempo Explore to verify whether it was stored;
unsampled IDs do not imply a stored trace. Exemplars are emitted only for sampled contexts when OpenMetrics is requested;
best-effort export or retention can still remove a trace. No unconditional log-to-trace link promises nonexistent data.
The reference collector exports allowlisted operational fields and drops messages/tracebacks before log shipping.

Alerts cover target availability, unhealthy dependencies, sustained errors with a minimum event count, pending work
without progress, stale/missing observations and pipeline failures. No-op and policy holds are distinct from failures.
See the deployment's `RUNBOOKS.md` and `ACCEPTANCE.md`. Rules are visible in Grafana/Prometheus; external notification
routing is an operator integration and sends nothing by default.

The reference is a single-node local environment with loopback-published backend access and an internal network trust
boundary. Remote use requires TLS at managed ingress. Retention is asynchronous, not a filesystem quota. Named volumes
preserve evidence across shutdown; review backups and disk limits before production use.

## Scope and business dashboards

The default home is **全服务总览** (all-service overview). It refreshes every 15 seconds and reads
Prometheus plus the current rule-state endpoint. It does not query Scope inventory, so an unavailable statistics
adapter does not hide service and telemetry health. Select service roles and instances, then inspect current
exceptions, freshness, dependency states, request trends and background progress.

| Dashboard | Filter | Default measurements |
| --- | --- | --- |
| All-service overview | Role, Instance | Current exceptions and actions, readiness, scrape timestamp, dependencies, traffic, execution failures, latency and backlog |
| Scope business progress | Scope ID, UTC usage period | Current processing, failed-attempt evidence, Artifact inventory, Candidate and feedback data |
| Background processing | Role, Instance, family | Running/queued tasks, concurrency limit, results awaiting confirmation, outcomes, latency and family-specific logs |
| Model calls | Role, Instance, Provider, capability, execution | Calls, outcome fractions, P50/P95, parent/Worker, HTTP status and per-call logs |
| Service and collection | Role, Instance, operation, family, error category | Request outcomes, status codes, dependencies, telemetry and filtered diagnostic logs |

The five main pages remain in `PowerContext`. Inventory and usage (`pc-business`), scoped activity (`pc-scopes`)
and dependency detail (`pc-dependencies`) remain in `PowerContext 高级明细` with their existing URLs.
Scope snapshots use manual refresh because they query persisted business data. Inventory, worker acknowledgements,
request counts, host adoption and estimated token reductions retain their separate meanings.

The overview shows fresh readiness independently of scrape reachability. Readiness older than 120 seconds,
a scrape older than 45 seconds or a failed target yields unknown readiness. A failed scrape can mean process,
network or observation-credential failure; it does not establish a database outage. Current alerts come from
`/api/v1/alerts` through the fixed `pc-alerts` Infinity datasource. Their table shows identity, state, affected instance,
condition-start time, impact, suggested checks and a runbook. Pending is distinct from firing; historical metric
samples are not presented as the current alert list. Telemetry alerts remain visible while filtering service roles.
Exporter failure charts use rates instead of cumulative counts. No traffic does not imply a measured zero failure fraction.

Click a plotted service or background data point to pin the tooltip, then select **查看此时的相关日志** (view related
logs); hovering alone does not expose the link. The target opens diagnostic logs within five minutes on either side
of that point. The link carries available instance, role, operation and family labels. Provider table rows open that instance's logs
for the selected range. The log view applies the visible operation, family and error-category regex filters; it includes
workers belonging to the selected target and owning role through `service_owner_role`. A `service_target` field must match the Prometheus `instance` endpoint for
exact-instance filtering. Untagged older logs are available only with all instances selected. See the
[identity contract](observability.md#operational-fields-and-correlation).

The log summary displays event, operation, outcome, error classification, duration and execution IDs. Expand a record
for its structured fields and **View trace**. Tempo can return matching logs for a stored trace. A missing trace remains
an evidence gap: an ID alone does not identify sampling, delivery or retention as its cause. Aggregate metrics identify
a time window; an exemplar identifies one sampled execution, not every request in that window.

The Service and Collection dashboard shows current alerts, readiness, scrape state, in-progress work and readiness
freshness before the HTTP/MCP request and latency trends. Request and application outcomes are summarized by outcome;
the sortable interface table retains operation-level lifetime counts. Status and counters use the end of the selected
time range, which represents the current value when the end is `now`. The alert table shows current pending/firing
alerts independently of the historical range. Operation, family, error-category, outcome and status-code regexes
filter logs only. Chart points and operation names in the interface table open the matching instance's logs;
the table converts displayed operation names back to stable IDs. Service diagnostics default to manual refresh
so expanded evidence remains open. Select an interval when needed; an explicit `refresh=15s` URL still refreshes.

Sustained transport failures first open estimated failure counts grouped by operation and HTTP status over the last
five minutes. Click a count for matching failure logs. A 401 establishes authentication rejection, not an expired
credential; distinguish 403, invalid parameters and 5xx errors. MCP failures do not receive fabricated HTTP statuses.
Processing-stall alerts open historical block evidence for the matching instance and family. The range covers at
least the last 24 hours or the earlier observed pending start, capped at 30 days. Compare historical evidence with
current processing state. `window_attempt_limit` explains why further attempts stopped, not why earlier attempts
failed. Alert `activeAt` starts the rule's pending condition; it is not the original error time. The guide opens
inside the local service dashboard without depending on a remote GitHub branch file.

The latest-log panel finds the most recent event in 24 hours for the selected instance and role, independently of
other log filters. Active requests with old log evidence require investigation; old logs for an idle service do not
by themselves establish a fault. Missing records remain an evidence gap. An already running process must restart
normally to load direct OTLP logging. Dashboard or Collector updates cannot enable that exporter inside an old
process. If a container's mounted files stop following host updates, a one-time catch-up after a Collector restart
does not establish continuous ingestion.

Existing deployment copies remain operator-owned. Back up edited files, merge the dashboard and datasource resources,
and merge the Collector `service_target` and `service_owner_role` fields before restarting the affected monitoring components. Its log receiver
accepts root JSONL files and JSONL files in one child directory, allowing separate service log directories. Keep
credentials, scrape targets and volumes. Configure the API and optional background targets in `OTEL_RESOURCE_ATTRIBUTES`
and load the new Server code through its normal restart. Updating JSON alone cannot add fields to an old process.
Dashboard files are loaded by provisioning's watcher. Avoid repeated provisioning reload calls while replacing files;
use a controlled Grafana restart if the pinned deployment cannot reload safely.

External imports also receive `alerts-datasource.yaml`. Adapt its fixed Prometheus URL to Grafana's network and keep
its optional private query headers protected. Select that datasource when importing the overview and current-alert
panels. Existing Prometheus, Loki, Tempo and statistics datasources remain separate.

For HTTP status observations, Service shows rates and current-process totals. These exclude health probes, metrics
and the MCP bridge; they describe outgoing REST status, not upstream Provider status. A stream can retain HTTP 200
while its execution fails. See the [response counting contract](observability.md).

To enable provider observations, load the updated Server code and copy `pc-providers.json` plus the updated Overview
and Service navigation. Merge the Collector allowlist entries `provider`, `model_capability`, `provider_retry` and
`provider_error_code`; validate its configuration and restart the Collector. Dashboard provisioning uses the file watcher.
Use the [provider observation contract](observability.md#provider-call-observations) to interpret worker report delays,
unknown retry markers and unavailable HTTP coverage. Updating a dashboard does not add instrumentation to an old process.

In **Business**, enter a Scope ID and press Enter. Use **Refresh** to obtain another snapshot. Automatic refresh is off:
statistics reads query persisted inventory and usage and have a cost proportional to the selected data. `as_of` describes
current inventory; `today`, `7d` and `30d` select UTC model/recall usage independently of Grafana's time picker.
Memory counts refer to current atomic Memory heads, not revisions or successful worker invocations. Inactive includes
forgotten, merged and retired entries. Processed Sources can include no-op results. Missing/denied/unavailable readings
are unknown; unavailable provider token counts remain null.

The internal statistics adapter calls only `POST /v1/stats`, using the existing Server identity and its Scope read
permissions. It has a separate proxy credential, fixed upstream, five-second timeout, eight-request concurrency limit
and a 16 MiB response limit. It has no published port, supports no business write route, and does not cache scoped
responses. Grafana installs the signed Infinity plugin pinned to 4.1.3. All Grafana readers share the configured
statistics identity: restrict access to trusted operators. Grafana authentication does not grant each user a separate
PowerContext identity. Never publish this operator datasource as a tenant portal.

**Activity and evidence detail** (`pc-scopes`) derives its measurements from retained structured logs, not high-cardinality Prometheus labels.
`application.scope.completed` is one participating Scope's observed application completion. A request involving two
Scopes produces two involvement observations and remains one request in the existing global request counter. Statistics
polling is excluded from scoped API graphs. Rejection before application dispatch has no trusted Scope attribution;
authentication/validation failures remain visible on Service. Empty traffic, dropped logs and expired retention must
not be interpreted as a healthy zero. Keep INFO operational logging enabled for these panels.

Scope IDs remain structured `scope` fields in logs and `powercontext.scope.id` / `powercontext.scope.ids` span
attributes. They are not indexed Loki stream labels or Prometheus labels. Runtime and spawned-worker model spans
inherit the owning Scope. The Role choice **Background** includes child `worker` events; **Combined** selects `all`.
Shared CPU/RSS, database health and global queue capacity cannot be attributed to an individual Scope and remain
explicitly global. Scope selection does not filter those shared measurements.

External deployments receive `stats-proxy.py`, private `stats.json`, and `stats-datasource.yaml`. Follow the generated
README to run the read-only adapter, install Infinity 4.1.3, and adapt the fixed upstream and Grafana-reachable address.
The ordinary Prometheus/Tempo/Loki imports continue to use existing datasources. Saved managed deployment copies stay
stable across package upgrades; new templates are not copied over an operator's existing configuration automatically.

## Scope detail dashboard

Open **Scope 业务进展** (Scope business progress) from the main dashboard navigation. Select one Scope and refresh. The first panel keeps the
native Canvas titled **Scope · 服务流程 · 当前快照** (Scope service flow, current snapshot). It connects input
inventory, current scheduling and execution, Candidate review, Artifact inventory, retrieval and lifecycle
maintenance. Below the Canvas, the processing table sorts failures and policy blocks first, then shows Artifact,
Candidate and Experience feedback inventory. Retrieval, logs, traces and topology evidence are collapsed by default.
Inventory and processing are current snapshots; log and metric trends use the Grafana time range.

The native Grafana Canvas at the top shows nodes and connections. Click a node to open its detail panel with the Scope and time range preserved. Worker nodes show locally observed state; missing observations are gray. API rate, retrieval duration and nonempty rate use the last five minutes of shared service data. Connections describe processing relationships; they do not prove that an individual input completed every step. Agent adoption remains unknown without host feedback.

The topology covers seven processing families: Memory, Topic Memory, Profile, Experience, Skill, Handoff and Prompt. Coding Agent belongs to a separate Client group outside Server ingestion and persistence. Source-driven scheduling connects to Memory, Topic Memory, Profile and Experience, subject to their configuration. Skill, Handoff and Prompt background work uses explicit Dream requests; Dream can also revise the first four families. Prompt also has a direct configuration-write path. A new Source does not automatically generate a Prompt, although an explicit Prompt Dream can select Sources as evidence. Each row shows artifact and pending-candidate counts side by side; it does not require review on every path. Profile review depends on its policy. Handoff inventory includes committed records, excluding temporary Prepared Handoffs. Prompt inventory counts persisted configurations, not effective uses.

The four searchable families show their own shared direct-retrieval p95. Context preparation is included in the overall retrieval metrics below and is not attributed to individual families. Profile shows direct head/revision read p95, including successful conditional reads (304); it does not measure Profile reads inside Context preparation. Direct reads have separate outcome and duration panels and do not enter retrieval denominators. Handoff shows successful `continue_handoff` p95; Prompt shows successful `get_prompt_configuration` p95. These observations do not prove host adoption or configuration use. Retrieval and Profile nodes link to their duration trends; Handoff and Prompt consumption nodes link to related API operation logs. `artifact.read.completed` identifies the read family and distinguishes returned content, 304, failure and cancellation in scoped logs.

`POST /v1/stats` accepts optional `include_processing: true` (default false). Each `by_scope` row then includes processing bindings, durable requested/handled and dirty/clean generations, and an independently timestamped local scheduler observation when available. `acknowledged` does not prove artifact creation. `pending_since` is a local lower bound. `next_check_seconds` is a check estimate, not an execution guarantee. API/background splits without a local leader report scheduler state as unobserved; remote state and attempt history are not reconstructed.

Processing nodes show the latest failure cause separately from scheduling state. The table adds a suggested action, failure boundary, consecutive failure count, last failure time and next check. `last_error_code`, `last_error_stage` and `last_error_at` describe the selected Scope and binding's current local retry cycle. Codes identify bounded categories such as access denial, unavailable authorization, model configuration, timeout, invalid model output or worker failure; they contain no exception message. Stages name an observation boundary, not an internal domain operation. The timestamp is when the supervisor observed the failure in UTC. A retry retains the previous failure while queued or running; verified acknowledgement clears it. Restart and retry-cache eviction can remove the observation. Older Servers show an unobserved cause instead of a guessed diagnosis. Suggested actions are guidance and do not grant permissions or change retry behavior.

Retrieval metrics cover dispatched `search_memory`, `search_atomic_memory`, `search_topic_memory`, `search_artifacts`, `prepare_context` and queried `list_managed_skills`. Empty results count as successful execution. Library browsing without a query is excluded. The shared service rates are not attributed to the selected Scope; Scope detail comes from content-free logs. Relevance and adoption remain unverified without labels or host feedback. Experience feedback counters are lifetime observations, not causal effectiveness. Skill usage requests are not deduplicated invocation counts.

Upgrade an existing deployment by loading the new Server code, copying `pc-flow.json` and the updated navigation dashboards, and replacing the statistics proxy script. Merge `retrieval_family`, `retrieval_outcome`, `result_count`, and `read_outcome` into the Collector allowlist and restart only the affected monitoring components. The existing deployment is not overwritten automatically. Keep backups of operator-edited files. No automatic forgetting or deletion policy is enabled by this feature.

The Scope processing table shows local Scope status, the last failure and the next check, with links to scheduling detail and the failed attempt evidence. Host-use receipts, actual thread/process inventory and complete per-attempt queue history remain explicitly unsupported. Failure links retain the existing worker identity and a five-minute window on either side of failure; a later running worker does not replace that identity. Missing IDs, sampling or retention can leave no trace. Verify acknowledgement progress, artifact inventory and fresh observations after remediation. Alert aggregation preserves job, instance and role to match the Overview role filter.
