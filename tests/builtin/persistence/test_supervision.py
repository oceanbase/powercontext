# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence import supervision as supervision_module
from powercontext.builtin.persistence.errors import ArtifactProcessingLeadershipLostError
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import (
    ArtifactProcessingBindingStateRepository,
    ArtifactProcessingLeaseRepository,
    StoredArtifactProcessingLease,
)
from powercontext.builtin.persistence.tables import ARTIFACT_PROCESSING_LEASES_TABLE, SHARED_TABLES


def test_single_process_terms_increment_and_fence_stale_workers() -> None:
    async def scenario() -> None:
        leases = ArtifactProcessingLeaseRepository()
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                first = await leases.start_single_process_term(connection, "holder-a")
            async with profile.database.transaction() as connection:
                second = await leases.start_single_process_term(connection, "holder-b")

            assert first.supervisor_generation == 1
            assert first.lease_expires_at is None
            assert second.supervisor_generation == 2

            async with profile.database.transaction() as connection:
                with pytest.raises(ArtifactProcessingLeadershipLostError):
                    await leases.require_fence(connection, first.fence("single-process"))
                assert await leases.require_fence(connection, second.fence("single-process")) == second

    asyncio.run(scenario())


def test_expiring_lease_acquisition_renewal_and_database_time_binding_state() -> None:
    async def scenario() -> None:
        leases = ArtifactProcessingLeaseRepository()
        states = ArtifactProcessingBindingStateRepository()
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                first = await leases.try_acquire(connection, "holder-a", 60)
            assert first is not None
            assert first.supervisor_generation == 1

            async with profile.database.transaction() as connection:
                assert await leases.try_acquire(connection, "holder-b", 60) is None
                await connection.execute(
                    update(ARTIFACT_PROCESSING_LEASES_TABLE)
                    .where(ARTIFACT_PROCESSING_LEASES_TABLE.c.supervisor_group == "global")
                    .values(lease_expires_at=datetime(2000, 1, 1, tzinfo=UTC).replace(tzinfo=None))
                )

            async with profile.database.transaction() as connection:
                second = await leases.try_acquire(connection, "holder-b", 60)
            assert second is not None
            assert second.supervisor_generation == 2

            async with profile.database.transaction() as connection:
                renewed = await leases.renew(connection, second.fence("oceanbase"), 120)
                completed = await states.mark_auto_wave_completed(connection, "topic-memory-source-window")
            assert renewed.supervisor_generation == second.supervisor_generation
            assert renewed.lease_expires_at is not None
            assert second.lease_expires_at is not None
            assert renewed.lease_expires_at > second.lease_expires_at
            assert completed.last_auto_wave_completed_at is not None
            now = datetime.now(UTC).replace(tzinfo=None)
            assert now - completed.last_auto_wave_completed_at < timedelta(seconds=5)

    asyncio.run(scenario())


def test_renew_samples_database_time_after_waiting_for_the_lease_lock(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        waiting_for_lock = asyncio.Event()
        lock_acquired = asyncio.Event()
        release_lock = asyncio.Event()
        clock = [datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)]

        class LockingLeaseRepository(ArtifactProcessingLeaseRepository):
            async def load(
                self,
                connection: AsyncConnection,
                supervisor_group: str = "global",
                /,
                *,
                for_update: bool = False,
            ) -> StoredArtifactProcessingLease | None:
                if for_update:
                    waiting_for_lock.set()
                    await connection.execute(
                        update(ARTIFACT_PROCESSING_LEASES_TABLE)
                        .where(ARTIFACT_PROCESSING_LEASES_TABLE.c.supervisor_group == supervisor_group)
                        .values(holder_id="holder-a")
                    )
                return await super().load(connection, supervisor_group, for_update=for_update)

        url = f"sqlite+aiosqlite:///{tmp_path / 'lease-renew-lock.db'}"
        config = SQLiteConfig(url=url, busy_timeout_ms=10_000)
        repository = LockingLeaseRepository()
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as first_profile:
            async with first_profile.database.transaction() as connection:
                lease = await repository.try_acquire(connection, "holder-a", 60)
                assert lease is not None
                await connection.execute(
                    update(ARTIFACT_PROCESSING_LEASES_TABLE)
                    .where(ARTIFACT_PROCESSING_LEASES_TABLE.c.supervisor_group == "global")
                    .values(lease_expires_at=clock[0] + timedelta(seconds=1))
                )

            async with SQLiteProfile.open(config, tables=SHARED_TABLES) as second_profile:

                async def hold_lease_lock() -> None:
                    async with first_profile.database.transaction() as connection:
                        await connection.execute(
                            update(ARTIFACT_PROCESSING_LEASES_TABLE)
                            .where(ARTIFACT_PROCESSING_LEASES_TABLE.c.supervisor_group == "global")
                            .values(holder_id="holder-a")
                        )
                        lock_acquired.set()
                        await release_lock.wait()

                async def fake_database_utc_now(connection: AsyncConnection) -> datetime:
                    del connection
                    return clock[0]

                lock_task = asyncio.create_task(hold_lease_lock())
                await asyncio.wait_for(lock_acquired.wait(), timeout=1)
                monkeypatch.setattr(supervision_module, "database_utc_now", fake_database_utc_now)

                async def renew() -> StoredArtifactProcessingLease:
                    async with second_profile.database.transaction() as connection:
                        return await repository.renew(connection, lease.fence("oceanbase"), 60)

                renew_task = asyncio.create_task(renew())
                await asyncio.wait_for(waiting_for_lock.wait(), timeout=1)
                clock[0] += timedelta(seconds=2)
                release_lock.set()
                await lock_task
                with pytest.raises(ArtifactProcessingLeadershipLostError):
                    await renew_task

    asyncio.run(scenario())
