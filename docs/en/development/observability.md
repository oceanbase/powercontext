---
title: Observability development
description: Add operational events with stable fields, correct correlation, and failure isolation.
---

# Observability development

Before adding a log, metric, or span, identify the question it answers and the boundary that owns the observation.
Use the [operational field contract](../docs/operate/observability.md) for names, units, outcomes, error categories,
and correlation semantics. [RFC 0046](../rfcs/0046_observability_foundations.md) defines privacy and signal ownership.

## Add an operational event

Use a module logger and `powercontext._logging.log_safely`. Keep the message fixed and put approved diagnostic values
in structured fields. Log useful state changes and failures; detailed stage completion normally belongs at `DEBUG`.
Existing transport/application instrumentation already counts and times operations; do not duplicate those units.

```python
import logging
from time import perf_counter

from powercontext._logging import log_safely

logger = logging.getLogger(__name__)


def record_search_completion(started_at: float) -> None:
    log_safely(
        logger,
        logging.DEBUG,
        "Memory search completed",
        extra={
            "event": "memory.search.completed",
            "operation": "memory.search",
            "unit": "stage",
            "outcome": "success",
            "duration_ms": max(perf_counter() - started_at, 0) * 1000,
        },
    )
```

Call this after the owning search stage completes successfully. Its operation matches the Runtime stage name;
application events use the declared HTTP operation ID, such as `search_memory`, across HTTP and MCP.
The example uses existing allowed fields and does not require a formatter change. It is not a public instrumentation API.

Record a failure or cancellation at the boundary that observes it. Keep no-op and policy blocks distinct from errors.
Use a registered diagnostic code and the actual mapped status where available; the adapter derives a bounded
`error_category`. Do not turn arbitrary exception messages into codes. Re-raise cancellation.

## Reuse execution identity

Server filters fill a missing `request_id` from the bound request context and attach current trace/span IDs.
Explicit request IDs are preserved. Do not create a request ID in a Runtime method. Trace IDs can exist without
recording/export, so a correlation ID alone is not evidence that a backend stored a trace.

The spawn runner binds assignment-owned worker fields for child events. The supervisor uses the same `worker_id`
for the attempt; each process has its own `service_instance_id`. Scheduled work clears accidental request correlation.
Code running inside a configured worker should emit its event without regenerating these identities.

`artifact_processing.worker.returned` reports the child result. Only
`artifact_processing.worker.acknowledged` reports supervisor verification of durable acknowledgement.
Keep that distinction when adding lifecycle events; process exit alone does not prove business completion.

## Extend structured fields

JSON formatting deliberately ignores unknown extras. When a new field is required:

1. Define its meaning, type, unit, allowed values, and privacy boundary. Counts must identify their counted unit;
   RSS high-water marks must not be presented as per-request allocation.
2. Update the applicable allowlist in `src/powercontext/server/logging.py`: `_OPERATIONAL_FIELDS` for general fields,
   `_PROCESSING_FIELDS` for processing, or `_CODE_FIELDS` for `code.*` events.
3. Verify JSON and console output. `_HumanContextFilter` uses the same allowlists; request, trace, and service
   identities have dedicated console rendering, and code-specific fields appear only on `code.*` events.
4. Update the operational field contract in both locales and add a focused observable-output regression test.
   Review compatibility before renaming or removing published fields or event names.

Service identity comes from `src/powercontext/server/telemetry.py`; logs and traces use the same resource identity.
Error classification comes from `powercontext._logging.error_category`, with domain-owned categories such as `blocked`
set at their owning boundary. Do not create independent vocabularies in each adapter.

## Preserve privacy and ownership

Do not record Source/Memory content, queries, prompts, responses, vectors, credentials, authorization headers,
full database URLs, or sensitive filesystem paths. An allowed field still requires safe values; an allowlist is not
a redaction engine. Do not interpolate those values into the message or include them in a raw provider exception.
Use `exc_info` only after reviewing the error's values and attached context for sensitive data.

The Server configures handlers and format. It injects the picklable `WorkerLogging` bootstrap into automatically
composed spawn workers through `worker_initializer`. Custom bindings own their launcher initialization.
Importing or running the Runtime as an SDK must not configure global logging. Keep Runtime observations independent
of Server settings, Prometheus, and OTel implementation types.

Operational logs are best-effort. The Server handler isolates filter/formatter/write failures and emits one
content-free failure notification per handler. Do not use logs or sampled traces as a durable audit, billing,
or commit ledger. Request/trace/scope/source/worker identities do not belong in metric labels.

## Verify the behavior

Run the focused logging and real-worker checks:

```bash
uv run pytest tests/test_server_logging.py tests/e2e/test_worker_logging.py -q
```

For new instrumentation, cover the observable outcome that can regress: success, failure/cancellation where relevant,
required field output, sensitive-value exclusion, and failure isolation. A process-boundary claim requires a real
spawned worker; invoking a hook directly or replacing the worker entrypoint cannot prove built-in worker coverage.
Model-provider and OTLP-backend claims require their own real execution evidence. Run `make check` before delivery.

## Child execution and metric observations

Server composition supplies `WorkerTracing.wrap` through the Runtime's optional `worker_entrypoint_wrapper` port.
The wrapper is built at launch, so only the current supervisor execution context crosses spawn. Child composition reads
its optional `worker_observability` context to construct instrumented models and runtime stages; it must not import
Server settings or configure a provider itself. Custom launchers remain responsible for their own bootstrap.

Processing observations are emitted after finalized reap, and only verified acknowledgement receives `success`.
Telemetry observers are synchronous, bounded, non-I/O callbacks and cannot change domain outcomes. Queue-loss telemetry
is emitted by the recorder that owns admission/write results. Spawn workers collect per-recorder loss deltas and return
one bounded summary after resource cleanup, including failure results. The supervisor reports it once per attempt;
missing reports remain unknown. Preserve received counters even if subsequent acknowledgement verification fails.
Queue gauges describe the parent recorder only; child loss counters are added without replacing that local snapshot.
Keep observation timestamps distinct from scrape time, and document cache, reset and incomplete-coverage semantics.

A fresh development environment installed with `uv sync` does not include `tracing-otlp`. In that environment,
`make test` skips OTLP worker tests and the tracing cases in worker model-usage tests. `make check` does not execute
these tests. The `Main` workflow's
`worker-observability` job installs the extra and requires all selected tests to pass without skips:

```bash
uv run --locked --extra tracing-otlp pytest tests/e2e/test_worker_otlp.py \
  tests/e2e/test_worker_model_usage.py tests/test_observability_health.py -q
```

These checks exercise real spawn, OTLP/HTTP, a local model fixture, Pydantic AI propagation and the metrics HTTP
surface. They prove local reception, not storage/query acceptance by an external backend.

The background CLI listener lifecycle requires a disposable OceanBase instance and
`POWERCONTEXT_TEST_MIGRATION_OCEANBASE_URL`. The `E2E harness` workflow's `atomic-memory-clean-oceanbase` job runs
`tests/e2e/test_background_metrics.py` through the clean migration acceptance runner, which rejects skipped tests.
The test initializes its fresh database before starting the real background CLI, then checks listener startup,
authorized metrics, absence of business routes and clean SIGTERM shutdown. The listener startup budget excludes
first-run schema and index creation. CI retains `background-metrics/background.log` with the migration reports;
startup failures include the child return code and a bounded log tail. Backend acceptance requires this step to pass.
Without the backend setting, default test runs skip that lifecycle test.
