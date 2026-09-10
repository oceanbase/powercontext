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
from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from powercontext.builtin.persistence.errors import (
    ArtifactProcessingLeadershipLostError,
    InvalidRepositoryArgumentError,
)
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sources import SOURCE_PROCESSING_BINDINGS, SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import (
    ArtifactProcessingBindingStateRepository,
    ArtifactProcessingLeaseRepository,
)
from powercontext.builtin.persistence.tables import SHARED_TABLES, SOURCE_JOURNAL_HEADS_TABLE
from powercontext.sources import SourceMaterialization
from tests.builtin.persistence.contract import SOURCE_ADAPTERS, NoteSource

BINDING = "topic-memory-source-window"


def test_late_requests_and_dirty_survive_partial_and_duplicate_acknowledgement() -> None:
    async def scenario() -> None:
        intents = ArtifactProcessingIntentRepository()
        async with (
            SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            await intents.mark_dirty(connection, "a", BINDING)
            first = await intents.request(connection, "a", BINDING)
            await intents.mark_dirty(connection, "a", BINDING)
            second = await intents.request(connection, "a", BINDING)
            acknowledged = await intents.acknowledge(
                connection, "a", BINDING, first.requested_generation, clean_generation=1
            )
            assert (acknowledged.requested_generation, acknowledged.handled_generation) == (2, 1)
            assert (acknowledged.dirty_generation, acknowledged.clean_generation) == (2, 1)
            duplicate = await intents.acknowledge(
                connection, "a", BINDING, first.requested_generation, clean_generation=2
            )
            assert duplicate == acknowledged
            partial = await intents.acknowledge(connection, "a", BINDING, second.requested_generation)
            assert partial.handled_generation == 2 and partial.clean_generation == 1
            with pytest.raises(InvalidRepositoryArgumentError):
                await intents.acknowledge(connection, "a", BINDING, 3)
            assert partial.pending_sequence == first.pending_sequence

    asyncio.run(scenario())


def test_resumed_scan_deduplicates_completed_front_pages_and_freezes_upper_bound() -> None:
    async def scenario() -> None:
        intents, states = ArtifactProcessingIntentRepository(), ArtifactProcessingBindingStateRepository()
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                first = await intents.mark_dirty(connection, "z-first", BINDING)
                second = await intents.mark_dirty(connection, "a-second", BINDING)
                scan = await states.start_scan(connection, BINDING, datetime(2026, 1, 1, tzinfo=UTC))
                assert scan.scan_upper_pending_sequence == second.pending_sequence
                accepted = await intents.admit(connection, "z-first", BINDING, scan.scan_generation)
                await intents.acknowledge(connection, "z-first", BINDING, accepted.requested_generation)
            # Simulates process restart after front-page work already completed.
            async with profile.database.transaction() as connection:
                late = await intents.mark_dirty(connection, "late", BINDING)
                resumed = await states.start_scan(connection, BINDING, datetime(2026, 2, 1, tzinfo=UTC))
                assert resumed == scan
                page = await intents.scan(
                    connection, BINDING, limit=1, dirty_only=True, upper_sequence=scan.scan_upper_pending_sequence
                )
                assert page[0].pending_sequence == first.pending_sequence
                repeated = await intents.admit(connection, "z-first", BINDING, scan.scan_generation)
                assert repeated.requested_generation == repeated.handled_generation == 1
                with pytest.raises(InvalidRepositoryArgumentError):
                    await intents.admit(connection, "late", BINDING, scan.scan_generation)
                await states.finish_scan(connection, BINDING)
                next_scan = await states.start_scan(connection, BINDING, datetime(2026, 2, 1, tzinfo=UTC))
                assert next_scan.scan_generation == scan.scan_generation + 1
                assert next_scan.scan_upper_pending_sequence == late.pending_sequence

    asyncio.run(scenario())


def test_concurrent_first_requests_keep_one_sequence_and_both_requests(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'intents.db'}", busy_timeout_ms=10_000)
        intents = ArtifactProcessingIntentRepository()
        async with (
            SQLiteProfile.open(config, tables=SHARED_TABLES) as first,
            SQLiteProfile.open(config, tables=SHARED_TABLES) as second,
        ):
            barrier = asyncio.Barrier(2)

            async def request(profile):
                async with profile.database.transaction() as connection:
                    await barrier.wait()
                    return await intents.request(connection, "same", BINDING)

            results = await asyncio.gather(request(first), request(second))
            assert results[0].pending_sequence == results[1].pending_sequence
            async with first.database.transaction() as connection:
                stored = await intents.load(connection, "same", BINDING)
                assert stored is not None and stored.requested_generation == 2

    asyncio.run(scenario())


def test_source_publication_dirties_every_family_once_and_rolls_back_with_intent_failure() -> None:
    async def scenario() -> None:
        source = NoteSource(name="note", materialization=SourceMaterialization.CAPTURED, body="body")
        sources, intents = SourceRepository(SOURCE_ADAPTERS), ArtifactProcessingIntentRepository()
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                await sources.add(connection, "a", source)
                await sources.add(connection, "a", source)
                for binding in SOURCE_PROCESSING_BINDINGS:
                    stored = await intents.load(connection, "a", binding)
                    assert stored is not None and stored.dirty_generation == 1 and stored.requested_generation == 0
                await connection.exec_driver_sql("""
                    CREATE TRIGGER reject_intent BEFORE INSERT ON pc_artifact_processing_intents
                    WHEN NEW.scope_id = 'b' BEGIN SELECT RAISE(ABORT, 'reject intent'); END
                """)
            with pytest.raises(IntegrityError, match="reject intent"):
                async with profile.database.transaction() as connection:
                    await sources.add(connection, "b", source)
            async with profile.database.transaction() as connection:
                assert await sources.list(connection, "b") == ()
                assert await intents.load(connection, "b", BINDING) is None

    asyncio.run(scenario())


def test_sqlite_fence_lock_serializes_worker_commit_and_term_replacement(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'fence.db'}", busy_timeout_ms=10_000)
        leases = ArtifactProcessingLeaseRepository()
        locked, release, replacing = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as worker_profile:
            async with worker_profile.database.transaction() as connection:
                lease = await leases.start_single_process_term(connection, "same-holder")
            async with SQLiteProfile.open(config, tables=SHARED_TABLES) as leader_profile:

                async def worker():
                    async with worker_profile.database.transaction() as connection:
                        await leases.require_fence(connection, lease.fence("single-process"))
                        locked.set()
                        await release.wait()
                        await connection.execute(
                            insert(SOURCE_JOURNAL_HEADS_TABLE).values(scope_id="commit", position=0)
                        )

                async def replace():
                    await locked.wait()
                    replacing.set()
                    async with leader_profile.database.transaction() as connection:
                        return await leases.start_single_process_term(connection, "same-holder")

                worker_task, replace_task = asyncio.create_task(worker()), asyncio.create_task(replace())
                await replacing.wait()
                # The replacement must wait while the real Worker transaction
                # owns its fence. This is production code, not a locking mock.
                await asyncio.sleep(0.03)
                assert not replace_task.done()
                release.set()
                await worker_task
                new_term = await replace_task
                assert new_term.supervisor_generation == lease.supervisor_generation + 1
                with pytest.raises(ArtifactProcessingLeadershipLostError):
                    async with worker_profile.database.transaction() as connection:
                        await leases.require_fence(connection, lease.fence("single-process"))
                        await connection.execute(
                            insert(SOURCE_JOURNAL_HEADS_TABLE).values(scope_id="stale", position=0)
                        )
                async with worker_profile.database.transaction() as connection:
                    scopes = tuple(await connection.scalars(select(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id)))
                    assert scopes == ("commit",)

    asyncio.run(scenario())
