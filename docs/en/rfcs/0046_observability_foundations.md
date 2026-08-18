- Proposal Name: `observability_foundations`
- Start Date: 2026-07-29
- RFC PR: [oceanbase/powercontext#46](https://github.com/oceanbase/powercontext/pull/46)
- Tracking Issue: [oceanbase/powercontext#39](https://github.com/oceanbase/powercontext/issues/39)
- Related RFCs: [RFC 0016](0016_pydantic_ai_inference_integration.md),
  [RFC 0019](0019_local_source_memory_runtime.md),
  [RFC 0020](0020_runtime_backed_memory_remote_access.md)

# Summary

This RFC defines the observability boundary for the PowerContext Server and built-in Runtime. PowerContext will
provide:

- operational logging;
- Prometheus-compatible metrics;
- OpenTelemetry tracing and context propagation.

These signals share a stable operation vocabulary and correlation model. `X-PowerContext-Request-ID` remains an always-available,
Server-owned support identifier derived from the inbound Server span ID. Trace recording and export are optional;
request context exists even when spans are not sampled.

The proposal establishes behavior and ownership, not a detailed implementation. Internal design and code changes
should first align the existing HTTP, MCP, Client, and background processing boundaries. Signal-specific features
should be implemented only after that alignment is validated.

# Motivation

PowerContext already exposes liveness, readiness, and request IDs, and it logs some failures. These pieces do not yet
provide a complete operational view.

Operators need to understand:

- whether the Server and Runtime are available;
- which operation failed for a reported request;
- operation rate, latency, concurrency, and failure trends;
- whether background Source processing is progressing;
- how work flows through HTTP, MCP, the Runtime, and remote dependencies.

The design also needs to prevent accidental data exposure and misleading measurements. In particular, MCP projects
PowerContext operations through an internal HTTP call. Telemetry must relate the external MCP request to the
application operation without presenting the internal bridge as another external request.

Without an agreed boundary, logging, metrics, and tracing could use different names, count different units, or expose
unbounded and sensitive values.

# Guide-level explanation

## Three signals

PowerContext treats logging, metrics, and tracing as complementary:

| Signal | Purpose |
| --- | --- |
| Logs | Diagnose individual failures and lifecycle changes |
| Metrics | Observe aggregate health, traffic, latency, and background progress |
| Traces | Follow one execution flow across transport, application, and dependency boundaries |

Logging and metrics are part of the ready-to-run Server. OpenTelemetry request context is always present, while trace
recording and export are optional.

## Correlation

PowerContext uses three identifiers:

- `request_id` identifies a request for support and diagnostics;
- `trace_id` identifies an execution flow;
- `span_id` identifies one operation within that flow.

The Server derives `request_id` from the inbound transport span ID. Callers propagate standard OpenTelemetry trace
context rather than a request ID. A non-recording OpenTelemetry context provides the same identifier when trace export
is disabled. For MCP, the logical protocol request span owns the request ID and the internal HTTP bridge reuses it.

Logs may contain all three identifiers. Metrics never use them as labels.

## Observable work

PowerContext distinguishes these units:

| Unit | Meaning |
| --- | --- |
| Transport request | One external HTTP or MCP protocol request |
| Application operation | One stable PowerContext operation, such as `search_memory` |
| Runtime stage | One bounded internal step within an application operation, such as `memory.search` |
| Background activation | One manual or scheduled Source processing activation |
| Dependency call | One outbound call to another service or provider |

Runtime stage spans use `stage` as their `powercontext.operation.unit` value. They expose internal latency without
creating another application operation.

A direct HTTP call produces one external request and one application operation. An MCP tool call also produces one
external request and one application operation. Its internal HTTP bridge does not count as a second external request.

Application operation identity is transport-neutral. HTTP and MCP use the same operation name for the same
PowerContext behavior.

## Data safety

Normal telemetry must not contain:

- Source or Memory content;
- search queries;
- prompts, model responses, or vectors;
- request or response bodies;
- credentials, authorization headers, or complete database URLs.

Metrics also exclude unbounded identities such as request IDs, trace IDs, `scope_id`, Source IDs, Memory IDs, and raw
paths.

Unexpected failures may include a traceback in Server logs. The structured fields around the traceback still follow
the same data policy.

## Diagnostic workflow

An operator can:

1. use liveness and readiness to determine whether the process should receive traffic;
2. use the request ID from a Client error to find the related log;
3. use metrics to determine whether the problem is isolated or widespread;
4. use a trace, when enabled, to follow the request and its dependency calls.

PowerContext does not require an external telemetry backend for normal local operation.

# Reference-level explanation

## Ownership

The ready-to-run Server owns observability configuration and lifecycle. Importing PowerContext as a library does not
configure global logging or start exporters.

The Server may observe transport and application behavior, but observability does not own domain decisions,
persistence, cursor movement, error mapping, or retry policy.

The built-in Runtime exposes the lifecycle and result of background processing without depending on a logging,
Prometheus, or OpenTelemetry implementation. Embedded Runtime users can run without Server observability.

## Shared operation vocabulary

Remote application operations use the stable operation IDs defined by the HTTP contract. MCP uses those same
identities for projected tools.

Infrastructure behavior, such as health probes and MCP protocol traffic, uses a small separate vocabulary. Raw paths,
Python function names, and implementation class names are not stable operation identities.

Signal-specific implementation may add attributes, but logging, metrics, and tracing must agree on the meaning of an
operation and its outcome.

## Logging boundary

Server logging covers:

- startup, readiness changes, and shutdown;
- request and application operation failures;
- useful operation completion events;
- background processing outcomes;
- observability configuration or export failures that require operator attention.

The Server supports human-readable and structured output and a configurable log level. Routine health and metrics
traffic should not dominate normal logs.

Operational logs are written to standard streams by default. The initial implementation does not provide file sinks, log
rotation, retention, or shipping. A service manager, container runtime, or log collector may provide those functions.

Logging is diagnostic rather than an audit trail. It does not promise durable storage, delivery, ordering across
processes, or retention.

## Metrics boundary

The initial metrics surface is Prometheus-compatible and covers:

- external request count, latency, failures, and concurrency;
- application operation count, latency, and outcomes;
- Runtime readiness;
- background Source processing count, latency, outcomes, and progress.

Metrics use bounded labels derived from declared operations and a small outcome vocabulary. They do not use
caller-controlled or content-derived labels.

The metrics endpoint is infrastructure, not part of the domain OpenAPI contract or MCP tool surface.

The initial proposal does not define custom application metrics, dashboards, alerts, or service-level objectives.

## OpenTelemetry boundary

OpenTelemetry provides tracing and context propagation. The initial integration covers:

- incoming HTTP and MCP requests;
- PowerContext application operations;
- scheduled background work;
- outbound PowerContext Client calls;
- the internal MCP bridge.

PowerContext uses W3C Trace Context and supports OTLP export. Vendor-specific tracing configuration is not part of the
initial design.

Trace recording and OTLP export are optional. When disabled, the Server uses a non-recording OpenTelemetry context so
request IDs, propagation, logging, metrics, and domain behavior continue to work without a telemetry backend.
The OpenTelemetry API and SDK therefore belong to the Server role, while the OTLP exporter is installed through the
`tracing-otlp` extra.

OTLP export of logs and metrics is outside the first implementation scope. It may be added later without changing the
signal semantics defined here.

## Adjacent capabilities

Audit records, Source or Memory data collection, inference input and output monitoring, and usage analytics are
separate product capabilities. They are not observability signals defined by this RFC.

The existing `powercontext doctor` command remains the starting point for installation diagnostics. A future
diagnostic bundle may extend it, but does not require operational logs to become durable records.

## HTTP, MCP, and background consistency

HTTP and MCP are entrypoints to the same application behavior. Their transport telemetry may differ, but application
operation telemetry must not.

The MCP implementation contains an internal HTTP bridge. That bridge remains visible when it helps explain a trace,
but it is not external traffic and must not inflate external request metrics or produce a misleading duplicate access
record.

Scheduled processing has no incoming request ID. It uses the same application outcome vocabulary and starts its own
trace when tracing is enabled.

## Failure isolation

Observability is not part of the authoritative operation result:

- a log formatting failure cannot change a response;
- a metrics collection failure cannot change Runtime state;
- an unavailable exporter cannot make the Server unready;
- cancellation continues to propagate;
- shutdown makes a bounded attempt to flush telemetry without preventing Runtime cleanup.

No-op Source processing is a successful outcome, not a failure.

## Compatibility

This RFC does not change Source, Artifact, Trigger, Memory, inference, persistence, or cursor semantics.

The `X-PowerContext-Request-ID` response header and Client error field remain compatible. This RFC refines RFC 0020
before release: request IDs are Server-owned span identifiers. A metrics endpoint and new configuration are additive
infrastructure surfaces and remain outside the domain OpenAPI contract.

Documented event names, metric names, labels, and tracing attributes become operational compatibility surfaces once
released. Additive fields are normally compatible. Renaming or removing them requires review because it can break
queries, dashboards, and alerts.

Internal observability hooks are not public extension APIs in the initial release.

## Acceptance criteria

- Logging, metrics, and tracing use the same application operation identity and outcome semantics.
- Direct HTTP and MCP calls to the same behavior produce one application operation.
- The internal MCP bridge is correlated but not counted as external traffic.
- Request IDs remain available when trace recording and export are disabled.
- Metrics have bounded cardinality.
- Telemetry excludes the prohibited data classes defined by this RFC.
- Background success, no-op, failure, and cancellation are distinguishable.
- Observability failures do not change domain behavior or readiness.
- OpenTelemetry propagation works across supported inbound and outbound boundaries when enabled.
- The default test suite requires no external telemetry service.
- English and Chinese user documentation remain in sync.

# Drawbacks

Observability adds dependencies, runtime overhead, configuration, and new compatibility surfaces.

Separating transport requests from application operations introduces more concepts than a single access log. The
distinction is necessary to represent MCP correctly.

Using Prometheus for initial metrics and OpenTelemetry for tracing creates two signal integrations. It avoids coupling
the first metrics contract to an OpenTelemetry metrics exporter, but requires consistent naming across both.

# Rationale and alternatives

## Instrument the current code directly

This would deliver visible signals sooner, but it risks encoding current internal HTTP and middleware behavior as the
public telemetry model. The proposal aligns semantic boundaries first.

## Use OpenTelemetry for every signal immediately

A single SDK could eventually simplify export, but it would couple initial logging and metrics to OpenTelemetry SDK
choices before PowerContext has validated its signal contract. The proposal starts with Python logging, Prometheus
metrics, and OpenTelemetry tracing.

## Provide only logs and metrics

This covers local diagnostics and aggregate behavior but cannot propagate execution context across MCP, Client, and
remote Server boundaries. Tracing remains optional but is part of the common design.

## Use raw paths and scope identifiers

These values are easy to collect but are unstable, potentially sensitive, and often unbounded. The proposal uses
declared operation identity and bounded outcomes.

# Prior art

BentoML separates Python logging, Prometheus request metrics, and OpenTelemetry tracing. It correlates logs with trace
context, records request rate and latency, and propagates context through Server and Client boundaries.

BentoML also writes model monitoring data to rotating files. That facility records inference data and is separate
from its stream-oriented operational logging. PowerContext adopts this signal separation and the same
Server-span-derived request ID model. It uses operation IDs instead of raw paths and does not adopt BentoML's
multiprocess metrics, model data collection, or usage analytics.

RFC 0016 defines privacy rules for inference telemetry. RFC 0019 defines background Runtime processing. RFC 0020
defines request IDs, operation IDs, HTTP error behavior, MCP projection, and Server lifecycle. This proposal aligns
observability with those contracts while refining request ID ownership before release.

# Unresolved questions

- Should Prometheus metrics be enabled by default for every ready-to-run Server profile?
- Which application and background measurements are required for the first public preview?
- Should explicit inference spans be part of the first tracing release?
- Which telemetry names should be treated as stable from the first release?

These questions affect feature scope and must be resolved before the corresponding implementation begins. Internal
mechanics that do not change the boundaries in this RFC can be decided during implementation review.

# Future possibilities

Later work may add:

- OTLP metrics and logs;
- a managed file sink with rotation and retention, if PowerContext owns a background service profile;
- a redacted diagnostic bundle built on `powercontext doctor`;
- dashboards, alerts, and service-level objectives;
- inference usage and dependency metrics;
- database and provider spans;
- trace exemplars in Prometheus metrics;
- a supported custom instrumentation API;
- deployment examples for OpenTelemetry Collector.

These additions must preserve the shared operation vocabulary, correlation model, data policy, and failure isolation
defined by this RFC.
