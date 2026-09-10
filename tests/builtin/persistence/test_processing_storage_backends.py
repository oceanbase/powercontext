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

"""Live backend contracts; each run owns a separate database/runtime path."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import Column, DateTime, MetaData, Table, func, insert, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.errors import ArtifactProcessingLeadershipLostError
from powercontext.builtin.persistence.oceanbase.profile import (
    OceanBaseConfig,
    OceanBaseProfile,
    _register_official_dialect,
)
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.processing_migration import (
    apply_processing_migration,
    assert_processing_schema_ready,
    plan_processing_migration,
    verify_processing_migration,
)
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.supervision import (
    ArtifactProcessingBindingStateRepository,
    ArtifactProcessingLeaseRepository,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_BINDING_STATES_TABLE as STATES,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_INTENTS_TABLE as INTENTS,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_MIGRATION_RECEIPTS_TABLE as RECEIPTS,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_PENDING_TABLE as PENDING,
)
from powercontext.builtin.persistence.tables import (
    SCOPE_TABLES,
    SHARED_TABLES,
    identity_string,
)
from powercontext.builtin.persistence.tables import (
    SOURCE_JOURNAL_HEADS_TABLE as HEADS,
)
from powercontext.builtin.sources import SourceCursor
from powercontext.limits import MAX_BINDING_NAME_LENGTH

BINDING = "topic-memory-source-window"
MANIFEST = {"mode": "global", "bindings": {BINDING: "topic-memory"}}


class _SimulatedCrash(Exception):
    pass


@asynccontextmanager
async def _storage_profile(backend: str, path: Path) -> AsyncIterator[OceanBaseProfile | SeekDBProfile]:
    legacy_state = Table(
        STATES.name,
        MetaData(),
        Column("binding_name", identity_string(MAX_BINDING_NAME_LENGTH), primary_key=True),
        Column("last_auto_wave_completed_at", DateTime(timezone=False)),
    )
    tables = (*SCOPE_TABLES, *tuple(table for table in SHARED_TABLES if table is not STATES), legacy_state)
    if backend == "seekdb":
        pytest.importorskip("pylibseekdb")
        # The embedded engine's Unix socket must fit sockaddr_un. A deeply
        # nested pytest base path can exceed that limit even for a short suffix.
        with TemporaryDirectory(prefix="pc-storage-", dir="/tmp") as directory:
            async with SeekDBProfile.open(SeekDBConfig(path=Path(directory) / "db"), tables=tables) as profile:
                yield profile
        return
    value = os.environ.get("POWERCONTEXT_PROCESSING_STORAGE_URL")
    if not value:
        pytest.skip("POWERCONTEXT_PROCESSING_STORAGE_URL is needed for the isolated live OceanBase contract")
    base = make_url(value).update_query_dict({"charset": "utf8mb4"})
    database = "pc_storage_" + uuid4().hex[:12]
    _register_official_dialect()
    engine = create_async_engine(base, connect_args={"init_command": "SET autocommit = 0"}, hide_parameters=True)
    try:
        async with engine.begin() as connection:
            await connection.exec_driver_sql(f"CREATE DATABASE {database} CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
        config = OceanBaseConfig(url=SecretStr(base.set(database=database).render_as_string(hide_password=False)))
        async with OceanBaseProfile.open(config, tables=tables) as profile:
            yield profile
    finally:
        # Only the uniquely named database created by this fixture is removed.
        async with engine.begin() as connection:
            await connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {database}")
        await engine.dispose()


@pytest.mark.parametrize("backend", ("oceanbase", "seekdb"))
def test_live_intents_fencing_and_resumable_legacy_migration(backend: str, tmp_path: Path) -> None:
    async def scenario() -> None:
        async with _storage_profile(backend, tmp_path) as profile:
            leases, intents = ArtifactProcessingLeaseRepository(), ArtifactProcessingIntentRepository()
            checkpoint = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)
            async with profile.database.transaction() as connection:
                old = await leases.start_single_process_term(connection, "old-holder")
                await connection.execute(insert(HEADS).values(scope_id="untracked", position=3))
                await SourceCursorRepository().save(
                    connection, "untracked", BINDING, SourceCursor(sequence=1), expected_generation=None
                )
                await connection.execute(
                    insert(PENDING).values(binding_name=BINDING, scope_id="untracked", source_through=3)
                )
                # The metadata includes new columns; use only old-column SQL
                # against this deliberately pre-migration table.
                await connection.execute(
                    insert(STATES).values(binding_name=BINDING, last_auto_wave_completed_at=checkpoint)
                )
                plan = await plan_processing_migration(connection, config_manifest=MANIFEST)
                assert len(plan.missing_state_columns) == 4
            while True:
                async with profile.database.transaction() as connection:
                    step = await apply_processing_migration(connection, config_manifest=MANIFEST)
                if step.phase == "backfill":
                    break
            with pytest.raises(_SimulatedCrash):
                async with profile.database.transaction() as connection:
                    await apply_processing_migration(connection, config_manifest=MANIFEST, batch_size=1)
                    raise _SimulatedCrash
            async with profile.database.transaction() as connection:
                assert await connection.scalar(select(func.count()).select_from(INTENTS)) == 0
                assert await connection.scalar(select(func.count()).select_from(RECEIPTS)) == 0
            for _ in range(20):
                async with profile.database.transaction() as connection:
                    step = await apply_processing_migration(connection, config_manifest=MANIFEST, batch_size=1)
                if step.complete:
                    break
            assert step.complete
            async with profile.database.transaction() as connection:
                await assert_processing_schema_ready(connection, MANIFEST)
                assert (await verify_processing_migration(connection, config_manifest=MANIFEST)).ready
                recovered = await intents.load(connection, "untracked", BINDING)
                assert recovered is not None and recovered.requested_generation == 1
                state = await ArtifactProcessingBindingStateRepository().load(connection, BINDING)
                assert state is not None and state.last_schedule_checkpoint_at == checkpoint
                if backend == "oceanbase":
                    lease = await leases.try_acquire(connection, "new-holder", 60)
                else:
                    lease = await leases.start_single_process_term(connection, "new-holder")
                assert lease is not None and lease.supervisor_generation > old.supervisor_generation
            mode = "oceanbase" if backend == "oceanbase" else "single-process"
            with pytest.raises(ArtifactProcessingLeadershipLostError):
                async with profile.database.transaction() as connection:
                    await leases.require_fence(connection, old.fence("single-process"))
                    await intents.request(connection, "stale", BINDING)
            async with profile.database.transaction() as connection:
                await leases.require_fence(connection, lease.fence(mode))
                await intents.acknowledge(connection, "untracked", BINDING, recovered.requested_generation)
                assert await intents.load(connection, "stale", BINDING) is None
            barrier = asyncio.Barrier(2)

            async def request():
                async with profile.database.transaction() as connection:
                    await barrier.wait()
                    return await intents.request(connection, "concurrent", BINDING)

            first, second = await asyncio.gather(request(), request())
            assert first.pending_sequence == second.pending_sequence
            async with profile.database.transaction() as connection:
                current = await intents.load(connection, "concurrent", BINDING)
                assert current is not None and current.requested_generation == 2
                await intents.mark_dirty(connection, "concurrent", BINDING)
                await intents.mark_dirty(connection, "concurrent", BINDING)
                await leases.require_fence(connection, lease.fence(mode))
                acknowledged = await intents.acknowledge(connection, "concurrent", BINDING, 1, clean_generation=1)
                assert (acknowledged.requested_generation, acknowledged.handled_generation) == (2, 1)
                assert (acknowledged.dirty_generation, acknowledged.clean_generation) == (2, 1)
                states = ArtifactProcessingBindingStateRepository()
                scan = await states.start_scan(connection, BINDING)
                admitted = await intents.admit(connection, "concurrent", BINDING, scan.scan_generation)
                assert admitted.requested_generation == 2
                await intents.acknowledge(connection, "concurrent", BINDING, 2, clean_generation=2)
                duplicate = await intents.admit(connection, "concurrent", BINDING, scan.scan_generation)
                assert duplicate.requested_generation == duplicate.handled_generation == 2
                await states.finish_scan(connection, BINDING)
                finished = await states.load(connection, BINDING)
                assert finished is not None and not finished.scan_in_progress

    asyncio.run(scenario())
