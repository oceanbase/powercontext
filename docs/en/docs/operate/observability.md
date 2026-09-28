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
calls; tracing does not capture unrelated host model calls automatically.

For environment-file deployments, put the tracing and OpenTelemetry variables in the explicit file passed to the
Server. See [environment configuration](../get-started/configure-server-environment.md) for precedence rules.
