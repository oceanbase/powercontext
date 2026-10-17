# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingBlock,
    ArtifactProcessingBlockedError,
    ArtifactProcessingWorkAssignment,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings, TracingConfig
from powercontext.server.telemetry import service_resource
from powercontext.server.tracing import ServerTracing

OTLPSpanExporter = pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter").OTLPSpanExporter
ExportTraceServiceRequest = pytest.importorskip(
    "opentelemetry.proto.collector.trace.v1.trace_service_pb2"
).ExportTraceServiceRequest


@contextmanager
def _receiver(timeout=False):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandler signature.
            return

        def do_POST(self):
            payload = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/v1/traces":
                request = ExportTraceServiceRequest()
                request.ParseFromString(payload)
                received.append(request)
                self.send_response(200)
                self.end_headers()
            else:
                if timeout:
                    time.sleep(0.5)
                    self.send_response(503)
                    self.end_headers()
                else:
                    response = json.dumps({
                        "id": "fixture",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "fixture",
                        "choices": [
                            {
                                "index": 0,
                                "finish_reason": "stop",
                                "message": {
                                    "role": "assistant",
                                    "content": json.dumps(
                                        {"probes": []} if b"probes" in payload else {"candidates": []}
                                    ),
                                },
                            }
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                    }).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(response)))
                    self.end_headers()
                    self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _spans(received):
    return [
        (span, {attribute.key: attribute.value.string_value for attribute in resource.resource.attributes})
        for request in list(received)
        for resource in request.resource_spans
        for scope in resource.scope_spans
        for span in scope.spans
    ]


def _blocked_worker(_assignment: ArtifactProcessingWorkAssignment):
    raise ArtifactProcessingBlockedError(
        ArtifactProcessingBlock("frontier", "topic_memory", "window_attempt_limit", (("attempts", 3),))
    )


def test_spawned_worker_policy_block_preserves_child_and_supervisor_trace_categories(tmp_path, monkeypatch):
    import asyncio

    from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
    from powercontext.builtin.persistence.sqlite import SQLiteProfile
    from powercontext.builtin.persistence.tables import SHARED_TABLES
    from powercontext.builtin.runtime.artifact_processing import (
        ArtifactProcessingBinding,
        ArtifactProcessingSupervisor,
        SpawnArtifactProcessingWorkerLauncher,
    )
    from powercontext.server.worker_tracing import WorkerTracing

    with _receiver() as (endpoint, received):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", endpoint + "/v1/traces")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", "0.2")
        provider = TracerProvider(resource=service_resource("all"), shutdown_on_exit=False)
        provider.add_span_processor(
            SimpleSpanProcessor(OTLPSpanExporter(endpoint=endpoint + "/v1/traces", timeout=0.2))
        )
        tracing = ServerTracing(provider)
        binding_name = "topic-memory-source-window"
        scope_id = "blocked"

        async def run():
            config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'blocked.db'}")
            async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
                intents = ArtifactProcessingIntentRepository()
                async with profile.database.transaction() as connection:
                    await intents.request(connection, scope_id, binding_name)
                binding = ArtifactProcessingBinding(
                    binding_name,
                    "topic-memory",
                    SpawnArtifactProcessingWorkerLauncher(
                        _blocked_worker, entrypoint_wrapper=WorkerTracing(TracingConfig(enabled=True)).wrap
                    ),
                )
                async with ArtifactProcessingSupervisor(
                    database=profile.database, bindings=(binding,), lease_mode="single-process", tracing=tracing
                ):
                    async with asyncio.timeout(20):
                        while not any(  # noqa: ASYNC110 - bounded wait for actual OTLP reception.
                            span.name == "artifact_processing.worker" for span, _ in _spans(received)
                        ):
                            await asyncio.sleep(0.01)
                async with profile.database.transaction() as connection:
                    intent = await intents.load(connection, scope_id, binding_name)
                    assert intent is not None and intent.requested_generation > intent.handled_generation

        try:
            asyncio.run(run())
            spans = [
                span
                for span, _ in _spans(received)
                if span.name
                in {
                    "artifact_processing.worker",
                    "artifact_processing.worker.wait",
                    "artifact_processing.worker.execute",
                }
            ]
            assert {span.name for span in spans} == {
                "artifact_processing.worker",
                "artifact_processing.worker.wait",
                "artifact_processing.worker.execute",
            }
            assert len({span.trace_id for span in spans}) == 1
            for span in spans:
                attributes = {attribute.key: attribute.value.string_value for attribute in span.attributes}
                assert attributes["powercontext.operation.outcome"] == "blocked"
                assert attributes["powercontext.error.category"] == "blocked"
                assert span.status.code == 0  # OTel UNSET: policy blocks are not execution errors.
        finally:
            provider.shutdown()


@pytest.mark.parametrize("family", ["memory", "topic-memory"])
@pytest.mark.parametrize("timeout", [False, True])
def test_builtin_spawned_worker_exports_model_and_execution_spans_to_otlp(
    tmp_path, monkeypatch, timeout, caplog, family
):
    private = "private OTLP Source sentinel: the user prefers concise answers and Python examples"
    with _receiver(timeout) as (endpoint, received):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", endpoint + "/v1/traces")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", "0.2")
        monkeypatch.setenv("OPENAI_API_KEY", "local-fixture-key")
        provider = TracerProvider(resource=service_resource("all"), shutdown_on_exit=False)
        provider.add_span_processor(
            SimpleSpanProcessor(OTLPSpanExporter(endpoint=endpoint + "/v1/traces", timeout=0.2))
        )
        app = create_server_app(
            settings=ServerSettings(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'otlp.db'}"),
                inference=InferenceConfig(
                    generation_model="openai-chat:fixture",
                    generation_base_url=endpoint + "/model",
                    generation_timeout_seconds=0.1 if timeout else 30,
                ),
                runtime=RuntimeConfig.model_validate({
                    "artifact_processing_families": (family,),
                    family.replace("-", "_") + "_schedule_seconds": 0.05,
                }),
                tracing=TracingConfig(enabled=True),
                mcp=McpConfig(enabled=False),
            ),
            tracing=ServerTracing(provider, instrumented=True),
        )
        try:
            with TestClient(app) as client:
                scope = client.get("/v1/scopes/default").json()["scope_id"]
                assert (
                    client.post(
                        "/v1/sources/content",
                        json={
                            "scope_id": scope,
                            "source_id": "otlp-fixture",
                            "content": private,
                        },
                    ).status_code
                    == 202
                )
                spans = []
                executions = []
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    spans = _spans(received)
                    executions = [
                        (span, identity)
                        for span, identity in spans
                        if span.name == "artifact_processing.worker.execute"
                    ]
                    if executions and any(span.name == "artifact_processing.worker" for span, _ in spans):
                        break
                    time.sleep(0.05)
                assert executions, "Actual built-in child must export execution spans over OTLP/HTTP"
                execution, identity = executions[0]
                assert identity["powercontext.service.role"] == "worker"
                parents = [span for span, _ in spans if span.span_id == execution.parent_span_id]
                assert parents and parents[0].trace_id == execution.trace_id
                child_spans = [span for span, role in spans if role.get("powercontext.service.role") == "worker"]
                model_spans = [span for span in child_spans if span.name.startswith("chat ")]
                assert model_spans
                assert all(not span.events and not span.status.message for span in model_spans)
                outcomes = {attribute.key: attribute.value.string_value for attribute in execution.attributes}
                if timeout:
                    assert outcomes["powercontext.operation.outcome"] == "failure"
                    assert outcomes["powercontext.error.category"] == "timeout"
                else:
                    assert outcomes["powercontext.operation.outcome"] == "success", [
                        getattr(record, "traceback", "") for record in caplog.records
                    ]
                    assert any(
                        span.name == ("memory.commit" if family == "memory" else "topic_memory.commit")
                        for span in child_spans
                    )
                assert private not in str(received)
                assert "local-fixture-key" not in str(received)
                assert "powercontext.request.id" not in outcomes
        finally:
            provider.shutdown()


def _run_pydantic_ai_host(base_url, scope_id, endpoint):
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from powercontext_pydantic_ai import PowerContext, PowerContextSettings
    from pydantic_ai import Agent
    from pydantic_ai.capabilities import Instrumentation
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.models.instrumented import InstrumentationSettings

    provider = TracerProvider(resource=Resource({"service.name": "pydantic-ai-fixture"}), shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(endpoint=endpoint + "/v1/traces", timeout=1)))
    trace.set_tracer_provider(provider)
    try:
        agent = Agent(
            FunctionModel(lambda _messages, _info: ModelResponse(parts=[TextPart("done")])),
            capabilities=[
                Instrumentation(settings=InstrumentationSettings(tracer_provider=provider, include_content=False)),
                PowerContext(settings=PowerContextSettings(base_url=base_url, scope_id=scope_id)),
            ],
        )
        assert agent.run_sync("host trace fixture").output == "done"
    finally:
        provider.shutdown()


@contextmanager
def _api_server(app):
    import socket

    import uvicorn

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_config=None))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started
            yield f"http://127.0.0.1:{listener.getsockname()[1]}"
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive()


def test_pydantic_ai_host_propagates_context_over_real_http(tmp_path):
    import multiprocessing

    import httpx

    from tests.test_server_metrics import _settings

    with _receiver() as (endpoint, received):
        provider = TracerProvider(resource=service_resource("api"), shutdown_on_exit=False)
        provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(endpoint=endpoint + "/v1/traces", timeout=1)))
        app = create_server_app(settings=_settings(tmp_path / "host-trace.db"), tracing=ServerTracing(provider))
        try:
            with _api_server(app) as base_url:
                scope = httpx.get(base_url + "/v1/scopes/default").json()["scope_id"]
                host = multiprocessing.get_context("spawn").Process(
                    target=_run_pydantic_ai_host,
                    args=(base_url, scope, endpoint),
                )
                host.start()
                try:
                    host.join(timeout=20)
                    assert host.exitcode == 0
                finally:
                    if host.is_alive():
                        host.kill()
                    host.join(timeout=5)
                spans = _spans(received)
                sdk = [span for span, _ in spans if span.name == "PowerContextClient prepare_context"]
                ingress = [span for span, _ in spans if span.name == "HTTP prepare_context"]
                assert sdk and ingress
                assert any(
                    server.parent_span_id == client.span_id and server.trace_id == client.trace_id
                    for server in ingress
                    for client in sdk
                )
        finally:
            provider.shutdown()


def test_exporter_outage_does_not_prevent_builtin_worker_acknowledgement(tmp_path, monkeypatch):
    with _receiver() as (endpoint, _received):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://127.0.0.1:1/v1/traces")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", "0.1")
        monkeypatch.setenv("OPENAI_API_KEY", "local-fixture-key")
        app = create_server_app(
            settings=ServerSettings(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'outage.db'}"),
                inference=InferenceConfig(
                    generation_model="openai-chat:fixture", generation_base_url=endpoint + "/model"
                ),
                runtime=RuntimeConfig(artifact_processing_families=("memory",), memory_schedule_seconds=0.05),
                tracing=TracingConfig(enabled=True),
                mcp=McpConfig(enabled=False),
            )
        )
        with TestClient(app) as client:
            scope = client.get("/v1/scopes/default").json()["scope_id"]
            assert (
                client.post(
                    "/v1/sources/content",
                    json={
                        "scope_id": scope,
                        "source_id": "outage-fixture",
                        "content": "User prefers short answers.",
                    },
                ).status_code
                == 202
            )
            text = ""
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                text = client.get("/metrics").text
                if 'powercontext_server_artifact_processing_completed_total{family="memory"} 1.0' in text:
                    break
                time.sleep(0.05)
            assert 'powercontext_server_artifact_processing_completed_total{family="memory"} 1.0' in text
