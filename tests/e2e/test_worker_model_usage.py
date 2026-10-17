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

"""Real built-in spawn workers report usage losses independently of tracing."""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig
from powercontext.builtin.runtime.processing_contracts import WorkerEntrypoint
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings, TracingConfig
from powercontext.server.worker_tracing import WorkerTracing


@dataclass(frozen=True)
class _UsageFaultWorker:
    entrypoint: WorkerEntrypoint
    failure: str

    def __call__(self, assignment):
        from powercontext.builtin.persistence.statistics import StatisticsRepository
        from powercontext.builtin.runtime._model_usage import _ModelUsageRecorder

        if self.failure == "crash":
            os._exit(1)

        async def failed_write(*_args, **_kwargs):
            raise RuntimeError("controlled statistics write failure")  # noqa: TRY003

        original_offer = _ModelUsageRecorder.offer

        def burst(recorder, *args, **kwargs):
            # Fill the real queue synchronously before its consumer can run.
            for _ in range(3):
                original_offer(recorder, *args, **kwargs)

        with (
            patch.object(StatisticsRepository, "record", failed_write),
            patch.object(_ModelUsageRecorder, "offer", burst),
        ):
            completion = self.entrypoint(assignment)
        if self.failure == "returned_error":
            raise RuntimeError("controlled worker failure after recorder shutdown")  # noqa: TRY003
        return completion


@contextmanager
def _model_server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - stdlib signature.
            return

        def do_POST(self):
            payload = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/v1/traces":
                response = b""
            else:
                response = json.dumps({
                    "id": "usage-fixture",
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
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _sample(text, name, **labels):
    return next(
        sample.value
        for metric in text_string_to_metric_families(text)
        for sample in metric.samples
        if sample.name == "powercontext_server_" + name and sample.labels == labels
    )


@pytest.mark.parametrize(
    ("family", "tracing", "failure"),
    [
        ("memory", False, "none"),
        ("topic-memory", False, "none"),
        ("memory", True, "none"),
        ("topic-memory", True, "none"),
        ("memory", False, "returned_error"),
        ("memory", False, "crash"),
    ],
)
def test_builtin_worker_usage_losses_reach_server_metrics(tmp_path, monkeypatch, family, tracing, failure):
    if tracing:
        pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    original_wrap = WorkerTracing.wrap
    monkeypatch.setattr(
        WorkerTracing, "wrap", lambda self, entrypoint: _UsageFaultWorker(original_wrap(self, entrypoint), failure)
    )
    monkeypatch.setenv("OPENAI_API_KEY", "local-usage-fixture")
    with _model_server() as endpoint:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", endpoint + "/v1/traces")
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", "0.2")
        app = create_server_app(
            settings=ServerSettings(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'usage.db'}"),
                inference=InferenceConfig(
                    generation_model="openai-chat:fixture", generation_base_url=endpoint + "/model"
                ),
                runtime=RuntimeConfig.model_validate({
                    "artifact_processing_families": (family,),
                    family.replace("-", "_") + "_schedule_seconds": 0.05,
                    "model_usage_queue_capacity": 1,
                }),
                tracing=TracingConfig(enabled=tracing),
                mcp=McpConfig(enabled=False),
            )
        )
        with TestClient(app) as client:
            scope = client.get("/v1/scopes/default").json()["scope_id"]
            assert (
                client.post(
                    "/v1/sources/content",
                    json={"scope_id": scope, "source_id": "usage-fixture", "content": "User prefers short answers."},
                ).status_code
                == 202
            )
            terminal = "completed" if failure == "none" else "failed"
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                text = client.get("/metrics").text
                if _sample(text, f"artifact_processing_{terminal}_total", family=family) >= 1:
                    break
                time.sleep(0.01)
            else:
                pytest.fail("Built-in worker did not reach its expected terminal outcome")
            if failure == "crash":
                assert _sample(text, "model_usage_worker_reports_total", outcome="missing") == 1
                return
            assert _sample(text, "model_usage_write_failures_total") >= 1
            assert _sample(text, "model_usage_dropped_records_total") >= 2
            assert _sample(text, "model_usage_worker_reports_total", outcome="received") == 1
            assert _sample(text, "model_usage_queue_depth") == 0
            assert _sample(text, "model_usage_queue_capacity") == 1
            repeated = client.get("/metrics").text
            for name in ("model_usage_dropped_records_total", "model_usage_write_failures_total"):
                assert _sample(repeated, name) == _sample(text, name)
