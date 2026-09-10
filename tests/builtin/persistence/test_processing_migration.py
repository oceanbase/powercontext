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
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError

from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.errors import ArtifactProcessingLeadershipLostError
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.processing_migration import (
    ProcessingSchemaNotReadyError,
    apply_processing_migration,
    assert_processing_schema_ready,
    bootstrap_processing_schema,
    plan_processing_migration,
    verify_processing_migration,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import (
    ArtifactProcessingBindingStateRepository,
    ArtifactProcessingLeaseRepository,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_AUTO_WAVE_TARGETS_TABLE as WAVES,
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
    ARTIFACT_PROCESSING_SCHEMA_TABLE as SCHEMA,
)
from powercontext.builtin.persistence.tables import (
    SHARED_TABLES,
)
from powercontext.builtin.persistence.tables import (
    SOURCE_JOURNAL_HEADS_TABLE as HEADS,
)
from powercontext.builtin.persistence.tables import (
    TOPIC_MEMORY_PROCESSING_TARGETS_TABLE as TARGETS,
)
from powercontext.builtin.sources import SourceCursor

BINDING = "topic-memory-source-window"
MANIFEST = {"mode": "global", "bindings": {BINDING: "topic-memory"}, "legacy_automatic_bindings": [BINDING]}
CHECKPOINT = datetime(2026, 1, 1, 12, 0, tzinfo=UTC).replace(tzinfo=None)


async def _legacy(profile):
    async with profile.database.transaction() as connection:
        await connection.exec_driver_sql("DROP TABLE pc_artifact_processing_binding_states")
        await connection.exec_driver_sql(
            "CREATE TABLE pc_artifact_processing_binding_states (binding_name VARCHAR(128) PRIMARY KEY NOT NULL, last_auto_wave_completed_at DATETIME)"
        )
        await connection.exec_driver_sql(
            "INSERT INTO pc_artifact_processing_binding_states VALUES (?, ?)", (BINDING, CHECKPOINT.isoformat(" "))
        )
        lease = await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "old-holder")
        for scope in ("flush", "untracked", "frozen", "completed", "missing-pending"):
            await connection.execute(insert(HEADS).values(scope_id=scope, position=5))
            await SourceCursorRepository().save(
                connection, scope, BINDING, SourceCursor(sequence=1), expected_generation=None
            )
            if scope != "missing-pending":
                await connection.execute(
                    insert(PENDING).values(
                        binding_name=BINDING, scope_id=scope, source_through=5, flush_generation=int(scope == "flush")
                    )
                )
        for scope in ("frozen", "completed"):
            await connection.execute(
                insert(WAVES).values(
                    wave_id="00000000-0000-4000-8000-000000000001",
                    binding_name=BINDING,
                    scope_id=scope,
                    source_through=1 if scope == "completed" else 3,
                    completed=scope == "completed",
                )
            )
        return lease


async def _finish(profile, manifest=MANIFEST, migration_id="rfc1515"):
    for _ in range(50):
        async with profile.database.transaction() as connection:
            result = await apply_processing_migration(
                connection, config_manifest=manifest, migration_id=migration_id, batch_size=1
            )
        if result.complete:
            return
    pytest.fail("migration did not finish within its bounded fixture")


def test_legacy_migration_preserves_targets_untracked_calls_cursor_and_remaining_interval(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'migration.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            old = await _legacy(profile)
            async with profile.database.transaction() as connection:
                plan = await plan_processing_migration(connection, config_manifest=MANIFEST)
                assert plan.required and len(plan.missing_state_columns) == 4
                with pytest.raises(ProcessingSchemaNotReadyError):
                    await bootstrap_processing_schema(connection, MANIFEST)
            await _finish(profile)
            async with profile.database.transaction() as connection:
                await assert_processing_schema_ready(connection, MANIFEST)
                assert (await verify_processing_migration(connection, config_manifest=MANIFEST)).ready
                intents = {
                    row.scope_id: row for row in await ArtifactProcessingIntentRepository().scan(connection, BINDING)
                }
                assert intents["flush"].requested_generation == 1
                assert intents["untracked"].requested_generation == 1
                assert intents["frozen"].requested_generation == 1
                assert intents["completed"].requested_generation == 0
                assert intents["missing-pending"].requested_generation == 0
                targets = {row["scope_id"]: row for row in (await connection.execute(select(TARGETS))).mappings()}
                assert targets["frozen"]["source_through"] == 3
                assert targets["untracked"]["source_through"] == 5
                assert targets["flush"]["captured_flush_generation"] == 1
                state = await ArtifactProcessingBindingStateRepository().load(connection, BINDING)
                assert state is not None and state.last_schedule_checkpoint_at == CHECKPOINT
                assert await connection.scalar(select(func.count()).select_from(WAVES)) == 0
                cursor = await SourceCursorRepository().load(connection, "frozen", BINDING)
                assert cursor is not None and cursor.cursor.sequence == cursor.generation == 1
                with pytest.raises(ArtifactProcessingLeadershipLostError):
                    await ArtifactProcessingLeaseRepository().require_fence(connection, old.fence("single-process"))
            # Reopen the database and apply again: no requested++ and no sequence reuse.
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            await _finish(profile)
            async with profile.database.transaction() as connection:
                after = {
                    row.scope_id: row for row in await ArtifactProcessingIntentRepository().scan(connection, BINDING)
                }
                assert after == intents

    asyncio.run(scenario())


def test_receipt_failure_rolls_back_request_and_target_before_resuming(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'receipt.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            await _legacy(profile)
            while True:
                async with profile.database.transaction() as connection:
                    step = await apply_processing_migration(connection, config_manifest=MANIFEST)
                if step.phase == "backfill":
                    break
            async with profile.database.transaction() as connection:
                await connection.exec_driver_sql(
                    "CREATE TRIGGER reject_receipt BEFORE INSERT ON pc_artifact_processing_migration_receipts BEGIN SELECT RAISE(ABORT, 'receipt failed'); END"
                )
            with pytest.raises(IntegrityError, match="receipt failed"):
                async with profile.database.transaction() as connection:
                    await apply_processing_migration(connection, config_manifest=MANIFEST, batch_size=10)
            async with profile.database.transaction() as connection:
                assert await connection.scalar(select(func.count()).select_from(INTENTS)) == 0
                assert await connection.scalar(select(func.count()).select_from(TARGETS)) == 0
                assert await connection.scalar(select(func.count()).select_from(RECEIPTS)) == 0
                assert await connection.scalar(select(func.count()).select_from(WAVES)) == 2
                with pytest.raises(ProcessingSchemaNotReadyError):
                    await assert_processing_schema_ready(connection, MANIFEST)
                await connection.exec_driver_sql("DROP TRIGGER reject_receipt")
            # Commit one page, then resume from the receipt anti-join.
            async with profile.database.transaction() as connection:
                await apply_processing_migration(connection, config_manifest=MANIFEST, batch_size=1)
            await _finish(profile)
            async with profile.database.transaction() as connection:
                intent = await ArtifactProcessingIntentRepository().load(connection, "flush", BINDING)
                assert intent is not None and intent.requested_generation == 1
                assert await connection.scalar(select(func.count()).select_from(RECEIPTS)) == 5

    asyncio.run(scenario())


def test_mode_switch_invalidates_old_permanent_lease_without_reimporting_work() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                await bootstrap_processing_schema(connection, MANIFEST)
                lease = await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "same-holder")
                before = await ArtifactProcessingIntentRepository().request(connection, "a", BINDING)
            dedicated = {**MANIFEST, "mode": "dedicated"}
            await _finish(profile, dedicated, "switch-dedicated")
            async with profile.database.transaction() as connection:
                with pytest.raises(ArtifactProcessingLeadershipLostError):
                    await ArtifactProcessingLeaseRepository().require_fence(connection, lease.fence("single-process"))
                assert await ArtifactProcessingIntentRepository().load(connection, "a", BINDING) == before
                with pytest.raises(ProcessingSchemaNotReadyError):
                    await assert_processing_schema_ready(connection, MANIFEST)
                await assert_processing_schema_ready(connection, {**dedicated, "legacy_automatic_bindings": []})
                term = await ArtifactProcessingLeaseRepository().start_single_process_term(
                    connection, "new-holder", supervisor_group="artifact:topic-memory"
                )
            await _finish(profile, MANIFEST, "switch-global")
            async with profile.database.transaction() as connection:
                with pytest.raises(ArtifactProcessingLeadershipLostError):
                    await ArtifactProcessingLeaseRepository().require_fence(connection, term.fence("single-process"))
                assert await ArtifactProcessingIntentRepository().load(connection, "a", BINDING) == before
                assert (await verify_processing_migration(connection, config_manifest=MANIFEST)).ready

    asyncio.run(scenario())


def test_partial_schema_column_addition_is_resumed_before_data_backfill() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            await _legacy(profile)
            async with profile.database.transaction() as connection:
                await apply_processing_migration(connection, config_manifest=MANIFEST)
            # Mimic MySQL's durable DDL followed by a process crash before its
            # next stage update: column exists, but no data has been migrated.
            async with profile.database.transaction() as connection:
                await connection.exec_driver_sql(
                    "ALTER TABLE pc_artifact_processing_binding_states ADD COLUMN last_schedule_checkpoint_at DATETIME NULL"
                )
            await _finish(profile)
            async with profile.database.transaction() as connection:
                assert (await verify_processing_migration(connection, config_manifest=MANIFEST)).ready
                checkpoint = await connection.scalar(select(STATES.c.last_schedule_checkpoint_at))
                assert checkpoint == CHECKPOINT
                assert await connection.scalar(select(SCHEMA.c.phase)) == "complete"

    asyncio.run(scenario())
