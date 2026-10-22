"""Real native workers return provider observations on success, failure and crash."""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig
from powercontext.builtin.runtime.processing_contracts import WorkerEntrypoint
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings, TracingConfig
from powercontext.server.tracing import configure_server_tracing
from powercontext.server.worker_tracing import WorkerTracing


@dataclass(frozen=True)
class _Crash:
    entrypoint: WorkerEntrypoint

    def __call__(self, assignment):
        os._exit(1)


@contextmanager
def _provider(statuses):
    observed = []
    traces = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - stdlib signature.
            pass

        def do_POST(self):
            payload = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/v1/traces":
                traces.append(payload)
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if b"Reply with one token" in payload:
                status = 200
            else:
                status = statuses[min(len(observed), len(statuses) - 1)]
                observed.append(status)
            result = (
                {"error": {"message": "private-provider-response"}}
                if status >= 400
                else {
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
                                "content": json.dumps({"probes": []} if b"probes" in payload else {"candidates": []}),
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            )
            encoded = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Retry-After", "0.01")
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", observed, traces
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _count(text, suffix, **labels):
    return sum(
        sample.value
        for family in text_string_to_metric_families(text)
        for sample in family.samples
        if sample.name == "powercontext_server_provider_" + suffix
        and all(sample.labels.get(key) == value for key, value in labels.items())
    )


@pytest.mark.parametrize(
    ("family", "failure", "tracing"),
    [
        ("memory", "retry", False),
        ("topic-memory", "none", False),
        ("memory", "denied", False),
        ("memory", "crash", False),
        ("memory", "retry", True),
        ("topic-memory", "none", True),
    ],
)
def test_native_worker_provider_reports_are_counted_once(tmp_path, monkeypatch, family, failure, tracing):
    monkeypatch.setenv("OPENAI_API_KEY", "local-provider-fixture")
    if failure == "crash":
        original = WorkerTracing.wrap
        monkeypatch.setattr(WorkerTracing, "wrap", lambda self, entrypoint: _Crash(original(self, entrypoint)))
    statuses = (429, 429, 200) if failure == "retry" else (403,) if failure == "denied" else (200,)
    if tracing:
        pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    with _provider(statuses) as (endpoint, observed, traces), ExitStack() as cleanup:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", endpoint + "/traces")
        configured_tracing = configure_server_tracing(TracingConfig(enabled=True), role="all") if tracing else None
        if configured_tracing is not None:
            cleanup.callback(configured_tracing.shutdown)
        app = create_server_app(
            settings=ServerSettings(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}"),
                inference=InferenceConfig(generation_model="openai-chat:fixture", generation_base_url=endpoint),
                runtime=RuntimeConfig.model_validate({
                    "artifact_processing_families": (family,),
                    family.replace("-", "_") + "_schedule_seconds": 0.05,
                }),
                mcp=McpConfig(enabled=False),
                tracing=TracingConfig(enabled=tracing),
            ),
            tracing=configured_tracing,
        )
        with TestClient(app) as client:
            scope = client.get("/v1/scopes/default").json()["scope_id"]
            assert (
                client.post(
                    "/v1/sources/content",
                    json={
                        "scope_id": scope,
                        "source_id": "fixture",
                        "content": "private-worker-source",
                    },
                ).status_code
                == 202
            )
            outcome = "missing" if failure == "crash" else "received"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                metrics = client.get("/metrics").text
                if _count(metrics, "worker_reports_total", outcome=outcome) >= 1:
                    break
                time.sleep(0.05)
            else:
                pytest.fail("Provider worker report did not arrive within 30 seconds")
            assert _count(metrics, "worker_reports_total", outcome=outcome) == 1
            assert _count(metrics, "http_attempts_total", execution="parent") == 0
            assert scope not in metrics
            if failure == "crash":
                assert _count(metrics, "http_attempts_total", execution="worker") == 0
                assert _count(metrics, "worker_reports_total", outcome="received") == 0
                return
            assert _count(metrics, "http_attempts_total", execution="worker") == len(observed)
            assert _count(metrics, "operations_total", execution="worker") >= 1
            if failure == "retry":
                assert observed == [429, 429, 200]
                assert _count(metrics, "http_attempts_total", execution="worker", retry="retry") == 2
                assert _count(metrics, "operations_total", execution="worker", outcome="success") == 1
            elif failure == "denied":
                assert observed == [403]
                assert _count(metrics, "http_responses_total", execution="worker", status_code="403") == 1
                assert _count(metrics, "operations_total", execution="worker", outcome="failure") == 1
            repeated = client.get("/metrics").text
            assert _count(repeated, "http_attempts_total", execution="worker") == _count(
                metrics, "http_attempts_total", execution="worker"
            )
            if tracing:
                assert any(b"provider.http.completed" in packet for packet in traces)


def _large_provider_report(_assignment):
    from powercontext.builtin.inference.observability import ProviderObservation, current_provider_observer
    from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingWorkerCompletion

    observer = current_provider_observer()
    assert observer is not None
    for code in range(200, 328):
        observer(ProviderObservation("http", "openai", "generation", "success", code / 1000, code, "initial"))
    return ArtifactProcessingWorkerCompletion()


def test_large_provider_report_drains_before_process_join():
    import asyncio

    from powercontext.builtin.persistence.supervision import ArtifactProcessingFence
    from powercontext.builtin.runtime.artifact_processing import SpawnArtifactProcessingWorkerLauncher
    from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingWorkAssignment

    async def scenario():
        assignment = ArtifactProcessingWorkAssignment(
            binding_name="memory-source-window",
            scope_id="scope-fixture",
            artifact_family="memory",
            claimed_request_generation=1,
            fence=ArtifactProcessingFence(
                supervisor_group="global", holder_id="fixture", supervisor_generation=1, lease_mode="single-process"
            ),
            worker_id="00000000-0000-4000-8000-000000000001",
        )
        launcher = SpawnArtifactProcessingWorkerLauncher(_large_provider_report, collect_provider_observations=True)
        handle = await launcher.start(assignment)
        try:
            completion = await asyncio.wait_for(handle.wait(), timeout=20)
            assert completion.provider_observations is not None
            assert sum(row.count for row in completion.provider_observations.rows) == 128
            assert completion.provider_observations.dropped == 0
        finally:
            await handle.terminate()

    asyncio.run(scenario())
