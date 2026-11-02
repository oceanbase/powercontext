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
from pydantic import ValidationError
from sqlalchemy import insert, inspect

from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import ArtifactProcessingBindingStateRepository, database_utc_now
from powercontext.builtin.persistence.tables import ARTIFACT_PROCESSING_INTENTS_TABLE as INTENTS
from powercontext.builtin.persistence.tables import SHARED_TABLES

BINDING = "topic-memory-source-window"
START = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)


def test_pending_count_survives_restart_and_partial_duplicate_ack(tmp_path):
    async def scenario():
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'pending.db'}")
        intents = ArtifactProcessingIntentRepository()
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            for _ in range(3):
                async with profile.database.transaction() as connection:
                    await intents.request(connection, "scope", BINDING)
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            async with profile.database.transaction(consistent_snapshot=True) as connection:
                before = await database_utc_now(connection)
                initial = await intents.pending_observation(connection, BINDING)
                after = await database_utc_now(connection)
                assert initial.pending_count == 1
                assert before <= initial.observed_at <= after
                assert initial.observed_at.tzinfo is None
                with pytest.raises(ValidationError, match="frozen"):
                    initial.pending_count = 2
                table_names = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
                assert "pc_artifact_processing_requests" not in table_names
            for generation in (1, 2, 3):
                async with profile.database.transaction() as connection:
                    await intents.acknowledge(connection, "scope", BINDING, generation)
                    await intents.acknowledge(connection, "scope", BINDING, generation)
                    snapshot = await intents.pending_observation(connection, BINDING)
                    assert snapshot.pending_count == int(generation < 3)

    asyncio.run(scenario())


def test_existing_intent_counters_need_no_timestamp_backfill(tmp_path):
    async def scenario():
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'existing.db'}")
        intents = ArtifactProcessingIntentRepository()
        async with (
            SQLiteProfile.open(config, tables=SHARED_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            await connection.execute(
                insert(INTENTS).values(
                    binding_name=BINDING,
                    scope_id="existing",
                    pending_sequence=1,
                    requested_generation=3,
                    handled_generation=1,
                )
            )
        async with (
            SQLiteProfile.open(config, tables=SHARED_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            assert (await intents.pending_observation(connection, BINDING)).pending_count == 1
            latest = await intents.request(connection, "existing", BINDING)
            assert latest.requested_generation == 4
            for generation in (2, 3, 4):
                await intents.acknowledge(connection, "existing", BINDING, generation)
                assert (await intents.pending_observation(connection, BINDING)).pending_count == int(generation < 4)

    asyncio.run(scenario())


def test_automatic_admission_and_transaction_rollback_preserve_pending_count():
    async def scenario():
        intents, states = ArtifactProcessingIntentRepository(), ArtifactProcessingBindingStateRepository()
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                await intents.mark_dirty(connection, "auto", BINDING)
                assert (await intents.pending_observation(connection, BINDING)).pending_count == 0
                scan = await states.start_scan(connection, BINDING, START)
                await intents.admit(connection, "auto", BINDING, scan.scan_generation)
                repeated = await intents.admit(connection, "auto", BINDING, scan.scan_generation)
                assert repeated.requested_generation == 1
                assert (await intents.pending_observation(connection, BINDING)).pending_count == 1
            with pytest.raises(RuntimeError):
                async with profile.database.transaction() as connection:
                    await intents.request(connection, "auto", BINDING)
                    await intents.request(connection, "rolled-back", BINDING)
                    assert (await intents.pending_observation(connection, BINDING)).pending_count == 2
                    raise RuntimeError
            async with profile.database.transaction() as connection:
                intent = await intents.load(connection, "auto", BINDING)
                assert intent is not None and intent.requested_generation == 1
                assert await intents.load(connection, "rolled-back", BINDING) is None
            with pytest.raises(RuntimeError):
                async with profile.database.transaction() as connection:
                    await intents.acknowledge(connection, "auto", BINDING, 1)
                    assert (await intents.pending_observation(connection, BINDING)).pending_count == 0
                    raise RuntimeError
            async with profile.database.transaction() as connection:
                intent = await intents.load(connection, "auto", BINDING)
                assert intent is not None and intent.handled_generation == 0
                assert (await intents.pending_observation(connection, BINDING)).pending_count == 1

    asyncio.run(scenario())


def test_pending_count_covers_large_backlog_and_isolates_bindings():
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
                        "requested_generation": 100,
                    }
                    for i in range(2000)
                ],
            )
            intents = ArtifactProcessingIntentRepository()
            # Coalesced generations count once, completed and merely dirty rows do not count.
            await intents.acknowledge(connection, "0", BINDING, 100)
            await intents.mark_dirty(connection, "0", BINDING)
            await connection.execute(
                insert(INTENTS).values(
                    binding_name="other-binding", scope_id="other", pending_sequence=2001, requested_generation=1
                )
            )
            first_page = await intents.scan(connection, BINDING, limit=10, requested_only=True)
            assert len(first_page) == 10
            assert (await intents.pending_observation(connection, BINDING)).pending_count == 1999
            assert (await intents.pending_observation(connection, "other-binding")).pending_count == 1
            assert (await intents.pending_observation(connection, "empty-binding")).pending_count == 0

    asyncio.run(scenario())


def test_interleaved_ack_and_request_keep_new_generation_pending(tmp_path):
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
                assert (await intents.pending_observation(connection, BINDING)).pending_count == 1

    asyncio.run(scenario())


def test_concurrent_duplicate_acknowledgements_preserve_pending_count(tmp_path):
    async def scenario():
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'duplicate.db'}", busy_timeout_ms=10_000)
        intents = ArtifactProcessingIntentRepository()
        async with (
            SQLiteProfile.open(config, tables=SHARED_TABLES) as first,
            SQLiteProfile.open(config, tables=SHARED_TABLES) as second,
        ):
            async with first.database.transaction() as connection:
                await intents.request(connection, "scope", BINDING)
                await intents.request(connection, "scope", BINDING)
            for generation in (1, 2):
                barrier = asyncio.Barrier(2)

                async def acknowledge(profile, accepted_generation, start):
                    async with profile.database.transaction() as connection:
                        await start.wait()
                        await intents.acknowledge(connection, "scope", BINDING, accepted_generation)

                await asyncio.gather(acknowledge(first, generation, barrier), acknowledge(second, generation, barrier))
                async with first.database.transaction(consistent_snapshot=True) as connection:
                    current = await intents.load(connection, "scope", BINDING)
                    assert current is not None and current.handled_generation == generation
                    assert (await intents.pending_observation(connection, BINDING)).pending_count == int(generation < 2)

    asyncio.run(scenario())
