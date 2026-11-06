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

"""Opt-in acceptance against real Collector, Tempo and Loki storage.

Run with PC_OBSERVABILITY_ACCEPTANCE=1 after starting deploy/observability.
The local provider fixture performs no external model calls.
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from tests.e2e import test_worker_otlp as workers

pytestmark = pytest.mark.skipif(
    os.environ.get("PC_OBSERVABILITY_ACCEPTANCE") != "1", reason="requires explicitly enabled reference stack"
)


@pytest.mark.parametrize("family", ["memory", "topic-memory"])
@pytest.mark.parametrize("timeout", [False, True])
def test_actual_spawned_worker_model_spans_are_queryable_in_tempo(tmp_path, monkeypatch, caplog, family, timeout):
    local = Path(__file__).resolve().parents[2] / "deploy/observability/.local"
    auth = ("collector", (local / "gateway-password").read_text().strip())
    gateway = os.environ.get("PC_OBSERVABILITY_GATEWAY", "http://127.0.0.1:14318")
    original_receiver = workers._receiver
    observed = []
    forwarding_errors = []

    @contextmanager
    def receiver(slow=False):
        with original_receiver(slow) as (endpoint, received):

            class Proxy(BaseHTTPRequestHandler):
                def log_message(self, format, *args):  # noqa: A002 - base method accepts this keyword.
                    pass

                def do_POST(self):
                    payload = self.rfile.read(int(self.headers["Content-Length"]))
                    headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
                    with httpx.Client(trust_env=False, timeout=10) as client:
                        if self.path == "/v1/traces":
                            try:
                                result = client.post(gateway + self.path, content=payload, headers=headers, auth=auth)
                                result.raise_for_status()
                            except Exception as error:
                                forwarding_errors.append(type(error).__name__)
                        result = client.post(endpoint + self.path, content=payload, headers=headers)
                    try:
                        self.send_response(result.status_code)
                        self.send_header("Content-Type", result.headers.get("Content-Type", "application/json"))
                        self.send_header("Content-Length", str(len(result.content)))
                        self.end_headers()
                        self.wfile.write(result.content)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

            proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
            thread = threading.Thread(target=proxy.serve_forever, daemon=True)
            thread.start()
            try:
                yield f"http://127.0.0.1:{proxy.server_port}", received
            finally:
                proxy.shutdown()
                proxy.server_close()
                thread.join(timeout=2)
                observed.extend(received)

    monkeypatch.setattr(workers, "_receiver", receiver)
    workers.test_builtin_spawned_worker_exports_model_and_execution_spans_to_otlp(
        tmp_path, monkeypatch, timeout, caplog, family
    )
    assert not forwarding_errors, forwarding_errors
    executions = [span for span, _ in workers._spans(observed) if span.name == "artifact_processing.worker.execute"]
    assert executions
    trace_id = executions[0].trace_id.hex()
    deadline = time.monotonic() + 30
    with httpx.Client(base_url=gateway, auth=auth, trust_env=False, timeout=5) as client:
        while time.monotonic() < deadline:
            result = client.get("/tempo/api/traces/" + trace_id, headers={"Accept": "application/json"})
            if result.status_code == 200 and "artifact_processing.worker.execute" in result.text:
                assert "chat " in result.text
                assert "private OTLP Source sentinel" not in result.text
                return
            time.sleep(0.5)
    pytest.fail("Real child and model spans did not become queryable in Tempo")


def _active_alerts(backend: httpx.Client, name: str, instance: str):
    # Alert state is authoritative; a TSDB lookback can still return an old ALERTS sample.
    response = backend.get("/prometheus/api/v1/alerts")
    response.raise_for_status()
    return [
        alert
        for alert in response.json()["data"]["alerts"]
        if alert["state"] == "firing"
        and alert["labels"].get("alertname") == name
        and alert["labels"].get("instance") == instance
    ]


@pytest.fixture
def host_target():
    import json

    path = Path(__file__).resolve().parents[2] / "deploy/observability/.local/targets.json"
    original = path.read_text()
    targets = json.loads(original)
    if not any("host.docker.internal:18080" in target["targets"] for target in targets):
        targets.append({"targets": ["host.docker.internal:18080"], "labels": {"role": "all"}})
        path.write_text(json.dumps(targets))
    try:
        local = path.parent
        with httpx.Client(
            base_url="http://127.0.0.1:14318",
            trust_env=False,
            auth=("collector", (local / "gateway-password").read_text().strip()),
        ) as backend:
            deadline = time.monotonic() + 30
            matches = []
            while time.monotonic() < deadline:
                active = backend.get("/prometheus/api/v1/targets").json()["data"]["activeTargets"]
                matches = [
                    target
                    for target in active
                    if target["labels"].get("instance") == "host.docker.internal:18080" and target["health"] == "up"
                ]
                if matches:
                    break
                time.sleep(0.5)
            assert matches, "Wait for live discovery, not a historical up sample"
        yield
    finally:
        path.write_text(original)


def test_host_database_write_failure_is_queryable_and_recovers(host_target):
    import secrets
    import sqlite3

    local = Path(__file__).resolve().parents[2] / "deploy/observability/.local"
    auth = ("collector", (local / "gateway-password").read_text().strip())
    token = (local / "observe-token").read_text().strip()
    trace_id = secrets.token_hex(16)
    name = "explicit-database-fault-" + secrets.token_hex(4)
    request = {"title": name, "summary": "Isolated reference acceptance", "idempotency_key": name}
    with (
        httpx.Client(
            base_url="http://127.0.0.1:18080", headers={"Authorization": "Bearer " + token}, trust_env=False, timeout=15
        ) as server,
        sqlite3.connect(local / "host.db") as database,
        httpx.Client(base_url="http://127.0.0.1:14318", auth=auth, trust_env=False, timeout=5) as backend,
    ):
        query = 'powercontext_server_transport_requests_total{operation="create_scope",outcome="failure",instance="host.docker.internal:18080"}'
        initial = backend.get("/prometheus/api/v1/query", params={"query": query}).json()["data"]["result"]
        before = sum(float(item["value"][1]) for item in initial)
        database.execute("BEGIN IMMEDIATE")
        try:
            result = server.post(
                "/v1/scopes",
                json=request,
                headers={
                    "traceparent": f"00-{trace_id}-{secrets.token_hex(8)}-01",
                },
            )
            assert result.status_code >= 500
            if os.environ.get("PC_OBSERVABILITY_STALL_ACCEPTANCE") == "1":
                # Database-backed observe authorization is also unavailable under the write lock.
                from deploy.observability.verify import wait_for

                wait_for(
                    lambda: _active_alerts(backend, "AcceptanceTargetDown", "host.docker.internal:18080"), timeout=20
                )
        finally:
            database.rollback()
        assert server.post("/v1/scopes", json=request).status_code == 201
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            logs = backend.get(
                "/loki/loki/api/v1/query_range",
                params={
                    "query": '{service_name="powercontext-server"} |= "' + trace_id + '"',
                },
            ).json()["data"]["result"]
            metrics = backend.get(
                "/prometheus/api/v1/query",
                params={
                    "query": query,
                },
            ).json()["data"]["result"]
            trace = backend.get("/tempo/api/traces/" + trace_id)
            if logs and sum(float(item["value"][1]) for item in metrics) > before and trace.status_code == 200:
                assert "failure" in str(logs)
                if os.environ.get("PC_OBSERVABILITY_STALL_ACCEPTANCE") == "1":
                    from deploy.observability.verify import wait_for

                    wait_for(
                        lambda: not _active_alerts(backend, "AcceptanceTargetDown", "host.docker.internal:18080"),
                        timeout=30,
                    )
                return
            time.sleep(0.5)
    pytest.fail("Actual database write failure was not stored across all three signals")


class _StallWorker:
    def __init__(self, delegate, release_path):
        self.delegate = delegate
        self.release_path = release_path

    def __call__(self, assignment):
        while not Path(self.release_path).exists():
            time.sleep(0.1)
        return self.delegate(assignment)


@pytest.mark.skipif(
    os.environ.get("PC_OBSERVABILITY_STALL_ACCEPTANCE") != "1", reason="requires acceptance Compose override"
)
def test_real_worker_stall_fires_and_resolves_after_acknowledgement(tmp_path, monkeypatch):
    import base64
    import json
    import secrets

    import uvicorn
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from pydantic import SecretStr

    from powercontext.builtin.persistence.sqlite import SQLiteConfig
    from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig
    from powercontext.server.factory import create_server_app
    from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerSettings, TracingConfig
    from powercontext.server.telemetry import service_resource
    from powercontext.server.tracing import ServerTracing
    from powercontext.server.worker_tracing import WorkerTracing

    local = Path(__file__).resolve().parents[2] / "deploy/observability/.local"
    token = (local / "observe-token").read_text().strip()
    auth = ("collector", (local / "gateway-password").read_text().strip())
    gateway = "http://127.0.0.1:14318"
    authorization = "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", gateway + "/v1/traces")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "Authorization=" + authorization.replace(" ", "%20"))
    monkeypatch.setenv("OPENAI_API_KEY", "local-stall-fixture")
    release = tmp_path / "release-worker"
    original_wrap = WorkerTracing.wrap
    monkeypatch.setattr(
        WorkerTracing, "wrap", lambda self, entrypoint: _StallWorker(original_wrap(self, entrypoint), str(release))
    )
    targets_path = local / "targets.json"
    original_targets = targets_path.read_text()
    targets = json.loads(original_targets)
    targets.append({"targets": ["host.docker.internal:18083"], "labels": {"role": "all"}})
    targets_path.write_text(json.dumps(targets))
    provider = TracerProvider(resource=service_resource("all"), shutdown_on_exit=False)
    provider.add_span_processor(
        BatchSpanProcessor(
            workers.OTLPSpanExporter(
                endpoint=gateway + "/v1/traces",
                headers={"Authorization": authorization},
                timeout=2,
            )
        )
    )
    with workers._receiver() as (endpoint, _received):
        app = create_server_app(
            settings=ServerSettings(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'stall.db'}"),
                auth=BearerAuthConfig(enabled=True, token=SecretStr(token)),
                inference=InferenceConfig(
                    generation_model="openai-chat:fixture", generation_base_url=endpoint + "/model"
                ),
                runtime=RuntimeConfig(
                    artifact_processing_families=("memory",),
                    memory_schedule_seconds=0.05,
                    memory_worker_timeout_seconds=120,
                ),
                tracing=TracingConfig(enabled=True),
                mcp=McpConfig(enabled=False),
            ),
            tracing=ServerTracing(provider, instrumented=True),
        )
        server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=18083, log_level="error"))  # noqa: S104 - authenticated scrape fixture.
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            with (
                httpx.Client(
                    base_url="http://127.0.0.1:18083",
                    headers={"Authorization": "Bearer " + token},
                    trust_env=False,
                    timeout=5,
                ) as client,
                httpx.Client(base_url=gateway, auth=auth, trust_env=False, timeout=5) as backend,
            ):
                deadline = time.monotonic() + 30
                while not server.started and time.monotonic() < deadline:
                    time.sleep(0.1)
                assert server.started
                scope = client.get("/v1/scopes/default").json()["scope_id"]
                assert (
                    client.post(
                        "/v1/sources/content",
                        json={
                            "scope_id": scope,
                            "source_id": secrets.token_hex(8),
                            "content": "Local isolated stall acceptance.",
                        },
                    ).status_code
                    == 202
                )
                deadline = time.monotonic() + 45
                alerts = []
                while time.monotonic() < deadline:
                    alerts = _active_alerts(backend, "AcceptanceProcessingStalled", "host.docker.internal:18083")
                    if alerts:
                        break
                    time.sleep(0.5)
                assert alerts, "A live stalled worker must trigger the acceptance rule"
                release.touch()
                deadline = time.monotonic() + 45
                alerts = []
                while time.monotonic() < deadline:
                    metrics = client.get("/metrics").text
                    alerts = _active_alerts(backend, "AcceptanceProcessingStalled", "host.docker.internal:18083")
                    if (
                        'powercontext_server_artifact_processing_completed_total{family="memory"} 1.0' in metrics
                        and not alerts
                    ):
                        return
                    time.sleep(0.5)
                pytest.fail("Stall alert did not resolve after real worker acknowledgement")
        finally:
            release.touch()
            server.should_exit = True
            thread.join(timeout=10)
            targets_path.write_text(original_targets)
            provider.shutdown()


@pytest.mark.skipif(
    os.environ.get("PC_OBSERVABILITY_STALL_ACCEPTANCE") != "1", reason="requires acceptance Compose override"
)
def test_model_timeout_dependency_alert_and_real_worker_recover(tmp_path, monkeypatch, capfd):
    import base64
    import json
    import logging

    import uvicorn
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from pydantic import AnyHttpUrl, SecretStr

    from deploy.observability.verify import wait_for
    from powercontext.builtin.persistence.sqlite import SQLiteConfig
    from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig
    from powercontext.server.factory import create_server_app
    from powercontext.server.logging import JsonFormatter, OperationalContextFilter
    from powercontext.server.settings import (
        BearerAuthConfig,
        McpConfig,
        ServerLoggingConfig,
        ServerSettings,
        TracingConfig,
    )
    from powercontext.server.telemetry import service_resource
    from powercontext.server.tracing import ServerTracing

    local = Path(__file__).resolve().parents[2] / "deploy/observability/.local"
    token = (local / "observe-token").read_text().strip()
    auth = ("collector", (local / "gateway-password").read_text().strip())
    gateway = "http://127.0.0.1:14318"
    authorization = "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", gateway + "/v1/traces")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "Authorization=" + authorization.replace(" ", "%20"))
    monkeypatch.setenv("OPENAI_API_KEY", "local-timeout-fixture")
    failed = threading.Event()
    failed.set()
    targets_path = local / "targets.json"
    original_targets = targets_path.read_text()
    targets = json.loads(original_targets)
    targets.append({"targets": ["host.docker.internal:18084"], "labels": {"role": "all"}})
    targets_path.write_text(json.dumps(targets))
    provider = TracerProvider(resource=service_resource("all"), shutdown_on_exit=False)
    provider.add_span_processor(
        BatchSpanProcessor(
            workers.OTLPSpanExporter(
                endpoint=gateway + "/v1/traces",
                headers={"Authorization": authorization},
                timeout=2,
            )
        )
    )
    identities = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(identities))
    log_path = local / "logs/model-timeout-acceptance.jsonl"
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    handler.addFilter(OperationalContextFilter(role="all"))
    root_logger = logging.getLogger()
    original_level = root_logger.level
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
    begin = time.time()
    with workers._receiver() as (endpoint, _received):

        class ModelProxy(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A002 - base method accepts this keyword.
                pass

            def do_POST(self):
                payload = self.rfile.read(int(self.headers["Content-Length"]))
                if failed.is_set():
                    time.sleep(1)
                    self.send_response(503)
                    self.end_headers()
                    return
                response = httpx.post(
                    endpoint + self.path,
                    content=payload,
                    headers={
                        "Content-Type": self.headers.get("Content-Type", "application/json"),
                    },
                    trust_env=False,
                    timeout=5,
                )
                try:
                    self.send_response(response.status_code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(response.content)))
                    self.end_headers()
                    self.wfile.write(response.content)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        model = ThreadingHTTPServer(("127.0.0.1", 0), ModelProxy)
        model_thread = threading.Thread(target=model.serve_forever, daemon=True)
        model_thread.start()
        app = create_server_app(
            settings=ServerSettings(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'model-timeout.db'}"),
                auth=BearerAuthConfig(enabled=True, token=SecretStr(token)),
                logging=ServerLoggingConfig(format="json"),
                inference=InferenceConfig(
                    generation_model="openai-chat:fixture",
                    generation_base_url=AnyHttpUrl(f"http://127.0.0.1:{model.server_port}/model"),
                    generation_timeout_seconds=0.25,
                ),
                runtime=RuntimeConfig(artifact_processing_families=("memory",), memory_schedule_seconds=0.05),
                tracing=TracingConfig(enabled=True),
                mcp=McpConfig(enabled=False),
            ),
            tracing=ServerTracing(provider, instrumented=True),
        )
        server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=18084, log_level="error"))  # noqa: S104 - authenticated scrape fixture.
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            wait_for(lambda: server.started, timeout=30)
            with (
                httpx.Client(
                    base_url="http://127.0.0.1:18084",
                    headers={"Authorization": "Bearer " + token},
                    trust_env=False,
                    timeout=5,
                ) as client,
                httpx.Client(base_url=gateway, auth=auth, trust_env=False, timeout=5) as backend,
            ):
                scope = client.get("/v1/scopes/default").json()["scope_id"]
                assert (
                    client.post(
                        "/v1/sources/content",
                        json={
                            "scope_id": scope,
                            "source_id": "timeout-recovery",
                            "content": "Isolated timeout recovery acceptance.",
                        },
                    ).status_code
                    == 202
                )
                wait_for(
                    lambda: _active_alerts(backend, "AcceptanceDependencyFailure", "host.docker.internal:18084"),
                    timeout=60,
                )
                parents = wait_for(
                    lambda: [
                        span
                        for span in identities.get_finished_spans()
                        if span.name == "artifact_processing.worker"
                        and (span.attributes or {}).get("powercontext.error.category") == "timeout"
                    ],
                    timeout=30,
                )
                trace_id = format(parents[0].context.trace_id, "032x")
                wait_for(lambda: backend.get("/tempo/api/traces/" + trace_id).status_code == 200)
                stored = backend.get("/tempo/api/traces/" + trace_id, headers={"Accept": "application/json"})
                assert "artifact_processing.worker.execute" in stored.text and "timeout" in stored.text
                assert "Isolated timeout recovery acceptance." not in stored.text
                captured = capfd.readouterr()
                with log_path.open("a", encoding="utf-8") as output:
                    output.write(captured.err)
                wait_for(
                    lambda: backend.get(
                        "/loki/loki/api/v1/query_range",
                        params={
                            "query": '{service_name="powercontext-server"} | json | error_category="timeout"',
                            "start": str(int(begin * 1_000_000_000)),
                        },
                    ).json()["data"]["result"]
                )
                failed.clear()
                wait_for(
                    lambda: client.get("/health/ready").json()["checks"].get("inference.generation") == "ready",
                    timeout=90,
                )
                wait_for(
                    lambda: not _active_alerts(backend, "AcceptanceDependencyFailure", "host.docker.internal:18084"),
                    timeout=30,
                )
                wait_for(
                    lambda: 'artifact_processing_completed_total{family="memory"} 1.0' in client.get("/metrics").text,
                    timeout=90,
                )
        finally:
            failed.clear()
            server.should_exit = True
            thread.join(timeout=10)
            model.shutdown()
            model.server_close()
            model_thread.join(timeout=2)
            targets_path.write_text(original_targets)
            provider.shutdown()
            root_logger.removeHandler(handler)
            root_logger.setLevel(original_level)
            handler.close()
