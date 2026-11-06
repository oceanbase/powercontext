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
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.protocols import RuntimeTraceContext
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


def test_http_and_application_spans_preserve_incoming_context(tmp_path) -> None:
    tracing, exporter = _tracing()
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'tracing.db'}"),
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


def test_inference_spans_sanitize_provider_exception_content() -> None:
    from pydantic_ai import ModelRequest, UserPromptPart
    from pydantic_ai.exceptions import ModelHTTPError
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.models.instrumented import InstrumentedModel

    private = "private provider response sentinel"
    tracing, exporter = _tracing(instrumented=True)
    instrumentation = tracing.instrumentation
    assert instrumentation is not None

    async def fail(_messages, _info):
        raise ModelHTTPError(400, "fixture-model", {"error": {"message": private}})

    async def scenario() -> None:
        model = InstrumentedModel(FunctionModel(fail), instrumentation)
        with pytest.raises(ModelHTTPError):
            await model.request(
                [ModelRequest(parts=[UserPromptPart("bounded prompt")])],
                None,
                ModelRequestParameters(),
            )

    asyncio.run(scenario())
    span = next(span for span in exporter.get_finished_spans() if span.name.startswith("chat "))
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description in (None, "")
    assert span.events == ()
    attributes = span.attributes or {}
    assert attributes["error.type"] == "ModelHTTPError"
    rendered = repr(dict(attributes)) + repr(span.events) + str(span.status.description)
    assert private not in rendered
    tracing.shutdown()


def test_worker_execution_span_preserves_inference_failure_category(monkeypatch) -> None:
    from powercontext.builtin.inference import InvalidInferenceOutputError
    from powercontext.builtin.persistence.supervision import ArtifactProcessingFence
    from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingWorkAssignment
    from powercontext.server.worker_tracing import WorkerTracing

    private = "private inference output sentinel"
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracing = ServerTracing(provider)
    monkeypatch.setattr(
        "powercontext.server.worker_tracing.configure_server_tracing", lambda *_args, **_kwargs: tracing
    )

    def fail(_assignment: ArtifactProcessingWorkAssignment):
        raise InvalidInferenceOutputError("memory-extract", private)

    assignment = ArtifactProcessingWorkAssignment(
        binding_name="memory-source-window",
        scope_id="invalid-output",
        artifact_family="memory",
        claimed_request_generation=1,
        fence=ArtifactProcessingFence(
            supervisor_group="global", holder_id="owner", supervisor_generation=1, lease_mode="single-process"
        ),
        worker_id="worker-attempt",
    )
    with pytest.raises(InvalidInferenceOutputError):
        WorkerTracing(TracingConfig(enabled=True)).wrap(fail)(assignment)

    span = next(span for span in exporter.get_finished_spans() if span.name == "artifact_processing.worker.execute")
    assert (span.attributes or {})["powercontext.error.category"] == "dependency"
    assert span.status.status_code is StatusCode.ERROR
    rendered = repr(span.attributes) + repr(span.events) + str(span.status.description)
    assert private not in rendered


def test_topic_memory_dream_worker_records_instrumented_model_span(tmp_path, monkeypatch) -> None:
    import json
    from functools import partial

    from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.models.instrumented import InstrumentedModel
    from pydantic_ai.usage import RequestUsage

    from powercontext.builtin.artifacts.topic_memory import TopicMemoryContent
    from powercontext.builtin.runtime import (
        BuiltinConfig,
        CaptureSource,
        CreateDreamRunRequest,
        GetDreamRunRequest,
        InferenceConfig,
        composition,
    )
    from powercontext.builtin.runtime.topic_memory_processing import (
        TopicMemoryWorkerSpec,
        _run_topic_memory_worker,
    )
    from powercontext.builtin.runtime.worker_observability import WorkerObservability, worker_observability
    from powercontext.builtin.scope import ScopeDraft
    from tests.e2e.dream_support import Controller, Handle, open_dream_runtime, process_pending
    from tests.e2e.test_dream_usage import seed_target

    tracing, exporter = _tracing(instrumented=True)

    def respond(messages, _info):
        value = next(
            json.loads(part.content)
            for message in reversed(messages)
            for part in message.parts
            if isinstance(part, UserPromptPart) and isinstance(part.content, str)
        )
        evidence_id = next(item["evidence_id"] for item in value["evidence"]["evidence"] if item["kind"] == "source")
        return ModelResponse(
            parts=[
                TextPart(
                    json.dumps({
                        "outcome": "proposed",
                        "reason": "The dream corrects the pending topic.",
                        "intent": "correct",
                        "proposal": TopicMemoryContent(
                            title="Replacement", summary="Issued", detail="# Progress\nIssued."
                        ).model_dump(mode="json"),
                        "evidence_ids": [evidence_id],
                    })
                )
            ],
            usage=RequestUsage(input_tokens=80, output_tokens=45),
        )

    async def open_model(*_args, **kwargs):
        model = InstrumentedModel(FunctionModel(respond), kwargs["instrumentation"])
        return model, model

    monkeypatch.setattr(composition, "_open_pydantic_ai_model", open_model)

    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'topic-dream-trace.db'}"),
        inference=InferenceConfig(generation_model="openai-chat:controlled-topic-dream"),
    )

    class WorkerHandle(Handle):
        async def execute(self):
            await self.ready.wait()
            worker = partial(_run_topic_memory_worker, TopicMemoryWorkerSpec(config=config))
            # Mirror the spawn entrypoint wrapper: only execution context crosses it.
            token = worker_observability.set(WorkerObservability(tracing, tracing.instrumentation))
            try:
                return await worker(self.assignment)
            finally:
                worker_observability.reset(token)

    class WorkerController(Controller):
        async def start(self, assignment):
            handle = WorkerHandle(self, assignment)
            self.handles.append(handle)
            return handle

    async def scenario():
        async with open_dream_runtime(config, controller=WorkerController()) as runtime:
            scope = await runtime.scopes.create(
                ScopeDraft(title="Topic dream", summary="Trace", idempotency_key="dream-trace")
            )
            source = await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(
                    source_id="progress",
                    content="private topic dream Source sentinel: the user prefers terse updates",
                    metadata={},
                )
            )
            target, _proposal = await seed_target(runtime, scope.scope_id, source.source_ref, "topic-memory")
            accepted = await runtime.dream.for_scope(scope.scope_id).create(
                CreateDreamRunRequest(
                    operation="revise_topic_memory",
                    target=target,
                    artifacts=(target,),
                    sources=(source.source_ref,),
                    idempotency_key="dream-trace",
                )
            )
            await process_pending(runtime)
            completed = await runtime.dream.for_scope(scope.scope_id).get(GetDreamRunRequest(run_id=accepted.run_id))
            assert (completed.status, completed.outcome) == ("succeeded", "proposed"), completed

    asyncio.run(scenario())

    spans = exporter.get_finished_spans()
    child = [span for span in spans if span.name.startswith("chat ")]
    assert child, [span.name for span in spans]
    for span in child:
        assert span.events == ()
        assert span.status.description in (None, "")
        rendered = repr(span.attributes) + repr(span.events) + str(span.status.description)
        assert "private topic dream Source sentinel" not in rendered
    tracing.shutdown()


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


def test_runtime_stage_records_explicit_noop_and_failure_outcomes() -> None:
    tracing, exporter = _tracing()

    with tracing.stage("memory.flush", attributes={}) as noop:
        noop.set_outcome("noop")
    with tracing.stage("memory.flush", attributes={}) as failed:
        failed.set_outcome("failure")

    spans = [span for span in exporter.get_finished_spans() if span.name == "memory.flush"]
    assert len(spans) == 2
    outcomes = {span.attributes["powercontext.operation.outcome"] for span in spans if span.attributes is not None}
    assert outcomes == {"noop", "failure"}
    failed_span = next(
        span
        for span in spans
        if span.attributes is not None and span.attributes["powercontext.operation.outcome"] == "failure"
    )
    assert failed_span.status.status_code is StatusCode.ERROR


@pytest.mark.parametrize("unit", ["stage", "background"])
@pytest.mark.parametrize("outcome", ["success", "noop"])
def test_runtime_exception_overrides_a_previously_successful_outcome(unit, outcome) -> None:
    tracing, exporter = _tracing()
    manager = (
        tracing.stage("test.operation", attributes={})
        if unit == "stage"
        else tracing.background("test.operation", operation="test.operation", attributes={})
    )
    try:
        with pytest.raises(RuntimeError), manager as span:
            span.set_outcome(outcome)
            raise RuntimeError
        exported = exporter.get_finished_spans()
        assert len(exported) == 1
        assert (exported[0].attributes or {})["powercontext.operation.outcome"] == "failure"
        assert (exported[0].attributes or {})["powercontext.error.category"] == "internal"
        assert exported[0].status.status_code is StatusCode.ERROR
    finally:
        tracing.shutdown()


def test_background_stage_starts_a_fresh_trace_outside_an_ambient_span() -> None:
    tracing, exporter = _tracing()
    ambient = tracing.start_span("HTTP flush_memory", kind=SpanKind.SERVER, attributes={})

    with tracing.background(
        "scheduled.process_source_window",
        operation="process_source_window",
        attributes={},
    ) as stage:
        stage.set_outcome("noop")
        stage.set_attributes({"powercontext.background.source_count": 0})
    ambient.finish("success")

    spans = {span.name: span for span in exporter.get_finished_spans()}
    ambient_span = spans["HTTP flush_memory"]
    root = spans["scheduled.process_source_window"]
    assert root.parent is None
    assert root.context.trace_id != ambient_span.context.trace_id
    assert root.kind is SpanKind.INTERNAL
    attributes = root.attributes or {}
    assert attributes["powercontext.operation.name"] == "process_source_window"
    assert attributes["powercontext.operation.unit"] == "background"
    assert attributes["powercontext.operation.outcome"] == "noop"
    assert attributes["powercontext.background.source_count"] == 0


def test_background_stage_links_a_recovered_attempt_without_parenting_it() -> None:
    tracing, exporter = _tracing()

    with tracing.background("work.execute", operation="work.execute", attributes={}) as first:
        context = first.trace_context
    assert context is not None
    with tracing.background(
        "work.execute",
        operation="work.execute",
        attributes={},
        links=(RuntimeTraceContext(trace_id=context.trace_id, span_id=context.span_id),),
    ):
        pass

    first_span, recovered_span = exporter.get_finished_spans()
    assert recovered_span.parent is None
    assert len(recovered_span.links) == 1
    assert recovered_span.links[0].context.trace_id == first_span.context.trace_id
    assert recovered_span.links[0].context.span_id == first_span.context.span_id


def test_background_isolates_child_spans_when_root_start_fails(monkeypatch) -> None:
    tracing, exporter = _tracing()
    ambient = tracing.start_span("HTTP flush_memory", kind=SpanKind.SERVER, attributes={})
    original = tracing.tracer.start_span

    def fail_scheduled_root(name: str, **kwargs: Any) -> Any:
        if name == "scheduled.process_source_window":
            raise RuntimeError
        return original(name, **kwargs)

    monkeypatch.setattr(tracing.tracer, "start_span", fail_scheduled_root)

    with (
        tracing.background(
            "scheduled.process_source_window",
            operation="process_source_window",
            attributes={},
        ),
        tracing.stage("memory.flush", attributes={}) as stage,
    ):
        stage.set_outcome("noop")
    ambient.finish("success")

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert "scheduled.process_source_window" not in spans
    ambient_span = spans["HTTP flush_memory"]
    flush = spans["memory.flush"]
    assert flush.parent is None
    assert flush.context.trace_id != ambient_span.context.trace_id


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
