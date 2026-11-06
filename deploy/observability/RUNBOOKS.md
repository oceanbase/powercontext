# Reference stack runbooks

Read the alert labels first. Keep `job`, `instance`, `role`, `family` and `dependency` attribution.
Use the Service, Processing and Dependencies dashboards; each panel describes its data source and limits.
Do not restart a production database or delete records as a telemetry repair.

## PowerContextTargetDown

**Meaning:** PowerContext metrics target is unreachable. The condition must persist for 2m.

**Action:** Check target address, process, server.observe credentials and network before restarting.

For a missing target, verify `.local/targets.json` and `/api/v1/targets` through the authenticated Prometheus gateway. Keep expected targets in discovery during an outage: removing one hides its `up=0` series. Check the observation credential has `server.observe`. Public liveness alone does not prove that authenticated scraping works. Scraping cannot refresh readiness.

**Recovery:** restore the owning dependency or collector, query fresh observations, then confirm in Prometheus `/api/v1/alerts` that this alert is no longer firing after its next evaluation. Historical `ALERTS` samples can remain in TSDB lookback. Retain the failure and recovery timestamps.

## PowerContextTargetsMissing

**Meaning:** No PowerContext targets are configured or observed. The condition must persist for 2m.

**Action:** Restore .local/targets.json. Missing series are not healthy zeroes.

For a missing target, verify `.local/targets.json` and `/api/v1/targets` through the authenticated Prometheus gateway. Keep expected targets in discovery during an outage: removing one hides its `up=0` series. Check the observation credential has `server.observe`. Public liveness alone does not prove that authenticated scraping works. Scraping cannot refresh readiness.

**Recovery:** restore the owning dependency or collector, query fresh observations, then confirm in Prometheus `/api/v1/alerts` that this alert is no longer firing after its next evaluation. Historical `ALERTS` samples can remain in TSDB lookback. Retain the failure and recovery timestamps.

## PowerContextReadinessStale

**Meaning:** Readiness evaluation is stale. The condition must persist for 2m.

**Action:** Check readiness evaluator and process logs. Scraping does not refresh dependency probes.

For a missing target, verify `.local/targets.json` and `/api/v1/targets` through the authenticated Prometheus gateway. Keep expected targets in discovery during an outage: removing one hides its `up=0` series. Check the observation credential has `server.observe`. Public liveness alone does not prove that authenticated scraping works. Scraping cannot refresh readiness.

**Recovery:** restore the owning dependency or collector, query fresh observations, then confirm in Prometheus `/api/v1/alerts` that this alert is no longer firing after its next evaluation. Historical `ALERTS` samples can remain in TSDB lookback. Retain the failure and recovery timestamps.

## PowerContextReadinessMissing

**Meaning:** A live target has no readiness observation. The condition must persist for 2m.

**Action:** Confirm that this endpoint is PowerContext metrics and that metrics are enabled.

For a missing target, verify `.local/targets.json` and `/api/v1/targets` through the authenticated Prometheus gateway. Keep expected targets in discovery during an outage: removing one hides its `up=0` series. Check the observation credential has `server.observe`. Public liveness alone does not prove that authenticated scraping works. Scraping cannot refresh readiness.

**Recovery:** restore the owning dependency or collector, query fresh observations, then confirm in Prometheus `/api/v1/alerts` that this alert is no longer firing after its next evaluation. Historical `ALERTS` samples can remain in TSDB lookback. Retain the failure and recovery timestamps.

## PowerContextDependencyFailure

**Meaning:** A configured dependency is unhealthy. The condition must persist for 3m.

**Action:** Inspect dependency label and readiness logs; check credentials, database and provider. Cached inference probes can delay resolution.

Inspect `/health/ready` on API/all roles and the dependency state series. Model timeouts and cached readiness can produce a degraded service while the Runtime remains available. Check database/model reachability from the affected process. Do not put provider error bodies or credentials into an incident note.

**Recovery:** restore the owning dependency or collector, query fresh observations, then confirm in Prometheus `/api/v1/alerts` that this alert is no longer firing after its next evaluation. Historical `ALERTS` samples can remain in TSDB lookback. Retain the failure and recovery timestamps.

## PowerContextSustainedErrors

**Meaning:** Sustained application errors exceed 5 percent and five failures. The condition must persist for 5m.

**Action:** Use operation/outcome panels, logs and sampled traces. No-op, blocked and cancelled outcomes are excluded from failures.

Group application failures by operation and instance. Use request IDs in Loki and query a sampled trace in Tempo. Application failure rules exclude no-op, holds and cancellation. Transport failure rules include responses rejected before application dispatch and can include client mistakes. Both require at least five failures in the five-minute window; isolated low-traffic failures still appear in logs without triggering this sustained-error rule.

**Recovery:** restore the owning dependency or collector, query fresh observations, then confirm in Prometheus `/api/v1/alerts` that this alert is no longer firing after its next evaluation. Historical `ALERTS` samples can remain in TSDB lookback. Retain the failure and recovery timestamps.

## PowerContextProcessingStalled

**Meaning:** Observed pending work has no acknowledged progress. The condition must persist for 5m.

**Action:** Inspect family, worker lifecycle and model timeout. Pending age is a local lower bound, not durable backlog age; tune for expected task duration.

Inspect the same family/instance in the Processing dashboard. Compare discovery and backlog observation timestamps with scrape time. Search Loki for worker failure, blocked, returned and acknowledged events. Child return is not durable acknowledgement. A healthy idle family need not have a recent success; local pending age omits undiscovered/evicted/restarted work. A policy block needs domain action rather than a process restart.

**Recovery:** restore the owning dependency or collector, query fresh observations, then confirm in Prometheus `/api/v1/alerts` that this alert is no longer firing after its next evaluation. Historical `ALERTS` samples can remain in TSDB lookback. Retain the failure and recovery timestamps.

## PowerContextDiscoveryStale

**Meaning:** Leader discovery has stopped reporting progress. The condition must persist for 2m.

**Action:** Check supervisor health and database connectivity; do not treat cached pending counts as current.

Inspect the same family/instance in the Processing dashboard. Compare discovery and backlog observation timestamps with scrape time. Search Loki for worker failure, blocked, returned and acknowledged events. Child return is not durable acknowledgement. A healthy idle family need not have a recent success; local pending age omits undiscovered/evicted/restarted work. A policy block needs domain action rather than a process restart.

**Recovery:** restore the owning dependency or collector, query fresh observations, then confirm in Prometheus `/api/v1/alerts` that this alert is no longer firing after its next evaluation. Historical `ALERTS` samples can remain in TSDB lookback. Retain the failure and recovery timestamps.

## PowerContextCollectionFailure

**Meaning:** Processing metrics snapshots failed. The condition must persist for 1m.

**Action:** Check supervisor snapshot errors. Existing gauges can be stale.

Inspect the same family/instance in the Processing dashboard. Compare discovery and backlog observation timestamps with scrape time. Search Loki for worker failure, blocked, returned and acknowledged events. Child return is not durable acknowledgement. A healthy idle family need not have a recent success; local pending age omits undiscovered/evicted/restarted work. A policy block needs domain action rather than a process restart.

**Recovery:** restore the owning dependency or collector, query fresh observations, then confirm in Prometheus `/api/v1/alerts` that this alert is no longer firing after its next evaluation. Historical `ALERTS` samples can remain in TSDB lookback. Retain the failure and recovery timestamps.

## TelemetryTargetDown

**Meaning:** Telemetry component cannot be scraped. The condition must persist for 1m.

**Action:** Check the component logs and memory limit. Business health is independent of telemetry health.

Check `docker compose -f deploy/observability/compose.yaml ps` and the affected component logs. Query Collector send failures, queue sizes, memory and receiver refusal metrics. Recover receiver/storage connectivity first. Queued telemetry can expire or disappear on restart; fresh successful ingestion is required even after scrape health recovers.

**Recovery:** restore the owning dependency or collector, query fresh observations, then confirm in Prometheus `/api/v1/alerts` that this alert is no longer firing after its next evaluation. Historical `ALERTS` samples can remain in TSDB lookback. Retain the failure and recovery timestamps.

## TelemetryExportFailure

**Meaning:** Collector export failed. The condition must persist for 1m.

**Action:** Check Tempo/Loki availability. Queue capacity and retries are bounded; expired batches are lost.

Check `docker compose -f deploy/observability/compose.yaml ps` and the affected component logs. Query Collector send failures, queue sizes, memory and receiver refusal metrics. Recover receiver/storage connectivity first. Queued telemetry can expire or disappear on restart; fresh successful ingestion is required even after scrape health recovers.

**Recovery:** restore the owning dependency or collector, query fresh observations, then confirm in Prometheus `/api/v1/alerts` that this alert is no longer firing after its next evaluation. Historical `ALERTS` samples can remain in TSDB lookback. Retain the failure and recovery timestamps.

## PowerContextSustainedTransportErrors

**Meaning:** Sustained transport failures exceed 5 percent and five responses. The condition must persist for 5m.

**Action:** Check logs by operation and error_category. This includes client authentication/validation errors and failures before application dispatch; distinguish these from server faults before paging a dependency owner.

Group application failures by operation and instance. Use request IDs in Loki and query a sampled trace in Tempo. Application failure rules exclude no-op, holds and cancellation. Transport failure rules include responses rejected before application dispatch and can include client mistakes. Both require at least five failures in the five-minute window; isolated low-traffic failures still appear in logs without triggering this sustained-error rule.

**Recovery:** restore the owning dependency or collector, query fresh observations, then confirm in Prometheus `/api/v1/alerts` that this alert is no longer firing after its next evaluation. Historical `ALERTS` samples can remain in TSDB lookback. Retain the failure and recovery timestamps.

## Storage and sampling limits

A log trace ID alone does not prove a stored trace. The reference deliberately avoids unconditional log-to-trace links.
Prometheus exemplars require sampled spans and OpenMetrics; recording, delivery and retention can still limit drill-down.
Missing usage reports are unknown loss, and local metrics are not durable billing or business ledgers.

## Database-backed scrape authorization

A database outage or write lock can prevent the `server.observe` authorization/audit path, producing HTTP 503 from
`/metrics` while public liveness still succeeds. Treat the failed scrape as unavailable observation, not zero errors.
Use target availability, readiness and already shipped dependency logs first. After recovery, query the newly scraped
failure counters and stored trace/log before declaring the failure path understood.

Expected targets should remain in discovery during an outage. When intentionally retiring a target, remove it from
discovery; the 45-second sample-freshness gate prevents old up samples from starting new availability alerts. The
reference scrape interval is 15 seconds; update its freshness cutoff if changing the interval.
