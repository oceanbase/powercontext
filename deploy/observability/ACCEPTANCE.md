# Reference deployment acceptance

The reference is opt-in. The default SDK and ordinary local Server require no monitoring containers.
All execution below uses isolated databases and local model fixtures, without paid provider calls.

| Boundary | Executed evidence | Scope |
| --- | --- | --- |
| Host network | Real host Server on macOS, authenticated Docker-to-host scrape, actual Tempo/Loki/Prometheus queries | macOS Docker Desktop, arm64 |
| Container network | Current source built under Python 3.11; service-DNS scrape and authenticated gateway export; all three storage queries and an exemplar | Linux arm64 containers |
| API/background | Disposable OceanBase, one-shot schema initializer before both roles; separate authorized background scrape, liveness and absent business routes | Real standalone CLI service |
| Background processing | Explicit Source submission, supervisor-verified acknowledgement counter, matching acknowledgement log and stored child/model trace | Local no-op HTTP model; no claim of Memory creation |
| Model success/timeout | Real spawned Memory and Topic Memory workers export through the authenticated Collector and are queried from Tempo; a slow HTTP model also causes a real dependency alert, stored timeout trace/log and worker failure, then recovers to readiness and acknowledged processing with alert resolution | Local HTTP provider fixtures, not external-provider certification |
| Database write failure | Real SQLite write lock makes requests fail and also blocks database-backed metrics authorization; availability alert fires, then resolves after unlock; counters, trace and log queried after recovery | Live acceptance timing, with normal authorization intact |
| Worker stall | Actual spawned worker paused before execution; live Prometheus alert fires; worker released; verified acknowledgement and alert resolution required | Acceptance-only shortened age/hold, production timing tested separately |
| Receiver/storage outage | Collector, Tempo and Loki individually stopped; ten business requests per component complete within two seconds; real pipeline alert fires and resolves; fresh trace/log queries succeed | Real containers and storage, no in-memory exporters |
| Startup isolation | Container Server restarted while Collector stopped and becomes ready; no telemetry readiness dependency | Real configured Server process |
| Persistence | A trace recorded before backend interruptions remains queryable after restart | Retained local volume/WAL evidence |
| Provisioning | Five datasources and eight dashboards (116 non-row panels) loaded through Grafana API; dashboard PromQL and LogQL expressions checked against the deployed backends | Includes pinned Infinity 4.1.3, the read-only statistics adapter and the fixed current-alert datasource; not a claim that every panel was exercised in every state. Desktop 1440-pixel pages and all eight 320-pixel pages had no document-level horizontal overflow |
| Diagnostic navigation | A browser click on a real metric point opened filtered logs with the selected instance, operation and a ten-minute window centered on the point; log details exposed the Trace lookup | Actual Grafana and Loki browser path; Trace persistence and log correlation are separately covered by the isolated Tempo acceptance, not inferred from an expired historical ID |
| Instance and Worker identity | Two isolated CLI Servers expose independently scraped targets; a spawned Memory worker retains its owning target and role while keeping its own process identity; selected-instance logs exclude the other request | Native spawned JSON/console logging and OTLP tests plus real backend queries |
| Current outage and recovery | Stop the second isolated Server, wait for the production two-minute target-down hold, verify the current-alert table's name, affected instance and suggestion, then restart and verify alert resolution | Production rule timing and current `/api/v1/alerts`, isolated SQLite and local model fixture; original discovery file restored |
| Scoped business data | Two isolated SQLite Scopes contain one and two persisted Memory heads; actual Grafana output matches public statistics. Scope switches change the displayed inventory and API observations; model usage differs by owning Scope | Browser interaction plus real datasource queries; no inference from worker completion counts |
| Scoped operations | Actual API/child/model observations queried from Loki and Tempo; a multi-Scope application trace is found through its Scope array attribute. Unknown token values remain null in Grafana | Operational observations remain best-effort; process resource capacity stays global |
| Distribution | Wheel and source archive contain packaged statistics/Scope resources and exclude checkout-local state and credentials | Source archive regression includes local-state and environment sentinels |
| Sampling | Sampled request exemplars ingested by Prometheus and linked trace queried from Tempo; unsampled and tracing-disabled requests offer no exemplars | OpenMetrics and compatible default Prometheus text |
| Alert semantics | Production rules checked with promtool for quiet/no-op/held work, low traffic, dependency failures, sustained transport/application failures, stale/missing signals and recovery | Synthetic time-series fixtures validate exact production expressions/hold times |

## Reproduce

Follow [README.md](README.md) for the host/container/split commands. Run `verify.py` for storage and correlation,
`faults.py --container-startup` for explicit telemetry outages, and the opt-in pytest suite with
`compose.acceptance.yaml` for real worker/model/database/stall coverage. Restore the default Compose configuration
after acceptance. Per-run machine-readable reports remain in Git-ignored `.local/verification.json` and
`.local/fault-verification.json`; credentials and databases are not checked in.

The acceptance override changes test timing only: a two-second pending age and three-second holds. The normal stall
rule retains a ten-minute pending/progress threshold and a five-minute hold. A wall-clock fifteen-minute stall test
is not claimed. Current Prometheus alert state is verified through `/api/v1/alerts`; historical `ALERTS` series alone
can remain visible through lookback. Observation authorization can fail with its database, so a missing scrape is
unavailable evidence rather than a healthy zero.

## Operational limits

Linux host-gateway configuration is supplied; a separate bare-metal Linux host run is not claimed by the macOS host
execution. No HA, production load, disk quota, remote TLS termination or external paging delivery is certified here.
The default environment sends no notifications. Resource limits, single-node storage, asynchronous retention,
bounded export queues/retries, log rotation and delivery-loss limits are documented in the README.
Model accounting remains best effort; it is not a billing ledger. Operational completion observations do not prove
that a particular Memory exists or can be retrieved. Existing Phoenix/Langfuse integrations remain supported.

Run `PC_OBSERVABILITY_ACCEPTANCE=1 uv run --locked --extra tracing-otlp pytest tests/e2e/test_scoped_observability_stack.py`
for Scope storage/query acceptance. Set `PC_OBSERVABILITY_PACKAGE_ACCEPTANCE=1` to run the source archive regression in
`tests/e2e/test_observability_package.py`. The standalone OceanBase topology was previously executed; the Scope update
was additionally verified with real native workers. Repeating the standalone topology requires enough Docker memory
for its configured 6 GiB database in addition to the monitoring components.

Run `PC_OBSERVABILITY_ACCEPTANCE=1 uv run --locked --extra tracing-otlp pytest tests/e2e/test_monitoring_diagnostics.py`
for the diagnostic and production target-down recovery checks. This opt-in test temporarily adds two authenticated
local targets to an existing managed stack and restores the original discovery bytes in `finally`. It creates its own
SQLite databases and log subdirectory, uses a local HTTP model fixture, and removes its temporary dashboard and logs.
It requires Docker-to-host access and takes approximately five minutes. Browser clicks are separately inspected;
direct datasource queries do not verify Grafana variable interpolation. The diagnostic log filters use backtick
strings so textbox regex values and escaped instance names remain valid LogQL.
