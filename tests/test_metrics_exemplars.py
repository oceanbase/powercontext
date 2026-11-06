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

"""OpenMetrics correlation must never imply an unsampled trace was recorded."""

from time import perf_counter

from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF, ALWAYS_ON
from prometheus_client.exposition import choose_encoder

from powercontext.server.factory import create_server_app
from powercontext.server.metrics import ServerMetrics
from tests.test_server_metrics import _settings


def test_sampled_application_observation_has_trace_exemplar():
    metrics = ServerMetrics(exemplars_enabled=True)
    provider = TracerProvider(sampler=ALWAYS_ON)
    with provider.get_tracer("test").start_as_current_span("operation") as span:
        metrics.observe_application("get_capabilities", "success", perf_counter())
        metrics.finish_transport("http", "get_capabilities", "success", perf_counter())
        trace_id = format(span.get_span_context().trace_id, "032x")
    encode, _ = choose_encoder("application/openmetrics-text; version=1.0.0")
    body = encode(metrics.registry).decode()
    assert body.count(f'trace_id="{trace_id}"') == 2
    assert "trace_id=" not in metrics.render().decode()
    provider.shutdown()


def test_unsampled_context_has_no_exemplar():
    metrics = ServerMetrics(exemplars_enabled=True)
    provider = TracerProvider(sampler=ALWAYS_OFF)
    with provider.get_tracer("test").start_as_current_span("operation"):
        assert trace.get_current_span().get_span_context().is_valid
        metrics.observe_application("get_capabilities", "success", perf_counter())
        metrics.finish_transport("http", "get_capabilities", "success", perf_counter())
    encode, _ = choose_encoder("application/openmetrics-text; version=1.0.0")
    assert "trace_id=" not in encode(metrics.registry).decode()
    provider.shutdown()


def test_metrics_http_negotiates_openmetrics_and_preserves_default(tmp_path):
    app = create_server_app(settings=_settings(tmp_path / "metrics.db"))
    with TestClient(app) as client:
        default = client.get("/metrics")
        response = client.get("/metrics", headers={"Accept": "application/openmetrics-text; version=1.0.0"})
    assert default.headers["content-type"].startswith("text/plain")
    assert response.headers["content-type"].startswith("application/openmetrics-text")
    assert response.text.endswith("# EOF\n")


def test_tracing_disabled_does_not_offer_exemplars_for_sampled_inbound_context(tmp_path):
    app = create_server_app(settings=_settings(tmp_path / "disabled.db"))
    with TestClient(app) as client:
        client.get(
            "/v1/capabilities",
            headers={
                "traceparent": "00-12345678901234567890123456789012-1234567890123456-01",
            },
        ).raise_for_status()
        response = client.get("/metrics", headers={"Accept": "application/openmetrics-text; version=1.0.0"})
    assert "trace_id=" not in response.text
