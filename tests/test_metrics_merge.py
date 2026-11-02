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

"""Regression coverage for combined passive and process-boundary metrics."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families
from pydantic import SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinRuntime
from powercontext.builtin.runtime.observations import DurationObservations
from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingModelUsageDelta
from powercontext.builtin.runtime.readiness import (
    DependencyReadinessSnapshot,
    RuntimeReadiness,
    RuntimeReadinessSnapshot,
    RuntimeReadinessStatus,
)
from powercontext.server.background_metrics import create_background_metrics_app as create_listener_app
from powercontext.server.cli import _run_background_async
from powercontext.server.factory import create_background_metrics_app
from powercontext.server.metrics import ServerMetrics
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig, MetricsConfig, ServerSettings
from powercontext.server.tracing import ServerTracing


def _samples(text):
    families = list(text_string_to_metric_families(text))
    names = [family.name for family in families]
    assert len(names) == len(set(names))
    samples = [sample for family in families for sample in family.samples]
    keys = [(sample.name, tuple(sorted(sample.labels.items()))) for sample in samples]
    assert len(keys) == len(set(keys))
    return dict(zip(keys, (sample.value for sample in samples), strict=True))


def _value(samples, suffix, **labels):
    return samples[("powercontext_server_" + suffix, tuple(sorted(labels.items())))]


def test_dependency_metric_merges_passive_probes_and_access_evaluation_without_duplicates():
    durations = DurationObservations()
    durations.record("ready", 0.25)
    runtime = cast(
        BuiltinRuntime,
        SimpleNamespace(
            readiness_snapshot=lambda: RuntimeReadinessSnapshot(
                status="ready",
                observed_at=101,
                dependencies=(
                    DependencyReadinessSnapshot("database", True, "ready", 100, 100, durations.snapshot()),
                    DependencyReadinessSnapshot("inference.generation", False, "disabled"),
                ),
            ),
            artifact_processing_supervisor=None,
        ),
    )
    metrics = ServerMetrics(role="api")
    metrics.observe_readiness(
        "not_ready",
        {
            "database": "unavailable",
            "runtime": "ready",
            "access_provider": "not_ready",
            "authentication_provider": "ready",
            "artifact_processing.memory": "standby",
        },
    )
    metrics.refresh_runtime(runtime)
    first = _samples(metrics.render().decode())
    assert _value(first, "dependency_state", dependency="database", state="ready") == 1
    assert _value(first, "dependency_state", dependency="database", state="unavailable") == 0
    assert _value(first, "dependency_state", dependency="inference.generation", state="disabled") == 1
    assert _value(first, "dependency_state", dependency="access_provider", state="not_ready") == 1
    assert _value(first, "dependency_state", dependency="authentication_provider", state="ready") == 1
    assert _value(first, "dependency_state", dependency="artifact_processing.memory", state="standby") == 1
    assert _value(first, "readiness_state", state="not_ready") == 1
    assert _value(first, "runtime_readiness_state", state="ready") == 1
    assert _value(first, "dependency_observed_timestamp_seconds", dependency="database") == 100
    assert _value(first, "dependency_probe_duration_seconds_count", dependency="database", outcome="ready") == 1
    assert not any(
        name.endswith("observed_timestamp_seconds") and ("dependency", "access_provider") in labels
        for name, labels in first
    )
    metrics.refresh_runtime(runtime)
    second = _samples(metrics.render().decode())
    for suffix in ("readiness_evaluated_timestamp_seconds", "runtime_readiness_observed_timestamp_seconds"):
        assert _value(first, suffix) == _value(second, suffix)
    assert _value(second, "dependency_probe_duration_seconds_count", dependency="database", outcome="ready") == 1


@pytest.mark.parametrize("factory", [create_background_metrics_app, create_listener_app])
def test_background_factory_entrypoints_preserve_public_liveness_and_protected_metrics(factory):
    class Access:
        deployment_id = "default"

        async def require(self, principal, action, resource, *, context):
            assert principal.id == "server-token"
            assert context.operation == "get_metrics"

    settings = ServerSettings(
        access=AccessControlConfig(mode="enforced"),
        auth=BearerAuthConfig(token=SecretStr("scrape-test-token")),
    )
    headers = {"Authorization": "Bearer scrape-test-token"}
    app = factory(settings, runtime=None, metrics=ServerMetrics(role="background"), access_control=Access())
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/metrics").status_code == 401
        response = client.get("/metrics", headers=headers)
        assert response.status_code == 200
        assert _value(_samples(response.text), "service_role_info", role="background") == 1
        for path in ("/health/ready", "/v1/capabilities", "/mcp", "/docs", "/openapi.json"):
            assert client.get(path, headers=headers).status_code == 404
    settings = settings.model_copy(update={"metrics": MetricsConfig(enabled=False)})
    app = factory(settings, runtime=None, metrics=ServerMetrics(), access_control=Access())
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/metrics", headers=headers).status_code == 404


def test_background_composition_retains_worker_reports_and_both_attempt_observations(monkeypatch):
    async def scenario():
        attempts = DurationObservations()
        attempts.record("success", 0.5)
        observations = SimpleNamespace(
            attempt_durations=attempts.snapshot(),
            discovery_durations=(),
            error_counts=(),
            retry_counts=(),
            admission_blocks=0,
        )

        async def readiness():
            return RuntimeReadiness(RuntimeReadinessStatus.READY, {"runtime": "ready"})

        runtime = SimpleNamespace(
            readiness=readiness,
            readiness_snapshot=lambda: RuntimeReadinessSnapshot("ready", 100, ()),
            artifact_processing_supervisor=SimpleNamespace(
                family_status={"memory": {"completed": 1}},
                processing_observations={"memory": observations},
            ),
        )

        @asynccontextmanager
        async def open_runtime(_config, **options):
            options["model_usage_observer"](2, 10, 2, 1)
            options["worker_model_usage_observer"](ArtifactProcessingModelUsageDelta(3, 4))
            options["worker_model_usage_observer"](None)
            options["processing_observer"]("memory", "success", 0.5)
            yield runtime

        rendered = []

        async def serve(app, settings, stopped):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                for _ in range(2):
                    response = await client.get("/metrics")
                    assert response.status_code == 200
                    rendered.append(_samples(response.text))
            stopped.set()

        monkeypatch.setattr("powercontext.server.cli.open_builtin_runtime", open_runtime)
        monkeypatch.setattr("powercontext.server.cli.serve_background_metrics", serve)
        await _run_background_async(
            ServerSettings(database=SQLiteConfig()), ServerTracing.context_only(role="background")
        )
        assert len(rendered) == 2
        for samples in rendered:
            assert _value(samples, "service_role_info", role="background") == 1
            assert _value(samples, "model_usage_queue_depth") == 2
            assert _value(samples, "model_usage_dropped_records_total") == 5
            assert _value(samples, "model_usage_write_failures_total") == 5
            assert _value(samples, "model_usage_worker_reports_total", outcome="received") == 1
            assert _value(samples, "model_usage_worker_reports_total", outcome="missing") == 1
            assert _value(samples, "readiness_state", state="ready") == 1
            for suffix in ("attempt", "invocation"):
                assert (
                    _value(
                        samples,
                        f"artifact_processing_{suffix}_duration_seconds_count",
                        family="memory",
                        outcome="success",
                    )
                    == 1
                )

    asyncio.run(scenario())
