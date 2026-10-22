# Optional reference observability stack

Prometheus stores metrics, Tempo stores traces, Loki stores reduced structured logs, and Grafana provisions those
signal datasources plus two read-only Infinity datasources. The eight dashboards include an all-service overview with
Service, Processing, Provider and Scope drill-downs. The internal statistics adapter queries persisted inventory and
usage. One OpenTelemetry Collector receives OTLP/HTTP traces and logs. The Server keeps a rotated JSON file for offline
audit, but the Collector does not tail that file. No Docker socket, host log tree, user database, automatic synthetic
workflow, cloud credentials or model calls are required. See
[RFC 0046](../../docs/en/rfcs/0046_observability_foundations.md).

## Start an isolated host demonstration

Requirements: Docker with Compose, OpenSSL, Python 3.11+, and `uv`. On macOS, if Docker Desktop's CLI is not on PATH,
run `export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"`. Reserve ports 13000, 14318 and 18080.
Run from the repository root:

```bash
uv run --locked python deploy/observability/init.py
docker compose -f deploy/observability/compose.yaml up -d
uv run --locked --extra tracing-otlp python deploy/observability/run-host.py
```

The last command stays in the foreground. It starts the real Server with an isolated `.local/host.db`, static bearer
authentication, 10% parent-based sampling, bounded trace/log exporter queues, and rotated JSON audit output. It clears
inherited PowerContext and model configuration so the example cannot select your existing database or paid model
provider.
The demo listens on all host interfaces for Docker-to-host scraping and requires its generated bearer credential.
Use a host firewall to restrict port 18080 to local/Docker traffic. Stop the foreground command with Ctrl-C.

Open http://127.0.0.1:13000, user `admin`; read the password from
`deploy/observability/.local/grafana-password`. Secrets are generated locally and ignored by Git. `init.py` preserves
existing secrets and `server.env`; it regenerates target discovery and the gateway password hash. File permissions on
explicit container secret mounts permit container reads; their host parent directory is private. Do not copy `.local`
into a public directory or publish it. The demonstration's Server token is an administrator; use a dedicated
`server.observe` Principal when connecting an existing deployment, with `scope.read` for the selected business statistics.

In a second terminal, verify actual storage queries and correlation:

```bash
uv run --locked --extra tracing-otlp python deploy/observability/verify.py
```

The verifier requires authorization failures for anonymous scrape/export requests, checks datasource/dashboard
provisioning, sends one sampled request with an explicit traceparent, and queries its trace, matching log and scraped
metric exemplar from real storage. It saves `.local/verification.json`. A receiver port or container health alone is
not acceptance evidence. No active probe runs unless you execute a verification command.

## Container demonstration

Build the current checkout, including the optional OTLP exporter, then start the isolated Server:

```bash
docker compose -f deploy/observability/compose.yaml -f deploy/observability/compose.container.yaml up -d --build
uv run --locked python deploy/observability/init.py --target reference-server:8000
uv run --locked --extra tracing-otlp python deploy/observability/verify.py --server http://127.0.0.1:18081
```

The Server exports traces and logs through the authenticated `gateway:8080`; Prometheus scrapes
`reference-server:8000` on the Compose network. The separate `reference-data` volume holds only demo data. The
container keeps rotated JSON logs for offline audit and sends reduced fields directly through OTLP/HTTP. The Server can
start while the telemetry receiver/storage is unavailable; telemetry is not a Server startup or readiness dependency.
The gateway starts after backend containers have been created, without assuming that storage is already ready.
Exporter retries handle temporary startup delay; the verifier waits for real data.
After switching targets, allow up to 30 seconds for discovery and a scrape. Recreate Prometheus if a host editor replaces
the bind-mounted target file instead of updating its contents. The gateway refreshes backend DNS every five seconds.

## Connect an existing deployment

First review the existing Server's access and operational contracts. Do not replace production configuration with the
demo environment. Copy only the tracing/logging settings from `.local/server.env`, install `tracing-otlp`, and configure
a dedicated observation token in `.local/observe-token`. Never place the token in target labels or committed YAML.
Set `POWERCONTEXT_SERVER_LOGGING_OTLP_ENABLED=true` for the Server and use the generated `server.env` as the source of
the OTLP endpoint, headers and bounded log-export queue settings. Add `otlp` to the Collector logs pipeline and apply
the generated transform fragment before export. JSON file logging remains optional for local audit; it is not a
Collector input. The transform removes free-form messages, exception strings, content and unregistered fields before
shipping. Allowed fields still require safe application values; the processor does not redact arbitrary secrets
embedded in an allowed field.

For a host Server, the scrape address is `host.docker.internal:<port>`. Linux Compose uses `host-gateway`; the host
listener must be reachable from that bridge, and the firewall must allow that traffic. For a container, connect it to
the Compose network and use its service DNS name. Do not use container `localhost` for a host or a different container.
The published OTLP endpoint is `http://127.0.0.1:14318` from the host and `http://gateway:8080` inside this network.
The reference uses loopback publication and an internal network trust boundary. Remote deployments must terminate TLS
at a managed ingress and use HTTPS; Basic authentication alone does not encrypt a transport.

Split `api` and `background` roles require OceanBase. Configure two targets:

```bash
uv run --locked python deploy/observability/init.py \
  --target host.docker.internal:8000 --background-target host.docker.internal:18082
```

Set `POWERCONTEXT_SERVER_METRICS_BACKGROUND_PORT=18082` and `POWERCONTEXT_SERVER_HTTP_HOST` in the background service's
own environment. Apply the same scrape credential or configure separate jobs/secret files for different Principals.
Keep `role` and `instance` labels: API snapshots do not observe another process's workers. The background listener
serves only `/metrics` and `/health/live`; do not probe its nonexistent business routes/readiness endpoint. Port 18082
avoids occupying the commonly used local embedding port 8001. SQLite/seekdb retain role `all`.

## Correlation and interpretation

The Tempo datasource provisions trace-to-log queries by service and trace ID. Logs retain `trace_id` as a field, not a
Loki index label. Copy that field into Tempo Explore's Trace ID query to check whether the backend stored it; a valid
ID can belong to an unsampled request. The logs dashboard states this limit and does not create unconditional links
to traces that may not exist. Prometheus exemplars carry only sampled trace IDs and are available with OpenMetrics
negotiation. Sampled does not guarantee delivery: exporter loss, retention and backend outages can remove the trace.
No span-derived duplicate metrics backend is configured.

All panels name their data source and limits in their description. Pending age is a local lower bound, policy blocks
are separate, and only supervisor-verified acknowledgement establishes completion. Readiness can contain cached
inference checks. Missing series, stale observations, worker reports and recorder losses must not be treated as healthy
zeroes. Usage observations are best-effort estimates, not an authoritative billing ledger.

## Limits, persistence and alerts

The pinned stack caps container memory at approximately 2.5 GiB in total, excluding the optional 768 MiB Server and
Docker overhead. A quiet local execution can use less; allow at least 4 GiB of Docker memory for experimentation.
Each container has a quota of at most one CPU. Prometheus keeps seven days with a 1 GB TSDB retention target; Tempo/Loki keep
24 hours. WALs, indexes, active blocks and compaction add overhead; retention is asynchronous and is not a disk quota.
Use a dedicated filesystem/quota and monitor disk space before operating at production scale. Local filesystem storage
is single-node and has no high-availability or backup guarantee. Named volumes survive `compose down`.

The Server's generated environment uses a 512-span queue, 128-span export batches, a 2-second export timeout and a
1-second batch schedule. Collector exporters each use 128 queued batches, two consumers, a 3-second request timeout,
and retries capped at 30 seconds per batch. Collector batches have at most 512 records. A 256 MiB memory limiter
refuses pressure before the 384 MiB container limit; refusal, overflow, expiry and process termination can lose data.
File offsets persist, export queues do not. File tailing is not exactly-once delivery; offsets can advance before
backend persistence. Demo log files rotate at 10 MiB with three backups; arbitrary external host logs need their own
rotation. The Docker runtime logs also rotate. Disk-full/unwritable logging and exporter failures remain best effort.

Prometheus evaluates actionable rules with hold times. The Grafana Dependencies dashboard shows firing alerts;
[runbooks](RUNBOOKS.md) explain diagnosis and recovery. Notification delivery is deliberately not preconfigured: connect
your organization's Alertmanager using an uncommitted override if external paging is wanted. No messages are sent by
this deployment. Check rules and low-traffic/no-op/hold/stale/recovery cases with:

```bash
docker compose -f deploy/observability/compose.yaml exec -T -w /etc/prometheus prometheus promtool test rules alert-tests.yaml
```

To explicitly interrupt only reference Collector, Tempo and Loki containers, verify business requests remain under
2 seconds, observe a real firing pipeline alert, restore each component, query fresh data and verify resolution:

```bash
uv run --locked --extra tracing-otlp python deploy/observability/faults.py
PC_OBSERVABILITY_ACCEPTANCE=1 uv run --locked --extra tracing-otlp pytest tests/e2e/test_reference_observability_stack.py -q
```

Add `--container-startup` when the container demo is also running to verify it can restart while the Collector is down.
The outage script takes approximately 5–8 minutes and restores a component in `finally` if a check fails. Run it only
against this isolated reference project. The worker tests run real spawned Memory and Topic Memory entrypoints,
including local HTTP model success and timeout, route their actual OTLP exports through the authenticated Collector,
and query their execution/model spans from Tempo. They use a local model fixture, not a paid/external provider.
Other acceptance boundaries are listed in [ACCEPTANCE.md](ACCEPTANCE.md).

## Stop

Run `docker compose -f deploy/observability/compose.yaml -f deploy/observability/compose.container.yaml down` and stop
the foreground host demo. This preserves volumes, secrets and reports. Removing volumes or `.local` deletes stored
evidence and requires a separate deliberate action. If you used split roles, stop that topology with:

```bash
docker compose --env-file deploy/observability/.local/split.env \
  -f deploy/observability/compose.yaml -f deploy/observability/compose.split.yaml down
```

## Configuration sources

Component configuration follows [Prometheus configuration](https://prometheus.io/docs/prometheus/latest/configuration/),
[Collector OTLP receiver](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/v0.141.0/receiver/otlpreceiver),
[Tempo configuration](https://grafana.com/docs/tempo/latest/configuration/),
[Loki OTLP ingestion](https://grafana.com/docs/loki/latest/send-data/otel/), and
[Grafana trace-to-log provisioning](https://grafana.com/docs/grafana/latest/datasources/tempo/configure-tempo-data-source/configure-trace-to-logs/).
Pinned images are reference choices; review release/security updates before production use.

## Isolated API/background acceptance

For the split-role demonstration, reserve at least 10 GiB of Docker memory: the disposable OceanBase container alone
has a 6 GiB limit. Its first bootstrap can take five minutes after downloading the image. Credentials and database
volumes are separate from any existing deployment:

```bash
uv run --locked python deploy/observability/init.py --split \
  --target reference-server:8000 --background-target reference-background:18082
docker compose --env-file deploy/observability/.local/split.env \
  -f deploy/observability/compose.yaml -f deploy/observability/compose.split.yaml up -d --build
uv run --locked --extra tracing-otlp python deploy/observability/verify.py --server http://127.0.0.1:18081
```

A one-shot `reference-init` initializes the isolated access/runtime schema before either concurrent role starts.
It refuses a database URL outside `reference-oceanbase/powercontext`. The explicit split demonstration includes an
unpublished local `reference-model` fixture that returns empty candidates/probes, with full sampling for acceptance.
It is suitable for observing successful no-op acknowledgement, not real extraction or inference quality. It performs
no paid model calls and creates no Source unless you explicitly submit one. To verify real background work as well:

```bash
uv run --locked --extra tracing-otlp python deploy/observability/verify.py \
  --server http://127.0.0.1:18081 --background http://127.0.0.1:18082 --exercise-background
```

This explicitly submits one Source to the isolated database, then requires a supervisor-verified completion metric,
an acknowledgement log and the corresponding stored worker/model trace. An acknowledgement can represent a no-op;
it does not promise a new Memory. Omit `--exercise-background` to check only observation/listener behavior.

The background listener is published only at `127.0.0.1:18082`. Check that anonymous `/metrics` returns 401, the
observation credential returns metrics, `/health/live` returns 200 and `/v1/capabilities` returns 404. Query Prometheus
for `up{job="powercontext",role="background"}` and require 1, rather than inferring observation from API metrics.
Use the same Compose arguments with `down` to stop without deleting volumes.

For the explicit real-worker stall test, enable the acceptance-only Prometheus override before running the opt-in
suite. It adds clearly named acceptance alerts with a 2-second pending-age threshold and 3-second holds; the production
10-minute age / 5-minute hold rule remains unchanged and its timing is checked by `promtool`:

```bash
docker compose -f deploy/observability/compose.yaml -f deploy/observability/compose.acceptance.yaml up -d prometheus
PC_OBSERVABILITY_ACCEPTANCE=1 PC_OBSERVABILITY_STALL_ACCEPTANCE=1 \
  uv run --locked --extra tracing-otlp pytest tests/e2e/test_reference_observability_stack.py -q
docker compose -f deploy/observability/compose.yaml up -d prometheus
```

Keep the isolated host demo running for this suite's real database write-lock test. The stall test temporarily adds
an authenticated target on port 18083 and restores target discovery when finished. It pauses an actual spawned worker,
observes the acceptance alert in Prometheus, then releases the worker and requires supervisor acknowledgement plus
alert resolution. This verifies live signals with accelerated test timing; it does not claim a wall-clock 15-minute
production threshold test. Do not leave the acceptance override enabled after verification.

Database-backed observation authorization can itself be unavailable during a database outage. In that case, use
scrape availability and fresh alert state as the first signal; failure counters can become queryable only after
recovery. The database write-lock test verifies a real scrape failure alert and resolution, followed by stored
failure metrics, trace and log. The current rule-state API is authoritative; historical `ALERTS` samples can still
appear through TSDB lookback. The alerts panel excludes samples older than 30 seconds.

Availability rules use a 45-second scrape-sample freshness cutoff for this stack's 15-second scrape interval. This
prevents retired targets from creating new outage/readiness alerts through historical lookback. If you lengthen the
scrape interval, adjust this cutoff with it. The missing-target rule also detects an entirely stale observation job.
Topology-specific verification reports are retained as `.local/verification-*.json` as well as the latest report.

The acceptance suite also runs a slow local HTTP model against a real Server/worker. It requires the dependency alert
to fire, queries the failed execution/model trace and timeout logs from storage, then restores the provider and waits
for cached readiness, worker acknowledgement and live alert resolution. `AcceptanceDependencyFailure` changes only
the production dependency rule's hold to three seconds. The ordinary dependency alert retains its three-minute hold.

## Installed CLI workflow

Use `powercontext observability up` (default `--backend docker-compose`) to start the packaged monitoring stack for an
existing Server, then restart that Server with `--observability`. The installed workflow, Docker override, platform
networking limits, state paths and verification commands are documented in
[English](../../docs/en/docs/operate/reference-observability-stack.md) and
[中文](../../docs/zh/docs/operate/reference-observability-stack.md).
The files linked from this directory have a single canonical source in
`src/powercontext/observability/resources/`; the CLI ships those same templates in the wheel.

## Scope filtering and business data

Open **Business** for current Memory total/active/inactive, Sources, Artifact families, Candidate states and model/recall
usage. Enter an exact Scope ID and press Enter; leave it empty for all accessible Scopes. Refresh is manual, and model
usage periods are UTC. Open **Scope activity** for scoped API completions, real worker events, logs and sampled traces.
Shared CPU/RSS, dependencies and worker capacity remain global. Scope IDs are structured metadata, not Prometheus
labels or indexed Loki stream labels. Grafana readers share the adapter's configured Server identity.

See the [complete Scope and business contract](../../docs/en/docs/operate/reference-observability-stack.md#scope-and-business-dashboards)
for counting units, authorization, retention, unknown values, external import and resource limits.


## Diagnostic navigation

The default `pc-overview` dashboard shows current exceptions and service/telemetry health, with a 15-second refresh.
It does not poll persisted Scope inventory. Current alerts use the fixed internal Prometheus rule endpoint through
`pc-alerts`, so names, condition-start times, annotations and runbooks remain available instead of a table of samples.

Click a plotted data point to pin the tooltip, then select **查看此时的相关日志** (view related logs). Hovering alone
does not expose the link. The target page opens the selected instance and operation's logs within five minutes on
either side. Log details expose request/Worker IDs and a Trace lookup; a missing stored Trace remains unknown. Exact instance filters require
`service_target`, mapped to `powercontext.service.target` in Trace resources. Managed profiles select API/background
endpoints and workers inherit their owning target. Untagged older logs require the all-instances filter.

Before updating an installed deployment, back up operator configuration and preserve credentials, targets and volumes.
Merge the dashboard/datasource resources, Collector allowlist and target resource attributes. Load instrumentation
changes through the normal Server restart. Dashboard-only updates do not instrument an old process. Allow file-based
provisioning to load replacements, or use a controlled Grafana restart; avoid repeated hot reload calls during writes.

Run `PC_OBSERVABILITY_ACCEPTANCE=1 uv run --locked --extra tracing-otlp pytest tests/e2e/test_monitoring_diagnostics.py -q`
against an enabled managed profile for actual two-instance requests, a spawned Worker failure/recovery, stored traces,
filtered Grafana log frames, and a production-duration target-down alert followed by recovery. The test adds only its
isolated targets, preserves scrape authorization and restores the original target file; no paid provider is used.
