# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import asyncio
import sys

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.client import PowerContextClient
from powercontext.server.factory import create_server_app
from powercontext.server.settings import (
    McpConfig,
    MetricsConfig,
    ServerLoggingConfig,
    ServerSettings,
    TracingConfig,
)
from powercontext.server.tracing import ServerTracing, configure_server_tracing

TRACE_ID = int("4bf92f3577b34da6a3ce929d0e0e4736", 16)
PARENT_SPAN_ID = int("00f067aa0ba902b7", 16)
TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def _tracing(*, instrumented: bool = False) -> tuple[ServerTracing, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return ServerTracing(provider, instrumented=instrumented), exporter


def test_http_and_application_spans_preserve_incoming_context() -> None:
    tracing, exporter = _tracing()
    app = create_server_app(
        settings=ServerSettings(
            mcp=McpConfig(enabled=False),
            metrics=MetricsConfig(enabled=False),
            logging=ServerLoggingConfig(access=False),
        ),
        tracing=tracing,
    )

    with TestClient(app) as client:
        response = client.get(
            "/v1/capabilities",
            headers={"traceparent": TRACEPARENT},
        )

    spans = {span.name: span for span in exporter.get_finished_spans()}
    transport = spans["HTTP get_capabilities"]
    application = spans["powercontext get_capabilities"]
    assert response.status_code == 200
    assert transport.context.trace_id == TRACE_ID
    assert transport.parent is not None
    assert transport.parent.span_id == PARENT_SPAN_ID
    assert transport.attributes is not None
    request_id = format(transport.context.span_id, "016x")
    assert response.headers["X-PowerContext-Request-ID"] == request_id
    assert transport.attributes["powercontext.request.id"] == request_id
    assert application.parent is not None
    assert application.parent.span_id == transport.context.span_id


def test_client_span_injects_w3c_trace_context(monkeypatch) -> None:
    tracing, exporter = _tracing()
    monkeypatch.setattr(
        "powercontext.client.tracing.trace.get_tracer",
        lambda _name: tracing.provider.get_tracer("powercontext.client"),
    )
    request_headers: httpx.Headers | None = None

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal request_headers
        request_headers = request.headers
        return httpx.Response(
            200,
            json={
                "source_types": [],
                "artifact_families": [],
                "memory_extraction": False,
                "experience_generation": False,
                "managed_skill_generation": False,
                "external_skill_registry": False,
                "handoff_generation": False,
                "search_modes": [],
                "context_versions": [],
            },
        )

    async def request_capabilities() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
            client = PowerContextClient("http://testserver", http_client=http_client, trust_transport_security=True)
            await client.get_capabilities()

    asyncio.run(request_capabilities())

    spans = exporter.get_finished_spans()
    assert request_headers is not None
    assert request_headers["traceparent"]
    assert [span.name for span in spans] == ["PowerContextClient get_capabilities"]
    assert spans[0].attributes is not None
    assert spans[0].attributes["powercontext.operation.outcome"] == "success"


def test_tracing_context_is_not_recorded_by_default() -> None:
    tracing = configure_server_tracing(TracingConfig())
    span = tracing.provider.get_tracer("test").start_span("test")

    assert span.get_span_context().is_valid
    assert span.is_recording() is False

    span.end()
    tracing.shutdown()


def test_inference_instrumentation_follows_the_tracing_setting() -> None:
    disabled = configure_server_tracing(TracingConfig())
    enabled, _ = _tracing(instrumented=True)

    assert disabled.instrumentation is None
    assert enabled.instrumentation is not None

    disabled.shutdown()


def test_inference_instrumentation_records_no_content() -> None:
    tracing, _ = _tracing(instrumented=True)
    instrumentation = tracing.instrumentation

    assert instrumentation is not None
    assert instrumentation.include_content is False
    assert instrumentation.include_binary_content is False
    assert instrumentation.include_model_request_parameters is False


def test_runtime_stage_records_attributes_and_inherits_current_span() -> None:
    tracing, exporter = _tracing()
    parent = tracing.start_span(
        "powercontext search_memory",
        kind=SpanKind.INTERNAL,
        attributes={},
    )

    with tracing.stage(
        "memory.search",
        attributes={"powercontext.memory.search.limit": 10},
    ) as stage:
        stage.set_attributes({"powercontext.memory.search.result_count": 2})
    parent.finish("success")

    spans = {span.name: span for span in exporter.get_finished_spans()}
    application_span = spans["powercontext search_memory"]
    stage_span = spans["memory.search"]
    assert stage_span.parent is not None
    assert stage_span.parent.span_id == application_span.context.span_id
    assert stage_span.kind is SpanKind.INTERNAL
    assert stage_span.attributes is not None
    assert stage_span.attributes["powercontext.operation.name"] == "memory.search"
    assert stage_span.attributes["powercontext.operation.unit"] == "stage"
    assert stage_span.attributes["powercontext.operation.outcome"] == "success"
    assert stage_span.attributes["powercontext.memory.search.limit"] == 10
    assert stage_span.attributes["powercontext.memory.search.result_count"] == 2


def test_runtime_stage_records_failure_and_reraises_same_error() -> None:
    tracing, exporter = _tracing()
    error = ValueError("sensitive query")

    with pytest.raises(ValueError) as raised, tracing.stage("memory.search", attributes={}):
        raise error

    assert raised.value is error
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes is not None
    assert spans[0].attributes["powercontext.operation.outcome"] == "failure"
    assert spans[0].attributes["error.type"] == "ValueError"
    assert "sensitive query" not in str(spans[0].attributes)
    assert spans[0].status.status_code is StatusCode.ERROR


def test_runtime_stage_records_cancellation_and_reraises_same_error() -> None:
    tracing, exporter = _tracing()
    error = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError) as raised, tracing.stage("memory.search", attributes={}):
        raise error

    assert raised.value is error
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes is not None
    assert spans[0].attributes["powercontext.operation.outcome"] == "cancelled"
    assert spans[0].attributes["error.type"] == "CancelledError"
    assert spans[0].status.status_code is StatusCode.UNSET


def test_runtime_stage_isolates_tracer_failure(monkeypatch) -> None:
    tracing, exporter = _tracing()

    class BrokenTracer:
        def start_span(self, *_args: object, **_kwargs: object) -> None:
            raise RuntimeError

    monkeypatch.setattr(tracing, "tracer", BrokenTracer())

    with tracing.stage("memory.search", attributes={}) as stage:
        stage.set_attributes({"powercontext.memory.search.result_count": 0})

    assert exporter.get_finished_spans() == ()


def test_readiness_ignores_tracing_setup_failure(monkeypatch, tmp_path) -> None:
    tracing, _ = _tracing(instrumented=True)
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            mcp=McpConfig(enabled=False),
            metrics=MetricsConfig(enabled=False),
            logging=ServerLoggingConfig(access=False),
        ),
        scheduler_path=tmp_path / "scheduler.db",
        tracing=tracing,
    )

    def fail_attach(_context: object) -> None:
        raise RuntimeError

    monkeypatch.setattr("powercontext.server.tracing.otel_context.attach", fail_attach)

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_tracing_export_requires_the_otlp_extra(monkeypatch) -> None:
    module = "opentelemetry.exporter.otlp.proto.http.trace_exporter"
    monkeypatch.setitem(sys.modules, module, None)

    with pytest.raises(RuntimeError, match="powercontext\\[server,tracing-otlp\\]"):
        configure_server_tracing(TracingConfig(enabled=True))
