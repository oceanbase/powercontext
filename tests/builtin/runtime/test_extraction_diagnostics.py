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


def _model_timeout(_assignment) -> None:
    raise InferenceTimeoutError("secret-model-input", 0.01)


def _worker_crash(_assignment) -> None:
    os._exit(1)


@pytest.mark.parametrize(
    ("entrypoint", "category"),
    [(_model_timeout, "model_timeout"), (_worker_crash, "worker_crash")],
)
def test_extraction_diagnostics_observes_spawned_worker_failure_and_stopped_supervisor(
    tmp_path, entrypoint, category
) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'diagnostics.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            diagnostics = ExtractionDiagnostics(model_configured=True, external_worker=False)
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
                    ),
                ),
                lease_mode="single-process",
                retry_base_seconds=60,
            ) as supervisor:
                supervisors = ArtifactProcessingSupervisors((supervisor,))
                async with asyncio.timeout(20):
                    while diagnostics.snapshot(supervisors).last_result == "unverified":  # noqa: ASYNC110
                        await asyncio.sleep(0.01)
                snapshot = diagnostics.snapshot(supervisors)
                assert snapshot.worker_status == "leader"
                assert snapshot.automatic_processing_enabled is False
                assert snapshot.last_result == "failed"
                assert snapshot.last_error == category
                assert snapshot.last_error_at is not None
                assert snapshot.last_success_at is None
                assert "secret-model-input" not in snapshot.model_dump_json()
            assert diagnostics.snapshot(supervisors).worker_status == "stopped"

    asyncio.run(scenario())


def test_remote_worker_observations_are_unknown() -> None:
    diagnostics = ExtractionDiagnostics(model_configured=False, external_worker=True)
    snapshot = diagnostics.snapshot(None)
    assert snapshot.worker_status == "external"
    assert snapshot.automatic_processing_enabled is None
    assert snapshot.last_result == "unverified"
    assert snapshot.last_error is None
    assert snapshot.last_success_at is None
