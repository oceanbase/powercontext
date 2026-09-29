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
import os
import time
from datetime import timedelta

import pytest

from powercontext.builtin.inference.errors import InferenceTimeoutError
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import SHARED_TABLES
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    ArtifactProcessingSupervisor,
    ArtifactProcessingSupervisors,
    SpawnArtifactProcessingWorkerLauncher,
)
from powercontext.builtin.runtime.extraction_diagnostics import ExtractionDiagnostics
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME


class _ProviderTimeout(InferenceTimeoutError):
    pass


def _model_timeout(_assignment) -> None:
    raise _ProviderTimeout("secret-model-input", 0.01)


def _worker_crash(_assignment) -> None:
    os._exit(1)


def _worker_timeout(_assignment) -> None:
    time.sleep(60)


@pytest.mark.parametrize(
    ("entrypoint", "category", "stage", "deadline"),
    [
        (_model_timeout, "model_timeout", "inference", 15),
        (_worker_crash, "worker_crash", "worker", 15),
        (_worker_timeout, "worker_timeout", "worker", 1),
    ],
)
def test_extraction_diagnostics_observes_spawned_worker_failure_and_stopped_supervisor(
    tmp_path, entrypoint, category, stage, deadline
) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'diagnostics.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            diagnostics = ExtractionDiagnostics(pipeline_configured=True, external_worker=False)
            async with profile.database.transaction() as connection:
                await ArtifactProcessingIntentRepository().request(connection, "scope", SOURCE_WINDOW_TRIGGER_NAME)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        SOURCE_WINDOW_TRIGGER_NAME,
                        "memory",
                        SpawnArtifactProcessingWorkerLauncher(entrypoint),
                        max_workers=1,
                        worker_timeout_seconds=deadline,
                    ),
                ),
                lease_mode="single-process",
                retry_base_seconds=60,
            ) as supervisor:
                supervisors = ArtifactProcessingSupervisors((supervisor,))
                async with asyncio.timeout(20):
                    while diagnostics.snapshot(supervisors).observation.status == "unverified":  # noqa: ASYNC110
                        await asyncio.sleep(0.01)
                snapshot = diagnostics.snapshot(supervisors)
                assert snapshot.background.location == "local"
                assert snapshot.background.role == "leader"
                assert snapshot.background.state == "running"
                assert snapshot.background.automatic_processing_enabled is False
                assert snapshot.observation.status == "observed"
                assert snapshot.observation.last_failure is not None
                assert snapshot.observation.last_failure.code == category
                assert snapshot.observation.last_failure.stage == stage
                assert snapshot.observation.last_failure.occurred_at >= snapshot.observation.since
                assert snapshot.observation.last_success_at is None
                assert "secret-model-input" not in snapshot.model_dump_json()
            stopped = diagnostics.snapshot(supervisors)
            assert stopped.background.state == "stopped"
            assert stopped.background.role is None
            assert stopped.observation == snapshot.observation

    asyncio.run(scenario())


@pytest.mark.parametrize("local_pipeline", [False, True])
def test_remote_worker_observations_are_unknown(local_pipeline) -> None:
    diagnostics = ExtractionDiagnostics(pipeline_configured=local_pipeline, external_worker=True)
    snapshot = diagnostics.snapshot(None)
    assert snapshot.configuration == "unknown"
    assert snapshot.background.location == "external"
    assert snapshot.background.role is None
    assert snapshot.background.state == "unknown"
    assert snapshot.background.automatic_processing_enabled is None
    assert snapshot.observation.status == "unverified"
    assert snapshot.observation.last_failure is None
    assert snapshot.observation.last_success_at is None


@pytest.mark.parametrize("automatic", [False, True])
def test_idle_leader_and_standby_are_running_without_execution_evidence(tmp_path, automatic) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'idle.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            diagnostics = ExtractionDiagnostics(pipeline_configured=True, external_worker=False)
            binding = ArtifactProcessingBinding(
                SOURCE_WINDOW_TRIGGER_NAME,
                "memory",
                SpawnArtifactProcessingWorkerLauncher(_worker_crash),
                automatic_processing_interval=timedelta(hours=1) if automatic else None,
            )
            # Exercise expiring-lease leadership with a local database, not a remote deployment.
            async with (
                ArtifactProcessingSupervisor(
                    database=profile.database, bindings=(binding,), lease_mode="oceanbase"
                ) as leader,
                ArtifactProcessingSupervisor(
                    database=profile.database, bindings=(binding,), lease_mode="oceanbase"
                ) as standby,
            ):
                for supervisor, role in ((leader, "leader"), (standby, "standby")):
                    snapshot = diagnostics.snapshot(ArtifactProcessingSupervisors((supervisor,)))
                    assert snapshot.background.role == role
                    assert snapshot.background.state == "running"
                    assert snapshot.background.automatic_processing_enabled is automatic
                    assert snapshot.observation.status == "unverified"
                    assert snapshot.observation.last_success_at is None
                    assert snapshot.observation.last_failure is None

    asyncio.run(scenario())


def test_discovery_failure_preserves_leadership_and_identifies_control_stage(tmp_path) -> None:
    class UnavailableDiscovery:
        async def reconcile(self, after_scope_id, limit, *, fence):
            raise TimeoutError("secret-database-endpoint")

    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'discovery.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            diagnostics = ExtractionDiagnostics(pipeline_configured=True, external_worker=False)
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding(
                        SOURCE_WINDOW_TRIGGER_NAME,
                        "memory",
                        SpawnArtifactProcessingWorkerLauncher(_worker_crash),
                        pending_provider=UnavailableDiscovery(),
                    ),
                ),
                lease_mode="single-process",
            ) as supervisor:
                snapshot = diagnostics.snapshot(ArtifactProcessingSupervisors((supervisor,)))
                assert snapshot.background.role == "leader"
                assert snapshot.background.state == "degraded"
                assert snapshot.observation.status == "observed"
                assert snapshot.observation.last_failure is not None
                assert snapshot.observation.last_failure.code == "scope_discovery_failed"
                assert snapshot.observation.last_failure.stage == "scope_discovery"
                assert snapshot.observation.last_success_at is None
                assert "secret-database-endpoint" not in snapshot.model_dump_json()

    asyncio.run(scenario())
