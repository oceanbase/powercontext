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
from powercontext.server.logging import JsonFormatter, _HumanContextFilter
from powercontext.server.settings import McpConfig, ServerLoggingConfig, ServerSettings, TracingConfig
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

        async def failed_write(_repository, connection, *_args, **_kwargs):
            await connection.exec_driver_sql("SELECT 1 FROM private_usage_fixture_table")

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
def _model_server(*, truncated: bool = False):
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
                            "finish_reason": "length" if truncated else "stop",
                            "message": {
                                "role": "assistant",
                                "content": "private-truncated-response"
                                if truncated
                                else json.dumps({"probes": []} if b"probes" in payload else {"candidates": []}),
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
        log_directory = tmp_path / "logs"
        app = create_server_app(
            settings=ServerSettings(
                logging=ServerLoggingConfig(file=True, directory=log_directory, format="json"),
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

        log_text = (log_directory / "all.log").read_text(encoding="utf-8")
        records = [json.loads(line) for line in log_text.splitlines()]
        child_usage = [
            record
            for record in records
            if record.get("event") == "model_usage.accounting.failed" and record.get("service_role") == "worker"
        ]
        queue_event = next(record for record in child_usage if record["failure_location"] == "queue_full")
        write_event = next(record for record in child_usage if record["failure_location"] == "write")
        assert queue_event["worker_id"] == write_event["worker_id"]
        assert queue_event["request_generation"] == write_event["request_generation"]
        assert write_event["processing_stage"] == "usage_accounting"
        assert write_event["failure_reason"] == "storage_failure"
        assert write_event["cause_exception_type"] == "OperationalError"
        assert write_event["family"] == family
        assert "request_id" not in write_event and "exception" not in write_event
        assert "private_usage_fixture_table" not in json.dumps(child_usage)


def test_provider_truncation_reaches_both_builtin_worker_and_supervisor_logs(
    tmp_path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "local-usage-fixture")
    log_directory = tmp_path / "logs"
    with _model_server(truncated=True) as endpoint:
        app = create_server_app(
            settings=ServerSettings(
                logging=ServerLoggingConfig(file=True, directory=log_directory, format="json"),
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'truncation.db'}"),
                inference=InferenceConfig(
                    generation_model="openai-chat:fixture",
                    generation_base_url=endpoint + "/model",
                    generation_max_requests=1,
                ),
                runtime=RuntimeConfig(artifact_processing_families=("memory",), memory_schedule_seconds=0.05),
                mcp=McpConfig(enabled=False),
            )
        )
        with TestClient(app) as client:
            scope = client.get("/v1/scopes/default").json()["scope_id"]
            assert (
                client.post(
                    "/v1/sources/content",
                    json={"scope_id": scope, "source_id": "truncated-input", "content": "private-artifact-evidence"},
                ).status_code
                == 202
            )
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                metrics = client.get("/metrics").text
                if _sample(metrics, "artifact_processing_failed_total", family="memory") >= 1:
                    break
                time.sleep(0.01)
            else:
                pytest.fail("Built-in Worker did not report the provider failure")
            assert _sample(metrics, "artifact_processing_completed_total", family="memory") == 0

    records = [json.loads(line) for line in (log_directory / "all.log").read_text(encoding="utf-8").splitlines()]
    child = next(item for item in records if item.get("event") == "artifact_processing.worker.failed")
    parent_log = next(
        item
        for item in caplog.records
        if getattr(item, "event", None) == "artifact_processing.failed"
        and getattr(item, "worker_id", None) == child["worker_id"]
    )
    assert _HumanContextFilter(role="all").filter(parent_log)
    assert 'failure_reason="output_truncated"' in vars(parent_log)["processing_context"]
    parent = json.loads(JsonFormatter().format(parent_log))
    for record in (child, parent):
        assert record["error_code"] == "invalid_model_output"
        assert record["processing_stage"] == "generation"
        assert record["failure_reason"] == "output_truncated"
        assert record["finish_reason"] == "length"
        assert record["provider_request_id"] == "usage-fixture"
        rendered = json.dumps(record)
        assert "private-truncated-response" not in rendered and "private-artifact-evidence" not in rendered
    assert child["service_role"] == "worker" and parent["service_role"] == "all"
    assert child["request_generation"] == parent["request_generation"]
