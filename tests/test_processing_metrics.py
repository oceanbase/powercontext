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

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from powercontext.limits import MAX_ARTIFACT_FAMILY_LENGTH
from powercontext.server.factory import create_server_app
from powercontext.server.metrics import ServerMetrics
from tests.test_server_metrics import _settings

PREFIX = "powercontext_server_artifact_processing_"


def _status(**changes):
    return {
        "status": "leader",
        "max_workers": 8,
        "available_workers": 5,
        "used_workers": 3,
        "ready": 7,
        "retry_wait": 2,
        "unacknowledged_requests": 19,
        "discovery_seconds": 0.125,
        "last_invocation_seconds": 1.75,
        "completed": 12,
        "failed": 4,
        "timeouts": 1,
        **changes,
    }


def _samples(rendered):
    return {
        (sample.name, sample.labels["family"]): sample.value
        for metric in text_string_to_metric_families(rendered)
        for sample in metric.samples
        if sample.name.startswith(PREFIX)
    }


def test_processing_metrics_expose_family_capacity_progress_and_counter_snapshots():
    metrics = ServerMetrics()
    snapshot = _status(scope_id="scope-secret", worker_id="worker-secret", binding_name="binding-secret")
    metrics.set_processing_families(dict.fromkeys(("memory", "topic-memory", "experience", "profile"), snapshot))

    rendered = metrics.render().decode()
    samples = _samples(rendered)
    assert len(samples) == 11 * 4
    for family in ("memory", "topic-memory", "experience", "profile"):
        for field in (
            "max_workers",
            "available_workers",
            "used_workers",
            "ready",
            "retry_wait",
            "unacknowledged_requests",
            "discovery_seconds",
            "last_invocation_seconds",
            "completed",
            "failed",
            "timeouts",
        ):
            suffix = "_total" if field in {"completed", "failed", "timeouts"} else ""
            assert samples[(PREFIX + field + suffix, family)] == snapshot[field]
    instruments = {metric.name: metric for metric in text_string_to_metric_families(rendered)}
    assert instruments[PREFIX + "completed"].type == "counter"
    assert instruments[PREFIX + "failed"].type == "counter"
    assert instruments[PREFIX + "timeouts"].type == "counter"
    assert instruments[PREFIX + "unacknowledged_requests"].type == "gauge"
    assert "latest requested-work discovery pass" in instruments[PREFIX + "unacknowledged_requests"].documentation
    assert "resets on restart" in instruments[PREFIX + "completed"].documentation
    assert not any(secret in rendered for secret in ("scope-secret", "worker-secret", "binding-secret"))
    assert all(
        set(sample.labels) == {"family"}
        for instrument in instruments.values()
        for sample in instrument.samples
        if sample.name.startswith(PREFIX)
    )

    # Scrapes observe cumulative state; they never add it repeatedly.
    metrics.set_processing_families({"memory": _status(completed=13)})
    assert _samples(metrics.render().decode())[(PREFIX + "completed_total", "memory")] == 13
    assert _samples(metrics.render().decode())[(PREFIX + "completed_total", "memory")] == 13
    metrics.set_processing_families({"memory": _status(completed=0)})
    assert _samples(metrics.render().decode())[(PREFIX + "completed_total", "memory")] == 0


@pytest.mark.parametrize(
    "invalid_family", ["scope/private-secret", 'memory"secret', "Memory", "", "a" * (MAX_ARTIFACT_FAMILY_LENGTH + 1)]
)
def test_processing_metrics_reject_noncanonical_family_labels(invalid_family):
    metrics = ServerMetrics()
    metrics.set_processing_families({invalid_family: _status(), "custom-family": _status()})

    samples = _samples(metrics.render().decode())
    assert {family for _, family in samples} == {"custom-family"}


def test_processing_metrics_replace_removed_families_and_omit_invalid_numbers():
    metrics = ServerMetrics()
    metrics.set_processing_families({"memory": _status()})
    metrics.set_processing_families({"topic-memory": _status()})
    assert {family for _, family in _samples(metrics.render().decode())} == {"topic-memory"}

    metrics.set_processing_families({"memory": {"used_workers": float("nan"), "failed": -1, "ready": "secret"}})
    assert _samples(metrics.render().decode()) == {}
    metrics.set_processing_families({})
    assert _samples(metrics.render().decode()) == {}


def test_existing_metrics_endpoint_refreshes_current_supervisor_snapshot(tmp_path):
    app = create_server_app(settings=_settings(tmp_path / "metrics.db"))
    with TestClient(app) as client:
        runtime = app.state.application
        previous = runtime.artifact_processing_supervisor
        supervisor = SimpleNamespace(family_status={"memory": _status()})
        runtime.artifact_processing_supervisor = supervisor
        try:
            first = client.get("/metrics")
            assert first.status_code == 200
            assert _samples(first.text)[(PREFIX + "used_workers", "memory")] == 3
            supervisor.family_status = {"profile": _status(used_workers=1, available_workers=7, completed=30)}
            second = client.get("/metrics")
            assert second.status_code == 200
            assert _samples(second.text)[(PREFIX + "completed_total", "profile")] == 30
            assert {family for _, family in _samples(second.text)} == {"profile"}
            runtime.artifact_processing_supervisor = None
            assert _samples(client.get("/metrics").text) == {}
        finally:
            runtime.artifact_processing_supervisor = previous
