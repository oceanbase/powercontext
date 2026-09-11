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
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr
from sqlalchemy import insert, select

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.processing_migration import (
    ProcessingSchemaNotReadyError,
    apply_processing_migration,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import ArtifactProcessingFence
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_PENDING_TABLE,
    ARTIFACT_PROCESSING_SCHEMA_TABLE,
    SHARED_TABLES,
    SOURCE_JOURNAL_HEADS_TABLE,
)
from powercontext.builtin.runtime import BuiltinConfig, InferenceConfig, RuntimeConfig, composition
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    ArtifactProcessingSupervisorStatus,
)
from powercontext.builtin.runtime.composition import (
    BuiltinConfigurationError,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.family_processing import FamilyWorkerSpec, run_family_worker
from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingWorkAssignment
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.runtime.topic_memory_processing import TopicMemoryWorkerSpec, run_topic_memory_worker

_FAMILIES = {"memory", "topic-memory", "experience", "profile"}
_BINDING = "topic-memory-source-window"
_OCEANBASE = OceanBaseConfig(
    url=SecretStr("mysql+aoceanbase://root%40test@127.0.0.1:2881/composition_unused?charset=utf8mb4")
)


def _sqlite(path: Path) -> SQLiteConfig:
    return SQLiteConfig(url=f"sqlite+aiosqlite:///{path}")


async def _seed_partial_migration(config: BuiltinConfig) -> None:
    assert isinstance(config.database, SQLiteConfig)
    async with (
        SQLiteProfile.open(config.database, tables=SHARED_TABLES) as profile,
        profile.database.transaction() as connection,
    ):
        progress = await apply_processing_migration(connection, config_manifest=canonical_processing_manifest(config))
        assert not progress.complete


def _assignment(family: str) -> ArtifactProcessingWorkAssignment:
    bindings = canonical_processing_manifest(BuiltinConfig())["bindings"]
    return ArtifactProcessingWorkAssignment(
        binding_name=next(binding for binding, value in bindings.items() if value == family),
        scope_id="project",
        artifact_family=family,
        claimed_request_generation=1,
        fence=ArtifactProcessingFence(
            supervisor_group="global", holder_id="stopped-parent", supervisor_generation=1, lease_mode="single-process"
        ),
        worker_id="composition-worker",
    )


def test_fresh_sqlite_bootstraps_and_reopens_after_business_data_is_written(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(database=_sqlite(tmp_path / "fresh.db"))
        async with open_builtin_contexts(config) as contexts, contexts.database.transaction() as connection:
            marker = (await connection.execute(select(ARTIFACT_PROCESSING_SCHEMA_TABLE))).mappings().one()
            assert marker["phase"] == "complete"
            assert marker["migration_id"] == "fresh"
            await connection.execute(insert(SOURCE_JOURNAL_HEADS_TABLE).values(scope_id="project", position=1))
        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            assert runtime.artifact_processing_supervisor is None

    asyncio.run(scenario())


def test_nonempty_legacy_sqlite_is_rejected_without_importing_or_discarding_work(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = _sqlite(tmp_path / "legacy.db")
        async with (
            SQLiteProfile.open(database, tables=SHARED_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            await connection.execute(insert(SOURCE_JOURNAL_HEADS_TABLE).values(scope_id="project", position=2))
            await connection.execute(
                insert(ARTIFACT_PROCESSING_PENDING_TABLE).values(
                    binding_name=_BINDING, scope_id="project", source_through=2, flush_generation=1
                )
            )
        with pytest.raises(ProcessingSchemaNotReadyError, match="offline processing migration"):
            async with open_builtin_runtime(BuiltinConfig(database=database), scheduler_path=tmp_path / "scheduler.db"):
                pytest.fail("legacy work must not be accepted as a fresh database")
        async with (
            SQLiteProfile.open(database, tables=SHARED_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            assert await connection.scalar(select(ARTIFACT_PROCESSING_SCHEMA_TABLE.c.singleton)) is None
            pending = (await connection.execute(select(ARTIFACT_PROCESSING_PENDING_TABLE))).mappings().one()
            assert pending["source_through"] == 2
            assert pending["flush_generation"] == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("role", ["api", "background"])
def test_partial_migration_refuses_split_role_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: Literal["api", "background"]
) -> None:
    async def scenario() -> None:
        database = _sqlite(tmp_path / "partial.db")
        await _seed_partial_migration(BuiltinConfig(database=database))

        # Exercise the OceanBase role's real startup gate against a real SQLite
        # transaction. A failed gate must run before dialect-specific indexes.
        @asynccontextmanager
        async def local_profile(_config, *, tables):
            async with SQLiteProfile.open(database, tables=tables) as profile:
                yield profile

        monkeypatch.setattr(composition.OceanBaseProfile, "open", local_profile)
        config = BuiltinConfig(database=_OCEANBASE, runtime=RuntimeConfig(artifact_processing_role=role))
        with pytest.raises(ProcessingSchemaNotReadyError, match="not ready"):
            async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db"):
                pytest.fail("split-role startup must reject a partial migration")

    asyncio.run(scenario())


@pytest.mark.parametrize("family", sorted(_FAMILIES))
def test_partial_migration_refuses_each_worker_entrypoint(tmp_path: Path, family: str) -> None:
    config = BuiltinConfig(
        database=_sqlite(tmp_path / "partial-worker.db"), inference=InferenceConfig(generation_model="test")
    )
    asyncio.run(_seed_partial_migration(config))
    with pytest.raises(ProcessingSchemaNotReadyError, match="not ready"):
        if family == "topic-memory":
            run_topic_memory_worker(TopicMemoryWorkerSpec(config=config), _assignment(family))
        else:
            run_family_worker(FamilyWorkerSpec(config=config), _assignment(family))


@pytest.mark.parametrize("change", ["mode", "capabilities"])
def test_startup_rejects_a_different_completed_deployment_manifest(tmp_path: Path, change: str) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=_sqlite(tmp_path / "identity.db"), inference=InferenceConfig(generation_model="test")
        )
        async with open_builtin_contexts(config):
            pass
        settings = (
            RuntimeConfig(artifact_processing_supervisor_mode="dedicated")
            if change == "mode"
            else RuntimeConfig(artifact_processing_families=("memory",))
        )
        changed = config.model_copy(update={"runtime": settings})
        with pytest.raises(ProcessingSchemaNotReadyError, match="completed maintenance manifest"):
            async with open_builtin_runtime(changed, scheduler_path=tmp_path / "scheduler.db"):
                pytest.fail("ownership identity changes require explicit maintenance")

    asyncio.run(scenario())


def test_schedule_and_worker_budgets_can_change_without_maintenance(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=_sqlite(tmp_path / "budget.db"), inference=InferenceConfig(generation_model="test")
        )
        async with open_builtin_contexts(config):
            pass
        changed = config.model_copy(
            update={
                "runtime": RuntimeConfig(
                    memory_schedule_seconds=90, memory_max_workers=2, memory_worker_timeout_seconds=120
                )
            }
        )
        async with open_builtin_contexts(changed):
            pass

    asyncio.run(scenario())


@pytest.mark.parametrize("family", ["experience", "skill"])
def test_dream_injection_requires_a_reconstructible_worker(tmp_path: Path, family: str) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=_sqlite(tmp_path / "dream-injection.db"),
            inference=InferenceConfig(generation_model="test"),
            runtime=RuntimeConfig(artifact_processing_families=(family,)),
        )
        with pytest.raises(BuiltinConfigurationError, match="child-reconstructible inference resources"):
            async with open_builtin_runtime(config, dream_generator=AsyncMock()):
                pytest.fail("a spawned Worker must not silently ignore an injected Dream generator")

    asyncio.run(scenario())


@pytest.mark.parametrize("role", ["all", "api"])
@pytest.mark.parametrize("duplicate", ["binding_name", "artifact_family", "config_prefix"])
def test_duplicate_custom_registration_is_rejected_for_every_role(role: Literal["all", "api"], duplicate: str) -> None:
    first = ArtifactProcessingBinding(
        binding_name="custom-first", artifact_family="custom-first", config_prefix="CUSTOM_FIRST", launcher=AsyncMock()
    )
    second = ArtifactProcessingBinding(
        binding_name=first.binding_name if duplicate == "binding_name" else "custom-second",
        artifact_family=first.artifact_family if duplicate == "artifact_family" else "custom-second",
        config_prefix=first.config_prefix if duplicate == "config_prefix" else "CUSTOM_SECOND",
        launcher=AsyncMock(),
    )
    config = BuiltinConfig(database=_OCEANBASE, runtime=RuntimeConfig(artifact_processing_role=role))
    with pytest.raises(BuiltinConfigurationError, match="one matching registration"):
        composition._artifact_processing_bindings(config, cast(RelationalContexts, SimpleNamespace()), (first, second))


def test_api_can_declare_topic_processing_without_constructing_models_or_controllers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        model_opener = AsyncMock(side_effect=AssertionError("API attempted to construct an inference model"))
        monkeypatch.setattr(composition, "_open_pydantic_ai_model", model_opener)
        config = BuiltinConfig(
            database=_OCEANBASE,
            runtime=RuntimeConfig(artifact_processing_role="api", artifact_processing_families=("topic-memory",)),
        )
        contexts = cast(RelationalContexts, SimpleNamespace())
        await composition.preflight_builtin_runtime(config)
        bindings = composition._artifact_processing_bindings(config, contexts, ())
        assert not bindings
        assert composition._topic_memory_processing_available(config, bindings)
        async with composition._open_artifact_processing_supervisor(config, contexts, bindings) as controllers:
            assert controllers is None
        model_opener.assert_not_called()

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["global", "dedicated"])
def test_composition_opens_one_global_or_four_dedicated_controllers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Literal["global", "dedicated"]
) -> None:
    async def scenario() -> None:
        launch = AsyncMock(side_effect=AssertionError("an empty deployment must not start a worker"))
        monkeypatch.setattr(composition.SpawnArtifactProcessingWorkerLauncher, "start", launch)
        config = BuiltinConfig(
            database=_sqlite(tmp_path / "controllers.db"),
            inference=InferenceConfig(generation_model="test"),
            runtime=RuntimeConfig(artifact_processing_supervisor_mode=mode),
        )
        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            group = runtime.artifact_processing_supervisor
            assert group is not None
            assert group.status == ArtifactProcessingSupervisorStatus.LEADER
            assert set(group.family_status) == _FAMILIES
            assert len(group.supervisors) == (1 if mode == "global" else 4)
            fences = [controller.fence for controller in group.supervisors]
            assert all(fence is not None for fence in fences)
            assert {fence.supervisor_group for fence in fences if fence is not None} == (
                {"global"} if mode == "global" else {f"artifact:{family}" for family in _FAMILIES}
            )
            assert all(status["used_workers"] == 0 for status in group.family_status.values())
        launch.assert_not_called()

    asyncio.run(scenario())
