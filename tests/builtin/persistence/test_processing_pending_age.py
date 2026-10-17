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
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError

from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import ArtifactProcessingBindingStateRepository
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_INTENTS_TABLE as INTENTS,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_REQUESTS_TABLE as REQUESTS,
)
from powercontext.builtin.persistence.tables import SHARED_TABLES

BINDING = "topic-memory-source-window"
START = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)


def test_pending_age_survives_restart_and_tracks_each_partial_ack(tmp_path, monkeypatch):
    async def scenario():
        now = START

        async def clock(_connection):
            return now

        monkeypatch.setattr("powercontext.builtin.persistence.supervision.database_utc_now", clock)
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'age.db'}")
        intents = ArtifactProcessingIntentRepository()
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            for generation in range(3):
                now = START + timedelta(seconds=generation)
                async with profile.database.transaction() as connection:
                    await intents.request(connection, "scope", BINDING)
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            async with profile.database.transaction(consistent_snapshot=True) as connection:
                initial = await intents.pending_observation(connection, BINDING)
                assert (initial.pending_count, initial.unknown_count, initial.oldest_requested_at) == (1, 0, START)
            for generation in (1, 2, 3):
                async with profile.database.transaction() as connection:
                    await intents.acknowledge(connection, "scope", BINDING, generation)
                    await intents.acknowledge(connection, "scope", BINDING, generation)
                    snapshot = await intents.pending_observation(connection, BINDING)
                    assert snapshot.oldest_requested_at == (
                        START + timedelta(seconds=generation) if generation < 3 else None
                    )
                    assert snapshot.pending_count == int(generation < 3)
                    assert await connection.scalar(select(func.count()).select_from(REQUESTS)) == 3 - generation

    asyncio.run(scenario())


def test_additive_upgrade_preserves_unknown_legacy_age_until_acked(tmp_path):
    async def scenario():
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")
        intents = ArtifactProcessingIntentRepository()
        async with (
            SQLiteProfile.open(config, tables=tuple(t for t in SHARED_TABLES if t is not REQUESTS)) as profile,
            profile.database.transaction() as connection,
        ):
            await connection.execute(
                insert(INTENTS).values(
                    binding_name=BINDING,
                    scope_id="legacy",
                    pending_sequence=1,
                    requested_generation=3,
                    handled_generation=1,
                )
            )
        async with (
            SQLiteProfile.open(config, tables=SHARED_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            legacy = await intents.pending_observation(connection, BINDING)
            assert (legacy.pending_count, legacy.unknown_count, legacy.oldest_requested_at) == (1, 1, None)
            latest = await intents.request(connection, "legacy", BINDING)
            assert latest.requested_generation == 4
            await intents.acknowledge(connection, "legacy", BINDING, 2)
            mixed = await intents.pending_observation(connection, BINDING)
            assert mixed.unknown_count == 1 and mixed.oldest_requested_at is not None
            await intents.acknowledge(connection, "legacy", BINDING, 3)
            known = await intents.pending_observation(connection, BINDING)
            assert known.unknown_count == 0 and known.oldest_requested_at == mixed.oldest_requested_at
            await intents.acknowledge(connection, "legacy", BINDING, 4)
            empty = await intents.pending_observation(connection, BINDING)
            assert empty.pending_count == empty.unknown_count == 0 and empty.oldest_requested_at is None

    asyncio.run(scenario())


def test_automatic_admission_records_only_new_acceptance_and_rollback_is_atomic():
    async def scenario():
        intents, states = ArtifactProcessingIntentRepository(), ArtifactProcessingBindingStateRepository()
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                await intents.mark_dirty(connection, "auto", BINDING)
                scan = await states.start_scan(connection, BINDING, START)
                await intents.admit(connection, "auto", BINDING, scan.scan_generation)
                await intents.admit(connection, "auto", BINDING, scan.scan_generation)
                assert await connection.scalar(select(func.count()).select_from(REQUESTS)) == 1
                await connection.exec_driver_sql("""
                    CREATE TRIGGER reject_timestamp BEFORE INSERT ON pc_artifact_processing_requests
                    BEGIN SELECT RAISE(ABORT, 'reject timestamp'); END
                """)
            with pytest.raises(IntegrityError, match="reject timestamp"):
                async with profile.database.transaction() as connection:
                    await intents.request(connection, "auto", BINDING)
            async with profile.database.transaction() as connection:
                intent = await intents.load(connection, "auto", BINDING)
                assert intent is not None and intent.requested_generation == 1
                await connection.exec_driver_sql("""
                    CREATE TRIGGER reject_prune BEFORE DELETE ON pc_artifact_processing_requests
                    BEGIN SELECT RAISE(ABORT, 'reject prune'); END
                """)
            with pytest.raises(IntegrityError, match="reject prune"):
                async with profile.database.transaction() as connection:
                    await intents.acknowledge(connection, "auto", BINDING, 1)
            async with profile.database.transaction() as connection:
                intent = await intents.load(connection, "auto", BINDING)
                assert intent is not None and intent.handled_generation == 0
                snapshot = await intents.pending_observation(connection, BINDING)
                assert snapshot.pending_count == 1 and snapshot.unknown_count == 0

    asyncio.run(scenario())


def test_global_oldest_uses_index_beyond_discovery_pages():
    async def scenario():
        async with (
            SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            await connection.execute(
                insert(INTENTS),
                [
                    {
                        "binding_name": BINDING,
                        "scope_id": str(i),
                        "pending_sequence": i + 1,
                        "requested_generation": 1,
                    }
                    for i in range(2000)
                ],
            )
            await connection.execute(
                insert(REQUESTS),
                [
                    {
                        "binding_name": BINDING,
                        "scope_id": str(i),
                        "generation": 1,
                        "requested_at": START + timedelta(seconds=2000 - i),
                    }
                    for i in range(2000)
                ],
            )
            intents = ArtifactProcessingIntentRepository()
            first_page = await intents.scan(connection, BINDING, limit=10, requested_only=True)
            assert "1999" not in {row.scope_id for row in first_page}
            snapshot = await intents.pending_observation(connection, BINDING)
            assert snapshot.pending_count == 2000 and snapshot.unknown_count == 0
            assert snapshot.oldest_requested_at == START + timedelta(seconds=1)
            plan = (
                await connection.exec_driver_sql(
                    "EXPLAIN QUERY PLAN SELECT requested_at FROM pc_artifact_processing_requests "
                    "WHERE binding_name = ? ORDER BY requested_at LIMIT 1",
                    (BINDING,),
                )
            ).all()
            # This protects the global observation's cost, rather than its SQL spelling.
            assert any("COVERING INDEX ix_pc_processing_request_oldest" in row[3] for row in plan)
            assert not any("TEMP B-TREE" in row[3] for row in plan)

    asyncio.run(scenario())


def test_interleaved_ack_and_request_keep_new_generation_timestamp(tmp_path):
    async def scenario():
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'interleaved.db'}", busy_timeout_ms=10_000)
        intents = ArtifactProcessingIntentRepository()
        async with (
            SQLiteProfile.open(config, tables=SHARED_TABLES) as first,
            SQLiteProfile.open(config, tables=SHARED_TABLES) as second,
        ):
            async with first.database.transaction() as connection:
                await intents.request(connection, "scope", BINDING)
            barrier = asyncio.Barrier(2)

            async def acknowledge():
                async with first.database.transaction() as connection:
                    await barrier.wait()
                    await intents.acknowledge(connection, "scope", BINDING, 1)

            async def request():
                async with second.database.transaction() as connection:
                    await barrier.wait()
                    await intents.request(connection, "scope", BINDING)

            await asyncio.gather(acknowledge(), request())
            async with first.database.transaction(consistent_snapshot=True) as connection:
                current = await intents.load(connection, "scope", BINDING)
                assert current is not None and (current.requested_generation, current.handled_generation) == (2, 1)
                rows = (await connection.execute(select(REQUESTS))).mappings().all()
                assert len(rows) == 1 and rows[0]["generation"] == 2
                snapshot = await intents.pending_observation(connection, BINDING)
                assert snapshot.oldest_requested_at == rows[0]["requested_at"]
                assert snapshot.pending_count == 1 and snapshot.unknown_count == 0

    asyncio.run(scenario())
