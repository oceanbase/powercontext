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

"""Processing counts and their freshness must belong to one successful scrape."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack, asynccontextmanager
from threading import Event, get_ident
from unittest.mock import Mock

import httpx
import pytest
from prometheus_client import generate_latest

from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    ArtifactProcessingSupervisor,
    ArtifactProcessingSupervisors,
)
from powercontext.server.background_metrics import create_background_metrics_app
from powercontext.server.factory import create_server_app
from powercontext.server.metrics import ServerMetrics
from tests.test_operational_metrics import _assert_no_exact_pending_age, _samples, _value
from tests.test_server_metrics import _settings


class _WaitingWorker:
    async def wait(self):
        await asyncio.Event().wait()

    async def terminate(self):
        pass


class _WaitingLauncher:
    async def start(self, assignment):
        return _WaitingWorker()


@asynccontextmanager
async def _running_host(tmp_path, host, *, metrics=None):
    settings = _settings(tmp_path / f"{host}.db")
    async with AsyncExitStack() as resources:
        if host == "api":
            app = create_server_app(settings=settings)
            await resources.enter_async_context(app.router.lifespan_context(app))
            runtime = app.state.application
        else:
            runtime = await resources.enter_async_context(
                open_builtin_runtime(BuiltinConfig(database=settings.database, runtime=settings.runtime))
            )
            app = create_background_metrics_app(
                settings,
                runtime=runtime,
                metrics=ServerMetrics(role="background") if metrics is None else metrics,
                access_control=None,
            )
        supervisor = ArtifactProcessingSupervisor(
            database=runtime.primary_database,
            bindings=[ArtifactProcessingBinding("controlled", "memory", _WaitingLauncher(), max_workers=1)],
            lease_mode="single-process",
            pending_observation_interval_seconds=0.01,
        )
        runtime.artifact_processing_supervisor = ArtifactProcessingSupervisors([supervisor])
        await resources.enter_async_context(supervisor)
        client = await resources.enter_async_context(
            httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://testserver")
        )
        yield client, runtime, supervisor, app.state.metrics


async def _add_pending(runtime, supervisor, scope_id, count):
    async with runtime.primary_database.transaction() as connection:
        await ArtifactProcessingIntentRepository().request(connection, scope_id, "controlled")
    supervisor.wake()
    async with asyncio.timeout(5):
        while supervisor.family_status["memory"]["unacknowledged_requests"] != count:  # noqa: ASYNC110
            await asyncio.sleep(0.001)


def _published_observation(samples):
    return {
        key: value
        for key, value in samples.items()
        if key[0].startswith((
            "powercontext_server_artifact_processing_",
            "powercontext_server_runtime_readiness_",
            "powercontext_server_dependency_",
        ))
        or key[0] == "powercontext_server_telemetry_collection_timestamp_seconds"
    }


@pytest.mark.parametrize("host", ["api", "background"])
def test_first_collection_failure_does_not_publish_partial_processing_counts(tmp_path, monkeypatch, host):
    async def scenario():
        async with _running_host(tmp_path, host) as (client, runtime, supervisor, metrics):
            await _add_pending(runtime, supervisor, "private-first-scope", 1)
            # API startup publishes dependency states independently of scrape snapshots.
            # Rendering the registry preserves that baseline without refreshing Runtime.
            baseline_samples = _samples(metrics.render().decode())
            baseline = _published_observation(baseline_samples)
            timestamp = "powercontext_server_telemetry_collection_timestamp_seconds"
            dependency_state = "powercontext_server_dependency_state"
            assert baseline[(timestamp, ())] == 0
            assert {name for name, _ in baseline} <= {timestamp, dependency_state}
            if host == "api":
                assert any(name == dependency_state for name, _ in baseline)
                assert _value(baseline_samples, "readiness_evaluated_timestamp_seconds") > 0
            else:
                assert baseline == {(timestamp, ()): 0}
            with monkeypatch.context() as failure:
                failure.setattr(
                    runtime, "readiness_snapshot", Mock(side_effect=RuntimeError("private-provider-details"))
                )
                response = await client.get("/metrics")
            assert response.status_code == 200
            samples = _samples(response.text)
            assert _value(samples, "telemetry_collection_failures_total") == 1
            assert _value(samples, "telemetry_collection_timestamp_seconds") == 0
            assert _published_observation(samples) == baseline
            assert "private-provider-details" not in response.text
            recovered = await client.get("/metrics")
            assert recovered.status_code == 200
            samples = _samples(recovered.text)
            _assert_no_exact_pending_age(samples)
            assert _value(samples, "artifact_processing_unacknowledged_requests", family="memory") == 1
            assert _value(samples, "artifact_processing_requested_snapshot_timestamp_seconds", family="memory") > 0
            assert _value(samples, "telemetry_collection_timestamp_seconds") > 0
            assert _value(samples, "telemetry_collection_failures_total") == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("render_directly", [False, True], ids=["server-render", "registry-render"])
def test_threaded_render_retains_one_snapshot_while_endpoint_publishes_next(tmp_path, monkeypatch, render_directly):
    async def scenario():
        metrics = ServerMetrics(role="background")
        async with _running_host(tmp_path, "background", metrics=metrics) as (client, runtime, supervisor, _):
            await _add_pending(runtime, supervisor, "private-first-scope", 1)
            first = await client.get("/metrics")
            assert first.status_code == 200
            previous = _published_observation(_samples(first.text))
            await _add_pending(runtime, supervisor, "private-second-scope", 2)

            collected_freshness = Event()
            resume_rendering = Event()
            main_thread = get_ident()
            collect = metrics.registry.collect

            def paused_collect():
                for metric in collect():
                    yield metric
                    if (
                        get_ident() != main_thread
                        and metric.name == "powercontext_server_telemetry_collection_timestamp_seconds"
                    ):
                        collected_freshness.set()
                        assert resume_rendering.wait(5), "endpoint could not publish while a scrape was rendering"

            monkeypatch.setattr(metrics.registry, "collect", paused_collect)
            with ThreadPoolExecutor(max_workers=1) as executor:
                render = (lambda: generate_latest(metrics.registry)) if render_directly else metrics.render
                pending_render = executor.submit(render)
                try:
                    assert await asyncio.to_thread(collected_freshness.wait, 5)
                    second = await client.get("/metrics")
                    assert second.status_code == 200
                    assert (
                        _value(_samples(second.text), "artifact_processing_unacknowledged_requests", family="memory")
                        == 2
                    )
                finally:
                    resume_rendering.set()
                rendered = await asyncio.to_thread(pending_render.result, 5)
            assert _published_observation(_samples(rendered.decode())) == previous

    asyncio.run(scenario())


@pytest.mark.parametrize("host", ["api", "background"])
def test_failed_concurrent_scrapes_keep_previous_counts_and_freshness_until_recovery(tmp_path, monkeypatch, host):
    async def scenario():
        async with _running_host(tmp_path, host) as (client, runtime, supervisor, _):
            await _add_pending(runtime, supervisor, "private-first-scope", 1)
            first = await client.get("/metrics")
            assert first.status_code == 200
            previous = _samples(first.text)
            assert _value(previous, "artifact_processing_unacknowledged_requests", family="memory") == 1
            collected_at = _value(previous, "telemetry_collection_timestamp_seconds")
            pending_at = _value(previous, "artifact_processing_requested_snapshot_timestamp_seconds", family="memory")
            assert collected_at > 0
            assert pending_at > 0

            await _add_pending(runtime, supervisor, "private-second-scope", 2)
            assert supervisor.family_status["memory"]["requested_snapshot_timestamp_seconds"] > pending_at
            with monkeypatch.context() as failure:
                failure.setattr(
                    runtime, "readiness_snapshot", Mock(side_effect=RuntimeError("private-provider-details"))
                )
                responses = await asyncio.gather(*(client.get("/metrics") for _ in range(8)))
            failures = []
            for response in responses:
                assert response.status_code == 200
                samples = _samples(response.text)
                assert _value(samples, "artifact_processing_unacknowledged_requests", family="memory") == 1
                assert _value(samples, "telemetry_collection_timestamp_seconds") == collected_at
                assert (
                    _value(samples, "artifact_processing_requested_snapshot_timestamp_seconds", family="memory")
                    == pending_at
                )
                assert _published_observation(samples) == _published_observation(previous)
                failures.append(_value(samples, "telemetry_collection_failures_total"))
                assert "private-provider-details" not in response.text
                assert "private-first-scope" not in response.text
                assert "private-second-scope" not in response.text
            assert sorted(failures) == list(range(1, 9))

            recovered = await client.get("/metrics")
            assert recovered.status_code == 200
            samples = _samples(recovered.text)
            _assert_no_exact_pending_age(samples)
            assert _value(samples, "artifact_processing_unacknowledged_requests", family="memory") == 2
            assert _value(samples, "telemetry_collection_timestamp_seconds") > collected_at
            assert (
                _value(samples, "artifact_processing_requested_snapshot_timestamp_seconds", family="memory")
                > pending_at
            )
            assert _value(samples, "telemetry_collection_failures_total") == 8

    asyncio.run(scenario())
