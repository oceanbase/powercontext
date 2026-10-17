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
import logging
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.runtime import BuiltinRuntime
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    ArtifactProcessingSupervisor,
    ArtifactProcessingSupervisors,
)
from powercontext.builtin.runtime.extraction_diagnostics import ExtractionDiagnostics
from powercontext.builtin.runtime.observations import DurationObservations
from powercontext.builtin.runtime.readiness import DependencyReadinessSnapshot, RuntimeReadinessSnapshot
from powercontext.server.factory import create_server_app
from powercontext.server.logging import _OperationalStreamHandler, log_record_failures
from powercontext.server.metrics import ServerMetrics
from tests.test_server_metrics import _settings


def _samples(metrics):
    return {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in text_string_to_metric_families(metrics)
        for sample in family.samples
    }


def _value(samples, name, **labels):
    return samples[("powercontext_server_" + name, tuple(sorted(labels.items())))]


def test_passive_dependency_snapshots_preserve_unobserved_disabled_zero_and_histograms():
    durations = DurationObservations()
    durations.record("ready", 0)
    dependency = DependencyReadinessSnapshot(
        name="database",
        blocking=True,
        status="ready",
        last_checked_at=100,
        last_success_at=100,
        durations=durations.snapshot(),
    )
    disabled = DependencyReadinessSnapshot(
        name="inference.generation",
        blocking=False,
        status="disabled",
        last_checked_at=None,
        last_success_at=None,
        durations=(),
    )
    runtime = SimpleNamespace(
        readiness_snapshot=lambda: RuntimeReadinessSnapshot(
            status="ready", observed_at=101, dependencies=(dependency, disabled)
        ),
        artifact_processing_supervisor=None,
    )
    metrics = ServerMetrics(role="api")
    metrics.operational.replace(cast(BuiltinRuntime, runtime))
    first = _samples(metrics.render().decode())
    metrics.operational.replace(cast(BuiltinRuntime, runtime))
    second = _samples(metrics.render().decode())
    assert _value(first, "dependency_probe_duration_seconds_count", dependency="database", outcome="ready") == 1
    assert _value(second, "dependency_probe_duration_seconds_count", dependency="database", outcome="ready") == 1
    assert _value(first, "dependency_probe_duration_seconds_sum", dependency="database", outcome="ready") == 0
    assert _value(first, "dependency_observed_timestamp_seconds", dependency="database") == 100
    assert _value(first, "dependency_state", dependency="inference.generation", state="disabled") == 1
    assert not any(
        name.endswith("timestamp_seconds") and ("dependency", "inference.generation") in labels
        for name, labels in first
    )
    assert _value(first, "runtime_readiness_state", state="ready") == 1
    metrics.operational.replace(None)
    assert _value(_samples(metrics.render().decode()), "runtime_readiness_state", state="unobserved") == 1


def test_endpoint_scrapes_are_passive_and_preserve_last_good_snapshot_on_collection_failure(tmp_path, monkeypatch):
    app = create_server_app(settings=_settings(tmp_path / "passive.db"))
    with TestClient(app) as client:
        runtime = app.state.application
        # Any runtime operation/database probe from a scrape is a regression.
        monkeypatch.setattr(runtime, "readiness", Mock(side_effect=AssertionError("scrape must not probe")))
        monkeypatch.setattr(
            runtime.primary_database, "transaction", Mock(side_effect=AssertionError("scrape must not query"))
        )
        first = client.get("/metrics")
        assert first.status_code == 200
        assert _value(_samples(first.text), "runtime_readiness_state", state="ready") == 1
        observed_at = _value(_samples(first.text), "telemetry_collection_timestamp_seconds")
        monkeypatch.setattr(runtime, "readiness_snapshot", Mock(side_effect=RuntimeError("private-provider-details")))
        failed = client.get("/metrics")
        assert failed.status_code == 200
        samples = _samples(failed.text)
        assert _value(samples, "telemetry_collection_failures_total") == 1
        assert _value(samples, "telemetry_collection_timestamp_seconds") == observed_at
        assert "private-provider-details" not in failed.text
        monkeypatch.undo()


def test_log_delivery_failure_is_counted_without_changing_log_result(capsys):
    handler = _OperationalStreamHandler(stream=Mock(write=Mock(side_effect=OSError("private-log-record"))))
    before = log_record_failures()
    handler.handle(logging.makeLogRecord({"msg": "private-log-record", "levelno": logging.INFO}))
    handler.handle(logging.makeLogRecord({"msg": "private-log-record", "levelno": logging.INFO}))
    assert log_record_failures() == before + 2
    stderr = capsys.readouterr().err
    assert stderr.count("observability.log_failed") == 1
    assert "private-log-record" not in stderr
    assert _value(_samples(ServerMetrics().render().decode()), "telemetry_log_records_dropped_total") == before + 2


def test_collection_cost_and_labels_depend_on_registered_families_not_scope_count():
    durations = DurationObservations()
    durations.record("noop", 0.01)
    processing = SimpleNamespace(
        attempt_durations=durations.snapshot(),
        discovery_durations=(),
        error_counts=(("internal", 1), ("private-error", 1)),
        retry_counts=(("blocked", 2),),
        admission_blocks=2,
    )
    families = {
        family: {
            "status": "leader",
            "supervisor_running": True,
            "supervisor_role": "leader",
            "unacknowledged_requests": 10**9,
            "last_acknowledgement_timestamp_seconds": 100,
            "requested_snapshot_timestamp_seconds": 110,
            "observed_pending_scopes": 1000,
            "oldest_observed_pending_age_seconds": 50,
        }
        for family in ("memory", "profile")
    }
    supervisor = SimpleNamespace(family_status=families, processing_observations=dict.fromkeys(families, processing))
    runtime = SimpleNamespace(
        readiness_snapshot=lambda: RuntimeReadinessSnapshot("unobserved", None, ()),
        artifact_processing_supervisor=supervisor,
    )
    metrics = ServerMetrics(role="background")
    metrics.operational.replace(cast(BuiltinRuntime, runtime))
    rendered = metrics.render().decode()
    assert "private-error" not in rendered
    samples = _samples(rendered)
    assert _value(samples, "artifact_processing_attempt_duration_seconds_count", family="memory", outcome="noop") == 1
    assert _value(samples, "artifact_processing_oldest_observed_pending_age_seconds", family="memory") == 50
    assert _value(samples, "artifact_processing_observed_pending_scopes", family="memory") == 1000
    for family in ("memory", "profile"):
        assert _value(samples, "artifact_processing_supervisor_role", family=family, state="leader") == 1
    names = [family.name for family in text_string_to_metric_families(rendered)]
    assert len(names) == len(set(names))  # one declaration per metric across families
    size = len(rendered)
    for values in families.values():
        values["unacknowledged_requests"] = 0
    metrics.operational.replace(cast(BuiltinRuntime, runtime))
    assert abs(len(metrics.render().decode()) - size) < 100


def test_healthy_combined_api_exposes_idle_stalled_and_stopped_processing(tmp_path):  # noqa: C901 - lifecycle regression
    # Fault injection is confined to the worker boundary. API, runtime, database,
    # supervisor, durable requests and Prometheus rendering are real components.
    app = create_server_app(settings=_settings(tmp_path / "stalled.db"))
    with TestClient(app) as client:
        runtime = app.state.application
        assert client.portal is not None
        started = asyncio.Event()

        class Handle:
            async def wait(self):
                started.set()
                await asyncio.Event().wait()

            async def terminate(self):
                pass

        class Launcher:
            async def start(self, assignment):
                return Handle()

        class Intents(ArtifactProcessingIntentRepository):
            fail_observation = False

            async def pending_observation(self, connection, binding_name):
                if self.fail_observation:
                    raise RuntimeError("isolated pending observation failure")  # noqa: TRY003
                return await super().pending_observation(connection, binding_name)

        intents = Intents()
        supervisor = ArtifactProcessingSupervisor(
            database=runtime.primary_database,
            intents=intents,
            bindings=[ArtifactProcessingBinding("controlled", "memory", Launcher())],
            lease_mode="single-process",
            pending_observation_interval_seconds=0.01,
        )
        runtime.artifact_processing_supervisor = ArtifactProcessingSupervisors([supervisor])
        client.portal.call(supervisor.start)
        try:
            idle = _samples(client.get("/metrics").text)
            assert _value(idle, "artifact_processing_unacknowledged_requests", family="memory") == 0
            assert not any(name.endswith("oldest_pending_age_seconds") for name, _ in idle)

            async def enqueue_and_wait_for_worker():
                async with runtime.primary_database.transaction() as connection:
                    await ArtifactProcessingIntentRepository().request(connection, "pending-scope", "controlled")
                supervisor.wake()
                await asyncio.wait_for(started.wait(), 3)
                async with asyncio.timeout(3):
                    while supervisor.family_status["memory"]["unacknowledged_requests"] != 1:  # noqa: ASYNC110
                        await asyncio.sleep(0.01)

            client.portal.call(enqueue_and_wait_for_worker)
            for _ in range(3):
                assert client.get("/health/ready").status_code == 200
                assert client.get("/v1/capabilities").status_code == 200
                stalled = _samples(client.get("/metrics").text)
                assert _value(stalled, "artifact_processing_unacknowledged_requests", family="memory") == 1
                assert _value(stalled, "artifact_processing_used_workers", family="memory") == 1
                assert (
                    _value(stalled, "artifact_processing_last_acknowledgement_timestamp_seconds", family="memory") == 0
                )
                assert _value(stalled, "artifact_processing_pending_age_unknown_scopes", family="memory") == 0
                assert _value(stalled, "artifact_processing_oldest_pending_timestamp_seconds", family="memory") > 0

            async def fail_observation():
                intents.fail_observation = True
                async with asyncio.timeout(3):
                    while supervisor.family_status["memory"]["pending_observation_failures"] == 0:  # noqa: ASYNC110
                        await asyncio.sleep(0.01)
                    # A successful read already in flight when the flag changed
                    # may publish first. After one failure, the cache is stable.
                    previous = supervisor.family_status["memory"]
                    while (  # noqa: ASYNC110
                        supervisor.family_status["memory"]["pending_observation_failures"]
                        == previous["pending_observation_failures"]
                    ):
                        await asyncio.sleep(0.01)
                return previous

            previous = client.portal.call(fail_observation)
            failed = _samples(client.get("/metrics").text)
            assert _value(failed, "artifact_processing_pending_observation_failures_total", family="memory") >= 1
            assert (
                _value(failed, "artifact_processing_requested_snapshot_timestamp_seconds", family="memory")
                == (previous["requested_snapshot_timestamp_seconds"])
            )
            diagnostics = ExtractionDiagnostics(pipeline_configured=True, external_worker=False)
            assert diagnostics.snapshot(runtime.artifact_processing_supervisor).observation.last_failure is None
            assert client.get("/v1/capabilities").status_code == 200

            async def recover_observation():
                intents.fail_observation = False
                async with asyncio.timeout(3):
                    while (  # noqa: ASYNC110
                        supervisor.family_status["memory"]["requested_snapshot_timestamp_seconds"]
                        == previous["requested_snapshot_timestamp_seconds"]
                    ):
                        await asyncio.sleep(0.01)

            client.portal.call(recover_observation)
            assert diagnostics.snapshot(runtime.artifact_processing_supervisor).observation.last_failure is None
            client.portal.call(supervisor.close)
            stopped = _samples(client.get("/metrics").text)
            assert _value(stopped, "artifact_processing_supervisor_running", family="memory") == 0
            assert _value(stopped, "artifact_processing_unacknowledged_requests", family="memory") == 1
            assert client.get("/health/ready").status_code == 200
            assert client.get("/v1/capabilities").status_code == 200
        finally:
            client.portal.call(supervisor.close)
            runtime.artifact_processing_supervisor = None
