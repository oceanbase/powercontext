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

from fastapi.testclient import TestClient
from pydantic import SecretStr

from powercontext.server.factory import create_background_metrics_app, create_server_app
from powercontext.server.metrics import ServerMetrics
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig
from tests.test_server_metrics import _settings


def test_readiness_metrics_distinguish_degraded_and_omit_unconfigured_dependencies():
    metrics = ServerMetrics()
    metrics.set_ready(True)
    metrics.observe_readiness("degraded", {"database": "ready", "inference.generation": "timeout"})
    text = metrics.render().decode()
    assert "powercontext_server_runtime_ready 1.0" in text
    assert 'powercontext_server_readiness_state{state="degraded"} 1.0' in text
    assert 'dependency="inference.generation",state="timeout"} 1.0' in text
    assert "inference.embedding" not in text
    metrics.observe_readiness("ready", {"database": "ready"})
    assert "inference.generation" not in metrics.render().decode()


def test_scrape_reads_last_evaluation_without_running_dependency_probes(tmp_path, monkeypatch):
    app = create_server_app(settings=_settings(tmp_path / "scrape.db"))
    with TestClient(app) as client:

        async def forbidden_probe():
            raise AssertionError("scrape must not probe dependencies")  # noqa: TRY003

        monkeypatch.setattr(app.state.application, "readiness", forbidden_probe)
        first = client.get("/metrics")
        second = client.get("/metrics")
        assert first.status_code == second.status_code == 200
        assert "powercontext_server_readiness_evaluated_timestamp_seconds" in first.text
        first_timestamp = [
            line
            for line in first.text.splitlines()
            if line.startswith("powercontext_server_readiness_evaluated_timestamp_seconds ")
        ]
        assert first_timestamp == [
            line
            for line in second.text.splitlines()
            if line.startswith("powercontext_server_readiness_evaluated_timestamp_seconds ")
        ]


def test_background_metrics_surface_enforces_auth_and_has_no_business_routes(tmp_path):
    settings = _settings(tmp_path / "background-metrics.db").model_copy(
        update={
            "access": AccessControlConfig(mode="enforced"),
            "auth": BearerAuthConfig(token=SecretStr("local-test-token")),
        }
    )
    server = create_server_app(settings=settings)
    with TestClient(server):
        app = create_background_metrics_app(
            settings, server.state.application, server.state.metrics, server.state.access_control
        )
        with TestClient(app) as client:
            assert client.get("/metrics").status_code == 401
            headers = {"Authorization": "Bearer local-test-token"}
            assert client.get("/metrics", headers=headers).status_code == 200
            assert client.get("/v1/scopes/default", headers=headers).status_code == 404
            assert client.get("/mcp", headers=headers).status_code == 404


def test_processing_histogram_counts_attempts_not_scrapes_and_bounds_labels():
    metrics = ServerMetrics()
    metrics.observe_processing("memory", "timeout", 3)
    metrics.observe_processing("memory", "success", 1)
    metrics.observe_processing("private/scope", "success", 1)
    first = metrics.render().decode()
    assert 'invocation_duration_seconds_count{family="memory",outcome="timeout"} 1.0' in first
    assert 'invocation_duration_seconds_sum{family="memory",outcome="success"} 1.0' in first
    assert "private/scope" not in first
    histogram = [
        line
        for line in first.splitlines()
        if line.startswith("powercontext_server_artifact_processing_invocation_duration_seconds")
    ]
    repeated = [
        line
        for line in metrics.render().decode().splitlines()
        if line.startswith("powercontext_server_artifact_processing_invocation_duration_seconds")
    ]
    assert histogram == repeated


def test_processing_freshness_survives_scrape_without_refreshing_observations():
    metrics = ServerMetrics()
    metrics.set_processing_families({
        "memory": {
            "supervisor_alive": 1,
            "leader": 1,
            "blocked": 2,
            "oldest_observed_pending_timestamp_seconds": 123,
            "discovered_timestamp_seconds": 456,
        }
    })
    text = metrics.render().decode()
    assert 'oldest_observed_pending_timestamp_seconds{family="memory"} 123.0' in text
    assert 'discovered_timestamp_seconds{family="memory"} 456.0' in text
    assert 'blocked{family="memory"} 2.0' in text


def test_background_listener_applies_non_loopback_auth_policy():
    import pytest
    from pydantic import ValidationError

    from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
    from powercontext.builtin.runtime.config import RuntimeConfig
    from powercontext.server.settings import HttpConfig, MetricsConfig, ServerSettings

    configuration = {
        "database": OceanBaseConfig(url=SecretStr("mysql+aoceanbase://fixture@127.0.0.1:2881/fixture?charset=utf8mb4")),
        "runtime": RuntimeConfig(artifact_processing_role="background"),
        "http": HttpConfig(host="0.0.0.0"),  # noqa: S104 - validate rejection; no socket is opened.
    }
    with pytest.raises(ValidationError, match="unauthenticated"):
        ServerSettings.model_validate(configuration)
    # With no listener, the existing supervisor-only bind behavior is preserved.
    settings = ServerSettings.model_validate({**configuration, "metrics": MetricsConfig(enabled=False)})
    assert settings.runtime.artifact_processing_role == "background"


def test_usage_counter_snapshots_do_not_recount_on_scrape():
    metrics = ServerMetrics()
    metrics.observe_model_usage(2, 4, 3, 1)
    first = metrics.render().decode()
    assert "powercontext_server_model_usage_dropped_records_total 3.0" in first
    assert "powercontext_server_model_usage_write_failures_total 1.0" in first
    assert "powercontext_server_model_usage_queue_depth 2.0" in first
    second = metrics.render().decode()
    assert "powercontext_server_model_usage_dropped_records_total 3.0" in second
